"""Ingest historical PR review data from GitHub into the local database."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from codesteward.db import Database
from codesteward.github_client import GitHubClient
from codesteward.pr_filter import PRClassifier, PRFilterConfig
from codesteward.repo_mapper import RepoMapper

logger = logging.getLogger(__name__)


class Ingestor:
    """Pulls PR metadata, reviews, and review comments from GitHub REST API."""

    def __init__(
        self,
        db: Database,
        gh: GitHubClient,
        filter_policy: PRFilterConfig | None = None,
    ) -> None:
        self.db = db
        self.gh = gh
        policy = filter_policy if filter_policy is not None else PRFilterConfig()
        self.classifier = PRClassifier(policy)
        if policy.enabled:
            logger.info("PR filter enabled (bot/CVE heuristics active)")

    def ingest(
        self,
        repo: str,
        since_days: int = 180,
        max_prs: int = 300,
        areas: list[str] | None = None,
        resume: bool = False,
    ) -> dict[str, int]:
        """Ingest PRs, reviews, and comments. Returns counts of ingested items.

        Args:
            repo: GitHub repo in ``owner/name`` format.
            since_days: Look-back window in days.
            max_prs: Maximum PRs to fetch from GitHub.
            areas: Optional area filters — only ingest PRs touching these areas.
            resume: If True, only ingest PRs created after the last successful ingest.
        """
        logger.info("Starting ingestion for %s (last %d days, max %d PRs)", repo, since_days, max_prs)
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)

        # Incremental: if resume, narrow cutoff to last ingest timestamp
        if resume:
            last_ts = self.db.get_last_ingest(repo)
            if last_ts:
                resume_cutoff = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                if resume_cutoff > cutoff:
                    cutoff = resume_cutoff
                    logger.info("Resuming from last ingest at %s", last_ts)

        # 1. Ingest ownership files
        mapper = RepoMapper(self.db, self.gh)
        ownership_count = mapper.ingest_ownership(repo)
        logger.info("Ingested %d ownership rules", ownership_count)

        # 2. Fetch closed/merged PRs
        prs = self.gh.list_prs(repo, state="closed", max_items=max_prs)
        logger.info("Fetched %d PRs from GitHub", len(prs))

        stats = {"prs": 0, "files": 0, "reviews": 0, "comments": 0, "ownership": ownership_count, "skipped_area": 0, "skipped_bot_cve": 0}
        latest_created: str = ""

        with self.db.bulk():
            for pr_data in prs:
                created_at = pr_data.get("created_at", "")
                if created_at:
                    pr_date = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    if pr_date < cutoff:
                        continue  # Skip PRs older than the window

                pr_number = pr_data["number"]

                # Bot/CVE PR filter: skip low-signal automated PRs
                skip, reason = self.classifier.should_skip(pr_data)
                if skip:
                    logger.debug("Skipping PR #%d (%s): %s", pr_number, pr_data.get("title", ""), reason)
                    stats["skipped_bot_cve"] += 1
                    continue

                # Fetch files early so we can filter by area
                try:
                    files = self.gh.get_pr_files(repo, pr_number)
                    file_records = [
                        {
                            "path": f.get("filename", ""),
                            "additions": f.get("additions", 0),
                            "deletions": f.get("deletions", 0),
                        }
                        for f in files
                    ]
                except Exception as e:
                    logger.warning("Failed to fetch files for PR #%d: %s", pr_number, e)
                    file_records = []

                # Area filter: skip PRs that don't touch requested areas
                if areas and file_records:
                    pr_areas = mapper.detect_areas([f["path"] for f in file_records])
                    if not any(a in pr_areas for a in areas):
                        stats["skipped_area"] += 1
                        continue

                logger.debug("Processing PR #%d: %s", pr_number, pr_data.get("title", ""))

                # Track latest created_at for incremental ingest
                if created_at > latest_created:
                    latest_created = created_at

                # Upsert PR
                labels = [lbl["name"] for lbl in pr_data.get("labels", [])]
                merged_at = pr_data.get("merged_at")
                pr_id = self.db.upsert_pr(
                    repo=repo,
                    number=pr_number,
                    title=pr_data.get("title", ""),
                    author=pr_data.get("user", {}).get("login", ""),
                    created_at=created_at,
                    merged_at=merged_at,
                    state="merged" if merged_at else pr_data.get("state", "closed"),
                    labels=labels,
                    body=pr_data.get("body", "") or "",
                )
                stats["prs"] += 1

                # Insert files
                if file_records:
                    self.db.insert_pr_files(pr_id, file_records)
                    stats["files"] += len(file_records)

                # Fetch reviews
                try:
                    reviews = self.gh.get_pr_reviews(repo, pr_number)
                    for review in reviews:
                        reviewer = review.get("user", {}).get("login", "")
                        if not reviewer:
                            continue
                        self.db.insert_review(
                            pr_id=pr_id,
                            reviewer=reviewer,
                            state=review.get("state", "COMMENTED"),
                            submitted_at=review.get("submitted_at", ""),
                        )
                        stats["reviews"] += 1
                except Exception as e:
                    logger.warning("Failed to fetch reviews for PR #%d: %s", pr_number, e)

                # Fetch review comments (line-level comments)
                try:
                    comments = self.gh.get_pr_review_comments(repo, pr_number)
                    for comment in comments:
                        reviewer = comment.get("user", {}).get("login", "")
                        if not reviewer:
                            continue
                        self.db.insert_review_comment(
                            pr_id=pr_id,
                            reviewer=reviewer,
                            body=comment.get("body", ""),
                            path=comment.get("path"),
                            line=comment.get("original_line") or comment.get("line"),
                            created_at=comment.get("created_at", ""),
                        )
                        stats["comments"] += 1
                except Exception as e:
                    logger.warning("Failed to fetch review comments for PR #%d: %s", pr_number, e)

        # Record last ingest timestamp for incremental runs
        if latest_created:
            self.db.set_last_ingest(repo, latest_created)

        logger.info(
            "Ingestion complete: %d PRs, %d files, %d reviews, %d comments "
            "(skipped %d by area filter, %d by bot/CVE filter)",
            stats["prs"], stats["files"], stats["reviews"], stats["comments"],
            stats["skipped_area"], stats["skipped_bot_cve"],
        )
        return stats
