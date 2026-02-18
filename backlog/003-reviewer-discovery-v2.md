# 003 - Reviewer Discovery v2 (Routing + Graph)

## Status
Not Started

## Problem
Current reviewer selection is primarily ownership/path-history based and misses label routing plus reviewer-maintainer interaction signals.

## Vision Alignment
Matches the documented discovery signals and reviewer role categorization.

## High-Level Scope
- Add label-based routing signal from PR labels/areas.
- Add reviewer interaction graph signal.
- Improve path matching beyond exact filename equality.
- Add explicit performance reviewer category.

## Out of Scope
- Social/organizational fairness policy modeling.

## Deliverables
- New scoring model with weighted explainable components.
- Updated reviewer categories and schema evolution.
- Discovery diagnostics in output for traceability.

## Acceptance Criteria
- Discovery uses ownership + path history + labels + interaction graph.
- Results are stable and explainable with per-signal score breakdown.
- Category coverage includes performance/security/test/API/docs/general.

## Dependencies
- None

## Suggested Implementation Plan
- Extend DB queries and graph feature extraction.
- Implement weighted ranker and tune defaults.
- Add tests for ranking, diversity, and category assignment.

## Test Strategy
- Synthetic ranking tests.
- Regression tests with seeded fixtures.
