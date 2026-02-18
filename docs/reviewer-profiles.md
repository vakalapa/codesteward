# Reviewer Profiles

CodeSteward builds behavioral profiles called **Reviewer Skill Cards** from historical review data. Each card captures how a specific reviewer thinks, what they prioritize, and how they give feedback.

This document explains the profiling algorithm, skill card structure, and how profiles influence review simulation.

## Skill Card Structure

A `ReviewerSkillCard` contains:

```json
{
  "reviewer": "alice",
  "focus_weights": {
    "api": 0.85,
    "tests": 0.72,
    "perf": 0.45,
    "docs": 0.30,
    "security": 0.60,
    "style": 0.25,
    "backward_compat": 0.70
  },
  "blocking_threshold": "high",
  "common_blockers": [
    "missing error handling",
    "breaking API change without deprecation",
    "no test coverage for edge case"
  ],
  "style_preferences": [
    "explicit error handling",
    "clear naming conventions",
    "prefer simplicity"
  ],
  "evidence_preferences": [
    "unit test",
    "benchmark",
    "documentation"
  ],
  "recent_interests": [
    "api",
    "security"
  ],
  "quote_bank": [
    "this needs explicit error handling",
    "can we add a test for the edge case",
    "the API contract should be documented"
  ],
  "total_reviews": 342,
  "approval_rate": 0.72,
  "avg_comments_per_review": 3.4
}
```

## Profiling Algorithm

The profiler (`codesteward/profiler.py`) builds each card from the reviewer's historical comments and review states.

### 1. Focus Weights

Each of the 7 focus domains is scored by keyword frequency in the reviewer's comments:

| Domain | Keywords (sample) |
|--------|-------------------|
| `api` | api, endpoint, schema, protobuf, grpc, openapi, swagger, rest, http, handler, route, contract |
| `tests` | test, coverage, assert, mock, fixture, e2e, unit, integration, flaky, ci |
| `perf` | performance, latency, throughput, benchmark, allocation, memory, cpu, cache, optimize, scale |
| `docs` | documentation, readme, comment, godoc, changelog, release note, example |
| `security` | security, auth, cve, vulnerability, secret, token, tls, certificate, rbac, permission, privilege |
| `style` | naming, convention, lint, format, readability, idiomatic, consistent, refactor, clean |
| `backward_compat` | backward, compatible, deprecate, migration, breaking change, semver, upgrade |

**Scoring**: for each domain, count how many comments contain at least one keyword. Normalize to 0.0-1.0 by dividing by the max domain count across all domains. This makes weights relative to the reviewer's own distribution.

### 2. Blocking Threshold

Derived from the reviewer's changes-requested rate:

```
blocking_rate = changes_requested / total_reviews
```

| Rate | Threshold |
|------|-----------|
| > 40% | `HIGH` -- frequently blocks PRs |
| 15-40% | `MEDIUM` -- sometimes blocks |
| <= 15% | `LOW` -- rarely blocks |

### 3. Common Blockers

Extracted by matching the reviewer's comments against 17 regex patterns for recurring issues:

- Missing tests / test coverage
- Error handling concerns
- Race conditions
- Null/nil pointer risks
- Breaking changes / backward compatibility
- Security concerns
- Missing documentation
- Performance issues
- Code duplication
- Configuration / hardcoded values
- Naming / style issues
- Logging / observability gaps

The top 5 most frequent patterns become the reviewer's `common_blockers`.

### 4. Style Preferences

Detected via 8 heuristic patterns in comment text:

| Preference | Detection Signal |
|------------|-----------------|
| Explicit error handling | Keywords: "error handling", "check err", "handle error" |
| Clear naming | Keywords: "naming", "variable name", "rename", "descriptive" |
| DRY principle | Keywords: "dry", "duplicate", "duplicated", "repeated", "consolidate" |
| Simplicity | Keywords: "simplify", "simpler", "complex", "complicated", "overengineered" |
| Comments/docs | Keywords: "comment", "document", "explain", "godoc", "jsdoc" |
| Consistency | Keywords: "consistent", "inconsistent", "convention", "style" |
| Small functions | Keywords: "too long", "break up", "extract", "split", "decompose" |
| Explicit over implicit | Keywords: "explicit", "implicit", "magic", "hidden", "surprising" |

