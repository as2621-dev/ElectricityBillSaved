# Design References — Remote Library

The heavy design library is hosted in a separate public repo to keep this template green:

**https://github.com/ashesh2621/design-references**

Total: ~1 GB across **86 skills + 511 design systems + 2,827 components + 20,660 shared_code templates**, all scraped from aura.build.

## When to consult it

| Task | Where to look |
|---|---|
| Pick a visual language for a new product | `design-systems/INDEX.md` |
| Find a code recipe (animation, layout, interaction) | `skills/INDEX.md` |
| Find a starter HTML component (hero, pricing, dashboard) | `components/INDEX.md` + `components/TAGS.md` |
| Find a full-page template | `shared-code-templates/INDEX.md` + `shared-code-templates/CATEGORIES.md` |

## How to query it (no clone required)

### Browse an index

```bash
curl -s https://raw.githubusercontent.com/ashesh2621/design-references/main/components/INDEX.md | head -80
curl -s https://raw.githubusercontent.com/ashesh2621/design-references/main/design-systems/INDEX.md | grep -i "neural\|technical"
curl -s https://raw.githubusercontent.com/ashesh2621/design-references/main/shared-code-templates/CATEGORIES.md
```

### Search by keyword (uses GitHub code search)

```bash
gh api -X GET search/code -f q="pricing card repo:ashesh2621/design-references" --jq '.items[].path'
gh api -X GET search/code -f q="three.js webgl repo:ashesh2621/design-references" --jq '.items[].path'
```

GitHub code search is rate-limited (10 req/min unauthenticated, 30/min authenticated). Cache hits or fall back to grepping INDEX files if you hit the limit.

### Fetch a specific HTML or meta file

```bash
SLUG="dither-background"  # from skills/INDEX.md
curl -s "https://raw.githubusercontent.com/ashesh2621/design-references/main/skills/003-${SLUG}.md"

SLUG="aether-neural-interface"  # from design-systems/INDEX.md
curl -s "https://raw.githubusercontent.com/ashesh2621/design-references/main/design-systems/${SLUG}.md"
curl -s "https://raw.githubusercontent.com/ashesh2621/design-references/main/design-systems-html-previews/${SLUG}.html"

SLUG="3-tier-saas-pricing-section-with-animated-background-2764"  # from components/INDEX.md
curl -s "https://raw.githubusercontent.com/ashesh2621/design-references/main/components/html/${SLUG}.html"
curl -s "https://raw.githubusercontent.com/ashesh2621/design-references/main/components/meta/${SLUG}.json"
```

### Bulk list a folder

```bash
gh api repos/ashesh2621/design-references/contents/skills --jq '.[].name'
gh api repos/ashesh2621/design-references/contents/components/html --paginate --jq '.[].name' | head -50
```

## Local cache pattern (optional)

If you find yourself reaching for the same subset often, clone just that folder via sparse-checkout:

```bash
mkdir -p ~/.cache/design-references && cd ~/.cache/design-references
git init && git remote add origin https://github.com/ashesh2621/design-references
git config core.sparseCheckout true
echo "skills/*" >> .git/info/sparse-checkout
echo "design-systems/*" >> .git/info/sparse-checkout
git pull --depth 1 origin main
```

That gets you skills + design-systems (~5 MB) without the 1 GB of components/templates.

## How `/cto` and `/run-phase` use this

- **`/cto`** — when the product has a UI, fetches `design-systems/INDEX.md` from the remote repo, picks 2-3 candidates, reads their full content, and lifts tokens into `reference/design-language.md`.
- **`/run-phase`** — when a sub-phase touches UI, fetches relevant `skills/` (animation, layout patterns) and `components/` (starter HTML) from the remote repo to adapt rather than write from scratch.

Both commands should `curl` index files first (cheap) and only fetch full content for items they decide to use (small per-item).

## Attribution

All content is the work of the aura.build community. Each file's metadata includes the original creator URL. Credit the original when adapting.
