# Stack Notes — Gotchas & Things Future-You Will Forget

**Why this doc exists:** Each library in this stack has a sharp edge that will burn an afternoon if forgotten. `/run-phase` reads this before writing code against any of them.

**When to update:** Any time a new gotcha is discovered, a library version is bumped, or a workaround stops being necessary.

---

## Python runtime

- **Python 3.12+** required. We use `datetime.UTC`, `ZoneInfo`, `match` statements, and PEP 695 typing in places — earlier versions will silently fail in subtle ways.
- Always work inside `.venv/`. CI-style commands assume the venv is active.
- `requirements.txt` is the source of truth until/unless we adopt `uv` or `poetry` formally (decide before M2; don't mix tools).

## Pinned (or near-pinned) library versions

These exist because they have known quirks; pin to a version range that has been validated against this codebase.

| Library | Version (initial pin) | Why pinned |
|---|---|---|
| `pyaprilaire` | `>=0.8.0` | Local TCP protocol; older releases miss reconnect handling. |
| `httpx` | `>=0.27,<1.0` | Async client across `weather/` and any other HTTP work. |
| `pydantic` | `>=2.6,<3.0` | v2 API only; v1-style validators will not work. |
| `pydantic-settings` | `>=2.2` | Used by `shared/settings.py`. |
| `structlog` | `>=24.1` | JSON logger config in `shared/logger.py`. |
| `typer` | `>=0.12` | CLI; uses Click under the hood. |
| `pandas` | `>=2.2` | For regression input shaping. Optional — `numpy` alone works if we skip pandas. |
| `numpy` | `>=1.26` | Required by the regression code. |
| `scipy` | `>=1.12` | `scipy.stats.linregress` or `scipy.optimize` for balance-point tuning. |
| `pytest` + `pytest-asyncio` | latest | Tests. |
| `ruff` | latest | Lint + format. |
| `freezegun` | latest | Time-travel in tests around billing-cycle math. |
| `smart-meter-texas` | `>=0.5.5,<1.0` | **Fallback only.** Unofficial portal-scraping lib; pin tight because SMT changes the portal periodically. |

Standard-library `imaplib` + `email` cover the primary IMAP ingest path — no extra dep needed. `imap-tools` is a nicer wrapper we *may* adopt in M1 if `imaplib` boilerplate gets ugly; decide once.

## Smart Meter Texas — Email subscription (primary M1 path)

**The actual delivery path:**

- In the SMT portal under `Manage Subscriptions`, create a subscription:
  - **Report Type:** `Energy Data 15 Min Interval`
  - **Frequency:** `Daily`
  - **Format:** `CSV` (default for email delivery; JSON may be offered)
  - **Delivery Type:** `Email` ← critical; **not** `API`
  - **ESIID:** the user's service-point ID
- SMT emails a daily message to the address on file with the file as an attachment. Arrival time is typically morning-after for the prior service day.

**Gotchas:**
- **Data is ~24h late.** Same as the portal — the email contains *yesterday's* day. Never query for "today."
- **Attachment naming is not promised stable.** Match by MIME type (`text/csv` or `application/octet-stream` with `.csv`/`.zip` extension) and by sender, not by filename.
- **The email itself sometimes ships as ZIP.** Be ready to unzip and find the CSV inside; one entry expected per service day.
- **Subscription can pause silently.** If SMT changes their portal or your email bounces, the subscription quietly stops delivering. M1 must alert if no email has arrived for >36h (cron checks the mailbox; absence is an error event with `fix_suggestion`).
- **96 intervals per complete day.** Anything less means partial data; mark the row `source='partial'` and re-ingest the next day.
- **Mailbox hygiene matters.** Use a dedicated Gmail label (`SMT/`) or a separate mailbox. After successful ingest, mark the message as read (don't delete — keep the raw as audit trail). Idempotency key: the (date, ESIID) tuple from the CSV, not the email message id.
- **Gmail-specific:** the IMAP password must be an **app password**, not the account password (Gmail blocks plain IMAP logins with the account password since 2022).
- **Time zone:** the CSV's interval timestamps are local (Houston / `America/Chicago`). Convert to UTC for storage; keep the local day as the partition key (`daily_usage.date`).

**Wrap behind our `meter/` interface.** Email path is `meter/email_backend.py`. The portal-scraping library is `meter/smt_lib_backend.py` and is selected only when `email_backend` fails N days in a row.

## Smart Meter Texas — Portal-scraping fallback (`smart-meter-texas` lib)

- **Same data, same ~24h latency** as the email path. Use only when the email path is broken.
- **Login captcha trap.** If the portal ever shows a captcha (after password change, region change, or too many failed logins), the library fails with an opaque auth error. Fix: log into the SMT web portal manually once to clear it.
- **Session reuse.** Authenticate once per run, then reuse the session for all queries. Re-authenticating per call is rate-limited and rude.
- **ESI ID + Meter Number** are both required to disambiguate accounts with multiple service points.
- **Library breakage risk.** SMT updates the portal periodically; the lib has lagged in the past. Treat this backend as *fragile* — failures here should fall back to manual CSV ingest, not crash the daily run.

## Smart Meter Texas — Push-API (intentionally not pursued)

For future-you: yes, SMT has a real push API. It is **not worth pursuing** for this project. Requirements (per SMT Interface Guide and Hubitat community):

- CA-authorized SSL certificate (production) or self-signed (staging).
- A registered domain you control.
- Static public IP, whitelisted by SMT support (IP changes require a support ticket, days of turnaround).
- A public HTTPS endpoint hosted by you that SMT POSTs daily JSON payloads to.
- JWT auth (post-Sept-2025).

You get the same data, with the same ~24h latency, as the email subscription. Not worth it for one house.

## `pyaprilaire` (Aprilaire local TCP)

- **Confirmed hardware: two Aprilaire S86WMUPR units** (FW 1.8.6), one per floor (1st & 2nd). Both behave like the 8800-series automation socket and are supported by `pyaprilaire` / HA 2025.1.0.
- **Real-device port is 8000, NOT 7001.** Port 7001 is only the `pyaprilaire` mock server. Each unit needs a static IP / DHCP reservation — a changed IP silently breaks the cron job. Env: `APRILAIRE_2F_HOST`, `APRILAIRE_1F_HOST`, `APRILAIRE_PORT=8000`.
- **The protocol speaks Celsius.** Convert F↔C at the `thermostat/models.py` boundary; the rest of the codebase stays in Fahrenheit (see conventions.md units rule).
- **Two units = two independent connections.** The one-connection-at-a-time rule is *per device*, so we hold one managed connection to each. Never open a second connection to the *same* unit — repeated/parallel connects hang it (recovery: physical power-cycle). Run a long-lived daemon per unit, or serialize CLI calls with a per-host file lock.
- **Enable Automation mode on each device first.** S86WMUPR path: Main Menu → Settings → hold Up+Down 3s → Installer Settings → Connection Type → **Automation System**. This likely disables the Healthy Air app for that unit (reversible).
- **Whole-house meter, two zones → uniform control.** The SMT meter is whole-house and can't attribute kWh per floor, so the model fits whole-house `β_cool`. The recommendation applies the **same Δ to both zones**; we don't optimize floors independently (no per-floor signal to do it from).
- **Mock server for dev:** `python -m pyaprilaire.mock_server` (listens on 7001). All `thermostat/` tests must work against the mock; real-device tests are opt-in and marked `@pytest.mark.live`.
- **Reads are cheap; writes are not.** Reading state is safe at high frequency. Writing setpoints causes HVAC to react — never write in tests against a real device, and never write more than once per minute in production code.
- **Setpoint deadband.** The thermostat enforces a minimum gap (typically 2°F) between heat and cool setpoints. Validate before write or the device will silently clamp.
- **Manual override detection.** If the indoor setpoint changes between two reads without our code initiating it, treat it as user override and stand down for the remainder of the day (see comfort rule §7-4 of the brief).

## Open-Meteo

- **Free, no API key.** Two endpoints we care about:
  - History: `https://archive-api.open-meteo.com/v1/archive` — historical daily temperatures.
  - Forecast: `https://api.open-meteo.com/v1/forecast` — up to ~16 days out.
- **Pass `temperature_unit=fahrenheit`** in every request — saves a conversion step and a class of bugs.
- **`daily=temperature_2m_mean,temperature_2m_max,temperature_2m_min`** is the field set we use. `temperature_2m_mean` is our `T_avg` for degree-day math.
- **Timezone:** pass `timezone=America/Chicago` so daily aggregates align with Houston civil days (and with the billing cycle).
- **Forecast horizon < billing cycle.** When `days_remaining > forecast_days`, fall back to **30-day trailing average** of historical `T_avg` for the gap (simple, defensible, no extra dep). Document the fallback in the recommendation output so it's visible.
- **No rate limit for personal use**, but cache responses for a day — re-querying inside one cron run is wasteful.

## Email delivery — SMTP (`notify/`)

- **Default transport: Gmail SMTP** (`smtp.gmail.com:587`, STARTTLS) with an **app password** — Gmail rejects plain-password SMTP since 2022. Use stdlib `smtplib` + `email.message.EmailMessage`; no extra dep needed.
- **Don't reuse the SMT-receiving mailbox connection.** IMAP (inbound SMT) and SMTP (outbound report) are separate sessions even if they're the same Gmail account. Keep them in different modules (`meter/` vs `notify/`).
- **Send a multipart message:** `text/plain` (the same body the CLI prints) + `text/html` (formatted). Plain-text alone is acceptable for M1; add HTML when the M2 verdict layout justifies it.
- **Deliverability:** a single recipient (yourself) over authenticated Gmail SMTP rarely hits spam. If using a transactional provider (Resend/SES/Postmark) later, you'll need SPF/DKIM on the sending domain — out of scope for M1.
- **Idempotent send:** record `(report_date, sent_at)` so a re-run of the daily job doesn't double-email. The cron job should send at most one email per service day unless `--force`.
- **Failure is non-fatal but loud.** If the SMTP send fails, still write the report to file and stdout, log `email_send_failed` with `fix_suggestion`, and exit 2 so cron surfaces it — never silently swallow.
- **Cadence config (`EMAIL_CADENCE`):** `daily` always sends; `on_action` sends only when the verdict is RAISE or LOWER. Default `daily`.

## Decision algorithm — implementation notes (`recommend/`)

Full spec is in `plans/master-plan.md` → "Daily decision algorithm." Implementation gotchas:

- **Solve against the upper bound, not the mean.** "Definitely hit target" means the smallest raise Δ such that `projected_high(Δ) ≤ target`, where `projected_high = projected + DECISION_CONFIDENCE_Z · proj_σ`. Solving against the point estimate will under-recommend and miss on hot streaks.
- **Search Δ in whole degrees**, from 0 up to `MAX_SETPOINT_STEP_F`, clamped to `[COMFORT_COOL_MIN_F, COMFORT_COOL_MAX_F]`. The projection is monotonic in Δ (more raise → fewer kWh), so a linear scan or bisection both work; a scan is fine at this size.
- **Deadband prevents thrash** (`DECISION_DEADBAND_PCT`, default 3% of target). Also enforce `DECISION_NO_REVERSE_DAYS` (default 2): don't reverse a prior change unless `projected` has moved more than the deadband since it.
- **Refuse to recommend on a weak fit.** If the model has <14 days of data or `R² < 0.4`, emit HOLD with an explicit "insufficient data to recommend" note — do not guess.
- **Confidence band combines two sources:** regression residual `σ` scaled by `sqrt(days_remaining)`, plus forecast uncertainty (widen for days served by the trailing-average fallback beyond the ~16-day Open-Meteo horizon).
- **Persist every decision** to `recommendations(ts, projected_kwh, projected_high_kwh, target_kwh, verdict, recommended_cool_setpoint, applied)` so M2's accuracy can be measured (predicted vs actual cycle-end) — that's the project's success metric.

## SQLite

- **One DB file at `DB_PATH` (default `./data/energyopt.db`).** Back it up before any schema migration (literal `cp`; we're not paying for an enterprise migrator).
- **Foreign keys are OFF by default in SQLite.** Enable per connection: `PRAGMA foreign_keys = ON;`.
- **Use parameterized queries always.** No string interpolation into SQL — even for "trusted" values like dates.
- **Schema lives in `storage/schema.sql` and is applied idempotently on startup.** No Alembic in M1–M4; revisit only if migrations get gnarly.
- **Idempotent ingest.** Daily inserts use `INSERT OR REPLACE` (or `INSERT ... ON CONFLICT DO UPDATE`) keyed on `(date, esi_id)`. Safe to re-run cron after a transient failure.
- **WAL mode** (`PRAGMA journal_mode=WAL;`) for safe concurrent reader + scheduled writer.

## Scheduling (launchd / cron)

- **macOS hosts:** `launchd` is the correct mechanism — *not* user crontab, which is unreliable on modern macOS. A `~/Library/LaunchAgents/com.energyopt.daily.plist` runs the ingest+report at a fixed local time. Sleep-during-trigger behavior: launchd will run the missed job on wake if `StartCalendarInterval` is used.
- **Linux hosts (e.g., Raspberry Pi):** `cron` or `systemd timer`. Prefer `systemd timer` for journal logging and `OnFailure=` hooks.
- **Run after SMT publishes.** Target 10:00 local time — gives SMT all night to send the email. Earlier than ~8am risks fetching nothing.
- **Lock the run.** A simple `flock` on a PID file prevents overlapping cron invocations if a previous run hangs.

## Two-term daily model (baseload + degree-day)

- **Model:** `kwh(day) ≈ baseload + β_cool · CDD(day) + β_heat · HDD(day)`.
  - `baseload` absorbs the monthly-average non-HVAC load: fridge + EV charging + everything else.
  - `CDD(day) = max(0, T_avg(day) − T_balance)`; `HDD(day) = max(0, T_balance − T_avg(day))`.
- **Need ~14+ days of data for any signal, ~30+ for trustworthy coefficients.** Below that, `recommend` should refuse to recommend and just report.
- **Balance-point tuning:** sweep `T_balance` from 55°F to 75°F in 1°F steps, refit at each, pick the value with the lowest residual RMSE. `O(20 × n_days)` — fast even on a Pi.
- **Houston in cooling season:** expect `β_cool` ≫ `β_heat`. If a fit comes back with large `β_heat` in July, something is wrong (likely a labeling or unit bug).
- **R² is a sanity check, not a target.** Because the baseload absorbs EV-day variance, expect lower R² than a textbook degree-day fit — `R² ≥ 0.5` is acceptable here. Anything <0.4: log a warning, don't silently use it.
- **Residual variance feeds the recommendation's confidence band.** Don't quote a single projected-total number without a ± range when the underlying fit is noisy.
- **Baseload is fit over a trailing 30-day window** and re-fit each day. It moves slowly; large shifts (>15% week-over-week) usually mean a new appliance, a long EV trip, or a measurement glitch — log a warning.

## Misc

- **Houston timezone is `America/Chicago`** (Central, observes DST). Hard-code the IANA name, never an offset.
- **Don't commit `data/energyopt.db`.** Add `data/` to `.gitignore` early.
- **README.md must list:** install, env-var setup, first-run sequence (incl. enabling the SMT email subscription), daily report command, troubleshooting (mailbox empty, thermostat hang, partial-day data). Update at the end of every milestone.
