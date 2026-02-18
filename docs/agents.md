# Agent Rules

This document defines the rules and expectations for any AI agent (Claude Code, Copilot, Cursor, or similar) working on the CodeSteward codebase. These rules are non-negotiable.

---

## Rule 1: Every Change Must Have Tests

No code change is complete without corresponding tests. This applies to bug fixes, new features, refactors, and scanner additions alike.

### What "thorough tests" means

- **Cover the happy path and failure paths.** If a function can succeed or fail, test both.
- **Cover edge cases.** Empty inputs, None values, boundary values, malformed data, unicode, whitespace-only strings.
- **Use parametrize for variant coverage.** When a function handles multiple input shapes (different evidence types, different PR formats, different reviewer categories), use `@pytest.mark.parametrize` rather than writing separate test functions.
- **Test integration points.** If your change affects how two components interact (e.g., simulator -> evidence validator, ingestor -> PR filter), write integration tests that exercise the full flow.
- **Test both strict and lenient modes.** Any change touching evidence validation must verify behavior in both `strict=True` and `strict=False` modes.
- **Don't test implementation details.** Test behavior and outputs, not private method internals. If you refactor internals, existing tests should still pass.
- **Immutability checks.** When a function transforms data (e.g., comment downgrade), verify that the original input is not mutated.

### Test file conventions

- Test files go in `tests/` and are named `test_<module>.py`.
- Use `@pytest.fixture` for shared setup (databases, sample data, mock clients).
- Use in-memory SQLite (`:memory:`) for database tests.
- Mock `GitHubClient` for any test that would make network calls.
- Group related tests in classes (e.g., `TestValidateCommentStrict`, `TestBotAuthorPatterns`).
- Include a module-level docstring listing what the test file covers.
- Use `# ==== Section ====` comment separators between test groups for readability.

### Test structure

Follow Arrange-Act-Assert:

```python
def test_blocker_without_evidence_downgraded(self) -> None:
    # Arrange
    comment = _make_comment(kind="blocker", body="Bad code")

    # Act
    result = self.validator.validate_comment(comment)

    # Assert
    assert result.kind == "question"
    assert result.confidence == CONFIDENCE_MISSING_EVIDENCE
    assert "Evidence needed" in result.body
```

### When to add tests

| Change type | Required tests |
|-------------|---------------|
| Bug fix | Regression test proving the bug is fixed + edge cases around it |
| New feature | Unit tests for the feature + integration tests for how it connects to existing components |
| New scanner | Tests with matching and non-matching patch content, comment cap verification, evidence validity |
| New config option | Test default value, override behavior, interaction with existing options |
| Schema change | Migration test (old schema -> new schema), query tests with new columns |
| Refactor | Existing tests must pass unchanged. If they don't, the refactor changed behavior. |

### Running tests

```bash
# Run all tests
pytest tests/ -v

# Run a specific file
pytest tests/test_evidence.py -v

# Run with coverage
pytest tests/ --cov=codesteward --cov-report=term-missing
```

All tests must pass before a change is considered complete. Do not merge with failing tests.

---

## Rule 2: Every Change Must Update Documentation

Documentation is not optional. When you fix a bug, add a feature, or change behavior, the corresponding docs must be updated in the same change.

### What "docs for every fix" means

- **If you change CLI behavior**, update `docs/cli-reference.md`.
- **If you add or change config options**, update `docs/configuration.md`.
- **If you modify the database schema**, update `docs/data-model.md` (tables, columns, indexes, migrations).
- **If you add or modify Pydantic models/enums**, update `docs/data-model.md`.
- **If you change the evidence validation pipeline**, update `docs/evidence-grounding.md`.
- **If you change the profiling algorithm or skill card structure**, update `docs/reviewer-profiles.md`.
- **If you add or modify heuristic scanners**, update `docs/heuristic-engine.md` (scanner tables, pattern descriptions).
- **If you change component interactions or add new components**, update `docs/architecture.md`.
- **If you change the development workflow**, update `docs/development.md`.
- **If you complete a backlog item**, update its status in `backlog/meta.md`.

### Documentation standards

- Be precise and concrete. Include actual field names, types, default values, and example values.
- Use tables for structured data (config options, scanner patterns, model fields).
- Include code examples where behavior is easier to show than describe.
- Don't duplicate information across docs. Reference other docs with relative links (e.g., `See [Configuration](configuration.md) for details.`).
- Keep the docs index in `docs/README.md` current when adding new docs.

---

## Rule 3: Understand Before Changing

- **Read the code before modifying it.** Do not propose changes to files you haven't read.
- **Read the tests before modifying them.** Understand what's being tested and why.
- **Read the backlog spec before implementing a feature.** Each backlog item (`backlog/NNN-*.md`) has scope, acceptance criteria, and a test strategy. Follow them.
- **Read `docs/README.md` for design philosophy.** CodeSteward is a stewardship tool, not a linter. Every design decision traces back to modeling real reviewer behavior.