A preference is included if the reviewer mentions it in more than 2 comments.

### 5. Evidence Preferences

What kinds of evidence the reviewer asks for, detected from keywords:

| Preference | Keywords |
|------------|----------|
| Benchmark | benchmark, bench, perf test |
| E2E test | e2e, end-to-end, integration test |
| Unit test | unit test, test case, test coverage |
| Documentation | document, doc, readme |
| Release note | release note, changelog |
| Design doc | design doc, proposal, kep, adr |
| Migration guide | migration, upgrade guide |
| API spec | api spec, openapi, swagger |
| Example | example, sample, demo |

### 6. Recent Interests

Topic frequency from the reviewer's most recent 50 comments (approximately last 90 days). Uses the same keyword sets as focus weights but only on recent data. Surfaces emerging areas of interest that may not yet be reflected in historical weights.

### 7. Quote Bank

10 representative comment excerpts selected by:

- Length: 5-25 words (short enough to be a style signal, long enough to be meaningful).
- Deduplication: no two quotes with >60% word overlap.
- Optionally redacted: with `redact_quotes: true`, @mentions, PR references, URLs, and commit SHAs are replaced with placeholders.

These quotes help the LLM simulator capture the reviewer's voice and tone.

### 8. Statistical Summary

- **total_reviews**: count of all reviews submitted by this reviewer.
- **approval_rate**: fraction of reviews with state `APPROVED`.
- **avg_comments_per_review**: average number of line-level comments per review.

## How Profiles Influence Simulation

### LLM Path

The reviewer's skill card is encoded directly into the Claude system prompt:

```
You are simulating a code review by {reviewer}.

Focus areas (0-1 weights):
  api: 0.85, tests: 0.72, perf: 0.45, ...

Blocking tendency: HIGH (blocks >40% of PRs)
Common issues they flag: missing error handling, breaking API changes, ...
Style preferences: explicit error handling, clear naming, ...
Evidence they expect: unit test, benchmark, documentation
Recent interests: api, security
Stats: 342 reviews, 72% approval rate, 3.4 comments/review avg

Representative quotes:
- "this needs explicit error handling"
- "can we add a test for the edge case"
...
```

This gives the LLM a concrete persona to simulate rather than generating generic feedback.

### Heuristic Path

The heuristic engine uses focus weights to constrain which scanners run:

1. The reviewer's top 2 focus areas are identified (e.g., `api` and `tests`).
2. Only scanners relevant to those areas are executed.
3. The blocking threshold influences the verdict:
   - HIGH blockers: `request-changes` if there are 2+ missing tests or 3+ total comments.
   - LOW/MEDIUM blockers: only `request-changes` if explicit blockers are found.

This ensures even the fallback engine produces persona-specific feedback rather than running all checks indiscriminately.

## Default Skill Cards

When a reviewer is discovered but has no profiled skill card in the database (because `profile` hasn't been run, or they weren't in the top-N), a default card is created from their `ReviewerCategory`:

| Category | Default Focus Boost |
|----------|-------------------|
| `TEST_CI_HAWK` | `tests: 0.9` |
| `API_STABILITY_HAWK` | `api: 0.9, backward_compat: 0.7` |
| `SECURITY_HAWK` | `security: 0.9` |
| `DOCS_HAWK` | `docs: 0.9` |
| `GENERAL` | All weights at moderate baseline (0.3-0.4) |

## Storage

Skill cards are serialized as JSON and stored in the `reviewer_cards` database table. They are keyed by `(repo, reviewer)` and updated via upsert on each `profile` run.

The `updated_at` timestamp tracks when the card was last refreshed. Discovery gives a 50% score boost to reviewers with cached cards, since they produce higher-quality simulations.

## Refreshing Profiles

Profiles should be refreshed periodically as reviewer behavior evolves:

```bash
# Re-ingest recent data
codesteward ingest --repo cilium/cilium --resume

# Re-profile (overwrites existing cards)
codesteward profile --repo cilium/cilium --top-reviewers 50
```

The `--resume` flag on ingest only fetches new PRs since the last run, making incremental updates fast.
