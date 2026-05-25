# Electricity Bill Saver — Market & Integration Research Findings

**As of 2026-05-25.** Consolidated from multi-agent research across thermostat integration, market sizing, Smart Meter Texas data access, bill-credit plan economics, utility-data vendors, and go-to-market. All quantities are **modeled estimates** unless marked confirmed — the *shape and rank order* are durable; exact counts are soft. Companion to `home-energy-optimizer-brief.md`; integration specifics live in `reference/integrations.md`.

---

## 1. Core product thesis (the one-liner)

The product is **bill-credit threshold optimization**, NOT generic "use less electricity."

Many Texas competitive-market retail plans are **minimum-usage bill-credit plans**: use ≥ a threshold (commonly **1,000 kWh**, sometimes 1,500/2,000) in a billing cycle → get a flat credit (**~$30–$125**). The product projects month-end kWh from smart-meter + weather data and nudges the AC (a 2–3°F setpoint change) so the home **crosses the threshold** — the marginal kWh costs ~$5 to capture a ~$100 credit. It also serves the minority on minimum-usage-**fee** plans (penalized for using *too little*) via the identical "hit the minimum" logic.

This resolves the brief's open question (optimize for kWh budget vs $ budget vs "don't exceed last month"): the answer is **optimize to a plan-specific usage threshold for a dollar credit.** The RAISE/LOWER/HOLD verdict is about hitting that line.

---

## 2. Market funnel (TAM → SAM → SOM)

| Stage | TX homes | Note |
|---|---|---|
| All households | ~11M | Census ACS ~11.0–11.45M |
| → own a smart/Wi-Fi thermostat | **~1.9M** (~17%) | two methods converged (top-down 2.0M, bottom-up 1.85M) |
| → **and** on Smart Meter Texas | **~1.33M** (~70%) | SMT = 4 competitive TDUs only |
| → **and** on a bill-credit plan | **~530K** (~40%) | weakest link, ±15 pts |
| **True high-value SOM** (marginal homes near threshold) | **~250K–350K** | where a nudge flips credit-miss → credit-bank |

**SMT covers only the 4 competitive TDUs** — Oncor (DFW), CenterPoint (Houston), AEP Texas, TNMP. **Austin Energy, CPS Energy (San Antonio), and rural co-ops are NOT on SMT** → ~570K smart-thermostat homes are structural dead zones (no consumption data). Densest serviceable areas: DFW + Houston metros.

**Value concentration inside the 530K:** "always-over" big homes get the credit automatically (low value); apartments (500–900 kWh) can't reach the threshold (but rarely have smart thermostats); the **marginal homes that dip below in shoulder/winter months** are the recurring, seasonal value moment.

---

## 3. Smart thermostat market by brand (Texas)

| Brand | TX homes (base) | Share | On SMT (~70%) |
|---|---|---|---|
| Google Nest | ~610K | ~32% | ~425K |
| Honeywell / Resideo | ~400K | ~21% | ~280K |
| ecobee | ~290K | ~15% | ~205K |
| Amazon Smart Thermostat | ~210K | ~11% | ~145K |
| Emerson / Copeland Sensi | ~150K | ~8% | ~105K |
| Aprilaire | ~38K | ~2% | ~27K |
| Other (Wyze, Mysa, OEM, ADT…) | ~195K | ~10% | ~135K |
| **Total** | **~1.9M** | 100% | **~1.33M** |

Counted as *homes with ≥1 of that brand* (not device units — zoned TX homes run ~1.4–1.7× more units). Rank order is reliable; exact % is ±5 pts (no TX-native brand data exists).

---

## 4. Thermostat integration & control tiers

**Two hard product constraints** (learned from Aprilaire): integration must be **(1) cloud-reachable** (works over the internet, no same-LAN requirement) and **(2) keep the customer's own app working** (no app-disabling "automation mode"). Must READ live temp + setpoint + mode and WRITE the cool setpoint.

**Two control tiers:** **closed-loop** (we read + write) vs **advisory** (we compute, the user/their ecosystem acts).

