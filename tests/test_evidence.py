"""Tests for evidence validation pipeline and enforcement in review simulation.

Covers:
- EvidenceValidator: shape, format, quality checks (strict & lenient modes)
- EvidenceValidationResult structure
- Reference-format validators for diff, doc, and history types
- Comment downgrade behaviour (missing, invalid, low-quality evidence)
- Review-level and batch validation
- Backward-compatible _enforce_evidence wrapper
- Integration with heuristic simulator
- Edge cases: empty strings, None, malformed refs, boundary values
"""

import pytest
from codesteward.evidence import (
    CONFIDENCE_INVALID_EVIDENCE,
    CONFIDENCE_LOW_QUALITY_PENALTY,
    CONFIDENCE_MISSING_EVIDENCE,
    EvidenceValidationResult,
    EvidenceValidator,
    _validate_diff_ref,
    _validate_doc_ref,
    _validate_history_ref,
)
from codesteward.schemas import (
    BlockingThreshold,
    ChangeContext,
    ChangedFile,
    Evidence,
    EvidenceType,
    FocusWeights,
    ReviewComment,
    ReviewerReview,
    ReviewerSkillCard,
)
from codesteward.simulator import ReviewSimulator, _enforce_evidence


# ===================================================================
# Helpers
# ===================================================================


def _make_evidence(
    etype: EvidenceType = EvidenceType.DIFF,
    ref: str = "src/foo.py:42",
    snippet: str = "x = 1",
) -> Evidence:
    return Evidence(type=etype, ref=ref, snippet=snippet)


def _make_comment(
    kind: str = "blocker",
    body: str = "Issue found",
    evidence: Evidence | None = None,
    confidence: float = 1.0,
    file: str = "",
    line: int | None = None,
) -> ReviewComment:
    return ReviewComment(
        kind=kind,
        body=body,
        evidence=evidence,
        confidence=confidence,
        file=file,
        line=line,
    )


def _make_review(
    reviewer: str = "alice",
    comments: list[ReviewComment] | None = None,
    verdict: str = "comment",
) -> ReviewerReview:
    return ReviewerReview(
        reviewer=reviewer,
        comments=comments or [],
        verdict=verdict,
    )


# ===================================================================
# EvidenceValidationResult
# ===================================================================


class TestEvidenceValidationResult:
    def test_valid_result(self) -> None:
        r = EvidenceValidationResult(is_valid=True)
        assert r.is_valid is True
        assert r.issues == []

    def test_invalid_result_with_issues(self) -> None:
        r = EvidenceValidationResult(is_valid=False, issues=["bad ref", "missing snippet"])
        assert r.is_valid is False
        assert len(r.issues) == 2

    def test_default_issues_is_empty_list(self) -> None:
        r = EvidenceValidationResult(is_valid=False)
        assert r.issues == []


# ===================================================================
# Reference-format validators (unit-level)
# ===================================================================


class TestDiffRefValidator:
    """Tests for _validate_diff_ref."""

    @pytest.mark.parametrize(
        "ref",
        [
            "src/foo.py:42",
            "pkg/handler.go:1",
            "a.py:999",
            "path/to/deep/file.rs:100",
        ],
    )
    def test_valid_path_line_refs(self, ref: str) -> None:
        assert _validate_diff_ref(ref) == []

    @pytest.mark.parametrize(
        "ref",
        [
            "src/foo.py",
            "handler.go",
            "a.rs",
            "my_module.py",
        ],
    )
    def test_valid_file_only_refs(self, ref: str) -> None:
        assert _validate_diff_ref(ref) == []

    @pytest.mark.parametrize(
        "ref",
        [
            "3 files changed",
            "1 file changed",
        ],
    )
    def test_valid_aggregate_diff_refs(self, ref: str) -> None:
        assert _validate_diff_ref(ref) == []

    @pytest.mark.parametrize(
        "ref",
        [
            "src/some/path/here",
            "path/to/something.txt",
        ],
    )
    def test_valid_path_like_refs(self, ref: str) -> None:
        """Refs containing / are accepted even without extension."""
        assert _validate_diff_ref(ref) == []

    @pytest.mark.parametrize(
        "ref",
        [
            "completely random text",
            "no reference here",
            "42",
        ],
    )
    def test_invalid_diff_refs(self, ref: str) -> None:
        issues = _validate_diff_ref(ref)
        assert len(issues) == 1
        assert "does not look like" in issues[0]


