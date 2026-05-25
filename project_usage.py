"""CLI: weather-driven projection of cycle kWh and the $125 credit cliff.

Method:
1. Fit a simple usage model from the days where we have BOTH metered usage (SMT)
   and that day's actual weather: kWh/day ≈ baseload + β · CDD65, where
   CDD65 = max(0, mean_temp_F − 65) is cooling-degree-days (the AC-driven part).
2. For every day of the billing cycle, use the metered value if we have it; else
   predict from that day's weather — observed reanalysis for days already past,
   Open-Meteo forecast for days ahead.
3. Sum to a projected cycle total, compare to the 1000 kWh credit threshold, and
   estimate the dollar bill (reusing the bill math in project_bill.py).

Daily mean temperature is approximated as (min + max) / 2. Precipitation is not a
separate regressor — rain shows up as cooler days (lower CDD), so temperature
already absorbs most of its effect; with more history it could be added explicitly.

Usage:
    python project_usage.py
    python project_usage.py --zip 77080 --cycle-start 2026-05-11 --next-read 2026-06-10
"""

from __future__ import annotations

import argparse
import os
from datetime import date, datetime, timedelta

from dotenv import load_dotenv

from meter.csv_parser import load_daily_usage
from project_bill import (
    BILL_CREDIT_AMOUNT_USD,
    BILL_CREDIT_THRESHOLD_KWH,
    estimate_bill,
)
from weather.client import configure_logging, fetch_forecast, geocode_zip

CDD_BASE_F: float = 65.0


def fit_usage_model(cdds: list[float], kwhs: list[float]) -> tuple[float, float, float]:
    """Ordinary-least-squares fit of kWh = baseload + beta * CDD.

    Args:
        cdds: Cooling-degree-days per day (regressor).
        kwhs: Metered kWh per day (response), aligned with `cdds`.

    Returns:
        (baseload, beta, r_squared). beta is kWh per cooling-degree-day.
    """
    n = len(cdds)
    sum_x, sum_y = sum(cdds), sum(kwhs)
    sum_xx = sum(x * x for x in cdds)
    sum_xy = sum(x * y for x, y in zip(cdds, kwhs))
    denom = n * sum_xx - sum_x * sum_x
    if n < 2 or denom == 0:
        return (sum_y / n if n else 0.0, 0.0, 0.0)
    beta = (n * sum_xy - sum_x * sum_y) / denom
    baseload = (sum_y - beta * sum_x) / n
    mean_y = sum_y / n
    ss_tot = sum((y - mean_y) ** 2 for y in kwhs)
    ss_res = sum((y - (baseload + beta * x)) ** 2 for x, y in zip(cdds, kwhs))
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return (baseload, beta, r_squared)


