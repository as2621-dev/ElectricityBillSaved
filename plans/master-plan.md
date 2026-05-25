# Master Plan

**Date:** 2026-05-19
**Source brief:** `documents/home-energy-optimizer-brief.md`
**Status:** Active

## Vision (one paragraph)

A single-user, locally-hosted Python CLI that ingests Smart Meter Texas (SMT) consumption data via the daily email subscription, pairs it with weather, fits a two-term daily energy model — a monthly-average **baseload** (absorbing EV charging, fridge, everything non-HVAC) plus a temperature-driven **HVAC** term (degree-day regression) — projects month-end usage against the billing cycle, and each day decides whether to **raise, lower, or hold** the Aprilaire cooling setpoint to hit a user-set target — delivering that verdict plus the consumption picture as a **daily email**. Closed-loop control (writing setpoints back to the thermostat) is opt-in and lives behind hard comfort/safety guardrails. Built for one house (mine), in Houston, in cooling season — explicitly not a SaaS, not multi-tenant, not cloud-hosted.

## Tech stack

- **Frontend:** **None for M1–M4.** The product is a daily terminal report + CLI. A read-only dashboard (Streamlit or a tiny FastAPI page) is a *post-MVP* option, flagged for revisit only after closed-loop control is trusted. Rationale: brief explicitly calls for a daily report — adding a web frontend now is scope creep (Rule 2).
- **Backend / data layer:** **SQLite file (local).** Single user, append-mostly daily data, never leaves the machine. Per-brief §8 schema fits in <10 small tables. No Convex/Supabase — would force the LAN-only thermostat client into a hybrid cloud-local topology for zero benefit.
- **Agent layer:** **None.** The brief's "brain" is deterministic statistics (least-squares degree-day regression, balance-point tuning, gap-closing root-find). Per CLAUDE.md Rule 5, "if code can answer, code answers." Reserve LLM only for the daily report's *prose* summary if the user wants it later — flagged, not built.
- **Background jobs:** **Host-native scheduler (`launchd` on macOS; `cron` on Linux).** Trigger.dev is ruled out: it's cloud-hosted and cannot reach the LAN-only thermostats on TCP 8000. Daily cadence aligns with SMT's ~24h data latency, so a single scheduled run/day is sufficient.
- **Hosting:** **User's local machine on the same LAN as the Aprilaire** (laptop, Mac mini, NUC, or Raspberry Pi). Hard requirement, not a preference — the thermostat accepts only LAN-local TCP and only one automation connection at a time.
- **Languages:** **Python 3.12+ only.** The ingest path is stdlib (`imaplib`/`email`/`csv`), and the critical device library (`pyaprilaire`, plus the `smart-meter-texas` fallback) is Python. No reason for a second language.

> **Conflict flagged (Rule 7):** The repo's *global* `~/CLAUDE.md` template recommends Next.js + Convex/Supabase + Vercel + Trigger.dev. That stack does not fit this product (no UI, LAN-only device, deterministic algorithm, single user). The *project* `CLAUDE.md` (14-rule template) and brief override. Revisit only if a remote/multi-home dashboard is later in scope.

## Architecture

```
┌──────────────────────────┐         ┌────────────────────────┐
│ Smart Meter Texas        │         │   Open-Meteo           │
│  daily email subscription│         │  (history + forecast,  │
│  (CSV attachment,        │         │   no API key)          │
│   15-min intervals)      │         │                        │
└────────────┬─────────────┘         └────────────┬───────────┘
             │ IMAP poll (daily)                  │ HTTPS (daily)
             ▼                                    ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                       energyopt (Python CLI, local)                        │
│                                                                            │
│   meter/  ──────── weather/  ──▶  model/ (baseload + degree-day fit)     │
│   (IMAP+CSV parser)                          │                             │
│                                              ▼                             │
│                          recommend/ (RAISE/LOWER/HOLD decision)            │
│                                  │                    │                     │
│                                  ▼                    ▼                     │
│              thermostat/ (read; write behind   report/ ──▶ notify/ (email) │
│                          guardrails)                  │                     │
│                                  │                    ▼                     │
│                                  ▼            stdout / file (always)        │
│                          storage/ (SQLite, local file)                     │
└─────────────────────┬──────────────────────────────────────────┬───────────┘
                      │ LAN TCP :8000 (one conn / unit)          │ SMTP out
                      ▼                                          ▼
            ┌────────────────────────┐                 ┌───────────────────┐
            │ 2× Aprilaire S86WMUPR  │                 │  Daily email      │
            │ (1st & 2nd floor)      │◀── manual       │  (consumed +      │
            │ uniform Δ to both zones│    override wins │   projected +     │
            └────────────────────────┘                 │   what-to-do)     │
                      ▲                                 │                   │
                      │ host-native scheduler          └───────────────────┘
                      │ (launchd / cron, once/day)
```

