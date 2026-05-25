"""CLI: project this billing cycle's kWh + bill, and check the $125 credit cliff.

The plan (4Change Energy "Maxx Saver Value 12", CenterPoint Houston) pays a flat
$125 bill credit on ANY cycle with usage >= 1000 kWh — a single hard threshold
(999 kWh = $0 credit, 1000 kWh = full $125, no upper limit). So the one number
that matters is: will the cycle land at or above 1000 kWh?

Usage:
    python project_bill.py
    python project_bill.py --cycle-start 2026-05-11 --next-read 2026-06-10
    python project_bill.py --daily-avg 36.8     # override the projection rate

All rates/credit below are taken from the 5/13/2026 bill (invoice 054853973570).
The next-read date is an ESTIMATE — CenterPoint drifts it a few business days.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from dotenv import load_dotenv

from meter.csv_parser import load_daily_usage
from meter.email_backend import configure_logging

# ─── Plan + bill parameters (from the 5/13/2026 4Change bill) ──────────────
BILL_CREDIT_AMOUNT_USD: float = 125.00
BILL_CREDIT_THRESHOLD_KWH: int = 1000
ENERGY_RATE_PER_KWH: float = 0.14611
# CenterPoint residential TDU delivery: fixed monthly + per-kWh (reproduces the
# bill's $58.38 at 1066 kWh: 4.39 + 0.0506*1066 = 58.33).
TDU_BASE_MONTHLY_USD: float = 4.39
TDU_PER_KWH: float = 0.0506
# Gross-receipts reimb + PUC assessment (~% of pre-tax charges) and sales tax,
# calibrated to reproduce the bill total within ~$0.20.
MISC_FACTOR: float = 0.0205
SALES_TAX_FACTOR: float = 0.01


def estimate_bill(kwh: float) -> dict[str, float]:
    """Estimate the dollar bill for a given cycle kWh, including the credit cliff.

    Args:
        kwh: Total cycle usage in kWh.

    Returns:
        A dict with energy, credit, tdu, misc, tax, and total (USD).
    """
    energy = ENERGY_RATE_PER_KWH * kwh
    credit = -BILL_CREDIT_AMOUNT_USD if kwh >= BILL_CREDIT_THRESHOLD_KWH else 0.0
    tdu = TDU_BASE_MONTHLY_USD + TDU_PER_KWH * kwh
    pre_misc = energy + credit + tdu
    misc = pre_misc * MISC_FACTOR
    tax = (pre_misc + misc) * SALES_TAX_FACTOR
    return {
        "energy": round(energy, 2),
        "credit": round(credit, 2),
        "tdu": round(tdu, 2),
        "misc": round(misc, 2),
        "tax": round(tax, 2),
        "total": round(pre_misc + misc + tax, 2),
    }


def main() -> int:
    load_dotenv()
    configure_logging()

    parser = argparse.ArgumentParser(description="Project cycle kWh + bill and check the 1000 kWh credit cliff.")
    parser.add_argument("--dir", default="data/smt", help="SMT CSV directory")
    parser.add_argument("--cycle-start", default="2026-05-11", help="First service day of the current cycle (YYYY-MM-DD)")
    parser.add_argument("--next-read", default="2026-06-10", help="ESTIMATED next meter-read date (YYYY-MM-DD)")
    parser.add_argument("--daily-avg", type=float, default=None, help="Override projection rate (kWh/day)")
    args = parser.parse_args()

    cycle_start = datetime.strptime(args.cycle_start, "%Y-%m-%d").date()
    next_read = datetime.strptime(args.next_read, "%Y-%m-%d").date()
    cycle_days = (next_read - cycle_start).days

    # Cycle-to-date actuals from SMT (only the days we have CSVs for).
    all_days = load_daily_usage(args.dir)
    cycle_actuals = [d for d in all_days if cycle_start <= d.service_date < next_read]
    actual_kwh = round(sum(d.total_kwh for d in cycle_actuals), 1)
    actual_count = len(cycle_actuals)

    if args.daily_avg is not None:
        daily_avg = args.daily_avg
        avg_source = "override"
    elif actual_count:
        daily_avg = actual_kwh / actual_count
        avg_source = f"{actual_count} SMT day(s) in-cycle"
    else:
        # No in-cycle data yet: fall back to any available SMT days.
        daily_avg = sum(d.total_kwh for d in all_days) / len(all_days) if all_days else 0.0
        avg_source = f"{len(all_days)} SMT day(s) (none in-cycle yet)"

    projected_kwh = round(daily_avg * cycle_days, 0)
    margin = round(projected_kwh - BILL_CREDIT_THRESHOLD_KWH, 0)
    required_avg = BILL_CREDIT_THRESHOLD_KWH / cycle_days if cycle_days else 0.0

    if projected_kwh >= BILL_CREDIT_THRESHOLD_KWH + 50:
        verdict = "LIKELY CLEAR — credit safe"
    elif projected_kwh >= BILL_CREDIT_THRESHOLD_KWH:
        verdict = "CLOSE — barely over; manage to stay above 1000"
    elif projected_kwh >= BILL_CREDIT_THRESHOLD_KWH - 75:
        verdict = "AT RISK — projected just UNDER 1000; act to clear it"
    else:
        verdict = "MISS — well under 1000 at this rate"

    proj_bill = estimate_bill(projected_kwh)
    bill_at_999 = estimate_bill(999)["total"]
    bill_at_1000 = estimate_bill(1000)["total"]

    print(f"\n=== Billing-cycle projection (ESI {cycle_actuals[-1].esi_id if cycle_actuals else '—'}) ===")
    print(f"  cycle window      : {cycle_start} → {next_read} (est.)   {cycle_days} days")
    print(f"  in-cycle actuals  : {actual_kwh:.1f} kWh over {actual_count} day(s)")
    print(f"  daily average     : {daily_avg:.2f} kWh/day  [{avg_source}]")
    print(f"  projected total   : ~{projected_kwh:.0f} kWh   (avg × {cycle_days} days)")
    print(f"\n  --- $125 credit cliff (threshold {BILL_CREDIT_THRESHOLD_KWH} kWh) ---")
    print(f"  projected margin  : {margin:+.0f} kWh vs threshold")
    print(f"  VERDICT           : {verdict}")
    print(f"  need full-cycle avg ≥ {required_avg:.1f} kWh/day to clear 1000 (you're at {daily_avg:.1f})")
    print(f"\n  --- estimated bill at projected {projected_kwh:.0f} kWh ---")
    print(f"  energy {proj_bill['energy']:>8.2f}  credit {proj_bill['credit']:>8.2f}  tdu {proj_bill['tdu']:>7.2f}"
          f"  fees {proj_bill['misc']:>5.2f}  tax {proj_bill['tax']:>5.2f}")
    print(f"  estimated total   : ${proj_bill['total']:.2f}")
    print(f"\n  the cliff in dollars: 999 kWh → ${bill_at_999:.2f}   vs   1000 kWh → ${bill_at_1000:.2f}"
          f"   ({bill_at_999 - bill_at_1000:+.2f} for 1 extra kWh)")
    print("\n  caveats: next-read date is estimated (CenterPoint drifts it ±a few business days);")
    print("  in-cycle days before the SMT subscription started are projected at the average, not metered;")
    print("  June heat typically pushes usage up, which this flat-average projection does not model.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
