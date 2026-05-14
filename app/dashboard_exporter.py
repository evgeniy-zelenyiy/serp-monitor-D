"""Export SERP history and screenshots for the static dashboard."""

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

from app.config_loader import load_settings
from app.database import SerpDatabase

LOGGER = logging.getLogger(__name__)
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


class DashboardExporter:
    """Build docs/data/results.json from SQLite history and generated artifacts."""

    def __init__(self, config_path: str | Path = "config.yaml", docs_dir: str | Path = "docs") -> None:
        self.settings = load_settings(config_path)
        self.docs_dir = Path(docs_dir)
        self.data_dir = self.docs_dir / "data"
        self.screenshot_dir = self.data_dir / "screenshots"
        self.results_path = self.data_dir / "results.json"

    def export(self) -> dict[str, Any]:
        self._ensure_database_schema()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

        rows = self._latest_mentions()
        mentions = [self._row_to_mention(row) for row in rows]
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project": self.settings.raw.get("project", {}).get("name", "SERP Monitor"),
            "summary": self._summary(mentions),
            "mentions": mentions,
            "screenshots": [mention for mention in mentions if mention.get("screenshot")],
            "entity_map": self._load_entity_map(),
        }
        self.results_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("Wrote dashboard data to %s", self.results_path)
        return payload

    def _ensure_database_schema(self) -> None:
        database = SerpDatabase(self.settings.database_path)
        try:
            database.initialize()
        finally:
            database.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.settings.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _latest_mentions(self) -> list[sqlite3.Row]:
        if not self.settings.database_path.exists():
            LOGGER.warning("SQLite database does not exist yet: %s", self.settings.database_path)
            return []

        with self._connect() as connection:
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

    def _row_to_mention(self, row: sqlite3.Row) -> dict[str, Any]:
        first_seen = row["historical_first_seen"] or row["first_seen"] or row["collected_at"]
        last_seen = row["historical_last_seen"] or row["last_seen"] or row["collected_at"]
        previous_rank = row["previous_rank"]
        rank = int(row["rank"])
        status = "new"
        if int(row["seen_count"] or 0) > 1:
            status = "changed" if previous_rank is not None and int(previous_rank) != rank else "existing"

        screenshot = self._copy_screenshot(row["screenshot_path"])
        risk_score = float(row["risk_score"] or 0.0)
        sentiment = row["sentiment"] or "neutral"
        return {
            "first_seen": first_seen,
            "last_seen": last_seen,
            "query": row["query"],
            "rank": rank,
            "title": row["title"] or row["url"],
            "url": row["url"],
            "domain": row["domain"] or "",
            "sentiment": sentiment,
            "risk_level": self._risk_level(sentiment, risk_score),
            "risk_score": risk_score,
            "risk_keywords": row["negative_keywords"] or "",
            "status": status,
            "previous_rank": int(previous_rank) if previous_rank is not None else None,
            "screenshot": screenshot,
        }

    def _copy_screenshot(self, screenshot_path: str | None) -> str | None:
        if not screenshot_path:
            return None
        source = Path(screenshot_path)
        if not source.exists():
            return None
        target = self.screenshot_dir / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return f"data/screenshots/{target.name}"

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
            "positive_neutral_mentions": sentiments.get("positive", 0) + sentiments.get("neutral", 0),
        }

    def _load_entity_map(self) -> dict[str, Any] | None:
        path = self.settings.entity_map_path
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            LOGGER.warning("Could not parse entity map JSON at %s", path)
            return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export static dashboard data from SERP history")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML configuration file")
    parser.add_argument("--docs-dir", default="docs", help="GitHub Pages docs directory")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format=LOG_FORMAT)
    DashboardExporter(args.config, args.docs_dir).export()


if __name__ == "__main__":
    main()
