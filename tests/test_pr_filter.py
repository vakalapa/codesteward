"""Tests for bot/CVE PR filtering (010-bot-cve-pr-filtering)."""

from __future__ import annotations

import pytest

from codesteward.pr_filter import PRClassifier, PRFilterConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pr(
    author: str = "alice",
    title: str = "Fix a bug",
    labels: list[str] | None = None,
    body: str = "",
) -> dict:
    return {
        "user": {"login": author},
        "title": title,
        "body": body,
        "labels": [{"name": lbl} for lbl in (labels or [])],
    }


def _classifier(
    enabled: bool = True,
    allowlist_authors: list[str] | None = None,
    allowlist_title_substrings: list[str] | None = None,
) -> PRClassifier:
    policy = PRFilterConfig(
        enabled=enabled,
        allowlist_authors=allowlist_authors or [],
        allowlist_title_substrings=allowlist_title_substrings or [],
    )
    return PRClassifier(policy)


# ---------------------------------------------------------------------------
# Policy disabled
# ---------------------------------------------------------------------------

class TestPolicyDisabled:
    def test_disabled_never_skips_bot(self) -> None:
        clf = _classifier(enabled=False)
        skip, reason = clf.should_skip(_pr(author="dependabot[bot]", title="Bump lodash from 1.0 to 2.0"))
        assert not skip
        assert reason == ""

    def test_disabled_never_skips_cve(self) -> None:
        clf = _classifier(enabled=False)
        skip, reason = clf.should_skip(_pr(title="[Security] Bump openssl to fix CVE-2024-1234"))
        assert not skip

    def test_disabled_preserves_normal_prs(self) -> None:
        clf = _classifier(enabled=False)
        skip, reason = clf.should_skip(_pr(author="alice", title="Add feature X"))
        assert not skip


# ---------------------------------------------------------------------------
# Bot author patterns
# ---------------------------------------------------------------------------

class TestBotAuthorPatterns:
    @pytest.mark.parametrize("login", [
        "dependabot[bot]",
        "dependabot",
        "renovate[bot]",
        "renovate",
        "snyk-bot",
        "pyup-bot",
        "greenkeeper[bot]",
        "whitesource-bolt-for-github[bot]",
        "mend-bolt-for-github[bot]",
        "deepsource-autofix[bot]",
        "github-actions[bot]",
        "my-custom-bot",
        "release-bot",
    ])
    def test_bot_author_skipped(self, login: str) -> None:
        clf = _classifier()
        skip, reason = clf.should_skip(_pr(author=login, title="Routine maintenance"))
        assert skip, f"Expected {login} to be skipped"
        assert "bot-author" in reason

    @pytest.mark.parametrize("login", [
        "alice",
        "bob",
        "carol",
        "octocat",
        "robot-framework-author",  # substring match should NOT catch this
    ])
    def test_human_author_not_skipped_by_author_alone(self, login: str) -> None:
        clf = _classifier()
        # Give a plain title so no other signal fires
        skip, _ = clf.should_skip(_pr(author=login, title="Refactor database layer"))
        assert not skip, f"Expected {login} not to be skipped"


# ---------------------------------------------------------------------------
# Title patterns
# ---------------------------------------------------------------------------

class TestTitlePatterns:
    @pytest.mark.parametrize("title", [
        "Bump lodash from 4.17.20 to 4.17.21",
        "bump axios from 0.21.1 to 0.21.4",
        "Update requests requirement from 2.27.0 to 2.28.0",
        "chore: bump mypy from 0.910 to 0.950",
        "build(deps): bump actions/checkout from 3 to 4",
        "[Security] Bump urllib3 to address CVE-2023-43804",
        "Fix CVE-2024-12345 in openssl dependency",
        "Patch for CVE-2023-0001",
        "dependency bump for security patch",
    ])
    def test_title_pattern_skipped(self, title: str) -> None:
        clf = _classifier()
        skip, reason = clf.should_skip(_pr(author="alice", title=title))
        assert skip, f"Expected title '{title}' to be filtered"
        assert "title-pattern" in reason or "bot-author" in reason  # author=alice so must be title

    @pytest.mark.parametrize("title", [
        "Add retry logic to HTTP client",
        "Refactor payment module",
        "Fix flaky CI test on Windows",
        "Implement feature flag support",
        "docs: update README with new API examples",
        "test: add integration tests for auth service",
    ])
    def test_normal_title_not_skipped(self, title: str) -> None:
        clf = _classifier()
        skip, _ = clf.should_skip(_pr(author="alice", title=title))
        assert not skip, f"Expected title '{title}' NOT to be filtered"


# ---------------------------------------------------------------------------
# Label patterns
# ---------------------------------------------------------------------------

