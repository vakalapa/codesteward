# 001 - Evidence Validation Pipeline

## Status
Not Started

## Problem
Evidence enforcement is inconsistent across generation paths; vision requires strict evidence grounding for every claim.

## Vision Alignment
Directly satisfies "No claim without evidence" and "convert unsupported assertions to questions".

## High-Level Scope
- Create a unified evidence validator applied to all simulator outputs.
- Validate evidence shape, reference format, and minimal quality.
- Convert invalid unsupported claims into questions with lower confidence.

## Out of Scope
- Retrieval ranking improvements (handled in feature 002).

## Deliverables
- Shared validation module.
- Integration in heuristic and LLM simulation paths.
- Configurable strict/non-strict modes.
- Tests covering valid, invalid, and missing evidence cases.

## Acceptance Criteria
- All review comments with non-question kinds have valid evidence references.
- Invalid evidence causes deterministic downgrade to question.
- Existing behavior remains backward-compatible in non-strict mode.

## Dependencies
- None

## Suggested Implementation Plan
- Define evidence contract and validator API.
- Integrate post-processing in simulator pipeline.
- Add unit tests and e2e assertions.

## Test Strategy
- Unit tests for evidence schema and normalization.
- Simulator tests for both heuristic and LLM paths.
- Regression tests for strict/non-strict toggles.
