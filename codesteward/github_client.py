"""GitHub REST API client with rate-limit handling."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.github.com"
PER_PAGE = 100
MAX_RETRIES = 3
BACKOFF_FACTOR = 2.0


class GitHubClientError(Exception):
    pass


class RateLimitError(GitHubClientError):
    pass


class GitHubClient:
    """Minimal GitHub REST API client with automatic rate-limit backoff."""

    def __init__(self, token: str) -> None:
        if not token:
            raise GitHubClientError(
                "GitHub token is required. Set GITHUB_TOKEN env var or config github_token."
            )
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    # ------------------------------------------------------------------
    # Core request
    # ------------------------------------------------------------------

    def _request(
        self, method: str, path: str, params: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        url = f"{API_BASE}{path}" if path.startswith("/") else path
        for attempt in range(1, MAX_RETRIES + 1):
            resp = self.session.request(method, url, params=params, **kwargs)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                reset_at = int(resp.headers.get("X-RateLimit-Reset", 0))
                wait = max(reset_at - int(time.time()), 0) + 1
                logger.warning("Rate limited. Sleeping %ds (attempt %d/%d)", wait, attempt, MAX_RETRIES)
                time.sleep(min(wait, 120))  # cap wait at 2 min
                continue
            if resp.status_code in (502, 503) and attempt < MAX_RETRIES:
                time.sleep(BACKOFF_FACTOR ** attempt)
                continue
            resp.raise_for_status()
        raise GitHubClientError(f"Request failed after {MAX_RETRIES} retries: {path}")

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def _paginate(
        self, path: str, params: dict[str, Any] | None = None, max_items: int = 1000
    ) -> list[Any]:
        """Paginate through a GitHub list endpoint."""
        params = dict(params or {})
        params.setdefault("per_page", PER_PAGE)
        items: list[Any] = []
        page = 1
        while len(items) < max_items:
            params["page"] = page
            data = self._get(path, params=params)
            if not data:
                break
            items.extend(data)
            if len(data) < PER_PAGE:
                break
            page += 1
        return items[:max_items]

    # ------------------------------------------------------------------
    # Repo helpers
    # ------------------------------------------------------------------

    def get_file_content(self, repo: str, path: str, ref: str = "HEAD") -> str | None:
        """Get raw file content from the repo. Returns None if not found."""
        url = f"{API_BASE}/repos/{repo}/contents/{path}"
        resp = self.session.get(url, params={"ref": ref}, headers={"Accept": "application/vnd.github.raw+json"})
        if resp.status_code == 200:
            return resp.text
        return None

    # ------------------------------------------------------------------
    # PRs
    # ------------------------------------------------------------------

    def list_prs(
        self,
        repo: str,
        state: str = "closed",
        sort: str = "updated",
        direction: str = "desc",
        since: str | None = None,
        max_items: int = 300,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"state": state, "sort": sort, "direction": direction}
        if since:
            params["since"] = since  # only for issues endpoint; PRs don't support since directly
        return self._paginate(f"/repos/{repo}/pulls", params=params, max_items=max_items)

    def get_pr(self, repo: str, number: int) -> dict[str, Any]:
        return self._get(f"/repos/{repo}/pulls/{number}")

    def get_pr_files(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self._paginate(f"/repos/{repo}/pulls/{number}/files", max_items=500)

    def get_pr_diff(self, repo: str, number: int) -> str:
        """Get the raw diff for a PR."""
        url = f"{API_BASE}/repos/{repo}/pulls/{number}"
        resp = self.session.get(url, headers={"Accept": "application/vnd.github.diff"})
        resp.raise_for_status()
        return resp.text

    def get_pr_reviews(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self._paginate(f"/repos/{repo}/pulls/{number}/reviews", max_items=200)

    def get_pr_review_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self._paginate(f"/repos/{repo}/pulls/{number}/comments", max_items=500)

    # ------------------------------------------------------------------
    # Rate-limit info
    # ------------------------------------------------------------------

    def rate_limit(self) -> dict[str, Any]:
        return self._get("/rate_limit")