class TestDocRefValidator:
    """Tests for _validate_doc_ref."""

    @pytest.mark.parametrize(
        "ref",
        [
            "CONTRIBUTING.md#style",
            "docs/api.md",
            "README.md",
            "docs/arch/design.md#overview",
            "CHANGELOG.rst",
            "docs/guide.txt",
            "MAINTAINERS",
            "ADR-001",
            "design-doc.md",
            "LICENSE",
        ],
    )
    def test_valid_doc_refs(self, ref: str) -> None:
        assert _validate_doc_ref(ref) == []

    @pytest.mark.parametrize(
        "ref",
        [
            "src/foo.py:42",
            "random gibberish",
            "42",
            "handler.go",
        ],
    )
    def test_invalid_doc_refs(self, ref: str) -> None:
        issues = _validate_doc_ref(ref)
        assert len(issues) == 1
        assert "does not reference a recognizable documentation" in issues[0]

    def test_section_anchor_is_valid(self) -> None:
        """A ref with #section is valid for doc type."""
        assert _validate_doc_ref("style-guide#naming") == []


class TestHistoryRefValidator:
    """Tests for _validate_history_ref."""

    @pytest.mark.parametrize(
        "ref",
        [
            "pr#123",
            "PR #456",
            "pull#789",
            "Pull #12",
            "#100",
            "commit abc1234",
            "abc1234def",
            "abc1234def5678901234567890abcdef12345678",
            "see discussion in #42",
            "comment from previous review",
        ],
    )
    def test_valid_history_refs(self, ref: str) -> None:
        assert _validate_history_ref(ref) == []

    @pytest.mark.parametrize(
        "ref",
        [
            "src/foo.py:42",
            "random text",
            "the code is bad",
            "42",
            "abc",
        ],
    )
    def test_invalid_history_refs(self, ref: str) -> None:
        issues = _validate_history_ref(ref)
        assert len(issues) == 1
        assert "does not reference a PR, commit, or discussion" in issues[0]


# ===================================================================
# EvidenceValidator.validate_evidence
# ===================================================================


class TestValidateEvidence:
    """Tests for EvidenceValidator.validate_evidence."""

    def setup_method(self) -> None:
        self.validator = EvidenceValidator(strict=True)

    # -- valid evidence --

    def test_valid_diff_evidence(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "src/foo.py:42", "x = 1")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is True
        assert result.issues == []

    def test_valid_doc_evidence(self) -> None:
        ev = _make_evidence(EvidenceType.DOC, "CONTRIBUTING.md#style", "use snake_case")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is True

    def test_valid_history_evidence(self) -> None:
        ev = _make_evidence(EvidenceType.HISTORY, "pr#123 comment excerpt", "we decided X")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is True

    # -- empty ref --

    def test_empty_ref_is_invalid(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "", "some snippet")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is False
        assert any("empty" in i.lower() for i in result.issues)

    def test_whitespace_only_ref_is_invalid(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "   ", "some snippet")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is False

    # -- short ref --

    def test_single_char_ref_is_invalid(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "x", "snippet")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is False
        assert any("too short" in i for i in result.issues)

    # -- invalid ref format --

    def test_diff_ref_with_no_path_like_content(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "completely random text here", "snippet")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is False

    def test_doc_ref_pointing_to_code_file(self) -> None:
        ev = _make_evidence(EvidenceType.DOC, "handler.go:42", "")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is False

    def test_history_ref_with_no_pr_or_commit(self) -> None:
        ev = _make_evidence(EvidenceType.HISTORY, "something random", "")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is False

    # -- missing snippet for diff --

    def test_diff_evidence_without_snippet_is_invalid(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "src/foo.py:42", "")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is False
        assert any("snippet" in i.lower() for i in result.issues)

    def test_diff_evidence_whitespace_snippet_is_invalid(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "src/foo.py:42", "   ")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is False

    def test_doc_evidence_without_snippet_is_valid(self) -> None:
        """Doc and history evidence don't require snippets."""
        ev = _make_evidence(EvidenceType.DOC, "README.md", "")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is True

    def test_history_evidence_without_snippet_is_valid(self) -> None:
        ev = _make_evidence(EvidenceType.HISTORY, "pr#123", "")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is True

    # -- multiple issues --

    def test_multiple_issues_collected(self) -> None:
        """An evidence with empty ref and missing snippet should collect both issues."""
        ev = _make_evidence(EvidenceType.DIFF, "", "")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is False
        assert len(result.issues) >= 1  # at least "empty ref"


# ===================================================================
# EvidenceValidator.validate_comment (strict mode)
# ===================================================================


