"""Review simulation: generates per-reviewer reviews using Claude API or heuristic fallback."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from codesteward.evidence import EvidenceValidator
from codesteward.schemas import (
    ChangeContext,
    Evidence,
    EvidenceType,
    ReviewComment,
    ReviewerReview,
    ReviewerSkillCard,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Claude API-based simulation
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_TEMPLATE = """\
You are simulating a code reviewer named "{reviewer}" for a GitHub pull request.

## Your Reviewer Persona
- Focus areas (0-1 weights): {focus_weights}
- Blocking threshold: {blocking_threshold} (how often you request changes vs. just comment)
- Common blockers you typically flag: {common_blockers}
- Style preferences: {style_preferences}
- Evidence you typically ask for: {evidence_preferences}
- Recent interests: {recent_interests}
- Approval rate: {approval_rate:.0%}
- Avg comments per review: {avg_comments:.1f}

## Your Task
Review the PR diff below as this reviewer persona. Produce a structured JSON response.

## Rules
1. Every comment MUST include an `evidence` object with:
   - type: "diff" (file+line reference), "doc" (repo doc reference), or "history" (prior PR reference)
   - ref: specific reference string (e.g., "src/foo.py:42" or "CONTRIBUTING.md#style")
   - snippet: the relevant code/text snippet
2. If you cannot provide evidence for a concern, convert it to a `question` instead of a claim.
3. Stay in character for this reviewer persona. Focus on their areas of expertise.
4. Be specific and actionable. No vague comments.

## Output Format (JSON)
{{
  "summary_bullets": ["bullet 1", "bullet 2", "bullet 3"],
  "verdict": "approve" | "request-changes" | "comment",
  "comments": [
    {{
      "kind": "blocker" | "suggestion" | "missing-test" | "docs-needed" | "question",
      "body": "description of the issue",
      "file": "path/to/file.py",
      "line": 42,
      "evidence": {{
        "type": "diff" | "doc" | "history",
        "ref": "path/to/file.py:42",
        "snippet": "relevant code snippet"
      }},
      "confidence": 0.9
    }}
  ]
}}
"""

USER_PROMPT_TEMPLATE = """\
## PR Info
- Repository: {repo}
- PR: #{pr_number} {pr_title}
- Areas: {areas}
- Risk flags: {risk_flags}

## Changed Files
{file_summary}

## Diff
```diff
{diff_content}
```

