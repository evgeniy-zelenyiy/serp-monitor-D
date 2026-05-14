"""Configuration loading utilities for the SERP monitor."""

from __future__ import annotations

import os
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")

DEFAULT_NEGATIVE_KEYWORDS_PT = [
    "fraude",
    "golpe",
    "scam",
    "investigação",
    "processo",
    "crime",
    "acusação",
    "reclamação",
    "lavagem",
    "irregularidade",
    "suspeita",
    "pirâmide",
    "denúncia",
    "risco",
    "bloqueio",
    "fraude financeira",
    "problema",
    "ilegal",
    "denúncia financeira",
    "reclame aqui",
]

DEFAULT_CONFIG: dict[str, Any] = {
    "project": {
        "database_path": "data/serp_history.sqlite3",
        "screenshots_dir": "data/screenshots",
        "entity_map_path": "data/entity_map.json",
    },
    "monitoring": {
        "provider": "serper",
        "queries": [],
        "country": "BR",
        "language": "pt",
        "location_name": "Brazil",
        "depth": 10,
        "demo_results_per_query": 3,
    },
    "sentiment": {
        "use_openai": True,
        "openai_model": "${OPENAI_MODEL:-gpt-4o-mini}",
        "negative_keywords_pt": DEFAULT_NEGATIVE_KEYWORDS_PT,
    },
    "screenshots": {
        "enabled": True,
        "max_per_run": 10,
        "timeout_ms": 30000,
        "full_page": True,
    },
    "telegram": {
        "enabled": True,
        "alert_on_sentiments": ["negative", "risky"],
        "max_message_mentions": 20,
        "send_screenshots": True,
    },
    "entity_map": {
        "enabled": True,
    },
}


@dataclass(frozen=True)
class Settings:
    """Runtime settings composed from YAML and environment variables."""

    raw: dict[str, Any]
    serper_api_key: str | None
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


def _deep_merge(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalize_legacy_config(config: dict[str, Any]) -> dict[str, Any]:
    """Accept older config.yaml shapes while exposing the runtime schema."""
    normalized = deepcopy(config)

    monitoring = dict(normalized.get("monitoring") or {})
    serp = normalized.get("serp") or {}

    if "queries" not in monitoring and "queries" in normalized:
        monitoring["queries"] = normalized["queries"]
    if "country" not in monitoring:
        monitoring["country"] = monitoring.get("country_code") or serp.get("country") or "BR"
    if "language" not in monitoring:
        monitoring["language"] = monitoring.get("language_code") or serp.get("language") or serp.get("language_code") or "pt"
    if "location_name" not in monitoring and serp.get("location_name"):
        monitoring["location_name"] = serp["location_name"]
    if "depth" not in monitoring and serp.get("depth"):
        monitoring["depth"] = serp["depth"]

    normalized["monitoring"] = monitoring

    sentiment = dict(normalized.get("sentiment") or {})
    if "negative_keywords_pt" not in sentiment and "negative_keywords" in normalized:
        sentiment["negative_keywords_pt"] = normalized["negative_keywords"]
    normalized["sentiment"] = sentiment

    return normalized


def load_settings(config_path: str | Path = "config.yaml") -> Settings:
    """Load YAML config and secrets from the environment."""
    load_dotenv()
    with Path(config_path).open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}

    normalized = _normalize_legacy_config(loaded)
    config = _expand_env(_deep_merge(DEFAULT_CONFIG, normalized))

    return Settings(
        raw=config,
        serper_api_key=os.getenv("SERPER_API_KEY"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
    )
