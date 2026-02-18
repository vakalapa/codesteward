# CodeSteward

**CodeSteward** is a reviewer-pattern intelligence system for open-source projects.  
It learns how real maintainers and reviewers think, what they prioritize, and how they give feedback — then applies those learned reviewer personas to evaluate new code *before* humans review it.

CodeSteward is **not** an auto-merge bot.  
It is a **pre-human stewardship layer** designed to raise quality, reduce review churn, and align contributions with project norms.

---

## Documentation Index

| Document | Description |
|----------|-------------|
| [Architecture](architecture.md) | System architecture, component interactions, data flow |
| [Data Model](data-model.md) | Database schema, Pydantic models, enums |
| [Configuration](configuration.md) | YAML config, environment variables, CLI flags |
| [CLI Reference](cli-reference.md) | All commands with flags, examples, output format |
| [Evidence Grounding](evidence-grounding.md) | Evidence system, validation pipeline, strict/lenient modes |
| [Reviewer Profiles](reviewer-profiles.md) | Skill cards, profiling algorithm, focus weights |
| [Heuristic Engine](heuristic-engine.md) | Pattern-based fallback engine, scanners, verdict logic |
| [Development Guide](development.md) | Dev setup, testing, project structure, contributing |
| [Agent Rules](agents.md) | Rules for AI agents: test requirements, doc requirements, conventions |

---

## Table of Contents