Review this PR as the "{reviewer}" persona. Return ONLY valid JSON matching the specified format.
"""


class ReviewSimulator:
    """Generates simulated reviews using Claude API with heuristic fallback."""

    def __init__(
        self,
        anthropic_api_key: str = "",
        strict_evidence: bool = True,
        llm_model: str = "claude-sonnet-4-20250514",
        llm_max_tokens: int = 4096,
        max_diff_chars: int = 12000,
    ) -> None:
        self.strict_evidence = strict_evidence
        self._evidence_validator = EvidenceValidator(strict=strict_evidence)
        self.llm_model = llm_model
        self.llm_max_tokens = llm_max_tokens
        self.max_diff_chars = max_diff_chars
        self.client = None
        if anthropic_api_key:
            try:
                import anthropic
                self.client = anthropic.Anthropic(api_key=anthropic_api_key)
                logger.info("Claude API client initialized")
            except ImportError:
                logger.warning("anthropic package not installed; falling back to heuristics")
            except Exception as e:
                logger.warning("Failed to init Claude client: %s; falling back to heuristics", e)

    def simulate_review(
        self,
        ctx: ChangeContext,
        diff_text: str,
        card: ReviewerSkillCard,
    ) -> ReviewerReview:
        """Simulate a single reviewer's review."""
        if self.client:
            try:
                return self._simulate_with_llm(ctx, diff_text, card)
            except Exception as e:
                logger.warning("LLM simulation failed for %s: %s. Falling back to heuristics.", card.reviewer, e)

        return self._simulate_heuristic(ctx, diff_text, card)

    def simulate_all(
        self,
        ctx: ChangeContext,
        diff_text: str,
        cards: list[ReviewerSkillCard],
    ) -> list[ReviewerReview]:
        """Simulate reviews from all selected reviewers."""
        reviews: list[ReviewerReview] = []
        for card in cards:
            logger.info("Simulating review by %s", card.reviewer)
            review = self.simulate_review(ctx, diff_text, card)
            reviews.append(review)
        return reviews

    # ------------------------------------------------------------------
    # LLM-based simulation
    # ------------------------------------------------------------------

    def _simulate_with_llm(
        self,
        ctx: ChangeContext,
        diff_text: str,
        card: ReviewerSkillCard,
    ) -> ReviewerReview:
        """Use Claude API to generate a reviewer simulation."""
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            reviewer=card.reviewer,
            focus_weights=card.focus_weights.model_dump(),
            blocking_threshold=card.blocking_threshold.value,
            common_blockers=", ".join(card.common_blockers) or "none identified",
            style_preferences=", ".join(card.style_preferences) or "none identified",
            evidence_preferences=", ".join(card.evidence_preferences) or "none identified",
            recent_interests=", ".join(card.recent_interests) or "general",
            approval_rate=card.approval_rate,
            avg_comments=card.avg_comments_per_review,
        )

        file_summary = "\n".join(
            f"- {f.path} (+{f.additions}/-{f.deletions})" for f in ctx.changed_files
        )

        # Truncate diff to avoid token limits
        max_diff = self.max_diff_chars
        truncated_diff = diff_text[:max_diff]
        if len(diff_text) > max_diff:
            truncated_diff += f"\n... (diff truncated, {len(diff_text) - max_diff} chars omitted)"

        user_prompt = USER_PROMPT_TEMPLATE.format(
            repo=ctx.repo,
            pr_number=ctx.pr_number or "N/A",
            pr_title=ctx.pr_title,
            areas=", ".join(ctx.areas) or "unclassified",
            risk_flags=", ".join(ctx.risk_flags) or "none",
            file_summary=file_summary,
            diff_content=truncated_diff,
            reviewer=card.reviewer,
        )

        response = self.client.messages.create(  # type: ignore[union-attr]
            model=self.llm_model,
            max_tokens=self.llm_max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Extract JSON from response
        raw_text = response.content[0].text  # type: ignore[union-attr]
        review_data = _extract_json(raw_text)

        review = _parse_llm_response(card.reviewer, review_data)

        # Validate evidence on LLM-generated reviews
        if self.strict_evidence:
            review = self._evidence_validator.validate_review(review)

        return review

    # ------------------------------------------------------------------
    # Heuristic-based fallback simulation
    # ------------------------------------------------------------------

    def _simulate_heuristic(
        self,
        ctx: ChangeContext,
        diff_text: str,
        card: ReviewerSkillCard,
    ) -> ReviewerReview:
        """Generate a review using pattern matching and templates.

        Each reviewer persona focuses on their top focus areas and skips
        areas outside their expertise, producing differentiated output.
        """
        comments: list[ReviewComment] = []
        summary_bullets: list[str] = []

        files = ctx.changed_files
        all_paths = [f.path for f in files]
        total_add = sum(f.additions for f in files)
        total_del = sum(f.deletions for f in files)

        summary_bullets.append(
            f"PR touches {len(files)} file(s) with +{total_add}/-{total_del} lines"
        )

        focus = card.focus_weights

        # Determine this reviewer's top 2 focus areas to constrain what they check
        weights = focus.model_dump()
        sorted_focus = sorted(weights.items(), key=lambda x: -x[1])
        top_focus_names = [name for name, w in sorted_focus[:2] if w > 0.2]
        if not top_focus_names:
            top_focus_names = ["tests", "style"]  # generic fallback

        # Large diff warning (only from reviewers who care about style or have high blocking)
        if total_add + total_del > 500 and ("style" in top_focus_names or card.blocking_threshold.value == "high"):
            comments.append(ReviewComment(
                kind="suggestion",
                body="This is a large PR. Consider splitting into smaller, focused changes for easier review.",
                evidence=Evidence(
                    type=EvidenceType.DIFF,
                    ref=f"{len(files)} files changed",
                    snippet=f"+{total_add}/-{total_del} lines across {len(files)} files",
                ),
            ))

        # Track which missing-test files we've already flagged (cap at 3 per reviewer)
        missing_test_count = 0
        MAX_MISSING_TESTS = 3

        for cf in files:
            path = cf.path
            patch = cf.patch

            # Test-focused reviewer: flag missing tests (capped)
            if "tests" in top_focus_names and missing_test_count < MAX_MISSING_TESTS:
                if not _has_corresponding_test(path, all_paths):
                    if not _is_test_file(path) and not _is_doc_file(path) and not _is_config_file(path):
                        comments.append(ReviewComment(
                            kind="missing-test",
                            body=f"No corresponding test file found for `{path}`. Consider adding unit tests covering the new/changed logic.",
                            file=path,
                            evidence=Evidence(
                                type=EvidenceType.DIFF,
                                ref=path,
                                snippet=f"Changed file without test coverage: {path}",
                            ),
                        ))
                        missing_test_count += 1

            # Test-focused reviewer: analyze test file quality
            if "tests" in top_focus_names and _is_test_file(path) and patch:
                test_issues = _scan_test_quality(path, patch)
                comments.extend(test_issues)

            # Security-focused checks
            if "security" in top_focus_names and patch:
                sec_issues = _scan_security_patterns(path, patch)
                comments.extend(sec_issues)

            # API-focused checks
            if "api" in top_focus_names or "backward_compat" in top_focus_names:
                if re.search(r"(api|proto|schema|swagger|openapi|types\.go|u8proto)", path, re.I):
                    summary_bullets.append(f"API surface change detected in `{path}`")
                    comments.append(ReviewComment(
                        kind="blocker" if card.blocking_threshold.value == "high" else "suggestion",
                        body=f"API surface change in `{path}`. Ensure backward compatibility and version bump if needed.",
                        file=path,
                        evidence=Evidence(
                            type=EvidenceType.DIFF,
                            ref=path,
                            snippet=f"API file modified: {path}",
                        ),
                    ))
                # Check for exported type/function changes (Go-specific)
                if patch and ("api" in top_focus_names or "backward_compat" in top_focus_names):
                    api_issues = _scan_api_changes(path, patch)
                    comments.extend(api_issues)

            # Style-focused checks
            if "style" in top_focus_names and patch:
                style_issues = _scan_style_patterns(path, patch)
                comments.extend(style_issues)

            # Perf-focused checks
            if "perf" in top_focus_names and patch:
                perf_issues = _scan_perf_patterns(path, patch)
                comments.extend(perf_issues)

            # Docs-focused: flag doc file changes
            if "docs" in top_focus_names and _is_doc_file(path):
                summary_bullets.append(f"Documentation update in `{path}`")

            # General code quality checks (any reviewer)
            if patch:
                quality_issues = _scan_code_quality(path, patch, top_focus_names)
                comments.extend(quality_issues)

        # Docs-focused reviewer: check for missing docs on non-trivial changes
        if "docs" in top_focus_names and total_add > 50:
            has_doc_change = any(_is_doc_file(f.path) for f in files)
            if not has_doc_change:
                comments.append(ReviewComment(
                    kind="docs-needed",
                    body="This PR has significant code changes but no documentation updates. Consider adding or updating docs/comments.",
                    evidence=Evidence(
                        type=EvidenceType.DIFF,
                        ref=f"{total_add} additions",
                        snippet=f"+{total_add} lines without doc changes",
                    ),
                ))

        # Backward-compat focused: look for removed exports, changed signatures
        if "backward_compat" in top_focus_names:
            compat_issues = _scan_compat_changes(files)
            comments.extend(compat_issues)

        # Persona-flavored summary bullet
        focus_desc = " and ".join(top_focus_names).replace("_", " ")
        areas_str = ", ".join(ctx.areas[:3]) if ctx.areas else "general"
        summary_bullets.append(f"Reviewed with focus on **{focus_desc}** across area(s): {areas_str}")

        # Add a personality-flavored note from quote bank or common blockers
        if card.common_blockers:
            summary_bullets.append(f"Watch list: {', '.join(card.common_blockers[:3])}")

        # Cap comments by kind
        blockers = [c for c in comments if c.kind == "blocker"]
        missing_tests = [c for c in comments if c.kind == "missing-test"]
        suggestions = [c for c in comments if c.kind == "suggestion"]
        questions = [c for c in comments if c.kind == "question"]
        docs_needed = [c for c in comments if c.kind == "docs-needed"]
        comments = blockers[:5] + missing_tests[:3] + suggestions[:4] + docs_needed[:2] + questions[:2]

        # Determine verdict based on reviewer personality
        if blockers:
            verdict = "request-changes"
        elif card.blocking_threshold.value == "high" and (len(missing_tests) > 1 or len(comments) > 3):
            verdict = "request-changes"
        elif card.blocking_threshold.value == "medium" and len(blockers) > 0:
            verdict = "request-changes"
        elif len(comments) == 0:
            verdict = "approve"
        else:
            verdict = "comment"

        # Enforce evidence grounding via validation pipeline
        if self.strict_evidence:
            comments = self._evidence_validator.validate_comments(comments)

        return ReviewerReview(
            reviewer=card.reviewer,
            category=card.blocking_threshold.value,
            summary_bullets=summary_bullets[:4],
            comments=comments,
            verdict=verdict,
        )


# ---------------------------------------------------------------------------
# Heuristic scanners
# ---------------------------------------------------------------------------

def _scan_security_patterns(path: str, patch: str) -> list[ReviewComment]:
    """Scan diff patch for common security anti-patterns (Python + Go aware)."""
    issues: list[ReviewComment] = []
    lines = patch.split("\n")

    for i, line in enumerate(lines):
        if not line.startswith("+"):
            continue
        content = line[1:]

        # Hardcoded secrets
        if re.search(r"(password|secret|token|api_key)\s*=\s*['\"][^'\"]+['\"]", content, re.I):
            issues.append(ReviewComment(
                kind="blocker",
                body="Possible hardcoded secret/credential detected. Use environment variables or a secrets manager.",
                file=path,
                line=i + 1,
                evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:80]),
            ))

        # SQL injection (Python)
        if re.search(r"(f['\"].*SELECT|\.format\(.*SELECT|%s.*SELECT)", content, re.I):
            issues.append(ReviewComment(
                kind="blocker",
                body="Possible SQL injection vector. Use parameterized queries.",
                file=path,
                line=i + 1,
                evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:80]),
            ))

        # Eval usage (Python)
        if re.search(r"\beval\s*\(", content):
            issues.append(ReviewComment(
                kind="blocker",
                body="`eval()` usage detected. This is a security risk. Consider safer alternatives.",
                file=path,
                line=i + 1,
                evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:80]),
            ))

        # Go: unsafe.Pointer usage
        if re.search(r"unsafe\.Pointer", content):
            issues.append(ReviewComment(
                kind="suggestion",
                body="`unsafe.Pointer` usage detected. Ensure this is necessary and well-documented — unsafe code bypasses Go's type safety.",
                file=path,
                line=i + 1,
                evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:80]),
            ))

        # Go: fmt.Sprintf used for building queries/commands
        if re.search(r'fmt\.Sprintf\s*\(.*(%s|%d|%v).*\)', content) and re.search(r'(query|exec|command|sql|cmd)', content, re.I):
            issues.append(ReviewComment(
                kind="blocker",
                body="String formatting used to build a query/command. This may be an injection vector. Use parameterized APIs.",
                file=path,
                line=i + 1,
                evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:80]),
            ))

        # Disabled TLS verification
        if re.search(r"InsecureSkipVerify\s*:\s*true|verify\s*=\s*False|VERIFY_NONE", content):
            issues.append(ReviewComment(
                kind="blocker",
                body="TLS verification disabled. This should not reach production — it enables man-in-the-middle attacks.",
                file=path,
                line=i + 1,
                evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:80]),
            ))

    return issues[:3]  # cap per file


