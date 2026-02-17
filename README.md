# CodeSteward

A CLI tool that simulates multi-reviewer code reviews for GitHub pull requests.
It discovers likely reviewers, builds behavioral profiles from their historical activity,
and generates evidence-grounded review feedback per reviewer persona.

## How It Works

1. **Ingest** historical PR data (reviews, comments, files) from a GitHub repo
2. **Profile** reviewers to build skill cards capturing their focus areas, blocking tendencies, and style preferences
3. **Review** a target PR by simulating each reviewer persona against the diff
4. **Aggregate** all reviews into a maintainer summary with a merge readiness verdict

Every generated comment must cite evidence (a diff location, repo doc reference, or historical PR discussion). If evidence is missing, the system outputs a question instead of a claim.

## Setup

### Prerequisites

- Python 3.11+
- A GitHub personal access token with `repo` scope

### Install

```bash
# Clone the repository
git clone <repo-url> && cd codesteward

# Create a virtual environment
python -m venv .venv && source .venv/bin/activate

# Install the package
pip install -e .

# Or install with dev dependencies
pip install -e ".[dev]"
```

### Environment Variables

```bash
# Required: GitHub API access
export GITHUB_TOKEN="ghp_..."

# Optional: enables Claude-powered review simulation (recommended)
export ANTHROPIC_API_KEY="sk-ant-..."
```

Without `ANTHROPIC_API_KEY`, the tool falls back to heuristic/template-based review generation.

## Usage

### 1. Initialize the Database

```bash
codesteward init
# Database initialized at ~/.codesteward/db.sqlite

# Custom location:
codesteward init --db ./my-project.sqlite
```

### 2. Ingest Historical Data

```bash
# Ingest last 6 months of PR data from a repo
codesteward ingest --repo kubernetes/kubernetes --since 180d --max-prs 300

# Ingest with area filter
codesteward ingest --repo kubernetes/kubernetes --since 365d --areas sig-network --max-prs 500
```

This fetches:
- PR metadata (title, author, labels, state)
- Files changed per PR
- Reviews (approval/rejection state)
- Line-level review comments
- CODEOWNERS and OWNERS file mappings

### 3. Build Reviewer Profiles

```bash
# Profile the top 50 reviewers by activity
codesteward profile --repo kubernetes/kubernetes --top-reviewers 50
```

Each profile (skill card) includes:
- **Focus weights**: api, tests, perf, docs, security, style, backward-compat
- **Blocking threshold**: how often they request changes vs. comment
- **Common blockers**: recurring issues they flag
- **Style preferences**: extracted heuristics from their comments
- **Quote bank**: short representative comment excerpts

### 4. Run a Simulated Review

```bash
# Review a PR by number (fetches diff from GitHub)
codesteward review --repo kubernetes/kubernetes --pr 12345

# Review a local diff file
codesteward review --repo kubernetes/kubernetes --diff ./my-changes.patch

# Control number of reviewers
codesteward review --repo kubernetes/kubernetes --pr 12345 --reviewers 3

# Custom output directory
codesteward review --repo kubernetes/kubernetes --pr 12345 --output ./reports
```

### Output

The tool writes two files to `./out/` (configurable):

**`review.md`** - Human-readable Markdown report:
- Merge verdict (READY / NEEDS_CHANGES / RISKY)
- Per-reviewer sections with blockers, suggestions, and questions
- Consolidated blockers with evidence references
- Disagreements between reviewers
- Prioritized fix plan

**`review.json`** - Structured JSON containing all comments with evidence objects.

## Configuration

Create `codesteward.yaml` in the project root or `~/.codesteward/config.yaml`:

```yaml
repo: kubernetes/kubernetes
default_areas:
  - sig-network
  - sig-api-machinery
ingest_window_days: 180
max_prs: 300
reviewer_count: 5
strict_evidence_mode: true
output_dir: ./out
redact_quotes: false
```

CLI flags override config file values. Environment variables (`GITHUB_TOKEN`, `ANTHROPIC_API_KEY`) override both.

## Architecture

```
codesteward/
  cli.py            # Typer CLI entrypoint
  config.py         # YAML + env config loading
  db.py             # SQLite schema and queries
  github_client.py  # GitHub REST API client with rate limiting
  repo_mapper.py    # CODEOWNERS/OWNERS parsing, area mapping
  ingest.py         # Historical PR data ingestion
  discovery.py      # Reviewer ranking and categorization
  profiler.py       # ReviewerSkillCard generation
  simulator.py      # Review simulation (Claude API + heuristic fallback)
  aggregator.py     # Multi-review merging and verdict
  render.py         # Markdown + JSON output
  schemas.py        # Pydantic data models
```

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Evidence Grounding

The system enforces strict evidence grounding. Each comment must reference:

| Evidence Type | Format | Example |
|---|---|---|
| `diff` | `file:line` | `src/api/handler.py:42` |
| `doc` | `path#section` | `CONTRIBUTING.md#style-guide` |
| `history` | `pr#N excerpt` | `PR #789: "avoid hidden defaults"` |

If a concern cannot be backed by evidence, it is automatically converted to a **question**.

## Limitations

- **No auto-posting**: outputs are draft reports only; nothing is posted to GitHub
- **Heuristic fallback**: without the Anthropic API key, review comments are template-based
- **Rate limits**: large repos may require multiple ingestion runs
- **Embeddings**: keyword-based retrieval only (TF-IDF/embedding search is a stub for now)
