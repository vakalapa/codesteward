"""Tests for CODEOWNERS file parsing."""

import pytest
from codesteward.repo_mapper import parse_codeowners


SAMPLE_CODEOWNERS = """\
# This is a comment

# Global owners
*       @global-owner1 @global-owner2

# JS files
*.js    @js-owner

# Go files in root
*.go    @go-team

# Build files
/build/ @build-team

# Documentation
/docs/  @docs-team @tech-writers

# Specific directory
/src/api/    @api-team @backend-lead
/src/frontend/ @frontend-team

# Org-level owners
*.py    @org/python-team
"""


class TestParseCodeowners:
    def test_parses_global_owners(self) -> None:
        entries = parse_codeowners(SAMPLE_CODEOWNERS)
        global_entry = next(e for e in entries if e.path_pattern == "*")
        assert "global-owner1" in global_entry.owners
        assert "global-owner2" in global_entry.owners

    def test_parses_extension_patterns(self) -> None:
        entries = parse_codeowners(SAMPLE_CODEOWNERS)
        js_entry = next(e for e in entries if e.path_pattern == "*.js")
        assert js_entry.owners == ["js-owner"]

    def test_parses_directory_patterns(self) -> None:
        entries = parse_codeowners(SAMPLE_CODEOWNERS)
        docs_entry = next(e for e in entries if e.path_pattern == "/docs/")
        assert "docs-team" in docs_entry.owners
        assert "tech-writers" in docs_entry.owners

    def test_parses_specific_paths(self) -> None:
        entries = parse_codeowners(SAMPLE_CODEOWNERS)
        api_entry = next(e for e in entries if e.path_pattern == "/src/api/")
        assert "api-team" in api_entry.owners
        assert "backend-lead" in api_entry.owners

    def test_strips_at_signs(self) -> None:
        entries = parse_codeowners(SAMPLE_CODEOWNERS)
        for entry in entries:
            for owner in entry.owners:
                assert not owner.startswith("@"), f"Owner {owner} should not start with @"

    def test_handles_org_owners(self) -> None:
        entries = parse_codeowners(SAMPLE_CODEOWNERS)
        py_entry = next(e for e in entries if e.path_pattern == "*.py")
        assert "org/python-team" in py_entry.owners

    def test_skips_comments_and_blanks(self) -> None:
        entries = parse_codeowners(SAMPLE_CODEOWNERS)
        # No entries should have comment content
        for entry in entries:
            assert not entry.path_pattern.startswith("#")

    def test_all_entries_are_codeowners_source(self) -> None:
        entries = parse_codeowners(SAMPLE_CODEOWNERS)
        for entry in entries:
            assert entry.source == "CODEOWNERS"

    def test_empty_file(self) -> None:
        entries = parse_codeowners("")
        assert entries == []

    def test_comments_only(self) -> None:
        entries = parse_codeowners("# just a comment\n# another")
        assert entries == []

    def test_entry_count(self) -> None:
        entries = parse_codeowners(SAMPLE_CODEOWNERS)
        # Count non-comment, non-blank lines with at least 2 fields
        assert len(entries) == 8
