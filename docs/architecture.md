# Architecture

This document describes CodeSteward's system architecture, component responsibilities, and data flow.

## Design Philosophy

CodeSteward models how real open-source reviewers think. Rather than applying generic static analysis, it learns reviewer behavior from historical PR data and simulates persona-specific feedback grounded in evidence.

Three principles guide the design:

1. **Evidence grounding** -- every claim must cite a diff location, repository document, or historical PR discussion. Unsupported claims are automatically downgraded to questions.
2. **Persona fidelity** -- each simulated reviewer reflects a real person's focus areas, blocking tendencies, and style preferences.
3. **Maintainer-level judgment** -- the system produces a merge-readiness verdict, not just a list of comments.

## High-Level Pipeline

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Ingest     │────▶│   Profile    │────▶│   Review     │
│  (one-time)  │     │  (one-time)  │     │  (per PR)    │
└──────────────┘     └──────────────┘     └──────────────┘
```

1. **Ingest** pulls historical PR data from GitHub into a local SQLite database.
2. **Profile** builds reviewer skill cards from that history.
3. **Review** simulates per-reviewer feedback on a target PR and aggregates into a maintainer summary.

Steps 1 and 2 are preparatory and run once per repository (with incremental updates via `--resume`). Step 3 runs per PR.

## Component Map

```
codesteward/
  cli.py              Typer CLI entrypoint
  config.py           YAML + env + CLI config loading
  db.py               SQLite schema, migrations, queries
  github_client.py    GitHub REST API with rate limiting
  ingest.py           Historical PR ingestion pipeline
  repo_mapper.py      CODEOWNERS/OWNERS parsing, area/risk detection
  pr_filter.py        Bot/CVE PR classification and filtering
  discovery.py        Reviewer ranking and categorization
  profiler.py         Reviewer skill card generation
  simulator.py        Review simulation (LLM + heuristic fallback)
  evidence.py         Evidence validation pipeline
  aggregator.py       Multi-review merging and verdict
  render.py           Markdown + JSON output rendering
  schemas.py          Pydantic data models and enums
```

## Component Interactions

### Ingestion Phase

```
cli.py (ingest command)
  │
  ├── config.py         Load config (YAML, env, CLI flags)
  ├── db.py             Initialize database, create tables
  ├── github_client.py  Fetch PRs, files, reviews, comments
  ├── repo_mapper.py    Parse CODEOWNERS/OWNERS, store ownership rules
  ├── pr_filter.py      Classify and skip bot/CVE PRs
  └── db.py             Store all PR data
```

The ingestor fetches closed/merged PRs within a configurable lookback window. For each PR, it stores metadata, changed files, review states, and line-level review comments. Bot and CVE dependency-bump PRs are optionally filtered to reduce noise. Ownership files (CODEOWNERS, OWNERS) are parsed and stored for later reviewer discovery.

### Profiling Phase

```
cli.py (profile command)
  │
  ├── db.py            Get top reviewers by review count
  ├── db.py            Get each reviewer's stats and comments
  ├── profiler.py      Compute focus weights, blockers, style prefs
  └── db.py            Store serialized skill cards
```

The profiler reads all review comments for each reviewer and extracts behavioral signals: what topics they care about (focus weights), how often they block (blocking threshold), recurring issues they flag (common blockers), their style preferences, and representative quotes.

### Review Phase

```
cli.py (review command)
  │
  ├── github_client.py   Fetch PR diff and metadata
  ├── repo_mapper.py     Build ChangeContext (areas, risks, ownership)
  ├── discovery.py       Rank and select reviewers
  ├── db.py              Load reviewer skill cards
  ├── simulator.py       Simulate each reviewer's review
  │     ├── (LLM path)   Claude API with persona prompt
  │     └── (heuristic)  Pattern-based fallback engine
  ├── evidence.py        Validate evidence on all comments
  ├── aggregator.py      Merge reviews, compute verdict, build fix plan
  └── render.py          Write review.md and review.json
