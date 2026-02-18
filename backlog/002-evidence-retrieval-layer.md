# 002 - Evidence Retrieval Layer (Diff/Docs/History)

## Status
Not Started

## Problem
Evidence references are mostly local/heuristic; project vision expects robust diff/doc/history grounding.

## Vision Alignment
Enables high-quality citations from repository documents and historical discussions.

## High-Level Scope
- Build retrievers for:
  - Diff anchors (file:line)
  - Repo docs (e.g., CONTRIBUTING/ADRs/KEPs)
  - Historical PR review snippets
- Provide ranked evidence candidates per claim topic.
- Keep keyword fallback and optionally support embeddings.

## Out of Scope
- Full semantic reasoning over code correctness.

## Deliverables
- Retrieval interface and pluggable backends.
- Citation normalizer for consistent `Evidence.ref` formatting.
- Integration hooks for simulator and aggregator.

## Acceptance Criteria
- Simulator can attach doc/history evidence in addition to diff evidence.
- Retrieval failures degrade safely to questions when strict mode is on.
- Retrieval quality can be measured by evaluation framework (feature 007).

## Dependencies
- 001 (validation)
- 004 (optional — if repo mapping v2 lands first, consume its design-doc index; otherwise use basic file discovery)

## Suggested Implementation Plan
- Define retriever contracts.
- Implement baseline keyword retrieval first.
- Add optional embedding backend behind feature flag.

## Test Strategy
- Unit tests for each retriever.
- Integration tests verifying evidence type coverage.
