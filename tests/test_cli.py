"""Tests for CLI helper functions."""

from __future__ import annotations

import pytest

from codesteward.cli import _parse_since, _parse_diff_to_files, _default_focus_for_categories
from codesteward.schemas import ReviewerCategory, FocusWeights


class TestParseSince:
    def test_days(self) -> None:
        assert _parse_since("180d") == 180

    def test_months(self) -> None:
        assert _parse_since("6m") == 180

    def test_years(self) -> None:
        assert _parse_since("1y") == 365

    def test_bare_number(self) -> None:
        assert _parse_since("90") == 90

    def test_uppercase(self) -> None:
        assert _parse_since("30D") == 30

    def test_whitespace(self) -> None:
        assert _parse_since("  60d  ") == 60


class TestParseDiffToFiles:
    SAMPLE_DIFF = """\
diff --git a/src/handler.py b/src/handler.py
--- a/src/handler.py
+++ b/src/handler.py
@@ -1,3 +1,5 @@
 existing line
+new line 1
+new line 2
-removed line
diff --git a/tests/test_handler.py b/tests/test_handler.py
--- /dev/null
+++ b/tests/test_handler.py
@@ -0,0 +1,3 @@
+def test_handler():
+    assert True
+    pass
"""

    def test_parses_file_count(self) -> None:
        files = _parse_diff_to_files(self.SAMPLE_DIFF)
        assert len(files) == 2

    def test_parses_file_paths(self) -> None:
        files = _parse_diff_to_files(self.SAMPLE_DIFF)
        paths = [f.path for f in files]
        assert "src/handler.py" in paths
        assert "tests/test_handler.py" in paths

    def test_counts_additions_deletions(self) -> None:
        files = _parse_diff_to_files(self.SAMPLE_DIFF)
        handler = next(f for f in files if f.path == "src/handler.py")
        assert handler.additions == 2
        assert handler.deletions == 1

    def test_new_file_all_additions(self) -> None:
        files = _parse_diff_to_files(self.SAMPLE_DIFF)
        test_file = next(f for f in files if f.path == "tests/test_handler.py")
        assert test_file.additions == 3
        assert test_file.deletions == 0

    def test_empty_diff(self) -> None:
        files = _parse_diff_to_files("")
        assert files == []

    def test_patch_content_stored(self) -> None:
        files = _parse_diff_to_files(self.SAMPLE_DIFF)
        handler = next(f for f in files if f.path == "src/handler.py")
        assert "+new line 1" in handler.patch


class TestDefaultFocusForCategories:
    def test_security_hawk_focus(self) -> None:
        focus = _default_focus_for_categories([ReviewerCategory.SECURITY_HAWK])
        assert focus.security == 0.9

    def test_test_hawk_focus(self) -> None:
        focus = _default_focus_for_categories([ReviewerCategory.TEST_CI_HAWK])
        assert focus.tests == 0.9

    def test_api_hawk_focus(self) -> None:
        focus = _default_focus_for_categories([ReviewerCategory.API_STABILITY_HAWK])
        assert focus.api == 0.9
        assert focus.backward_compat == 0.7

    def test_docs_hawk_focus(self) -> None:
        focus = _default_focus_for_categories([ReviewerCategory.DOCS_HAWK])
        assert focus.docs == 0.9

    def test_general_category(self) -> None:
        focus = _default_focus_for_categories([ReviewerCategory.GENERAL])
        # Should have moderate defaults across the board
        assert focus.api == 0.4
        assert focus.tests == 0.4

    def test_empty_categories(self) -> None:
        focus = _default_focus_for_categories([])
        assert isinstance(focus, FocusWeights)
