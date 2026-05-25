"""HTTP clients for the weather projection path — all free, no API key.

Three upstreams (see `reference/integrations.md`):
- ZIP -> lat/lon : https://api.zippopotam.us/us/<zip>   (Open-Meteo geocoding is
  unreliable for US 5-digit ZIPs, so we resolve coordinates here instead.)
- Near-term daily forecast : https://api.open-meteo.com/v1/forecast  (<=16 days)
- Climatology normals      : https://archive-api.open-meteo.com/v1/archive
  (ERA5 reanalysis; we average each calendar day over the last N years.)

All functions are synchronous httpx calls — this mirrors the stdlib-style, script-
driven `meter/` module rather than the async agent path.
"""

from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from typing import NamedTuple

import httpx
import structlog

from .models import DailyWeather, GeoLocation

logger = structlog.get_logger()

ZIPPOPOTAM_URL: str = "https://api.zippopotam.us/us/{zip_code}"
OPEN_METEO_FORECAST_URL: str = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL: str = "https://archive-api.open-meteo.com/v1/archive"

# Open-Meteo daily fields we request, in a fixed order.
DAILY_FIELDS: str = "temperature_2m_max,temperature_2m_min,precipitation_sum"
TEMPERATURE_UNIT: str = "fahrenheit"
PRECIPITATION_UNIT: str = "inch"

MAX_FORECAST_DAYS: int = 16  # Open-Meteo hard cap for forecast_days.
DEFAULT_TIMEOUT_SECONDS: float = 20.0


