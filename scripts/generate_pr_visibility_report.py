"""Generate NDA-safe PR visibility reports from exported SERP snapshots."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable
from urllib.parse import urlparse

import yaml

LOGGER = logging.getLogger(__name__)
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
DEFAULT_REPORT_DIR = Path("reports/pr_visibility")
DEFAULT_DASHBOARD_DATA = Path("docs/data/results.json")
DEFAULT_DOCS_REPORT_DIR = Path("docs/reports/pr_visibility")


@dataclass(frozen=True)
class ReportPaths:
    latest_markdown: Path
    latest_csv: Path
    archive_markdown: Path
    archive_csv: Path
    docs_markdown: Path
    docs_csv: Path


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def normalized_domain(value: str) -> str:
    candidate = value.strip().lower()
    if "://" in candidate:
        candidate = urlparse(candidate).netloc
    return candidate.removeprefix("www.").split(":")[0]


def contains_keyword(value: str, keywords: Iterable[str]) -> bool:
    haystack = value.lower()
    return any(keyword.lower() in haystack for keyword in keywords if keyword)


def is_risky(item: dict[str, Any]) -> bool:
    return (
        str(item.get("sentiment", "")).lower() in {"negative", "risky"}
        or str(item.get("risk_level", "")).lower() in {"medium", "high"}
    )


def is_pr_style(
    item: dict[str, Any],
    public_safe_domains: set[str],
    domain_keywords: list[str],
    content_keywords: list[str],
) -> bool:
    domain = normalized_domain(str(item.get("domain") or item.get("url") or ""))
    if domain in public_safe_domains:
        return True
    if contains_keyword(domain, domain_keywords):
        return True
    content = " ".join(
        str(item.get(field) or "")
        for field in ("title", "snippet", "source_type", "domain_entity")
    )
    return contains_keyword(content, content_keywords)


def load_dashboard_rows(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    if not path.exists():
        return [], [], None
    payload = json.loads(path.read_text(encoding="utf-8"))
    current = list(payload.get("latest_top10") or [])
    changes = list(payload.get("recent_changes") or [])
    generated_at = payload.get("generated_at")
    return current, changes, str(generated_at) if generated_at else None


def load_sqlite_rows(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    if not path.exists():
        return [], [], None
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        latest = connection.execute(
            """
            SELECT run_id, run_datetime
            FROM runs
            ORDER BY run_datetime DESC
            LIMIT 1
            """
        ).fetchone()
        if not latest:
            return [], [], None
        current = connection.execute(
            """
            SELECT *
            FROM serp_snapshots
            WHERE run_id = ? AND status != 'disappeared'
            ORDER BY query, COALESCE(rank, 999)
            """,
            (latest["run_id"],),
        ).fetchall()
        changes = connection.execute(
            """
            SELECT *
            FROM serp_snapshots
            WHERE status IN ('new', 'changed', 'disappeared')
            ORDER BY run_datetime DESC, id DESC
            LIMIT 500
            """
        ).fetchall()
        return [dict(row) for row in current], [dict(row) for row in changes], str(latest["run_datetime"])
    finally:
        connection.close()


def item_rank(item: dict[str, Any]) -> int | None:
    value = item.get("current_rank", item.get("rank"))
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def rank_delta(item: dict[str, Any]) -> int:
    try:
        return int(item.get("rank_delta") or 0)
    except (TypeError, ValueError):
        return 0


def reporting_period(current: list[dict[str, Any]], changes: list[dict[str, Any]], generated_at: str | None) -> str:
    timestamps = [
        str(item.get("run_datetime") or item.get("last_seen") or "")
        for item in [*current, *changes]
        if item.get("run_datetime") or item.get("last_seen")
    ]
    parsed_dates: list[date] = []
    for value in timestamps:
        try:
            parsed_dates.append(datetime.fromisoformat(value.replace("Z", "+00:00")).date())
        except ValueError:
            continue
    if parsed_dates:
        start = min(parsed_dates)
        end = max(parsed_dates)
        return start.isoformat() if start == end else f"{start.isoformat()} to {end.isoformat()}"
    if generated_at:
        try:
            return datetime.fromisoformat(generated_at.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).date().isoformat()


def visibility_status(improvements: int, drops: int, new_results: int, disappeared: int) -> str:
    score = improvements + new_results - drops - disappeared
    if score > 0:
        return "improving"
    if score < 0:
        return "declining"
    return "stable"


def recommended_actions(metrics: dict[str, Any]) -> list[str]:
    actions = ["Strengthen PR URLs that are already visible."]
    if metrics["average_pr_rank"] is not None and metrics["average_pr_rank"] > 5:
        actions.append("Support weak PR URLs with relevant links.")
    if metrics["risky_top10_results"] > 0:
        actions.append("Monitor queries where risk visibility increased.")
    if metrics["pr_visibility_share_percent"] < 30:
        actions.append("Create additional neutral or positive assets because PR visibility share is low.")
    return actions


def safe_value(value: Any) -> str:
    return "N/A" if value is None else str(value)


def anonymized_assets(
    current: list[dict[str, Any]],
    public_safe_domains: set[str],
    domain_keywords: list[str],
    content_keywords: list[str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    pr_assets: list[dict[str, str]] = []
    risk_assets: list[dict[str, str]] = []
    pr_domain_labels: dict[str, str] = {}
    risk_domain_labels: dict[str, str] = {}

    for item in current:
        domain = normalized_domain(str(item.get("domain") or item.get("url") or ""))
        rank = safe_value(item_rank(item))
        delta = rank_delta(item)
        movement = "improved" if delta > 0 else "dropped" if delta < 0 else "stable"
        if is_pr_style(item, public_safe_domains, domain_keywords, content_keywords):
            if domain in public_safe_domains:
                domain_label = domain
            else:
                domain_label = pr_domain_labels.setdefault(domain, f"PR Domain {len(pr_domain_labels) + 1}")
            pr_assets.append(
                {
                    "article": f"PR Article {len(pr_assets) + 1}",
                    "domain": domain_label,
                    "rank": rank,
                    "movement": movement,
                }
            )
        if is_risky(item):
            if domain in public_safe_domains:
                domain_label = domain
            else:
                domain_label = risk_domain_labels.setdefault(domain, f"Risk Domain {len(risk_domain_labels) + 1}")
            risk_assets.append({"domain": domain_label, "rank": rank, "movement": movement})
    return pr_assets, risk_assets


def calculate_metrics(
    current: list[dict[str, Any]],
    changes: list[dict[str, Any]],
    generated_at: str | None,
    configured_queries: list[str],
    public_safe_domains: set[str],
    domain_keywords: list[str],
    content_keywords: list[str],
) -> dict[str, Any]:
    classifier = lambda item: is_pr_style(item, public_safe_domains, domain_keywords, content_keywords)
    current_pr = [item for item in current if classifier(item)]
    changed_pr = [item for item in changes if classifier(item)]
    ranks = [rank for item in current_pr if (rank := item_rank(item)) is not None]
    current_queries = {str(item.get("query") or "") for item in current if item.get("query")}
    pr_queries = {str(item.get("query") or "") for item in current_pr if item.get("query")}
    total_queries = len(set(configured_queries) or current_queries)
    total_slots = len(current)
    improvements = sum(1 for item in changed_pr if item.get("status") == "changed" and rank_delta(item) > 0)
    drops = sum(1 for item in changed_pr if item.get("status") == "changed" and rank_delta(item) < 0)
    new_results = sum(1 for item in changed_pr if item.get("status") == "new")
    disappeared = sum(1 for item in changed_pr if item.get("status") == "disappeared")
    metrics = {
        "reporting_period": reporting_period(current, changes, generated_at),
        "total_monitored_queries": total_queries,
        "total_pr_style_results": len(current_pr),
        "total_top10_slots": total_slots,
        "pr_visibility_share_percent": round((len(current_pr) / total_slots * 100), 1) if total_slots else 0.0,
        "query_coverage_count": len(pr_queries),
        "query_coverage_percent": round((len(pr_queries) / total_queries * 100), 1) if total_queries else 0.0,
        "average_pr_rank": round(mean(ranks), 2) if ranks else None,
        "best_pr_rank": min(ranks) if ranks else None,
        "worst_pr_rank": max(ranks) if ranks else None,
        "pr_rank_improvements": improvements,
        "pr_rank_drops": drops,
        "newly_visible_pr_results": new_results,
        "pr_results_disappeared": disappeared,
        "risky_top10_results": sum(1 for item in current if is_risky(item)),
        "overall_visibility_status": visibility_status(improvements, drops, new_results, disappeared),
    }
    metrics["recommended_actions"] = recommended_actions(metrics)
    return metrics


def markdown_report(
    project: str,
    metrics: dict[str, Any],
    nda_safe: bool,
    pr_assets: list[dict[str, str]],
    risk_assets: list[dict[str, str]],
) -> str:
    actions = "\n".join(f"{index}. {action}" for index, action in enumerate(metrics["recommended_actions"], start=1))
    pr_summary = "\n".join(
        f"- **{item['article']}** - {item['domain']}; rank #{item['rank']}; {item['movement']}."
        for item in pr_assets
    ) or "- No PR-style result is currently visible."
    risk_summary = "\n".join(
        f"- **{item['domain']}** - rank #{item['rank']}; {item['movement']}."
        for item in risk_assets
    ) or "- No risky or negative result is currently visible."
    return f"""# NDA-safe PR Visibility Report

