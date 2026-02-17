"""Map changed files to areas, owners, and risk flags using CODEOWNERS/OWNERS files."""

from __future__ import annotations

import logging
import re
from typing import Any

from codesteward.db import Database
from codesteward.github_client import GitHubClient
from codesteward.schemas import ChangeContext, ChangedFile, OwnershipEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path-prefix heuristics for area labelling
# ---------------------------------------------------------------------------

AREA_HEURISTICS: list[tuple[str, str]] = [
    (r"^api/", "sig-api"),
    (r"^pkg/api", "sig-api"),
    (r"^cmd/", "sig-cli"),
    (r"^pkg/kubectl", "sig-cli"),
    (r"^test/", "sig-testing"),
    (r"^hack/", "sig-testing"),
    (r"^docs/", "sig-docs"),
    (r"^vendor/", "area-dependency"),
    (r"^go\.mod$", "area-dependency"),
    (r"^go\.sum$", "area-dependency"),
    (r"requirements.*\.txt$", "area-dependency"),
    (r"^\.github/", "area-ci"),
    (r"^Makefile", "area-build"),
    (r"^Dockerfile", "area-build"),
    (r"^deploy/", "sig-cluster-lifecycle"),
    (r"^staging/", "sig-api-machinery"),
    (r"^pkg/controller", "sig-apps"),
    (r"^pkg/scheduler", "sig-scheduling"),
    (r"^pkg/proxy", "sig-network"),
    (r"^pkg/kubelet", "sig-node"),
    (r"^plugin/", "sig-storage"),
    (r"^pkg/volume", "sig-storage"),
    (r"^pkg/security", "sig-auth"),
    (r"^pkg/auth", "sig-auth"),
    (r"^cluster/", "sig-cluster-lifecycle"),
    # Generic language patterns
    (r"^src/", "area-core"),
    (r"^lib/", "area-core"),
    (r"^tests?/", "area-testing"),
    (r"^spec/", "area-testing"),
]

RISK_PATTERNS: list[tuple[str, str]] = [
    (r"(^api/|openapi|swagger|proto)", "api-surface"),
    (r"(security|auth|crypto|tls|cert|token|password|secret)", "security"),
    (r"(bench|perf|optim|cache|pool|buffer)", "perf"),
    (r"(compat|deprecat|migration|upgrade|breaking)", "compat"),
    (r"(window|wsl|win32|ntfs)", "windows"),
    (r"(config|\.env|\.yaml|\.toml|settings)", "config-change"),
    (r"(require|depend|go\.mod|go\.sum|package\.json|Cargo\.toml)", "new-dependency"),
]


# ---------------------------------------------------------------------------
# CODEOWNERS Parser
# ---------------------------------------------------------------------------

def parse_codeowners(content: str) -> list[OwnershipEntry]:
    """Parse a GitHub CODEOWNERS file into ownership entries."""
    entries: list[OwnershipEntry] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        pattern = parts[0]
        owners = [o.lstrip("@") for o in parts[1:] if not o.startswith("#")]
        if owners:
            entries.append(OwnershipEntry(path_pattern=pattern, owners=owners, source="CODEOWNERS"))
    return entries


# ---------------------------------------------------------------------------
# Kubernetes-style OWNERS parser (simplified)
# ---------------------------------------------------------------------------

def parse_owners_file(content: str, directory: str = "") -> list[OwnershipEntry]:
    """Parse a Kubernetes-style OWNERS file (YAML-like).

    Supports:
      approvers:
        - user1
        - user2
      reviewers:
        - user3
    """
    entries: list[OwnershipEntry] = []
    current_section: str | None = None
    owners: list[str] = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("approvers:") or line.startswith("reviewers:"):
            # Flush previous section
            if current_section and owners:
                pattern = f"{directory}/**" if directory else "**"
                entries.append(
                    OwnershipEntry(path_pattern=pattern, owners=list(owners), source="OWNERS")
                )
                owners = []
            current_section = line.split(":")[0]
            continue
        if line.startswith("- ") and current_section:
            user = line[2:].strip().strip('"').strip("'")
            if user:
                owners.append(user)

    # Flush last section
    if current_section and owners:
        pattern = f"{directory}/**" if directory else "**"
        entries.append(OwnershipEntry(path_pattern=pattern, owners=list(owners), source="OWNERS"))

    return entries


# ---------------------------------------------------------------------------
# RepoMapper
# ---------------------------------------------------------------------------

