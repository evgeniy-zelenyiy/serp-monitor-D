"""Export SQLite SERP snapshots to the GitHub Pages dashboard payload."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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

        rows = self._snapshot_rows()
        latest_run_id = self._latest_run_id(rows)
        latest = [self._row_to_item(row) for row in rows if row["run_id"] == latest_run_id and row["status"] != "disappeared"]
        all_items = [self._row_to_item(row) for row in rows]
        entity_map = self._load_entity_map()
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project": self.config.get("project", {}).get("name", "SERP Monitor"),
            "latest_run_id": latest_run_id,
            "queries": sorted({item["query"] for item in latest}),
            "summary": self._summary(all_items, latest_run_id),
            "latest_top10": latest,
            "mentions": all_items,
            "views": {
                "all": all_items,
                "new_urls": [item for item in all_items if item["status"] == "new"],
                "rank_changes": [item for item in all_items if item["status"] == "changed"],
                "disappeared": [item for item in all_items if item["status"] == "disappeared"],
            },
            "screenshots": self._screenshots(all_items),
            "domains": self._domain_summary(latest, entity_map),
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

    def _snapshot_rows(self) -> list[sqlite3.Row]:
        if not self.database_path.exists():
            LOGGER.warning("SQLite database does not exist yet: %s", self.database_path)
            return []
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(
                """
                SELECT s.*, r.mode
                FROM serp_snapshots s
                LEFT JOIN runs r ON r.run_id = s.run_id
                ORDER BY s.run_datetime DESC, s.query ASC, COALESCE(s.rank, 999) ASC, s.id DESC
                """
            ).fetchall()
        finally:
            connection.close()

    @staticmethod
    def _latest_run_id(rows: list[sqlite3.Row]) -> str | None:
        return rows[0]["run_id"] if rows else None

    def _row_to_item(self, row: sqlite3.Row) -> dict[str, Any]:
        screenshot = self._copy_screenshot(row["screenshot_path"])
        return {
            "run_id": row["run_id"],
            "run_datetime": row["run_datetime"],
            "query": row["query"],
            "country": row["country"],
            "language": row["language"],
            "device": row["device"],
            "current_rank": int(row["rank"]) if row["rank"] is not None else None,
            "rank": int(row["rank"]) if row["rank"] is not None else None,
            "previous_rank": int(row["previous_rank"]) if row["previous_rank"] is not None else None,
            "rank_delta": int(row["rank_delta"]) if row["rank_delta"] is not None else None,
            "status": row["status"],
            "title": row["title"] or row["url"],
            "url": row["url"],
            "domain": row["domain"] or "",
            "sentiment": row["sentiment"] or "neutral",
            "risk_level": row["risk_level"] or "none",
            "risk_keywords": row["risk_keywords"] or row["negative_keywords"] or "",
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "disappeared_at": row["disappeared_at"],
            "date_published": row["date_published"],
            "date_published_source": row["date_published_source"],
            "date_published_confidence": row["date_published_confidence"],
            "source_type": row["source_type"] or "organic",
            "screenshot": screenshot,
        }

    def _copy_screenshot(self, screenshot_path: str | None) -> str | None:
        if not screenshot_path:
            return None
        source = Path(screenshot_path)
        if not source.exists():
            return None
        run_date = source.parent.name if source.parent.name else "screenshots"
        target_dir = self.screenshots_dir / run_date
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return f"screenshots/{run_date}/{target.name}"

    def _screenshots(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str, str]] = set()
        screenshots = []
        for item in items:
            if not item.get("screenshot"):
                continue
            key = (item["run_datetime"][:10], item["query"], item["screenshot"])
            if key in seen:
                continue
            seen.add(key)
            screenshots.append({"date": key[0], "query": item["query"], "path": item["screenshot"]})
        return screenshots

    @staticmethod
    def _summary(items: list[dict[str, Any]], latest_run_id: str | None) -> dict[str, int]:
        latest = [item for item in items if item["run_id"] == latest_run_id and item["status"] != "disappeared"]
        statuses = Counter(item["status"] for item in items if item["run_id"] == latest_run_id)
        sentiments = Counter(item["sentiment"] for item in latest)
        return {
            "total_mentions": len(latest),
            "new_mentions": statuses.get("new", 0),
            "changed_mentions": statuses.get("changed", 0),
            "disappeared_mentions": statuses.get("disappeared", 0),
            "risky_mentions": sentiments.get("risky", 0),
            "negative_mentions": sentiments.get("negative", 0),
            "positive_mentions": sentiments.get("positive", 0),
            "neutral_mentions": sentiments.get("neutral", 0),
        }

    @staticmethod
    def _domain_summary(items: list[dict[str, Any]], entity_map: dict[str, Any] | None) -> list[dict[str, Any]]:
        domains: dict[str, dict[str, Any]] = {}
        for item in items:
            domain = item.get("domain") or "unknown"
            entry = domains.setdefault(domain, {"domain": domain, "total": 0, "best_rank": None, "risky": 0, "negative": 0, "positive": 0, "neutral": 0})
            entry["total"] += 1
            entry[item.get("sentiment") or "neutral"] = entry.get(item.get("sentiment") or "neutral", 0) + 1
            rank = item.get("current_rank")
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