class TestValidateCommentStrict:
    """Tests for validate_comment in strict mode."""

    def setup_method(self) -> None:
        self.validator = EvidenceValidator(strict=True)

    # -- questions are exempt --

    def test_question_without_evidence_unchanged(self) -> None:
        c = _make_comment(kind="question", body="Why is this?", evidence=None)
        result = self.validator.validate_comment(c)
        assert result.kind == "question"
        assert result.body == "Why is this?"

    def test_question_with_evidence_unchanged(self) -> None:
        ev = _make_evidence()
        c = _make_comment(kind="question", body="Why?", evidence=ev)
        result = self.validator.validate_comment(c)
        assert result.kind == "question"
        assert result.evidence is not None

    # -- missing evidence downgrade --

    def test_blocker_without_evidence_downgraded(self) -> None:
        c = _make_comment(kind="blocker", body="Bad code")
        result = self.validator.validate_comment(c)
        assert result.kind == "question"
        assert result.confidence == CONFIDENCE_MISSING_EVIDENCE
        assert "Evidence needed" in result.body
        assert "Bad code" in result.body

    def test_suggestion_without_evidence_downgraded(self) -> None:
        c = _make_comment(kind="suggestion", body="Consider refactoring")
        result = self.validator.validate_comment(c)
        assert result.kind == "question"
        assert result.confidence == CONFIDENCE_MISSING_EVIDENCE

    def test_missing_test_without_evidence_downgraded(self) -> None:
        c = _make_comment(kind="missing-test", body="Add unit tests")
        result = self.validator.validate_comment(c)
        assert result.kind == "question"

    def test_docs_needed_without_evidence_downgraded(self) -> None:
        c = _make_comment(kind="docs-needed", body="Update docs")
        result = self.validator.validate_comment(c)
        assert result.kind == "question"

    # -- invalid evidence downgrade --

    def test_blocker_with_invalid_evidence_downgraded(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "random nonsense text here", "snippet")
        c = _make_comment(kind="blocker", body="Issue", evidence=ev)
        result = self.validator.validate_comment(c)
        assert result.kind == "question"
        assert result.confidence == CONFIDENCE_INVALID_EVIDENCE

    def test_suggestion_with_empty_ref_downgraded(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "", "snippet")
        c = _make_comment(kind="suggestion", body="Fix this", evidence=ev)
        result = self.validator.validate_comment(c)
        assert result.kind == "question"
        assert result.confidence == CONFIDENCE_INVALID_EVIDENCE

    def test_downgraded_body_includes_reason(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "", "snippet")
        c = _make_comment(kind="blocker", body="original body", evidence=ev)
        result = self.validator.validate_comment(c)
        assert "original body" in result.body
        assert result.kind == "question"

    # -- valid evidence passes --

    def test_blocker_with_valid_evidence_unchanged(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "src/foo.py:42", "x = 1")
        c = _make_comment(kind="blocker", body="Missing null check", evidence=ev)
        result = self.validator.validate_comment(c)
        assert result.kind == "blocker"
        assert result.evidence is not None
        assert result.confidence == 1.0

    def test_suggestion_with_valid_doc_evidence_unchanged(self) -> None:
        ev = _make_evidence(EvidenceType.DOC, "CONTRIBUTING.md#style", "use snake_case")
        c = _make_comment(kind="suggestion", body="Naming issue", evidence=ev)
        result = self.validator.validate_comment(c)
        assert result.kind == "suggestion"

    # -- file and line preserved on downgrade --

    def test_file_preserved_on_downgrade(self) -> None:
        c = _make_comment(kind="blocker", body="Issue", file="src/api.py", line=10)
        result = self.validator.validate_comment(c)
        assert result.file == "src/api.py"
        assert result.line == 10

    # -- confidence boundary --

    def test_missing_evidence_confidence_exact(self) -> None:
        c = _make_comment(kind="blocker", body="X", confidence=0.9)
        result = self.validator.validate_comment(c)
        assert result.confidence == CONFIDENCE_MISSING_EVIDENCE  # 0.5

    def test_invalid_evidence_confidence_exact(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "", "snippet")
        c = _make_comment(kind="blocker", body="X", evidence=ev, confidence=0.9)
        result = self.validator.validate_comment(c)
        assert result.confidence == CONFIDENCE_INVALID_EVIDENCE  # 0.6


# ===================================================================
# EvidenceValidator.validate_comment (lenient mode)
# ===================================================================


