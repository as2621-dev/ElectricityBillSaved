"""Tests for bill.calculator — the pure bill computation.

These encode WHY the math matters: it must reproduce a real 4Change bill within
tolerance (else reconciliation is meaningless), and it must model the $125 credit
as a hard cliff at 1000 kWh (the entire product thesis hinges on that boundary).
"""

from __future__ import annotations

from bill.calculator import DEFAULT_PLAN_TERMS, compute_bill

# The user's real 5/13/2026 bill: 1066 kWh billed at $91.65.
REAL_BILL_KWH = 1066
REAL_BILL_TOTAL_CENTS = 9165
RECONCILE_TOLERANCE_CENTS = 100  # $1; a bill carries one-off items we don't model


def test_compute_bill_reproduces_real_4change_bill_within_one_dollar():
    """If our model can't reproduce a known bill, every reconciliation is noise."""
    estimate = compute_bill(REAL_BILL_KWH)
    assert abs(estimate.total_cents - REAL_BILL_TOTAL_CENTS) <= RECONCILE_TOLERANCE_CENTS


def test_credit_is_a_cliff_dropping_below_1000_costs_the_full_125():
    """The product exists because 999 kWh forfeits $125 that 1000 kWh keeps.

    The paradox is the point: using LESS (999) yields a LARGER bill than using
    one kWh more (1000). A regression that softened this cliff would break the
    core advice, so we assert the inversion explicitly, not just the flag.
    """
    just_under = compute_bill(999)
    at_threshold = compute_bill(1000)

    assert just_under.credit_applied is False
    assert just_under.bill_credit_cents == 0
    assert at_threshold.credit_applied is True
    assert at_threshold.bill_credit_cents == -round(DEFAULT_PLAN_TERMS.bill_credit_usd * 100)
    # Using one more kWh must make the bill dramatically cheaper, by ~the credit.
    assert just_under.total_cents - at_threshold.total_cents > 12000


def test_zero_usage_still_owes_tdu_base_plus_fees_no_credit():
    """A zero-usage cycle is not a zero bill: the TDU fixed charge still applies."""
    estimate = compute_bill(0)
    assert estimate.energy_charge_cents == 0
    assert estimate.bill_credit_cents == 0
    assert estimate.credit_applied is False
    # TDU fixed charge ($4.39) flows through, grossed up by fees/tax.
    assert estimate.total_cents >= round(DEFAULT_PLAN_TERMS.tdu_base_usd_per_cycle * 100)
    assert estimate.total_cents < 600