def configure_logging() -> None:
    """Configure structlog to emit JSON to stderr (matches the meter/ module)."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


class ClimatologyNormal(NamedTuple):
    """Multi-year average for a single calendar day (month/day), in US units."""

    temp_min_f: float
    temp_max_f: float
    precip_in: float


def geocode_zip(zip_code: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> GeoLocation:
    """Resolve a US ZIP code to a `GeoLocation` via zippopotam.us.

    Args:
        zip_code: 5-digit US ZIP code.
        timeout: Per-request timeout in seconds.

    Returns:
        The resolved location with latitude/longitude for weather queries.

    Raises:
        ValueError: If the ZIP is unknown (404) or returns no places.
        httpx.HTTPStatusError: For other non-2xx responses.

    Example:
        >>> loc = geocode_zip("75201")
        >>> loc.place_name
        'Dallas'
    """
    zip_code = zip_code.strip()
    logger.info("geocode_zip_started", zip_code=zip_code)
    response = httpx.get(ZIPPOPOTAM_URL.format(zip_code=zip_code), timeout=timeout)
    if response.status_code == 404:
        logger.error(
            "geocode_zip_not_found",
            zip_code=zip_code,
            fix_suggestion="Pass a valid 5-digit US ZIP code.",
        )
        raise ValueError(f"ZIP code not found: {zip_code}")
    response.raise_for_status()

    payload = response.json()
    places = payload.get("places") or []
    if not places:
        raise ValueError(f"ZIP code returned no places: {zip_code}")

    place = places[0]
    location = GeoLocation(
        zip_code=str(payload.get("post code", zip_code)),
        place_name=place.get("place name", ""),
        state=place.get("state", ""),
        latitude=float(place["latitude"]),
        longitude=float(place["longitude"]),
    )
    logger.info(
        "geocode_zip_completed",
        zip_code=zip_code,
        place_name=location.place_name,
        latitude=location.latitude,
        longitude=location.longitude,
    )
    return location


def _daily_rows(payload: dict) -> list[tuple[date, float, float, float]]:
    """Unpack an Open-Meteo `daily` block into (date, min, max, precip) tuples.

    Skips any day where a field is null (Open-Meteo emits null for gaps).
    """
    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    maxes = daily.get("temperature_2m_max") or []
    mins = daily.get("temperature_2m_min") or []
    precips = daily.get("precipitation_sum") or []

    rows: list[tuple[date, float, float, float]] = []
    for iso_day, temp_max, temp_min, precip in zip(times, maxes, mins, precips):
        if temp_max is None or temp_min is None or precip is None:
            continue
        rows.append((date.fromisoformat(iso_day), float(temp_min), float(temp_max), float(precip)))
    return rows


MAX_PAST_DAYS: int = 92  # Open-Meteo cap for past_days on the forecast endpoint.


def fetch_forecast(
    latitude: float,
    longitude: float,
    *,
    days: int = MAX_FORECAST_DAYS,
    past_days: int = 0,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[DailyWeather]:
    """Fetch the near-term daily forecast (min/max temp + precip) from Open-Meteo.

    With `past_days > 0` the response is prefixed with recent OBSERVED/reanalysis
    days (available without the archive's ~5-day lag), which is what callers need to
    regress against actual recent weather. All rows are tagged source='forecast';
    callers that care should classify observed vs forecast by date.

    Args:
        latitude: Decimal latitude.
        longitude: Decimal longitude.
        days: Number of forecast days (clamped to Open-Meteo's 16-day cap).
        past_days: Recent days to prepend (clamped to Open-Meteo's 92-day cap).
        timeout: Per-request timeout in seconds.

    Returns:
        One `DailyWeather` per day, ascending (past days first, then today→future).

    Example:
        >>> rows = fetch_forecast(32.79, -96.80, days=16, past_days=14)
        >>> rows[0].source
        'forecast'
    """
    days = max(1, min(days, MAX_FORECAST_DAYS))
    past_days = max(0, min(past_days, MAX_PAST_DAYS))
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": DAILY_FIELDS,
        "forecast_days": days,
        "past_days": past_days,
        "temperature_unit": TEMPERATURE_UNIT,
        "precipitation_unit": PRECIPITATION_UNIT,
        "timezone": "auto",
    }
    logger.info("fetch_forecast_started", latitude=latitude, longitude=longitude, days=days, past_days=past_days)
    response = httpx.get(OPEN_METEO_FORECAST_URL, params=params, timeout=timeout)
    response.raise_for_status()

    rows = _daily_rows(response.json())
    forecast = [
        DailyWeather(
            forecast_date=day,
            temp_min_f=temp_min,
            temp_max_f=temp_max,
            precip_in=precip,
            source="forecast",
        )
        for day, temp_min, temp_max, precip in rows
    ]
    logger.info("fetch_forecast_completed", latitude=latitude, longitude=longitude, day_count=len(forecast))
    return forecast


def _shift_year(anchor: date, year: int) -> date:
    """Move a date to the same month/day in `year`, mapping Feb 29 -> Feb 28 if needed."""
    try:
        return anchor.replace(year=year)
    except ValueError:
        return anchor.replace(year=year, day=28)


def fetch_climatology_normals(
    latitude: float,
    longitude: float,
    cycle_start: date,
    num_days: int,
    *,
    lookback_years: int = 5,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[tuple[int, int], ClimatologyNormal]:
    """Compute per-calendar-day climatology normals from the Open-Meteo archive.

    For each of the last `lookback_years` years, fetches the same calendar window
    that the cycle covers and averages each (month, day) across those years. The
    archive is fully historical, so the ~5-day reanalysis lag never affects us.

    Args:
        latitude: Decimal latitude.
        longitude: Decimal longitude.
        cycle_start: First day of the cycle whose calendar days we need normals for.
        num_days: Length of the cycle window in days.
        lookback_years: How many prior years to average (default 5).
        timeout: Per-request timeout in seconds.

    Returns:
        A dict keyed by (month, day) -> `ClimatologyNormal` of averaged values.

    Example:
        >>> normals = fetch_climatology_normals(32.79, -96.80, date(2026, 6, 10), 14)
        >>> normals[(6, 10)].temp_max_f  # doctest: +SKIP
        91.2
    """
    accumulator: dict[tuple[int, int], list[tuple[float, float, float]]] = defaultdict(list)
    last_full_year = date.today().year - 1

    for year in range(last_full_year, last_full_year - lookback_years, -1):
        hist_start = _shift_year(cycle_start, year)
        hist_end = hist_start + timedelta(days=num_days - 1)
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "daily": DAILY_FIELDS,
            "start_date": hist_start.isoformat(),
            "end_date": hist_end.isoformat(),
            "temperature_unit": TEMPERATURE_UNIT,
            "precipitation_unit": PRECIPITATION_UNIT,
            "timezone": "auto",
        }
        logger.info(
            "fetch_climatology_year_started",
            year=year,
            start_date=params["start_date"],
            end_date=params["end_date"],
        )
        response = httpx.get(OPEN_METEO_ARCHIVE_URL, params=params, timeout=timeout)
        response.raise_for_status()
        for day, temp_min, temp_max, precip in _daily_rows(response.json()):
            accumulator[(day.month, day.day)].append((temp_min, temp_max, precip))

    normals: dict[tuple[int, int], ClimatologyNormal] = {}
    for month_day, samples in accumulator.items():
        normals[month_day] = ClimatologyNormal(
            temp_min_f=round(statistics.mean(s[0] for s in samples), 1),
            temp_max_f=round(statistics.mean(s[1] for s in samples), 1),
            precip_in=round(statistics.mean(s[2] for s in samples), 3),
        )
    logger.info(
        "fetch_climatology_completed",
        lookback_years=lookback_years,
        calendar_days=len(normals),
    )
    return normals