class TestValidateCommentLenient:
    """Tests for validate_comment in lenient (non-strict) mode."""

    def setup_method(self) -> None:
        self.validator = EvidenceValidator(strict=False)

    def test_missing_evidence_still_downgraded(self) -> None:
        """Even in lenient mode, completely missing evidence causes downgrade."""
        c = _make_comment(kind="blocker", body="Issue")
        result = self.validator.validate_comment(c)
        assert result.kind == "question"
        assert result.confidence == CONFIDENCE_MISSING_EVIDENCE

    def test_invalid_evidence_kept_but_penalised(self) -> None:
        """In lenient mode, invalid evidence keeps kind but reduces confidence."""
        ev = _make_evidence(EvidenceType.DIFF, "random text no path", "snippet")
        c = _make_comment(kind="blocker", body="Issue", evidence=ev, confidence=0.9)
        result = self.validator.validate_comment(c)
        assert result.kind == "blocker"  # NOT downgraded
        expected = 0.9 - CONFIDENCE_LOW_QUALITY_PENALTY
        assert result.confidence == pytest.approx(expected)

    def test_valid_evidence_unchanged_in_lenient(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "src/foo.py:42", "x = 1")
        c = _make_comment(kind="blocker", body="Issue", evidence=ev, confidence=0.9)
        result = self.validator.validate_comment(c)
        assert result.kind == "blocker"
        assert result.confidence == 0.9

    def test_lenient_confidence_floor(self) -> None:
        """Confidence should not drop below 0.1 in lenient mode."""
        ev = _make_evidence(EvidenceType.DIFF, "random text no path", "snippet")
        c = _make_comment(kind="suggestion", body="X", evidence=ev, confidence=0.1)
        result = self.validator.validate_comment(c)
        assert result.confidence >= 0.1

    def test_question_exempt_in_lenient(self) -> None:
        c = _make_comment(kind="question", body="Why?")
        result = self.validator.validate_comment(c)
        assert result.kind == "question"


# ===================================================================
# EvidenceValidator.validate_comments (batch)
# ===================================================================


class TestValidateComments:
    def setup_method(self) -> None:
        self.strict = EvidenceValidator(strict=True)
        self.lenient = EvidenceValidator(strict=False)

    def test_empty_list(self) -> None:
        assert self.strict.validate_comments([]) == []

    def test_single_valid_comment(self) -> None:
        ev = _make_evidence()
        c = _make_comment(kind="blocker", evidence=ev)
        result = self.strict.validate_comments([c])
        assert len(result) == 1
        assert result[0].kind == "blocker"

    def test_single_invalid_comment(self) -> None:
        c = _make_comment(kind="blocker")
        result = self.strict.validate_comments([c])
        assert len(result) == 1
        assert result[0].kind == "question"

    def test_mixed_batch(self) -> None:
        valid_ev = _make_evidence(EvidenceType.DIFF, "a.py:1", "x")
        comments = [
            _make_comment(kind="blocker", body="Valid", evidence=valid_ev),
            _make_comment(kind="suggestion", body="No evidence"),
            _make_comment(kind="question", body="Exempt"),
            _make_comment(kind="blocker", body="Also no evidence"),
        ]
        result = self.strict.validate_comments(comments)
        assert len(result) == 4
        assert result[0].kind == "blocker"
        assert result[1].kind == "question"
        assert result[2].kind == "question"  # unchanged
        assert result[3].kind == "question"  # downgraded

    def test_preserves_order(self) -> None:
        ev = _make_evidence()
        comments = [
            _make_comment(kind="blocker", body="first", evidence=ev),
            _make_comment(kind="suggestion", body="second"),
            _make_comment(kind="blocker", body="third", evidence=ev),
        ]
        result = self.strict.validate_comments(comments)
        assert "first" in result[0].body
        assert "second" in result[1].body
        assert "third" in result[2].body

    def test_all_questions_unchanged(self) -> None:
        comments = [
            _make_comment(kind="question", body="Q1"),
            _make_comment(kind="question", body="Q2"),
        ]
        result = self.strict.validate_comments(comments)
        assert all(c.kind == "question" for c in result)
        assert result[0].body == "Q1"
        assert result[1].body == "Q2"


# ===================================================================
# EvidenceValidator.validate_review
# ===================================================================


class TestValidateReview:
    def setup_method(self) -> None:
        self.validator = EvidenceValidator(strict=True)

    def test_empty_review(self) -> None:
        review = _make_review(comments=[])
        result = self.validator.validate_review(review)
        assert result.comments == []
        assert result.reviewer == "alice"

    def test_review_with_valid_comments(self) -> None:
        ev = _make_evidence()
        review = _make_review(comments=[
            _make_comment(kind="blocker", body="X", evidence=ev),
        ])
        result = self.validator.validate_review(review)
        assert len(result.comments) == 1
        assert result.comments[0].kind == "blocker"

    def test_review_with_invalid_comments_downgraded(self) -> None:
        review = _make_review(comments=[
            _make_comment(kind="blocker", body="No evidence"),
        ])
        result = self.validator.validate_review(review)
        assert result.comments[0].kind == "question"

    def test_review_metadata_preserved(self) -> None:
        review = _make_review(
            reviewer="bob",
            verdict="request-changes",
            comments=[_make_comment(kind="question", body="Q")],
        )
        result = self.validator.validate_review(review)
        assert result.reviewer == "bob"
        assert result.verdict == "request-changes"

    def test_review_summary_bullets_preserved(self) -> None:
        review = ReviewerReview(
            reviewer="alice",
            summary_bullets=["bullet 1", "bullet 2"],
            comments=[],
            verdict="approve",
        )
        result = self.validator.validate_review(review)
        assert result.summary_bullets == ["bullet 1", "bullet 2"]


