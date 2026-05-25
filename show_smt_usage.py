"""CLI: parse the downloaded SMT CSVs and print daily electricity usage.

Reads every CSV under the data dir (default data/smt), aggregates 15-min intervals
into per-day kWh totals, and prints a table plus simple summary stats.

Usage:
    python show_smt_usage.py                 # table of all downloaded days
    python show_smt_usage.py --dir data/smt
    python show_smt_usage.py --json          # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from dotenv import load_dotenv

from meter.csv_parser import load_daily_usage
from meter.email_backend import configure_logging


def main() -> int:
    load_dotenv()
    configure_logging()

    parser = argparse.ArgumentParser(description="Show daily kWh from downloaded SMT CSVs.")
    parser.add_argument("--dir", default=os.environ.get("SMT_DEST_DIR", "data/smt"), help="CSV directory")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    args = parser.parse_args()

    days = load_daily_usage(args.dir)
    if not days:
        print(f"No parseable CSVs in {args.dir}/. Run: python fetch_smt_csv.py --since 2026-05-19", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([d.model_dump(mode="json") for d in days], indent=2))
        return 0

    esi_id = days[-1].esi_id
    print(f"\nESIID {esi_id} — {len(days)} day(s) of usage\n")
    print(f"  {'date':<12}{'kWh':>9}   {'intervals':>9}  flags")
    print(f"  {'-' * 11:<12}{'-' * 8:>9}   {'-' * 9:>9}  {'-' * 12}")
    for day in days:
        flags = []
        if not day.is_complete:
            flags.append(f"PARTIAL({day.interval_count}/96)")
        if day.has_estimates:
            flags.append("estimated")
        print(f"  {day.service_date.isoformat():<12}{day.total_kwh:>9.3f}   {day.interval_count:>9}  {', '.join(flags)}")

    totals = [d.total_kwh for d in days]
    complete = [d for d in days if d.is_complete]
    avg = sum(d.total_kwh for d in complete) / len(complete) if complete else 0.0
    print(f"\n  latest day ({days[-1].service_date.isoformat()}): {days[-1].total_kwh:.3f} kWh")
    print(f"  sum over {len(days)} day(s)         : {sum(totals):.3f} kWh")
    print(f"  avg per complete day ({len(complete)}) : {avg:.3f} kWh/day\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
