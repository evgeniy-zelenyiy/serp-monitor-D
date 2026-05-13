"""SERP mention screenshot capture."""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from playwright.sync_api import sync_playwright

from app.database import Mention

LOGGER = logging.getLogger(__name__)


def _safe_filename(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")[:120]


class ScreenshotService:
    """Capture local screenshots for SERP result URLs."""

    def __init__(self, screenshots_dir: Path, config: dict) -> None:
        self.screenshots_dir = screenshots_dir
        self.config = config["screenshots"]
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

    def capture_many(self, run_id: str, mentions: Iterable[Mention]) -> list[Mention]:
        mentions = list(mentions)
        if not self.config.get("enabled", True):
            return mentions

        max_per_run = int(self.config.get("max_per_run", 10))
        timeout_ms = int(self.config.get("timeout_ms", 30000))
        full_page = bool(self.config.get("full_page", True))
        updated: list[Mention] = []

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1365, "height": 900})
            for index, mention in enumerate(mentions):
                if index >= max_per_run:
                    updated.append(mention)
                    continue
                try:
                    filename = f"{run_id}-{mention.rank}-{_safe_filename(mention.domain)}.png"
                    path = self.screenshots_dir / filename
                    LOGGER.info("Capturing screenshot for %s", mention.url)
                    page.goto(mention.url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.screenshot(path=str(path), full_page=full_page, timeout=timeout_ms)
                    updated.append(replace(mention, screenshot_path=str(path)))
                except Exception:  # noqa: BLE001 - screenshots should not stop data collection.
                    LOGGER.exception("Screenshot failed for %s", mention.url)
                    updated.append(mention)
            browser.close()
        return updated