class TestLabelPatterns:
    @pytest.mark.parametrize("label", [
        "dependencies",
        "automated pr",
        "automated",
        "bot",
        "security-patch",
        "cve-patch",
        "dep-update",
    ])
    def test_matching_label_skipped(self, label: str) -> None:
        clf = _classifier()
        skip, reason = clf.should_skip(_pr(author="alice", title="Normal title", labels=[label]))
        assert skip, f"Expected label '{label}' to trigger skip"
        assert "label:" in reason

    @pytest.mark.parametrize("label", [
        "bugfix",
        "enhancement",
        "breaking-change",
        "needs-review",
        "approved",
    ])
    def test_normal_label_not_skipped(self, label: str) -> None:
        clf = _classifier()
        skip, _ = clf.should_skip(_pr(author="alice", title="Normal title", labels=[label]))
        assert not skip, f"Expected label '{label}' NOT to trigger skip"


# ---------------------------------------------------------------------------
# Allowlist overrides
# ---------------------------------------------------------------------------

class TestAllowlist:
    def test_allowlist_author_bypasses_bot_pattern(self) -> None:
        """A bot that we still want to track should be allowlistable."""
        clf = _classifier(allowlist_authors=["my-custom-bot"])
        skip, _ = clf.should_skip(_pr(author="my-custom-bot", title="Security audit PR"))
        assert not skip

    def test_allowlist_author_case_insensitive(self) -> None:
        clf = _classifier(allowlist_authors=["DependaBot"])
        skip, _ = clf.should_skip(_pr(author="dependabot", title="Bump lib"))
        assert not skip

    def test_allowlist_title_substring_bypasses_title_pattern(self) -> None:
        """e.g. a team that intentionally merges CVE PRs for audit."""
        clf = _classifier(allowlist_title_substrings=["audit-tracked"])
        skip, _ = clf.should_skip(_pr(
            author="alice",
            title="Fix CVE-2024-1234 (audit-tracked)",
        ))
        assert not skip

    def test_allowlist_title_bypass_case_insensitive(self) -> None:
        clf = _classifier(allowlist_title_substrings=["AUDIT-TRACKED"])
        skip, _ = clf.should_skip(_pr(
            author="alice",
            title="bump dep (audit-tracked)",
        ))
        assert not skip

    def test_allowlist_does_not_shadow_unrelated_pr(self) -> None:
        clf = _classifier(allowlist_authors=["alice"])
        skip, _ = clf.should_skip(_pr(author="dependabot[bot]", title="Bump lib"))
        assert skip  # dependabot is NOT on allowlist; alice is irrelevant here


# ---------------------------------------------------------------------------
# False positive guards
# ---------------------------------------------------------------------------

class TestFalsePositiveGuards:
    def test_pr_with_no_signals_not_skipped(self) -> None:
        clf = _classifier()
        skip, _ = clf.should_skip(_pr(author="alice", title="Improve error messages", labels=[]))
        assert not skip

    def test_large_security_pr_with_no_bot_signal_not_skipped(self) -> None:
        clf = _classifier()
        skip, _ = clf.should_skip(_pr(
            author="security-team",
            title="Harden TLS configuration and update cipher list",
            labels=["security"],
        ))
        assert not skip

    def test_pr_missing_user_field_does_not_raise(self) -> None:
        clf = _classifier()
        pr = {"title": "Bump lib from 1 to 2", "labels": [], "body": ""}
        skip, reason = clf.should_skip(pr)
        # Should still catch the title pattern; must not raise
        assert isinstance(skip, bool)

    def test_pr_with_none_body_does_not_raise(self) -> None:
        clf = _classifier()
        pr = {"user": {"login": "alice"}, "title": "Normal PR", "body": None, "labels": []}
        skip, _ = clf.should_skip(pr)
        assert not skip

    def test_pr_with_null_label_name_does_not_raise(self) -> None:
        clf = _classifier()
        pr = {
            "user": {"login": "alice"},
            "title": "Normal PR",
            "body": "",
            "labels": [{"name": None}],
        }
        skip, _ = clf.should_skip(pr)
        assert not skip


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

class TestPRFilterConfigDefaults:
    def test_enabled_by_default(self) -> None:
        cfg = PRFilterConfig()
        assert cfg.enabled is True

    def test_default_bot_patterns_present(self) -> None:
        cfg = PRFilterConfig()
        assert len(cfg.bot_author_patterns) > 0

    def test_default_title_patterns_present(self) -> None:
        cfg = PRFilterConfig()
        assert len(cfg.title_patterns) > 0

    def test_default_label_patterns_present(self) -> None:
        cfg = PRFilterConfig()
        assert len(cfg.label_patterns) > 0

    def test_allowlists_empty_by_default(self) -> None:
        cfg = PRFilterConfig()
        assert cfg.allowlist_authors == []
        assert cfg.allowlist_title_substrings == []

    def test_custom_patterns_override_defaults(self) -> None:
        cfg = PRFilterConfig(bot_author_patterns=[r"^mybot$"])
        clf = PRClassifier(cfg)
        skip, _ = clf.should_skip(_pr(author="mybot", title="Do something"))
        assert skip

        # dependabot is no longer in patterns, so should not be skipped on author alone
        skip2, _ = clf.should_skip(_pr(author="dependabot[bot]", title="Normal title"))
        assert not skip2


