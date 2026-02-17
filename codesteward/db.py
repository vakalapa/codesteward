"""SQLite database setup and query helpers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

SCHEMA_VERSION = 2

DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS prs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    repo       TEXT    NOT NULL,
    number     INTEGER NOT NULL,
    title      TEXT,
    author     TEXT,
    created_at TEXT,
    merged_at  TEXT,
    state      TEXT,
    labels_json TEXT,
    body       TEXT DEFAULT '',
    UNIQUE(repo, number)
);

CREATE TABLE IF NOT EXISTS pr_files (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id     INTEGER NOT NULL,
    path      TEXT    NOT NULL,
    additions INTEGER DEFAULT 0,
    deletions INTEGER DEFAULT 0,
    FOREIGN KEY (pr_id) REFERENCES prs(id)
);

CREATE TABLE IF NOT EXISTS reviews (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id        INTEGER NOT NULL,
    reviewer     TEXT    NOT NULL,
    state        TEXT,
    submitted_at TEXT,
    FOREIGN KEY (pr_id) REFERENCES prs(id)
);

CREATE TABLE IF NOT EXISTS review_comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id      INTEGER NOT NULL,
    reviewer   TEXT    NOT NULL,
    body       TEXT,
    path       TEXT,
    line       INTEGER,
    created_at TEXT,
    FOREIGN KEY (pr_id) REFERENCES prs(id),
    UNIQUE(pr_id, reviewer, path, created_at)
);

CREATE TABLE IF NOT EXISTS ownership (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    repo         TEXT NOT NULL,
    path_pattern TEXT NOT NULL,
    owner        TEXT NOT NULL,
    source       TEXT DEFAULT 'CODEOWNERS'
);

CREATE TABLE IF NOT EXISTS reviewer_cards (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    repo       TEXT NOT NULL,
    reviewer   TEXT NOT NULL,
    card_json  TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(repo, reviewer)
);

CREATE INDEX IF NOT EXISTS idx_prs_repo       ON prs(repo);
CREATE INDEX IF NOT EXISTS idx_prs_repo_num   ON prs(repo, number);
CREATE INDEX IF NOT EXISTS idx_pr_files_pr    ON pr_files(pr_id);
CREATE INDEX IF NOT EXISTS idx_pr_files_path  ON pr_files(path);
CREATE INDEX IF NOT EXISTS idx_reviews_pr     ON reviews(pr_id);
CREATE INDEX IF NOT EXISTS idx_reviews_rev    ON reviews(reviewer);
CREATE INDEX IF NOT EXISTS idx_rc_pr          ON review_comments(pr_id);
CREATE INDEX IF NOT EXISTS idx_rc_reviewer    ON review_comments(reviewer);
CREATE INDEX IF NOT EXISTS idx_rc_path        ON review_comments(path);
CREATE INDEX IF NOT EXISTS idx_rc_pr_rev      ON review_comments(pr_id, reviewer);
CREATE INDEX IF NOT EXISTS idx_reviews_compound ON reviews(pr_id, reviewer, state);
CREATE INDEX IF NOT EXISTS idx_ownership_repo ON ownership(repo);
"""