```

## Data Flow Detail

### 1. ChangeContext Construction

`repo_mapper.py` analyzes the changed files in a PR and produces a `ChangeContext`:

- **Areas**: detected via path heuristics (e.g., `api/` -> `sig-api`, `test/` -> `sig-testing`). 19 built-in heuristic patterns cover common project structures.
- **Risk flags**: detected via filename patterns (`RiskFlag` enum: `API_SURFACE`, `SECURITY`, `PERF`, `COMPAT`, `LARGE_DIFF`, `NEW_DEPENDENCY`, `CONFIG_CHANGE`, `TEST_ONLY`, `DOCS_ONLY`).
- **Ownership**: looked up from stored CODEOWNERS/OWNERS rules.
- **Relevant docs**: suggested based on changed paths (e.g., changes to `api/` suggest checking `docs/api/`).

### 2. Reviewer Discovery

`discovery.py` scores candidate reviewers using multiple signals:

| Signal | Weight | Source |
|--------|--------|--------|
| Ownership match | 1.0 | CODEOWNERS/OWNERS entries for changed paths |
| Historical reviews | 0.7 | Prior reviews on the same file paths |
| Global activity | 0.3 | Top reviewers by repo-wide review count |

Additional adjustments:
- Category detection from comment keywords (test, security, API, docs).
- Team/org names (containing `/`) are penalized 90% unless they have review history.
- Reviewers with cached skill cards get a 50% boost.
- Diversity enforcement ensures at least one reviewer per detected category.

### 3. Review Simulation

`simulator.py` generates a `ReviewerReview` for each selected reviewer using one of two paths:

**LLM path** (requires `ANTHROPIC_API_KEY`):
- Constructs a system prompt encoding the reviewer's skill card (focus weights, blocking threshold, common blockers, style preferences, evidence expectations).
- Sends PR diff and metadata as user prompt.
- Parses structured JSON response into `ReviewerReview`.
- Applies evidence validation in strict mode.

**Heuristic fallback** (no API key):
- Constrains checks to the reviewer's top 2 focus areas.
- Runs file-by-file pattern scanners (security, style, performance, API, test quality, code quality, backward compatibility).
- Applies persona-based verdict logic (blocking threshold affects verdict).
- Caps comment counts per category.

### 4. Evidence Validation

`evidence.py` enforces the "no claim without evidence" rule:

- Every non-question comment must have an `Evidence` object with a valid `type`, `ref`, and optional `snippet`.
- Reference formats are validated per type (diff refs must look like `file:line`, doc refs must reference `.md`/`.rst` files, history refs must reference `pr#N` or commit SHAs).
- In strict mode, comments with missing or invalid evidence are downgraded to questions with reduced confidence.
- In lenient mode, invalid evidence triggers a confidence penalty but preserves the comment kind.

### 5. Aggregation

`aggregator.py` merges all per-reviewer reviews into a `MaintainerSummary`:

- **Deduplication**: comments with >50% word-set Jaccard similarity are merged.
- **Disagreement detection**: identifies verdict splits and multi-reviewer conflicts on the same files.
- **Verdict logic**:
  - `NEEDS_CHANGES`: >= 2 rejections OR >= 3 blockers
  - `READY`: 0 rejections, 0 blockers, all approved
  - `RISKY`: any rejection/blocker + security/API/compat risk flags
- **Fix plan**: prioritized action items ([P0] blockers, [P1] missing tests/docs, [P2] suggestions), capped at 15 items.

### 6. Output Rendering

`render.py` produces two output files:

- **`review.md`**: human-readable Markdown with merge verdict, risk flags, per-reviewer sections (grouped by comment kind), consolidated blockers with evidence, disagreements, and prioritized fix plan.
- **`review.json`**: structured JSON (Pydantic `model_dump_json`) for programmatic consumption or agent-loop integration.

## Database

CodeSteward uses SQLite in WAL mode for concurrent read access. The schema includes 7 tables:

| Table | Purpose |
|-------|---------|
| `meta` | Key-value store (schema version, last ingest timestamps) |
| `prs` | PR metadata (repo, number, title, author, state, labels) |
| `pr_files` | Files changed per PR |
| `reviews` | Review states per reviewer (APPROVED, CHANGES_REQUESTED, etc.) |
| `review_comments` | Line-level review comments with file/line location |
| `ownership` | CODEOWNERS/OWNERS rules per repo |
| `reviewer_cards` | Serialized reviewer skill cards (JSON) |

See [Data Model](data-model.md) for full schema details.

## Extension Points

- **LLM model**: configurable via `llm.model` in config (default: `claude-sonnet-4-20250514`).
- **PR filtering**: bot/CVE filter patterns are fully configurable and can be disabled.
- **Area heuristics**: `AREA_HEURISTICS` in `repo_mapper.py` can be extended for project-specific path conventions.
- **Heuristic scanners**: new pattern scanners can be added to `simulator.py` for language-specific checks.
- **Output format**: `render.py` can be extended to produce additional output formats.
