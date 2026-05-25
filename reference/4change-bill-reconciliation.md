# 4Change Bill Reconciliation — Handoff Brief

Self-contained spec for a fresh coding agent. Read `reference/conventions.md` first; this doc
assumes those conventions. **No browser/portal scraping** — that approach was explicitly dropped.

## Goal

Each month, independently compute the user's 4Change electricity bill from SMT usage + plan terms,
then compare it against the dollar amount 4Change actually charged (read from a notification email).

- **Match** (within tolerance) → our bill model is validated → we can trust it to advise on the
  bill-credit threshold (the core product).
- **Mismatch** → flag a discrepancy to investigate.

We do **not** fetch the invoice PDF. We only need two things from 4Change: the **amount due** and the
**billing period**, both present in a notification email. Usage (kWh) comes from SMT. The bill is
computed by us.

## Account / plan facts (confirmed from the user's inbox)

- Provider: **4Change Energy** (Vistra Corp brand). Account **940005800209**.
- Plan: **4Change Energy Maxx Saver Value 12**. Contract start **04/15/2026** (12-month term).
- Service address: Houston, TX 77080 → TDU is **CenterPoint Energy Houston Electric**.
- Bill credit: **$125 when cycle usage ≥ 1000 kWh** (single cliff; confirm exact band against the EFL).
- SMT meter ESI ID observed in the CSVs: `1008901025004174540124`.

## Input 1 — the "bill ready" notification email

- **Sender (trigger):** `4change@notifications.4changeenergy.com`
- **Subject:** `Your bill's ready` (note the curly apostrophe U+2019; match on substring `bill` to be safe)
- **MUST EXCLUDE marketing sender:** `Info@mail.4changeenergy.com` (e.g. "Shoot. Upload. Win!" contest
  spam). A naive `from:4changeenergy.com` filter fires on junk — pin the `notifications` sender.
- **Body contains** (both HTML and plaintext parts): Account Number, **Amount Due**, **Due Date**,
  **Billing Period** (start–end). It does **NOT** contain kWh or any credit line.
- The "View Bill" button is a Vistra click-tracker (`click.notifications.vistracorp.com/?qs=...`) that
  redirects into the login-walled portal — not a PDF deep link. Ignore it.

**Sample (first bill, 2026-05-13):**
```
Amount Due:     $91.65
Due Date:       05/29/2026
Billing Period: 04/15/2026 - 05/10/2026
```

Parse these from the plaintext body with regex (labels: `Amount Due:`, `Due Date:`,
`Billing Period:`). Dates are `MM/DD/YYYY`. Period is inclusive of both endpoints.

## Input 2 — SMT interval usage

- Location: `data/smt/*.CSV`, fetched over IMAP from the SMT daily email subscription by the existing
  `meter/email_backend.py`. Parser: `meter/csv_parser.py` → `IntervalReading` / `DailyUsage`
  (`meter/models.py`).
- **Real CSV schema** (header, confirmed):
  `ESIID,USAGE_DATE,REVISION_DATE,USAGE_START_TIME,USAGE_END_TIME,USAGE_KWH,ESTIMATED_ACTUAL,CONSUMPTION_SURPLUSGENERATION`
  - `ESIID` has a leading single quote to strip. `USAGE_DATE` = `MM/DD/YYYY` civil date.
  - 15-minute intervals (96/day when complete). `ESTIMATED_ACTUAL`: `A` = actual.
  - `CONSUMPTION_SURPLUSGENERATION`: `Consumption` vs surplus generation — sum **Consumption** only.
- **CRITICAL COVERAGE GAP:** as of 2026-05-25 the only data on disk covers service dates
  **05/19–05/23/2026** (one service day per daily report; collection started ~05/20). The first bill's
  period (04/15–05/10) has **no usage data**, so that bill **cannot be validated yet**. Two fixes:
  1. **SMT historical backfill (one-time):** request an on-demand historical interval report from the
     SMT portal for 04/15→today; it emails the CSV → the existing IMAP pipeline ingests it.
  2. Otherwise wait for the first gap-free cycle (~06/11–07/10) given continuous daily collection.

## Input 3 — plan terms (EFL), one-time config

