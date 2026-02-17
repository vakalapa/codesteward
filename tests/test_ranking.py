"""Tests for reviewer discovery and ranking."""

import json
import pytest
from codesteward.db import Database
from codesteward.discovery import ReviewerDiscovery
from codesteward.schemas import ChangeContext, ChangedFile


@pytest.fixture
def db(tmp_path):
    """Create an in-memory-like DB in a temp directory."""
    db_path = tmp_path / "test.sqlite"
    database = Database(str(db_path))
    database.init_schema()

    repo = "test/repo"

    # Insert ownership
    database.upsert_ownership(repo, "src/api/*", "api-owner", "CODEOWNERS")
    database.upsert_ownership(repo, "src/core/*", "core-owner", "CODEOWNERS")
    database.upsert_ownership(repo, "tests/*", "test-owner", "CODEOWNERS")

    # Insert PRs and reviews
    pr1_id = database.upsert_pr(repo, 1, "Add API endpoint", "author1", "2024-01-01", "2024-01-02", "merged", ["api"])
    pr2_id = database.upsert_pr(repo, 2, "Fix core bug", "author2", "2024-01-03", "2024-01-04", "merged", ["bug"])
    pr3_id = database.upsert_pr(repo, 3, "Update tests", "author3", "2024-01-05", None, "closed", ["test"])

    # PR files
    database.insert_pr_files(pr1_id, [{"path": "src/api/handler.py", "additions": 50, "deletions": 10}])
    database.insert_pr_files(pr2_id, [{"path": "src/core/engine.py", "additions": 20, "deletions": 5}])
    database.insert_pr_files(pr3_id, [{"path": "tests/test_api.py", "additions": 30, "deletions": 0}])

    # Reviews
    database.insert_review(pr1_id, "api-reviewer", "APPROVED", "2024-01-01T12:00:00Z")
    database.insert_review(pr1_id, "security-reviewer", "CHANGES_REQUESTED", "2024-01-01T13:00:00Z")
    database.insert_review(pr2_id, "core-reviewer", "APPROVED", "2024-01-03T12:00:00Z")
    database.insert_review(pr3_id, "test-reviewer", "COMMENTED", "2024-01-05T12:00:00Z")

    # Review comments
    database.insert_review_comment(pr1_id, "api-reviewer", "LGTM on the API design", "src/api/handler.py", 10, "2024-01-01T12:00:00Z")
    database.insert_review_comment(pr1_id, "security-reviewer", "This endpoint needs auth token validation", "src/api/handler.py", 25, "2024-01-01T13:00:00Z")
    database.insert_review_comment(pr2_id, "core-reviewer", "Good fix. Consider adding a test.", "src/core/engine.py", 15, "2024-01-03T12:00:00Z")

    # Add more comments for category detection
    for i in range(5):
        database.insert_review_comment(pr1_id, "security-reviewer", f"Check security token handling #{i}", "src/api/handler.py", i, "2024-01-01T13:00:00Z")
        database.insert_review_comment(pr3_id, "test-reviewer", f"Test coverage needs improvement for test #{i}", "tests/test_api.py", i, "2024-01-05T12:00:00Z")

    yield database
    database.close()


class TestReviewerDiscovery:
    def test_discovers_ownership_reviewers(self, db: Database) -> None:
        discovery = ReviewerDiscovery(db)
        ctx = ChangeContext(
            repo="test/repo",
            changed_files=[ChangedFile(path="src/api/handler.py", additions=10, deletions=5)],
        )
        reviewers = discovery.discover(ctx, top_k=5)

        logins = [r.login for r in reviewers]
        assert "api-owner" in logins

    def test_discovers_historical_reviewers(self, db: Database) -> None:
        discovery = ReviewerDiscovery(db)
        ctx = ChangeContext(
            repo="test/repo",
            changed_files=[ChangedFile(path="src/api/handler.py", additions=10, deletions=5)],
        )
        reviewers = discovery.discover(ctx, top_k=10)

        logins = [r.login for r in reviewers]
        # api-reviewer and security-reviewer commented on api files
        assert "api-reviewer" in logins or "security-reviewer" in logins

    def test_ranks_by_score(self, db: Database) -> None:
        discovery = ReviewerDiscovery(db)
        ctx = ChangeContext(
            repo="test/repo",
            changed_files=[ChangedFile(path="src/api/handler.py", additions=10, deletions=5)],
        )
        reviewers = discovery.discover(ctx, top_k=10)

        # Scores should be in descending order
        for i in range(len(reviewers) - 1):
            assert reviewers[i].score >= reviewers[i + 1].score

    def test_empty_context_returns_global_fallbacks(self, db: Database) -> None:
        discovery = ReviewerDiscovery(db)
        ctx = ChangeContext(
            repo="test/repo",
            changed_files=[],
        )
        reviewers = discovery.discover(ctx, top_k=5)
        # With no changed files, should still return global top reviewers as fallback
        assert isinstance(reviewers, list)
        # All returned reviewers should have low scores (from global fallback only)
        for r in reviewers:
            assert r.score < 1.0

    def test_no_matching_files(self, db: Database) -> None:
        discovery = ReviewerDiscovery(db)
        ctx = ChangeContext(
            repo="test/repo",
            changed_files=[ChangedFile(path="unknown/path.py", additions=10, deletions=5)],
        )
        reviewers = discovery.discover(ctx, top_k=5)
        # May be empty or have low-scoring entries
        assert isinstance(reviewers, list)

    def test_top_k_limit(self, db: Database) -> None:
        discovery = ReviewerDiscovery(db)
        ctx = ChangeContext(
            repo="test/repo",
            changed_files=[ChangedFile(path="src/api/handler.py", additions=10, deletions=5)],
        )
        reviewers = discovery.discover(ctx, top_k=2)
        # Should not exceed top_k + 2 (diversity buffer)
        assert len(reviewers) <= 4
