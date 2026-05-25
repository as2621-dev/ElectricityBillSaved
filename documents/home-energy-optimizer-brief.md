# Home Energy / Thermostat Optimizer — Build Spec

**Goal:** A personal tool that watches my home electricity consumption, knows where I am in my billing cycle, projects where I'll land by month-end given the weather forecast and my Tesla charging, and tells me how to adjust my Aprilaire thermostat to hit a target. Start with my house only. Build it in phases.

This document is written to be handed to Claude Code as the starting brief. It states what's been confirmed about my setup, the realistic integration options for each part, the algorithm, a phased build plan, and the open decisions to resolve along the way.

---

## 0. My setup (what we're building against)

- **Location:** Houston, TX.
- **Distribution utility (TDU):** CenterPoint Energy. In Texas the meter data flows through **Smart Meter Texas (SMT)**, the statewide portal shared by CenterPoint, Oncor, AEP, and TNMP. This is the data source for consumption — not my retail provider's app.
- **Thermostat:** Aprilaire (model TBD — must confirm; see Phase 2). Likely an 8800-series Automation or 6000-series Zone Control, which are the locally-controllable ones.
- **EV:** Tesla. Charges at home, mostly bumping certain days well above baseline ("up days").
- **Billing:** Retail electricity plan (provider/plan structure TBD — confirm whether flat-rate, tiered, or free-nights, since that changes whether we optimize for kWh or for $).

**Things to confirm before/early in the build:**

1. Exact Aprilaire model number and whether it supports "Automation" connection mode.
2. Retail plan: is the target a kWh budget, a dollar budget, or "don't exceed last month"?
3. Whether I already have SMT portal credentials (ESI ID + meter number + a login).

---

## 1. Architecture at a glance

Four data sources feed one model that produces one recommendation, optionally closing the loop back to the thermostat.

```
  Smart Meter (SMT)  ─┐
  Tesla charging      ─┤
  Weather forecast    ─┼──>  energy model + projection  ──>  recommendation  ──(optional)──>  Aprilaire
  Billing cycle dates ─┘                                                                            └──>  daily report (terminal / file / notification)
```

Suggested module layout (Python, but language is open):

```
energyopt/
  meter/        # Smart Meter Texas client + CSV/Green Button parser
  vehicle/      # Tesla charging history (or charge-signature detection)
  weather/      # forecast client
  thermostat/   # Aprilaire read + control
  model/        # degree-day regression, projection
  recommend/    # decision logic -> setpoint suggestion
  storage/      # local SQLite of daily readings + model fits
  report/       # daily report rendering
  cli/          # entry points (ingest, report, recommend, apply)
  config.py     # secrets, budget target, comfort bounds, balance point
```

**Run model:** a scheduled daily job (cron / launchd / systemd timer) that ingests yesterday's data, refits, re-projects, and emits a report. Recommendation and "apply to thermostat" stay manual until trust is built.

---

## 2. Phase 1 — Get the consumption data and a daily report

This is the first thing to nail down. Everything else depends on reliably getting my usage numbers.

### Important reality about SMT data latency

SMT data is roughly a day behind. Meters transmit once daily (around midnight) and the portal typically updates 24–48h later. So:

- A daily report (yesterday and earlier) is fully achievable.
- "Every 15 minutes" is available as granularity (96 intervals/day), but not in real time — you get yesterday's 96 intervals, not the current quarter-hour.
- True real-time monitoring would require separate hardware (a CT-clamp energy monitor like an Emporia Vue / Sense on the panel). Treat that as an optional later add-on, not Phase 1.

So **Phase 1 target:** a reliable daily pull of interval + daily-total data into local storage, plus a daily report. Don't over-invest in real-time.

### Options for pulling SMT data (in rough order of preference)

