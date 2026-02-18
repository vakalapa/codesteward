# CLI Reference

CodeSteward provides four commands that map to the pipeline stages: `init`, `ingest`, `profile`, and `review`.

## Installation

```bash
pip install -e .              # Core dependencies
pip install -e ".[llm]"       # Add Claude API support
pip install -e ".[dev]"       # Add dev/test tools
pip install -e ".[llm,dev]"   # Both
```

The `codesteward` CLI is registered as a console script entry point.

---

## `codesteward init`

Initialize the database and cache directory.

```bash
codesteward init [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--db` | `TEXT` | `~/.codesteward/db.sqlite` | Path to SQLite database file |
| `--verbose` / `-v` | `FLAG` | `false` | Enable debug logging |

### Behavior

- Creates the parent directory if it doesn't exist.
- Creates all tables and indexes.
- Runs schema migrations if upgrading from an older version.
- Safe to run multiple times (idempotent).

### Example

```bash
# Default location
codesteward init

# Custom database
codesteward init --db ./my-project.sqlite
```

---

## `codesteward ingest`

Ingest repository ownership data and historical PR review data from GitHub.

```bash
codesteward ingest --repo OWNER/NAME [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--repo` | `TEXT` | **required** | GitHub repository in `owner/name` format |
| `--since` | `TEXT` | `180d` | Lookback window. Supports `Nd` (days), `Nm` (months), `Ny` (years) |
| `--areas` | `TEXT` | `None` | Comma-separated area filters (only ingest PRs touching these areas) |
| `--max-prs` | `INT` | `300` | Maximum number of PRs to fetch |
| `--resume` | `FLAG` | `false` | Only ingest PRs newer than last run |
| `--db` | `TEXT` | config default | Path to SQLite database file |
| `--config` | `TEXT` | `None` | Path to YAML config file |
| `--verbose` / `-v` | `FLAG` | `false` | Enable debug logging |

### What It Does

1. **Ownership ingestion**: fetches and parses `CODEOWNERS` and `OWNERS` files from the repository. Tries multiple paths (`CODEOWNERS`, `.github/CODEOWNERS`, `docs/CODEOWNERS` for CODEOWNERS; `OWNERS` at repo root for OWNERS).
2. **PR listing**: fetches closed PRs from GitHub sorted by last updated, filtered by the `--since` window.
3. **Bot/CVE filtering**: optionally skips PRs from bot authors (dependabot, renovate, etc.) and CVE/dependency-bump PRs. Configurable via `pr_filter` in config.
4. **Area filtering**: if `--areas` is specified, skips PRs that don't touch files in those areas.
5. **Per-PR data**: for each accepted PR, fetches and stores:
   - PR metadata (title, author, body, labels, state, timestamps)
   - Changed files (path, additions, deletions)
   - Reviews (reviewer, state, timestamp)
   - Line-level review comments (reviewer, body, file, line, timestamp)
6. **Resume tracking**: records the latest PR `created_at` timestamp for incremental runs.

### Output

Prints a summary table:

```
┌────────────────────────┐
│   Ingestion Summary    │
├────────────────┬───────┤
│ Metric         │ Count │
├────────────────┼───────┤
│ Prs            │   247 │
│ Files          │  3891 │
│ Reviews        │   812 │
│ Comments       │  2104 │
│ Ownership      │    63 │
│ Skipped Area   │    18 │
│ Skipped Bot Cve│    35 │
└────────────────┴───────┘
```

### Examples

```bash
# Basic ingestion
codesteward ingest --repo cilium/cilium --since 180d --max-prs 300

# Large repo with area filter
codesteward ingest --repo cilium/cilium --since 365d --areas sig-network --max-prs 500

# Incremental update
codesteward ingest --repo cilium/cilium --resume

# Custom database and verbose logging
codesteward ingest --repo cilium/cilium --since 90d --db ./cilium.sqlite -v
```

---

## `codesteward profile`

Build or update reviewer skill cards from ingested data.

```bash
codesteward profile --repo OWNER/NAME [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--repo` | `TEXT` | **required** | GitHub repository in `owner/name` format |
| `--top-reviewers` | `INT` | `50` | Number of top reviewers to profile (ranked by review count) |
| `--db` | `TEXT` | config default | Path to SQLite database file |
| `--config` | `TEXT` | `None` | Path to YAML config file |
| `--verbose` / `-v` | `FLAG` | `false` | Enable debug logging |

### What It Does

1. Queries the database for the top N reviewers by total review count.
2. For each reviewer, computes a skill card:
   - **Focus weights**: keyword frequency analysis across 7 domains (api, tests, perf, docs, security, style, backward_compat).
   - **Blocking threshold**: derived from changes-requested rate (HIGH >40%, MEDIUM 15-40%, LOW <=15%).
   - **Common blockers**: top 5 recurring issues from 17 regex patterns.
   - **Style preferences**: 8+ heuristic detectors (error handling, naming conventions, DRY, simplicity).
   - **Evidence preferences**: what types of evidence the reviewer asks for.
   - **Recent interests**: topic trends from last 90 days of comments.
   - **Quote bank**: 10 representative 5-25 word comment excerpts.
