# 006 - Aggregator Intelligence v2

## Status
Not Started

## Problem
Aggregation logic is useful but still heuristic-heavy; conflict synthesis and merge-readiness confidence can be stronger.

## Vision Alignment
Strengthens maintainer-level decision support and prioritized fix planning.

## High-Level Scope
- Improve deduplication semantics beyond simple word overlap.
- Enhance disagreement detection and explanation.
- Add confidence-aware verdicting and risk escalation rules.
- Produce sharper, dependency-aware fix plans.

## Out of Scope
- Fully autonomous merge decisions.

## Deliverables
- Enhanced aggregation model and policy rules.
- Structured disagreement objects with evidence grouping.
- Improved fix-plan prioritization.

## Acceptance Criteria
- Lower duplicate noise in merged feedback.
- More actionable and stable verdict outcomes.
- Fix plans align with blocker criticality and dependency ordering.

## Dependencies
- 001, 002, 003, 005

## Suggested Implementation Plan
- Introduce semantic clustering for comments.
- Refine verdict policy with calibrated thresholds.
- Validate using replay framework once available.

## Test Strategy
- Unit tests for dedup, disagreements, verdict policy.
- Replay-based regression checks.