1. **Unofficial `smart-meter-texas` Python library** (the one the Home Assistant SMT integration is built on). Logs in with my SMT portal username/password and pulls interval/daily reads. Best balance of automation vs effort. Risk: it's unofficial and SMT occasionally changes auth, so wrap it behind our own `meter/` interface so it can be swapped.
2. **Scheduled CSV / Green Button export.** SMT can email a daily usage report or export CSV / Green Button XML. We ingest the file. Most robust against API changes; needs either manual download or an email-scraping step.
3. **Email subscription + parser.** Turn on SMT's daily usage email, parse it on arrival. Good fallback.
4. **Browser automation (Playwright) against the SMT portal.** Most fragile; use only if 1–3 fail. The portal is a JS app, so it'd need a real headless browser, not plain HTTP.
5. **Official SMT REST API.** Exists but is built for registered third parties/CSPs and requires a CA-authorized SSL cert — impractical for a single household. Skip.

> Build `meter/` as a clean interface (`get_intervals(date)`, `get_daily_total(date)`, `get_billing_periods()`) with a pluggable backend, and start with option 1 or 2.

### Phase 1 deliverable

- `energyopt ingest` — pulls and stores yesterday's intervals + daily total in SQLite.
- `energyopt report` — prints/saves a daily report: yesterday's kWh, 7-day trend, month-to-date total, days into cycle.
- Idempotent ingest (safe to re-run; dedupe on date).

---

## 3. Phase 2 — Read and control the Aprilaire thermostat

### Confirm the model first

Local control works for **8800-series Home Automation** and **6000-series Zone Control** Aprilaire thermostats via the `pyaprilaire` Python library (same library the Home Assistant Aprilaire integration uses). Not every "smart" Aprilaire supports it — some marketed as automation-capable don't expose automation mode. First step: read the model number and verify.

### How local control works

- Enable **Automation mode** on the thermostat: *Contractor Menu → Connection Type → Automation*. (Exact steps vary by model — check the manual.)
- `pyaprilaire` then connects over a local TCP socket (default port 7001) on the LAN. No cloud needed.
- **Hard constraint:** the thermostat allows only **one** automation connection at a time. Don't run multiple connectors. Repeated/parallel connects can hang the thermostat (recover with a power cycle). Our client must hold a single managed connection and reconnect cleanly.
- For development without touching the real device, `pyaprilaire` ships a mock server (`python -m pyaprilaire.mock_server`) — build and test against that first.

If the model turns out unsupported, fallbacks are the Aprilaire cloud app / Google Assistant integration (cloud, clunkier), but local is strongly preferred.

### Phase 2 deliverable

`thermostat/` interface:

- `get_state()` → current mode, current setpoint(s), indoor temp, humidity.
- `set_cool_setpoint(f)` / `set_heat_setpoint(f)` with guardrails enforced in code (see §7).
- `energyopt thermostat status` and `energyopt thermostat set --cool 76` CLI commands.
- All control behind a `--dry-run` default; real writes require an explicit flag.

---

## 4. Phase 3 — The algorithm (the actual brain)

Five steps: figure out the cycle, build a weather→energy model, separate out Tesla charging, project forward using a forecast, and convert the gap into a setpoint recommendation.

### 4.1 Find where the billing cycle started, and consumption-to-date

- Get the cycle start/end from either the previous bill's service period dates or SMT's monthly billing reads (24 months available). Prefer SMT so it's automatable; use the uploaded bill to seed/validate.
- Model the cycle as `[cycle_start, cycle_end)`; `days_elapsed` and `days_remaining` derive from today.
- `MTD_kwh` = sum of daily totals from `cycle_start` to yesterday (from our stored data).

### 4.2 Separate Tesla charging from everything else

Tesla charging is a big, discrete load that would otherwise pollute the weather model. Two ways to handle it — implement (a), optionally add (b):

**(a) Detect charging from the meter signature (no Tesla API, free).** EV charging shows up in the 15-min interval data as a sustained block (~7–11 kW for L2, often overnight). Detect those blocks, attribute the kWh to "EV," and subtract from the day's total to get a clean "house" daily kWh. Cheap and avoids API setup; good enough to start.

