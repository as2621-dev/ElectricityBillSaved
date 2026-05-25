"""Pure bill calculator: (total_kwh, PlanTerms) -> BillEstimate.

The single source of truth for "what should this cycle cost." Kept pure and
side-effect-free so it is trivially unit-testable against the EFL average-price
points and a real bill. A TX residential bill is:

    REP energy charge
    + TDU delivery (fixed per cycle + per kWh)
    − bill credit (flat, only if usage >= threshold; the $125 cliff)
    + gross-receipts reimbursement + PUC assessment (~% of pre-tax charges)
    + sales tax (~% of the above)

Penny-exactness is not the goal (a bill also carries one-off items like a tax
refund credit); reconcile-within-tolerance is, and every line item is explicit so
a discrepancy is explainable.
"""

from __future__ import annotations

from datetime import date

from .models import BillEstimate, PlanTerms

# Default terms from the user's 5/13/2026 4Change bill (invoice 054853973570).
# TDU split reproduces the bill's $58.38 at 1066 kWh: 4.39 + 0.0506*1066 = 58.33.
# misc/tax factors calibrated to reproduce the $91.65 total within ~$0.20.
DEFAULT_PLAN_TERMS = PlanTerms(
    plan_name="4Change Energy Maxx Saver Value 12",
    effective_date=date(2026, 4, 15),
    energy_charge_usd_per_kwh=0.14611,
    tdu_base_usd_per_cycle=4.39,
    tdu_charge_usd_per_kwh=0.0506,
    bill_credit_usd=125.0,
    bill_credit_threshold_kwh=1000,
    misc_fee_factor=0.0205,
    sales_tax_factor=0.01,
)


def _usd_to_cents(amount_usd: float) -> int:
    """Round a USD amount to integer cents."""
    return round(amount_usd * 100)


def compute_bill(total_kwh: float, terms: PlanTerms = DEFAULT_PLAN_TERMS) -> BillEstimate:
    """Compute an itemized `BillEstimate` for a cycle's usage. Pure function.

    Args:
        total_kwh: Total cycle consumption in kWh.
        terms: Plan + delivery rate terms (defaults to the user's plan).

    Returns:
        A `BillEstimate` with each line item in USD cents.

    Example:
        >>> est = compute_bill(1066)
        >>> est.credit_applied
        True
    """
    energy_usd = terms.energy_charge_usd_per_kwh * total_kwh
    credit_applied = total_kwh >= terms.bill_credit_threshold_kwh
    credit_usd = -terms.bill_credit_usd if credit_applied else 0.0
    tdu_usd = terms.tdu_base_usd_per_cycle + terms.tdu_charge_usd_per_kwh * total_kwh
    pre_misc_usd = energy_usd + credit_usd + tdu_usd
    misc_usd = pre_misc_usd * terms.misc_fee_factor
    tax_usd = (pre_misc_usd + misc_usd) * terms.sales_tax_factor
    total_usd = pre_misc_usd + misc_usd + tax_usd

    return BillEstimate(
        total_kwh=total_kwh,
        energy_charge_cents=_usd_to_cents(energy_usd),
        tdu_delivery_cents=_usd_to_cents(tdu_usd),
        bill_credit_cents=_usd_to_cents(credit_usd),
        misc_fees_cents=_usd_to_cents(misc_usd),
        sales_tax_cents=_usd_to_cents(tax_usd),
        total_cents=_usd_to_cents(total_usd),
        credit_applied=credit_applied,
    )
