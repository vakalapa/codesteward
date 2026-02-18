"""Configuration loading from YAML, env vars, and CLI defaults."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr

from codesteward.pr_filter import PRFilterConfig

DEFAULT_CONFIG_PATHS = [
    Path("codesteward.yaml"),
    Path.home() / ".codesteward" / "config.yaml",
]

DEFAULT_DB_PATH = Path.home() / ".codesteward" / "db.sqlite"


class LLMConfig(BaseModel):
    """LLM-specific configuration."""
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    max_diff_chars: int = 12000


class Config(BaseModel):
    repo: str = ""
    default_areas: list[str] = Field(default_factory=list)
    ingest_window_days: int = 180
    max_prs: int = 300
    reviewer_count: int = 5
    strict_evidence_mode: bool = True
    db_path: str = str(DEFAULT_DB_PATH)
    github_token: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    output_dir: str = "./out"
    redact_quotes: bool = False
    large_diff_threshold: int = 500
    llm: LLMConfig = Field(default_factory=LLMConfig)
    pr_filter: PRFilterConfig = Field(default_factory=PRFilterConfig)


def load_config(
    config_path: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> Config:
    """Load config from YAML file, env vars, and caller overrides (in that priority)."""
    raw: dict[str, Any] = {}

    # 1. Load from YAML file
    paths_to_try = [Path(config_path)] if config_path else DEFAULT_CONFIG_PATHS
    for p in paths_to_try:
        if p.exists():
            with open(p) as f:
                raw = yaml.safe_load(f) or {}
            break

    # 2. Env var overrides
    if tok := os.environ.get("GITHUB_TOKEN"):
        raw.setdefault("github_token", tok)
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        raw.setdefault("anthropic_api_key", key)
    if db := os.environ.get("CODESTEWARD_DB"):
        raw["db_path"] = db

    # 3. Caller overrides (CLI flags)
    if overrides:
        raw.update({k: v for k, v in overrides.items() if v is not None})

    return Config(**raw)