**(b) Tesla Fleet API for ground truth (optional, costs a little).** The current path is the **Tesla Fleet API** (the old Owner API is gone). It has a charging history endpoint (paginated sessions + invoice PDFs). Requires registering an app at developer.tesla.com, OAuth with a third-party token, and selecting scopes. It's pay-per-use but each account gets a ~$10/month credit that covers light personal use. Use this to validate/correct the signature detector, not as a hard dependency.

Output of this step: for each historical day, `house_kwh = total_kwh − ev_kwh`, plus a model of typical EV kWh per "charging day" and how often charging days occur (for projection).

### 4.3 Fit a weather → consumption model (the energy signature)

Classic **degree-day regression** on the cleaned `house_kwh`:

```
house_kwh(day) ≈ baseload + β_cool · CDD(day) + β_heat · HDD(day)

CDD(day) = max(0, T_avg(day) − T_balance)   # cooling degree-days
HDD(day) = max(0, T_balance − T_avg(day))   # heating degree-days
```

- `T_balance` = balance-point temperature (outdoor temp where no HVAC is needed; start ~65°F and tune it by trying a range and picking the best fit — this matters more than people expect).
- Houston in cooling season → `β_cool` is the dominant term. Keep `β_heat` in the model for completeness.
- Fit `baseload`, `β_cool`, `β_heat` by least squares over the historical daily data we've collected (need ~2–4 weeks minimum for a usable fit; gets better over time). Pull historical daily temps from the weather provider's history API to pair with each day's kWh.
- Store each fit (coefficients, R², date range) so we can see the model improve and detect when it's unreliable.

> `β_cool` is the key sensitivity: kWh per cooling-degree-day. It's what lets us translate a thermostat change into kWh.

### 4.4 Forward projection to month-end

- Pull the forecast for `days_remaining` (see weather options below). For days beyond forecast range, fall back to seasonal normals or the trailing average.
- For each remaining day, compute expected `house_kwh` from `T_avg`, then add expected `ev_kwh` (probability of a charging day × typical session kWh, or a known schedule if charging is routine).
- `projected_total = MTD_kwh + Σ expected_daily_kwh(remaining days)`.
- Carry an uncertainty band from the regression residuals + forecast spread so the recommendation can express confidence.

### 4.5 Turn the gap into a thermostat recommendation