def _scan_style_patterns(path: str, patch: str) -> list[ReviewComment]:
    """Scan for common style issues."""
    issues: list[ReviewComment] = []
    lines = patch.split("\n")

    for i, line in enumerate(lines):
        if not line.startswith("+"):
            continue
        content = line[1:]

        # TODO/FIXME left behind
        if re.search(r"\b(TODO|FIXME|HACK|XXX)\b", content):
            issues.append(ReviewComment(
                kind="suggestion",
                body=f"TODO/FIXME comment found. Is this intentional for this PR, or should it be addressed?",
                file=path,
                line=i + 1,
                evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:80]),
            ))

        # Very long lines
        if len(content) > 120:
            issues.append(ReviewComment(
                kind="suggestion",
                body=f"Line exceeds 120 characters ({len(content)} chars). Consider breaking it up.",
                file=path,
                line=i + 1,
                evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:80]),
            ))

    return issues[:2]  # cap per file


def _scan_perf_patterns(path: str, patch: str) -> list[ReviewComment]:
    """Scan for potential performance issues (Python + Go aware)."""
    issues: list[ReviewComment] = []
    lines = patch.split("\n")

    for i, line in enumerate(lines):
        if not line.startswith("+"):
            continue
        content = line[1:]

        # N+1 query/call pattern (Python)
        if re.search(r"for.*in.*:\s*$", content) and i + 1 < len(lines):
            next_line = lines[i + 1][1:] if lines[i + 1].startswith("+") else ""
            if re.search(r"(\.query|\.execute|\.fetch|\.get\(|requests\.|http\.)", next_line):
                issues.append(ReviewComment(
                    kind="suggestion",
                    body="Possible N+1 pattern: I/O call inside a loop. Consider batching.",
                    file=path,
                    line=i + 1,
                    evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:80]),
                ))

        # Go: allocations in hot path (append in loop, make in loop)
        if path.endswith(".go"):
            if re.search(r"for\s+.*{", content):
                # Check next lines for allocations
                for j in range(i + 1, min(i + 5, len(lines))):
                    if j < len(lines) and lines[j].startswith("+"):
                        nl = lines[j][1:]
                        if re.search(r"\bmake\s*\(|append\s*\(.*make", nl):
                            issues.append(ReviewComment(
                                kind="suggestion",
                                body="Allocation (`make`/`append`) inside a loop. Consider pre-allocating the slice/map before the loop.",
                                file=path,
                                line=j + 1,
                                evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{j+1}", snippet=nl.strip()[:80]),
                            ))
                            break

        # Go: sync.Mutex as value (should be pointer or embedded)
        if path.endswith(".go"):
            if re.search(r"sync\.Mutex\b(?!.*\*)", content) and re.search(r"=\s*sync\.Mutex", content):
                issues.append(ReviewComment(
                    kind="suggestion",
                    body="`sync.Mutex` should not be copied. Ensure it is used by pointer or embedded in a struct.",
                    file=path,
                    line=i + 1,
                    evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:80]),
                ))

    return issues[:2]


