"""Tests for database operations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codesteward.db import Database, _pattern_matches


@pytest.fixture
def db(tmp_path: Path) -> Database:
    """Create a fresh database in a temp directory."""
    db_path = tmp_path / "test.sqlite"
    database = Database(str(db_path))
    database.init_schema()
    yield database
    database.close()


class TestSchema:
    def test_init_creates_tables(self, db: Database) -> None:
        tables = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {r["name"] for r in tables}
        assert "prs" in table_names
        assert "pr_files" in table_names
        assert "reviews" in table_names
        assert "review_comments" in table_names
        assert "ownership" in table_names
        assert "reviewer_cards" in table_names
        assert "meta" in table_names

    def test_schema_version(self, db: Database) -> None:
        row = db.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        assert row is not None
        assert int(row["value"]) >= 2

    def test_idempotent_init(self, db: Database) -> None:
        # Calling init_schema twice should be safe
        db.init_schema()
        row = db.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        assert row is not None


class TestBulkOperations:
    def test_bulk_commits_once(self, db: Database) -> None:
        with db.bulk():
            pr_id = db.upsert_pr("repo", 1, "Title", "author", "2024-01-01", None, "merged", [])
            db.insert_review(pr_id, "reviewer1", "APPROVED", "2024-01-01T12:00:00Z")
            db.insert_review(pr_id, "reviewer2", "COMMENTED", "2024-01-01T13:00:00Z")
        # Data should be committed
        row = db.conn.execute("SELECT COUNT(*) as n FROM reviews").fetchone()
        assert row["n"] == 2

    def test_bulk_rolls_back_on_error(self, db: Database) -> None:
        try:
            with db.bulk():
                db.upsert_pr("repo", 100, "Title", "author", "2024-01-01", None, "merged", [])
                raise ValueError("simulated error")
        except ValueError:
            pass
        # PR should NOT be committed
        row = db.conn.execute(
            "SELECT COUNT(*) as n FROM prs WHERE number=100"
        ).fetchone()
        assert row["n"] == 0

    def test_individual_commits_without_bulk(self, db: Database) -> None:
        pr_id = db.upsert_pr("repo", 1, "Title", "author", "2024-01-01", None, "merged", [])
        # Should be visible immediately
        row = db.conn.execute("SELECT COUNT(*) as n FROM prs WHERE number=1").fetchone()
        assert row["n"] == 1


class TestUpsertPr:
    def test_insert_and_fetch(self, db: Database) -> None:
        pr_id = db.upsert_pr("owner/repo", 42, "My PR", "alice", "2024-01-01", None, "closed", ["bug"])
        assert pr_id > 0
        fetched_id = db.get_pr_id("owner/repo", 42)
        assert fetched_id == pr_id

    def test_upsert_updates_existing(self, db: Database) -> None:
        db.upsert_pr("owner/repo", 42, "OLD title", "alice", "2024-01-01", None, "closed", [])
        db.upsert_pr("owner/repo", 42, "NEW title", "alice", "2024-01-01", "2024-01-02", "merged", ["fix"])
        row = db.conn.execute("SELECT title, state FROM prs WHERE number=42").fetchone()
        assert row["title"] == "NEW title"
        assert row["state"] == "merged"

    def test_different_repos_same_number(self, db: Database) -> None:
        id1 = db.upsert_pr("repo/a", 1, "A", "alice", "2024-01-01", None, "merged", [])
        id2 = db.upsert_pr("repo/b", 1, "B", "bob", "2024-01-01", None, "merged", [])
        assert id1 != id2


class TestReviewCommentDedup:
    def test_duplicate_comments_ignored(self, db: Database) -> None:
        pr_id = db.upsert_pr("repo", 1, "PR", "author", "2024-01-01", None, "merged", [])
        db.insert_review_comment(pr_id, "alice", "Good job", "file.py", 10, "2024-01-01T12:00:00Z")
        # Exact duplicate should be ignored (UNIQUE constraint)
        db.insert_review_comment(pr_id, "alice", "Good job", "file.py", 10, "2024-01-01T12:00:00Z")
        row = db.conn.execute("SELECT COUNT(*) as n FROM review_comments").fetchone()
        assert row["n"] == 1

    def test_different_timestamps_not_deduped(self, db: Database) -> None:
        pr_id = db.upsert_pr("repo", 1, "PR", "author", "2024-01-01", None, "merged", [])
        db.insert_review_comment(pr_id, "alice", "Comment 1", "file.py", 10, "2024-01-01T12:00:00Z")
        db.insert_review_comment(pr_id, "alice", "Comment 2", "file.py", 20, "2024-01-01T13:00:00Z")
        row = db.conn.execute("SELECT COUNT(*) as n FROM review_comments").fetchone()
        assert row["n"] == 2


class TestIngestTracking:
    def test_get_set_last_ingest(self, db: Database) -> None:
        assert db.get_last_ingest("owner/repo") is None
        db.set_last_ingest("owner/repo", "2024-06-15T12:00:00Z")
        assert db.get_last_ingest("owner/repo") == "2024-06-15T12:00:00Z"

    def test_last_ingest_per_repo(self, db: Database) -> None:
        db.set_last_ingest("repo/a", "2024-01-01T00:00:00Z")
        db.set_last_ingest("repo/b", "2024-06-01T00:00:00Z")
        assert db.get_last_ingest("repo/a") == "2024-01-01T00:00:00Z"
        assert db.get_last_ingest("repo/b") == "2024-06-01T00:00:00Z"

    def test_last_ingest_updates(self, db: Database) -> None:
        db.set_last_ingest("repo", "2024-01-01T00:00:00Z")
        db.set_last_ingest("repo", "2024-06-01T00:00:00Z")
        assert db.get_last_ingest("repo") == "2024-06-01T00:00:00Z"


class TestPatternMatches:
    def test_exact_match(self) -> None:
        assert _pattern_matches("README.md", "README.md")

    def test_directory_prefix(self) -> None:
        assert _pattern_matches("/docs/", "docs/guide.md")
        assert _pattern_matches("docs/", "docs/guide.md")

    def test_wildcard(self) -> None:
        assert _pattern_matches("*.py", "setup.py")
        # In CODEOWNERS, *.py matches all .py files at any depth
        assert _pattern_matches("*.py", "src/setup.py")

    def test_globstar(self) -> None:
        assert _pattern_matches("src/**", "src/deep/nested/file.py")

    def test_no_match(self) -> None:
        assert not _pattern_matches("/api/", "docs/guide.md")

    def test_path_prefix_without_trailing_slash(self) -> None:
        assert _pattern_matches("src/api", "src/api/handler.py")

    def test_strip_leading_slash(self) -> None:
        assert _pattern_matches("/src/api/", "src/api/handler.py")


class TestReviewerQueries:
    def test_get_top_reviewers(self, db: Database) -> None:
        pr_id = db.upsert_pr("repo", 1, "PR", "author", "2024-01-01", None, "merged", [])
        db.insert_review(pr_id, "alice", "APPROVED", "2024-01-01T12:00:00Z")
        db.insert_review(pr_id, "alice", "COMMENTED", "2024-01-01T13:00:00Z")
        db.insert_review(pr_id, "bob", "APPROVED", "2024-01-01T14:00:00Z")

        top = db.get_top_reviewers("repo", limit=10)
        assert len(top) == 2
        assert top[0]["reviewer"] == "alice"
        assert top[0]["review_count"] == 2

    def test_get_reviewer_stats(self, db: Database) -> None:
        pr_id = db.upsert_pr("repo", 1, "PR", "author", "2024-01-01", None, "merged", [])
        db.insert_review(pr_id, "alice", "APPROVED", "2024-01-01T12:00:00Z")
        db.insert_review(pr_id, "alice", "CHANGES_REQUESTED", "2024-01-02T12:00:00Z")
        db.insert_review_comment(pr_id, "alice", "Fix this", "file.py", 10, "2024-01-01T12:00:00Z")

        stats = db.get_reviewer_stats("repo", "alice")
        assert stats["total_reviews"] == 2
        assert stats["approved"] == 1
        assert stats["changes_requested"] == 1
        assert stats["total_comments"] == 1

    def test_get_reviewers_for_paths(self, db: Database) -> None:
        pr_id = db.upsert_pr("repo", 1, "PR", "author", "2024-01-01", None, "merged", [])
        db.insert_pr_files(pr_id, [{"path": "src/handler.py", "additions": 10, "deletions": 5}])
        db.insert_review_comment(pr_id, "alice", "Comment", "src/handler.py", 10, "2024-01-01T12:00:00Z")

        reviewers = db.get_reviewers_for_paths("repo", ["src/handler.py"])
        assert len(reviewers) == 1
        assert reviewers[0]["reviewer"] == "alice"

    def test_get_reviewers_empty_paths(self, db: Database) -> None:
        reviewers = db.get_reviewers_for_paths("repo", [])
        assert reviewers == []