- Compare `projected_total` to the target (kWh budget, $ budget converted via plan rate, or last month's total).
- If `projected_total ≤ target`: report "on track," no change needed (optionally suggest comfort you can afford to add).
- If `projected_total > target`: compute the setpoint change that closes the gap.

Model the cooling setpoint as shifting the effective balance point. Raising the cool setpoint by Δ°F reduces each remaining day's cooling degree-days:

```
CDD_new(day) = max(0, T_avg(day) − (T_balance + Δ))
saved_kwh    ≈ β_cool · Σ_remaining [ CDD_old(day) − CDD_new(day) ]
```

Solve for the smallest `Δ` such that `projected_total − saved_kwh ≤ target`, clamped to comfort/safety bounds (§7). Empirically validate `β_cool` over time by comparing predicted vs actual savings after a change and correcting.

### Phase 3 deliverable — the recommendation report

`energyopt recommend` prints something like:

```
Billing cycle:        Apr 22 → May 21  (day 18 of 30, 12 days left)
Used so far:          612 kWh  (incl. ~95 kWh Tesla charging)
Target:               950 kWh
Projected month-end:  1,034 kWh  ⚠ over by 84 kWh
Forecast (12 days):   avg high 91°F — hot stretch ahead
Recommendation:       raise cooling setpoint 74°F → 77°F for the rest of the cycle
Expected result:      ~948 kWh  (within target; ±40 kWh)
Comfort note:         77°F is within your set bounds (max 78°F)
```

---

## 5. Phase 4 — Close the loop (optional, build trust first)

Once the recommendation is trustworthy, add `energyopt apply` to push the recommended setpoint to the thermostat through `thermostat/`, with all §7 guardrails, a confirmation step, full logging, and easy manual override. Consider a gentle scheduler that nudges the setpoint and backs off if comfort bounds or an override are hit. Don't automate writes until the projection has been accurate for a few cycles.

---

## 6. Weather provider options

- **Open-Meteo** — free, no API key, has both forecast and historical daily temps. Good default for both model-fitting (history) and projection (forecast).
- **NWS / weather.gov API** — free, US, official, good forecasts; pair with another source for history.
- **OpenWeather / Tomorrow.io** — fine, need keys.

We need both historical daily `T_avg` (to fit the model) and forecast daily `T_avg` (to project). Open-Meteo covers both, so start there. Use the actual airport/station nearest the house for consistency.

---

## 7. Safety & comfort guardrails (non-negotiable)

Thermostat control affects a real home, so bake these into `thermostat/` so they can't be bypassed by the algorithm:

- **Hard min/max setpoints** (e.g., never cool-set above 80°F or heat-set below 60°F — set my real bounds in config).
- **Never disable heating in freezing conditions**, regardless of budget.
- **Max change per step** (e.g., ≤3°F at a time) and a rate limit (no thrashing).
- **Manual override always wins** — if someone changes the thermostat by hand, the system stands down for the day.
- **Dry-run by default**; writes need an explicit flag/confirmation; every write logged.
- **Single managed connection** to the thermostat (the one-connection hardware limit).

---

## 8. Data model (SQLite to start)

- `daily_usage(date, total_kwh, ev_kwh, house_kwh, source)`
- `intervals(date, interval_start, kwh)` — 96/day
- `billing_cycles(cycle_start, cycle_end, source)`
- `weather(date, t_avg, t_high, t_low, is_forecast)`
- `model_fits(fit_date, t_balance, baseload, beta_cool, beta_heat, r2, n_days, train_start, train_end)`
- `recommendations(ts, projected_kwh, target_kwh, recommended_cool_setpoint, applied)`
- `thermostat_log(ts, action, old_setpoint, new_setpoint, source)`

---

## 9. Suggested stack

- **Python** (pandas/numpy for the model; the SMT, Aprilaire, and Tesla libraries are all Python).
- **SQLite** for storage; **Typer/Click** for the CLI; **APScheduler** or system cron for scheduling.
- Secrets in a `.env` (SMT login, Tesla token, weather key if needed) — never commit them.
- `pyaprilaire` mock server for thermostat dev; record a sample of real SMT data early so the model can be built offline.

---

## 10. Build order (mirrors the phases)

1. `meter/` ingest from SMT + SQLite + ingest/report CLI. (Daily report working.)
2. Weather history + forecast client; pair temps with stored kWh.
3. EV charge-signature detector; compute `house_kwh`.
4. Degree-day model fit + balance-point tuning; store fits.
5. Billing-cycle detection + month-to-date.
6. Forward projection + recommend report.
7. `thermostat/` read/control against the mock server, then the real device, with guardrails.
8. *(Optional)* Tesla Fleet API to validate EV estimates.
9. *(Optional)* `apply` + closed-loop scheduling once projections prove accurate.

---

## 11. Open decisions to resolve

- Aprilaire exact model + automation-mode support? (blocks Phase 2)
- Optimize for kWh, dollars, or "≤ last month"? (sets the target)
- SMT access method: unofficial lib vs CSV/email export? (start with whichever authenticates cleanly)
- Is Tesla charging on a fixed schedule (easy to project) or ad hoc (must model probabilistically)?
- Do I want real-time monitoring later (panel hardware), or is day-behind SMT data enough?

---

## Reference notes (for the implementer)

- **Smart Meter Texas:** 15-min intervals (96/day), daily reads, 24 months of monthly billing; data ~1 day behind; official API needs CA SSL cert (skip); unofficial `smart-meter-texas` lib powers the Home Assistant integration.
- **Aprilaire:** `pyaprilaire` (PyPI / chamberlain2007/pyaprilaire); 8800 Home Automation + 6000 Zone Control; requires Automation mode; local TCP 7001; one connection at a time; ships a mock server.
- **Tesla:** Fleet API (developer.tesla.com) has a paginated charging-history endpoint; OAuth third-party token; pay-per-use with ~$10/mo account credit; Owner API deprecated.
- **Weather:** Open-Meteo gives free history + forecast, no key.
