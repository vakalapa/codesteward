# 005 - Reviewer Profiler v2 (Recency & Trends)

## Status
Not Started

## Problem
Recency behavior is currently coarse; personas need continuous time weighting and topic trend fidelity.

## Vision Alignment
Delivers continuously updated skill cards with recency-weighted interests.

## High-Level Scope
- Introduce explicit time-decay weighting in profile metrics.
- Track topic trend trajectories (rising/stable/falling).
- Improve blocker/style/evidence preference extraction robustness.

## Out of Scope
- Deep NLP model training for reviewer behavior.

## Deliverables
- Updated profiler computations and card fields.
- Backward-compatible card serialization strategy.
- Re-profiling strategy for existing databases.

## Acceptance Criteria
- Recent behavior has stronger influence than old behavior.
- Trend signals are visible and testable in skill cards.

## Dependencies
- None

## Suggested Implementation Plan
- Define decay function and apply across metric pipelines.
- Add trend summaries by topic and blocker classes.
- Add migration path for new card fields.

## Test Strategy
- Time-windowed fixtures validating decay behavior.
- Deterministic tests for trend extraction.
