"""Tests for the maintainer aggregator."""

from __future__ import annotations

import pytest

from codesteward.aggregator import (
    MaintainerAggregator,
    _deduplicate,
    _find_disagreements,
)
from codesteward.schemas import (
    ChangeContext,
    ChangedFile,
    Evidence,
    EvidenceType,
    MergeVerdict,
    ReviewComment,
    ReviewerReview,
)


def _comment(kind: str = "blocker", body: str = "issue", file: str = "f.py") -> ReviewComment:
    return ReviewComment(
        kind=kind,
        body=body,
        file=file,
        evidence=Evidence(type=EvidenceType.DIFF, ref=f"{file}:1", snippet="x"),
    )


def _review(
    reviewer: str,
    verdict: str = "approve",
    comments: list[ReviewComment] | None = None,
) -> ReviewerReview:
    return ReviewerReview(
        reviewer=reviewer,
        verdict=verdict,
        comments=comments or [],
        summary_bullets=["bullet"],
    )


def _ctx(**kwargs) -> ChangeContext:
    defaults = {
        "repo": "test/repo",
        "changed_files": [ChangedFile(path="f.py", additions=10)],
        "risk_flags": [],
    }
    defaults.update(kwargs)
    return ChangeContext(**defaults)


class TestDeduplication:
    def test_identical_removed(self) -> None:
        c1 = _comment(body="missing null check on input")
        c2 = _comment(body="missing null check on input")
        result = _deduplicate([c1, c2])
        assert len(result) == 1

    def test_similar_removed(self) -> None:
        c1 = _comment(body="missing null check on user input parameter")
        c2 = _comment(body="missing null check on input parameter for user")
        result = _deduplicate([c1, c2])
        # Jaccard > 0.5, so should dedup
        assert len(result) == 1

    def test_different_kept(self) -> None:
        c1 = _comment(body="missing null check on input")
        c2 = _comment(body="add performance benchmark for new endpoint")
        result = _deduplicate([c1, c2])
        assert len(result) == 2

    def test_empty_list(self) -> None:
        assert _deduplicate([]) == []


class TestDisagreements:
    def test_verdict_split_detected(self) -> None:
        reviews = [
            _review("alice", "approve"),
            _review("bob", "request-changes"),
        ]
        disagreements = _find_disagreements(reviews)
        assert len(disagreements) >= 1
        assert disagreements[0]["type"] == "verdict-split"
        assert "alice" in disagreements[0]["approvers"]
        assert "bob" in disagreements[0]["rejecters"]

    def test_no_disagreement_on_consensus(self) -> None:
        reviews = [
            _review("alice", "approve"),
            _review("bob", "approve"),
        ]
        disagreements = _find_disagreements(reviews)
        verdict_splits = [d for d in disagreements if d["type"] == "verdict-split"]
        assert len(verdict_splits) == 0

    def test_file_contention_detected(self) -> None:
        reviews = [
            _review("alice", "approve", [_comment("blocker", "issue A", "handler.py")]),
            _review("bob", "comment", [_comment("suggestion", "issue B", "handler.py")]),
        ]
        disagreements = _find_disagreements(reviews)
        contention = [d for d in disagreements if d["type"] == "file-contention"]
        assert len(contention) == 1
        assert contention[0]["file"] == "handler.py"


class TestVerdict:
    def test_all_approve_no_blockers_is_ready(self) -> None:
        agg = MaintainerAggregator()
        reviews = [_review("alice", "approve"), _review("bob", "approve")]
        summary = agg.aggregate(_ctx(), reviews)
        assert summary.verdict == MergeVerdict.READY

    def test_multiple_rejections_needs_changes(self) -> None:
        agg = MaintainerAggregator()
        reviews = [
            _review("alice", "request-changes", [_comment()]),
            _review("bob", "request-changes", [_comment(body="other issue")]),
        ]
        summary = agg.aggregate(_ctx(), reviews)
        assert summary.verdict == MergeVerdict.NEEDS_CHANGES

    def test_many_blockers_needs_changes(self) -> None:
        agg = MaintainerAggregator()
        comments = [_comment(body=f"blocker {i}") for i in range(4)]
        reviews = [_review("alice", "comment", comments)]
        summary = agg.aggregate(_ctx(), reviews)
        assert summary.verdict == MergeVerdict.NEEDS_CHANGES

    def test_security_risk_with_rejection_needs_changes(self) -> None:
        agg = MaintainerAggregator()
        reviews = [_review("alice", "request-changes", [_comment()])]
        summary = agg.aggregate(_ctx(risk_flags=["security"]), reviews)
        assert summary.verdict == MergeVerdict.NEEDS_CHANGES

    def test_security_risk_without_rejection_is_risky(self) -> None:
        agg = MaintainerAggregator()
        reviews = [_review("alice", "comment")]
        summary = agg.aggregate(_ctx(risk_flags=["security"]), reviews)
        assert summary.verdict == MergeVerdict.RISKY

    def test_single_rejection_is_risky(self) -> None:
        agg = MaintainerAggregator()
        reviews = [
            _review("alice", "approve"),
            _review("bob", "request-changes", [_comment()]),
        ]
        summary = agg.aggregate(_ctx(), reviews)
        assert summary.verdict == MergeVerdict.RISKY


class TestFixPlan:
    def test_blockers_are_p0(self) -> None:
        agg = MaintainerAggregator()
        reviews = [_review("alice", "comment", [_comment("blocker", "fix the bug")])]
        summary = agg.aggregate(_ctx(), reviews)
        p0_items = [p for p in summary.fix_plan if p.startswith("[P0]")]
        assert len(p0_items) >= 1
        assert "fix the bug" in p0_items[0]

    def test_missing_tests_are_p1(self) -> None:
        agg = MaintainerAggregator()
        reviews = [_review("alice", "comment", [_comment("missing-test", "needs test for handler")])]
        summary = agg.aggregate(_ctx(), reviews)
        p1_items = [p for p in summary.fix_plan if p.startswith("[P1]")]
        assert len(p1_items) >= 1

    def test_plan_capped_at_15(self) -> None:
        agg = MaintainerAggregator()
        comments = [_comment("suggestion", f"suggestion {i}") for i in range(20)]
        reviews = [_review("alice", "comment", comments)]
        summary = agg.aggregate(_ctx(), reviews)
        assert len(summary.fix_plan) <= 15