# ---------------------------------------------------------------------------
# Ingest integration: skip accounting
# ---------------------------------------------------------------------------

class TestIngestSkipAccounting:
    """Validate that the Ingestor correctly propagates skip counts."""

    def test_ingestor_counts_skipped_bot_prs(self, tmp_path) -> None:
        from unittest.mock import MagicMock, patch
        from codesteward.db import Database
        from codesteward.ingest import Ingestor

        db_path = tmp_path / "filter_test.sqlite"
        db = Database(str(db_path))
        db.init_schema()

        gh = MagicMock()
        gh.list_prs.return_value = [
            {
                "number": 1,
                "title": "Bump lodash from 4 to 5",
                "user": {"login": "dependabot[bot]"},
                "labels": [],
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-02T00:00:00Z",
                "state": "closed",
                "body": "",
            },
            {
                "number": 2,
                "title": "Add feature X",
                "user": {"login": "alice"},
                "labels": [],
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-02T00:00:00Z",
                "state": "closed",
                "body": "",
            },
        ]
        gh.get_pr_files.return_value = []
        gh.get_pr_reviews.return_value = []
        gh.get_pr_review_comments.return_value = []

        with patch("codesteward.ingest.RepoMapper") as MockMapper:
            mock_mapper = MagicMock()
            mock_mapper.ingest_ownership.return_value = 0
            mock_mapper.detect_areas.return_value = []
            MockMapper.return_value = mock_mapper

            ingestor = Ingestor(db, gh, filter_policy=PRFilterConfig(enabled=True))
            stats = ingestor.ingest("test/repo", since_days=365, max_prs=100)

        assert stats["skipped_bot_cve"] == 1
        assert stats["prs"] == 1  # only alice's PR ingested

    def test_ingestor_disabled_filter_ingests_all(self, tmp_path) -> None:
        from unittest.mock import MagicMock, patch
        from codesteward.db import Database
        from codesteward.ingest import Ingestor

        db_path = tmp_path / "filter_disabled.sqlite"
        db = Database(str(db_path))
        db.init_schema()

        gh = MagicMock()
        gh.list_prs.return_value = [
            {
                "number": 1,
                "title": "Bump lodash from 4 to 5",
                "user": {"login": "dependabot[bot]"},
                "labels": [],
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-02T00:00:00Z",
                "state": "closed",
                "body": "",
            },
        ]
        gh.get_pr_files.return_value = []
        gh.get_pr_reviews.return_value = []
        gh.get_pr_review_comments.return_value = []

        with patch("codesteward.ingest.RepoMapper") as MockMapper:
            mock_mapper = MagicMock()
            mock_mapper.ingest_ownership.return_value = 0
            mock_mapper.detect_areas.return_value = []
            MockMapper.return_value = mock_mapper

            ingestor = Ingestor(db, gh, filter_policy=PRFilterConfig(enabled=False))
            stats = ingestor.ingest("test/repo", since_days=365, max_prs=100)

        assert stats["skipped_bot_cve"] == 0
        assert stats["prs"] == 1

    def test_ingestor_default_filter_enabled(self, tmp_path) -> None:
        """When no policy passed, default (enabled) policy is used."""
        from unittest.mock import MagicMock, patch
        from codesteward.db import Database
        from codesteward.ingest import Ingestor

        db_path = tmp_path / "filter_default.sqlite"
        db = Database(str(db_path))
        db.init_schema()

        gh = MagicMock()
        gh.list_prs.return_value = [
            {
                "number": 1,
                "title": "Bump requests from 2.27 to 2.28",
                "user": {"login": "renovate[bot]"},
                "labels": [],
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-02T00:00:00Z",
                "state": "closed",
                "body": "",
            },
        ]
        gh.get_pr_files.return_value = []
        gh.get_pr_reviews.return_value = []
        gh.get_pr_review_comments.return_value = []

        with patch("codesteward.ingest.RepoMapper") as MockMapper:
            mock_mapper = MagicMock()
            mock_mapper.ingest_ownership.return_value = 0
            mock_mapper.detect_areas.return_value = []
            MockMapper.return_value = mock_mapper

            ingestor = Ingestor(db, gh)  # no policy → default enabled
            stats = ingestor.ingest("test/repo", since_days=365, max_prs=100)

        assert stats["skipped_bot_cve"] == 1
        assert stats["prs"] == 0
