"""Sum SMT interval usage over a billing period, with a completeness verdict.

Reconciliation is only trustworthy when the period's usage is complete: every
service day present, each with 96 intervals, none estimated. A gap (e.g. the
pre-subscription days the SMT daily email never delivered) must surface as
`is_complete=False` so reconcile reports 'incomplete' rather than a false match.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import structlog

from meter.csv_parser import load_daily_usage

from .models import UsageForPeriod

logger = structlog.get_logger()


def sum_usage_for_period(
    csv_dir: str | Path,
    period_start: date,
    period_end: date,
) -> UsageForPeriod:
    """Sum consumption kWh over [period_start, period_end] (inclusive) from SMT CSVs.

    Args:
        csv_dir: Directory of SMT interval CSVs (e.g. data/smt).
        period_start: Billing period start (inclusive civil date).
        period_end: Billing period end (inclusive civil date) = the meter-read day.

    Returns:
        A `UsageForPeriod` with the total, day counts, completeness, and any gaps.
    """
    days_in_period = {
        d.service_date: d for d in load_daily_usage(csv_dir) if period_start <= d.service_date <= period_end
    }
    expected = [period_start + timedelta(days=i) for i in range((period_end - period_start).days + 1)]
    missing = [d for d in expected if d not in days_in_period]

    total_kwh = round(sum(d.total_kwh for d in days_in_period.values()), 3)
    is_complete = (
        not missing
        and all(d.is_complete for d in days_in_period.values())
        and not any(d.has_estimates for d in days_in_period.values())
    )

    logger.info(
        "usage_for_period_summed",
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        total_kwh=total_kwh,
        days_present=len(days_in_period),
        days_expected=len(expected),
        is_complete=is_complete,
        missing_count=len(missing),
    )
    return UsageForPeriod(
        period_start=period_start,
        period_end=period_end,
        total_kwh=total_kwh,
        days_present=len(days_in_period),
        days_expected=len(expected),
        is_complete=is_complete,
        missing_dates=missing,
    )
