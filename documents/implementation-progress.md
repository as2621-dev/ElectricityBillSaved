# Implementation Progress — Electricity Bill Saver

Living status doc for AI agents picking up this project. Last updated: **2026-05-25**.

## 1. What this product is

Optimize a Texas electricity bill that has a **bill-credit threshold**: the plan pays a
flat **$125 credit on any billing cycle with usage ≥ 1,000 kWh** (4Change Energy
"Maxx Saver Value 12", CenterPoint / Houston). It is a single hard cliff — 999 kWh = $0,
1,000 kWh = full $125, no upper limit. The product is **not** "use less power"; it is
"make sure the cycle lands on the right side of the 1,000-kWh cliff," and more broadly
optimize cost given thresholds like this.

The one number that matters each cycle: **will projected total kWh clear 1,000?**

## 2. What exists today (built + working)

### `meter/` — Smart Meter Texas (SMT) ingest
- `email_backend.py` — pulls SMT's daily interval-usage CSV attachments over IMAP
  (Gmail app password). SMT emails the report only to the account-profile inbox.
- `csv_parser.py` — parses the 15-min interval CSV → `IntervalReading` → `DailyUsage`
  (per-day kWh totals, completeness/estimate flags). Schema validated against real files.
- `models.py` — Pydantic models for the above.
- CLIs: `fetch_smt_csv.py` (download), `show_smt_usage.py` (print daily kWh).
- **Data on hand:** 5 complete days, ESIID `1008901025004174540124`:

  | service_date | kWh    |
  |--------------|--------|
  | 2026-05-19   | 39.112 |
  | 2026-05-20   | 23.427 |
  | 2026-05-21   | 23.903 |
  | 2026-05-22   | 37.129 |
  | 2026-05-23   | 34.070 |

  (Personal CSVs live in `data/smt/`, which is **gitignored** — never committed.)

### `project_bill.py` — current cycle/bill projection
- Projects cycle kWh and the $125 credit cliff using a **flat daily-average** rate
  (`--daily-avg`, default from observed days). Rates/credit hardcoded from the
  5/13/2026 bill (energy rate 0.14611 $/kWh, $125 credit, 1,000 kWh threshold).
- Cycle window example: `--cycle-start 2026-05-11 --next-read 2026-06-10` (~11th→10th;
  CenterPoint drifts the read date a few business days).
- **Limitation this project is fixing:** flat daily average ignores weather. A cool
  spell vs a heat wave swings daily kWh from ~23 to ~39 (see table) — so a flat rate
  mis-projects whether the cycle clears 1,000 kWh.

### `weather/` — Open-Meteo cycle weather projection (built this session)
- Free, **no API key**. Three upstreams:
  - `api.zippopotam.us/us/<zip>` — ZIP → lat/lon (Open-Meteo's geocoder is unreliable
    for US 5-digit ZIPs).
  - `api.open-meteo.com/v1/forecast` — real daily min/max temp + rainfall, days 1–16.
  - `archive-api.open-meteo.com/v1/archive` — historical, used to build **climatology
    normals** (per-calendar-day average over the last N years, default 5).
- `build_monthly_projection(zip, num_days=30)` stitches a live 16-day forecast to a
  climatology backfill for the rest of the cycle. Every day is tagged
  `source = forecast | climatology` so callers can weight confidence.
- CLI: `show_weather.py --zip 75201 --days 30` (table or `--json`).
- Endpoint contract documented in `reference/integrations.md` §2.

## 3. What's NEXT — temperature-driven usage model (designed, NOT yet built)

Goal: replace `project_bill.py`'s flat daily average with a model that predicts daily
kWh from temperature, then projects the rest of the cycle using `weather/`.

### The model — cooling degree-day decomposition
```
daily_kWh = base_load + cooling_coeff × CDD
CDD (cooling degree-days) = max(0, T_avg − balance_point)
```
- **base_load** — weather-independent kWh/day (fridge, standby, water heater, lights).
  The regression intercept.
- **cooling_coeff** — kWh per cooling degree-day (AC demand). The regression slope.
- **balance_point** — outdoor temp above which AC engages. **Assume 65 °F** (industry
  standard), expose as a tunable param.

### Fitting (small-sample aware)
- Pair each SMT `DailyUsage` day with that day's `T_avg`, compute CDD, run OLS of
  kWh on CDD → `base_load` (intercept) + `cooling_coeff` (slope) + R².
- Only 5 data points → guardrails: if the fit yields a **negative** base load or slope
  (noise can do that), fall back to an assumption: base_load ≈ 55% of mean usage, HVAC
  the rest (matches the owner's "40–50% HVAC" estimate). Always report the implied
  base/HVAC split and R² so confidence is explicit.

### Projection
- Apply the fitted equation to each remaining cycle day's `T_avg` from `weather/`
  (forecast days 1–16, climatology after), sum with actuals-to-date, compare to 1,000 kWh
  and the $125 credit. This supersedes `project_bill.py --daily-avg`.

### Planned files
- `usage_model/` — `models.py` (typed fit + projection), `fit.py` (fit the equation),
  `project.py` (project the cycle).
- `show_usage_model.py` — CLI: fit on SMT data, print equation + R² + base/HVAC split +
  cycle projection vs 1,000 kWh.

### Weather change required for the model
`weather/` currently fetches min/max temp + precip. The model needs **`T_avg`**:
- Add `temperature_2m_mean` to `DAILY_FIELDS` in `weather/client.py` and a `temp_avg_f`
  field on `DailyWeather`.
- Add `fetch_daily_temps(lat, lon, start, end)` to get T_avg for **past** SMT days —
  use the forecast endpoint's `past_days` parameter (lower lag than the archive for the
  most recent days).

## 4. Open questions (need owner input)

- **Exact ZIP.** Houston is confirmed (CenterPoint), but Houston is large and weather
  varies by ZIP — need the precise ZIP for accurate temps. Set `WEATHER_ZIP` in `.env`.
- **Billing cycle window.** Example bill suggests ~11th→10th; confirm the exact
  cycle-start and next-read dates so the projection window is right.

## 5. Conventions / stack
See `reference/conventions.md`, `reference/stack-notes.md`, `reference/integrations.md`,
and the root `CLAUDE.md` (14-rule template). Python: Pydantic v2 models, structlog JSON
to stderr, Google-style docstrings, type hints everywhere. Secrets in `.env` only;
personal usage data under `data/` (both gitignored).
