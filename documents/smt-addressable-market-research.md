# Smart Meter Texas (SMT) — Addressable Market Research

**Question:** Of the ~11 million households in Texas, how many are on Smart Meter Texas (SMT)?

**Short answer:** Roughly **7–8 million premises (~65–70%)**. SMT only covers the ERCOT *deregulated* (competitive retail) territories, so the remaining ~3M households are structurally out of reach for any product that depends on SMT interval-data access.

> ⚠️ **Confidence note:** The meter counts below are from-memory estimates and shift year to year. They have **not** been verified against current SMT/TDU sources. Confirm with live data before using these figures in a brief, pitch, or financial model.

---

## What SMT actually is

Smart Meter Texas (SMT) is the centralized portal that holds smart-meter interval data for the ERCOT deregulated market. It is the shared data repository across the participating Transmission & Distribution Utilities (TDUs) — **not** a retail provider app. This makes it the canonical consumption-data source for any tool operating in those territories.

## Coverage by TDU (deregulated territories on SMT)

| TDU | Approx. meters |
|---|---|
| Oncor | ~3.7M |
| CenterPoint | ~2.6M |
| AEP Texas (Central + North) | ~1.1M |
| TNMP (Texas-New Mexico Power) | ~0.27M |
| **Total on SMT** | **~7–8M premises** |

## The gap vs. 11M households

The difference between ~7–8M (on SMT) and ~11M (total TX households) is **not** noise — it is the **non-deregulated** portion of the state, which does *not* use SMT and runs its own metering:

- **Municipally-owned utilities** — e.g., Austin Energy, CPS Energy (San Antonio)
- **Electric cooperatives**
- **El Paso Electric**

These ~3M households are outside the SMT system entirely.

## Implication for the product

If the product depends on SMT interval-data access, then **~7–8M of 11M households (~65–70%)** is the realistic addressable market — not the full 11M. The ~3M non-deregulated households would require separate, utility-specific integrations (or are not addressable via the SMT path at all).

Relevance to this project: the home brief targets a **Houston / CenterPoint** household, which sits squarely inside SMT coverage — so the single-home build is unaffected. The 65–70% ceiling matters only if/when this expands toward a broader Texas product.

---

## Open follow-up

- [ ] Verify per-TDU meter counts against current SMT / TDU published figures (live web search or official reports).
- [ ] Confirm whether any municipal/co-op utilities expose comparable interval-data APIs, if broader coverage is ever in scope.