## Key design decisions

1. **Local-only execution.** Each Aprilaire accepts exactly one LAN automation connection on TCP 8000 (two units → two connections); cloud hosting can't reach them. Rules out: Vercel, Trigger.dev, any cloud DB.
2. **SMT data is treated as ~24h-late by contract.** All ingest, projection, and reporting assume "yesterday" is the freshest day. Rules out: real-time dashboards, intraday recommendations (deferred to optional CT-clamp hardware later).
3. **Single managed thermostat connection.** A long-lived process holds the connection; CLI commands talk to it via a local IPC (or a short-lived connection with backoff). Concurrent connects can hang the device and require a physical power cycle. Rules out: naive "open-do-close" per CLI invocation when a daemon is running.
4. **`meter/` ingests via SMT's daily email subscription (CSV).** An IMAP poller pulls the daily email from a dedicated mailbox / filtered Gmail label, extracts the CSV attachment, and parses 96 intervals + the daily total per service day. Interface: `get_intervals(date)`, `get_daily_total(date)`, `get_billing_periods()`. The unofficial `smart-meter-texas` portal-scraping library is a *not-now* fallback if email parsing fails. The SMT push-API path is explicitly **not pursued** (requires CA-issued SSL cert + static public IP + customer-hosted HTTPS endpoint — infrastructure overkill for one household; same data, same latency).
5. **EV charging is absorbed into the baseload, not separately tracked.** Daily model is `kwh ≈ baseload + β_cool · CDD + β_heat · HDD`, where `baseload` is fit over a 30-day window and represents the **monthly-average** non-HVAC load (fridge + EV charging + everything else). Rules out: a `vehicle/` module, Tesla Fleet API integration, EV signature detection, and any per-day EV attribution. Trade-off accepted: EV-heavy days inflate regression residuals, lowering R², but `β_cool` and `β_heat` coefficients remain unbiased and the projection works.
6. **Deterministic statistics, no LLM in the core loop.** Degree-day regression + balance-point tuning + linear gap-closure. Rules out: opaque "AI recommendation," nondeterministic outputs, agent frameworks.
7. **Dry-run by default; writes require an explicit flag.** Comfort/safety guardrails (min/max setpoints, freezing-day heat lockout, max Δ per step, manual-override-wins) are enforced in the `thermostat/` module so the algorithm cannot bypass them. Rules out: silent failures into "the house got too hot/cold."
8. **One target metric, user-selected.** kWh budget, dollar budget, or "≤ previous cycle." All three reduce to a single `target_kwh` after a rate conversion. Rules out: a multi-objective optimizer that hedges between competing targets.
9. **SQLite over a server DB.** Single-process, single-machine, file-backed. Rules out: ops burden of running Postgres just for one house.
10. **No web frontend in MVP.** The product surface is a **daily email** (plus stdout/file for debugging). A read-only Streamlit/FastAPI dashboard is a *post-trust* nice-to-have, not on the M1–M4 path. Rules out: scope creep into "build me a UI."
11. **The daily verdict is RAISE / LOWER / HOLD, decided with hysteresis.** A deadband (default 3% of target) prevents day-to-day flip-flopping, and a re-change is suppressed within 2 days unless the projection moves more than the deadband. Rules out: a thrashing controller that nudges the setpoint every day.
12. **"Definitely hit the target" is solved against the upper confidence bound, not the point estimate.** The recommended raise Δ is the smallest whole-degree change (≤ max step, within comfort bounds) such that even the pessimistic `projected_high` lands ≤ target. Rules out: confident-sounding recommendations that only work in the average case and miss on a hot streak.
13. **Email is the daily surface; SMTP send lives in `notify/`.** Introduced in M1 (consumption half), completed in M2 (projection + verdict). Rules out: requiring the user to open a terminal to learn the day's recommendation.
14. **Two thermostats (confirmed S86WMUPR, 1st & 2nd floor), controlled uniformly.** The SMT meter is whole-house and can't attribute kWh per floor, so the model fits a single whole-house `β_cool` and the recommendation applies the **same Δ to both zones**. `thermostat/` holds one managed connection *per unit* (port 8000; the one-connection limit is per device). Rules out: per-floor optimization (no per-floor signal exists), and any assumption of a single thermostat.

