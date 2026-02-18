# Data Model

This document covers all data structures in CodeSteward: the SQLite database schema, Pydantic models, and enums.

## Database Schema

CodeSteward stores all ingested data in a local SQLite database (default: `~/.codesteward/db.sqlite`). The database runs in WAL mode with foreign key constraints enabled.

**Current schema version**: 2

### Tables

#### `meta`

Key-value configuration store for internal bookkeeping.

```sql
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
```

Stored keys:
- `schema_version` -- current schema version (used for migrations)
- `last_ingest:{owner/repo}` -- ISO timestamp of the most recent ingestion run for a repo (used by `--resume`)

#### `prs`

Pull request metadata.

```sql
CREATE TABLE prs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    repo       TEXT NOT NULL,
    number     INTEGER NOT NULL,
    title      TEXT,
    author     TEXT,
    body       TEXT,
    created_at TEXT,    -- ISO 8601
    merged_at  TEXT,    -- ISO 8601 or NULL
    state      TEXT,    -- "merged", "open", "closed"
    labels_json TEXT,   -- JSON array of label name strings
    UNIQUE(repo, number)
);
```

Upserted on ingestion -- if a PR already exists (same repo + number), its metadata is updated.

#### `pr_files`

Files changed in each PR.

```sql
CREATE TABLE pr_files (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id     INTEGER NOT NULL REFERENCES prs(id),
    path      TEXT,
    additions INTEGER,
    deletions INTEGER
);
```

#### `reviews`

PR review states submitted by reviewers.

```sql
CREATE TABLE reviews (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id        INTEGER NOT NULL REFERENCES prs(id),
    reviewer     TEXT,
    state        TEXT,    -- "APPROVED", "CHANGES_REQUESTED", "COMMENTED", "PENDING", "DISMISSED"
    submitted_at TEXT     -- ISO 8601
);
```

#### `review_comments`

Line-level review comments attached to specific files and lines.

```sql
CREATE TABLE review_comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id      INTEGER NOT NULL REFERENCES prs(id),
    reviewer   TEXT,
    body       TEXT,
    path       TEXT,
    line       INTEGER,
    created_at TEXT,  -- ISO 8601
    UNIQUE(pr_id, reviewer, path, created_at)
);
```

The unique constraint prevents duplicate comment ingestion on re-runs.

#### `ownership`

CODEOWNERS and OWNERS file rules.

```sql
CREATE TABLE ownership (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    repo         TEXT,
    path_pattern TEXT,
    owner        TEXT,
    source       TEXT  -- "CODEOWNERS" or "OWNERS"
);
```

Ownership rules are cleared and re-ingested on each run to reflect the latest file contents.

#### `reviewer_cards`

Cached reviewer skill card profiles (serialized JSON).

```sql
CREATE TABLE reviewer_cards (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    repo       TEXT,
    reviewer   TEXT,
    card_json  TEXT,       -- Serialized ReviewerSkillCard
    updated_at TEXT,       -- ISO 8601
    UNIQUE(repo, reviewer)
);
```

### Indexes

```sql
CREATE INDEX idx_prs_repo          ON prs(repo);
CREATE INDEX idx_pr_files_pr_id    ON pr_files(pr_id);
CREATE INDEX idx_reviews_pr_id     ON reviews(pr_id);
CREATE INDEX idx_reviews_reviewer  ON reviews(reviewer);
CREATE INDEX idx_rc_pr_id          ON review_comments(pr_id);
CREATE INDEX idx_rc_reviewer       ON review_comments(reviewer);
CREATE INDEX idx_rc_path           ON review_comments(path);
CREATE INDEX idx_ownership_repo    ON ownership(repo);
CREATE INDEX idx_reviewer_cards_repo ON reviewer_cards(repo);
CREATE INDEX idx_reviews_pr_reviewer_state ON reviews(pr_id, reviewer, state);
```

### Migrations

The database auto-migrates from v1 to v2 on startup:

- **v1 -> v2**: Adds `body` column to `prs` table, adds `labels_json` column to `prs` table.

### Pattern Matching

The `get_owners_for_path` query uses application-level pattern matching (not SQL LIKE) supporting:

