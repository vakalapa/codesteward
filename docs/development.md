# Development Guide

This document covers setting up a development environment, running tests, project structure, and contributing.

## Prerequisites

- Python 3.11+
- A GitHub personal access token with `repo` scope (for integration testing)
- (Optional) An Anthropic API key for LLM-path testing

## Setup

```bash
# Clone and enter the repo
git clone <repo-url> && cd codesteward

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install with all development dependencies
pip install -e ".[dev,llm]"
```

## Project Structure

```
codesteward/
  __init__.py
  cli.py              CLI entrypoint (Typer)
  config.py            Config loading (YAML + env + CLI)
  db.py                SQLite database layer
  github_client.py     GitHub REST API client
  ingest.py            PR data ingestion pipeline
  repo_mapper.py       CODEOWNERS/OWNERS parsing, area/risk detection
  pr_filter.py         Bot/CVE PR classification
  discovery.py         Reviewer ranking and selection
  profiler.py          Reviewer skill card generation
  simulator.py         Review simulation (LLM + heuristic)
  evidence.py          Evidence validation pipeline
  aggregator.py        Multi-review merging and verdict
  render.py            Markdown + JSON output rendering
  schemas.py           Pydantic data models and enums

tests/
  test_aggregator.py   Aggregation, dedup, verdicts, fix plans
  test_cli.py          CLI helper functions
  test_codeowners.py   CODEOWNERS parser
  test_config.py       Config loading and overrides
  test_db.py           Database operations and queries
  test_e2e.py          End-to-end pipeline tests
  test_evidence.py     Evidence validation pipeline
  test_owners.py       Kubernetes OWNERS parser
  test_pr_filter.py    Bot/CVE PR filtering
  test_ranking.py      Reviewer discovery and ranking

backlog/
  meta.md              Backlog index and workflow
  001-*.md through     Feature specs
  010-*.md

docs/
  README.md            High-level design document
  architecture.md      System architecture
  data-model.md        Database schema and Pydantic models
  configuration.md     Config reference
  cli-reference.md     CLI command reference
  evidence-grounding.md Evidence system deep dive
  reviewer-profiles.md  Skill card profiling
  heuristic-engine.md  Heuristic fallback engine
  development.md       This file
```

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_evidence.py -v

# Run with coverage
pytest tests/ --cov=codesteward --cov-report=term-missing

# Run only fast tests (no network, no LLM)
pytest tests/ -v -k "not e2e"
```

All tests use in-memory SQLite databases and mock external services. No GitHub token or Anthropic key is needed for the test suite.

## Test Architecture

Tests are organized by component:

- **Unit tests**: test individual functions and classes in isolation. Most test files (test_aggregator, test_cli, test_codeowners, test_config, test_db, test_evidence, test_owners, test_pr_filter, test_ranking) are unit tests.
- **End-to-end tests**: `test_e2e.py` runs the full pipeline (init -> ingest -> profile -> review -> aggregate -> render) with mocked GitHub responses.
- **Fixtures**: tests use `@pytest.fixture` for shared setup (databases, sample data, mock clients).
- **Parametrized tests**: `@pytest.mark.parametrize` is used extensively for testing multiple input combinations.

### Writing Tests

When adding a new feature:

1. Write tests first or alongside the implementation.
2. Use in-memory SQLite (`:memory:`) for database tests.
3. Mock `GitHubClient` for any test that would make network calls.
4. Test both success and failure paths.
5. For the simulator, test both LLM and heuristic paths.
6. For evidence validation, test strict and lenient modes.

Example test structure:

```python
import pytest
from codesteward.db import Database
from codesteward.schemas import ReviewComment, Evidence, EvidenceType


@pytest.fixture
def db():
    database = Database(":memory:")
    database.init_schema()
    yield database
    database.close()


def test_my_feature(db):
    # Arrange
    ...
    # Act
    result = my_function(db, ...)
    # Assert
    assert result.field == expected_value