**Project:** {project}
**Reporting period:** {metrics["reporting_period"]}
**Privacy mode:** {"NDA-safe" if nda_safe else "standard"}
**Overall visibility status:** **{metrics["overall_visibility_status"]}**

## Executive Summary

This report summarizes the visibility of PR-style results in monitored Google top-10 results. It intentionally excludes full URLs, private notes, screenshots, API data, and internal monitoring details.

## Visibility Metrics

| Metric | Value |
| --- | ---: |
| Total monitored queries | {metrics["total_monitored_queries"]} |
| Total PR-style results found | {metrics["total_pr_style_results"]} |
| Total observed top-10 slots | {metrics["total_top10_slots"]} |
| PR visibility share | {metrics["pr_visibility_share_percent"]}% |
| Query coverage | {metrics["query_coverage_count"]} queries ({metrics["query_coverage_percent"]}%) |
| Average PR rank | {safe_value(metrics["average_pr_rank"])} |
| Best PR rank | {safe_value(metrics["best_pr_rank"])} |
| Worst PR rank | {safe_value(metrics["worst_pr_rank"])} |
| PR rank improvements | {metrics["pr_rank_improvements"]} |
| PR rank drops | {metrics["pr_rank_drops"]} |
| Newly visible PR results | {metrics["newly_visible_pr_results"]} |
| PR results disappeared from top-10 | {metrics["pr_results_disappeared"]} |
| Risky or negative results visible in top-10 | {metrics["risky_top10_results"]} |