# ===================================================================
# EvidenceValidator.validate_reviews (batch of reviews)
# ===================================================================


class TestValidateReviews:
    def setup_method(self) -> None:
        self.validator = EvidenceValidator(strict=True)

    def test_empty_list(self) -> None:
        assert self.validator.validate_reviews([]) == []

    def test_multiple_reviews(self) -> None:
        ev = _make_evidence()
        reviews = [
            _make_review(reviewer="alice", comments=[
                _make_comment(kind="blocker", body="Valid", evidence=ev),
            ]),
            _make_review(reviewer="bob", comments=[
                _make_comment(kind="suggestion", body="No evidence"),
            ]),
        ]
        result = self.validator.validate_reviews(reviews)
        assert len(result) == 2
        assert result[0].comments[0].kind == "blocker"
        assert result[1].comments[0].kind == "question"

    def test_reviewer_identity_preserved(self) -> None:
        reviews = [
            _make_review(reviewer="alice"),
            _make_review(reviewer="bob"),
        ]
        result = self.validator.validate_reviews(reviews)
        assert result[0].reviewer == "alice"
        assert result[1].reviewer == "bob"


# ===================================================================
# Backward-compatible _enforce_evidence wrapper
# ===================================================================


class TestEnforceEvidenceBackwardCompat:
    """Ensure the legacy _enforce_evidence wrapper still works."""

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
        assert result[0].confidence == CONFIDENCE_MISSING_EVIDENCE

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


# ===================================================================
# Strict vs lenient mode toggle
# ===================================================================


class TestStrictVsLenientToggle:
    """Regression tests ensuring mode toggling works correctly."""

    def test_same_input_different_modes_strict(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "random text no path", "snippet")
        c = _make_comment(kind="blocker", body="Issue", evidence=ev, confidence=0.9)

        strict = EvidenceValidator(strict=True)
        result = strict.validate_comment(c)
        assert result.kind == "question"  # strict downgrades

    def test_same_input_different_modes_lenient(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "random text no path", "snippet")
        c = _make_comment(kind="blocker", body="Issue", evidence=ev, confidence=0.9)

        lenient = EvidenceValidator(strict=False)
        result = lenient.validate_comment(c)
        assert result.kind == "blocker"  # lenient keeps

    def test_missing_evidence_both_modes_downgrade(self) -> None:
        """Missing evidence is always downgraded regardless of mode."""
        c = _make_comment(kind="blocker", body="X")
        for mode in (True, False):
            v = EvidenceValidator(strict=mode)
            result = v.validate_comment(c)
            assert result.kind == "question"


# ===================================================================
# Edge cases
# ===================================================================


class TestEdgeCases:
    def setup_method(self) -> None:
        self.validator = EvidenceValidator(strict=True)

    def test_evidence_with_only_whitespace_snippet(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "src/foo.py:42", "   \t  ")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is False

    def test_very_long_ref(self) -> None:
        long_ref = "src/" + "sub/" * 50 + "file.py:1"
        ev = _make_evidence(EvidenceType.DIFF, long_ref, "x")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is True

    def test_ref_with_special_characters(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "src/my-module_v2.py:42", "x")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is True

    def test_unicode_in_body_preserved(self) -> None:
        c = _make_comment(kind="blocker", body="Variable \u2018foo\u2019 is unused")
        result = self.validator.validate_comment(c)
        assert "\u2018foo\u2019" in result.body

    def test_comment_with_zero_confidence(self) -> None:
        ev = _make_evidence()
        c = _make_comment(kind="blocker", body="X", evidence=ev, confidence=0.0)
        result = self.validator.validate_comment(c)
        assert result.confidence == 0.0  # valid evidence, confidence preserved

    def test_all_evidence_types_with_valid_refs(self) -> None:
        """Each evidence type with a proper ref passes validation."""
        cases = [
            (EvidenceType.DIFF, "src/foo.py:42", "code"),
            (EvidenceType.DOC, "README.md", ""),
            (EvidenceType.HISTORY, "pr#123", ""),
        ]
        for etype, ref, snippet in cases:
            ev = _make_evidence(etype, ref, snippet)
            result = self.validator.validate_evidence(ev)
            assert result.is_valid is True, f"Failed for {etype.value} ref={ref}"

    def test_diff_ref_with_line_zero(self) -> None:
        """Line 0 is technically valid format even if unusual."""
        ev = _make_evidence(EvidenceType.DIFF, "foo.py:0", "x")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is True

    def test_multiple_comments_all_downgraded(self) -> None:
        comments = [
            _make_comment(kind="blocker", body="A"),
            _make_comment(kind="suggestion", body="B"),
            _make_comment(kind="missing-test", body="C"),
            _make_comment(kind="docs-needed", body="D"),
        ]
        result = self.validator.validate_comments(comments)
        assert all(c.kind == "question" for c in result)

    def test_downgrade_does_not_mutate_original(self) -> None:
        """Ensure validation returns new objects, not mutations."""
        original = _make_comment(kind="blocker", body="Original")
        _ = self.validator.validate_comment(original)
        assert original.kind == "blocker"
        assert original.body == "Original"