| Brand | Best $0 legitimate path | Tier | Blocker |
|---|---|---|---|
| **Aprilaire** | direct unofficial cloud — **DONE** | Closed-loop ✅ | unofficial, breakage risk |
| **Honeywell / Resideo** | free official OAuth (open reg, slow ~4mo approval) or `aiosomecomfort` | Closed-loop ✅ | tight default rate limit (sized ~20 devices — must negotiate) |
| **Sensi** | `pysensi` (unofficial, brittle, reCaptcha) or DR partner API | Closed-loop ⚠️ | no self-serve official API |
| **Nest** | advisory default; opt-in SDM self-register ($5/customer) | Advisory / opt-in ⚠️ | Google froze new commercial SDM apps; ToS personal-use + 10-day data cap |
| **ecobee** | none free (registration closed since 2024) | Advisory ❌ | dev registration closed |
| **Amazon** | advisory + Alexa Routines only | Advisory only ❌ | Alexa-locked; no consumer API exists at all |
| **Other** | mixed | — | — |

**Market × tier:** closed-loop free today ≈ **~310K homes** (Resideo ~280K + Aprilaire ~27K, on SMT) — the auto-apply beachhead. Advisory-tier (Nest + ecobee + Amazon on SMT) ≈ **~775K homes**. Since the 3 advisory-only brands = ~58% of the market, **advisory-first is mandatory**; auto-apply is an upgrade.

**Priority:** after Aprilaire (done), **Honeywell/Resideo is the #1 free closed-loop target** — ~280K on-SMT homes, ~10× Aprilaire's base, free OAuth, both constraints satisfied.

**Ruled out:** Matter (LAN/hub-only — fails cloud constraint); SmartThings-via-Seam (needs a hub); Alexa Smart Home Skill / Alexa Smart Properties (manufacturer-in or enterprise-fleet only).

---

## 5. Open-source vs paid aggregator (Seam)

- **Seam** (seam.co) is a paid unified API ($5–$50/device/mo; the $5 tier caps at 5 actions/device/day — our read+write loop likely forces the $50 tier). It would unlock ecobee + Resideo + Sensi **because Seam already holds those developer approvals**. **Ruled out: user cannot spend money.**
- **No free hosted "open-source Seam" exists** — every hosted unified API charges, because the real product is *holding the brand approvals*, which can't ship in a repo.
- **The open-source equivalent = Home Assistant's per-brand Python libraries** (`google-nest-sdm`, `python-ecobee-api`, `aiosomecomfort`, `pysensi`, `pyaprilaire`), vendored behind one typed interface — exactly the pattern already used for `pyaprilaire`.
- **Critical catch:** open source replaces the **code, not the approvals**. The libs still need your own OAuth app/API key per brand. So they unlock brands with *open* developer programs (Resideo, Aprilaire, risky Sensi) for $0, but **cannot rescue ecobee or Nest** — those are blocked at the credential layer. For those two, the $0 answer is the **advisory tier**.

---

## 6. Nest & Amazon — legitimate alternative paths (no scraping)

**Nest** — no clean $0 cloud-control path today:
- **Google Home APIs** (the new 2024–25 program): mobile-SDK only (Android/iOS on-device), **no server/cloud-to-cloud path** — dead end for a cloud service.
- **SDM self-registration** (each customer pays own $5): full control works, but ToS = personal-use-only + 10-day data retention cap → opt-in power-user tier only, legal risk.
- **DR-aggregator partnership** (Renew Home / EnergyHub / Virtual Peaker, active in ERCOT): the only sanctioned at-scale control, but you become a sub-aggregator (BD-heavy).
- **Default: advisory** (compute target, user sets it in the Google Home app).