- [Motivation](#motivation)
- [What CodeSteward Does](#what-codesteward-does)
- [What CodeSteward Does *Not* Do](#what-codesteward-does-not-do)
- [Core Concepts](#core-concepts)
- [System Architecture](#system-architecture)
- [Detailed Component Design](#detailed-component-design)
  - Repo Mapper
  - Reviewer Discovery
  - History Ingestor
  - Reviewer Profiler
  - Reviewer Simulator
  - Maintainer Aggregator
- [Reviewer Skill Cards](#reviewer-skill-cards)
- [End-to-End Flow](#end-to-end-flow)
- [CLI Workflow](#cli-workflow)
- [Integration with Code-Generation Agents](#integration-with-code-generation-agents)
- [Evidence & Grounding Rules](#evidence--grounding-rules)
- [Evaluation Strategy](#evaluation-strategy)
- [Data Model Overview](#data-model-overview)
- [Non-Goals](#non-goals)
- [Roadmap](#roadmap)

---

## Motivation

Open-source review quality depends on **context**, **history**, and **people**.

Most AI code reviewers fail because they:
- ignore project-specific norms
- miss historical design decisions
- sound confident without evidence
- optimize for style, not maintainability

Human reviewers don’t do that.  
They carry mental models shaped by years of review history.

**CodeSteward exists to model those mental models.**

---

## What CodeSteward Does

- Understands which **SIG / subproject / area** a change belongs to
- Identifies **who is likely to review** the change
- Learns **how each reviewer behaves** from historical reviews
- Simulates realistic, reviewer-specific feedback
- Produces a **maintainer-style merge readiness signal**

---

## What CodeSteward Does *Not* Do

- ❌ Auto-merge PRs
- ❌ Replace human reviewers
- ❌ Post comments directly to GitHub (by default)
- ❌ Make ungrounded claims

CodeSteward prepares code **for humans**, not instead of them.

---

## Core Concepts

| Concept | Meaning |
|------|--------|
| Stewardship | Protecting long-term project quality |
| Reviewer Persona | A learned model of how a specific reviewer thinks |
| Skill Card | Structured representation of reviewer behavior |
| Evidence Grounding | Every claim must be justified |
| Merge Readiness | Maintainer-level decision, not style feedback |

---

## System Architecture


```
            ┌────────────────────┐
            │   Code Generator   │
            │   (other agent)    │
            └─────────┬──────────┘
                      │ PR diff
                      ▼

┌──────────────────────────────────────────────────┐
│                  CodeSteward                     │
│                                                  │
│  ┌──────────────┐    ┌────────────────────────┐  │
│  │ Repo Mapper  │──▶│ Reviewer Discovery     │  │
│  └──────────────┘    └───────────┬────────────┘  │
│                                   │              │
│  ┌──────────────┐    ┌────────────▼────────────┐ │
│  │ History      │──▶│ Reviewer Profiler       │ │
│  │ Ingestor     │    │ (Skill Cards)           │ │
│  └──────────────┘    └────────────┬────────────┘ │
│                                   │              │
│                   ┌──────────────▼────────────┐  │
│                   │ Reviewer Simulator        │  │
│                   └──────────────┬────────────┘  │
│                                   │              │
│                   ┌──────────────▼────────────┐  │
│                   │ Maintainer Aggregator     │  │
│                   └───────────────────────────┘  │
└──────────────────────────────────────────────────┘

```

---

## Detailed Component Design

### Repo Mapper

**Purpose:** Determine *where* the change belongs.

Responsibilities:
- Parse `CODEOWNERS`, `OWNERS`, `MAINTAINERS`
- Map file paths to SIGs / subprojects
- Identify relevant design docs (KEPs, ADRs, CONTRIBUTING)
- Flag risk dimensions:
  - API surface changes
  - Backward compatibility
  - Performance / scale
  - Security
  - Platform-specific concerns

**Output:** `ChangeContext`

---

### Reviewer Discovery

**Purpose:** Determine *who* is likely to review.

Signals used:
1. Ownership files (highest confidence)
2. Historical reviewers for same paths
3. Reviewer-maintainer interaction graph
4. Label-based routing (e.g., `sig-network`)

Reviewers are categorized:
- Primary owners
- API guardians
- Test / CI hawks
- Performance reviewers
- Security reviewers

---

### History Ingestor

**Purpose:** Build ground truth from real reviews.

Ingested data:
- PR metadata
- Review states (approve / request changes)
- Inline review comments
- Review outcomes

Stored as normalized events in SQLite.

This data drives *all* learning — no synthetic assumptions.

---

### Reviewer Profiler

**Purpose:** Learn how each reviewer thinks.

Produces a **Reviewer Skill Card** capturing:
- Focus areas
- Strictness threshold
- Common blockers
- Style preferences
- Evidence expectations
- Recent interests

Skill cards are continuously updated with recency weighting.

---

### Reviewer Simulator

**Purpose:** Simulate realistic reviews.

For each reviewer persona:
1. Summarize changes
2. Identify risks through that reviewer’s lens
3. Generate:
   - Blockers
   - Non-blocking suggestions
   - Missing tests
   - Docs / rollout concerns
   - Questions (only if evidence is missing)

**Hard rule:**  
No claim without evidence.

---

### Maintainer Aggregator

**Purpose:** Decide *should this merge?*

Responsibilities:
- Merge and de-duplicate feedback
- Surface disagreements
- Highlight high-risk issues
- Produce merge verdict:
  - `READY`
  - `NEEDS_CHANGES`
  - `RISKY`

Also outputs a prioritized fix plan.

---

## Reviewer Skill Cards

Example:

```json
{
  "reviewer": "alice",
  "areas": ["sig-network"],
  "focus_weights": {
    "api": 0.9,
    "tests": 0.8,
    "perf": 0.6,
    "docs": 0.4,
    "security": 0.5
  },
  "blocking_threshold": "high",
  "common_blockers": [
    "API changes without design doc",
    "Missing e2e coverage"
  ],
  "style_preferences": [
    "explicit config",
    "clear error semantics"
  ],
  "evidence_preferences": [
    "benchmarks",
    "upgrade tests"
  ],
  "recent_interests": [
    "feature-gates",
    "dataplane refactor"
  ]
}
````

---

## End-to-End Flow

1. Code is generated or a PR is opened
2. CodeSteward maps the change
3. Relevant reviewers are discovered
4. Reviewer personas are applied
5. Maintainer summary is produced
6. Code is iterated until merge-ready

---

## CLI Workflow

```bash
codesteward init

codesteward ingest \
  --repo owner/name \
  --since 180d \
  --areas sig-network \
  --max-prs 300

codesteward profile \
  --repo owner/name \
  --top-reviewers 50

codesteward review \
  --repo owner/name \
  --pr 12345
```

Outputs:

* `out/review.md`
* `out/review.json`

---

## Integration with Code-Generation Agents

```
Code Generator
  └─ produces PR
      └─ CodeSteward review
           ├─ reviewer feedback
           ├─ maintainer verdict
           └─ fix plan
      └─ generator iterates
           └─ CodeSteward re-check
                └─ READY
```

This forms a **closed-loop quality system**.

---

## Evidence & Grounding Rules

Every comment must reference at least one:

* Diff line
* Repository document
* Prior PR discussion

If evidence is unavailable:

* Convert assertion into a question

This is non-negotiable.

---

## Evaluation Strategy

Replay historical PRs:

* Run CodeSteward *before* human reviews
* Compare against actual review outcomes

Metrics:

* Blocker precision / recall
* Topic overlap
* Actionability rate
* Merge outcome alignment

---

## Data Model Overview

Core tables:

* `prs`
* `pr_files`
* `reviews`
* `review_comments`
* `ownership`
* `reviewer_cards`

Designed for traceability and auditability.

---

## Non-Goals

* Full semantic correctness proofs
* Autonomous policy enforcement
* Style-only linting replacement
* Replacing maintainer judgment

---

## Roadmap

**Phase 1**

* Single repo
* Single SIG
* Heuristic topic extraction

**Phase 2**

* Recency-weighted interests
* Cross-SIG support

**Phase 3**

* Agent-to-agent negotiation
* Public OSS release
* CFP / research paper

---

## Closing Thought

CodeSteward doesn’t try to be clever.

It tries to be **respectful of how open-source actually works** —
people, history, and long-term responsibility.

That’s stewardship.
