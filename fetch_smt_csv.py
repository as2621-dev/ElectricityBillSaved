"""CLI: pull Smart Meter Texas report emails over IMAP and save the CSV attachments.

Reads IMAP settings from .env (see `.env.example`, SMT_IMAP_* keys). Requires a
Gmail **app password** (2-Step Verification on) — NOT the account password.

Usage:
    python fetch_smt_csv.py                       # fetch all SMT CSVs to data/smt/
    python fetch_smt_csv.py --since 2026-05-19    # only mail on/after this date
    python fetch_smt_csv.py --unseen              # only unread messages
    python fetch_smt_csv.py --preview 8           # show more rows of each new CSV

On success it prints, for every CSV saved this run, the header row, the first few
rows, and the total row count — so the actual SMT column layout is visible.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

from meter.email_backend import (
    DEFAULT_FROM_FILTER,
    DEFAULT_IMAP_HOST,
    DEFAULT_IMAP_PORT,
    DEFAULT_MAILBOX,
    configure_logging,
    fetch_reports,
)
from meter.models import SavedReport


def _parse_since(value: str | None) -> date | None:
    """Parse a --since YYYY-MM-DD string into a date, or None."""
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _preview_csv(path: Path, max_rows: int) -> None:
    """Print the header, first `max_rows` data rows, and total row count of a CSV."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        rows = list(reader)
    if not rows:
        print(f"  (empty file: {path.name})")
        return
    header, *data_rows = rows
    print(f"\n  {path.name}  —  {len(data_rows)} data rows, {len(header)} columns")
    print(f"    header: {header}")
    for row in data_rows[:max_rows]:
        print(f"    row   : {row}")
    if len(data_rows) > max_rows:
        print(f"    … {len(data_rows) - max_rows} more rows")


def main() -> int:
    load_dotenv()
    configure_logging()

    parser = argparse.ArgumentParser(description="Fetch SMT report CSVs from a mailbox over IMAP.")
    parser.add_argument("--since", default=None, help="Only fetch mail on/after this date (YYYY-MM-DD)")
    parser.add_argument("--unseen", action="store_true", help="Only fetch UNSEEN (unread) messages")
    parser.add_argument("--dest", default=os.environ.get("SMT_DEST_DIR", "data/smt"), help="Where to save CSVs")
    parser.add_argument("--mailbox", default=os.environ.get("SMT_IMAP_MAILBOX", DEFAULT_MAILBOX))
    parser.add_argument("--from", dest="from_filter", default=os.environ.get("SMT_EMAIL_FROM_FILTER", DEFAULT_FROM_FILTER))
    parser.add_argument("--preview", type=int, default=5, help="Rows of each new CSV to print (0 to disable)")
    args = parser.parse_args()

    username = os.environ.get("SMT_IMAP_USERNAME")
    app_password = os.environ.get("SMT_IMAP_APP_PASSWORD")
    if not username or not app_password:
        print(
            "error: set SMT_IMAP_USERNAME and SMT_IMAP_APP_PASSWORD in .env "
            "(Gmail app password, not your account password). See .env.example.",
            file=sys.stderr,
        )
        return 2

    host = os.environ.get("SMT_IMAP_HOST", DEFAULT_IMAP_HOST)
    port = int(os.environ.get("SMT_IMAP_PORT", str(DEFAULT_IMAP_PORT)))

    reports: list[SavedReport] = fetch_reports(
        username=username,
        app_password=app_password,
        host=host,
        port=port,
        mailbox=args.mailbox,
        from_filter=args.from_filter,
        dest_dir=args.dest,
        since=_parse_since(args.since),
        unseen_only=args.unseen,
    )

    if not reports:
        print(
            f"\nNo new CSVs saved to {args.dest}/ "
            f"(none matched, or all already downloaded).\n"
            f"Try a wider window, e.g. --since 2026-05-01, or check the mailbox filter."
        )
        return 0

    print(f"\nSaved {len(reports)} new CSV file(s) to {args.dest}/:")
    for report in reports:
        print(f"  • {report.attachment_filename}  (from {report.received_at.date()})")
        if args.preview > 0:
            _preview_csv(Path(report.saved_path), args.preview)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
