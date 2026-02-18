# CodeSteward Vision Backlog (Meta)

This folder tracks high-level features required to fully realize the project vision described in `docs/README.md`.

Design principles for this backlog:
- Each feature is independently executable by one agent session.
- Every feature has a numbered Markdown file.
- Files should evolve from high-level intent to implementable spec.
- Cross-feature dependencies are explicit to enable parallel work.

## Feature Index

| ID | Feature | Why it matters for vision | Spec file | Priority | Depends on |
|---|---|---|---|---|---|
| 001 | Evidence Validation Pipeline | Enforces non-negotiable "no claim without evidence" rule across all simulation modes | `001-evidence-validation-pipeline.md` | P0 | - |
| 002 | Evidence Retrieval Layer (Diff/Docs/History) | Improves grounding quality and supports doc/history citations beyond heuristics | `002-evidence-retrieval-layer.md` | P1 | 001, 004 (optional) |
| 003 | Reviewer Discovery v2 (Routing + Graph) | Aligns reviewer selection with ownership, labels, and interaction behavior from vision | `003-reviewer-discovery-v2.md` | P1 | - |
| 004 | Repo Mapping v2 (Maintainers + Design Docs) | Expands mapping from basic ownership to stewardship context (MAINTAINERS/ADRs/KEPs) | `004-repo-mapping-v2.md` | P1 | - |
| 005 | Reviewer Profiler v2 (Recency & Trends) | Produces more realistic personas with explicit time decay and evolving interests | `005-reviewer-profiler-v2.md` | P2 | - |
| 006 | Aggregator Intelligence v2 | Better merge-readiness logic, conflict handling, and fix-plan quality | `006-aggregator-intelligence-v2.md` | P2 | 001,002,003,005 |
| 007 | Evaluation Replay Framework | Measures blocker precision/recall, overlap, actionability, and verdict alignment | `007-evaluation-replay-framework.md` | P1 | 001,002 |
| 008 | Agent Loop Interface | Enables closed-loop generator→steward→iteration workflow from the docs vision | `008-agent-loop-interface.md` | P3 | 001,002,006,007 |
| 009 | Operability & Governance | Improves reliability, observability, and safe rollout for practical OSS use | `009-operability-and-governance.md` | P2 | - |
| 010 | Bot/CVE Basic PR Filtering | Reduces low-signal automated PR noise by skipping routine bot/CVE updates when configured | `010-bot-cve-pr-filtering.md` | P1 | - |

## File Convention

Recommended naming: `NNN-short-feature-name.md`

Numbering policy for this backlog:
- IDs can be renumbered when priorities change.
- `meta.md` is the source of truth for current ordering.
- When renumbering, update file names and index links in one pass.

Each feature file should include:
1. Problem statement
2. Vision alignment
3. Scope (in/out)
4. Deliverables
5. Acceptance criteria
6. Dependencies
7. Suggested implementation plan
8. Test strategy

## Session Workflow

1. Pick one feature file.
2. Refine the file into an implementation spec for that session.
3. Implement only what is in scope.
4. Update status and leave handoff notes.

## Status Legend

- `Not Started`
- `In Progress`
- `Blocked`
- `Done`