## Daily decision algorithm (RAISE / LOWER / HOLD)

Lives in `recommend/`. Runs once per day after ingest + model refit. Cooling-season convention: raising the cool setpoint reduces AC kWh.

**Inputs:** `target_kwh`, `MTD_kwh`, `days_remaining`, the model fit (`baseload`, `β_cool`, `β_heat`, `T_balance`, residual `σ`), forecast `T_avg` per remaining day, `current_cool_setpoint_f`, comfort bounds, max step, deadband.

1. **Project at current setpoint:**
   `remaining = Σ_d [ baseload + β_cool·CDD(day_d) + β_heat·HDD(day_d) ]`,  `projected = MTD_kwh + remaining`.
2. **Uncertainty band:** `proj_σ ≈ sqrt(days_remaining)·σ` combined with forecast spread; `projected_high = projected + z·proj_σ` (z = 1.28 ≈ 90%).
3. **Decide (deadband `D` = default 3% of target):**
   - **RAISE** if `projected_high > target` (judge by the pessimistic case).
   - **LOWER** if `projected_high < target − D` (real headroom — buy back comfort).
   - **HOLD** otherwise.
4. **Solve the change** (whole °F, ≤ `MAX_SETPOINT_STEP_F`, within comfort bounds):
   - RAISE → smallest Δ such that `projected_high(T_balance + Δ) ≤ target`. If even at comfort-max we're over, report the residual overage instead of overpromising.
   - LOWER → largest Δ_down such that `projected_high(after lowering) ≤ target`.
5. **Anti-thrash:** don't reverse a setpoint change within 2 days unless `projected` has moved by more than `D` since the last change.

The email always reports (1) consumed-so-far + cycle position, (2) `projected` with its band, and (3) the verdict + exact setpoint change + expected resulting total + comfort note. Default cadence: **one email per day**, verdict in the subject line. (Open question: suppress emails on HOLD days vs. always send — see below.)

## Milestones (not phases — phases come from `/plan-phases`)

- **M1 — Daily ingest + emailed report:** SMT email subscription enabled and arriving at a dedicated mailbox. `energyopt ingest` polls IMAP, downloads the daily CSV attachment, parses 96 intervals + daily total, stores idempotently in SQLite. `energyopt report` prints **and emails** yesterday's kWh, 7-day trend, MTD, days into cycle (the consumption half — no projection yet). A `launchd`/`cron` job runs both daily. *De-risks: "can we reliably pull SMT data via the email path, and reliably send a daily email out?"*
- **M2 — Weather + model + projection + verdict:** Open-Meteo history paired with stored kWh; baseload fit over the trailing 30 days; degree-day regression fit with auto-tuned balance point; forward projection to month-end with a confidence band; the RAISE/LOWER/HOLD decision algorithm. `energyopt recommend` computes the verdict, and the daily email now carries the full payload (consumed + projected + what-to-do-to-definitely-hit-target). *De-risks: "is the energy signature stable enough to recommend from?"*
- **M3 — Thermostat read (dry-run only):** `pyaprilaire` against the mock server (port 7001), then **both real S86WMUPR units** (port 8000, one connection per floor). `energyopt thermostat status` reads mode/setpoints/indoor temp/humidity for each zone, converting C↔F at the boundary. **No writes yet.** Guardrails coded but exercised only in unit tests. *De-risks: "can we hold stable per-unit connections to both confirmed S86WMUPR thermostats?" (model + automation support already confirmed).*
- **M4 — Closed-loop apply (opt-in):** `energyopt apply` writes the recommended cool setpoint with confirmation, full logging, and all §7 guardrails active. A gentle scheduler nudges and backs off on manual override. *De-risks: "are recommendations accurate enough to act on?"*