```

## Code Quality Tools

### Ruff (Linter + Formatter)

```bash
# Check for issues
ruff check codesteward/

# Auto-fix
ruff check codesteward/ --fix

# Format
ruff format codesteward/
```

Configuration in `pyproject.toml`:
- Target: Python 3.11
- Line length: 100

### Mypy (Type Checker)

```bash
mypy codesteward/
```

Configuration in `pyproject.toml`:
- Strict mode enabled
- `warn_return_any: true`

## Database Migrations

When modifying the database schema:

1. Increment the schema version in `db.py`.
2. Add a migration function in `_run_migrations()`.
3. Migrations run automatically on `init_schema()`.
4. Test the migration path in `test_db.py`.

Current migration history:
- **v1 -> v2**: Added `body` and `labels_json` columns to `prs` table.

## Adding a New Scanner

To add a new pattern scanner to the heuristic engine:

1. Add the scanner method to `ReviewSimulator` in `simulator.py`:

```python
def _scan_my_patterns(self, path: str, patch: str) -> list[ReviewComment]:
    comments = []
    for i, line in enumerate(patch.split("\n")):
        if line.startswith("+") and "my_pattern" in line:
            comments.append(ReviewComment(
                kind="suggestion",
                body="Description of the issue.",
                file=path,
                line=i,
                evidence=Evidence(
                    type=EvidenceType.DIFF,
                    ref=f"{path}:{i}",
                    snippet=line[1:].strip(),
                ),
            ))
    return comments
```

2. Call it from `_simulate_heuristic()` under the appropriate focus area check.
3. Add tests in a new or existing test file.

## Adding a New Focus Domain

To add a new focus domain (e.g., `observability`):

1. Add the field to `FocusWeights` in `schemas.py`.
2. Add keywords to `TOPIC_KEYWORDS` in `profiler.py`.
3. Add scanner logic in `simulator.py` for the heuristic path.
4. Update the LLM system prompt template in `simulator.py` to include the new weight.
5. Update tests.

## Backlog Workflow

Feature development follows the backlog workflow defined in `backlog/meta.md`:

1. Pick a feature from the backlog index.
2. Refine the spec (the `.md` file in `backlog/`).
3. Implement with tests.
4. Update the feature status.

Features are numbered and may have dependencies on each other. Check `meta.md` for the dependency graph.

### Current Feature Status

| # | Feature | Status |
|---|---------|--------|
| 001 | Evidence Validation Pipeline | Done |
| 002 | Evidence Retrieval Layer | Not Started |
| 003 | Reviewer Discovery v2 | Not Started |
| 004 | Repo Mapping v2 | Not Started |
| 005 | Reviewer Profiler v2 | Not Started |
| 006 | Aggregator Intelligence v2 | Not Started |
| 007 | Evaluation Replay Framework | Not Started |
| 008 | Agent Loop Interface | Not Started |
| 009 | Operability & Governance | Not Started |
| 010 | Bot/CVE PR Filtering | Done |

## Debugging

### Verbose Logging

All CLI commands accept `-v` / `--verbose` for debug-level logging via Rich:

```bash
codesteward ingest --repo owner/name --since 30d -v
```

### Database Inspection

The SQLite database can be inspected directly:

```bash
sqlite3 ~/.codesteward/db.sqlite

-- Check schema version
SELECT * FROM meta;

-- Count ingested data
SELECT repo, COUNT(*) FROM prs GROUP BY repo;
SELECT COUNT(*) FROM reviews;
SELECT COUNT(*) FROM review_comments;

-- Check reviewer cards
SELECT reviewer, updated_at FROM reviewer_cards WHERE repo = 'owner/name';
```

### Heuristic vs. LLM Debugging

To compare heuristic and LLM output:

1. Run with `ANTHROPIC_API_KEY` set for LLM mode.
2. Run without `ANTHROPIC_API_KEY` (or with an invalid key) for heuristic fallback.
3. Compare the `review.json` outputs.

The heuristic path logs which scanners were selected and how many matches were found at debug level (`-v`).
