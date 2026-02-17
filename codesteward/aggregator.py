"""Aggregate per-reviewer reviews into a maintainer summary with merge verdict."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from codesteward.schemas import (
    ChangeContext,
    MaintainerSummary,
    MergeVerdict,
    ReviewComment,
    ReviewerReview,
)

logger = logging.getLogger(__name__)

# Similarity threshold for deduplication (Jaccard on word sets)
DEDUP_THRESHOLD = 0.5


class MaintainerAggregator:
    """Merges all reviewer personas into a consolidated summary."""

    def aggregate(
        self,
        ctx: ChangeContext,
        reviews: list[ReviewerReview],
    ) -> MaintainerSummary:
        """Produce a MaintainerSummary from multiple reviewer reviews."""

        # Collect all comments
        all_blockers: list[ReviewComment] = []
        all_suggestions: list[ReviewComment] = []

        for review in reviews:
            for comment in review.comments:
                if comment.kind == "blocker":
                    all_blockers.append(comment)
                else:
                    all_suggestions.append(comment)

        # Deduplicate
        merged_blockers = _deduplicate(all_blockers)
        merged_suggestions = _deduplicate(all_suggestions)

        # Detect disagreements
        disagreements = _find_disagreements(reviews)

        # Compute verdict
        verdict = self._compute_verdict(reviews, merged_blockers, ctx)

        # Build fix plan
        fix_plan = self._build_fix_plan(merged_blockers, merged_suggestions, ctx)

        return MaintainerSummary(
            repo=ctx.repo,
            pr_number=ctx.pr_number,
            pr_title=ctx.pr_title,
            verdict=verdict,
            risk_flags=ctx.risk_flags,
            reviewer_reviews=reviews,
            merged_blockers=merged_blockers,
            merged_suggestions=merged_suggestions,
            disagreements=disagreements,
            fix_plan=fix_plan,
        )

    def _compute_verdict(
        self,
        reviews: list[ReviewerReview],
        blockers: list[ReviewComment],
        ctx: ChangeContext,
    ) -> MergeVerdict:
        """Determine merge readiness."""
        # Count verdicts
        approvals = sum(1 for r in reviews if r.verdict == "approve")
        rejections = sum(1 for r in reviews if r.verdict == "request-changes")

        # Strong signals
        if rejections >= 2 or len(blockers) >= 3:
            return MergeVerdict.NEEDS_CHANGES

        if rejections == 0 and len(blockers) == 0 and approvals == len(reviews):
            return MergeVerdict.READY

        # Risk-flag escalation
        high_risk_flags = {"security", "api-surface", "compat"}
        if high_risk_flags & set(ctx.risk_flags):
            if rejections > 0 or len(blockers) > 0:
                return MergeVerdict.NEEDS_CHANGES
            return MergeVerdict.RISKY

        if rejections > 0 or len(blockers) > 0:
            return MergeVerdict.RISKY

        return MergeVerdict.READY

    def _build_fix_plan(
        self,
        blockers: list[ReviewComment],
        suggestions: list[ReviewComment],
        ctx: ChangeContext,
    ) -> list[str]:
        """Build a prioritized list of actions for the PR author."""
        plan: list[str] = []

        # Priority 1: Blockers
        for i, b in enumerate(blockers, 1):
            file_ref = f" in `{b.file}`" if b.file else ""
            plan.append(f"[P0] {b.body}{file_ref}")

        # Priority 2: High-confidence suggestions
        for s in suggestions:
            if s.kind == "missing-test":
                file_ref = f" for `{s.file}`" if s.file else ""
                plan.append(f"[P1] Add tests{file_ref}: {s.body}")
            elif s.kind == "docs-needed":
                plan.append(f"[P1] {s.body}")

        # Priority 3: Other suggestions
        for s in suggestions:
            if s.kind not in ("missing-test", "docs-needed"):
                plan.append(f"[P2] {s.body}")

        return plan[:15]  # cap


def _deduplicate(comments: list[ReviewComment]) -> list[ReviewComment]:
    """Remove near-duplicate comments using word-set Jaccard similarity."""
    if not comments:
        return []

    unique: list[ReviewComment] = []
    seen_word_sets: list[set[str]] = []

    for comment in comments:
        words = set(comment.body.lower().split())
        is_dup = False
        for seen in seen_word_sets:
            if not words or not seen:
                continue
            jaccard = len(words & seen) / len(words | seen)
            if jaccard > DEDUP_THRESHOLD:
                is_dup = True
                break
        if not is_dup:
            unique.append(comment)
            seen_word_sets.append(words)

    return unique


def _find_disagreements(reviews: list[ReviewerReview]) -> list[dict[str, Any]]:
    """Detect cases where reviewers disagree (one approves, another requests changes)."""
    disagreements: list[dict[str, Any]] = []

    verdicts: dict[str, str] = {r.reviewer: r.verdict for r in reviews}

    approvers = [r for r, v in verdicts.items() if v == "approve"]
    rejecters = [r for r, v in verdicts.items() if v == "request-changes"]

    if approvers and rejecters:
        disagreements.append({
            "type": "verdict-split",
            "approvers": approvers,
            "rejecters": rejecters,
            "note": "Reviewers disagree on merge readiness.",
        })

    # Check for conflicting file-level comments
    file_comments: dict[str, list[tuple[str, ReviewComment]]] = defaultdict(list)
    for review in reviews:
        for comment in review.comments:
            if comment.file:
                file_comments[comment.file].append((review.reviewer, comment))

    for filepath, reviewer_comments in file_comments.items():
        kinds = {rc[1].kind for rc in reviewer_comments}
        if "blocker" in kinds and len(reviewer_comments) > 1:
            reviewers_involved = [rc[0] for rc in reviewer_comments]
            if len(set(reviewers_involved)) > 1:
                disagreements.append({
                    "type": "file-contention",
                    "file": filepath,
                    "reviewers": list(set(reviewers_involved)),
                    "note": f"Multiple reviewers have comments on `{filepath}`, including blockers.",
                })

    return disagreements
