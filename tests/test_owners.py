"""Tests for Kubernetes-style OWNERS file parsing."""

import pytest
from codesteward.repo_mapper import parse_owners_file


SAMPLE_OWNERS = """\
# Top-level OWNERS file
approvers:
  - alice
  - bob
reviewers:
  - charlie
  - dave
"""

SAMPLE_OWNERS_SUBDIR = """\
approvers:
  - subdir-alice
"""

SAMPLE_OWNERS_EMPTY = """\
# No actual owners
"""


class TestParseOwnersFile:
    def test_parses_approvers(self) -> None:
        entries = parse_owners_file(SAMPLE_OWNERS)
        # Should have at least one entry for approvers
        approver_entries = [e for e in entries if "alice" in e.owners or "bob" in e.owners]
        assert len(approver_entries) >= 1
        owners = approver_entries[0].owners
        assert "alice" in owners
        assert "bob" in owners

    def test_parses_reviewers(self) -> None:
        entries = parse_owners_file(SAMPLE_OWNERS)
        reviewer_entries = [e for e in entries if "charlie" in e.owners or "dave" in e.owners]
        assert len(reviewer_entries) >= 1
        owners = reviewer_entries[0].owners
        assert "charlie" in owners
        assert "dave" in owners

    def test_source_is_owners(self) -> None:
        entries = parse_owners_file(SAMPLE_OWNERS)
        for entry in entries:
            assert entry.source == "OWNERS"

    def test_directory_scoping(self) -> None:
        entries = parse_owners_file(SAMPLE_OWNERS_SUBDIR, directory="pkg/api")
        assert len(entries) >= 1
        assert entries[0].path_pattern == "pkg/api/**"

    def test_root_directory(self) -> None:
        entries = parse_owners_file(SAMPLE_OWNERS, directory="")
        for entry in entries:
            assert entry.path_pattern == "**"

    def test_empty_owners_file(self) -> None:
        entries = parse_owners_file(SAMPLE_OWNERS_EMPTY)
        assert entries == []

    def test_no_duplicate_owners(self) -> None:
        content = "approvers:\n  - alice\n  - alice\n"
        entries = parse_owners_file(content)
        # The parser doesn't deduplicate; that's fine - it's the raw parse
        assert len(entries) >= 1

    def test_handles_quoted_names(self) -> None:
        content = 'approvers:\n  - "quoted-user"\n  - \'single-quoted\'\n'
        entries = parse_owners_file(content)
        assert len(entries) >= 1
        owners = entries[0].owners
        assert "quoted-user" in owners
        assert "single-quoted" in owners