Needed to compute the bill. The EFL for Maxx Saver Value 12 / CenterPoint (issued ~04/2026) is linked
in the user's welcome email but behind a JS PDF viewer (`myaccount.4changeenergy.com/PdfViewer`,
Sitecore/Vercel) — **not fetchable programmatically**. Obtain via the user's downloaded PDF or the
public EFL (then verify it matches the user's version). Components to capture:

- Energy charge ¢/kWh (flat or tiered).
- REP base/monthly charge (if any).
- Bill credit: dollar amount + exact kWh threshold/band (expected $125 @ ≥1000 kWh).
- **CenterPoint TDU pass-through**: fixed monthly charge + per-kWh delivery charge.
- Minimum-usage fee (if any).
- Stated average price per kWh at 500 / 1000 / 2000 kWh (sanity-check targets).
- **TDU charges change ~semiannually** → store as *dated* config, not hardcoded. A reconciliation
  mismatch may legitimately trace to a TDU rate change rather than our error.

## Processing pipeline

1. **IMAP poll** Gmail for the trigger email (reuse `meter/email_backend.py` connection style; same
   `SMT_IMAP_*` Gmail app-password env). Exclude the marketing sender. Idempotent by message-id.
2. **Parse** email body → `BillNotice` { account_number, amount_due_usd_cents, due_date,
   period_start, period_end, email_message_id, received_at }.
3. **Sum usage** over `[period_start, period_end]` (civil dates, `America/Chicago`, inclusive) from the
   SMT data → `total_kwh` + a completeness flag (all days present, 96 intervals each, no estimates).
4. **Compute** `calculator(total_kwh, period, PlanTerms) → BillEstimate` with explicit line items
   (energy charge, TDU fixed + per-kWh, bill credit if usage ≥ threshold, base/min fee, taxes/fees).
5. **Reconcile** `BillEstimate.total` vs `BillNotice.amount_due_usd_cents` within tolerance
   (≤ ~$1 or ≤ ~1.5%) → `BillReconciliation` { status: match | discrepancy, delta_usd_cents, notes }.

**Precision caveat:** a TX residential bill = REP energy + TDU pass-through + bill credit + sales tax +
PUC assessment (~0.1655%). Penny-exact requires modeling every line; target reconcile-within-tolerance
and make line items explicit so a discrepancy is explainable.

## Proposed module layout (mirror `meter/`)

```
bill/
  __init__.py          # small public surface
  models.py            # BillNotice, PlanTerms, BillEstimate, BillReconciliation (Pydantic v2)
  email_notice.py      # IMAP fetch + parse the notice email -> BillNotice
  usage.py             # sum SMT kWh over a billing period -> total + completeness
  calculator.py        # pure (total_kwh, period, PlanTerms) -> BillEstimate  [the core]
  reconcile.py         # BillEstimate vs BillNotice within tolerance -> BillReconciliation
reconcile_4change_bill.py   # runner: poll -> parse -> usage -> compute -> reconcile -> log/persist
tests/bill/            # calculator (credit-cliff boundary, zero usage, happy), parser fixture,
                       # reconcile (match / mismatch / tolerance edge)
```

## Conventions to follow (from `reference/conventions.md`)

- Python 3.12, `.venv`. Pydantic v2 for all module-boundary data (no bare dicts).
- `structlog` JSON to **stderr**; no `print()` outside `cli/`; error logs carry `fix_suggestion`.
- **Money = USD cents (int).** **Billing dates = local civil `date`** (`America/Chicago`). Energy in kWh.
- Ruff line-length 120, double quotes. Run `ruff check --fix . && ruff format .` before done.
- Tests mirror source paths; names encode *why*; mock at the boundary (IMAP/file), not business logic.
- `calculator.py` must be a **pure function** — trivially unit-testable against EFL average-price points.

## Env vars

No 4Change password is needed (scraping dropped). The notice poll reuses the existing Gmail creds
(`SMT_IMAP_USERNAME`, `SMT_IMAP_APP_PASSWORD`). Optionally add config (not secret):
`FOURCHANGE_FROM_FILTER=4change@notifications.4changeenergy.com`, `FOURCHANGE_SUBJECT_FILTER=bill`.

## Open decisions (ask the user; do not assume)

1. **EFL numbers** — user-supplied PDF vs public EFL (then verify).
2. **SMT backfill** for 04/15–05/10 (to validate the $91.65 bill now) vs wait for a gap-free cycle.
3. **Scheduling** — macOS launchd ~4–6h inbox poll (recommended) vs Trigger.dev vs manual.

## Settled — do NOT relitigate

- **No portal scraping / browser automation.** Rejected for ToS breach, credential custody, 2FA, and
  fragility. Replaced by: read amount+period from email, compute bill from SMT usage + EFL, compare.
- The notification email is sufficient for the dollar value and period; everything else is computed.
