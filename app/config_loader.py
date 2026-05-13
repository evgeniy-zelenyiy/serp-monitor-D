"""Configuration loading utilities for the SERP monitor."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")


@dataclass(frozen=True)
class Settings:
    """Runtime settings composed from YAML and environment variables."""

    raw: dict[str, Any]
    dataforseo_login: str
    dataforseo_password: str
    openai_api_key: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None

    @property
    def database_path(self) -> Path:
        return Path(self.raw["project"]["database_path"])

    @property
    def screenshots_dir(self) -> Path:
        return Path(self.raw["project"]["screenshots_dir"])

    @property
    def entity_map_path(self) -> Path:
        return Path(self.raw["project"]["entity_map_path"])


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: os.getenv(match.group(1), match.group(2) or ""), value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def load_settings(config_path: str | Path = "config.yaml") -> Settings:
    """Load YAML config and secrets from the environment."""
    load_dotenv()
    with Path(config_path).open("r", encoding="utf-8") as handle:
        config = _expand_env(yaml.safe_load(handle))

    return Settings(
        raw=config,
        dataforseo_login=os.getenv("DATAFORSEO_LOGIN", ""),
        dataforseo_password=os.getenv("DATAFORSEO_PASSWORD", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
    )