## Riskiest assumption (from brief) and how we test it

**The riskiest assumption is that the SMT email subscription will arrive reliably with a parseable CSV attachment every day** — every downstream milestone is built on it. M1 tests it directly: enable the subscription in the SMT portal, then verify at least 7 consecutive successful runs (email arrived, attachment parsed, 96 intervals stored) against the user's real account before starting M2. If the email path fails (missed days, schema change, attachment format drift), the M1 fallback is the unofficial `smart-meter-texas` portal-scraping library behind the same `meter/` interface; if both fail, the project is reconsidered.

The *second*-riskiest assumption — Aprilaire automation support — is now **resolved**: the hardware is confirmed as two S86WMUPR units (FW 1.8.6), supported by `pyaprilaire` over local TCP port 8000. The residual M3 risk is operational, not existential: holding one stable connection *per unit* without tripping the per-device single-connection limit.

## Out of scope

- Multi-tenant SaaS, accounts, billing, sharing.
- Real-time (sub-daily) monitoring without separate panel hardware.
- A web frontend during M1–M4.
- **EV-specific tracking, Tesla Fleet API integration, per-day EV attribution, charge-signature detection** — EV load is absorbed into the baseload by design.
- **Pursuing SMT's API-push delivery** (requires CA-issued SSL cert + static public IP + customer-hosted HTTPS endpoint; same data is available via email).
- Heating optimization (Houston cooling season is the dominant load; heating coefficients are fit but not optimized against).
- Solar/battery export modeling.
- Automated bill payment / utility account management.
- Generalizing to non-SMT utilities (other US states / non-Texas grids).
- Cloud sync, mobile push apps, voice assistants.
- A predictive ML model beyond degree-day regression (no neural nets, no time-series forecasting libs).

## Open questions for `/plan-phases`

1. **Target metric: kWh, dollars, or "≤ last month"?** Blocks M2's `recommend` output. Default proposed: "≤ last month" with a configurable hard kWh ceiling — easy to start, no rate-card math required.
2. **Mailbox for SMT emails.** The SMT subscription delivers to the SMT-registered email address. Options: (a) point that registered address at a dedicated Gmail/Fastmail mailbox we IMAP-poll, (b) keep it on the user's primary inbox and filter by sender into a label/folder. Affects credentials and M1 sub-phase wiring. Default proposed: a dedicated Gmail label + app-password IMAP.
3. **Host machine:** which device will run `launchd`/`cron`? (Laptop with sleep gaps vs always-on Mac mini / Pi.) Note: the host must be on the same LAN as **both** S86WMUPR units to control them. Affects scheduler choice and missed-run recovery design.
4. **Outbound email transport.** How does `notify/` send? Options: (a) Gmail SMTP with an app password, (b) a transactional provider (Resend / SES / Postmark) with an API key, (c) reuse the same Gmail account that *receives* the SMT emails. Default proposed: (a) Gmail SMTP + app password — zero extra accounts, fine for one recipient. Affects M1 sub-phase wiring and `.env`.
5. **Email cadence:** one email every day (verdict in the subject) vs. only email when the verdict is RAISE/LOWER (suppress HOLD days). Default proposed: **daily**, so the consumed/projected numbers are always visible; revisit if it feels noisy.
6. **Two-zone control behavior:** confirm uniform Δ to both floors is acceptable, or whether one floor (e.g., unused upstairs) should be biased warmer. Default proposed: uniform Δ; revisit after M3 once we can read both zones' real setpoints.