def _has_corresponding_test(path: str, all_paths: list[str]) -> bool:
    """Check if a test file exists for the given source file in the changed set."""
    if _is_test_file(path) or _is_doc_file(path):
        return True

    base = re.sub(r"\.(py|js|ts|go|rs|java)$", "", path.split("/")[-1])
    test_patterns = [
        f"test_{base}",
        f"{base}_test",
        f"{base}.test",
        f"{base}.spec",
        f"test/{base}",
        f"tests/{base}",
    ]

    for tp in all_paths:
        for pat in test_patterns:
            if pat in tp:
                return True
    return False


def _is_test_file(path: str) -> bool:
    return bool(re.search(r"(test|spec|_test\.|\.test\.|__tests__)", path, re.I))


def _is_doc_file(path: str) -> bool:
    return bool(re.search(r"(\.md$|\.rst$|\.txt$|docs/|README)", path, re.I))


def _is_config_file(path: str) -> bool:
    return bool(re.search(r"(\.yaml$|\.yml$|\.json$|\.toml$|\.cfg$|\.ini$|\.conf$|Makefile|Dockerfile|\.github/)", path, re.I))


def _scan_api_changes(path: str, patch: str) -> list[ReviewComment]:
    """Detect exported type/function signature changes (Go-aware)."""
    issues: list[ReviewComment] = []
    lines = patch.split("\n")

    for i, line in enumerate(lines):
        if not line.startswith("+"):
            continue
        content = line[1:]

        # New exported Go functions/types (uppercase first letter after func/type keyword)
        if re.search(r"^func\s+[A-Z]", content) or re.search(r"^func\s+\([^)]+\)\s+[A-Z]", content):
            issues.append(ReviewComment(
                kind="suggestion",
                body=f"New exported function added. Verify this is intentional API surface expansion and document the public interface.",
                file=path,
                line=i + 1,
                evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:100]),
            ))

        if re.search(r"^type\s+[A-Z]\w+\s+(struct|interface)", content):
            issues.append(ReviewComment(
                kind="suggestion",
                body=f"New exported type defined. Ensure naming follows project conventions and consider adding godoc.",
                file=path,
                line=i + 1,
                evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:100]),
            ))

        # New const/var blocks with exported names
        if re.search(r"^(const|var)\s+[A-Z]", content):
            issues.append(ReviewComment(
                kind="suggestion",
                body=f"New exported constant/variable. Verify naming and add documentation comment.",
                file=path,
                line=i + 1,
                evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:100]),
            ))

    # Detect removed exported symbols (breaking change)
    for i, line in enumerate(lines):
        if not line.startswith("-"):
            continue
        content = line[1:]
        if re.search(r"^func\s+[A-Z]", content) or re.search(r"^type\s+[A-Z]\w+\s+(struct|interface)", content):
            issues.append(ReviewComment(
                kind="blocker",
                body=f"Exported symbol removed — this is a breaking API change. Ensure this is intentional and deprecation was announced.",
                file=path,
                line=i + 1,
                evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:100]),
            ))

    return issues[:3]