# Migration from schema v1 → v2
MIGRATION_V1_TO_V2 = """
-- Add UNIQUE constraint on review_comments to prevent duplicates on re-ingest.
-- SQLite doesn't support ALTER TABLE ADD CONSTRAINT, so we recreate the table.
CREATE TABLE IF NOT EXISTS review_comments_new (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id      INTEGER NOT NULL,
    reviewer   TEXT    NOT NULL,
    body       TEXT,
    path       TEXT,
    line       INTEGER,
    created_at TEXT,
    FOREIGN KEY (pr_id) REFERENCES prs(id),
    UNIQUE(pr_id, reviewer, path, created_at)
);
INSERT OR IGNORE INTO review_comments_new(id, pr_id, reviewer, body, path, line, created_at)
    SELECT id, pr_id, reviewer, body, path, line, created_at FROM review_comments;
DROP TABLE review_comments;
ALTER TABLE review_comments_new RENAME TO review_comments;
CREATE INDEX IF NOT EXISTS idx_rc_pr          ON review_comments(pr_id);
CREATE INDEX IF NOT EXISTS idx_rc_reviewer    ON review_comments(reviewer);
CREATE INDEX IF NOT EXISTS idx_rc_path        ON review_comments(path);
CREATE INDEX IF NOT EXISTS idx_rc_pr_rev      ON review_comments(pr_id, reviewer);
CREATE INDEX IF NOT EXISTS idx_reviews_compound ON reviews(pr_id, reviewer, state);
"""


