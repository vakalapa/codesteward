"""Data models for CodeSteward."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Change Context
# ---------------------------------------------------------------------------

class ChangedFile(BaseModel):
    path: str
    additions: int = 0
    deletions: int = 0
    patch: str = ""  # raw diff hunk for this file


class RiskFlag(str, enum.Enum):
    API_SURFACE = "api-surface"
    SECURITY = "security"
    PERF = "perf"
    COMPAT = "compat"
    WINDOWS = "windows"
    LARGE_DIFF = "large-diff"
    NEW_DEPENDENCY = "new-dependency"
    CONFIG_CHANGE = "config-change"
    TEST_ONLY = "test-only"
    DOCS_ONLY = "docs-only"


class ChangeContext(BaseModel):
    repo: str
    base_ref: str = "main"
    head_ref: str = ""
    pr_number: int | None = None
    pr_title: str = ""
    pr_body: str = ""
    changed_files: list[ChangedFile] = Field(default_factory=list)
    areas: list[str] = Field(default_factory=list)
    likely_reviewers: list[str] = Field(default_factory=list)
    relevant_docs: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Reviewer Discovery
# ---------------------------------------------------------------------------

class ReviewerCategory(str, enum.Enum):
    PRIMARY_OWNER = "primary-owner"
    TEST_CI_HAWK = "test-ci-hawk"
    API_STABILITY_HAWK = "api-stability-hawk"
    SECURITY_HAWK = "security-hawk"
    DOCS_HAWK = "docs-hawk"
    GENERAL = "general"


class ReviewerInfo(BaseModel):
    login: str
    score: float = 0.0
    categories: list[ReviewerCategory] = Field(default_factory=list)
    ownership_paths: list[str] = Field(default_factory=list)
    review_count: int = 0


# ---------------------------------------------------------------------------
# Reviewer Skill Card
# ---------------------------------------------------------------------------

class FocusWeights(BaseModel):
    api: float = 0.0
    tests: float = 0.0
    perf: float = 0.0
    docs: float = 0.0
    security: float = 0.0
    style: float = 0.0
    backward_compat: float = 0.0


class BlockingThreshold(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReviewerSkillCard(BaseModel):
    reviewer: str
    focus_weights: FocusWeights = Field(default_factory=FocusWeights)
    blocking_threshold: BlockingThreshold = BlockingThreshold.MEDIUM
    common_blockers: list[str] = Field(default_factory=list)
    style_preferences: list[str] = Field(default_factory=list)
    evidence_preferences: list[str] = Field(default_factory=list)
    recent_interests: list[str] = Field(default_factory=list)
    quote_bank: list[str] = Field(default_factory=list)
    total_reviews: int = 0
    approval_rate: float = 0.0
    avg_comments_per_review: float = 0.0


# ---------------------------------------------------------------------------
# Review Simulation Output
# ---------------------------------------------------------------------------

class EvidenceType(str, enum.Enum):
    DIFF = "diff"
    DOC = "doc"
    HISTORY = "history"


class Evidence(BaseModel):
    type: EvidenceType
    ref: str  # "path:line", "docs/file.md#section", "pr#123 comment excerpt"
    snippet: str = ""


class ReviewComment(BaseModel):
    kind: str  # "blocker", "suggestion", "missing-test", "docs-needed", "question"
    body: str
    file: str = ""
    line: int | None = None
    evidence: Evidence | None = None
    confidence: float = 1.0


class ReviewerReview(BaseModel):
    reviewer: str
    category: str = ""
    summary_bullets: list[str] = Field(default_factory=list)
    comments: list[ReviewComment] = Field(default_factory=list)
    verdict: str = ""  # "approve", "request-changes", "comment"


# ---------------------------------------------------------------------------
# Maintainer Summary
# ---------------------------------------------------------------------------

class MergeVerdict(str, enum.Enum):
    READY = "READY"
    NEEDS_CHANGES = "NEEDS_CHANGES"
    RISKY = "RISKY"


class MaintainerSummary(BaseModel):
    repo: str
    pr_number: int | None = None
    pr_title: str = ""
    verdict: MergeVerdict = MergeVerdict.NEEDS_CHANGES
    risk_flags: list[str] = Field(default_factory=list)
    reviewer_reviews: list[ReviewerReview] = Field(default_factory=list)
    merged_blockers: list[ReviewComment] = Field(default_factory=list)
    merged_suggestions: list[ReviewComment] = Field(default_factory=list)
    disagreements: list[dict[str, Any]] = Field(default_factory=list)
    fix_plan: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

class OwnershipEntry(BaseModel):
    path_pattern: str
    owners: list[str]
    source: str = "CODEOWNERS"  # or "OWNERS"
