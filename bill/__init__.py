"""Independent 4Change bill computation + reconciliation against the charged amount.

Pipeline: parse the bill-ready email (`email_notice`) → sum SMT usage over its
period (`usage`) → compute the bill (`calculator`) → compare within tolerance
(`reconcile`). See `reference/4change-bill-reconciliation.md`.
"""

from .calculator import DEFAULT_PLAN_TERMS, compute_bill
from .email_notice import fetch_bill_notices, parse_bill_notice
from .models import (
    BillEstimate,
    BillNotice,
    BillReconciliation,
    PlanTerms,
    UsageForPeriod,
)
from .reconcile import reconcile
from .usage import sum_usage_for_period

__all__ = [
    "compute_bill",
    "DEFAULT_PLAN_TERMS",
    "fetch_bill_notices",
    "parse_bill_notice",
    "sum_usage_for_period",
    "reconcile",
    "BillEstimate",
    "BillNotice",
    "BillReconciliation",
    "PlanTerms",
    "UsageForPeriod",
]
