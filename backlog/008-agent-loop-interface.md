# 008 - Agent Loop Interface

## Status
Not Started

## Problem
Vision describes a closed-loop between code generation and stewardship, but interface contracts for this loop are not defined.

## Vision Alignment
Enables iterative generator→reviewer-agent collaboration until merge-readiness.

## High-Level Scope
- Define structured interface for input diff/context and output fix directives.
- Add deterministic machine-readable output mode for downstream agents.
- Add iteration metadata to support loop tracking.

## Out of Scope
- Building a full external orchestrator platform.

## Deliverables
- Stable JSON contract for loop integration.
- CLI/API mode focused on agent consumption.
- Documentation and examples for multi-agent workflows.

## Acceptance Criteria
- External agent can consume output and apply next-step fixes reliably.
- Multiple iteration cycles can be tracked with clear state and deltas.

## Dependencies
- 001, 002, 006, 007

## Suggested Implementation Plan
- Define schema and versioning strategy.
- Implement output mode + validation.
- Add integration example with mock generator.

## Test Strategy
- Contract tests for output schema.
- Loop simulation tests over 2-3 iterations.
