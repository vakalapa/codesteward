"""Build ReviewerSkillCards from historical review data."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from codesteward.db import Database
from codesteward.schemas import (
    BlockingThreshold,
    FocusWeights,
    ReviewerSkillCard,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Topic keyword sets for focus-weight classification
# ---------------------------------------------------------------------------

TOPIC_KEYWORDS: dict[str, list[str]] = {
    "api": [
        "api", "endpoint", "proto", "swagger", "openapi", "grpc", "rest",
        "backward", "compat", "breaking", "deprecat", "version", "schema",
    ],
    "tests": [
        "test", "coverage", "e2e", "unit", "integration", "flak", "ci",
        "fixture", "mock", "stub", "assert", "expect",
    ],
    "perf": [
        "perf", "benchmark", "latency", "throughput", "memory", "cpu",
        "cache", "pool", "buffer", "optim", "slow", "fast",
    ],
    "docs": [
        "doc", "readme", "comment", "godoc", "docstring", "changelog",
        "release note", "example", "tutorial",
    ],
    "security": [
        "security", "auth", "token", "secret", "cve", "vuln", "inject",
        "sanitiz", "escape", "tls", "cert", "crypto", "permission",
    ],
    "style": [
        "style", "lint", "format", "naming", "convention", "nit",
        "readab", "clean", "refactor", "unused", "dead code",
    ],
    "backward_compat": [
        "backward", "compat", "migration", "deprecat", "breaking",
        "upgrade", "downgrade", "rollback",
    ],
}

# Common blocker pattern keywords
BLOCKER_PATTERNS: list[tuple[str, str]] = [
    (r"\bmissing test", "missing tests"),
    (r"\bno test", "missing tests"),
    (r"\btest coverage", "test coverage"),
    (r"\berror handling", "error handling"),
    (r"\brace condition", "race condition"),
    (r"\bnot thread.safe", "thread safety"),
    (r"\bnull|nil pointer", "null safety"),
    (r"\bbreak.*change", "breaking change"),
    (r"\bbackward.*compat", "backward compatibility"),
    (r"\bdoc(s|umentation)?\s+(missing|needed|required)", "missing documentation"),
    (r"\brelease note", "release notes needed"),
    (r"\blog(ging)?\s+(missing|needed)", "missing logging"),
    (r"\bvalidat", "input validation"),
    (r"\bsecur", "security concern"),
    (r"\bperformance", "performance concern"),
    (r"\bmemory leak", "memory leak"),
    (r"\bhardcod", "hardcoded values"),
    (r"\bmagic number", "magic numbers"),
    (r"\btodo|fixme|hack", "TODO/FIXME left behind"),
]

# Evidence preference indicators
EVIDENCE_KEYWORDS: list[tuple[str, str]] = [
    (r"\bbenchmark", "benchmarks"),
    (r"\be2e", "e2e tests"),
    (r"\bunit test", "unit tests"),
    (r"\bintegration test", "integration tests"),
    (r"\bdoc(s|umentation)", "documentation"),
    (r"\brelease note", "release notes"),
    (r"\bchangelog", "changelog"),
    (r"\bexample", "usage examples"),
    (r"\breproduci", "reproduction steps"),
]

MAX_QUOTE_LENGTH = 25  # words

# Regex patterns for redacting identifiable info from quotes
_REDACT_PATTERNS: list[tuple[str, str]] = [
    (r"@[a-zA-Z0-9_-]+", "@<user>"),
    (r"#\d+", "#<number>"),
    (r"https?://\S+", "<url>"),
    (r"\b[a-f0-9]{7,40}\b", "<sha>"),
]


class ReviewerProfiler:
    """Builds ReviewerSkillCards from historical review comments and behavior."""

    def __init__(self, db: Database, redact_quotes: bool = False) -> None:
        self.db = db
        self.redact_quotes = redact_quotes

    def build_card(self, repo: str, reviewer: str) -> ReviewerSkillCard:
        """Analyze a reviewer's history and build their skill card."""
        stats = self.db.get_reviewer_stats(repo, reviewer)
        comments = self.db.get_reviewer_comments(repo, reviewer, limit=500)

        total_reviews = stats["total_reviews"]
        if total_reviews == 0:
            return ReviewerSkillCard(reviewer=reviewer)

        # Focus weights
        focus = self._compute_focus_weights(comments)

        # Blocking threshold
        blocking = self._compute_blocking_threshold(stats)

        # Common blockers
        common_blockers = self._extract_common_blockers(comments)

        # Style preferences
        style_prefs = self._extract_style_preferences(comments)

        # Evidence preferences
        evidence_prefs = self._extract_evidence_preferences(comments)

        # Recent interests (last 90 days)
        recent = self._extract_recent_interests(comments)

        # Quote bank
        quotes = self._build_quote_bank(comments, redact=self.redact_quotes)

        # Approval rate
        approval_rate = stats["approved"] / total_reviews if total_reviews > 0 else 0.0

        # Avg comments per review
        avg_comments = stats["total_comments"] / total_reviews if total_reviews > 0 else 0.0

        return ReviewerSkillCard(
            reviewer=reviewer,
            focus_weights=focus,
            blocking_threshold=blocking,
            common_blockers=common_blockers,
            style_preferences=style_prefs,
            evidence_preferences=evidence_prefs,
            recent_interests=recent,
            quote_bank=quotes,
            total_reviews=total_reviews,
            approval_rate=round(approval_rate, 3),
            avg_comments_per_review=round(avg_comments, 2),
        )

    def profile_all(self, repo: str, top_n: int = 50) -> list[ReviewerSkillCard]:
        """Build cards for the top N reviewers by review count."""
        top = self.db.get_top_reviewers(repo, limit=top_n)
        cards: list[ReviewerSkillCard] = []
        for entry in top:
            login = entry["reviewer"]
            logger.info("Profiling reviewer: %s (%d reviews)", login, entry["review_count"])
            card = self.build_card(repo, login)
            # Persist
            self.db.upsert_reviewer_card(
                repo=repo,
                reviewer=login,
                card_json=card.model_dump_json(),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            cards.append(card)
        return cards

    # ------------------------------------------------------------------
    # Internal analysis methods
    # ------------------------------------------------------------------

    def _compute_focus_weights(self, comments: list[dict[str, Any]]) -> FocusWeights:
        """Score each topic by keyword frequency across all comments."""
        scores: dict[str, float] = {topic: 0.0 for topic in TOPIC_KEYWORDS}
        total_words = 0

        for c in comments:
            body = (c.get("body") or "").lower()
            path = (c.get("path") or "").lower()
            text = body + " " + path
            words = text.split()
            total_words += len(words)

            for topic, keywords in TOPIC_KEYWORDS.items():
                for kw in keywords:
                    scores[topic] += text.count(kw)

        # Normalize to 0-1 range
        max_score = max(scores.values()) if scores else 1.0
        if max_score > 0:
            for topic in scores:
                scores[topic] = round(scores[topic] / max_score, 3)

        return FocusWeights(**scores)

    def _compute_blocking_threshold(self, stats: dict[str, Any]) -> BlockingThreshold:
        """Determine how aggressively the reviewer blocks PRs."""
        total = stats["total_reviews"]
        if total == 0:
            return BlockingThreshold.MEDIUM

        change_rate = stats["changes_requested"] / total
        if change_rate > 0.4:
            return BlockingThreshold.HIGH
        elif change_rate > 0.15:
            return BlockingThreshold.MEDIUM
        else:
            return BlockingThreshold.LOW

    def _extract_common_blockers(self, comments: list[dict[str, Any]]) -> list[str]:
        """Find recurring blocker patterns in review comments."""
        counter: Counter[str] = Counter()
        for c in comments:
            body = (c.get("body") or "").lower()
            for pattern, label in BLOCKER_PATTERNS:
                if re.search(pattern, body, re.IGNORECASE):
                    counter[label] += 1
        # Return top 5 by frequency
        return [label for label, _ in counter.most_common(5)]

    def _extract_style_preferences(self, comments: list[dict[str, Any]]) -> list[str]:
        """Extract style-related preferences heuristically."""
        prefs: set[str] = set()
        for c in comments:
            body = (c.get("body") or "").lower()
            if re.search(r"explicit.*error|error.*explicit", body):
                prefs.add("prefers explicit error handling")
            if re.search(r"avoid.*hidden|hidden.*default", body):
                prefs.add("avoid hidden defaults")
            if re.search(r"naming|name should|rename", body):
                prefs.add("cares about naming")
            if re.search(r"comment.*why|explain.*why", body):
                prefs.add("wants comments explaining why")
            if re.search(r"dry|don.t repeat", body):
                prefs.add("prefers DRY code")
            if re.search(r"simple|simplif|kiss", body):
                prefs.add("prefers simplicity")
            if re.search(r"idiomatic", body):
                prefs.add("prefers idiomatic patterns")
            if re.search(r"early return", body):
                prefs.add("prefers early returns")
        return sorted(prefs)[:8]  # cap at 8

    def _extract_evidence_preferences(self, comments: list[dict[str, Any]]) -> list[str]:
        """Detect what kinds of evidence the reviewer typically asks for."""
        counter: Counter[str] = Counter()
        for c in comments:
            body = (c.get("body") or "").lower()
            for pattern, label in EVIDENCE_KEYWORDS:
                if re.search(pattern, body, re.IGNORECASE):
                    counter[label] += 1
        return [label for label, _ in counter.most_common(5)]

    def _extract_recent_interests(self, comments: list[dict[str, Any]]) -> list[str]:
        """Topics from the last 90 days (comments are pre-sorted by date desc)."""
        recent_comments = comments[:50]  # already sorted desc
        topics: Counter[str] = Counter()
        for c in recent_comments:
            body = (c.get("body") or "").lower()
            path = (c.get("path") or "").lower()
            text = body + " " + path
            for topic, keywords in TOPIC_KEYWORDS.items():
                for kw in keywords:
                    if kw in text:
                        topics[topic] += 1
                        break  # count once per topic per comment
        return [t for t, _ in topics.most_common(3)]

    def _build_quote_bank(self, comments: list[dict[str, Any]], redact: bool = False) -> list[str]:
        """Extract short representative quotes (<=25 words).

        If *redact* is True, mask @mentions, PR numbers, URLs, and commit SHAs.
        """
        quotes: list[str] = []
        seen: set[str] = set()

        for c in comments:
            body = (c.get("body") or "").strip()
            if not body:
                continue
            # Take the first sentence
            sentences = re.split(r"[.!?]\s", body)
            for sent in sentences:
                words = sent.split()
                if 5 <= len(words) <= MAX_QUOTE_LENGTH:
                    normalized = " ".join(words).strip()
                    if redact:
                        for pattern, replacement in _REDACT_PATTERNS:
                            normalized = re.sub(pattern, replacement, normalized)
                    if normalized.lower() not in seen:
                        seen.add(normalized.lower())
                        quotes.append(normalized)
                        if len(quotes) >= 10:
                            return quotes
        return quotes
