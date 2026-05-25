# Conventions

**Why this doc exists:** `/plan-phases` and `/run-phase` read this to keep code, logging, and structure consistent across phases. Without it, each phase drifts.

**When to update:** When a convention is added, changed, or retired. Never silently fork — surface the change here first.

---

## Language & runtime

- **Python 3.12+** only. No Node, no TS in this project unless a post-MVP dashboard is approved.
- Virtualenv lives in `.venv/` (gitignored). Activate before running anything.
- Dependencies pinned in `requirements.txt` (or `pyproject.toml` if we adopt `uv` / `poetry` later — decide once, not per-phase).

## Project layout

```
energyopt/
  __init__.py
  meter/        # SMT email/IMAP poller + CSV parser; pluggable backend
  weather/      # Open-Meteo client (history + forecast)
  thermostat/   # Aprilaire local control + guardrails
  model/        # Baseload + degree-day regression, balance-point tuning, projection
  recommend/    # RAISE/LOWER/HOLD decision + gap-closing setpoint solve
  storage/      # SQLite schema, migrations, repository functions
  report/       # Daily report rendering (text + HTML email body)
  notify/       # Outbound email (SMTP) delivery of the daily report
  cli/          # Typer entry points (ingest, report, recommend, apply, thermostat)
  shared/
    logger.py   # structlog setup
    settings.py # pydantic-settings (env vars, comfort bounds, target)
    exceptions.py
  config.py     # Re-exports settings; convenience for callers

tests/
  meter/ weather/ thermostat/ model/ recommend/ storage/
  conftest.py   # fixtures: fake SMT emails/CSVs, fake forecasts, in-memory SQLite
```

**Hard rules:**
- No file over 1000 lines (CLAUDE.md global rule). Agent-style modules under 500.
- Each subpackage exposes a small public interface via `__init__.py`. Tests target the public surface, not internals.

## Naming

- **Functions / variables / modules:** `snake_case`, intention-revealing.
  - `entity_id` not `id`, `entity_name` not `name`, `daily_total_kwh` not `kwh`.
- **Classes / Pydantic models:** `PascalCase`. E.g. `IntervalReading`, `DailyUsage`, `ModelFit`.
- **Constants:** `UPPER_SNAKE_CASE`. E.g. `DEFAULT_BALANCE_POINT_F = 65.0`.
- **CLI commands:** `kebab-case` verbs. `energyopt ingest`, `energyopt thermostat status`, `energyopt apply --dry-run`.

Units in names where ambiguous: `kwh`, `f` (Fahrenheit), `c` (Celsius), `usd`. E.g. `target_kwh`, `cool_setpoint_f`.

## Types & data flow

- Type hints on **every** function signature and class attribute. No bare `dict` at module boundaries.
- All structured data crossing a module boundary is a **Pydantic v2 model**. Internal helpers can use dataclasses or plain tuples.
- Pydantic models live in the module that *produces* them. E.g. `IntervalReading` in `meter/models.py`.
- Cross-module shared types (e.g. `BillingCycle`) go in `storage/models.py` since they map to tables.

## Time, dates, units

- All timestamps stored and computed in **UTC** (`datetime.datetime` with `tzinfo=ZoneInfo("UTC")`).
- Billing-cycle dates are **local civil dates** (Houston, `America/Chicago`) — store as `date`, not `datetime`. A billing day is the local day, not a 24h UTC window.
- Temperatures stored in **Fahrenheit** (user-facing units in Houston). Convert at API boundaries: Open-Meteo returns Celsius by default — request Fahrenheit at the query level; the **Aprilaire protocol speaks Celsius** — convert F↔C inside `thermostat/models.py` so the rest of the codebase stays Fahrenheit.
- Energy in **kWh** everywhere. No watt-hours, no joules.
- Money in **USD cents as integer** if dollars ever enter the model (Phase 3 §4.5 target conversion). Avoid floating-point dollars.

## Logging (structured JSON via `structlog`)

`shared/logger.py` configures structlog to emit JSON to stdout. Every log line carries:

- `event` — snake_case verb phrase (`ingest_started`, `model_fit_completed`, `setpoint_write_blocked`).
- Contextual fields relevant to the event.
- For errors: `error_type`, `error_message`, and **`fix_suggestion`** (mandatory — what would unblock this).

Examples:

```python
logger.info("ingest_started", source="smt_lib", target_date="2026-05-18")
logger.info("ingest_completed", source="smt_lib", target_date="2026-05-18",
            intervals_count=96, daily_total_kwh=42.7)
logger.error("smt_auth_failed",
             error_type="HTTPStatusError",
             error_message=str(exc),
             fix_suggestion="Verify SMT_USERNAME/SMT_PASSWORD in .env; if recently rotated, log into the SMT portal once to clear the captcha.")
```

Forbidden:
- `print()` outside of `cli/` (CLI may print user-facing prose; everything else logs).
- Logging secret values (passwords, tokens, full URLs with tokens in query strings).
- Free-form English log messages without structured fields.

## Errors

