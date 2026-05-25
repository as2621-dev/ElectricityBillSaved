"""Smart Meter Texas (SMT) ingest via the daily email subscription.

See `reference/integrations.md` section 1 for the delivery contract. `email_backend`
pulls the CSV attachments over IMAP; `csv_parser` (added once the real schema is
confirmed) turns them into 15-min interval + daily-usage records.
"""

from .csv_parser import load_daily_usage, parse_interval_csv, summarize_day
from .email_backend import fetch_reports
from .models import DailyUsage, IntervalReading, SavedReport

__all__ = [
    "fetch_reports",
    "parse_interval_csv",
    "summarize_day",
    "load_daily_usage",
    "SavedReport",
    "IntervalReading",
    "DailyUsage",
]