def main() -> int:
    load_dotenv()
    configure_logging()

    parser = argparse.ArgumentParser(description="Weather-driven projection of cycle kWh + the 1000 kWh credit cliff.")
    parser.add_argument("--dir", default="data/smt", help="SMT CSV directory")
    parser.add_argument("--zip", dest="zip_code", default=os.environ.get("WEATHER_ZIP", "77080"))
    parser.add_argument("--cycle-start", default="2026-05-11", help="First service day of the cycle (YYYY-MM-DD)")
    parser.add_argument("--next-read", default="2026-06-10", help="ESTIMATED next meter-read date (YYYY-MM-DD)")
    parser.add_argument("--today", default=None, help="Override 'today' (YYYY-MM-DD); default = system date")
    args = parser.parse_args()

    cycle_start = datetime.strptime(args.cycle_start, "%Y-%m-%d").date()
    next_read = datetime.strptime(args.next_read, "%Y-%m-%d").date()
    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else date.today()
    cycle_dates = [cycle_start + timedelta(days=i) for i in range((next_read - cycle_start).days)]
    cycle_end = cycle_dates[-1]

    # 1. Metered usage (SMT) keyed by service date.
    usage_by_date = {d.service_date: d.total_kwh for d in load_daily_usage(args.dir)}
    esi = next((d.esi_id for d in load_daily_usage(args.dir)), "—")

    # 2. Weather for the whole window: observed (past_days) + forecast.
    location = geocode_zip(args.zip_code)
    past_days = max(0, (today - cycle_start).days) + 1
    forecast_days = max(1, (cycle_end - today).days + 1)
    rows = fetch_forecast(location.latitude, location.longitude, days=forecast_days, past_days=past_days)
    weather_by_date = {r.forecast_date: r for r in rows}

    def mean_temp(d: date) -> float | None:
        w = weather_by_date.get(d)
        return (w.temp_min_f + w.temp_max_f) / 2 if w else None

    def cdd(d: date) -> float | None:
        t = mean_temp(d)
        return max(0.0, t - CDD_BASE_F) if t is not None else None

    # 3. Fit on days with BOTH metered usage and weather.
    fit_dates = [d for d in cycle_dates if d in usage_by_date and cdd(d) is not None]
    fit_cdds = [cdd(d) for d in fit_dates]
    fit_kwhs = [usage_by_date[d] for d in fit_dates]
    baseload, beta, r2 = fit_usage_model(fit_cdds, fit_kwhs)

    def predict(d: date) -> float | None:
        c = cdd(d)
        return max(0.0, baseload + beta * c) if c is not None else None

    # 4. Walk the cycle: metered where known, else modeled.
    print(f"\n=== Weather-driven cycle projection (ESI {esi}) ===")
    print(f"  location     : {location.place_name}, {location.state} {location.zip_code}")
    print(f"  cycle        : {cycle_start} → {next_read} (est.)  {len(cycle_dates)} service days   (today {today})")
    print(f"\n  fitted model : kWh/day ≈ {baseload:.1f} baseload + {beta:.2f} × CDD65(°F)")
    print(f"  fit quality  : R² = {r2:.2f} on {len(fit_dates)} metered day(s)")
    print(f"  reads as     : ~{baseload:.0f} kWh/day with AC off, +{beta:.2f} kWh for every °F the daily mean sits above 65°F\n")

    print(f"  {'date':<12}{'mean°F':>7}{'CDD':>6}  {'src':<5}{'kWh':>8}  note")
    print(f"  {'-'*11:<12}{'-'*6:>7}{'-'*5:>6}  {'-'*4:<5}{'-'*7:>8}  {'-'*4}")
    actual_sum = modeled_sum = 0.0
    actual_n = modeled_n = 0
    missing: list[date] = []
    for d in cycle_dates:
        t, c = mean_temp(d), cdd(d)
        if d in usage_by_date:
            kwh, note, actual_sum, actual_n = usage_by_date[d], "metered", actual_sum + usage_by_date[d], actual_n + 1
        elif c is not None:
            kwh = predict(d) or 0.0
            note, modeled_sum, modeled_n = "modeled", modeled_sum + kwh, modeled_n + 1
        else:
            missing.append(d)
            continue
        src = "obs" if d < today else "fcst"
        print(f"  {d.isoformat():<12}{(t or 0):>7.1f}{(c or 0):>6.1f}  {src:<5}{kwh:>8.1f}  {note}")

    total = round(actual_sum + modeled_sum, 0)
    margin = round(total - BILL_CREDIT_THRESHOLD_KWH, 0)
    if total >= BILL_CREDIT_THRESHOLD_KWH + 60:
        verdict = "LIKELY CLEAR — credit safe"
    elif total >= BILL_CREDIT_THRESHOLD_KWH:
        verdict = "CLOSE — barely over; protect the margin"
    elif total >= BILL_CREDIT_THRESHOLD_KWH - 75:
        verdict = "AT RISK — projected just UNDER 1000; act to clear it"
    else:
        verdict = "MISS — well under 1000 at this rate"

    bill = estimate_bill(total)
    print(f"\n  metered so far : {actual_sum:.1f} kWh ({actual_n} days)   modeled : {modeled_sum:.1f} kWh ({modeled_n} days)")
    print(f"  PROJECTED TOTAL: ~{total:.0f} kWh over {len(cycle_dates)} days")
    print(f"\n  --- $125 credit cliff (threshold {BILL_CREDIT_THRESHOLD_KWH} kWh) ---")
    print(f"  margin   : {margin:+.0f} kWh vs 1000     VERDICT: {verdict}")
    print(f"  est. bill at ~{total:.0f} kWh: ${bill['total']:.2f}  (credit {bill['credit']:.0f})")
    print(f"  cliff: 999 kWh → ${estimate_bill(999)['total']:.2f}  vs  1000 kWh → ${estimate_bill(1000)['total']:.2f}"
          f"  ({estimate_bill(999)['total'] - estimate_bill(1000)['total']:+.0f} for 1 kWh)")
    if missing:
        print(f"\n  note: no weather for {len(missing)} day(s) (forecast horizon) — excluded.")
    print(f"  caveats: model fit on only {len(fit_dates)} days; next-read date estimated; mean temp ≈ (min+max)/2.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