- Exact path match
- Directory prefix (`/path/` matches all files under that directory)
- Wildcards (`*` matches any single path component)
- Globstar (`**` matches any depth of directories)
- Leading `/` anchors to repo root

---

## Pydantic Models

All runtime data structures are defined in `codesteward/schemas.py` using Pydantic v2.

### Enums

#### `RiskFlag`

Categories of change risk detected from file paths and diff content.

```python
class RiskFlag(str, Enum):
    API_SURFACE    = "api-surface"
    SECURITY       = "security"
    PERF           = "perf"
    COMPAT         = "backward-compat"
    WINDOWS        = "windows"
    LARGE_DIFF     = "large-diff"
    NEW_DEPENDENCY = "new-dependency"
    CONFIG_CHANGE  = "config-change"
    TEST_ONLY      = "test-only"
    DOCS_ONLY      = "docs-only"
```

#### `ReviewerCategory`

Reviewer specialization types derived from comment analysis.

```python
class ReviewerCategory(str, Enum):
    PRIMARY_OWNER     = "primary-owner"
    TEST_CI_HAWK      = "test-ci-hawk"
    API_STABILITY_HAWK = "api-stability-hawk"
    SECURITY_HAWK     = "security-hawk"
    DOCS_HAWK         = "docs-hawk"
    GENERAL           = "general"
```

#### `BlockingThreshold`

How frequently a reviewer requests changes.

```python
class BlockingThreshold(str, Enum):
    LOW    = "low"     # <= 15% changes-requested rate
    MEDIUM = "medium"  # 15-40% changes-requested rate
    HIGH   = "high"    # > 40% changes-requested rate
```

#### `EvidenceType`

```python
class EvidenceType(str, Enum):
    DIFF    = "diff"     # file:line reference
    DOC     = "doc"      # repository document reference
    HISTORY = "history"  # prior PR/commit reference
```

#### `MergeVerdict`

```python
class MergeVerdict(str, Enum):
    READY        = "READY"
    NEEDS_CHANGES = "NEEDS_CHANGES"
    RISKY        = "RISKY"
```

### Core Models

#### `ChangedFile`

A file modified in a PR.

| Field | Type | Description |
|-------|------|-------------|
| `path` | `str` | File path relative to repo root |
| `additions` | `int` | Lines added |
| `deletions` | `int` | Lines deleted |
| `patch` | `str` | Raw unified diff hunk (default: `""`) |

#### `ChangeContext`

Complete metadata for a PR under review. Built by `RepoMapper`.

| Field | Type | Description |
|-------|------|-------------|
| `repo` | `str` | Repository in `owner/name` format |
| `base_ref` | `str` | Base branch (default: `"main"`) |
| `head_ref` | `str` | PR branch (default: `""`) |
| `pr_number` | `int \| None` | PR number |
| `pr_title` | `str` | PR title |
| `pr_body` | `str` | PR description body |
| `changed_files` | `list[ChangedFile]` | All modified files |
| `areas` | `list[str]` | Detected area labels |
| `likely_reviewers` | `list[str]` | Ownership/history-based reviewer candidates |
| `relevant_docs` | `list[str]` | Suggested documentation files |
| `risk_flags` | `list[str]` | Detected risk categories |

#### `ReviewerInfo`

A discovered reviewer with ranking metadata.

| Field | Type | Description |
|-------|------|-------------|
| `login` | `str` | GitHub username |
| `score` | `float` | Relevance score (default: `0.0`) |
| `categories` | `list[ReviewerCategory]` | Detected specializations |
| `ownership_paths` | `list[str]` | CODEOWNERS paths they own |
| `review_count` | `int` | Reviews on changed files |

#### `FocusWeights`

Normalized (0.0-1.0) expertise distribution across review domains.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `api` | `float` | `0.0` | API surface and interface design |
| `tests` | `float` | `0.0` | Test coverage and quality |
| `perf` | `float` | `0.0` | Performance and scalability |
| `docs` | `float` | `0.0` | Documentation completeness |
| `security` | `float` | `0.0` | Security concerns |
| `style` | `float` | `0.0` | Code style and conventions |
| `backward_compat` | `float` | `0.0` | Backward compatibility |

#### `ReviewerSkillCard`

