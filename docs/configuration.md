# Configuration

CodeSteward loads configuration from three sources in order of increasing priority:

1. **YAML config file** (lowest priority)
2. **Environment variables**
3. **CLI flags** (highest priority)

## Config File

CodeSteward searches for a YAML config file in this order:

1. `./codesteward.yaml` (current directory)
2. `~/.codesteward/config.yaml` (home directory)

A custom path can be passed via `--config` on any CLI command.

### Full Config File Reference

```yaml
# Target repository (owner/name format)
repo: cilium/cilium

# Default area filters for ingestion
default_areas:
  - sig-network
  - sig-api

# PR lookback window in days
ingest_window_days: 180

# Maximum PRs to fetch per ingestion run
max_prs: 300

# Number of reviewers to simulate per PR review
reviewer_count: 5

# Enforce evidence grounding on all comments
# When true, ungrounded claims are downgraded to questions
strict_evidence_mode: true

# Output directory for review reports
output_dir: ./out

# Redact @mentions, PR#, URLs, and commit SHAs from reviewer quote banks
redact_quotes: false

# Lines-of-change threshold that triggers the "large-diff" risk flag
large_diff_threshold: 500

# LLM configuration
llm:
  # Claude model ID for review simulation
  model: claude-sonnet-4-20250514
  # Maximum response tokens from Claude
  max_tokens: 4096
  # Maximum diff characters sent in the LLM prompt
  max_diff_chars: 12000

# PR filtering policy (bot/CVE noise reduction)
pr_filter:
  # Master enable/disable for PR filtering
  enabled: true

  # Regex patterns matching bot author logins
  bot_author_patterns:
    - "^dependabot"
    - "^renovate"
    - "^snyk-bot$"
    - "^greenkeeper"
    - "^pyup-bot$"
    - "^mend-bolt"
    - "^deepsource"
    - "^github-actions"
    - ".*-bot$"
    - ".*\\[bot\\]$"

  # Regex patterns matching bot/CVE PR titles
  title_patterns:
    - "^CVE-\\d{4}-\\d+"
    - "^Bump .+ from .+ to"
    - "^chore\\(deps\\): bump"
    - "^chore: bump"
    - "^dependency bump"
    - "^\\[Security\\] Bump"
    - "^Update .+ requirement"
    - "^(dependabot|renovate)"

  # Regex patterns matching bot/CVE labels
  label_patterns:
    - "^dependencies$"
    - "^automated$"
    - "^bot$"
    - "^security-patch$"
    - "^cve-patch$"
    - "^dep-update$"

  # Authors that always bypass the bot filter
  allowlist_authors: []

  # Title substrings that prevent filtering (override patterns)
  allowlist_title_substrings: []
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_TOKEN` | Yes | GitHub personal access token with `repo` scope |
| `ANTHROPIC_API_KEY` | No | Anthropic API key for Claude-powered review simulation |
| `CODESTEWARD_DB` | No | Override SQLite database path |

Environment variables override config file values. `GITHUB_TOKEN` is required for all commands that access the GitHub API (`ingest`, `review` with `--pr`).

Without `ANTHROPIC_API_KEY`, the review simulator falls back to the built-in heuristic engine (pattern-based analysis without LLM).

## CLI Flag Overrides

Every CLI command accepts flags that override both the config file and environment variables:

| Flag | Commands | Description |
|------|----------|-------------|
| `--repo` | ingest, profile, review | Target repository (owner/name) |
| `--db` | all | SQLite database path |
| `--config` | ingest, profile, review | Path to YAML config file |
| `--verbose` / `-v` | all | Enable debug logging |
| `--since` | ingest | Lookback window (e.g., `180d`, `6m`, `1y`) |
| `--areas` | ingest | Comma-separated area filters |
| `--max-prs` | ingest | Maximum PRs to ingest |
| `--resume` | ingest | Only ingest PRs newer than last run |
| `--top-reviewers` | profile | Number of reviewers to profile |
| `--pr` | review | PR number to review |
| `--diff` | review | Path to local diff/patch file |
| `--reviewers` / `-n` | review | Number of reviewers to simulate |
| `--output` / `-o` | review | Output directory |

## Defaults

| Setting | Default Value |
|---------|---------------|
| Database path | `~/.codesteward/db.sqlite` |
| Ingest window | 180 days |
| Max PRs | 300 |
| Reviewer count | 5 |
| Strict evidence | `true` |
| Output directory | `./out` |
| Redact quotes | `false` |
| Large diff threshold | 500 lines |
| LLM model | `claude-sonnet-4-20250514` |
| LLM max tokens | 4096 |
| LLM max diff chars | 12000 |
| PR filtering | enabled |

## Resolution Order

For any given setting, the effective value is determined by:

```
CLI flag  >  Environment variable  >  Config file  >  Built-in default
```

Example: if `repo` is set in `codesteward.yaml` but `--repo` is passed on the command line, the CLI flag wins.

## Database Path Resolution

The database path is resolved in this order:

1. `--db` CLI flag
2. `CODESTEWARD_DB` environment variable
3. `db_path` in YAML config
4. `~/.codesteward/db.sqlite` (default)

The parent directory is created automatically by `codesteward init`.

## Example Configurations

### Minimal (env-only)

```bash
export GITHUB_TOKEN="ghp_..."
codesteward init
codesteward ingest --repo cilium/cilium --since 180d
codesteward profile --repo cilium/cilium
codesteward review --repo cilium/cilium --pr 12345
```

### Project-specific config

Create `codesteward.yaml` in your project root:

```yaml
repo: cilium/cilium
ingest_window_days: 365
max_prs: 500
reviewer_count: 7
output_dir: ./reviews
llm:
  model: claude-sonnet-4-20250514
  max_diff_chars: 16000
```

Then run:

```bash
codesteward ingest --since 365d
codesteward profile --top-reviewers 100
codesteward review --pr 12345
```

### Heuristic-only mode (no LLM)

Omit `ANTHROPIC_API_KEY` to use the built-in pattern-based review engine:

```bash
export GITHUB_TOKEN="ghp_..."
# No ANTHROPIC_API_KEY set
codesteward review --repo cilium/cilium --pr 12345
```

The heuristic engine produces reviewer-persona-constrained feedback using static pattern analysis. See [Heuristic Engine](heuristic-engine.md) for details.

### Disable bot filtering

```yaml
pr_filter:
  enabled: false
```

Or keep filtering enabled but allowlist specific authors:

```yaml
pr_filter:
  enabled: true
  allowlist_authors:
    - "my-trusted-bot"
  allowlist_title_substrings:
    - "critical dependency"
```
