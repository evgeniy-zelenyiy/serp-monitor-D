"""Command-line entrypoint for the SERP monitoring system."""

from __future__ import annotations

import argparse
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from app.config_loader import load_settings
from app.database import Mention, SerpDatabase
from app.entity_map import EntityMapBuilder
from app.publication_date import PublicationDateExtractor
from app.screenshots import ScreenshotService
from app.sentiment import SentimentAnalyzer
from app.serp_fetcher import SerperSerpFetcher
from app.telegram_report import TelegramReporter

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=LOG_FORMAT)


def run(config_path: str) -> None:
    settings = load_settings(config_path)
    monitoring = settings.raw.get("monitoring") or {}
    run_id = uuid.uuid4().hex
    run_datetime = datetime.now(timezone.utc).isoformat()
    country = str(monitoring.get("country", "BR"))
    language = str(monitoring.get("language", "pt"))
    device = str(monitoring.get("device", "desktop"))
    configured_queries = [str(query) for query in monitoring.get("queries", [])]
    logging.info("Starting SERP snapshot run_id=%s", run_id)

    database = SerpDatabase(settings.database_path)
    database.initialize()

    try:
        fetcher = SerperSerpFetcher(settings.serper_api_key, settings.raw)
        analyzer = SentimentAnalyzer(settings.raw, settings.openai_api_key)
        publisher = PublicationDateExtractor(enabled=bool(monitoring.get("extract_publication_date", True)))
        screenshots = ScreenshotService(settings.screenshots_dir, settings.raw)
        reporter = TelegramReporter(settings.telegram_bot_token, settings.telegram_chat_id, settings.raw)
        entity_map_builder = EntityMapBuilder(settings.entity_map_path)

        mentions = fetcher.fetch_all()
        logging.info("Fetched %d organic SERP results", len(mentions))
        mentions = analyzer.analyze_many(mentions)
        mentions = publisher.enrich_many(mentions)
        grouped_mentions = _group_by_query(mentions)
        snapshot_queries = configured_queries or sorted(grouped_mentions)
        screenshot_paths = screenshots.capture_serp_snapshots(run_datetime, mentions, country, language, snapshot_queries)

        changes = []
        for query in snapshot_queries:
            changes.extend(
                database.save_serp_snapshot(
                    run_id=run_id,
                    run_datetime=run_datetime,
                    query=query,
                    country=country,
                    language=language,
                    device=device,
                    mode="demo" if fetcher.demo_mode else "live",
                    mentions=grouped_mentions.get(query, []),
                    screenshot_path=screenshot_paths.get(query),
                    track_disappeared=bool(monitoring.get("track_disappeared", True)),
                )
            )

        if settings.raw.get("entity_map", {}).get("enabled", True):
            entity_map_builder.build(changes)
        reporter.send(changes, demo_mode=fetcher.demo_mode, run_datetime=run_datetime, country=country, language=language)
        logging.info("SERP snapshot run complete")
    finally:
        database.close()


def _group_by_query(mentions: list[Mention]) -> dict[str, list[Mention]]:
    grouped: dict[str, list[Mention]] = defaultdict(list)
    for mention in mentions:
        grouped[mention.query].append(mention)
    return {query: sorted(items, key=lambda item: item.rank or 999) for query, items in grouped.items()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Google SERP monitor for reputation management and SERM")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML configuration file")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)
    run(args.config)


if __name__ == "__main__":
    main()
