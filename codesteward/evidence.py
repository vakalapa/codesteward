"""Evidence validation pipeline for CodeSteward.

Enforces the "no claim without evidence" rule across all simulation modes.
Every non-question review comment must have a valid, well-formed evidence
reference.  Comments that fail validation are deterministically downgraded
to questions with lowered confidence.

Two modes are supported:
- **strict** (default): evidence must be present AND pass shape/format/quality
  checks.  Invalid or missing evidence causes a downgrade.
- **lenient**: only completely missing evidence triggers a downgrade; malformed
  evidence is kept but confidence is reduced.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from codesteward.schemas import (
    Evidence,
    EvidenceType,
    ReviewComment,
    ReviewerReview,
)

logger = logging.getLogger(__name__)

# Kinds that are exempt from evidence requirements (they ARE the fallback).
_EVIDENCE_EXEMPT_KINDS: frozenset[str] = frozenset({"question"})

# Confidence values used when downgrading comments.
CONFIDENCE_MISSING_EVIDENCE = 0.5
CONFIDENCE_INVALID_EVIDENCE = 0.6
CONFIDENCE_LOW_QUALITY_PENALTY = 0.15  # subtracted from original

# Minimum meaningful ref length (e.g., "a.py" is 4 chars).
_MIN_REF_LENGTH = 2

# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------


@dataclass
class EvidenceValidationResult:
    """Result of validating a single Evidence object."""

    is_valid: bool
    issues: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reference-format validators (per evidence type)
# ---------------------------------------------------------------------------

# Diff refs should look like "path/file.ext:123" or at minimum a file path.
_DIFF_REF_WITH_LINE = re.compile(r"^.+:\d+$")
_DIFF_REF_FILE_ONLY = re.compile(r"^[^\s:]+\.[a-zA-Z0-9]+$|^\d+ files? changed$")

# Doc refs should reference a documentation file or section.
_DOC_REF_PATTERN = re.compile(
    r"(\.md|\.rst|\.txt|docs/|README|CONTRIBUTING|CHANGELOG|LICENSE|MAINTAINERS|ADR|design)"
    r"|#[a-zA-Z]",
    re.I,
)

# History refs should mention a PR number, commit hash, or discussion.
_HISTORY_REF_PATTERN = re.compile(
    r"(pr\s*#?\d+|pull\s*#?\d+|#\d+|commit\s+[0-9a-f]{7,}|[0-9a-f]{7,40}\b|discussion|comment)",
    re.I,
)


def _validate_diff_ref(ref: str) -> list[str]:
    """Return issues for a diff-type evidence reference."""
    issues: list[str] = []
    if not (_DIFF_REF_WITH_LINE.match(ref) or _DIFF_REF_FILE_ONLY.match(ref)):
        # Allow some freeform diff refs if they contain meaningful path-like content
        if "/" not in ref and "." not in ref and "file" not in ref.lower():
            issues.append(
                f"Diff ref '{ref}' does not look like a file path or path:line reference"
            )
    return issues


def _validate_doc_ref(ref: str) -> list[str]:
    """Return issues for a doc-type evidence reference."""
    issues: list[str] = []
    if not _DOC_REF_PATTERN.search(ref):
        issues.append(
            f"Doc ref '{ref}' does not reference a recognizable documentation file or section"
        )
    return issues


def _validate_history_ref(ref: str) -> list[str]:
    """Return issues for a history-type evidence reference."""
    issues: list[str] = []
    if not _HISTORY_REF_PATTERN.search(ref):
        issues.append(
            f"History ref '{ref}' does not reference a PR, commit, or discussion"
        )
    return issues


_REF_VALIDATORS = {
    EvidenceType.DIFF: _validate_diff_ref,
    EvidenceType.DOC: _validate_doc_ref,
    EvidenceType.HISTORY: _validate_history_ref,
}

# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------


class EvidenceValidator:
    """Validates and optionally downgrades review comments based on evidence quality.

    Parameters
    ----------
    strict:
        When ``True`` (default) both presence and quality of evidence are
        enforced.  When ``False`` only completely missing evidence causes
        a downgrade; malformed evidence is kept but confidence is reduced.
    """

    def __init__(self, strict: bool = True) -> None:
        self.strict = strict

    # -- single Evidence object ---------------------------------------------

    def validate_evidence(self, evidence: Evidence) -> EvidenceValidationResult:
        """Validate shape, reference format, and basic quality of *evidence*."""
        issues: list[str] = []

        # Shape: ref must be non-empty.
        if not evidence.ref or not evidence.ref.strip():
            issues.append("Evidence ref is empty")

        elif len(evidence.ref.strip()) < _MIN_REF_LENGTH:
            issues.append(
                f"Evidence ref '{evidence.ref}' is too short (min {_MIN_REF_LENGTH} chars)"
            )

        # Shape: type-specific reference format.
        if evidence.ref and evidence.ref.strip():
            validator = _REF_VALIDATORS.get(evidence.type)
            if validator:
                issues.extend(validator(evidence.ref.strip()))

        # Quality: snippet should be non-empty for diff evidence.
        if evidence.type == EvidenceType.DIFF and not evidence.snippet.strip():
            issues.append("Diff evidence should include a code snippet")

        return EvidenceValidationResult(is_valid=len(issues) == 0, issues=issues)

    # -- single ReviewComment -----------------------------------------------

    def validate_comment(self, comment: ReviewComment) -> ReviewComment:
        """Validate a comment's evidence and return a (possibly downgraded) copy.

        Downgrade rules:
        - Questions are exempt (returned unchanged).
        - Missing evidence → downgrade to question, confidence = 0.5.
        - Invalid evidence (strict mode) → downgrade to question, confidence = 0.6.
        - Low-quality evidence (strict mode) → keep kind, reduce confidence.
        """
        if comment.kind in _EVIDENCE_EXEMPT_KINDS:
            return comment

        # Missing evidence entirely.
        if comment.evidence is None:
            return self._downgrade_to_question(
                comment,
                reason="Evidence needed",
                confidence=CONFIDENCE_MISSING_EVIDENCE,
            )

        result = self.validate_evidence(comment.evidence)

        if not result.is_valid:
            if self.strict:
                # Strict: invalid evidence → downgrade.
                reason = "; ".join(result.issues)
                return self._downgrade_to_question(
                    comment,
                    reason=reason,
                    confidence=CONFIDENCE_INVALID_EVIDENCE,
                )
            else:
                # Lenient: keep the comment but penalise confidence.
                new_confidence = max(0.1, comment.confidence - CONFIDENCE_LOW_QUALITY_PENALTY)
                return comment.model_copy(update={"confidence": new_confidence})

        return comment

    # -- list of ReviewComments ---------------------------------------------

    def validate_comments(self, comments: list[ReviewComment]) -> list[ReviewComment]:
        """Validate and potentially transform every comment in *comments*."""
        return [self.validate_comment(c) for c in comments]

    # -- full ReviewerReview ------------------------------------------------

    def validate_review(self, review: ReviewerReview) -> ReviewerReview:
        """Validate all comments in a review and return a new ReviewerReview."""
        validated = self.validate_comments(review.comments)
        return review.model_copy(update={"comments": validated})

    # -- batch of reviews ---------------------------------------------------

    def validate_reviews(self, reviews: list[ReviewerReview]) -> list[ReviewerReview]:
        """Validate every review in a list."""
        return [self.validate_review(r) for r in reviews]

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _downgrade_to_question(
        comment: ReviewComment,
        *,
        reason: str,
        confidence: float,
    ) -> ReviewComment:
        """Return a copy of *comment* downgraded to a question."""
        return comment.model_copy(
            update={
                "kind": "question",
                "body": f"[{reason}] {comment.body}",
                "confidence": confidence,
            }
        )
