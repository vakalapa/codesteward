"""Tests for configuration loading."""

import os
from pathlib import Path

import pytest

from codesteward.config import Config, LLMConfig, load_config


class TestConfigDefaults:
    def test_default_values(self) -> None:
        cfg = Config()
        assert cfg.repo == ""
        assert cfg.ingest_window_days == 180
        assert cfg.max_prs == 300
        assert cfg.reviewer_count == 5
        assert cfg.strict_evidence_mode is True
        assert cfg.redact_quotes is False
        assert cfg.large_diff_threshold == 500
        assert cfg.github_token.get_secret_value() == ""
        assert cfg.anthropic_api_key.get_secret_value() == ""

    def test_llm_defaults(self) -> None:
        cfg = Config()
        assert cfg.llm.model == "claude-sonnet-4-20250514"
        assert cfg.llm.max_tokens == 4096
        assert cfg.llm.max_diff_chars == 12000

    def test_secret_str_masking(self) -> None:
        cfg = Config(github_token="ghp_secret123")
        # SecretStr should not expose value in repr/str
        assert "ghp_secret123" not in repr(cfg)
        assert "ghp_secret123" not in str(cfg)
        # But get_secret_value() should work
        assert cfg.github_token.get_secret_value() == "ghp_secret123"


class TestLoadConfig:
    def test_load_from_yaml(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(
            "repo: owner/name\n"
            "reviewer_count: 10\n"
            "redact_quotes: true\n"
            "llm:\n"
            "  model: claude-opus-4-20250514\n"
            "  max_tokens: 8192\n"
        )
        cfg = load_config(config_path=str(yaml_path))
        assert cfg.repo == "owner/name"
        assert cfg.reviewer_count == 10
        assert cfg.redact_quotes is True
        assert cfg.llm.model == "claude-opus-4-20250514"
        assert cfg.llm.max_tokens == 8192

    def test_env_var_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        cfg = load_config()
        assert cfg.github_token.get_secret_value() == "ghp_test_token"
        assert cfg.anthropic_api_key.get_secret_value() == "sk-test-key"

    def test_cli_overrides_take_priority(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("repo: yaml/repo\nreviewer_count: 3\n")
        cfg = load_config(
            config_path=str(yaml_path),
            overrides={"repo": "cli/repo", "reviewer_count": 7},
        )
        assert cfg.repo == "cli/repo"
        assert cfg.reviewer_count == 7

    def test_none_overrides_ignored(self) -> None:
        cfg = load_config(overrides={"repo": None, "max_prs": None})
        assert cfg.repo == ""  # default, not None

    def test_empty_yaml(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("")
        cfg = load_config(config_path=str(yaml_path))
        assert cfg.repo == ""

    def test_missing_yaml_uses_defaults(self) -> None:
        cfg = load_config(config_path="/nonexistent/path.yaml")
        assert cfg.repo == ""
        assert cfg.reviewer_count == 5