- Custom exceptions in `shared/exceptions.py`, one per failure class: `MeterIngestError`, `ThermostatConnectionError`, `GuardrailViolation`, `ModelFitError`.
- Catch at the CLI boundary, log with `fix_suggestion`, exit with a non-zero code. Don't swallow in the middle.
- A `GuardrailViolation` from `thermostat/` is always fatal for that command — never auto-relax a guardrail.
- Per Rule 12 ("fail loud"): a partially completed ingest must log `ingest_partial` (not `ingest_completed`) and exit non-zero so cron retries cleanly.

## Configuration & secrets

- `.env` (gitignored) holds secrets. `.env.example` is the onboarding contract.
- `shared/settings.py` defines a `Settings(BaseSettings)` Pydantic model with every config field typed and documented.
- **Never** log a secret value. **Never** put a secret in a default. Required secrets must raise on missing.
- User preferences (target, comfort bounds, balance-point seed, ZIP/lat-lon) live in the same `Settings` model — they're just non-secret config.

Required env vars for M1+:
```
# Primary M1 ingest path: SMT daily email subscription, parsed via IMAP
SMT_IMAP_HOST=imap.gmail.com
SMT_IMAP_PORT=993
SMT_IMAP_USERNAME=
SMT_IMAP_APP_PASSWORD=             # Gmail app password (not the account password)
SMT_IMAP_MAILBOX=INBOX/SMT         # dedicated label/folder for SMT emails
SMT_EMAIL_FROM_FILTER=donotreply@smartmetertexas.com
SMT_ESI_ID=                        # used to validate CSV rows belong to our meter
SMT_METER_NUMBER=

# Fallback M1 ingest path: unofficial smart-meter-texas portal-scraping library
SMT_PORTAL_USERNAME=
SMT_PORTAL_PASSWORD=

# Weather
WEATHER_LAT=29.7604                # Houston default; override per user
WEATHER_LON=-95.3698

# Storage
DB_PATH=./data/energyopt.db

# Comfort & safety bounds (enforced in thermostat/, not bypassable)
COMFORT_COOL_MIN_F=68
COMFORT_COOL_MAX_F=80
COMFORT_HEAT_MIN_F=60
COMFORT_HEAT_MAX_F=72
MAX_SETPOINT_STEP_F=3

# Target
TARGET_KWH=                        # if set, this is the M1+ target
TARGET_MODE=previous_cycle         # one of: kwh | usd | previous_cycle

# Decision algorithm tuning
DECISION_DEADBAND_PCT=0.03         # hysteresis band as fraction of target
DECISION_CONFIDENCE_Z=1.28         # ~90% upper bound for "definitely hit target"
DECISION_NO_REVERSE_DAYS=2         # anti-thrash: don't reverse within N days

# Outbound email (notify/)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=
SMTP_APP_PASSWORD=                 # Gmail app password (not the account password)
EMAIL_FROM=
EMAIL_TO=
EMAIL_CADENCE=daily                # one of: daily | on_action

# Aprilaire (M3+) — two units, one per floor
APRILAIRE_2F_HOST=                 # static IP / DHCP reservation, 2nd floor
APRILAIRE_1F_HOST=                 # static IP / DHCP reservation, 1st floor
APRILAIRE_PORT=8000                # real S86WMUPR port (7001 is the mock server only)
```

## Testing

- `pytest` + `pytest-asyncio`. Tests mirror source paths.
- **Per Rule 9:** test names encode *why*. `test_signature_detector_attributes_continuous_8kw_block_to_ev` beats `test_detector_works`.
- Minimum per public function: happy path, one failure path, one edge case.
- **Mock at the boundary, not inside business logic.** Mock `httpx.AsyncClient.get`, mock `pyaprilaire.Client`, mock SQLite by using an in-memory DB fixture — don't mock `model.fit_degree_day`.
- Never hit real SMT/Open-Meteo/Aprilaire in unit tests. A separate `tests/integration/` directory holds opt-in live tests, skipped by default (marker: `@pytest.mark.live`).
- Record one real SMT response and one real Open-Meteo response as JSON fixtures early — they're the difference between confidence and hope when the libraries change.

## Linting & formatting

- **Ruff** (latest) for lint + format. Line length 120. Double quotes.
- Run `ruff check --fix .` and `ruff format .` before any commit.
- No `# noqa` without a justifying comment on the same line.

## CLI conventions (Typer)

- One verb per command, one Typer sub-app per major module.
- Every command that mutates external state (`apply`, `thermostat set`) defaults to `--dry-run=True`. Real writes require `--apply` (or `--no-dry-run`) plus a confirmation prompt unless `--yes` is passed.
- `--json` flag on every read command for machine output (the daily-report cron uses it).
- Exit codes: `0` success, `1` user error / guardrail, `2` upstream failure (SMT/weather/thermostat unreachable). Cron differentiates retry vs alert based on this.

## Git / commits

- Conventional commits via `/commit`. Never `-A`, never `--amend`, never `--no-verify`.
- Phase end produces exactly one commit (`/run-phase` handles this).
