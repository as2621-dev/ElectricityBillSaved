"""Reconcile our computed bill against 4Change's charged amount, within tolerance.

A TX bill carries small one-off line items we don't model (e.g. a tax-refund
credit), so an exact match is unrealistic; we accept the larger of an absolute or
a relative tolerance. If the period's usage is incomplete we cannot fairly compare,
so the result is 'incomplete' rather than a misleading discrepancy.
"""

from __future__ import annotations

import structlog

from .models import BillEstimate, BillNotice, BillReconciliation, UsageForPeriod

logger = structlog.get_logger()

DEFAULT_TOLERANCE_CENTS: int = 100  # $1.00 absolute floor
DEFAULT_TOLERANCE_FRACTION: float = 0.015  # 1.5% of the charged amount


def reconcile(
    estimate: BillEstimate,
    notice: BillNotice,
    usage: UsageForPeriod,
    *,
    tolerance_cents: int = DEFAULT_TOLERANCE_CENTS,
    tolerance_fraction: float = DEFAULT_TOLERANCE_FRACTION,
) -> BillReconciliation:
    """Compare a `BillEstimate` to a `BillNotice` and classify the result.

    Args:
        estimate: Our computed bill for the period.
        notice: The amount 4Change actually charged.
        usage: The period's usage summary (gates on completeness).
        tolerance_cents: Absolute tolerance floor (cents).
        tolerance_fraction: Relative tolerance as a fraction of the charged amount.

    Returns:
        A `BillReconciliation` with status match | discrepancy | incomplete.
    """
    delta = estimate.total_cents - notice.amount_due_cents
    tolerance = max(tolerance_cents, round(notice.amount_due_cents * tolerance_fraction))

    if not usage.is_complete:
        status = "incomplete"
        notes = (
            f"Usage incomplete for {usage.period_start}–{usage.period_end}: "
            f"{usage.days_present}/{usage.days_expected} days"
            + (f", missing {len(usage.missing_dates)}" if usage.missing_dates else "")
            + ". Backfill SMT data for this period before trusting the comparison."
        )
    elif abs(delta) <= tolerance:
        status = "match"
        notes = f"Within tolerance (|Δ| {abs(delta)}¢ ≤ {tolerance}¢). Bill model validated for this cycle."
    else:
        status = "discrepancy"
        notes = (
            f"|Δ| {abs(delta)}¢ exceeds {tolerance}¢. Investigate: TDU rate change (dated terms?), "
            f"plan-vintage energy rate, or a one-off line item on the bill."
        )

    logger.info(
        "bill_reconciled",
        status=status,
        estimate_total_cents=estimate.total_cents,
        notice_amount_cents=notice.amount_due_cents,
        delta_cents=delta,
        tolerance_cents=tolerance,
    )
    return BillReconciliation(
        status=status,
        estimate_total_cents=estimate.total_cents,
        notice_amount_cents=notice.amount_due_cents,
        delta_cents=delta,
        tolerance_cents=tolerance,
        notes=notes,
    )