class RepoMapper:
    """Maps changed files to areas, risk flags, and likely reviewers."""

    def __init__(self, db: Database, gh: GitHubClient | None = None) -> None:
        self.db = db
        self.gh = gh

    def ingest_ownership(self, repo: str) -> int:
        """Fetch and store CODEOWNERS and OWNERS files from the repo."""
        if not self.gh:
            logger.warning("No GitHub client; skipping ownership ingestion")
            return 0

        self.db.clear_ownership(repo)
        count = 0

        # Try common CODEOWNERS locations
        for path in ["CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"]:
            content = self.gh.get_file_content(repo, path)
            if content:
                entries = parse_codeowners(content)
                for entry in entries:
                    for owner in entry.owners:
                        self.db.upsert_ownership(repo, entry.path_pattern, owner, "CODEOWNERS")
                        count += 1
                logger.info("Parsed %d ownership rules from %s", len(entries), path)
                break  # Only use the first CODEOWNERS found

        # Try root OWNERS file
        content = self.gh.get_file_content(repo, "OWNERS")
        if content:
            entries = parse_owners_file(content, "")
            for entry in entries:
                for owner in entry.owners:
                    self.db.upsert_ownership(repo, entry.path_pattern, owner, "OWNERS")
                    count += 1
            logger.info("Parsed %d ownership rules from OWNERS", len(entries))

        return count

    def detect_areas(self, paths: list[str]) -> set[str]:
        """Return the set of area labels for a list of file paths (no DB needed)."""
        areas: set[str] = set()
        for path in paths:
            for pattern, area in AREA_HEURISTICS:
                if re.search(pattern, path, re.IGNORECASE):
                    areas.add(area)
        return areas

    def build_change_context(
        self,
        repo: str,
        changed_files: list[ChangedFile],
        pr_number: int | None = None,
        pr_title: str = "",
        pr_body: str = "",
        base_ref: str = "main",
        head_ref: str = "",
    ) -> ChangeContext:
        """Analyze changed files and build a ChangeContext."""
        areas = set[str]()
        risk_flags = set[str]()
        likely_reviewers = set[str]()
        relevant_docs: list[str] = []

        total_additions = sum(f.additions for f in changed_files)
        total_deletions = sum(f.deletions for f in changed_files)

        # Large diff flag
        if total_additions + total_deletions > 500:
            risk_flags.add("large-diff")

        # Check if test-only or docs-only
        all_paths = [f.path for f in changed_files]
        if all_paths and all(_is_test_file(p) for p in all_paths):
            risk_flags.add("test-only")
        if all_paths and all(_is_doc_file(p) for p in all_paths):
            risk_flags.add("docs-only")

        for cf in changed_files:
            # Area heuristics
            for pattern, area in AREA_HEURISTICS:
                if re.search(pattern, cf.path, re.IGNORECASE):
                    areas.add(area)

            # Risk flag heuristics
            for pattern, flag in RISK_PATTERNS:
                if re.search(pattern, cf.path, re.IGNORECASE):
                    risk_flags.add(flag)

            # Ownership lookup
            owners = self.db.get_owners_for_path(repo, cf.path)
            for o in owners:
                likely_reviewers.add(o["owner"])

        # Detect relevant docs
        for cf in changed_files:
            if _is_doc_file(cf.path):
                relevant_docs.append(cf.path)
        # Always suggest CONTRIBUTING.md if it might exist
        if not risk_flags & {"test-only", "docs-only"}:
            relevant_docs.append("CONTRIBUTING.md")

        # Historical reviewer lookup
        hist_reviewers = self.db.get_reviewers_for_paths(repo, all_paths, limit=10)
        for r in hist_reviewers:
            likely_reviewers.add(r["reviewer"])

        return ChangeContext(
            repo=repo,
            base_ref=base_ref,
            head_ref=head_ref,
            pr_number=pr_number,
            pr_title=pr_title,
            pr_body=pr_body,
            changed_files=changed_files,
            areas=sorted(areas),
            likely_reviewers=sorted(likely_reviewers),
            relevant_docs=relevant_docs,
            risk_flags=sorted(risk_flags),
        )


def _is_test_file(path: str) -> bool:
    return bool(re.search(r"(test|spec|_test\.|\.test\.|__tests__)", path, re.IGNORECASE))


def _is_doc_file(path: str) -> bool:
    return bool(re.search(r"(\.md$|\.rst$|\.txt$|docs/|doc/|README)", path, re.IGNORECASE))
