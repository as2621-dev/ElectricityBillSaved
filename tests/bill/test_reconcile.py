"""Tests for bill.reconcile — comparing our estimate to 4Change's charged amount.

WHY these matter: a 'match' is what licenses us to trust the bill model for credit
advice, so the tolerance boundary and the incompleteness gate must behave exactly.
A false 'match' on incomplete usage would validate the model on data it never saw.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from bill.models import BillEstimate, BillNotice, UsageForPeriod
from bill.reconcile import reconcile


def _estimate(total_cents: int) -> BillEstimate:
    return BillEstimate(
        total_kwh=1000.0,
        energy_charge_cents=0,
        tdu_delivery_cents=0,
        bill_credit_cents=0,
        misc_fees_cents=0,
        sales_tax_cents=0,
        total_cents=total_cents,
        credit_applied=True,
    )


def _notice(amount_cents: int) -> BillNotice:
    return BillNotice(
        account_number="940005800209",
        amount_due_cents=amount_cents,
        due_date=date(2026, 5, 29),
        period_start=date(2026, 4, 15),
        period_end=date(2026, 5, 10),
        email_message_id="test-id",
        received_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )


def _usage(*, complete: bool) -> UsageForPeriod:
    return UsageForPeriod(
        period_start=date(2026, 4, 15),
        period_end=date(2026, 5, 10),
        total_kwh=1066.0,
        days_present=26 if complete else 5,
        days_expected=26,
        is_complete=complete,
        missing_dates=[] if complete else [date(2026, 4, 16)],
    )


def test_reconcile_matches_when_estimate_within_tolerance():
    """A near-exact computation on complete usage validates the model."""
    result = reconcile(_estimate(9182), _notice(9165), _usage(complete=True))
    assert result.status == "match"
    assert result.delta_cents == 17


def test_reconcile_flags_discrepancy_beyond_tolerance():
    """A large gap on complete usage must be surfaced, not absorbed."""
    result = reconcile(_estimate(9165), _notice(5000), _usage(complete=True))
    assert result.status == "discrepancy"
    assert result.delta_cents == 4165


def test_reconcile_reports_incomplete_when_usage_has_a_gap_even_if_amounts_equal():
    """Equal dollars must NOT read as 'match' when we never had the period's usage."""
    result = reconcile(_estimate(9165), _notice(9165), _usage(complete=False))
    assert result.status == "incomplete"


def test_tolerance_boundary_is_inclusive():
    """Delta exactly at the absolute $1 floor counts as a match (<=, not <)."""
    # Small bill so the relative 1.5% (≈1¢) is below the $1 absolute floor → tol=100.
    result = reconcile(_estimate(1100), _notice(1000), _usage(complete=True))
    assert result.tolerance_cents == 100
    assert result.delta_cents == 100
    assert result.status == "match"