## Anonymized PR Visibility Summary

{pr_summary}

## Anonymized Risk Context

{risk_summary}

## Recommended Next Actions

{actions}

## NDA-safe Disclosure

Only aggregated metrics and non-sensitive summaries are included. Domains that are not explicitly configured as public-safe, article titles, full URLs, screenshots, raw monitoring records, and internal notes are omitted.
"""


def csv_rows(metrics: dict[str, Any]) -> list[tuple[str, str]]:
    rows = [
        ("reporting_period", metrics["reporting_period"]),
        ("total_monitored_queries", metrics["total_monitored_queries"]),
        ("total_pr_style_results", metrics["total_pr_style_results"]),
        ("total_top10_slots", metrics["total_top10_slots"]),
        ("pr_visibility_share_percent", metrics["pr_visibility_share_percent"]),
        ("query_coverage_count", metrics["query_coverage_count"]),
        ("query_coverage_percent", metrics["query_coverage_percent"]),
        ("average_pr_rank", safe_value(metrics["average_pr_rank"])),
        ("best_pr_rank", safe_value(metrics["best_pr_rank"])),
        ("worst_pr_rank", safe_value(metrics["worst_pr_rank"])),
        ("pr_rank_improvements", metrics["pr_rank_improvements"]),
        ("pr_rank_drops", metrics["pr_rank_drops"]),
        ("newly_visible_pr_results", metrics["newly_visible_pr_results"]),
        ("pr_results_disappeared", metrics["pr_results_disappeared"]),
        ("risky_top10_results", metrics["risky_top10_results"]),
        ("overall_visibility_status", metrics["overall_visibility_status"]),
    ]
    rows.extend((f"recommended_action_{index}", action) for index, action in enumerate(metrics["recommended_actions"], 1))
    return [(str(key), str(value)) for key, value in rows]


def report_paths(report_dir: Path, docs_report_dir: Path, report_date: date) -> ReportPaths:
    archive = report_dir / "archive"
    return ReportPaths(
        latest_markdown=report_dir / "latest_pr_visibility_report.md",
        latest_csv=report_dir / "latest_pr_visibility_report.csv",
        archive_markdown=archive / f"{report_date.isoformat()}-pr-visibility-report.md",
        archive_csv=archive / f"{report_date.isoformat()}-pr-visibility-report.csv",
        docs_markdown=docs_report_dir / "latest_pr_visibility_report.md",
        docs_csv=docs_report_dir / "latest_pr_visibility_report.csv",
    )


def write_reports(paths: ReportPaths, markdown: str, rows: list[tuple[str, str]]) -> None:
    for path in paths.__dict__.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    paths.latest_markdown.write_text(markdown, encoding="utf-8")
    with paths.latest_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value"])
        writer.writerows(rows)
    shutil.copyfile(paths.latest_markdown, paths.archive_markdown)
    shutil.copyfile(paths.latest_csv, paths.archive_csv)
    shutil.copyfile(paths.latest_markdown, paths.docs_markdown)
    shutil.copyfile(paths.latest_csv, paths.docs_csv)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an NDA-safe PR visibility report")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dashboard-data", default=str(DEFAULT_DASHBOARD_DATA))
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--docs-report-dir", default=str(DEFAULT_DOCS_REPORT_DIR))
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    args = parse_args()
    config = load_config(Path(args.config))
    report_config = ((config.get("reporting") or {}).get("pr_visibility") or {})
    if not report_config.get("enabled", True):
        LOGGER.info("PR visibility reporting is disabled")
        return

    current, changes, generated_at = load_dashboard_rows(Path(args.dashboard_data))
    if not current:
        database_path = Path((config.get("project") or {}).get("database_path", "data/serp_history.sqlite3"))
        current, changes, generated_at = load_sqlite_rows(database_path)

    public_safe_domains = {
        normalized_domain(str(domain))
        for domain in report_config.get("public_safe_domains", [])
    }
    domain_keywords = [str(value).lower() for value in report_config.get("pr_domain_keywords", [])]
    content_keywords = domain_keywords + [
        "press release",
        "interview",
        "announcement",
        "feature",
        "profile",
        "fintech",
        "business",
        "finance",
    ]
    metrics = calculate_metrics(
        current=current,
        changes=changes,
        generated_at=generated_at,
        configured_queries=[str(query) for query in (config.get("monitoring") or {}).get("queries", [])],
        public_safe_domains=public_safe_domains,
        domain_keywords=domain_keywords,
        content_keywords=content_keywords,
    )
    pr_assets, risk_assets = anonymized_assets(
        current=current,
        public_safe_domains=public_safe_domains,
        domain_keywords=domain_keywords,
        content_keywords=content_keywords,
    )
    paths = report_paths(
        Path(args.report_dir),
        Path(args.docs_report_dir),
        datetime.now(timezone.utc).date(),
    )
    markdown = markdown_report(
        str((config.get("project") or {}).get("name", "SERP Monitor")),
        metrics,
        bool(report_config.get("nda_safe", True)),
        pr_assets,
        risk_assets,
    )
    write_reports(paths, markdown, csv_rows(metrics))
    LOGGER.info("Generated NDA-safe PR visibility reports in %s", paths.latest_markdown.parent)


if __name__ == "__main__":
    main()
