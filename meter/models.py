"""Pydantic models for the SMT email-ingest path.

This module covers only what the *fetch* step produces: a record of each CSV
report saved out of the mailbox. Interval/daily-usage models live in
`csv_parser.py` and are written against the real CSV schema (see
`reference/integrations.md` section 1 — the column layout is validated against an
actual SMT file, not assumed).
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class IntervalReading(BaseModel):
    """One 15-minute meter interval from an SMT IntervalMeterUsage CSV row.

    SMT columns map as: ESIID→esi_id, USAGE_DATE→service_date,
    USAGE_START_TIME→interval_start, USAGE_KWH→usage_kwh,
    ESTIMATED_ACTUAL→is_estimated ('A'=actual), CONSUMPTION_SURPLUSGENERATION→reading_type.
    """

    esi_id: str = Field(..., description="Electric Service Identifier (leading quote stripped)")
    service_date: date = Field(..., description="Service day the interval belongs to (USAGE_DATE)")
    interval_start: datetime = Field(..., description="Start of the 15-min interval (service_date + USAGE_START_TIME)")
    interval_end: datetime = Field(..., description="End of the interval (interval_start + 15 min; midnight-safe)")
    usage_kwh: float = Field(..., description="kWh recorded in this interval (USAGE_KWH)")
    is_estimated: bool = Field(..., description="True if ESTIMATED_ACTUAL is not 'A' (i.e. estimated, not metered)")
    reading_type: str = Field(..., description="'consumption' or 'surplus_generation'")


class DailyUsage(BaseModel):
    """Aggregated usage for one service day, summed from its interval readings."""

    esi_id: str = Field(..., description="Electric Service Identifier for the day's intervals")
    service_date: date = Field(..., description="The service day these totals cover")
    total_kwh: float = Field(..., description="Sum of consumption usage_kwh across the day (rounded to 3 dp)")
    interval_count: int = Field(..., description="Number of consumption intervals found (96 = a complete day)")
    is_complete: bool = Field(..., description="True when interval_count == 96")
    has_estimates: bool = Field(..., description="True if any interval was estimated rather than actual")


class SavedReport(BaseModel):
    """One SMT report attachment saved out of the mailbox to local disk."""

    message_id: str = Field(..., description="Gmail/IMAP message id the attachment came from")
    sender: str = Field(..., description="From header of the email (e.g. info@communications.smartmetertexas.com)")
    subject: str = Field(..., description="Subject line of the email")
    received_at: datetime = Field(..., description="Date header of the email, parsed to a datetime")
    attachment_filename: str = Field(..., description="Original attachment filename, e.g. IntervalMeterUsage….CSV")
    saved_path: str = Field(..., description="Absolute path the CSV was written to under the dest dir")
    was_zip: bool = Field(False, description="True if the attachment arrived as a ZIP and was extracted to CSV")