**Amazon Smart Thermostat** — no automated closed-loop path exists at all:
- Alexa Smart Home Skill = manufacturer-in only; Alexa Smart Properties / "Alexa for Residential" = apartment-manager/enterprise-fleet only; no Matter; no new consumer API.
- **Best legitimate move: advisory + Alexa Routines** — we compute the schedule, the customer enters it once as Routines (a custom-trigger skill can *fire* a pre-built routine but can't set a temp or read state). Hardware swap is the only true-control upgrade.

---

## 7. Smart Meter Texas (SMT) data access

The data source the whole product runs on. **Three paths:**

1. **Email-subscription ingest (MVP choice).** User → SMT → Manage Subscriptions → schedule a recurring report (CSV/GreenButton/JSON; "Energy Data 15 Min Interval" available). **CORRECTION (confirmed from SMT Residential User Guide):** delivery is hard-wired to the **account-profile email** — there is NO recipient field. So "point SMT delivery at an inbox we own" does **not** exist. To land it in our inbox, the user must **auto-forward** (e.g., Gmail filter `from:smartmetertexas.com → ingest@app`; confirm code lands with us, we auto-accept). Zero business onboarding for us, but two user chores (account+subscription, AND forwarding) → drop-off risk.
2. **Third-party / Authorized Agent API** (2018 PUCT order). We register as an SMT third party; per-user Energy Data Agreement (authorize via code or email invite, revocable, ≤12 months then renew); pull 15-min intervals + daily + monthly reads. Low *user* friction. **Cost is on us:** register at private.SmartMeterTexas.com, **valid DUNS required** (immutable after approval), SMT manually verifies the company AND tests our FTP/API connectivity. Realistic **~3–6 weeks** upfront. Better path beyond MVP.
3. **Aggregator** (e.g., Arcadia "Plug", Bayou — see §9). Fastest integration, $ per connection, vendor dependency.

**Avoid:** Gmail `gmail.readonly` restricted scope (annual CASA security assessment + OAuth verification — costly, kills signup trust). The `mrand/smart_meter_texas` repo is single-meter DIY, not multi-user.

**Still to validate on a real SMT account:** (a) emailed CSV = attachment vs body link? (b) does daily cadence deliver yesterday's full 15-min set (data lags 24–48h)?

---

## 8. Bill-credit plans: prevalence + geography

**Prevalence:** ~**40%** of TX competitive-residential customers are on a bill-credit (min-usage-credit) plan (supply-side ~45%, demand-side ~35%; range 30–50%). Another ~10% are on minimum-usage-**fee** plans → ~50% have *some* usage threshold worth hitting. Threshold distribution: **1,000 kWh dominates (~65–75%)**, 2,000 kWh minority. No REP publishes enrollment by plan type → this is the weakest number in the chain.

**Geography (confirmed):** The 1,000-kWh bill-credit archetype is **essentially Texas-only**.
- **Texas: abundant** — 13+ REPs (Gexa, Frontier, 4Change, Discount Power, Cirro, Reliant, TXU, Constellation…).
- **Everywhere else (IL, OH, PA, NJ, NY, MA, CT, NH, MI, MD): none found** — those markets sell flat ¢/kWh only.
- **Why:** Texas PUCT mandates the EFL 500/1,000/2,000 kWh three-tier rate display, which is what makes bill-credit plans marketable. No other state mandates it. Constellation sells the credit plan ONLY in Texas.
- **Implication:** The product is **Texas-first/Texas-only at the plan-optimization layer.** Expanding geography does NOT expand the market — the plan type doesn't exist elsewhere. Non-TX expansion requires reframing to generic usage/cost optimization.

---

## 9. Bayou Energy (candidate utility-data vendor)

- **Pricing:** **$24/meter/year** ($2/meter/bill), **first 10 meters free**, unlimited API calls, no setup fees; price independent of frequency/granularity.
- **Data:** Pull API + webhooks (you build reports). Lists 15-min interval for SMT. Granularity set by utility; delay 24–72h. TX contact: james@bayou.energy.
- **CRITICAL — Texas not live:** Smart Meter Texas and CenterPoint are both **"Roadmap," NOT active.** Bayou's ~23 active utilities cover other states (Duke, FPL, PG&E, ComEd, Con Ed…); targeting 90% of US meters by Dec 2026.
- **The collision:** Bayou's active markets have ZERO 1,000-kWh bill-credit plans (Texas-only archetype), and its Texas coverage isn't live. So Bayou today covers everywhere the product has no market, and not the one place it does.
- **Apply:** Don't bank on Bayou for MVP. Use the SMT email-subscription path for Texas now; re-check Bayou's SMT status (verify "live for my account," not the roadmap label) before committing.

---

## 10. Go-to-market / targeting

**Core reality:** You **cannot** buy a verified "owns a smart thermostat + on SMT + bill-credit plan" list — utility/SMT data is confidential (PUC §25.130) and device ownership isn't a purchasable attribute. **Target proxies + let the product's signup (which requires SMT + plan + thermostat authorization) self-qualify.** Plan type IS knowable at signup (from the EFL) — it's both qualifier and value hook ("we'll make sure you bank your $100 credit").

**Ranked channels:**

| Channel | How to reach them | CAC | Scale |
|---|---|---|---|
| **REP white-label / bundle** (TXU, Reliant, NRG…) | They hold SMT auth + addresses + sell the bill-credit plans → perfect alignment | ~$0 (they fund it) | Huge (slow sales cycle) |
| **HVAC installer referrals** | They install the thermostat — ownership known at point of sale | ~$20–50 | Medium — **lowest CAC, highest conversion** |
| **Utility DR / VPP co-marketing** (EnergyHub, Renew Home) | Pre-aggregated verified owners on SMT grids | Low | High |
| **Google Search (high-intent)** | "lower my electric bill texas" → ZIP-gated self-qualify page | ~$91 CPL (niche beats it) | Medium — **start here for real CAC data** |
| **Meta Advantage+** | ZIP lists + interests (Nest/ecobee/smart home) + lookalikes | ~$28 CPL | High |
| **Nextdoor** | hyperlocal, homeowner-dense | ~$2.50–3.50 CPC | Low |
| **Direct mail (proxy list)** | ZIP-filtered homeowner/SF/value | ~$60–90+ | Co-branded amplifier only |

**Meta note:** a home energy-*savings service* is **NOT** a Special Ad Category (those are housing/credit/employment/finance) — *as long as creative avoids mortgage/rent/credit/loan language* — so full ZIP/interest/lookalike targeting is available.

**Recommended sequence:** Google Search → self-qualify page (now) → feed qualified leads to Meta lookalikes → recruit HVAC shops in DFW/Houston in parallel → start REP white-label + utility DR conversations (the scale plays). The **REP white-label deal is the elegant unlock** — the REP legally holds the SMT auth, the data, the address, AND sells the exact bill-credit plans the product optimizes.

---

## 11. Legal guardrails

- **Utility/SMT data:** confidential (PUC §25.130); only the customer or an explicitly authorized party can release it (12-month authorizations).
- **Cold email (CAN-SPAM):** allowed without prior consent if valid physical address + honest headers + working opt-out honored within 10 days.
- **Cold SMS/calls (TCPA):** NOT allowed — requires prior express written consent; collect opt-in at the funnel; honor revocation within 10 days.
- **Unofficial cloud APIs (Aprilaire, Sensi, AlexaPy):** ToS gray area; expect breakage on app updates. Productization needs per-customer consent, encrypted refresh tokens (never passwords), token refresh.

---

## 12. Open questions / to-validate

1. SMT emailed CSV: attachment vs body link? Daily cadence = yesterday's full 96 intervals? (validate on a real account)
2. Tighten the ~40% bill-credit enrollment number (pull actual Power to Choose plan counts).
3. Resideo: confirm current v2 API schema, rate-limit policy, and commercial-onboarding terms directly with Resideo before building.
4. Bayou SMT go-live date (re-check before relying on it).
5. Nest SDM commercial-application reopening (monitor; currently frozen).
6. Whether smart-thermostat homes are over/under-indexed on bill-credit plans (assumed neutral).

---

## 13. Confidence summary

| Finding | Confidence |
|---|---|
| SMT covers only the 4 competitive TDUs (Austin/SA/co-ops excluded) | High (confirmed) |
| Bill-credit archetype is Texas-only | High (confirmed) |
| Total TX smart-thermostat homes (~1.9M) | Medium-High (two methods converged) |
| On-SMT count (~1.33M) | Medium-High |
| Per-brand split | Low-Medium (rank order reliable, % ±5) |
| Bill-credit enrollment (~40% → ~530K) | **Low-Medium (weakest link, ±15 pts)** |
| Control-tier verdicts per brand | High (current as of May 2026; APIs can change) |
| Bayou pricing / Texas-not-live | High (confirmed) |

---

## 14. Key sources

- Census ACS / QuickFacts (TX households); Parks Associates (16% of internet homes have smart thermostats, brand shares); Grand View / Mordor (brand market share).
- ERCOT / FERC (~8M competitive premises); PUCT (smart-meter privacy, EFL display rules); Mission:data (SMT covers competitive TDUs only).
- Google Device Access (SDM apply page — commercial freeze; ToS personal-use + 10-day cap; $5 fee); Google Home APIs (mobile-only).
- ecobee developer page (registration closed); Resideo developer.honeywellhome.com (v2 API, rate-limit FAQ); Seam docs + pricing.
- Amazon: Alexa Smart Home Skill API, Alexa Smart Properties / Alexa-for-Residential (enterprise-gated).
- SMT Residential User Guide (email-to-profile-only); Bayou Energy (bayou.energy — pricing, roadmap status).
- Bill credits: ElectricityPlans/ComparePower/EnergyBot/Choose Texas Power; TCAP; Hortaçsu-Madanizadeh-Puller (NBER w20988, consumer inertia); EIA Texas profile (~1,100 kWh/mo).
- GTM: Acxiom/Experian list attributes; direct-mail benchmarks (MPA); Meta Special Ad Category (data-axle, Jon Loomer); home-services CPL (LocaliQ, Sotros); TCPA (Foley, BCLP).
