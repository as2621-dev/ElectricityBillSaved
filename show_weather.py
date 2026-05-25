"""CLI: project min/max temperature and rainfall over a billing cycle for a ZIP.

Resolves the ZIP to coordinates, pulls Open-Meteo's live ~16-day forecast, and
backfills the rest of the cycle with multi-year climatology normals. Days past the
forecast horizon are marked `~clim` — they are historical averages, not a real
forecast (nobody can forecast a specific day a month out).

Usage:
    python show_weather.py --zip 75201               # 30-day outlook, table
    python show_weather.py --zip 75201 --days 30
    python show_weather.py --zip 75201 --json        # machine-readable
    python show_weather.py                           # uses WEATHER_ZIP from .env
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx
from dotenv import load_dotenv

from weather import build_monthly_projection, configure_logging, summarize_projection


def main() -> int:
    load_dotenv()
    configure_logging()

    parser = argparse.ArgumentParser(description="Project temperature + rainfall over a billing cycle for a ZIP.")
    parser.add_argument("--zip", dest="zip_code", default=os.environ.get("WEATHER_ZIP"), help="5-digit US ZIP code")
    parser.add_argument("--days", type=int, default=30, help="Cycle length in days (default 30)")
    parser.add_argument("--lookback-years", type=int, default=5, help="Years of history to average for the tail")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    args = parser.parse_args()

    if not args.zip_code:
        print("error: pass --zip 75201 (or set WEATHER_ZIP in .env).", file=sys.stderr)
        return 2

    try:
        projection = build_monthly_projection(
            args.zip_code, num_days=args.days, lookback_years=args.lookback_years
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except httpx.HTTPError as exc:
        print(f"error: weather service request failed: {exc}", file=sys.stderr)
        return 1
    summary = summarize_projection(projection)

    if args.json:
        print(
            json.dumps(
                {"projection": projection.model_dump(mode="json"), "summary": summary.model_dump(mode="json")},
                indent=2,
            )
        )
        return 0

    location = projection.location
    print(f"\n{location.place_name}, {location.state} {location.zip_code}  ({location.latitude}, {location.longitude})")
    print(f"{projection.cycle_start.isoformat()} → {projection.cycle_end.isoformat()}  ({summary.day_count} days)\n")
    print(f"  {'date':<12}{'min °F':>8}{'max °F':>8}{'rain in':>9}   source")
    print(f"  {'-' * 11:<12}{'-' * 7:>8}{'-' * 7:>8}{'-' * 8:>9}   {'-' * 10}")
    for day in projection.days:
        tag = "forecast" if day.source == "forecast" else "~clim"
        print(f"  {day.forecast_date.isoformat():<12}{day.temp_min_f:>8.1f}{day.temp_max_f:>8.1f}{day.precip_in:>9.3f}   {tag}")

    print(
        f"\n  forecast days : {summary.forecast_day_count}   climatology days : {summary.climatology_day_count}"
    )
    print(f"  avg high / low: {summary.avg_temp_max_f:.1f} °F / {summary.avg_temp_min_f:.1f} °F")
    print(f"  hottest / coldest: {summary.hottest_temp_max_f:.1f} °F / {summary.coldest_temp_min_f:.1f} °F")
    print(f"  total rainfall: {summary.total_precip_in:.2f} in\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
