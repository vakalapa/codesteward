"""Tests for evidence enforcement in review simulation."""

import pytest
from codesteward.schemas import (
    ChangeContext,
    ChangedFile,
    Evidence,
    EvidenceType,
    FocusWeights,
    ReviewComment,
    ReviewerSkillCard,
    BlockingThreshold,
)
from codesteward.simulator import ReviewSimulator, _enforce_evidence


class TestEvidenceEnforcement:
    def test_comment_with_evidence_passes(self) -> None:
        comments = [
            ReviewComment(
                kind="blocker",
                body="Missing null check",
                file="src/api.py",
                line=42,
                evidence=Evidence(type=EvidenceType.DIFF, ref="src/api.py:42", snippet="if x:"),
            )
        ]
        result = _enforce_evidence(comments)
        assert len(result) == 1
        assert result[0].kind == "blocker"
        assert result[0].evidence is not None

    def test_comment_without_evidence_becomes_question(self) -> None:
        comments = [
            ReviewComment(
                kind="blocker",
                body="This looks wrong",
                file="src/api.py",
                evidence=None,
            )
        ]
        result = _enforce_evidence(comments)
        assert len(result) == 1
        assert result[0].kind == "question"
        assert "[Evidence needed]" in result[0].body
        assert result[0].confidence == 0.5

    def test_mixed_evidence_comments(self) -> None:
        comments = [
            ReviewComment(
                kind="blocker",
                body="Real issue",
                evidence=Evidence(type=EvidenceType.DIFF, ref="a.py:1", snippet="x"),
            ),
            ReviewComment(
                kind="suggestion",
                body="Vague suggestion",
                evidence=None,
            ),
            ReviewComment(
                kind="blocker",
                body="Another real issue",
                evidence=Evidence(type=EvidenceType.DOC, ref="CONTRIBUTING.md#style", snippet="y"),
            ),
        ]
        result = _enforce_evidence(comments)
        assert len(result) == 3
        assert result[0].kind == "blocker"
        assert result[1].kind == "question"
        assert result[2].kind == "blocker"

    def test_empty_comments_list(self) -> None:
        result = _enforce_evidence([])
        assert result == []


class TestHeuristicSimulation:
    def _make_simulator(self, strict: bool = True) -> ReviewSimulator:
        return ReviewSimulator(anthropic_api_key="", strict_evidence=strict)

    def _make_context(self, files: list[ChangedFile] | None = None) -> ChangeContext:
        return ChangeContext(
            repo="test/repo",
            pr_number=42,
            pr_title="Test PR",
            changed_files=files or [
                ChangedFile(path="src/api/handler.py", additions=50, deletions=10, patch="+def handle():\n+    pass"),
            ],
            areas=["sig-api"],
            risk_flags=["api-surface"],
        )

    def _make_card(self, focus: str = "api") -> ReviewerSkillCard:
        weights = FocusWeights()
        setattr(weights, focus, 0.8)
        return ReviewerSkillCard(
            reviewer="test-reviewer",
            focus_weights=weights,
            blocking_threshold=BlockingThreshold.MEDIUM,
            common_blockers=["missing tests"],
            total_reviews=50,
            approval_rate=0.7,
        )

    def test_heuristic_review_produces_output(self) -> None:
        sim = self._make_simulator()
        ctx = self._make_context()
        card = self._make_card("api")
        review = sim.simulate_review(ctx, "diff content", card)

        assert review.reviewer == "test-reviewer"
        assert len(review.summary_bullets) > 0
        assert review.verdict in ("approve", "request-changes", "comment")

    def test_strict_evidence_mode(self) -> None:
        sim = self._make_simulator(strict=True)
        ctx = self._make_context()
        card = self._make_card("api")
        review = sim.simulate_review(ctx, "diff content", card)

        # All comments should have evidence or be questions
        for comment in review.comments:
            assert comment.evidence is not None or comment.kind == "question"

    def test_large_diff_flagged(self) -> None:
        files = [
            ChangedFile(path="src/big.py", additions=400, deletions=200, patch="+big change"),
        ]
        sim = self._make_simulator()
        ctx = self._make_context(files=files)
        card = self._make_card("style")
        review = sim.simulate_review(ctx, "diff content", card)

        bodies = [c.body for c in review.comments]
        assert any("large" in b.lower() or "split" in b.lower() for b in bodies)

    def test_test_focused_reviewer_flags_missing_tests(self) -> None:
        files = [
            ChangedFile(path="src/core/engine.py", additions=50, deletions=10, patch="+def foo(): pass"),
        ]
        sim = self._make_simulator()
        ctx = self._make_context(files=files)
        card = self._make_card("tests")
        review = sim.simulate_review(ctx, "diff content", card)

        kinds = [c.kind for c in review.comments]
        assert "missing-test" in kinds

    def test_security_scanner_detects_hardcoded_secret(self) -> None:
        files = [
            ChangedFile(
                path="src/config.py",
                additions=5,
                deletions=0,
                patch='+password = "hunter2"\n+api_key = "sk-12345"',
            ),
        ]
        sim = self._make_simulator()
        ctx = self._make_context(files=files)
        card = self._make_card("security")
        review = sim.simulate_review(ctx, '+password = "hunter2"', card)

        blocker_bodies = [c.body for c in review.comments if c.kind == "blocker"]
        assert any("secret" in b.lower() or "credential" in b.lower() or "password" in b.lower() for b in blocker_bodies)
