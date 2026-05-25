"""Parse SMT IntervalMeterUsage CSVs into interval + daily-usage models.

Schema validated against real SMT files (see `reference/integrations.md` section 1):

    ESIID, USAGE_DATE, REVISION_DATE, USAGE_START_TIME, USAGE_END_TIME,
    USAGE_KWH, ESTIMATED_ACTUAL, CONSUMPTION_SURPLUSGENERATION

Notes that shaped this parser:
- ESIID arrives with a leading apostrophe (Excel text-guard), e.g. "'10089…" → stripped.
- USAGE_DATE is MM/DD/YYYY and is the *service day*; the email arrives ~24h later.
- USAGE_START_TIME is " HH:MM" (leading space), 00:00 … 23:45 for a full 96-row day.
- The last row's USAGE_END_TIME is "00:00" (next-day midnight). We derive interval_end
  as interval_start + 15 min so the midnight wrap never produces a wrong date.
- ESTIMATED_ACTUAL: 'A' = actual (metered); anything else treated as estimated.
- CONSUMPTION_SURPLUSGENERATION: 'Consumption' vs 'SurplusGeneration' (solar export).
  Daily totals sum **consumption** only.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

import structlog

from .models import DailyUsage, IntervalReading

logger = structlog.get_logger()

INTERVAL_MINUTES: int = 15
COMPLETE_DAY_INTERVALS: int = 96


def _strip_esiid(raw: str) -> str:
    """Remove the Excel text-guard apostrophe SMT prefixes to the ESIID."""
    return raw.strip().lstrip("'")


def _parse_reading_type(raw: str) -> str:
    """Normalize CONSUMPTION_SURPLUSGENERATION to snake_case ('consumption'/'surplus_generation')."""
    value = raw.strip().lower()
    return "surplus_generation" if value.startswith("surplus") else "consumption"


def _parse_interval_start(usage_date: str, start_time: str) -> datetime:
    """Combine USAGE_DATE (MM/DD/YYYY) and USAGE_START_TIME (' HH:MM') into a datetime."""
    return datetime.strptime(f"{usage_date.strip()} {start_time.strip()}", "%m/%d/%Y %H:%M")


def parse_interval_csv(path: str | Path) -> list[IntervalReading]:
    """Parse one IntervalMeterUsage CSV into a list of `IntervalReading`.

    Args:
        path: Path to a single SMT interval CSV.

    Returns:
        One `IntervalReading` per data row, in file order.

    Raises:
        KeyError: If an expected column header is missing (schema drift).
        ValueError: If a date/time/number field cannot be parsed.

    Example:
        >>> readings = parse_interval_csv("data/smt/IntervalMeterUsage….CSV")
        >>> readings[0].usage_kwh
        0.203
    """
    path = Path(path)
    readings: list[IntervalReading] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            interval_start = _parse_interval_start(row["USAGE_DATE"], row["USAGE_START_TIME"])
            readings.append(
                IntervalReading(
                    esi_id=_strip_esiid(row["ESIID"]),
                    service_date=interval_start.date(),
                    interval_start=interval_start,
                    interval_end=interval_start + timedelta(minutes=INTERVAL_MINUTES),
                    usage_kwh=float(row["USAGE_KWH"]),
                    is_estimated=row["ESTIMATED_ACTUAL"].strip().upper() != "A",
                    reading_type=_parse_reading_type(row["CONSUMPTION_SURPLUSGENERATION"]),
                )
            )
    logger.info("smt_csv_parsed", path=str(path), interval_count=len(readings))
    return readings


def summarize_day(readings: list[IntervalReading]) -> DailyUsage:
    """Aggregate one service day's consumption intervals into a `DailyUsage`.

    Sums consumption only (ignores surplus-generation rows). Expects all readings
    to share one service_date and ESIID; the first reading defines both.

    Args:
        readings: Interval readings for a single service day.

    Returns:
        A `DailyUsage` with the day's total kWh and completeness flags.

    Raises:
        ValueError: If `readings` is empty.
    """
    if not readings:
        raise ValueError("summarize_day requires at least one reading")

    consumption = [r for r in readings if r.reading_type == "consumption"]
    total_kwh = round(sum(r.usage_kwh for r in consumption), 3)
    return DailyUsage(
        esi_id=readings[0].esi_id,
        service_date=readings[0].service_date,
        total_kwh=total_kwh,
        interval_count=len(consumption),
        is_complete=len(consumption) == COMPLETE_DAY_INTERVALS,
        has_estimates=any(r.is_estimated for r in consumption),
    )


def load_daily_usage(csv_dir: str | Path) -> list[DailyUsage]:
    """Parse every CSV in a directory and return one `DailyUsage` per service day.

    Intervals are grouped by service_date across files (so re-deliveries of the same
    day merge), summed, and returned sorted by date ascending.

    Args:
        csv_dir: Directory containing SMT interval CSVs (e.g. data/smt).

    Returns:
        Daily totals sorted by service_date. Empty if no CSVs are present.
    """
    csv_dir = Path(csv_dir)
    by_date: dict[tuple[str, str], list[IntervalReading]] = {}
    for path in sorted(csv_dir.glob("*.CSV")) + sorted(csv_dir.glob("*.csv")):
        for reading in parse_interval_csv(path):
            by_date.setdefault((reading.service_date.isoformat(), reading.esi_id), []).append(reading)

    days = [summarize_day(readings) for readings in by_date.values()]
    days.sort(key=lambda d: d.service_date)
    return days
