"""Export SQLite SERP history to the GitHub Pages dashboard payload."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.database import SerpDatabase

LOGGER = logging.getLogger(__name__)
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


class DashboardExport:
    """Prepare docs/data/results.json and dashboard screenshot assets."""

    def __init__(self, config_path: str | Path, docs_dir: str | Path) -> None:
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.docs_dir = Path(docs_dir)
        self.data_dir = self.docs_dir / "data"
        self.screenshots_dir = self.docs_dir / "screenshots"
        project = self.config.get("project", {})
        self.database_path = Path(project.get("database_path", "data/serp_history.sqlite3"))
        self.entity_map_path = Path(project.get("entity_map_path", "data/entity_map.json"))
        self.results_path = self.data_dir / "results.json"

    def run(self) -> dict[str, Any]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_database_schema()

        mentions = [self._row_to_mention(row) for row in self._latest_rows()]
        entity_map = self._load_entity_map()
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project": self.config.get("project", {}).get("name", "SERP Monitor"),
            "summary": self._summary(mentions),
            "mentions": mentions,
            "screenshots": [mention for mention in mentions if mention.get("screenshot")],
            "domains": self._domain_summary(mentions, entity_map),
            "entity_map": entity_map,
        }
        self.results_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("Exported dashboard data to %s", self.results_path)
        return payload

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        return yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}

    def _ensure_database_schema(self) -> None:
        database = SerpDatabase(self.database_path)
        try:
            database.initialize()
        finally:
            database.close()

    def _latest_rows(self) -> list[sqlite3.Row]:
        if not self.database_path.exists():
            LOGGER.warning("SQLite database does not exist yet: %s", self.database_path)
            return []

        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(
                """
                WITH ranked AS (
                    SELECT
                        m.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY m.query, m.url
                            ORDER BY m.collected_at DESC, m.id DESC
                        ) AS row_number,
                        LAG(m.rank) OVER (
                            PARTITION BY m.query, m.url
                            ORDER BY m.collected_at ASC, m.id ASC
                        ) AS previous_rank,
                        COUNT(*) OVER (PARTITION BY m.query, m.url) AS seen_count,
                        MIN(COALESCE(m.first_seen, m.collected_at)) OVER (PARTITION BY m.query, m.url) AS historical_first_seen,
                        MAX(COALESCE(m.last_seen, m.collected_at)) OVER (PARTITION BY m.query, m.url) AS historical_last_seen
                    FROM mentions m
                )
                SELECT * FROM ranked
                WHERE row_number = 1
                ORDER BY historical_last_seen DESC, query ASC, rank ASC
                """
            ).fetchall()
        finally:
            connection.close()

    def _row_to_mention(self, row: sqlite3.Row) -> dict[str, Any]:
        rank = int(row["rank"])
        previous_rank = row["previous_rank"]
        status = "new"
        if int(row["seen_count"] or 0) > 1:
            status = "changed" if previous_rank is not None and int(previous_rank) != rank else "existing"

        sentiment = row["sentiment"] or "neutral"
        risk_score = float(row["risk_score"] or 0.0)
        risk_level = row["risk_level"] or self._risk_level(sentiment, risk_score)
        risk_keywords = row["risk_keywords"] or row["negative_keywords"] or ""
        return {
            "first_seen": row["historical_first_seen"] or row["first_seen"] or row["collected_at"],
            "last_seen": row["historical_last_seen"] or row["last_seen"] or row["collected_at"],
            "query": row["query"],
            "rank": rank,
            "title": row["title"] or row["url"],
            "url": row["url"],
            "domain": row["domain"] or "",
            "sentiment": sentiment,
            "risk_level": risk_level,
            "risk_score": risk_score,
            "risk_keywords": risk_keywords,
            "source_type": row["source_type"] or "organic",
            "status": status,
            "previous_rank": int(previous_rank) if previous_rank is not None else None,
            "screenshot": self._copy_screenshot(row["screenshot_path"]),
        }

    def _copy_screenshot(self, screenshot_path: str | None) -> str | None:
        if not screenshot_path:
            return None
        source = Path(screenshot_path)
        if not source.exists():
            return None
        target = self.screenshots_dir / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return f"screenshots/{target.name}"

    @staticmethod
    def _risk_level(sentiment: str, risk_score: float) -> str:
        if sentiment == "negative" or risk_score >= 0.75:
            return "high"
        if sentiment == "risky" or risk_score >= 0.35:
            return "medium"
        if risk_score > 0:
            return "low"
        return "none"

    @staticmethod
    def _summary(mentions: list[dict[str, Any]]) -> dict[str, int]:
        sentiments = Counter(mention["sentiment"] for mention in mentions)
        return {
            "total_mentions": len(mentions),
            "new_mentions": sum(1 for mention in mentions if mention["status"] == "new"),
            "risky_mentions": sentiments.get("risky", 0),
            "negative_mentions": sentiments.get("negative", 0),
            "positive_mentions": sentiments.get("positive", 0),
            "neutral_mentions": sentiments.get("neutral", 0),
        }

    @staticmethod
    def _domain_summary(mentions: list[dict[str, Any]], entity_map: dict[str, Any] | None) -> list[dict[str, Any]]:
        domains: dict[str, dict[str, Any]] = {}
        for mention in mentions:
            domain = mention.get("domain") or "unknown"
            entry = domains.setdefault(
                domain,
                {
                    "domain": domain,
                    "total": 0,
                    "best_rank": None,
                    "risky": 0,
                    "negative": 0,
                    "positive": 0,
                    "neutral": 0,
                },
            )
            entry["total"] += 1
            entry[mention.get("sentiment") or "neutral"] = entry.get(mention.get("sentiment") or "neutral", 0) + 1
            rank = int(mention.get("rank") or 0)
            if rank and (entry["best_rank"] is None or rank < entry["best_rank"]):
                entry["best_rank"] = rank

        if entity_map:
            for node in entity_map.get("nodes", []):
                if node.get("type") == "domain" and node.get("label") in domains:
                    domains[node["label"]]["entity_sentiment_counts"] = node.get("sentiment_counts", {})

        return sorted(domains.values(), key=lambda item: (-item["total"], item["domain"]))

    def _load_entity_map(self) -> dict[str, Any] | None:
        if not self.entity_map_path.exists():
            return None
        try:
            return json.loads(self.entity_map_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            LOGGER.warning("Could not parse entity map JSON at %s", self.entity_map_path)
            return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export SERP dashboard data for GitHub Pages")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML configuration file")
    parser.add_argument("--docs-dir", default="docs", help="GitHub Pages docs directory")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format=LOG_FORMAT)
    DashboardExport(args.config, args.docs_dir).run()


if __name__ == "__main__":
    main()
