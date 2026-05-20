# Browser Debug Playbook

The tooling reference for `/debug`. Two CLIs, one loop: **`browser-use` reproduces, `chrome-devtools-mcp` diagnoses.**

This file is the routing brain and the command cheat-sheets. `/debug` reads it; you can read it standalone.

---

## 1. Why `browser-use` is good (and what to copy from it)

`browser-use` is not "Playwright with an LLM." Its advantages are structural, and they are the reason `/debug` reproduces bugs through it instead of writing scripts:

| Property | What it buys us for debugging |
|---|---|
| **Persistent daemon (~50ms/cmd)** | You drive the page one command at a time, like a human at a REPL. No browser cold-start between steps, so reproducing a 12-step flow is cheap and interactive. |
| **DOM-tree element indices, not selectors** | `state` returns numbered interactive elements read straight from CDP. No brittle CSS/XPath, no screenshot OCR. Token-efficient and stable across re-renders — the agent reasons about *the element list*, not pixels. |
| **Real Chrome profile reuse (`--profile`)** | Reproduce bugs that only happen *logged in*, behind SSO, with the user's extensions/cookies. Playwright's clean-context model can't see these without re-scripting auth. |
| **`eval` arbitrary JS** | Probe app state (`window.__STORE__`, React fiber, feature flags) at the exact failing moment. |
| **Structured `--json` output** | Every command is machine-parseable, so the autonomous loop can branch on results without scraping prose. |

**The lesson we copy:** the agent should think in *observe → act → observe*, using cheap structured state reads, not in *write-script → run → read-trace*. That is why `/debug` is more effective than a Playwright-CLI loop for *interactive* bug hunting.

What `browser-use` is **not** good at: deep instrumentation. It can click and read DOM, but it does not give you source-mapped stack traces, a real performance trace, CDP network timing, or heap snapshots. That is the other tool's job.

---

## 2. Routing — which tool for which symptom

Decide from the **symptom class**, not the page.

| Symptom / question | Tool | Why |
|---|---|---|
| Reproduce a user flow (click→type→submit) | `browser-use` | Fast daemon, element indices, profile reuse |
| Bug only happens when logged in / behind SSO | `browser-use --profile` | Real session cookies & extensions |
| "What's actually in the DOM / app state right now?" | `browser-use` (`state`, `get html`, `eval`) | Live, token-efficient reads |
| Uncaught JS error / "it just breaks" | `chrome-devtools` `list_console_messages` | **Source-mapped** stack traces → real file:line |
| API call fails / 4xx / 5xx / CORS / wrong payload | `chrome-devtools` `list_network_requests` + `get_network_request` | Full CDP request/response, headers, timing |
| Page is slow / janky / freezes | `chrome-devtools` `performance_start_trace` → `performance_analyze_insight` | Real DevTools trace, long-task & layout insights |
| Memory grows / tab leaks | `chrome-devtools` `take_memory_snapshot` + `get_nodes_by_class` | Heap diffing |
| Lighthouse / a11y / best-practice regression | `chrome-devtools` `lighthouse_audit` | Audited score + opportunities |
| "Does my fix actually work?" (re-verify) | `browser-use` to re-drive flow, `chrome-devtools` to confirm evidence gone | Close the loop on the *same* signal that proved the bug |

**Rule of thumb:** if the action is *being a user*, it's `browser-use`. If the action is *being an instrument*, it's `chrome-devtools`. Most bugs need both, in that order.

---

## 3. `browser-use` cheat-sheet (the hands)

Install: `curl -fsSL https://browser-use.com/cli/install.sh | bash` then `browser-use doctor`.
Always pass `--json` in the loop. Daemon auto-starts; `browser-use close` at the end.

```bash
browser-use --profile "Default" open "http://localhost:3000/checkout"  # reuse real login
browser-use --json state                 # numbered interactive elements + url + title
browser-use --json get html --selector "#cart"   # scoped DOM
browser-use click 7                      # by element index from `state`
browser-use input 3 "test@example.com"   # focus index 3, type
browser-use keys "Enter"
browser-use --json eval "JSON.stringify(window.__APP_STATE__ ?? null)"  # probe app state
browser-use screenshot --full /tmp/debug-repro.png   # evidence artifact
browser-use --json get value 3           # read back what a field actually holds
browser-use close                        # tear down daemon when done
```