3. Persists each card as JSON in the `reviewer_cards` table.

### Output

Prints a reviewer summary table:

```
┌───────────────────────────────────────────────────────────────────────┐
│                      Reviewer Profiles (50)                          │
├──────────────┬─────────┬──────────────┬──────────┬──────────┬────────┤
│ Reviewer     │ Reviews │ Approval Rate│ Blocking │ Top Focus│ ...    │
├──────────────┼─────────┼──────────────┼──────────┼──────────┼────────┤
│ alice        │     342 │          72% │ high     │ api      │ ...    │
│ bob          │     218 │          89% │ low      │ tests    │ ...    │
└──────────────┴─────────┴──────────────┴──────────┴──────────┴────────┘
```

### Examples

```bash
# Profile top 50 reviewers
codesteward profile --repo cilium/cilium --top-reviewers 50

# Profile top 100 for a large repo
codesteward profile --repo cilium/cilium --top-reviewers 100
```

---

## `codesteward review`

Run a simulated multi-reviewer review on a PR or local diff.

```bash
codesteward review --repo OWNER/NAME (--pr NUMBER | --diff PATH) [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--repo` | `TEXT` | **required** | GitHub repository in `owner/name` format |
| `--pr` | `INT` | `None` | PR number to review (fetches diff from GitHub) |
| `--diff` | `TEXT` | `None` | Path to a local diff/patch file |
| `--reviewers` / `-n` | `INT` | `5` | Number of reviewers to simulate |
| `--output` / `-o` | `TEXT` | `./out` | Output directory for reports |
| `--db` | `TEXT` | config default | Path to SQLite database file |
| `--config` | `TEXT` | `None` | Path to YAML config file |
| `--verbose` / `-v` | `FLAG` | `false` | Enable debug logging |

Either `--pr` or `--diff` must be provided. `--pr` fetches the diff from GitHub (requires `GITHUB_TOKEN`). `--diff` reads a local unified diff file.

### What It Does

1. **Fetch/parse diff**: gets the PR diff and changed files.
2. **Build ChangeContext**: detects areas, risk flags, ownership, relevant docs.
3. **Discover reviewers**: ranks candidates by ownership, historical reviews, and global activity. Falls back to ownership-based candidates if no reviewers are found.
4. **Load skill cards**: retrieves profiled skill cards from the database. Creates default cards (with category-based focus weights) for reviewers without profiles.
5. **Simulate reviews**: generates a review from each reviewer persona using LLM (Claude API) or heuristic fallback. Applies evidence validation in strict mode.
6. **Aggregate**: merges reviews, deduplicates comments, detects disagreements, computes merge verdict, builds fix plan.
7. **Render output**: writes `review.md` and `review.json` to the output directory.

### Output Files

**`review.md`** -- human-readable Markdown report containing:
- Merge verdict (READY / NEEDS_CHANGES / RISKY)
- Risk flags
- Per-reviewer sections with verdicts, summaries, and grouped comments (blockers, missing tests, suggestions, docs needed, questions)
- Evidence references for each comment
- Consolidated blockers section
- Disagreements between reviewers
- Prioritized fix plan

**`review.json`** -- structured JSON (Pydantic serialization) for programmatic consumption. Contains the full `MaintainerSummary` object.

### Console Output

```
Analyzing 14 changed file(s)...
  Areas: sig-network, area-datapath
  Risk flags: api-surface, security
  Selected 5 reviewer(s): alice, bob, charlie, dave, eve

Merge Verdict: NEEDS_CHANGES
  Blockers: 3
  Suggestions: 7
  Disagreements: 1

Report written to ./out/review.md
JSON written to ./out/review.json
```

### Examples

```bash
# Review a GitHub PR
codesteward review --repo cilium/cilium --pr 12345

# Review with more reviewers
codesteward review --repo cilium/cilium --pr 12345 --reviewers 7

# Review a local diff file
codesteward review --repo cilium/cilium --diff ./my-changes.patch

# Custom output directory
codesteward review --repo cilium/cilium --pr 12345 --output ./reports
```

---

## Duration Syntax

The `--since` flag on `ingest` accepts duration strings:

| Suffix | Meaning | Example |
|--------|---------|---------|
| `d` | Days | `180d` = 180 days |
| `m` | Months (30 days each) | `6m` = 180 days |
| `y` | Years (365 days each) | `1y` = 365 days |
| (none) | Days (raw integer) | `180` = 180 days |

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error (missing token, missing input, no reviewers found, etc.) |