def _scan_test_quality(path: str, patch: str) -> list[ReviewComment]:
    """Analyze test file patches for quality patterns."""
    issues: list[ReviewComment] = []
    lines = patch.split("\n")
    added_lines = [l[1:] for l in lines if l.startswith("+")]
    added_text = "\n".join(added_lines)

    # Detect missing assertions in test functions
    has_test_func = any(re.search(r"func\s+Test|def\s+test_|it\(|describe\(", l) for l in added_lines)
    has_assertion = any(re.search(r"assert|expect|require\.|should|Equal|NotNil|Error|NoError", l) for l in added_lines)
    if has_test_func and not has_assertion and len(added_lines) > 5:
        issues.append(ReviewComment(
            kind="suggestion",
            body="Test function appears to lack assertions. Ensure the test validates expected behavior, not just that it runs without panic.",
            file=path,
            evidence=Evidence(type=EvidenceType.DIFF, ref=path, snippet="Test function without visible assertions"),
        ))

    # Detect hardcoded sleep in tests
    for i, line in enumerate(lines):
        if not line.startswith("+"):
            continue
        content = line[1:]
        if re.search(r"time\.Sleep|sleep\(|Thread\.sleep", content):
            issues.append(ReviewComment(
                kind="suggestion",
                body="Hardcoded `sleep` in test — consider using polling/retry with timeout for more reliable and faster tests.",
                file=path,
                line=i + 1,
                evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:80]),
            ))

    return issues[:2]