Complete behavioral profile for a reviewer persona.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `reviewer` | `str` | -- | GitHub login |
| `focus_weights` | `FocusWeights` | -- | Expertise distribution |
| `blocking_threshold` | `BlockingThreshold` | -- | How often they block |
| `common_blockers` | `list[str]` | `[]` | Recurring issues they flag |
| `style_preferences` | `list[str]` | `[]` | Code style heuristics |
| `evidence_preferences` | `list[str]` | `[]` | Types of evidence they request |
| `recent_interests` | `list[str]` | `[]` | Topics from last 90 days |
| `quote_bank` | `list[str]` | `[]` | Representative comment excerpts |
| `total_reviews` | `int` | `0` | Total review count |
| `approval_rate` | `float` | `0.0` | Fraction of approvals |
| `avg_comments_per_review` | `float` | `0.0` | Average comments per review |

Serialized as JSON and stored in the `reviewer_cards` table.

#### `Evidence`

Grounding reference for a review comment.

| Field | Type | Description |
|-------|------|-------------|
| `type` | `EvidenceType` | Category of evidence |
| `ref` | `str` | Reference string (e.g., `"src/foo.py:42"`, `"docs/api.md#endpoints"`, `"pr#789"`) |
| `snippet` | `str` | Relevant code or text excerpt (default: `""`) |

#### `ReviewComment`

Individual comment from a simulated reviewer.

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `str` | One of: `"blocker"`, `"suggestion"`, `"missing-test"`, `"docs-needed"`, `"question"` |
| `body` | `str` | Comment text |
| `file` | `str` | Affected file path (default: `""`) |
| `line` | `int \| None` | Line number |
| `evidence` | `Evidence \| None` | Grounding evidence |
| `confidence` | `float` | Confidence score 0.0-1.0 (default: `0.8`) |

#### `ReviewerReview`

One reviewer's complete review output.

| Field | Type | Description |
|-------|------|-------------|
| `reviewer` | `str` | GitHub login |
| `category` | `str` | Reviewer category |
| `summary_bullets` | `list[str]` | Executive summary points |
| `comments` | `list[ReviewComment]` | Detailed feedback |
| `verdict` | `str` | One of: `"approve"`, `"request-changes"`, `"comment"` |

#### `MaintainerSummary`

Final aggregated review report.

| Field | Type | Description |
|-------|------|-------------|
| `repo` | `str` | Repository |
| `pr_number` | `int \| None` | PR number |
| `pr_title` | `str` | PR title |
| `verdict` | `MergeVerdict` | Merge recommendation |
| `risk_flags` | `list[str]` | Consolidated risk flags |
| `reviewer_reviews` | `list[ReviewerReview]` | All per-reviewer reviews |
| `merged_blockers` | `list[ReviewComment]` | Deduplicated blockers |
| `merged_suggestions` | `list[ReviewComment]` | Deduplicated suggestions |
| `disagreements` | `list[dict]` | Reviewer conflicts |
| `fix_plan` | `list[str]` | Prioritized action items |
| `generated_at` | `datetime` | Report generation timestamp |

#### `OwnershipEntry`

Parsed CODEOWNERS/OWNERS rule.

| Field | Type | Description |
|-------|------|-------------|
| `path_pattern` | `str` | File path glob pattern |
| `owners` | `list[str]` | Owner usernames |
| `source` | `str` | `"CODEOWNERS"` or `"OWNERS"` |

### Configuration Models

#### `LLMConfig`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | `str` | `"claude-sonnet-4-20250514"` | Claude model ID |
| `max_tokens` | `int` | `4096` | Max response tokens |
| `max_diff_chars` | `int` | `12000` | Max diff characters sent to LLM |

#### `PRFilterConfig`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `True` | Enable bot/CVE filtering |
| `bot_author_patterns` | `list[str]` | 11 patterns | Regex patterns matching bot authors |
| `title_patterns` | `list[str]` | 8 patterns | Regex patterns matching bot PR titles |
| `label_patterns` | `list[str]` | 6 patterns | Regex patterns matching bot labels |
| `allowlist_authors` | `list[str]` | `[]` | Authors that bypass bot filter |
| `allowlist_title_substrings` | `list[str]` | `[]` | Title substrings that prevent filtering |

#### `Config`

Main application configuration. See [Configuration](configuration.md) for full details.
