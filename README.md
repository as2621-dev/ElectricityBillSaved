# Project Template

A slim Claude Code template. 9 commands, 12 rules. No fluff.

## Use this template for a new project

This repo is configured as a **GitHub Template Repository**. Don't fork — use the template button.

1. On GitHub: open the repo page → click **"Use this template" → Create a new repository** → name it (e.g. `acme-app`) and create.
2. In Cursor / VS Code: **New Window → Clone Repository →** paste the new repo's HTTPS URL.
3. In the new project root:
   ```bash
   cp .env.example .env   # fill in real secrets — .env is gitignored
   ```
4. Open Claude Code in that directory and run:
   ```
   /cmo "<your rough idea>"
   /cto
   /plan-phases
   /run-phase plans/phase-1-*.md
   ```

You now have a fresh repo with its own git history, all rules in `CLAUDE.md`, all 9 commands in `.claude/commands/`, and empty `plans/`, `reference/`, `documents/`, `.agents/` ready to fill.

> **Heads up:** `.claude/settings.local.json`, `.env`, and `.cursor/rules/openmemory.mdc` are all gitignored — they're machine-local. The shared, version-controlled config is `.claude/settings.json` (if present), `.env.example`, and everything in `.claude/commands/`.

## The 12 rules

See [`CLAUDE.md`](./CLAUDE.md). These apply to every task.

## The 9 commands

| Command | Use it to… |
|---|---|
| `/office-hours` | Run a weekly diagnostic. What's stuck, what's risky, what's the next call. |
| `/cmo` | Refine a rough idea into a product brief. Fills holes, sharpens scope. |
| `/cto` | Turn the product brief into a master plan + reference docs. |
| `/plan-phases` | Break a milestone into phases, each with **exactly 4 sub-phases**. Self-critiques through product/engineering/risk lenses. |
| `/run-phase` | Execute one phase end-to-end. Spawns sub-agents per sub-phase. Phase-end: DoD + slop scan + CSO + single commit. Opt-in worktree parallelism. |
| `/rca` | Root-cause analysis for a bug. Diagnoses + proposes a fix. Doesn't apply it. |
| `/debug` | Autonomous browser bug hunt. Reproduces with `browser-use`, diagnoses with Chrome DevTools, fixes, re-verifies in-browser, loops until gone. Applies the fix; hands off to `/commit`. |
| `/commit` | Conventional commit. Stages explicit files. Never amends. Never skips hooks. |
| `/codex` | Adversarial second opinion. The 200-IQ pedant. Use when stuck or want pushback that doesn't social-smooth. |

## Typical flow for a new initiative

```
/cmo "rough idea"                     → documents/product-brief.md
/cto                                   → plans/master-plan.md + reference/*.md
/plan-phases                           → plans/phase-1-*.md, phase-2-*.md, ...
/run-phase plans/phase-1-foo.md       → sub-agents → slop scan → CSO → 1 commit
/run-phase plans/phase-2-bar.md       → ...
/office-hours                          → weekly check-in
/rca "thing X broke"                   → .agents/rca/*.md (when bugs happen)
/debug "checkout button does nothing"  → .agents/debug/*.md (browser bugs, auto-fixed)
/codex challenge plans/phase-3-*.md   → when you want adversarial pressure
```

## What `/run-phase` actually does at phase end

After all sub-phases report success, before the single commit:

1. **DoD pass** — phase-level "definition of done" check from the phase file
2. **Slop scan** — flags vacuous comments, `any` casts, defensive try/catch, dead code, marketing voice in docs, hardcoded `localhost`, leftover TODOs
3. **CSO lite** — secrets in diff, auth boundary changes, input validation gaps, injection surface, new dependency health, log hygiene

All three must pass. Findings get fixed before commit (or for medium/low CSO, logged to `.agents/cso-findings/` for follow-up).

## Directory layout

```
CLAUDE.md                          # The 13 rules
.claude/commands/                  # The 9 slash commands
documents/                         # Product briefs (CMO output)
plans/                             # Master plan + phase files (CTO + plan-phases output)
reference/                         # Stack notes, conventions, API contracts, design language (CTO output)
  └── browser-debug-playbook.md    # Tool routing + CLI cheat-sheets for /debug
design-references/                 # Pointer only — full library is remote
  └── RESOURCES.md                 # Points to github.com/ashesh2621/design-references
                                   # (86 skills + 511 design systems + 2,827 components
                                   #  + 20,660 templates, ~1 GB, fetch on demand)
.agents/
  ├── execution-reports/           # Per-sub-phase reports from /run-phase
  ├── office-hours/                # Weekly diagnostic notes
  ├── rca/                         # Root-cause analyses
  ├── debug/                       # Browser debug reports from /debug
  ├── codex/                       # Codex transcripts
  └── cso-findings/                # Deferred medium/low security findings
```

## Notes

- `/run-phase` and `/debug` are the only commands that touch feature code (`/run-phase` builds phases; `/debug` applies a verified bug fix). Everything else writes docs, plans, or reports.
- One commit per phase. Sub-phase progress in `plans/[slug]-progress.md` so a failed phase resumes cleanly.
- Each sub-phase runs in a **fresh sub-agent context** — keep sub-phases scoped tightly enough that an agent can execute one given only the phase file and `CLAUDE.md`.
- `/codex` is **user-triggered only**. `/run-phase` does NOT auto-invoke Codex on findings — humans decide when to escalate.
