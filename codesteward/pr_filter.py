"""Bot/CVE PR filtering: classify and skip low-signal automated PRs."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Filter policy configuration
# ---------------------------------------------------------------------------

class PRFilterConfig(BaseModel):
    """Configuration for bot/CVE PR filtering.

    Enabled by default with conservative heuristics that match common
    dependency-bump and CVE-patch bots while preserving genuine security work.
    """

    enabled: bool = True

    # Author login patterns (regex, case-insensitive)
    bot_author_patterns: list[str] = Field(
        default_factory=lambda: [
            r"^dependabot(\[bot\])?$",
            r"^renovate(\[bot\])?$",
            r"^snyk-bot$",
            r"^greenkeeper(\[bot\])?$",
            r"^pyup-bot$",
            r"^whitesource-bolt-for-github(\[bot\])?$",
            r"^mend-bolt-for-github(\[bot\])?$",
            r"^deepsource-autofix(\[bot\])?$",
            r"^github-actions(\[bot\])?$",
            r".*-bot$",
            r".*\[bot\]$",
        ],
        description="Regex patterns matched against PR author login (case-insensitive).",
    )

    # PR title patterns (regex, case-insensitive)
    title_patterns: list[str] = Field(
        default_factory=lambda: [
            r"\bCVE-\d{4}-\d+\b",
            r"^(bump|update|upgrade)\b.+\bfrom\b.+\bto\b",
            r"^(chore|build)\s*(\(.+\))?:\s*(bump|update|upgrade)\b",
            r"\bdependency\s+(bump|update|upgrade)\b",
            r"^\[Security\]\s+Bump\b",
            r"^Update\s+\S+\s+requirement",
            r"dependabot",
            r"renovate",
        ],
        description="Regex patterns matched against PR title (case-insensitive).",
    )

    # Label name patterns (regex, case-insensitive)
    label_patterns: list[str] = Field(
        default_factory=lambda: [
            r"^dependencies$",
            r"^automated(\s+pr)?$",
            r"^bot$",
            r"^security-patch$",
            r"^cve-patch$",
            r"^dep-update$",
        ],
        description="Regex patterns matched against any PR label name (case-insensitive).",
    )

    # Author logins that are always allowed through (override bot patterns)
    allowlist_authors: list[str] = Field(
        default_factory=list,
        description="Author logins that bypass bot author pattern matching.",
    )

    # Title substrings that prevent a PR from being filtered (override title patterns)
    allowlist_title_substrings: list[str] = Field(
        default_factory=list,
        description="Case-insensitive substrings that, if present in the title, prevent filtering.",
    )


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class PRClassifier:
    """Classifies a PR as low-signal (skip) or worth ingesting.

    All heuristics must combine positively to skip — a PR must match
    *at least one signal* to be filtered.  The allowlist always wins.
    """

    def __init__(self, policy: PRFilterConfig) -> None:
        self.policy = policy
        self._bot_re = [re.compile(p, re.I) for p in policy.bot_author_patterns]
        self._title_re = [re.compile(p, re.I) for p in policy.title_patterns]
        self._label_re = [re.compile(p, re.I) for p in policy.label_patterns]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_skip(self, pr: dict[str, Any]) -> tuple[bool, str]:
        """Determine whether a PR should be skipped.

        Args:
            pr: Raw PR dict as returned by the GitHub REST API.

        Returns:
            ``(skip, reason)`` – *skip* is True when the PR is low-signal;
            *reason* is a short human-readable string indicating which
            heuristic matched (empty when *skip* is False).
        """
        if not self.policy.enabled:
            return False, ""

        author = (pr.get("user") or {}).get("login", "") or ""
        title = pr.get("title", "") or ""
        labels: list[str] = [lbl.get("name", "") for lbl in (pr.get("labels") or [])]

        # Allowlist: if author is explicitly allowed, never skip
        if author.lower() in {a.lower() for a in self.policy.allowlist_authors}:
            return False, ""

        # Allowlist: if title contains an allowlisted substring, never skip
        title_lower = title.lower()
        for substr in self.policy.allowlist_title_substrings:
            if substr.lower() in title_lower:
                return False, ""

        # Check bot author
        if author and self._matches_any(author, self._bot_re):
            return True, f"bot-author:{author}"

        # Check title patterns
        for pat, compiled in zip(self.policy.title_patterns, self._title_re):
            if compiled.search(title):
                return True, f"title-pattern:{pat}"

        # Check labels
        for label in labels:
            if label and self._matches_any(label, self._label_re):
                return True, f"label:{label}"

        return False, ""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _matches_any(value: str, patterns: list[re.Pattern]) -> bool:  # type: ignore[type-arg]
        return any(p.search(value) for p in patterns)
