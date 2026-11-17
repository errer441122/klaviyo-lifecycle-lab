"""When to send the second-order email: how long a store's customers take to buy again.

Reads the same orders file as sync_klaviyo.py (this repo's simple format or a
raw Shopify orders export) and prints, for customers who had a full year to
come back, the share who ordered again by day 30, 60, 90, 180 and 365, and the
median number of days among those who came back. Those numbers are the delay
to type into the post-purchase flow, and the point after which a win-back
offer starts paying for orders that would not have come anyway.

A second order on the same day counts as the same visit. The first month of
the file is left out, because it can hold customers who first bought before
the export starts.

    python src/repeat_timing.py --orders data/orders.csv

The same calculation on a public dataset (UCI Online Retail II, 3,359
customers with a full year of data): half of those who come back do it within
71 days. See the CRM retention practice repository.

Pure standard library.
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from sync_klaviyo import order_rows, parse_day

CHECKPOINTS = (30, 60, 90, 180, 365)
MIN_CUSTOMERS = 30  # below this, one customer moves a share by more than 3 points


def order_days(path: Path) -> dict[str, set[date]]:
    """The distinct days on which each customer ordered."""
    days: dict[str, set[date]] = defaultdict(set)
    for email, day, _value in order_rows(path):
        email = (email or "").strip().lower()
        when = parse_day(day)
        if email and when:
            days[email].add(when)
    return days


def repeat_timing(days: dict[str, set[date]]) -> dict:
    """Share back by each checkpoint and median days, for customers with a full year."""
    window = CHECKPOINTS[-1]
    all_days = [d for ds in days.values() for d in ds]
    data_end = max(all_days) + timedelta(days=1)
    first_month = min(all_days).strftime("%Y-%m")
    gaps: list[int | None] = []
    for ds in days.values():
        first, *later = sorted(ds)
        if first.strftime("%Y-%m") == first_month or (data_end - first).days < window:
            continue
        gaps.append((later[0] - first).days if later else None)
    back = [g for g in gaps if g is not None and g <= window]
    return {
        "customers": len(gaps),
        "share_back_by_day": {
            k: (sum(1 for g in back if g <= k) / len(gaps) if gaps else 0.0) for k in CHECKPOINTS
        },
        "median_days_if_back": statistics.median(back) if back else None,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--orders", type=Path, default=Path("data/shopify_orders_example.csv"))
    args = p.parse_args(argv)

    days = order_days(args.orders)
    if not days:
        print("No orders with an email and a date in this file.")
        return 1
    t = repeat_timing(days)
    n = t["customers"]
    if not n:
        print("No customer has a full year of data after their first order yet: "
              "run this again when the file covers more than a year.")
        return 0
    if n < MIN_CUSTOMERS:
        print(f"Only {n} customers have a full year of data: treat the numbers below as a rough sketch.\n")
    print(f"Customers with a full year of data: {n:,}")
    for day, share in t["share_back_by_day"].items():
        print(f"  ordered again by day {day:>3}: {share:5.0%}")
    if t["median_days_if_back"] is None:
        print("\nNobody came back within a year: there is no repeat window to time a flow on.")
    else:
        m = t["median_days_if_back"]
        print(f"\nHalf of those who came back within a year did so within {m:.0f} days.")
        print(f"Second-order email: before day {m:.0f}. By day 90, "
              f"{t['share_back_by_day'][90]:.0%} have already ordered again; keep the "
              f"win-back offer for those still missing after that.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
