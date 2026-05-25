"""CLI: reconcile 4Change's charged amount against our own bill computation.

For each 'Your bill's ready' email: parse the amount + billing period, sum SMT
usage over that period, compute the bill from plan terms, and compare within
tolerance. A 'match' validates our bill model (so we can trust it to advise on the
1000 kWh credit threshold); 'discrepancy' flags something to investigate;
'incomplete' means we lack usage data for that period (cannot fairly compare yet).

Uses the same Gmail app-password creds as the SMT fetch (SMT_IMAP_* in .env).

Usage:
    python reconcile_4change_bill.py
    python reconcile_4change_bill.py --since 2026-05-01
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

from bill.calculator import DEFAULT_PLAN_TERMS, compute_bill
from bill.email_notice import DEFAULT_FROM_FILTER, fetch_bill_notices
from bill.reconcile import reconcile
from bill.usage import sum_usage_for_period
from meter.email_backend import DEFAULT_IMAP_HOST, DEFAULT_IMAP_PORT, configure_logging


def _fmt_usd(cents: int) -> str:
    """Format integer cents as a $ string."""
    return f"${cents / 100:,.2f}"


def main() -> int:
    load_dotenv()
    configure_logging()

    parser = argparse.ArgumentParser(description="Reconcile 4Change bills vs our computed bill from SMT usage.")
    parser.add_argument("--dir", default=os.environ.get("SMT_DEST_DIR", "data/smt"), help="SMT CSV directory")
    parser.add_argument("--since", default=None, help="Only notices on/after this date (YYYY-MM-DD)")
    parser.add_argument(
        "--from", dest="from_filter", default=os.environ.get("FOURCHANGE_FROM_FILTER", DEFAULT_FROM_FILTER)
    )
    args = parser.parse_args()

    username = os.environ.get("SMT_IMAP_USERNAME")
    app_password = os.environ.get("SMT_IMAP_APP_PASSWORD")
    if not username or not app_password:
        print("error: set SMT_IMAP_USERNAME and SMT_IMAP_APP_PASSWORD in .env. See .env.example.", file=sys.stderr)
        return 2

    since = datetime.strptime(args.since, "%Y-%m-%d").date() if args.since else None
    notices = fetch_bill_notices(
        username=username,
        app_password=app_password,
        host=os.environ.get("SMT_IMAP_HOST", DEFAULT_IMAP_HOST),
        port=int(os.environ.get("SMT_IMAP_PORT", str(DEFAULT_IMAP_PORT))),
        mailbox=os.environ.get("SMT_IMAP_MAILBOX", "INBOX"),
        from_filter=args.from_filter,
        since=since,
    )

    if not notices:
        print("\nNo 4Change bill-ready notices found. Try --since, or check the sender filter.")
        return 0

    print(f"\nReconciling {len(notices)} bill notice(s) against plan: {DEFAULT_PLAN_TERMS.plan_name}\n")
    discrepancies = 0
    for notice in notices:
        usage = sum_usage_for_period(args.dir, notice.period_start, notice.period_end)
        estimate = compute_bill(usage.total_kwh, DEFAULT_PLAN_TERMS)
        result = reconcile(estimate, notice, usage)
        if result.status == "discrepancy":
            discrepancies += 1

        print(f"  ── Billing period {notice.period_start} → {notice.period_end} ──")
        print(f"     4Change charged : {_fmt_usd(notice.amount_due_cents)}   (due {notice.due_date})")
        print(
            f"     usage           : {usage.total_kwh:.1f} kWh  "
            f"({usage.days_present}/{usage.days_expected} days, complete={usage.is_complete})"
        )
        print(
            f"     our estimate    : {_fmt_usd(estimate.total_cents)}  "
            f"[energy {_fmt_usd(estimate.energy_charge_cents)}, credit {_fmt_usd(estimate.bill_credit_cents)}, "
            f"tdu {_fmt_usd(estimate.tdu_delivery_cents)}, fees {_fmt_usd(estimate.misc_fees_cents)}, "
            f"tax {_fmt_usd(estimate.sales_tax_cents)}]"
        )
        print(
            f"     STATUS          : {result.status.upper()}  (Δ {_fmt_usd(result.delta_cents)}, tol {_fmt_usd(result.tolerance_cents)})"
        )
        print(f"     {result.notes}\n")

    return 1 if discrepancies else 0


if __name__ == "__main__":
    raise SystemExit(main())
