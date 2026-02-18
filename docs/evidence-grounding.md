# Evidence Grounding

CodeSteward enforces a strict rule: **every claim must be backed by evidence**. If a concern cannot be grounded, it is automatically converted to a question rather than stated as fact.

This document explains the evidence model, validation pipeline, reference formats, and the strict vs. lenient modes.

## Why Evidence Grounding Matters

Most AI code reviewers fail because they sound confident without justification. They assert problems that don't exist, flag style issues that contradict project norms, or make claims with no traceable basis.

CodeSteward inverts this: **evidence is required, not optional**. The system is designed so that:

- Reviewers must cite where in the diff, docs, or history their concern originates.
- Comments that lack valid evidence are downgraded to questions (lower confidence, `"kind": "question"`).
- The output distinguishes between grounded assertions and open questions.

This makes the review report trustworthy -- every blocker can be traced back to a specific artifact.

## Evidence Types

Each `Evidence` object has a `type`, `ref`, and optional `snippet`:

| Type | What It References | Example `ref` | Example `snippet` |
|------|-------------------|---------------|-------------------|
| `diff` | A location in the PR diff | `src/datapath/handler.go:142` | `if err != nil { return }` |
| `doc` | A repository document | `docs/contributing.md#style-guide` | `"All exported functions must have doc comments"` |
| `history` | A prior PR or commit | `pr#789` | `"We decided to avoid hidden defaults in #789"` |

## Reference Format Validation

The evidence validator checks that each `ref` string matches the expected format for its type:

### `diff` References

Must look like a file path with optional line number:

- `src/foo.py:42` -- file and line
- `pkg/handler.go:100` -- file and line
- `10 files changed` -- summary reference (accepted but low quality)

Pattern: contains `:` with digits, or references file changes.

### `doc` References

Must reference a documentation file:

- `docs/api.md#endpoints` -- Markdown file with section anchor
- `CONTRIBUTING.md` -- top-level doc
- `README.rst` -- reStructuredText doc

Pattern: ends in `.md` or `.rst`, or matches known doc paths (`docs/`, `README`, `CONTRIBUTING`, `CHANGELOG`), or contains a `#section` anchor.

### `history` References

Must reference a prior PR, commit, or discussion:

- `pr#789` -- PR number reference
- `PR #123` -- alternate format
- `abc1234` -- short commit SHA
- `commit abcdef1234567890` -- explicit commit reference

Pattern: contains `pr#`, `PR #`, `commit`, or hex SHA-like strings.

## Validation Pipeline

The `EvidenceValidator` class (`codesteward/evidence.py`) applies validation at two levels:

### 1. Evidence Shape Validation

For each `Evidence` object:

1. **Presence check**: `ref` must be non-empty.
2. **Length check**: `ref` must be at least 2 characters.
3. **Format check**: `ref` must match the expected format for its `type`.
4. **Quality check**: for `diff` evidence, a non-empty `snippet` is recommended.

Returns an `EvidenceValidationResult` with `is_valid: bool` and `issues: list[str]`.

### 2. Comment-Level Validation

For each `ReviewComment`:

1. **Exempt check**: comments with `kind == "question"` bypass validation entirely (questions are the designed fallback).
2. **Missing evidence**: if a non-question comment has no `Evidence` object at all:
   - Downgraded to `kind: "question"` with `confidence: 0.5`.
   - Original body is prefixed with `"[Evidence missing] "`.
3. **Invalid evidence**: if the `Evidence` object fails shape validation:
   - **Strict mode**: downgraded to `kind: "question"` with `confidence: 0.6`.
   - **Lenient mode**: keeps original `kind`, but confidence is penalized by `-0.15`.

## Strict vs. Lenient Mode

Controlled by the `strict_evidence_mode` config setting (default: `true`).

### Strict Mode (`strict_evidence_mode: true`)

- Invalid evidence causes the comment to be downgraded to a question.
- The review report clearly separates grounded claims from open questions.
- This is the recommended mode for production use.
- Ensures the "no claim without evidence" invariant is maintained end-to-end.

### Lenient Mode (`strict_evidence_mode: false`)

- Invalid evidence is noted but the comment kind is preserved.
- A confidence penalty of 0.15 is applied instead of a downgrade.
- Useful during development or when testing with limited historical data.
- The system still validates and annotates issues, but doesn't alter comment categorization.

## Confidence Scores

Evidence validation affects comment confidence scores:

| Scenario | Confidence |
|----------|------------|
| Valid evidence present | Original (default `0.8`) |
| Missing evidence (downgraded to question) | `0.5` |
| Invalid evidence, strict mode (downgraded) | `0.6` |
| Invalid evidence, lenient mode (penalty) | Original - `0.15` |

Confidence scores flow through to the aggregator and affect fix plan prioritization.

## Integration Points

### Simulator Integration

The `ReviewSimulator` applies evidence validation after generating each review:

```
LLM/Heuristic output → ReviewerReview → EvidenceValidator → Validated ReviewerReview
```

In LLM mode, the system prompt instructs Claude to provide evidence for every comment. The validator catches any that slip through.

In heuristic mode, the pattern scanners attach diff-type evidence (file + line references) directly. Comments that the heuristic engine generates without a clear diff anchor are assigned evidence pointing to the file path.

### Aggregator Integration

The aggregator works with already-validated reviews. Deduplication and disagreement detection operate on the final comment kinds and confidence scores, so evidence validation shapes the aggregate output.

### Render Integration

The Markdown renderer formats evidence references inline:

```markdown
**Blocker** (confidence: 0.85)
Missing error handling for nil pointer dereference.
> Evidence: `src/handler.go:42` — `if err != nil { return }`

**Question** (confidence: 0.50)
[Evidence missing] Is this function thread-safe?
```

## Heuristic Engine Evidence

The heuristic fallback engine attaches `diff`-type evidence to all comments it generates:

- **File + line**: when a specific pattern match is found (e.g., security scanner finds `eval()` on line 57).
- **File only**: when the concern applies to the entire file (e.g., missing test file).
- **Summary**: when the concern is cross-file (e.g., large diff warning).

All heuristic comments include a `snippet` extracted from the patch content near the match.

## Design Decisions

1. **Questions as fallback, not failure**: converting ungrounded claims to questions preserves information while signaling uncertainty. The reviewer might have a valid concern -- they just can't prove it yet.

2. **Deterministic downgrade**: the validation is purely mechanical (regex-based format checks). There is no LLM in the validation loop. This makes the behavior predictable and testable.

3. **Separate from generation**: validation is a post-processing step, not embedded in prompt engineering. This ensures it works identically for both LLM and heuristic paths.

4. **Backward-compatible**: lenient mode preserves old behavior while still annotating issues. Strict mode can be adopted incrementally.
