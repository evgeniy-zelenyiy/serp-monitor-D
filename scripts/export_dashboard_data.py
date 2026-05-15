"""Export SQLite SERP snapshots to the GitHub Pages dashboard payload."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.database import SerpDatabase

LOGGER = logging.getLogger(__name__)
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
RECENT_CHANGE_LIMIT = 250
RISKY_LIMIT = 250
LATEST_STATUS_LIMIT = 500
URL_HISTORY_LIMIT = 160
CHART_DAY_LIMIT = 60

ENTITY_PATTERNS = {
    "LinkedIn": ["linkedin.com"],
    "Facebook": ["facebook.com"],
    "Instagram": ["instagram.com"],
    "Medium": ["medium.com"],
    "Crunchbase": ["crunchbase.com"],
    "TikTok": ["tiktok.com"],
    "Pinterest": ["pinterest.com"],
    "YouTube": ["youtube.com", "youtu.be"],
    "X / Twitter": ["twitter.com", "x.com"],
    "Reclame Aqui": ["reclameaqui.com.br"],
}
AUTHORITY_ENTITIES = {"LinkedIn", "Crunchbase", "Medium", "YouTube"}
POSITIVE_ASSET_ENTITIES = {"LinkedIn", "Crunchbase", "Medium", "Instagram", "Facebook", "YouTube"}


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
        tags = self.config.get("domain_tags", {}) or {}
        self.domain_tags = {
            "owned": set(tags.get("owned_domains", [])),
            "trusted": set(tags.get("trusted_domains", [])),
            "risky": set(tags.get("risky_domains", [])),
            "ignored": set(tags.get("ignored_domains", [])),
        }

    def run(self) -> dict[str, Any]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_database_schema()

        latest_run_id = self._latest_run_id()
        latest_rows = self._latest_top10_rows(latest_run_id)
        recent_change_rows = self._recent_change_rows()
        risky_rows = self._risky_rows()
        latest_status_rows = self._latest_status_rows()

        latest = [self._row_to_item(row) for row in latest_rows]
        recent_changes = [self._row_to_item(row) for row in recent_change_rows]
        risky_mentions = [self._row_to_item(row) for row in risky_rows]
        latest_statuses = [self._row_to_item(row) for row in latest_status_rows]
        dashboard_items = self._unique_items([*latest, *recent_changes, *risky_mentions, *latest_statuses])
        url_histories = self._url_histories({item["url"] for item in dashboard_items})
        historical_rows = [self._row_to_item(row) for row in self._chart_rows()]
        entity_map = self._load_entity_map()
        volatility = self._volatility(recent_changes)
        query_health = self._query_health(latest, recent_changes, historical_rows)
        charts = self._charts(historical_rows)
        executive_summary = self._executive_summary(latest, recent_changes, volatility, query_health)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project": self.config.get("project", {}).get("name", "SERP Monitor"),
            "latest_run_id": latest_run_id,
            "queries": sorted({item["query"] for item in latest}),
            "summary": self._summary(latest, recent_changes),
            "latest_top10": latest,
            "mentions": dashboard_items,
            "recent_changes": recent_changes,
            "risky_mentions": risky_mentions,
            "latest_statuses": latest_statuses,
            "url_histories": url_histories,
            "volatility": volatility,
            "query_health": query_health,
            "charts": charts,
            "executive_summary_markdown": executive_summary,
            "views": {
                "all": latest,
                "new": [item for item in recent_changes if item["status"] == "new"],
                "changed": [item for item in recent_changes if item["status"] == "changed"],
                "disappeared": [item for item in recent_changes if item["status"] == "disappeared"],
            },
            "screenshots": self._screenshots(latest),
            "domains": self._domain_summary(latest, entity_map),
            "entity_map": entity_map,
            "export_limits": {
                "recent_changes": RECENT_CHANGE_LIMIT,
                "risky_mentions": RISKY_LIMIT,
                "latest_statuses": LATEST_STATUS_LIMIT,
                "url_history": URL_HISTORY_LIMIT,
                "chart_days": CHART_DAY_LIMIT,
            },
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

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _latest_run_id(self) -> str | None:
        if not self.database_path.exists():
            LOGGER.warning("SQLite database does not exist yet: %s", self.database_path)
            return None
        with self._connect() as connection:
            row = connection.execute("SELECT run_id FROM runs ORDER BY run_datetime DESC LIMIT 1").fetchone()
            return row["run_id"] if row else None

    def _latest_top10_rows(self, latest_run_id: str | None) -> list[sqlite3.Row]:
        if not latest_run_id:
            return []
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT s.*, r.mode
                FROM serp_snapshots s
                LEFT JOIN runs r ON r.run_id = s.run_id
                WHERE s.run_id = ? AND s.status != 'disappeared'
                ORDER BY s.query ASC, COALESCE(s.rank, 999) ASC, s.id DESC
                """,
                (latest_run_id,),
            ).fetchall()

    def _recent_change_rows(self) -> list[sqlite3.Row]:
        if not self.database_path.exists():
            return []
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT s.*, r.mode
                FROM serp_snapshots s
                LEFT JOIN runs r ON r.run_id = s.run_id
                WHERE s.status IN ('new', 'changed', 'disappeared')
                ORDER BY s.run_datetime DESC, s.query ASC, COALESCE(s.rank, 999) ASC, s.id DESC
                LIMIT ?
                """,
                (RECENT_CHANGE_LIMIT,),
            ).fetchall()

    def _risky_rows(self) -> list[sqlite3.Row]:
        if not self.database_path.exists():
            return []
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT s.*, r.mode
                FROM serp_snapshots s
                LEFT JOIN runs r ON r.run_id = s.run_id
                WHERE s.sentiment IN ('risky', 'negative') OR s.risk_level IN ('medium', 'high')
                ORDER BY s.run_datetime DESC, s.query ASC, COALESCE(s.rank, 999) ASC, s.id DESC
                LIMIT ?
                """,
                (RISKY_LIMIT,),
            ).fetchall()

    def _latest_status_rows(self) -> list[sqlite3.Row]:
        if not self.database_path.exists():
            return []
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT s.*, r.mode
                FROM serp_snapshots s
                LEFT JOIN runs r ON r.run_id = s.run_id
                WHERE s.id IN (
                    SELECT MAX(id)
                    FROM serp_snapshots
                    GROUP BY query, url
                )
                ORDER BY s.run_datetime DESC, s.query ASC, COALESCE(s.rank, 999) ASC
                LIMIT ?
                """,
                (LATEST_STATUS_LIMIT,),
            ).fetchall()

    def _chart_rows(self) -> list[sqlite3.Row]:
        if not self.database_path.exists():
            return []
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT s.*, r.mode
                FROM serp_snapshots s
                LEFT JOIN runs r ON r.run_id = s.run_id
                WHERE date(s.run_datetime) IN (
                    SELECT DISTINCT date(run_datetime)
                    FROM serp_snapshots
                    ORDER BY date(run_datetime) DESC
                    LIMIT ?
                )
                ORDER BY s.run_datetime ASC, s.query ASC, COALESCE(s.rank, 999) ASC
                """,
                (CHART_DAY_LIMIT,),
            ).fetchall()

    def _history_rows_for_urls(self, urls: set[str]) -> list[sqlite3.Row]:
        if not urls or not self.database_path.exists():
            return []
        placeholders = ",".join("?" for _ in urls)
        with self._connect() as connection:
            return connection.execute(
                f"""
                SELECT s.*, r.mode
                FROM serp_snapshots s
                LEFT JOIN runs r ON r.run_id = s.run_id
                WHERE s.url IN ({placeholders})
                ORDER BY s.url ASC, s.run_datetime ASC, s.query ASC
                LIMIT ?
                """,
                (*sorted(urls), URL_HISTORY_LIMIT * max(1, len(urls))),
            ).fetchall()

    def _row_to_item(self, row: sqlite3.Row) -> dict[str, Any]:
        screenshot = self._copy_screenshot(row["screenshot_path"])
        raw_domain = row["domain"] or ""
        parent_domain = self._parent_domain(raw_domain)
        entity = self._domain_entity(parent_domain)
        return {
            "id": int(row["id"]),
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
            "domain": raw_domain,
            "parent_domain": parent_domain,
            "domain_entity": entity,
            "domain_tags": self._domain_tags(raw_domain, parent_domain),
            "sentiment": row["sentiment"] or "neutral",
            "risk_level": row["risk_level"] or "none",
            "risk_keywords": row["risk_keywords"] or row["negative_keywords"] or "",
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "disappeared_at": row["disappeared_at"],
            "date_published": row["date_published"],
            "date_published_source": row["date_published_source"],
            "source_type": row["source_type"] or "organic",
            "screenshot": screenshot,
        }

    @staticmethod
    def _unique_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[int] = set()
        unique = []
        for item in items:
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            unique.append(item)
        return unique

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
    def _summary(latest: list[dict[str, Any]], recent_changes: list[dict[str, Any]]) -> dict[str, int]:
        statuses = Counter(item["status"] for item in recent_changes)
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

    def _domain_summary(self, items: list[dict[str, Any]], entity_map: dict[str, Any] | None) -> list[dict[str, Any]]:
        domains: dict[str, dict[str, Any]] = {}
        for item in items:
            entity = item.get("domain_entity") or item.get("parent_domain") or "unknown"
            entry = domains.setdefault(entity, {"domain": entity, "raw_domains": set(), "total": 0, "best_rank": None, "risky": 0, "negative": 0, "positive": 0, "neutral": 0, "tags": set()})
            entry["raw_domains"].add(item.get("domain") or "")
            entry["tags"].update(item.get("domain_tags") or [])
            entry["total"] += 1
            entry[item.get("sentiment") or "neutral"] = entry.get(item.get("sentiment") or "neutral", 0) + 1
            rank = item.get("current_rank")
            if rank and (entry["best_rank"] is None or rank < entry["best_rank"]):
                entry["best_rank"] = rank
        if entity_map:
            for node in entity_map.get("nodes", []):
                if node.get("type") == "domain":
                    entity = self._domain_entity(self._parent_domain(node.get("label") or ""))
                    if entity in domains:
                        domains[entity]["entity_sentiment_counts"] = node.get("sentiment_counts", {})
        normalized = []
        for item in domains.values():
            normalized.append({**item, "raw_domains": sorted(item["raw_domains"]), "tags": sorted(item["tags"])})
        return sorted(normalized, key=lambda item: (-item["total"], item["domain"]))

    def _url_histories(self, urls: set[str]) -> dict[str, dict[str, Any]]:
        histories: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self._history_rows_for_urls(urls):
            item = self._row_to_item(row)
            histories[item["url"]].append(item)
        result: dict[str, dict[str, Any]] = {}
        for url, rows in histories.items():
            queries = sorted({row["query"] for row in rows})
            first_seen = min((row["first_seen"] for row in rows if row.get("first_seen")), default=None)
            last_seen = max((row["last_seen"] for row in rows if row.get("last_seen")), default=None)
            disappeared_at = max((row["disappeared_at"] for row in rows if row.get("disappeared_at")), default=None)
            result[url] = {
                "url": url,
                "queries": queries,
                "first_seen": first_seen,
                "last_seen": last_seen,
                "disappeared_at": disappeared_at,
                "history": rows[-URL_HISTORY_LIMIT:],
                "rank_series": [{"date": row["run_datetime"][:10], "rank": row["current_rank"], "query": row["query"], "status": row["status"]} for row in rows[-URL_HISTORY_LIMIT:]],
                "sentiment_changes": [{"date": row["run_datetime"][:10], "sentiment": row["sentiment"], "risk_level": row["risk_level"]} for row in rows[-URL_HISTORY_LIMIT:]],
            }
        return result

    @staticmethod
    def _volatility(changes: list[dict[str, Any]]) -> dict[str, Any]:
        increases = sorted([item for item in changes if (item.get("rank_delta") or 0) > 0], key=lambda item: item["rank_delta"], reverse=True)[:5]
        drops = sorted([item for item in changes if (item.get("rank_delta") or 0) < 0], key=lambda item: item["rank_delta"])[:5]
        by_query: dict[str, int] = defaultdict(int)
        for item in changes:
            by_query[item["query"]] += abs(item.get("rank_delta") or 0)
        today = date.today().isoformat()
        new_domains = sorted({item.get("parent_domain") for item in changes if item["status"] == "new" and item["run_datetime"].startswith(today) and item.get("parent_domain")})
        disappeared_domains = sorted({item.get("parent_domain") for item in changes if item["status"] == "disappeared" and item["run_datetime"].startswith(today) and item.get("parent_domain")})
        most_volatile = max(by_query.items(), key=lambda pair: pair[1], default=(None, 0))
        return {
            "biggest_rank_increases": increases,
            "biggest_rank_drops": drops,
            "most_volatile_query": {"query": most_volatile[0], "movement": most_volatile[1]},
            "new_domains_today": new_domains,
            "disappeared_domains_today": disappeared_domains,
        }

    def _query_health(self, latest: list[dict[str, Any]], changes: list[dict[str, Any]], historical: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
        changes_by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
        historical_by_query_date: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
        for item in latest:
            by_query[item["query"]].append(item)
        for item in changes:
            changes_by_query[item["query"]].append(item)
        for item in historical:
            if item["status"] != "disappeared":
                historical_by_query_date[item["query"]][item["run_datetime"][:10]].append(item)
        scores: dict[str, dict[str, Any]] = {}
        for query, rows in by_query.items():
            score = self._score_query(rows, changes_by_query.get(query, []))
            previous_score = None
            dated = sorted(historical_by_query_date.get(query, {}).items())
            if len(dated) >= 2:
                previous_score = self._score_query(dated[-2][1], [])
            trend = "flat"
            if previous_score is not None and score - previous_score >= 3:
                trend = "up"
            elif previous_score is not None and previous_score - score >= 3:
                trend = "down"
            scores[query] = {"score": score, "trend": trend, "previous_score": previous_score}
        return scores

    @staticmethod
    def _score_query(rows: list[dict[str, Any]], changes: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        total = len(rows)
        risky = sum(1 for item in rows if item["sentiment"] == "risky" or item["risk_level"] in {"medium", "high"})
        negative = sum(1 for item in rows if item["sentiment"] == "negative")
        authority = sum(1 for item in rows if item.get("domain_entity") in AUTHORITY_ENTITIES)
        positive_assets = sum(1 for item in rows if item.get("domain_entity") in POSITIVE_ASSET_ENTITIES or "owned" in item.get("domain_tags", []) or "trusted" in item.get("domain_tags", []))
        top3_risky = sum(1 for item in rows if (item.get("current_rank") or 99) <= 3 and (item["sentiment"] in {"risky", "negative"} or item["risk_level"] in {"medium", "high"}))
        volatility = sum(abs(item.get("rank_delta") or 0) for item in changes) / max(1, len(changes))
        score = 82
        score -= int((risky / total) * 28)
        score -= int((negative / total) * 35)
        score -= top3_risky * 8
        score -= min(12, int(volatility * 1.5))
        score += min(10, authority * 2)
        score += min(12, positive_assets * 2)
        return max(0, min(100, score))

    def _charts(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in rows:
            by_date[item["run_datetime"][:10]].append(item)
        labels = sorted(by_date)
        top_domains = [domain for domain, _ in Counter(item.get("domain_entity") for item in rows if item.get("domain_entity")).most_common(5)]
        domain_trends = {domain: [] for domain in top_domains}
        for label in labels:
            day_rows = by_date[label]
            active = [item for item in day_rows if item["status"] != "disappeared"]
            counts = Counter(item.get("domain_entity") for item in active)
            for domain in top_domains:
                domain_trends[domain].append(counts.get(domain, 0))
        return {
            "labels": labels,
            "visibility": [sum(1 for item in by_date[label] if item["status"] != "disappeared") for label in labels],
            "risky_mentions": [sum(1 for item in by_date[label] if item["sentiment"] in {"risky", "negative"} or item["risk_level"] in {"medium", "high"}) for label in labels],
            "new_urls": [sum(1 for item in by_date[label] if item["status"] == "new") for label in labels],
            "average_rank": [round(sum(item["current_rank"] or 0 for item in by_date[label] if item["current_rank"]) / max(1, sum(1 for item in by_date[label] if item["current_rank"])), 2) for label in labels],
            "domain_trends": domain_trends,
        }

    def _executive_summary(self, latest: list[dict[str, Any]], changes: list[dict[str, Any]], volatility: dict[str, Any], query_health: dict[str, dict[str, Any]]) -> str:
        avg_health = round(sum(item["score"] for item in query_health.values()) / max(1, len(query_health)))
        risky = sum(1 for item in latest if item["sentiment"] in {"risky", "negative"} or item["risk_level"] in {"medium", "high"})
        lines = [
            f"# SERP Executive Summary",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            "",
            f"- Current top-10 rows: {len(latest)}",
            f"- Recent meaningful changes: {len(changes)}",
            f"- Risky or negative current mentions: {risky}",
            f"- Average query health score: {avg_health}/100",
        ]
        if volatility.get("most_volatile_query", {}).get("query"):
            lines.append(f"- Most volatile query: {volatility['most_volatile_query']['query']} ({volatility['most_volatile_query']['movement']} rank points)")
        return "\n".join(lines) + "\n"

    def _domain_tags(self, raw_domain: str, parent_domain: str) -> list[str]:
        tags = []
        for tag, domains in self.domain_tags.items():
            if raw_domain in domains or parent_domain in domains:
                tags.append(tag)
        return tags

    @staticmethod
    def _parent_domain(domain: str) -> str:
        domain = (domain or "").lower().removeprefix("www.")
        parts = [part for part in domain.split(".") if part]
        if len(parts) <= 2:
            return domain
        if len(parts) >= 3 and parts[-2] in {"com", "org", "net", "gov"} and len(parts[-1]) == 2:
            return ".".join(parts[-3:])
        return ".".join(parts[-2:])

    @staticmethod
    def _domain_entity(parent_domain: str) -> str:
        for entity, patterns in ENTITY_PATTERNS.items():
            if parent_domain in patterns or any(parent_domain.endswith(f".{pattern}") for pattern in patterns):
                return entity
        if not parent_domain:
            return "unknown"
        return parent_domain.split(".")[0].replace("-", " ").title()

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
