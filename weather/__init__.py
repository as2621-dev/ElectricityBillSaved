"""Weather projection for a billing cycle (Open-Meteo, free, no API key).

Resolves a ZIP to coordinates, pulls the live ~16-day daily forecast, and
backfills the rest of the cycle with multi-year climatology normals, returning a
single source-tagged outlook. See `reference/integrations.md` for the endpoints.
"""

from .client import (
    configure_logging,
    fetch_climatology_normals,
    fetch_forecast,
    geocode_zip,
)
from .models import (
    DailyWeather,
    GeoLocation,
    MonthlyProjection,
    ProjectionSummary,
)
from .projection import build_monthly_projection, summarize_projection

__all__ = [
    "configure_logging",
    "geocode_zip",
    "fetch_forecast",
    "fetch_climatology_normals",
    "build_monthly_projection",
    "summarize_projection",
    "GeoLocation",
    "DailyWeather",
    "MonthlyProjection",
    "ProjectionSummary",
]
