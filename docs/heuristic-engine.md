# Heuristic Engine

When no Anthropic API key is configured (or when the LLM call fails), CodeSteward falls back to a built-in heuristic review engine. This engine uses pattern-based static analysis constrained by each reviewer's persona to produce review feedback.

## How It Works

The heuristic engine is not a generic linter. It simulates reviewer behavior by:

1. **Selecting scanners** based on the reviewer's top 2 focus areas from their skill card.
2. **Running file-by-file analysis** with pattern matchers specific to each focus domain.
3. **Applying persona-based verdict logic** influenced by the reviewer's blocking threshold.
4. **Capping comment counts** per category to avoid overwhelming output.

This ensures different reviewer personas produce different feedback on the same diff.

## Scanner Selection

Given a reviewer's `FocusWeights`, the engine picks the top 2 domains by weight:

```
focus_weights: { api: 0.85, tests: 0.72, perf: 0.45, security: 0.60, ... }
→ top areas: ["api", "tests"]
→ active scanners: API scanner, Test scanner, (+ general checks)
```

Only scanners relevant to the selected focus areas run. This means a test-focused reviewer won't flag API stability issues, and a security reviewer won't flag style concerns.

## Scanners

### Test Scanner (`tests` focus)

**Missing test detection**: for each non-test, non-doc, non-config source file, checks whether a corresponding test file exists. A "corresponding test" is found by looking for files matching `test_*.py`, `*_test.go`, `*_test.js`, `*.spec.ts`, etc. in the changed file set.

- Generates `missing-test` comments for source files without corresponding tests.
- Capped at 3 missing-test comments per review.

**Test quality analysis**: for test files in the diff, scans patch content for:

| Pattern | Issue |
|---------|-------|
| `assert` keyword absent | "Test file lacks assertions" |
| `time.Sleep` / `time.sleep` | "Hardcoded sleep in test (may cause flakiness)" |

### Security Scanner (`security` focus)

Scans patch content (added lines) for security anti-patterns:

| Pattern | Comment |
|---------|---------|
| Hardcoded secrets (`password\s*=\s*["']`, `secret\s*=\s*["']`, `token\s*=\s*["']`) | "Possible hardcoded secret" |
| SQL injection (`fmt.Sprintf.*SELECT`, string concatenation in SQL) | "Possible SQL injection -- use parameterized queries" |
| `eval()` / `exec()` | "Use of eval/exec -- potential code injection" |
| `unsafe.Pointer` (Go) | "Use of unsafe.Pointer -- ensure memory safety" |
| TLS disabled (`InsecureSkipVerify`, `tls.Config{...}` without verification) | "TLS verification disabled" |

Each match generates a `blocker` comment with diff-type evidence pointing to the file and line.

### API Scanner (`api` focus)

Detects API surface changes in the diff:

| Pattern | Comment |
|---------|---------|
| New exported Go function (`^+func [A-Z]`) | `suggestion`: "New exported function -- ensure API documentation" |
| New exported Go type (`^+type [A-Z]`) | `suggestion`: "New exported type -- review API surface impact" |
| Removed exported symbol (`^-func [A-Z]`, `^-type [A-Z]`) | `blocker`: "Removed exported symbol -- potential breaking change" |

### Style Scanner (`style` focus)

| Pattern | Comment |
|---------|---------|
| `TODO` or `FIXME` in added lines | `suggestion`: "TODO/FIXME left in code" |
| Lines > 120 characters | `suggestion`: "Long line -- consider breaking up for readability" |

### Performance Scanner (`perf` focus)

| Pattern | Comment |
|---------|---------|
| N+1 query pattern (loop containing `query`, `select`, `find`, `fetch`) | `suggestion`: "Possible N+1 query pattern -- consider batching" |
| Allocation in loop (`make(`, `new(`, `append(` inside `for` block) | `suggestion`: "Allocation inside loop -- consider pre-allocating" |
| `sync.Mutex` / `sync.RWMutex` without corresponding unlock | `suggestion`: "Mutex usage -- verify unlock path" |

### Documentation Scanner (`docs` focus)

Checks whether significant code changes include documentation updates:

- If the diff modifies source files but no `.md`/`.rst`/`README` files are changed, generates a `docs-needed` comment.
- Only triggers on diffs with > 5 source files changed (to avoid noise on small changes).

### Backward Compatibility Scanner (`backward_compat` focus)

Cross-file analysis:

- Scans all changed files for removed exported symbols (functions, types, constants).
- Generates `blocker` comments for removed exports that may break downstream consumers.

### Code Quality Scanner (always runs)

General checks applied regardless of focus area:

| Pattern | Comment |
|---------|---------|
| `panic(` in non-test Go files | `blocker`: "panic() in production code -- use error returns" |
| Large file changes (> 200 lines added to a single file) | `suggestion`: "Large change to single file -- consider splitting" |
| Unchecked errors (Go: `err` assigned but next line isn't an `if err` check) | `suggestion`: "Unchecked error return" |

## Comment Caps

To keep reviews focused, the engine caps comments per category:

| Category | Max Comments |
|----------|-------------|
| Blockers | 5 |
| Missing tests | 3 |
| Suggestions | 4 |
| Docs needed | 2 |
| Questions | 2 |

Comments are prioritized by specificity (file+line > file-only > general).

## Verdict Logic

The heuristic engine determines the review verdict based on the reviewer's persona:

```
if blockers > 0:
    verdict = "request-changes"
elif blocking_threshold == HIGH and missing_tests >= 2:
    verdict = "request-changes"
elif blocking_threshold == HIGH and total_comments >= 3:
    verdict = "request-changes"
elif total_comments == 0:
    verdict = "approve"
else:
    verdict = "comment"
```

This means a HIGH-blocking reviewer will request changes on noisy diffs even without explicit blockers, while a LOW-blocking reviewer will only block on clear issues.

## Large Diff Handling

If the total diff exceeds 500 lines (configurable via `large_diff_threshold`):

- Style-focused or high-blocking reviewers generate a `suggestion` comment noting the diff size.
- The `LARGE_DIFF` risk flag is set in the `ChangeContext`.

## Evidence Generation

All heuristic comments include `diff`-type evidence:

- **File + line**: when a pattern match is found at a specific line in the patch.
- **File only**: when the concern applies to the whole file (e.g., missing test companion).

The `snippet` field contains the relevant patch line(s) that triggered the match.

## Summary Bullets

The heuristic engine generates summary bullets based on what was found:

```
summary_bullets = [
    "Found 2 potential security issues in pkg/auth/",
    "3 source files lack corresponding test files",
    "1 exported function removed (potential breaking change)"
]
```

## Comparison with LLM Path

| Aspect | Heuristic | LLM |
|--------|-----------|-----|
| Speed | Fast (no API call) | Slower (API round-trip) |
| Cost | Free | Per-token API cost |
| Depth | Pattern-based surface analysis | Semantic understanding of intent |
| Context | File-level patterns only | Full diff + PR description + persona |
| Evidence | Always present (from pattern match) | Must be validated (may need post-processing) |
| Persona fidelity | Focus area selection only | Full persona simulation (style, tone, priorities) |
| Language support | Go-heavy (some Python/JS patterns) | Language-agnostic |

The heuristic engine is useful for:

- Quick feedback without API costs.
- CI pipelines where latency matters.
- Repositories where LLM access is restricted.
- Development and testing of the review pipeline.

For production use on important reviews, the LLM path is recommended as it provides deeper semantic analysis and more natural reviewer voice.