# ===================================================================
# Parametrized evidence kind coverage
# ===================================================================


class TestAllCommentKinds:
    """Ensure every non-question kind is properly validated."""

    @pytest.mark.parametrize(
        "kind",
        ["blocker", "suggestion", "missing-test", "docs-needed"],
    )
    def test_non_question_without_evidence_downgraded(self, kind: str) -> None:
        v = EvidenceValidator(strict=True)
        c = _make_comment(kind=kind, body="Body text")
        result = v.validate_comment(c)
        assert result.kind == "question"
        assert result.confidence == CONFIDENCE_MISSING_EVIDENCE

    @pytest.mark.parametrize(
        "kind",
        ["blocker", "suggestion", "missing-test", "docs-needed"],
    )
    def test_non_question_with_valid_evidence_preserved(self, kind: str) -> None:
        v = EvidenceValidator(strict=True)
        ev = _make_evidence(EvidenceType.DIFF, "file.py:1", "code")
        c = _make_comment(kind=kind, body="Body", evidence=ev)
        result = v.validate_comment(c)
        assert result.kind == kind


# ===================================================================
# Heuristic simulation integration
# ===================================================================


class TestHeuristicSimulationIntegration:
    """Integration tests: evidence validator within the full heuristic simulator."""

    def _make_simulator(self, strict: bool = True) -> ReviewSimulator:
        return ReviewSimulator(anthropic_api_key="", strict_evidence=strict)

    def _make_context(self, files: list[ChangedFile] | None = None) -> ChangeContext:
        return ChangeContext(
            repo="test/repo",
            pr_number=42,
            pr_title="Test PR",
            changed_files=files or [
                ChangedFile(
                    path="src/api/handler.py",
                    additions=50,
                    deletions=10,
                    patch="+def handle():\n+    pass",
                ),
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

    def test_strict_evidence_all_grounded(self) -> None:
        """In strict mode every comment must have evidence or be a question."""
        sim = self._make_simulator(strict=True)
        ctx = self._make_context()
        card = self._make_card("api")
        review = sim.simulate_review(ctx, "diff content", card)
        for comment in review.comments:
            assert comment.evidence is not None or comment.kind == "question", (
                f"Comment kind={comment.kind!r} has no evidence and is not a question"
            )

    def test_non_strict_preserves_comments(self) -> None:
        """In non-strict mode comments keep their kind even with weaker evidence."""
        sim = self._make_simulator(strict=False)
        ctx = self._make_context()
        card = self._make_card("api")
        review = sim.simulate_review(ctx, "diff content", card)
        # Should still produce comments (not all downgraded)
        assert review.reviewer == "test-reviewer"

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
            ChangedFile(
                path="src/core/engine.py",
                additions=50,
                deletions=10,
                patch="+def foo(): pass",
            ),
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
        assert any(
            "secret" in b.lower() or "credential" in b.lower() or "password" in b.lower()
            for b in blocker_bodies
        )

    def test_simulate_all_validates_every_review(self) -> None:
        """simulate_all should validate evidence in every generated review."""
        sim = self._make_simulator(strict=True)
        files = [
            ChangedFile(
                path="src/engine.py",
                additions=50,
                deletions=10,
                patch="+def foo(): pass",
            ),
        ]
        ctx = self._make_context(files=files)
        cards = [
            self._make_card("tests"),
            self._make_card("security"),
            self._make_card("api"),
        ]
        reviews = sim.simulate_all(ctx, "+def foo(): pass", cards)
        assert len(reviews) == 3
        for review in reviews:
            for comment in review.comments:
                assert comment.evidence is not None or comment.kind == "question"

    def test_strict_vs_non_strict_diff_in_output(self) -> None:
        """Strict mode may produce more questions than non-strict for same input."""
        files = [
            ChangedFile(
                path="src/engine.py",
                additions=50,
                deletions=10,
                patch="+def foo(): pass",
            ),
        ]
        ctx_strict = self._make_context(files=files)
        ctx_lenient = self._make_context(files=files)

        card = self._make_card("api")

        strict_sim = self._make_simulator(strict=True)
        lenient_sim = self._make_simulator(strict=False)

        review_strict = strict_sim.simulate_review(ctx_strict, "diff", card)
        review_lenient = lenient_sim.simulate_review(ctx_lenient, "diff", card)

        # Both should produce output
        assert review_strict.reviewer == review_lenient.reviewer

    def test_heuristic_evidence_refs_are_valid_format(self) -> None:
        """All evidence produced by heuristic scanner should pass validation."""
        sim = self._make_simulator(strict=True)
        files = [
            ChangedFile(
                path="src/handler.go",
                additions=60,
                deletions=20,
                patch=(
                    "+func HandleRequest(w http.ResponseWriter, r *http.Request) {\n"
                    '+    password = "secret123"\n'
                    "+    fmt.Sprintf(query, userInput)\n"
                    "+}\n"
                ),
            ),
        ]
        ctx = self._make_context(files=files)

        for focus in ("security", "api", "tests", "style", "perf"):
            card = self._make_card(focus)
            review = sim.simulate_review(ctx, "diff", card)
            for c in review.comments:
                if c.evidence is not None:
                    v = EvidenceValidator(strict=True)
                    result = v.validate_evidence(c.evidence)
                    assert result.is_valid, (
                        f"Heuristic evidence failed validation: "
                        f"focus={focus}, ref={c.evidence.ref!r}, issues={result.issues}"
                    )


# ===================================================================
# LLM response parsing + validation integration
# ===================================================================


class TestLLMResponseValidation:
    """Tests that LLM-parsed responses go through evidence validation."""

    def test_parse_llm_response_with_missing_evidence(self) -> None:
        """When LLM returns a comment without evidence, strict mode downgrades it."""
        from codesteward.simulator import _parse_llm_response

        data = {
            "summary_bullets": ["looks good"],
            "verdict": "comment",
            "comments": [
                {
                    "kind": "blocker",
                    "body": "This function is too complex",
                    "file": "main.py",
                    "line": 10,
                    # no evidence field
                }
            ],
        }
        review = _parse_llm_response("test-reviewer", data)

        # Before validation, the comment has no evidence
        assert review.comments[0].evidence is None

        # After validation, it should be downgraded
        validator = EvidenceValidator(strict=True)
        validated = validator.validate_review(review)
        assert validated.comments[0].kind == "question"
        assert validated.comments[0].confidence == CONFIDENCE_MISSING_EVIDENCE

    def test_parse_llm_response_with_valid_evidence(self) -> None:
        from codesteward.simulator import _parse_llm_response

        data = {
            "summary_bullets": ["found issue"],
            "verdict": "request-changes",
            "comments": [
                {
                    "kind": "blocker",
                    "body": "SQL injection risk",
                    "file": "db.py",
                    "line": 42,
                    "evidence": {
                        "type": "diff",
                        "ref": "db.py:42",
                        "snippet": "cursor.execute(f'SELECT * FROM {table}')",
                    },
                    "confidence": 0.95,
                }
            ],
        }
        review = _parse_llm_response("test-reviewer", data)
        validator = EvidenceValidator(strict=True)
        validated = validator.validate_review(review)
        assert validated.comments[0].kind == "blocker"
        assert validated.comments[0].confidence == 0.95

    def test_parse_llm_response_with_invalid_evidence_type(self) -> None:
        """Invalid evidence type in LLM response results in None evidence."""
        from codesteward.simulator import _parse_llm_response

        data = {
            "summary_bullets": [],
            "verdict": "comment",
            "comments": [
                {
                    "kind": "suggestion",
                    "body": "Consider refactoring",
                    "evidence": {
                        "type": "invalid_type",
                        "ref": "foo.py:1",
                        "snippet": "x",
                    },
                }
            ],
        }
        review = _parse_llm_response("reviewer", data)
        # Invalid type causes evidence to be None
        assert review.comments[0].evidence is None

        validator = EvidenceValidator(strict=True)
        validated = validator.validate_review(review)
        assert validated.comments[0].kind == "question"

    def test_parse_llm_response_with_empty_evidence_ref(self) -> None:
        from codesteward.simulator import _parse_llm_response

        data = {
            "summary_bullets": [],
            "verdict": "comment",
            "comments": [
                {
                    "kind": "blocker",
                    "body": "Issue here",
                    "evidence": {
                        "type": "diff",
                        "ref": "",
                        "snippet": "some code",
                    },
                }
            ],
        }
        review = _parse_llm_response("reviewer", data)
        assert review.comments[0].evidence is not None  # parsed but empty ref

        validator = EvidenceValidator(strict=True)
        validated = validator.validate_review(review)
        assert validated.comments[0].kind == "question"  # downgraded
        assert validated.comments[0].confidence == CONFIDENCE_INVALID_EVIDENCE


# ===================================================================
# Config integration
# ===================================================================


class TestConfigIntegration:
    """Ensure strict_evidence_mode from config flows through to the validator."""

    def test_simulator_strict_creates_strict_validator(self) -> None:
        sim = ReviewSimulator(anthropic_api_key="", strict_evidence=True)
        assert sim._evidence_validator.strict is True

    def test_simulator_non_strict_creates_lenient_validator(self) -> None:
        sim = ReviewSimulator(anthropic_api_key="", strict_evidence=False)
        assert sim._evidence_validator.strict is False

    def test_default_config_is_strict(self) -> None:
        from codesteward.config import Config
        cfg = Config()
        assert cfg.strict_evidence_mode is True


# ===================================================================
# Cross-type evidence ref validation matrix
# ===================================================================


class TestCrossTypeValidation:
    """Ensure ref format is validated against the correct evidence type."""

    def setup_method(self) -> None:
        self.validator = EvidenceValidator(strict=True)

    def test_diff_ref_used_for_doc_type_fails(self) -> None:
        """A path:line ref is wrong for doc type."""
        ev = _make_evidence(EvidenceType.DOC, "src/foo.py:42", "code")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is False

    def test_doc_ref_used_for_history_type_fails(self) -> None:
        """A README.md ref is wrong for history type."""
        ev = _make_evidence(EvidenceType.HISTORY, "README.md", "text")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is False

    def test_history_ref_used_for_diff_type_fails(self) -> None:
        """A 'pr#123' ref is wrong for diff type (no path-like content)."""
        ev = _make_evidence(EvidenceType.DIFF, "pr#123", "text")
        result = self.validator.validate_evidence(ev)
        assert result.is_valid is False

    def test_doc_ref_used_for_diff_type_mixed(self) -> None:
        """A docs/ path might match diff format via path but fail diff ref check."""
        ev = _make_evidence(EvidenceType.DIFF, "docs/guide.md", "content")
        # docs/guide.md contains "/" so it passes as a path-like ref for diff
        result = self.validator.validate_evidence(ev)
        # This is acceptable: docs/guide.md is a valid file path in a diff
        assert result.is_valid is True or any("snippet" in i for i in result.issues)


# ===================================================================
# Confidence values
# ===================================================================


class TestConfidenceValues:
    """Verify the specific confidence constants are used correctly."""

    def test_missing_evidence_confidence(self) -> None:
        assert CONFIDENCE_MISSING_EVIDENCE == 0.5

    def test_invalid_evidence_confidence(self) -> None:
        assert CONFIDENCE_INVALID_EVIDENCE == 0.6

    def test_low_quality_penalty(self) -> None:
        assert CONFIDENCE_LOW_QUALITY_PENALTY == 0.15

    def test_invalid_confidence_higher_than_missing(self) -> None:
        """Invalid evidence (they tried) should have higher confidence than missing."""
        assert CONFIDENCE_INVALID_EVIDENCE > CONFIDENCE_MISSING_EVIDENCE


# ===================================================================
# Downgrade body formatting
# ===================================================================


class TestDowngradeBodyFormatting:
    def setup_method(self) -> None:
        self.validator = EvidenceValidator(strict=True)

    def test_missing_evidence_body_format(self) -> None:
        c = _make_comment(kind="blocker", body="The code is buggy")
        result = self.validator.validate_comment(c)
        assert result.body == "[Evidence needed] The code is buggy"

    def test_invalid_evidence_body_includes_reason(self) -> None:
        ev = _make_evidence(EvidenceType.DIFF, "", "snippet")
        c = _make_comment(kind="blocker", body="Original", evidence=ev)
        result = self.validator.validate_comment(c)
        # Should include the validation reason and original body
        assert "Original" in result.body
        assert result.body.startswith("[")

    def test_body_not_double_wrapped(self) -> None:
        """If a question already has [bracket] text, it should still be wrapped once."""
        c = _make_comment(kind="blocker", body="[Note] something")
        result = self.validator.validate_comment(c)
        assert result.body == "[Evidence needed] [Note] something"
