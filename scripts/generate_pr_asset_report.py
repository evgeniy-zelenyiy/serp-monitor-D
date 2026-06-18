"""Generate a query-by-query ranking report for configured PR assets."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

LOGGER = logging.getLogger(__name__)
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
DEFAULT_REPORT_DIR = Path("reports/pr_assets")
DEFAULT_DASHBOARD_DATA = Path("docs/data/results.json")


@dataclass(frozen=True)
class AssetRank:
    asset: str
    url: str
    query: str
    current_rank: int | None
    previous_rank: int | None
    rank_delta: int | None
    best_rank: int | None
    first_seen: str | None
    last_seen: str | None

    @property
    def in_top3(self) -> bool:
        return self.current_rank is not None and self.current_rank <= 3

    @property
    def in_top10(self) -> bool:
        return self.current_rank is not None and self.current_rank <= 10


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def normalize_url(value: str) -> str:
    return value.strip().rstrip("/")


def integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def rows_from_sqlite(database_path: Path, assets: list[str], queries: list[str]) -> list[AssetRank]:
    if not database_path.exists():
        return []
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        output: list[AssetRank] = []
        for asset_index, asset_url in enumerate(assets, start=1):
            normalized_asset = normalize_url(asset_url)
            for query in queries:
                history = connection.execute(
                    """
                    SELECT rank, first_seen, last_seen, run_datetime, status
                    FROM serp_snapshots
                    WHERE query = ? AND RTRIM(url, '/') = ?
                    ORDER BY run_datetime DESC, id DESC
                    """,
                    (query, normalized_asset),
                ).fetchall()
                visible = [row for row in history if row["status"] != "disappeared" and row["rank"] is not None]
                latest = history[0] if history else None
                current = visible[0] if latest and latest["status"] != "disappeared" and visible else None
                previous_index = 1 if current else 0
                previous = visible[previous_index] if len(visible) > previous_index else None
                ranks = [int(row["rank"]) for row in visible]
                current_rank = int(current["rank"]) if current else None
                previous_rank = int(previous["rank"]) if previous else None
                rank_delta = (
                    previous_rank - current_rank
                    if current_rank is not None and previous_rank is not None
                    else None
                )
                first_seen_values = [str(row["first_seen"]) for row in history if row["first_seen"]]
                last_seen_values = [str(row["last_seen"]) for row in visible if row["last_seen"]]
                output.append(
                    AssetRank(
                        asset=f"PR Asset {asset_index}",
                        url=asset_url,
                        query=query,
                        current_rank=current_rank,
                        previous_rank=previous_rank,
                        rank_delta=rank_delta,
                        best_rank=min(ranks) if ranks else None,
                        first_seen=min(first_seen_values) if first_seen_values else None,
                        last_seen=max(last_seen_values) if last_seen_values else None,
                    )
                )
        return output
    finally:
        connection.close()


def dashboard_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("mentions") or payload.get("latest_top10") or [])


def rows_from_dashboard(path: Path, assets: list[str], queries: list[str]) -> list[AssetRank]:
    items = dashboard_items(path)
    output: list[AssetRank] = []
    for asset_index, asset_url in enumerate(assets, start=1):
        normalized_asset = normalize_url(asset_url)
        for query in queries:
            matches = [
                item
                for item in items
                if str(item.get("query") or "") == query
                and normalize_url(str(item.get("url") or "")) == normalized_asset
            ]
            matches.sort(
                key=lambda item: str(item.get("run_datetime") or item.get("last_seen") or ""),
                reverse=True,
            )
            current = next((item for item in matches if item.get("status") != "disappeared"), None)
            current_rank = integer((current or {}).get("current_rank", (current or {}).get("rank")))
            previous_rank = integer((current or {}).get("previous_rank"))
            historical_ranks = [
                rank
                for item in matches
                if (rank := integer(item.get("current_rank", item.get("rank")))) is not None
            ]
            output.append(
                AssetRank(
                    asset=f"PR Asset {asset_index}",
                    url=asset_url,
                    query=query,
                    current_rank=current_rank,
                    previous_rank=previous_rank,
                    rank_delta=integer((current or {}).get("rank_delta")),
                    best_rank=min(historical_ranks) if historical_ranks else current_rank,
                    first_seen=min(
                        (str(item["first_seen"]) for item in matches if item.get("first_seen")),
                        default=None,
                    ),
                    last_seen=max(
                        (str(item["last_seen"]) for item in matches if item.get("last_seen")),
                        default=None,
                    ),
                )
            )
    return output


def empty_rows(assets: list[str], queries: list[str]) -> list[AssetRank]:
    return [
        AssetRank(f"PR Asset {asset_index}", asset_url, query, None, None, None, None, None, None)
        for asset_index, asset_url in enumerate(assets, start=1)
        for query in queries
    ]


def display(value: Any) -> str:
    return "N/A" if value is None else str(value)


def bool_text(value: bool) -> str:
    return "yes" if value else "no"


def markdown_report(rows: list[AssetRank], generated_at: str) -> str:
    lines = [
        "# PR Asset Tracking Report",
        "",
        f"**Generated:** {generated_at}",
        f"**Tracked assets:** {len({row.url for row in rows})}",
        f"**Tracked queries:** {len({row.query for row in rows})}",
        "",
        "| Asset | URL | Query | Current rank | Previous rank | Rank delta | Best rank | First seen | Last seen | Top 3 | Top 10 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {asset} | {url} | {query} | {current} | {previous} | {delta} | {best} | "
            "{first} | {last} | {top3} | {top10} |".format(
                asset=row.asset,
                url=row.url,
                query=row.query,
                current=display(row.current_rank),
                previous=display(row.previous_rank),
                delta=display(row.rank_delta),
                best=display(row.best_rank),
                first=display(row.first_seen),
                last=display(row.last_seen),
                top3=bool_text(row.in_top3),
                top10=bool_text(row.in_top10),
            )
        )
    lines.extend(
        [
            "",
            "Rank delta is calculated as previous rank minus current rank. Positive values indicate improvement.",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(report_dir: Path, rows: list[AssetRank]) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    markdown_path = report_dir / "latest_pr_assets_report.md"
    csv_path = report_dir / "latest_pr_assets_report.csv"
    markdown_path.write_text(markdown_report(rows, generated_at), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "asset",
                "url",
                "query",
                "current_rank",
                "previous_rank",
                "rank_delta",
                "best_rank",
                "first_seen",
                "last_seen",
                "in_top3",
                "in_top10",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.asset,
                    row.url,
                    row.query,
                    display(row.current_rank),
                    display(row.previous_rank),
                    display(row.rank_delta),
                    display(row.best_rank),
                    display(row.first_seen),
                    display(row.last_seen),
                    bool_text(row.in_top3),
                    bool_text(row.in_top10),
                ]
            )
    LOGGER.info("Generated PR asset reports in %s", report_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a PR asset tracking report")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dashboard-data", default=str(DEFAULT_DASHBOARD_DATA))
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    args = parse_args()
    config = load_config(Path(args.config))
    report_config = ((config.get("reporting") or {}).get("pr_assets") or {})
    if not report_config.get("enabled", True):
        LOGGER.info("PR asset reporting is disabled")
        return
    assets = [str(value) for value in report_config.get("urls", [])]
    queries = [str(value) for value in report_config.get("queries", [])]
    database_path = Path((config.get("project") or {}).get("database_path", "data/serp_history.sqlite3"))
    rows = rows_from_sqlite(database_path, assets, queries)
    if not rows:
        rows = rows_from_dashboard(Path(args.dashboard_data), assets, queries)
    if not rows:
        rows = empty_rows(assets, queries)
    write_reports(Path(args.report_dir), rows)


if __name__ == "__main__":
    main()