def _scan_code_quality(path: str, patch: str, focus_areas: list[str]) -> list[ReviewComment]:
    """General code quality checks applied regardless of reviewer persona."""
    issues: list[ReviewComment] = []
    lines = patch.split("\n")

    for i, line in enumerate(lines):
        if not line.startswith("+"):
            continue
        content = line[1:]

        # Detect panic() in non-test Go code
        if path.endswith(".go") and not _is_test_file(path):
            if re.search(r"\bpanic\s*\(", content):
                issues.append(ReviewComment(
                    kind="blocker",
                    body="`panic()` in production code. Return an error instead — panics crash the entire process.",
                    file=path,
                    line=i + 1,
                    evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:80]),
                ))

        # Detect ignored errors in Go (Go-specific: _ = someFunc())
        if path.endswith(".go"):
            if re.search(r"_\s*=\s*\w+\.\w+\(", content) or re.search(r"_\s*,\s*err\s*:?=|err\s*=.*;\s*_", content):
                pass  # too noisy — skip
            # Explicit error discard pattern
            if re.search(r"\berr\b.*=.*\(.*\)$", content) and not re.search(r"if\s+err", content):
                # Check next few lines for error handling
                next_lines = [lines[j][1:] if j < len(lines) and lines[j].startswith("+") else lines[j] if j < len(lines) else ""
                              for j in range(i + 1, min(i + 4, len(lines)))]
                if not any(re.search(r"if\s+err|return.*err", nl) for nl in next_lines):
                    issues.append(ReviewComment(
                        kind="suggestion",
                        body="Error return value may not be checked. Ensure errors are handled or explicitly documented as ignorable.",
                        file=path,
                        line=i + 1,
                        evidence=Evidence(type=EvidenceType.DIFF, ref=f"{path}:{i+1}", snippet=content.strip()[:80]),
                    ))

        # Detect large function additions (>50 added lines in one hunk)
        # This is tracked separately

    # Check for very large file changes (might need splitting)
    added_count = sum(1 for l in lines if l.startswith("+"))
    if added_count > 100 and not _is_test_file(path) and not _is_config_file(path):
        issues.append(ReviewComment(
            kind="suggestion",
            body=f"`{path}` has {added_count}+ added lines. Consider whether this file change can be broken into smaller, independently reviewable pieces.",
            file=path,
            evidence=Evidence(type=EvidenceType.DIFF, ref=path, snippet=f"+{added_count} lines in single file"),
        ))

    return issues[:2]  # cap per file


