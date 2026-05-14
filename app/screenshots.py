"""SERP snapshot screenshot rendering."""

from __future__ import annotations

import html
import logging
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from playwright.sync_api import sync_playwright

from app.database import Mention

LOGGER = logging.getLogger(__name__)


def _safe_slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")[:90] or "query"


class ScreenshotService:
    """Render screenshots of the fetched SERP top-10 instead of opening article pages."""

    def __init__(self, screenshots_dir: Path, config: dict) -> None:
        self.screenshots_dir = screenshots_dir
        self.config = config.get("screenshots") or {}
        self.monitoring = config.get("monitoring") or {}
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

    def capture_serp_snapshots(
        self,
        run_datetime: str,
        mentions: Iterable[Mention],
        country: str,
        language: str,
    ) -> dict[str, str]:
        mentions_by_query: dict[str, list[Mention]] = defaultdict(list)
        for mention in mentions:
            mentions_by_query[mention.query].append(mention)

        if not self.config.get("enabled", True) or self.monitoring.get("screenshot_mode", "serp") != "serp":
            return {}

        run_date = _run_date(run_datetime)
        output_dir = self.screenshots_dir / run_date
        output_dir.mkdir(parents=True, exist_ok=True)
        timeout_ms = int(self.config.get("timeout_ms", 30000))
        paths: dict[str, str] = {}

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 1600})
            for query, query_mentions in mentions_by_query.items():
                try:
                    filename = f"{_safe_slug(query)}-{country.lower()}-{language.lower()}-top10.png"
                    path = output_dir / filename
                    LOGGER.info("Rendering SERP screenshot for query=%s", query)
                    page.set_content(
                        self._render_html(query, query_mentions, run_datetime, country, language),
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    page.screenshot(path=str(path), full_page=True, timeout=timeout_ms)
                    paths[query] = str(path)
                except Exception:  # noqa: BLE001 - screenshots should not stop monitoring.
                    LOGGER.exception("SERP screenshot failed for query=%s", query)
            browser.close()
        return paths

    def capture_many(self, run_id: str, mentions: Iterable[Mention]) -> list[Mention]:
        return list(mentions)

    @staticmethod
    def _render_html(query: str, mentions: list[Mention], run_datetime: str, country: str, language: str) -> str:
        rows = []
        for mention in sorted(mentions, key=lambda item: item.rank or 999):
            rows.append(
                f"""
                <article class="result">
                  <div class="rank">{mention.rank}</div>
                  <div class="body">
                    <a class="title" href="{html.escape(mention.url)}">{html.escape(mention.title or mention.url)}</a>
                    <div class="url">{html.escape(mention.domain)} - {html.escape(mention.url)}</div>
                    <p>{html.escape(mention.snippet or '')}</p>
                    <div class="chips"><span>{html.escape(mention.sentiment)}</span><span>{html.escape(mention.risk_level)}</span></div>
                  </div>
                </article>
                """
            )
        return f"""
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8" />
            <style>
              body {{ margin: 0; background: #f8fafc; color: #17202a; font-family: Arial, sans-serif; }}
              .page {{ padding: 34px 46px 50px; }}
              .meta {{ color: #5f6b7a; font-size: 14px; margin-bottom: 8px; }}
              h1 {{ font-size: 30px; line-height: 1.2; margin: 0 0 20px; }}
              .result {{ background: #fff; border: 1px solid #dfe5ec; border-radius: 10px; display: grid; grid-template-columns: 44px 1fr; gap: 14px; margin: 12px 0; padding: 16px; }}
              .rank {{ align-items: center; background: #0f766e; border-radius: 999px; color: white; display: flex; font-weight: 700; height: 34px; justify-content: center; width: 34px; }}
              .title {{ color: #1a0dab; display: block; font-size: 20px; margin-bottom: 5px; text-decoration: none; }}
              .url {{ color: #0f766e; font-size: 13px; margin-bottom: 8px; word-break: break-word; }}
              p {{ color: #394150; font-size: 15px; line-height: 1.45; margin: 0; }}
              .chips {{ display: flex; gap: 8px; margin-top: 10px; }}
              .chips span {{ background: #eef2f7; border-radius: 999px; color: #334155; font-size: 12px; font-weight: 700; padding: 4px 8px; text-transform: uppercase; }}
            </style>
          </head>
          <body>
            <main class="page">
              <div class="meta">SERP top 10 - {html.escape(country)} / {html.escape(language)} - {html.escape(run_datetime)}</div>
              <h1>{html.escape(query)}</h1>
              {''.join(rows)}
            </main>
          </body>
        </html>
        """


def _run_date(run_datetime: str) -> str:
    try:
        return datetime.fromisoformat(run_datetime.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return run_datetime[:10]
