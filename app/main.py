"""Command-line entrypoint for the SERP monitoring system."""

from __future__ import annotations

import argparse
import logging
import uuid

from app.config_loader import load_settings
from app.database import SerpDatabase
from app.entity_map import EntityMapBuilder
from app.screenshots import ScreenshotService
from app.sentiment import SentimentAnalyzer
from app.serp_fetcher import SerperSerpFetcher
from app.telegram_report import TelegramReporter

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=LOG_FORMAT)


def run(config_path: str) -> None:
    settings = load_settings(config_path)
    run_id = uuid.uuid4().hex
    logging.info("Starting SERP monitor run_id=%s", run_id)

    database = SerpDatabase(settings.database_path)
    database.initialize()

    try:
        fetcher = SerperSerpFetcher(settings.serper_api_key, settings.raw)
        analyzer = SentimentAnalyzer(settings.raw, settings.openai_api_key)
        screenshots = ScreenshotService(settings.screenshots_dir, settings.raw)
        reporter = TelegramReporter(settings.telegram_bot_token, settings.telegram_chat_id, settings.raw)
        entity_map_builder = EntityMapBuilder(settings.entity_map_path)

        mentions = fetcher.fetch_all()
        logging.info("Fetched %d mentions", len(mentions))
        mentions = analyzer.analyze_many(mentions)
        mentions = screenshots.capture_many(run_id, mentions)
        changes = database.save_mentions(run_id, mentions)
        if settings.raw.get("entity_map", {}).get("enabled", True):
            entity_map_builder.build(changes)
        reporter.send(changes, demo_mode=fetcher.demo_mode)
        logging.info("SERP monitor run complete")
    finally:
        database.close()


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
