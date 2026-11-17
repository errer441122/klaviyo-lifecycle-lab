"""What the browser page (docs/index.html) calls.

The page runs this repository's Python in the browser (Pyodide), so it uses
the same functions as the command line and the same tests cover both. Each
function takes the paths of the files the user picked, and returns JSON with
everything the page shows and lets the user download. A file the tools cannot
read comes back as {"error": "..."} with the reason, not as a crash.

Nothing here opens a network connection: the page never sends anything to
Klaviyo. Writing to Klaviyo stays a command-line step with an API key.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path

import pre_send_check as psc
import repeat_timing as rt
import sync_klaviyo as sk


def _csv(header: list[str], rows: list[list[object]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def _guarded(fn):
    """Turn a file the tools cannot read into a message for the page."""
    def run(*args):
        try:
            return json.dumps(fn(*args))
        except (SystemExit, ValueError, KeyError) as e:
            return json.dumps({"error": str(e) or e.__class__.__name__})
    return run


@_guarded
def check_template(html: str, subject: str = "", preview: str = "") -> dict:
    return {"problems": psc.check(html, None, subject or None, preview or None)}


@_guarded
def lifecycle(orders_path: str, consent_path: str, as_of: str = "") -> dict:
    day = date.fromisoformat(as_of) if as_of else date.today()
    orders = sk.read_orders(Path(orders_path), day)
    consent = sk.read_consent(Path(consent_path))
    profiles, report, held = sk.build_profiles(orders, consent, day)
    header = ["email", "lifecycle_stage", "days_since_last_order", "orders", "spend",
              "consent_source", "consent_timestamp"]
    rows = []
    for p in profiles:
        a = p["attributes"]["properties"]
        rows.append([sk.safe_cell(p["attributes"]["email"]), a["lifecycle_stage"], a["lifecycle_recency_days"],
                     a["lifecycle_orders"], a["lifecycle_monetary_eur"], sk.safe_cell(a["consent_source"]),
                     sk.safe_cell(a["consent_timestamp"])])
    held_rows = [[sk.safe_cell(email), reason] for email, reason in held]
    return {
        "report": report,
        "header": header,
        "profiles": rows,
        "suppressed": held_rows,
        "profiles_csv": _csv(header, rows),
        "suppressed_csv": _csv(["email", "reason"], held_rows),
    }


@_guarded
def timing(orders_path: str) -> dict:
    days = rt.order_days(Path(orders_path))
    if not days:
        raise ValueError("No orders with a customer and a date in this file.")
    t = rt.repeat_timing(days)
    return {"customers": t["customers"], "min_customers": rt.MIN_CUSTOMERS,
            "share_back_by_day": {str(k): v for k, v in t["share_back_by_day"].items()},
            "median_days_if_back": t["median_days_if_back"]}