def _scan_compat_changes(files: list) -> list[ReviewComment]:
    """Look for signals of backward-incompatible changes across all files."""
    issues: list[ReviewComment] = []
    for cf in files:
        if not cf.patch:
            continue
        lines = cf.patch.split("\n")
        removed_exports = 0
        for line in lines:
            if line.startswith("-") and not line.startswith("---"):
                content = line[1:]
                if re.search(r"^func\s+[A-Z]|^type\s+[A-Z]|^const\s+[A-Z]|^var\s+[A-Z]", content):
                    removed_exports += 1
        if removed_exports > 0:
            issues.append(ReviewComment(
                kind="blocker",
                body=f"`{cf.path}` removes {removed_exports} exported symbol(s). This may break downstream consumers. Verify deprecation notices were issued.",
                file=cf.path,
                evidence=Evidence(type=EvidenceType.DIFF, ref=cf.path, snippet=f"{removed_exports} exported symbols removed"),
            ))
    return issues[:3]


def _enforce_evidence(comments: list[ReviewComment]) -> list[ReviewComment]:
    """Convert comments without evidence into questions.

    .. deprecated::
        Use :class:`~codesteward.evidence.EvidenceValidator` instead.
        This function is kept for backward compatibility with existing callers.
    """
    validator = EvidenceValidator(strict=True)
    return validator.validate_comments(comments)


# ---------------------------------------------------------------------------
# LLM response parsing
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict[str, Any]:
    """Extract JSON from LLM response text (handles markdown code blocks)."""
    # Try to find JSON in code blocks
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()

    # Try direct parse
    try:
        return json.loads(text)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        pass

    # Try to find first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start : end + 1])  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse JSON from LLM response")
    return {"summary_bullets": ["Failed to parse LLM response"], "verdict": "comment", "comments": []}


def _parse_llm_response(reviewer: str, data: dict[str, Any]) -> ReviewerReview:
    """Parse structured JSON from LLM into ReviewerReview."""
    comments: list[ReviewComment] = []
    for c in data.get("comments", []):
        evidence = None
        if ev := c.get("evidence"):
            try:
                evidence = Evidence(
                    type=EvidenceType(ev.get("type", "diff")),
                    ref=ev.get("ref", ""),
                    snippet=ev.get("snippet", ""),
                )
            except ValueError:
                evidence = None

        comments.append(ReviewComment(
            kind=c.get("kind", "suggestion"),
            body=c.get("body", ""),
            file=c.get("file", ""),
            line=c.get("line"),
            evidence=evidence,
            confidence=c.get("confidence", 0.8),
        ))

    return ReviewerReview(
        reviewer=reviewer,
        summary_bullets=data.get("summary_bullets", []),
        comments=comments,
        verdict=data.get("verdict", "comment"),
    )
