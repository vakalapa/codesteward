# CodeSteward - Agent Instructions

Read `docs/agents.md` before making any changes. It defines the non-negotiable rules for this codebase.

## Critical Rules

1. **Every change must have thorough tests.** Happy path, failure path, edge cases, integration. No exceptions.
2. **Every change must update documentation.** If you change behavior, update the corresponding doc in `docs/`. See the quick reference table in `docs/agents.md`.
3. **Evidence grounding is non-negotiable.** Every non-question review comment must have valid evidence. Do not bypass the `EvidenceValidator`.
4. **Read before writing.** Do not modify code you haven't read. Do not modify tests you don't understand.
5. **Scope discipline.** Do only what is asked. No drive-by refactors, no bonus features, no extra docstrings on unchanged code.

## Quick Commands

```bash
# Run tests
pytest tests/ -v

# Run tests with coverage
pytest tests/ --cov=codesteward --cov-report=term-missing

# Lint
ruff check codesteward/

# Type check
mypy codesteward/
```

## Key Docs

- `docs/agents.md` - Full agent rules and conventions
- `docs/architecture.md` - System design and component interactions
- `docs/data-model.md` - Database schema and Pydantic models
- `docs/configuration.md` - Config options reference
- `docs/evidence-grounding.md` - Evidence system specification
- `backlog/meta.md` - Feature backlog and workflow