---

## Rule 4: Evidence Grounding Is Non-Negotiable

This is the project's core invariant. Any code that generates review comments must ensure:

1. Every non-question comment has a valid `Evidence` object with `type`, `ref`, and (for diff evidence) `snippet`.
2. Invalid or missing evidence causes deterministic downgrade to `kind: "question"` in strict mode.
3. The `EvidenceValidator` is the single source of truth for validation. Do not bypass it or create parallel validation logic.
4. New scanners must attach evidence to every comment they produce.

See `docs/evidence-grounding.md` for the full specification.

---

## Rule 5: Follow Existing Patterns

### Code conventions

- **Pydantic v2** for all data models. Define models in `schemas.py`.
- **Type hints** on all function signatures and return types.
- **`from __future__ import annotations`** at the top of every module.
- **Line length**: 100 characters (configured in `pyproject.toml` via ruff).
- **Python 3.11+** syntax (union types with `|`, match statements where appropriate).
- **Use existing helpers.** Check `schemas.py`, `db.py`, and existing test files for helper functions before creating new ones.

### Architecture conventions

- **Database access** goes through `db.py`. Do not write raw SQL outside the `Database` class.
- **GitHub API access** goes through `github_client.py`. Do not call `requests` directly.
- **Configuration** flows through `config.py`. Do not read environment variables directly in other modules.
- **All review comments** flow through the evidence validator. Do not skip validation.

### Naming conventions

- Test helper functions: `_make_*(...)` (e.g., `_make_comment`, `_make_evidence`, `_pr`, `_classifier`).
- Test classes: `Test<WhatIsBeingTested>` (e.g., `TestValidateCommentStrict`, `TestBotAuthorPatterns`).
- Private methods: `_method_name` (single leading underscore).
- Constants: `UPPER_SNAKE_CASE`.

---

## Rule 6: Scope Discipline

- **Do only what is asked.** A bug fix does not include a refactor of surrounding code. A new scanner does not include changes to unrelated scanners.
- **Do not add features that aren't in scope.** If the backlog spec says "out of scope", respect it.
- **Do not add comments, docstrings, or type annotations to code you didn't change.** The only exception is if the change is specifically about documentation.
- **Do not "improve" existing code while fixing a bug.** Fix the bug, add the test, update the docs, stop.
- **Do not over-engineer.** Three similar lines of code are better than a premature abstraction.

---

## Rule 7: Backlog Workflow

When implementing a backlog feature:

1. Read the full spec in `backlog/NNN-*.md`.
2. Check dependencies in `backlog/meta.md`. Do not start a feature whose dependencies are not `Done`.
3. Implement what the spec defines. Follow the acceptance criteria exactly.
4. Write tests matching the spec's test strategy section.
5. Update docs for every behavioral change.
6. Update the feature's status in `backlog/meta.md` to `Done`.
7. Leave handoff notes in the spec file if anything was deferred or changed from the original plan.

---

## Rule 8: Git Hygiene

- Write concise commit messages that explain *why*, not just *what*.
- Do not commit secrets, tokens, or credentials.
- Do not commit generated files (database files, `__pycache__`, `.pyc`).
- Do not force-push or rewrite published history.
- Stage specific files rather than using `git add -A` or `git add .`.

---

## Checklist Before Declaring a Task Complete

Use this checklist for every change:

- [ ] Code change is minimal and scoped to the task.
- [ ] Tests are thorough: happy path, failure path, edge cases, integration.
- [ ] All existing tests still pass (`pytest tests/ -v`).
- [ ] Documentation is updated for every behavioral change.
- [ ] Evidence grounding invariant is maintained (if touching simulator/evidence code).
- [ ] No new warnings from `ruff check codesteward/`.
- [ ] Backlog status updated (if implementing a backlog feature).

---

## Quick Reference: Doc File Responsibilities

| When you change... | Update this doc |
|---------------------|-----------------|
| CLI commands or flags | `docs/cli-reference.md` |
| Config options, env vars | `docs/configuration.md` |
| Database schema, models, enums | `docs/data-model.md` |
| Component interactions, new modules | `docs/architecture.md` |
| Evidence validation rules | `docs/evidence-grounding.md` |
| Profiling algorithm, skill cards | `docs/reviewer-profiles.md` |
| Heuristic scanners, verdict logic | `docs/heuristic-engine.md` |
| Dev setup, testing, project structure | `docs/development.md` |
| Agent rules or conventions | `docs/agents.md` |
| Backlog feature status | `backlog/meta.md` |
