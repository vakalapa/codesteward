"""Reviewer discovery and ranking."""

from __future__ import annotations

import logging
import re
from typing import Any

from codesteward.db import Database
from codesteward.schemas import ChangeContext, ReviewerCategory, ReviewerInfo

logger = logging.getLogger(__name__)

# Weights for scoring
W_OWNERSHIP = 1.0
W_HISTORICAL = 0.7
W_RECENCY = 0.3  # bonus for recent activity


class ReviewerDiscovery:
    """Discovers and ranks likely reviewers for a given ChangeContext."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def discover(
        self, ctx: ChangeContext, top_k: int = 5
    ) -> list[ReviewerInfo]:
        """Return the top-K ranked reviewers for the change context.

        Prefers individual reviewers with profile data over team/org names
        from CODEOWNERS that have no review history.
        """
        scores: dict[str, float] = {}
        ownership_map: dict[str, list[str]] = {}
        category_map: dict[str, set[ReviewerCategory]] = {}

        repo = ctx.repo
        paths = [f.path for f in ctx.changed_files]

        # 1. Score from ownership (CODEOWNERS / OWNERS)
        for path in paths:
            owners = self.db.get_owners_for_path(repo, path)
            for entry in owners:
                login = entry["owner"]
                scores[login] = scores.get(login, 0) + W_OWNERSHIP
                ownership_map.setdefault(login, []).append(entry["pattern"])
                category_map.setdefault(login, set()).add(ReviewerCategory.PRIMARY_OWNER)

        # 2. Score from historical review activity on changed paths
        hist = self.db.get_reviewers_for_paths(repo, paths, limit=30)
        for entry in hist:
            login = entry["reviewer"]
            count = entry["review_count"]
            scores[login] = scores.get(login, 0) + W_HISTORICAL * min(count / 5.0, 3.0)

        # 2b. Also pull top reviewers for the repo overall as fallback candidates
        top_global = self.db.get_top_reviewers(repo, limit=20)
        for entry in top_global:
            login = entry["reviewer"]
            if login not in scores:
                count = entry["review_count"]
                scores[login] = W_RECENCY * min(count / 10.0, 1.5)

        # 3. Categorize reviewers by their historical focus
        for login in scores:
            cats = category_map.setdefault(login, set())
            comments = self.db.get_reviewer_comments(repo, login, limit=100)
            cat_signals = _detect_categories(comments)
            cats.update(cat_signals)

        # 4. Build ranked list, penalizing team/org names without review history
        ranked: list[ReviewerInfo] = []
        for login, score in sorted(scores.items(), key=lambda x: -x[1]):
            stats = self.db.get_reviewer_stats(repo, login)
            review_count = stats.get("total_reviews", 0)

            # Penalize team/org names (contain /) that have no individual review data
            is_team = "/" in login
            if is_team and review_count == 0:
                score *= 0.1  # heavy penalty — prefer real individuals

            # Boost reviewers with profile cards in the DB
            has_card = self.db.get_reviewer_card(repo, login) is not None
            if has_card:
                score *= 1.5

            ranked.append(
                ReviewerInfo(
                    login=login,
                    score=round(score, 3),
                    categories=sorted(category_map.get(login, set()), key=lambda c: c.value),
                    ownership_paths=ownership_map.get(login, []),
                    review_count=review_count,
                )
            )

        # Re-sort after adjustments
        ranked.sort(key=lambda r: -r.score)

        # Ensure diversity: include at least one from each category present
        result = ranked[:top_k]
        covered_cats = {cat for r in result for cat in r.categories}
        for r in ranked[top_k:]:
            if len(result) >= top_k + 2:
                break
            new_cats = set(r.categories) - covered_cats
            if new_cats:
                result.append(r)
                covered_cats.update(new_cats)

        return result


def _detect_categories(comments: list[dict[str, Any]]) -> set[ReviewerCategory]:
    """Heuristic category detection from review comment content."""
    cats: set[ReviewerCategory] = set()
    if not comments:
        return cats

    bodies = " ".join(c.get("body", "") for c in comments).lower()
    paths = [c.get("path", "") for c in comments if c.get("path")]

    # Test/CI hawk
    test_signals = sum(1 for p in paths if re.search(r"test|spec|_test\.", p, re.I))
    test_body = len(re.findall(r"\b(test|coverage|ci|flak[ey]|e2e|unit test|integration)\b", bodies))
    if test_signals > 3 or test_body > 5:
        cats.add(ReviewerCategory.TEST_CI_HAWK)

    # API stability hawk
    api_signals = sum(1 for p in paths if re.search(r"api|proto|openapi|swagger", p, re.I))
    api_body = len(re.findall(r"\b(api|backward|compat|breaking|deprecat|version)\b", bodies))
    if api_signals > 2 or api_body > 5:
        cats.add(ReviewerCategory.API_STABILITY_HAWK)

    # Security hawk
    sec_body = len(re.findall(r"\b(security|auth|token|secret|cve|vuln|inject|sanitiz|escape)\b", bodies))
    if sec_body > 3:
        cats.add(ReviewerCategory.SECURITY_HAWK)

    # Docs hawk
    doc_signals = sum(1 for p in paths if re.search(r"\.md$|docs/|README", p, re.I))
    if doc_signals > 3:
        cats.add(ReviewerCategory.DOCS_HAWK)

    if not cats:
        cats.add(ReviewerCategory.GENERAL)

    return cats
