"""Stitch a near-term forecast to climatology normals into one cycle outlook.

The horizon problem: genuine daily forecasts only run ~16 days, but a billing
cycle is ~30. So for each day in the window we use the live forecast when it
exists, and fall back to a multi-year historical average ("climatology") for the
tail. Every day is tagged with its `source` so callers can weight confidence.
"""

from __future__ import annotations

from datetime import date, timedelta

import structlog

from .client import (
    MAX_FORECAST_DAYS,
    fetch_climatology_normals,
    fetch_forecast,
    geocode_zip,
)
from .models import DailyWeather, MonthlyProjection, ProjectionSummary

logger = structlog.get_logger()

DEFAULT_CYCLE_DAYS: int = 30


def build_monthly_projection(
    zip_code: str,
    *,
    cycle_start: date | None = None,
    num_days: int = DEFAULT_CYCLE_DAYS,
    forecast_horizon_days: int = MAX_FORECAST_DAYS,
    lookback_years: int = 5,
) -> MonthlyProjection:
    """Build a full-cycle weather outlook for a ZIP: forecast + climatology backfill.

    Args:
        zip_code: 5-digit US ZIP code.
        cycle_start: First day of the window (defaults to today).
        num_days: Length of the window in days (defaults to 30).
        forecast_horizon_days: Live-forecast days to request (capped at 16).
        lookback_years: Years of history to average for the climatology tail.

    Returns:
        A `MonthlyProjection` with one contiguous, source-tagged day per cycle day.

    Example:
        >>> proj = build_monthly_projection("75201", num_days=30)  # doctest: +SKIP
        >>> proj.days[0].source
        'forecast'
    """
    cycle_start = cycle_start or date.today()
    cycle_end = cycle_start + timedelta(days=num_days - 1)
    logger.info(
        "build_monthly_projection_started",
        zip_code=zip_code,
        cycle_start=cycle_start.isoformat(),
        cycle_end=cycle_end.isoformat(),
        num_days=num_days,
    )

    location = geocode_zip(zip_code)
    forecast_rows = fetch_forecast(
        location.latitude, location.longitude, days=forecast_horizon_days
    )
    forecast_by_date: dict[date, DailyWeather] = {row.forecast_date: row for row in forecast_rows}

    # Which cycle days have no live forecast and therefore need climatology fill?
    cycle_dates = [cycle_start + timedelta(days=offset) for offset in range(num_days)]
    needs_climatology = any(day not in forecast_by_date for day in cycle_dates)

    normals = (
        fetch_climatology_normals(
            location.latitude,
            location.longitude,
            cycle_start,
            num_days,
            lookback_years=lookback_years,
        )
        if needs_climatology
        else {}
    )

    days: list[DailyWeather] = []
    forecast_used = 0
    for current_day in cycle_dates:
        forecast_row = forecast_by_date.get(current_day)
        if forecast_row is not None:
            days.append(forecast_row)
            forecast_used += 1
            continue

        normal = normals.get((current_day.month, current_day.day))
        if normal is None:
            logger.error(
                "climatology_gap",
                missing_date=current_day.isoformat(),
                fix_suggestion="Increase lookback_years or check archive availability for this location.",
            )
            continue
        days.append(
            DailyWeather(
                forecast_date=current_day,
                temp_min_f=normal.temp_min_f,
                temp_max_f=normal.temp_max_f,
                precip_in=normal.precip_in,
                source="climatology",
            )
        )

    logger.info(
        "build_monthly_projection_completed",
        zip_code=zip_code,
        day_count=len(days),
        forecast_day_count=forecast_used,
        climatology_day_count=len(days) - forecast_used,
    )
    return MonthlyProjection(
        location=location,
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        forecast_horizon_days=forecast_used,
        days=days,
    )


def summarize_projection(projection: MonthlyProjection) -> ProjectionSummary:
    """Compute cycle-level aggregates (averages, extremes, total rain) for a projection.

    Args:
        projection: A built `MonthlyProjection`.

    Returns:
        A `ProjectionSummary` of the projection's days.

    Raises:
        ValueError: If the projection has no days to summarize.
    """
    days = projection.days
    if not days:
        raise ValueError("summarize_projection requires a projection with at least one day")

    forecast_days = [d for d in days if d.source == "forecast"]
    return ProjectionSummary(
        day_count=len(days),
        forecast_day_count=len(forecast_days),
        climatology_day_count=len(days) - len(forecast_days),
        avg_temp_max_f=round(sum(d.temp_max_f for d in days) / len(days), 1),
        avg_temp_min_f=round(sum(d.temp_min_f for d in days) / len(days), 1),
        hottest_temp_max_f=max(d.temp_max_f for d in days),
        coldest_temp_min_f=min(d.temp_min_f for d in days),
        total_precip_in=round(sum(d.precip_in for d in days), 3),
    )
