"""Lifecycle stage -> Klaviyo profile property, behind a hard consent gate.

The segmentation rules are the ones from my earlier analysis of the public
UCI orders (see `data/REAL_DATA_PROVENANCE.md`): recency and order count map every customer to New / Repeat / At risk / Dormant / Churned.
Here they run on the real orders of a live store instead of on the public
dataset, and the resulting stage is written to Klaviyo as a profile property.

What this script does NOT do, on purpose:

* It never grants marketing consent. A Klaviyo bulk import writes profile
  data; it does not subscribe anyone to email marketing. Consent is collected
  only by the double opt-in signup form (`docs/consent.md`). This script just
  refuses to touch anyone who has not already opted in.
* It never invents an email address. Rows without one are reported, not
  guessed. The public dataset used in the analysis repo is anonymous
  (customer ids, no contacts), which is exactly why it cannot be synced.

It reads a store's own files: the orders export straight from Shopify
(Orders > Export, no editing needed) and the consent export from the signup
form or ESP. Besides the Klaviyo sync it can write the suppression list, one
row per person held back and why, for the team to exclude from campaigns.

Segments and flows live in Klaviyo, not here: the property set below is read
by a Klaviyo segment definition, and the flow triggers on segment entry. See
`docs/flows.md`.

Pure standard library.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

API_ROOT = "https://a.klaviyo.com/api/profile-bulk-import-jobs"
# Klaviyo pins behaviour to a dated revision; bumping it is a deliberate act.
API_REVISION = "2026-07-15"
# Klaviyo caps a bulk import at 10k profiles / 5MB. Stay well under both.
BATCH_SIZE = 1000

LIFECYCLE_ORDER = ["New", "Repeat", "At risk", "Dormant", "Churned"]
STAGE_ACTION = {
    "New": "Onboarding / second-purchase nudge",
    "Repeat": "Loyalty / cross-sell flow",
    "At risk": "Win-back sequence",
    "Dormant": "Reactivation offer",
    "Churned": "Low-cost reactivation, then sunset",
}
ELIGIBLE_CONSENT = "opted_in"

# A raw Shopify orders export (Orders > Export) is recognised by these columns.
# It has one row per line item, and order-level fields sit on the first row.
SHOPIFY_COLUMNS = {"Name", "Email", "Created at", "Total"}
NOT_A_SALE = {"refunded", "voided"}  # Shopify "Financial Status" values
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y")
VALUE_COLUMNS = ("order_value_eur", "order_value", "value", "total", "order_value_gbp")
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(text: str) -> str:
    """A cell Excel would run as a formula gets a leading apostrophe. Emails and
    sources come from sign-up forms, so they are user input."""
    return "'" + text if text.startswith(FORMULA_START) else text


def read_csv(path: Path) -> csv.DictReader:
    """Rows of a CSV as real tools save it: comma, semicolon (Italian Excel) or
    tab as separator, with or without the byte-order mark Excel adds."""
    text = path.read_text(encoding="utf-8-sig")
    header = text.split("\n", 1)[0]
    return csv.DictReader(io.StringIO(text, newline=""), delimiter=max(",;\t", key=header.count))


def parse_day(text: str | None) -> date | None:
    """Date from "2026-05-20", "2026-05-20 14:03:00 +0200", "2026-05-20T14:03Z"
    or "20/05/2026" (with or without a time); None if empty."""
    text = (text or "").strip()
    if not text:
        return None
    head = text.split()[0].split("T")[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(head, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"date {text!r} is neither YYYY-MM-DD nor DD/MM/YYYY")


def parse_amount(text: str | None) -> float:
    """Number from "49.90", "49,90", "1.234,56" or "1,234.56" (currency signs ignored)."""
    t = "".join(ch for ch in (text or "") if ch.isdigit() or ch in ",.-")
    if not t:
        return 0.0
    if "," in t and "." in t:  # the separator that comes last is the decimal one
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    else:
        t = t.replace(",", ".")
    return float(t)


def lifecycle_stage(recency_days: int, orders: int) -> str:
    """Same thresholds as crm_lifecycle.real_lifecycle() in the analysis repo."""
    if recency_days <= 90:
        return "Repeat" if orders >= 2 else "New"
    if recency_days <= 180:
        return "At risk"
    if recency_days <= 365:
        return "Dormant"
    return "Churned"


def order_rows(path: Path):
    """Yield (email, YYYY-MM-DD, value) once per order.

    Reads this repo's simple format (email, order_date, order_value_eur) or a
    raw Shopify orders export. In the Shopify file the line items of one order
    are folded into one order, and cancelled, refunded and voided orders are
    skipped: they are not purchases.
    """
    reader = read_csv(path)
    columns = set(reader.fieldnames or ())
    if not SHOPIFY_COLUMNS <= columns:
        # Simple format. A customer id instead of an email is fine for the
        # timing tool; the Klaviyo sync then has nobody it can write to.
        who = next((c for c in ("email", "customer_id", "customer") if c in columns), "email")
        value = next((c for c in VALUE_COLUMNS if c in columns), "order_value_eur")
        for row in reader:
            yield row.get(who), row.get("order_date"), row.get(value)
        return
    orders: dict[str, dict] = {}
    for row in reader:
        order = orders.setdefault(row["Name"], {})
        for k, v in row.items():
            if v and not order.get(k):
                order[k] = v
    for o in orders.values():
        if o.get("Cancelled at") or o.get("Financial Status", "").lower() in NOT_A_SALE:
            continue
        yield o.get("Email"), o.get("Created at"), o.get("Total")


def read_orders(path: Path, as_of: date | None = None) -> dict[str, dict]:
    """Aggregate an orders export to one row per email.

    Takes either file format order_rows() reads; see data/README.md.

    Orders dated after `as_of` are ignored: with a pinned --as-of they have
    not happened yet, and counting them gives a negative recency that files
    a lapsed customer as "New".
    """
    agg: dict[str, dict] = {}
    for email, day, value in order_rows(path):
        email = (email or "").strip().lower()
        order_date = parse_day(day)
        if not email or order_date is None:
            continue
        if as_of and order_date > as_of:
            continue
        a = agg.setdefault(email, {"orders": 0, "last": order_date, "monetary": 0.0})
        a["orders"] += 1
        a["monetary"] += parse_amount(value)
        if order_date > a["last"]:
            a["last"] = order_date
    return agg


def read_consent(path: Path) -> dict[str, dict]:
    """Consent record per email, as exported from the signup form / ESP.

    Expected columns: email, consent_status, consent_source, consent_timestamp
    (ISO 8601). Anything that is not exactly opted_in is treated as not
    consenting; see build_profiles() for what an opted_in record also needs.
    """
    out: dict[str, dict] = {}
    for row in read_csv(path):
        email = (row.get("email") or "").strip().lower()
        if email:
            out[email] = {
                "status": (row.get("consent_status") or "unknown").strip(),
                "source": (row.get("consent_source") or "").strip(),
                "timestamp": (row.get("consent_timestamp") or "").strip(),
            }
    return out


def consent_date(timestamp: str) -> date | None:
    """Date of a consent timestamp, or None if there is no readable date."""
    try:
        return parse_day(timestamp)
    except ValueError:
        return None


def build_profiles(
    orders: dict[str, dict], consent: dict[str, dict], as_of: date
) -> tuple[list[dict], dict, list[tuple[str, str]]]:
    """Return (profiles to sync, report, suppression list of (email, reason)).

    The consent gate is hard: it is applied before anything else and is never
    a scoring term. A customer with no consent record is suppressed as
    unknown, not optimistically included. An opted_in record also needs a
    source and a date on or before as_of: GDPR asks the shop to be able to
    show how and when each person consented (Art. 7(1)), and a consent it
    cannot show is treated as missing.
    """
    profiles: list[dict] = []
    held: list[tuple[str, str]] = []
    suppressed: dict[str, int] = defaultdict(int)
    by_stage: dict[str, int] = defaultdict(int)
    by_source: dict[str, int] = defaultdict(int)

    for email, a in sorted(orders.items()):
        record = consent.get(email, {})
        status = record.get("status", "unknown")
        if status != ELIGIBLE_CONSENT:
            suppressed[status] += 1
            held.append((email, status))
            continue
        given = consent_date(record.get("timestamp", ""))
        if not record.get("source") or given is None or given > as_of:
            suppressed["opted_in_without_proof"] += 1
            held.append((email, "opted_in_without_proof"))
            continue
        by_source[record["source"]] += 1
        recency = (as_of - a["last"]).days
        stage = lifecycle_stage(recency, a["orders"])
        by_stage[stage] += 1
        profiles.append(
            {
                "type": "profile",
                "attributes": {
                    "email": email,
                    "properties": {
                        "lifecycle_stage": stage,
                        "lifecycle_recency_days": recency,
                        "lifecycle_orders": a["orders"],
                        "lifecycle_monetary_eur": round(a["monetary"], 2),
                        "lifecycle_recommended_action": STAGE_ACTION[stage],
                        # Two dates, because they answer different questions and
                        # collapsing them into one is how a pinned reference date
                        # stops being a disclosure. `computed_at` is when the
                        # script ran; `as_of` is the date the recency was measured
                        # from, and it is written onto the profile so anyone
                        # reading it in the ESP can see it was pinned.
                        "lifecycle_computed_at": date.today().isoformat(),
                        "lifecycle_as_of": as_of.isoformat(),
                        # Where and when the person consented, next to the
                        # stage, so a segment can tell sign-up sources apart.
                        "consent_source": record["source"],
                        "consent_timestamp": record["timestamp"],
                    },
                },
            }
        )

    report = {
        "as_of": as_of.isoformat(),
        "customers_in_orders": len(orders),
        "eligible": len(profiles),
        "suppressed": sum(suppressed.values()),
        "suppressed_by_reason": dict(suppressed),
        "eligible_by_stage": {s: by_stage[s] for s in LIFECYCLE_ORDER if by_stage[s]},
        "eligible_by_consent_source": dict(sorted(by_source.items())),
    }
    return profiles, report, held


def _payload(batch: list[dict], list_id: str) -> dict:
    body: dict = {
        "data": {
            "type": "profile-bulk-import-job",
            "attributes": {"profiles": {"data": batch}},
        }
    }
    if list_id:
        body["data"]["relationships"] = {
            "lists": {"data": [{"type": "list", "id": list_id}]}
        }
    return body


def push(profiles: list[dict], list_id: str, api_key: str, live: bool) -> list[str]:
    """POST the profiles in batches. Returns the accepted job ids.

    Dry run is the default everywhere: without live=True this opens no socket.
    """
    job_ids: list[str] = []
    for start in range(0, len(profiles), BATCH_SIZE):
        batch = profiles[start : start + BATCH_SIZE]
        if not live:
            print(f"  [dry-run] batch of {len(batch)} profiles not sent")
            continue
        req = urllib.request.Request(
            API_ROOT,
            data=json.dumps(_payload(batch, list_id)).encode("utf-8"),
            headers={
                "Authorization": f"Klaviyo-API-Key {api_key}",
                "Content-Type": "application/vnd.api+json",
                "Accept": "application/vnd.api+json",
                "revision": API_REVISION,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            # Klaviyo puts the useful part in the body, not the status line.
            detail = exc.read().decode("utf-8", "replace")[:500]
            # Earlier batches were accepted and will import; say which, or a
            # re-run double-imports them without anyone knowing.
            sent = ", ".join(job_ids) or "none"
            sys.exit(
                f"Klaviyo rejected the batch starting at profile {start} "
                f"({exc.code}): {detail}\nAlready accepted jobs: {sent}"
            )
        job_ids.append(payload.get("data", {}).get("id", "accepted"))
        print(f"  sent batch of {len(batch)} profiles -> job {job_ids[-1]}")
    return job_ids


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--orders", type=Path, default=Path("data/orders_example.csv"))
    p.add_argument("--consent", type=Path, default=Path("data/consent_example.csv"))
    p.add_argument("--list-id", default="", help="Klaviyo list id to attach profiles to")
    p.add_argument(
        "--as-of",
        default="",
        help="reference date YYYY-MM-DD for recency (default: today)",
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="actually call Klaviyo; without it nothing leaves the machine",
    )
    p.add_argument("--report", type=Path, default=Path("reports/sync_report.json"))
    p.add_argument(
        "--suppressed",
        type=Path,
        help="write the suppression list here (email, reason), e.g. reports/suppressed.csv",
    )
    args = p.parse_args(argv)

    as_of = (
        datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else date.today()
    )
    orders = read_orders(args.orders, as_of)
    consent = read_consent(args.consent)
    profiles, report, held = build_profiles(orders, consent, as_of)

    print(json.dumps(report, indent=2))
    if args.suppressed:
        args.suppressed.parent.mkdir(parents=True, exist_ok=True)
        with args.suppressed.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["email", "reason"])
            w.writerows([safe_cell(email), reason] for email, reason in held)
        who = "person" if len(held) == 1 else "people"
        print(f"\nsuppression list ({len(held)} {who}) -> {args.suppressed}")
    if not profiles:
        print("\nNothing eligible to sync - the consent gate suppressed everyone.")
        return 0

    # The key is read from the environment, never from a flag: a private key
    # passed on the command line ends up in shell history and in `ps`.
    api_key = os.environ.get("KLAVIYO_API_KEY", "")
    if args.live and not api_key:
        sys.exit("KLAVIYO_API_KEY is not set - refusing to run --live.")
    if args.live and not args.list_id:
        sys.exit("--list-id is required with --live, or the import lands nowhere.")

    print(f"\n{'LIVE' if args.live else 'DRY RUN'}: {len(profiles)} profiles")
    report["job_ids"] = push(profiles, args.list_id, api_key, args.live)
    report["mode"] = "live" if args.live else "dry-run"

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nreport -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
