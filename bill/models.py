"""Pydantic models for 4Change bill reconciliation.

We independently compute the user's electricity bill from SMT usage + plan terms,
then compare it to what 4Change actually charged (read from a notification email).
See `reference/4change-bill-reconciliation.md`.

Money is USD **cents (int)** at every amount boundary (per `reference/conventions.md`);
per-unit rates stay as USD floats since a rate like $0.14611/kWh has sub-cent precision.
Billing dates are local civil `date` (Houston, America/Chicago), never datetimes.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

ReconcileStatus = Literal["match", "discrepancy", "incomplete"]


class PlanTerms(BaseModel):
    """Retail + delivery rate terms needed to compute a bill. Store as DATED config.

    TDU pass-through charges change ~semiannually, so a reconciliation mismatch can
    legitimately mean the rate changed, not that our math is wrong — hence
    `effective_date`. Values are taken from a real bill / EFL, not guessed.
    """

    plan_name: str = Field(..., description="Retail plan name, e.g. '4Change Energy Maxx Saver Value 12'")
    effective_date: date = Field(..., description="Date these terms took effect (TDU rates are dated)")
    energy_charge_usd_per_kwh: float = Field(..., description="REP energy charge, USD per kWh")
    tdu_base_usd_per_cycle: float = Field(..., description="TDU fixed delivery charge per billing cycle, USD")
    tdu_charge_usd_per_kwh: float = Field(..., description="TDU volumetric delivery charge, USD per kWh")
    bill_credit_usd: float = Field(..., description="Flat bill credit applied at/above the threshold, USD")
    bill_credit_threshold_kwh: int = Field(..., description="Cycle kWh at/above which the credit applies")
    misc_fee_factor: float = Field(
        ..., description="Gross-receipts reimb + PUC assessment, as a fraction of pre-tax charges"
    )
    sales_tax_factor: float = Field(..., description="Sales tax as a fraction of (pre-tax charges + misc fees)")


class BillEstimate(BaseModel):
    """Our computed bill for a cycle, as explicit line items in USD cents."""

    total_kwh: float = Field(..., description="Cycle usage the estimate was computed for")
    energy_charge_cents: int = Field(..., description="REP energy charge (cents)")
    tdu_delivery_cents: int = Field(..., description="TDU fixed + volumetric delivery (cents)")
    bill_credit_cents: int = Field(..., description="Bill credit applied (<= 0; 0 if under threshold)")
    misc_fees_cents: int = Field(..., description="Gross-receipts + PUC fees (cents)")
    sales_tax_cents: int = Field(..., description="Sales tax (cents)")
    total_cents: int = Field(..., description="Estimated total amount due (cents)")
    credit_applied: bool = Field(..., description="True if usage met the bill-credit threshold")


class BillNotice(BaseModel):
    """The dollar facts parsed from a 4Change 'Your bill's ready' notification email."""

    account_number: str = Field(..., description="4Change account number from the email")
    amount_due_cents: int = Field(..., description="Amount due charged by 4Change (cents)")
    due_date: date = Field(..., description="Payment due date")
    period_start: date = Field(..., description="Billing period start (inclusive)")
    period_end: date = Field(..., description="Billing period end (inclusive) = the meter-read date")
    email_message_id: str = Field(..., description="IMAP message id the notice was parsed from (idempotency key)")
    received_at: datetime = Field(..., description="When the notification email was received")


class UsageForPeriod(BaseModel):
    """SMT usage summed over a billing period, with a completeness verdict."""

    period_start: date = Field(..., description="Period start (inclusive)")
    period_end: date = Field(..., description="Period end (inclusive)")
    total_kwh: float = Field(..., description="Sum of consumption kWh over the period")
    days_present: int = Field(..., description="Service days found with data in the period")
    days_expected: int = Field(..., description="Service days the period spans (inclusive)")
    is_complete: bool = Field(..., description="True iff every day present, each 96 intervals, no estimates")
    missing_dates: list[date] = Field(default_factory=list, description="Period days with no SMT data")


class BillReconciliation(BaseModel):
    """Result of comparing our `BillEstimate` to the 4Change `BillNotice`."""

    status: ReconcileStatus = Field(..., description="match | discrepancy | incomplete (usage gap)")
    estimate_total_cents: int = Field(..., description="Our computed total (cents)")
    notice_amount_cents: int = Field(..., description="4Change's charged amount (cents)")
    delta_cents: int = Field(..., description="estimate - notice (cents); positive = we over-estimated")
    tolerance_cents: int = Field(..., description="Allowed |delta| for a 'match' (cents)")
    notes: str = Field(..., description="Human-readable explanation, esp. for discrepancy/incomplete")
