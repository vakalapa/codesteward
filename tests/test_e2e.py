"""End-to-end smoke test of the full pipeline: init -> ingest (seeded) -> profile -> review."""

import json
from pathlib import Path

import pytest

from codesteward.aggregator import MaintainerAggregator
from codesteward.db import Database
from codesteward.discovery import ReviewerDiscovery
from codesteward.profiler import ReviewerProfiler
from codesteward.render import render_markdown, render_json, write_outputs
from codesteward.repo_mapper import RepoMapper
from codesteward.schemas import ChangedFile, ReviewerSkillCard
from codesteward.simulator import ReviewSimulator


SAMPLE_DIFF = """\
diff --git a/src/api/handler.py b/src/api/handler.py
--- /dev/null
+++ b/src/api/handler.py
@@ -0,0 +1,15 @@
+import os
+
+API_KEY = "sk-secret-hardcoded-key"
+
+def handle_request(request):
+    user_input = request.get("query")
+    result = eval(user_input)
+    return {"status": "ok", "result": result}
+
+def get_config():
+    password = "hunter2"
+    return {"db_host": "localhost", "password": password}
+
+# TODO: add authentication
+# FIXME: remove hardcoded secrets
"""


@pytest.fixture
def seeded_db(tmp_path):
    """Create a DB with realistic seeded data."""
    db_path = tmp_path / "e2e.sqlite"
    db = Database(str(db_path))
    db.init_schema()
    repo = "test/repo"

    # Ownership
    db.upsert_ownership(repo, "src/api/*", "api-owner", "CODEOWNERS")
    db.upsert_ownership(repo, "src/*", "core-owner", "CODEOWNERS")

    # Seed PRs, reviews, comments
    for i in range(1, 11):
        pr_id = db.upsert_pr(
            repo, i, f"PR #{i}", f"author{i}", f"2024-0{min(i, 9):d}-01",
            f"2024-0{min(i, 9):d}-02", "merged", ["api", "bugfix"],
        )
        db.insert_pr_files(pr_id, [
            {"path": "src/api/handler.py", "additions": 10 * i, "deletions": 2 * i},
        ])
        # alice is a security hawk
        db.insert_review(pr_id, "alice", "CHANGES_REQUESTED" if i % 2 == 0 else "APPROVED", f"2024-0{min(i, 9):d}-01T12:00:00Z")
        db.insert_review_comment(pr_id, "alice", f"Check security token handling, auth validation needed (comment {i})", "src/api/handler.py", i * 3, f"2024-0{min(i, 9):d}-01T12:00:00Z")
        db.insert_review_comment(pr_id, "alice", f"This needs input sanitization to prevent injection (comment {i})", "src/api/handler.py", i * 3 + 1, f"2024-0{min(i, 9):d}-01T12:00:00Z")

        # bob is a test hawk
        db.insert_review(pr_id, "bob", "APPROVED", f"2024-0{min(i, 9):d}-01T14:00:00Z")
        db.insert_review_comment(pr_id, "bob", f"Test coverage needs improvement, add unit test for this path (comment {i})", "src/api/handler.py", i * 3 + 2, f"2024-0{min(i, 9):d}-01T14:00:00Z")

    yield db
    db.close()


class TestEndToEnd:
    def test_full_pipeline(self, seeded_db: Database, tmp_path) -> None:
        repo = "test/repo"

        # Step 1: Profile reviewers
        profiler = ReviewerProfiler(seeded_db)
        cards = profiler.profile_all(repo, top_n=10)
        assert len(cards) >= 2
        alice_card = next(c for c in cards if c.reviewer == "alice")
        bob_card = next(c for c in cards if c.reviewer == "bob")
        assert alice_card.total_reviews == 10
        assert bob_card.total_reviews == 10
        # Alice should have higher security focus
        assert alice_card.focus_weights.security > 0

        # Step 2: Build change context
        changed_files = [
            ChangedFile(
                path="src/api/handler.py",
                additions=15,
                deletions=0,
                patch=SAMPLE_DIFF,
            ),
        ]
        mapper = RepoMapper(seeded_db, gh=None)
        ctx = mapper.build_change_context(
            repo=repo,
            changed_files=changed_files,
            pr_number=99,
            pr_title="Add new API handler",
        )
        assert "api-owner" in ctx.likely_reviewers or "core-owner" in ctx.likely_reviewers

        # Step 3: Discover reviewers
        discovery = ReviewerDiscovery(seeded_db)
        reviewer_infos = discovery.discover(ctx, top_k=3)
        assert len(reviewer_infos) >= 2

        # Step 4: Simulate (heuristic mode, no API key)
        sim = ReviewSimulator(anthropic_api_key="", strict_evidence=True)
        reviews = sim.simulate_all(ctx, SAMPLE_DIFF, [alice_card, bob_card])
        assert len(reviews) == 2

        # Verify evidence grounding
        for review in reviews:
            for comment in review.comments:
                assert comment.evidence is not None or comment.kind == "question", \
                    f"Comment missing evidence and not a question: {comment.body}"

        # Step 5: Aggregate
        aggregator = MaintainerAggregator()
        summary = aggregator.aggregate(ctx, reviews)
        assert summary.verdict is not None
        assert len(summary.fix_plan) >= 0

        # Step 6: Render
        md = render_markdown(summary)
        assert "# Review Report" in md
        assert "Merge Verdict" in md

        json_str = render_json(summary)
        data = json.loads(json_str)
        assert "verdict" in data
        assert "reviewer_reviews" in data

        # Step 7: Write outputs
        out_dir = tmp_path / "output"
        md_path, json_path = write_outputs(summary, str(out_dir))
        assert md_path.exists()
        assert json_path.exists()
        assert len(md_path.read_text()) > 100
        assert len(json_path.read_text()) > 100

    def test_security_issues_detected(self, seeded_db: Database) -> None:
        """The heuristic scanner should catch hardcoded secrets and eval() in SAMPLE_DIFF."""
        repo = "test/repo"

        profiler = ReviewerProfiler(seeded_db)
        cards = profiler.profile_all(repo, top_n=5)
        alice_card = next(c for c in cards if c.reviewer == "alice")

        changed_files = [
            ChangedFile(
                path="src/api/handler.py",
                additions=15,
                deletions=0,
                patch=SAMPLE_DIFF,
            ),
        ]
        mapper = RepoMapper(seeded_db, gh=None)
        ctx = mapper.build_change_context(repo=repo, changed_files=changed_files, pr_number=99, pr_title="Test")

        sim = ReviewSimulator(anthropic_api_key="", strict_evidence=True)
        review = sim.simulate_review(ctx, SAMPLE_DIFF, alice_card)

        # Should detect at least one security issue (hardcoded secret or eval)
        blocker_bodies = " ".join(c.body.lower() for c in review.comments if c.kind == "blocker")
        assert "secret" in blocker_bodies or "credential" in blocker_bodies or "eval" in blocker_bodies
