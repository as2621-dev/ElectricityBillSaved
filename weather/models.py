"""Pydantic models for the weather projection path.

These cover a full billing-cycle weather outlook for one location: a real daily
forecast for the near term (Open-Meteo, ~16 days) stitched to climatology normals
(multi-year historical averages) for the remainder of the cycle. See
`reference/integrations.md` for the endpoint contracts.

Fields use US units to match the audience: temperatures in degrees Fahrenheit
("minimum/maximum heat") and precipitation in inches ("rainfall").
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

DailySource = Literal["forecast", "climatology"]


class GeoLocation(BaseModel):
    """A US location resolved from a ZIP code (via zippopotam.us)."""

    zip_code: str = Field(..., description="5-digit US ZIP code that was looked up")
    place_name: str = Field(..., description="City/place name for the ZIP (e.g. 'Dallas')")
    state: str = Field(..., description="State name (e.g. 'Texas')")
    latitude: float = Field(..., description="Decimal latitude used for the weather query")
    longitude: float = Field(..., description="Decimal longitude used for the weather query")


class DailyWeather(BaseModel):
    """One day's temperature/precipitation, from forecast or climatology normals."""

    forecast_date: date = Field(..., description="The calendar day these values describe")
    temp_min_f: float = Field(..., description="Daily minimum temperature in °F ('minimum heat')")
    temp_max_f: float = Field(..., description="Daily maximum temperature in °F ('maximum heat')")
    precip_in: float = Field(..., description="Total precipitation in inches ('rainfall')")
    source: DailySource = Field(
        ...,
        description="'forecast' = real near-term forecast; 'climatology' = multi-year historical average for this date",
    )


class ProjectionSummary(BaseModel):
    """Cycle-level aggregates over a `MonthlyProjection`'s days."""

    day_count: int = Field(..., description="Number of days covered by the projection")
    forecast_day_count: int = Field(..., description="Days backed by a real forecast")
    climatology_day_count: int = Field(..., description="Days backed by climatology normals")
    avg_temp_max_f: float = Field(..., description="Mean of daily max temps across the cycle (°F)")
    avg_temp_min_f: float = Field(..., description="Mean of daily min temps across the cycle (°F)")
    hottest_temp_max_f: float = Field(..., description="Highest single-day max temp in the cycle (°F)")
    coldest_temp_min_f: float = Field(..., description="Lowest single-day min temp in the cycle (°F)")
    total_precip_in: float = Field(..., description="Sum of daily precipitation across the cycle (inches)")


class MonthlyProjection(BaseModel):
    """A full billing-cycle weather outlook for one location.

    `days` is contiguous and sorted ascending from `cycle_start` to `cycle_end`,
    each day tagged with its `source` so callers know how much is real forecast
    versus historical fill.
    """

    location: GeoLocation = Field(..., description="Resolved location the projection is for")
    cycle_start: date = Field(..., description="First day of the projected window (inclusive)")
    cycle_end: date = Field(..., description="Last day of the projected window (inclusive)")
    forecast_horizon_days: int = Field(..., description="How many near-term days came from the live forecast")
    days: list[DailyWeather] = Field(..., description="Per-day outlook, sorted by forecast_date ascending")