class Database:
    """Thin wrapper around sqlite3 for CodeSteward."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def init_schema(self) -> None:
        """Create tables and indexes, running migrations if needed."""
        self.conn.executescript(DDL)
        # Check for migrations
        self._run_migrations()
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        self.conn.commit()

    def _run_migrations(self) -> None:
        """Apply pending schema migrations."""
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        current = int(row["value"]) if row else 1  # type: ignore[index]

        if current < 2:
            self.conn.executescript(MIGRATION_V1_TO_V2)
            self.conn.commit()

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    @contextmanager
    def bulk(self) -> Generator[None, None, None]:
        """Context manager for batching multiple writes in a single transaction.

        Usage:
            with db.bulk():
                db.upsert_pr(...)
                db.insert_review(...)
                # commit happens on exit
        """
        self._in_bulk = True
        try:
            yield
        except Exception:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()
        finally:
            self._in_bulk = False

    def _maybe_commit(self) -> None:
        """Commit unless inside a bulk() context."""
        if not getattr(self, "_in_bulk", False):
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------
    # PRs
    # ------------------------------------------------------------------

    def upsert_pr(
        self,
        repo: str,
        number: int,
        title: str,
        author: str,
        created_at: str,
        merged_at: str | None,
        state: str,
        labels: list[str],
        body: str = "",
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO prs(repo, number, title, author, created_at, merged_at, state, labels_json, body)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(repo, number) DO UPDATE SET
                 title=excluded.title, author=excluded.author,
                 created_at=excluded.created_at, merged_at=excluded.merged_at,
                 state=excluded.state, labels_json=excluded.labels_json,
                 body=excluded.body""",
            (repo, number, title, author, created_at, merged_at, state, json.dumps(labels), body),
        )
        self._maybe_commit()
        # Fetch the id
        row = self.conn.execute(
            "SELECT id FROM prs WHERE repo=? AND number=?", (repo, number)
        ).fetchone()
        return row["id"]  # type: ignore[index]

    def get_pr_id(self, repo: str, number: int) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM prs WHERE repo=? AND number=?", (repo, number)
        ).fetchone()
        return row["id"] if row else None  # type: ignore[index]

    # ------------------------------------------------------------------
    # PR Files
    # ------------------------------------------------------------------

    def insert_pr_files(self, pr_id: int, files: list[dict[str, Any]]) -> None:
        self.conn.executemany(
            "INSERT OR IGNORE INTO pr_files(pr_id, path, additions, deletions) VALUES (?, ?, ?, ?)",
            [(pr_id, f["path"], f.get("additions", 0), f.get("deletions", 0)) for f in files],
        )
        self._maybe_commit()

    # ------------------------------------------------------------------
    # Reviews
    # ------------------------------------------------------------------

    def insert_review(
        self, pr_id: int, reviewer: str, state: str, submitted_at: str
    ) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO reviews(pr_id, reviewer, state, submitted_at)
               VALUES (?, ?, ?, ?)""",
            (pr_id, reviewer, state, submitted_at),
        )
        self._maybe_commit()

    # ------------------------------------------------------------------
    # Review Comments
    # ------------------------------------------------------------------

    def insert_review_comment(
        self,
        pr_id: int,
        reviewer: str,
        body: str,
        path: str | None,
        line: int | None,
        created_at: str,
    ) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO review_comments(pr_id, reviewer, body, path, line, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (pr_id, reviewer, body, path, line, created_at),
        )
        self._maybe_commit()

    # ------------------------------------------------------------------
    # Ownership
    # ------------------------------------------------------------------

    def upsert_ownership(
        self, repo: str, path_pattern: str, owner: str, source: str = "CODEOWNERS"
    ) -> None:
        self.conn.execute(
            """INSERT INTO ownership(repo, path_pattern, owner, source)
               VALUES (?, ?, ?, ?)""",
            (repo, path_pattern, owner, source),
        )
        self._maybe_commit()

    def clear_ownership(self, repo: str) -> None:
        self.conn.execute("DELETE FROM ownership WHERE repo=?", (repo,))
        self._maybe_commit()

    def get_owners_for_path(self, repo: str, path: str) -> list[dict[str, str]]:
        """Return owners whose pattern matches the given path (simple prefix match)."""
        rows = self.conn.execute(
            "SELECT path_pattern, owner, source FROM ownership WHERE repo=?",
            (repo,),
        ).fetchall()
        matches = []
        for row in rows:
            pattern = row["path_pattern"]
            if _pattern_matches(pattern, path):
                matches.append(
                    {"pattern": pattern, "owner": row["owner"], "source": row["source"]}
                )
        return matches

    # ------------------------------------------------------------------
    # Reviewer Cards
    # ------------------------------------------------------------------

    def upsert_reviewer_card(self, repo: str, reviewer: str, card_json: str, updated_at: str) -> None:
        self.conn.execute(
            """INSERT INTO reviewer_cards(repo, reviewer, card_json, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(repo, reviewer) DO UPDATE SET
                 card_json=excluded.card_json, updated_at=excluded.updated_at""",
            (repo, reviewer, card_json, updated_at),
        )
        self._maybe_commit()

    # ------------------------------------------------------------------
    # Ingest tracking (incremental ingestion)
    # ------------------------------------------------------------------

    def get_last_ingest(self, repo: str) -> str | None:
        """Return ISO timestamp of last successful ingest for a repo, or None."""
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key=?",
            (f"last_ingest:{repo}",),
        ).fetchone()
        return row["value"] if row else None  # type: ignore[index]

    def set_last_ingest(self, repo: str, timestamp: str) -> None:
        """Record the timestamp of the most recent ingested PR."""
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (f"last_ingest:{repo}", timestamp),
        )
        self._maybe_commit()

    def get_reviewer_card(self, repo: str, reviewer: str) -> str | None:
        row = self.conn.execute(
            "SELECT card_json FROM reviewer_cards WHERE repo=? AND reviewer=?",
            (repo, reviewer),
        ).fetchone()
        return row["card_json"] if row else None  # type: ignore[index]

    def get_all_reviewer_cards(self, repo: str) -> list[dict[str, str]]:
        rows = self.conn.execute(
            "SELECT reviewer, card_json FROM reviewer_cards WHERE repo=?", (repo,)
        ).fetchall()
        return [{"reviewer": r["reviewer"], "card_json": r["card_json"]} for r in rows]

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_reviewers_for_paths(
        self, repo: str, paths: list[str], limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return reviewers ranked by review count for files overlapping the given paths."""
        if not paths:
            return []
        placeholders = ",".join("?" for _ in paths)
        query = f"""
            SELECT rc.reviewer, COUNT(DISTINCT rc.pr_id) as review_count
            FROM review_comments rc
            JOIN pr_files pf ON rc.pr_id = pf.pr_id
            JOIN prs p ON p.id = rc.pr_id
            WHERE p.repo = ? AND pf.path IN ({placeholders})
            GROUP BY rc.reviewer
            ORDER BY review_count DESC
            LIMIT ?
        """
        rows = self.conn.execute(query, [repo, *paths, limit]).fetchall()
        return [{"reviewer": r["reviewer"], "review_count": r["review_count"]} for r in rows]

    def get_reviewer_stats(self, repo: str, reviewer: str) -> dict[str, Any]:
        """Get aggregate stats for a reviewer."""
        total = self.conn.execute(
            "SELECT COUNT(*) as n FROM reviews r JOIN prs p ON p.id=r.pr_id WHERE p.repo=? AND r.reviewer=?",
            (repo, reviewer),
        ).fetchone()
        approved = self.conn.execute(
            "SELECT COUNT(*) as n FROM reviews r JOIN prs p ON p.id=r.pr_id WHERE p.repo=? AND r.reviewer=? AND r.state='APPROVED'",
            (repo, reviewer),
        ).fetchone()
        changes_req = self.conn.execute(
            "SELECT COUNT(*) as n FROM reviews r JOIN prs p ON p.id=r.pr_id WHERE p.repo=? AND r.reviewer=? AND r.state='CHANGES_REQUESTED'",
            (repo, reviewer),
        ).fetchone()
        comment_count = self.conn.execute(
            "SELECT COUNT(*) as n FROM review_comments rc JOIN prs p ON p.id=rc.pr_id WHERE p.repo=? AND rc.reviewer=?",
            (repo, reviewer),
        ).fetchone()
        return {
            "total_reviews": total["n"],  # type: ignore[index]
            "approved": approved["n"],  # type: ignore[index]
            "changes_requested": changes_req["n"],  # type: ignore[index]
            "total_comments": comment_count["n"],  # type: ignore[index]
        }

    def get_reviewer_comments(
        self, repo: str, reviewer: str, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Get review comments for a reviewer."""
        rows = self.conn.execute(
            """SELECT rc.body, rc.path, rc.line, rc.created_at, p.number as pr_number
               FROM review_comments rc
               JOIN prs p ON p.id = rc.pr_id
               WHERE p.repo=? AND rc.reviewer=?
               ORDER BY rc.created_at DESC
               LIMIT ?""",
            (repo, reviewer, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_top_reviewers(self, repo: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get top reviewers by review count."""
        rows = self.conn.execute(
            """SELECT r.reviewer, COUNT(*) as review_count
               FROM reviews r
               JOIN prs p ON p.id = r.pr_id
               WHERE p.repo = ?
               GROUP BY r.reviewer
               ORDER BY review_count DESC
               LIMIT ?""",
            (repo, limit),
        ).fetchall()
        return [{"reviewer": r["reviewer"], "review_count": r["review_count"]} for r in rows]


def _pattern_matches(pattern: str, path: str) -> bool:
    """Simple CODEOWNERS-style pattern matching.

    Supports:
    - Exact match
    - Directory prefix (pattern ends with /)
    - Wildcard * (single directory level)
    - Globstar ** (any depth)
    - Leading / means repo root
    """
    import fnmatch

    # Strip leading slash (CODEOWNERS paths are relative to repo root)
    pattern = pattern.lstrip("/")
    path = path.lstrip("/")

    # If pattern ends with /, match anything under that directory
    if pattern.endswith("/"):
        return path.startswith(pattern) or path.startswith(pattern.rstrip("/"))

    # Try fnmatch which handles * and ?
    if fnmatch.fnmatch(path, pattern):
        return True

    # Handle ** globstar: replace with fnmatch-compatible pattern
    if "**" in pattern:
        regex_pattern = pattern.replace("**", "*")
        if fnmatch.fnmatch(path, regex_pattern):
            return True

    # Simple prefix match for directory patterns without trailing /
    if "/" in pattern and not any(c in pattern for c in "*?["):
        return path.startswith(pattern + "/") or path == pattern

    return False