Reproduction discipline: capture the **exact ordered command list** that triggers the bug. That list IS the regression scenario and the verification script.

---

## 4. `chrome-devtools-mcp` cheat-sheet (the instruments)

Install: `npm i chrome-devtools-mcp@latest -g` (gives the `chrome-devtools` binary).
Daemon model mirrors `browser-use`. Always pass `--output-format=json` in the loop.

```bash
chrome-devtools start --headless                    # background server
chrome-devtools status
chrome-devtools navigate_page "http://localhost:3000/checkout"

# JS errors — the high-value one. Source-mapped stack → real file:line.
chrome-devtools list_console_messages --output-format=json
chrome-devtools get_console_message <id> --output-format=json

# Network failures
chrome-devtools list_network_requests --output-format=json
chrome-devtools get_network_request <id> --output-format=json   # headers, body, timing, status

# Performance
chrome-devtools performance_start_trace
#   ...reproduce the slow interaction via browser-use...
chrome-devtools performance_stop_trace --output-format=json
chrome-devtools performance_analyze_insight --output-format=json   # actionable insights

# Memory leak
chrome-devtools take_memory_snapshot
#   ...exercise the suspected leak...
chrome-devtools take_memory_snapshot
chrome-devtools get_nodes_by_class <className> --output-format=json

chrome-devtools lighthouse_audit --mode snapshot --output-format=json
chrome-devtools stop
```

The console + network tools are the ones that turn "it's broken" into a `file:line` you can fix. Reach for them first on functional bugs.

---

## 5. The combined loop (what `/debug` runs)

```
1. REPRODUCE   browser-use: drive the exact flow → confirm the symptom is visible.
               (Cannot reproduce → STOP, ask the user. Never fix blind.)
2. INSTRUMENT  chrome-devtools: capture the proving signal — console stack trace,
               failed request, perf insight, or heap delta. This is the EVIDENCE.
3. MAP         Trace evidence → source file:line. Read the code path (callers too).
4. TEST        Write the regression test FIRST. Run it on unfixed code — it MUST
               fail for the bug's reason. Passes on broken code → back to 3.
5. FIX         Smallest change that kills the root cause. Surgical, no drive-by edits.
6. VERIFY      Re-run the SAME browser-use flow, re-capture the SAME signal, run
               the ENTIRE test suite. Signal gone AND flow succeeds AND every
               test green with zero skipped.
7. LOOP        Not fixed? Back to 3 with what you learned. Bounded — see below.
```

**Success criteria (must all hold to declare fixed):**
- The recorded `browser-use` reproduction flow now completes without the symptom.
- The specific `chrome-devtools` signal that proved the bug (that console error / that failed request / that perf insight) is absent on re-capture.
- The regression test (written before the fix, failing on old code) now passes (Rule 9).
- The **whole suite is green with zero tests skipped**, no previously-passing test regressed. Pre-existing unrelated failures are surfaced in the report, never silently excluded to claim green (Rule 12). Never weaken/`skip`/delete a test to force green.

**Loop bound:** max 4 fix→verify iterations. If still failing after 4, STOP and report what each attempt ruled out (Rule 12 — fail loud, do not silently thrash). Reclassify as a possible design-class bug and hand to `/rca`.

---

## 6. Why this beats the Playwright CLI for debugging

- Playwright drives via scripts and an accessibility/screenshot model; `browser-use`'s CDP element-index + daemon makes *interactive* reproduction far cheaper and login-aware.
- Playwright's trace viewer is good for *test* post-mortems; `chrome-devtools-mcp` gives you the *live* DevTools surface — source-mapped console, real performance insights, CDP network bodies, heap snapshots — which is what actually localizes a production bug to a line.
- Splitting "hands" and "instruments" means each step uses the tool that's best at it, instead of one framework that's mediocre at both.
