# 007 - Evaluation Replay Framework

## Status
Not Started

## Problem
Evaluation strategy is documented but there is no dedicated replay/metrics workflow to measure progress.

## Vision Alignment
Implements metrics-driven stewardship quality validation.

## High-Level Scope
- Add replay command/workflow over historical PRs.
- Compute key metrics:
  - blocker precision/recall
  - topic overlap
  - actionability rate
  - merge outcome alignment
- Emit machine-readable and human-readable evaluation reports.

## Out of Scope
- Full benchmark leaderboard infrastructure.

## Deliverables
- `eval` CLI command and report artifacts.
- Metric definitions and reproducible computation pipeline.
- Baseline benchmark profiles.

## Acceptance Criteria
- Users can run replay on a target dataset/repo slice.
- Metrics are stable, explainable, and comparable across versions.

## Dependencies
- 001, 002
- 006 is a soft dependency: build replay against the current aggregator first, then upgrade when 006 lands.

## Suggested Implementation Plan
- Define metric formulas and target schema.
- Build replay executor and scoring engine.
- Integrate with CI as optional quality gate.

## Test Strategy
- Deterministic fixture datasets with expected metric values.
- Smoke tests for end-to-end replay command.
