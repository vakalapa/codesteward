# 004 - Repo Mapping v2 (Maintainers + Design Docs)

## Status
Not Started

## Problem
Repo mapping currently parses a subset of stewardship files and has limited design-doc discovery.

## Vision Alignment
Implements broader context mapping with MAINTAINERS and design-document awareness.

## High-Level Scope
- Parse MAINTAINERS-like files in common formats.
- Expand OWNERS discovery beyond repo root where feasible.
- Discover and index design docs (ADR/KEP/RFC patterns).
- Improve risk dimensions and area tagging precision.

## Out of Scope
- Project-specific custom parser plugins for every OSS project.

## Deliverables
- Extended mapping parsers and heuristics.
- Document index with references usable by retrieval layer.
- Updated ChangeContext relevance fields.

## Acceptance Criteria
- Mapping includes maintainers/design docs where present.
- Risk and area signals improve on representative fixtures.

## Dependencies
- None

**Downstream consumer:** 002 (Evidence Retrieval Layer) will use the design-doc index produced here. Coordinate output format if both are in flight.

## Suggested Implementation Plan
- Add parser adapters for common maintainer file conventions.
- Add configurable search paths for design docs.
- Update tests and migration notes if schema changes are needed.

## Test Strategy
- Parser unit tests with fixture files.
- Context-building integration tests.
