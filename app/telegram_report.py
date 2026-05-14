"""Telegram alert delivery."""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import httpx

from app.database import MentionChange

LOGGER = logging.getLogger(__name__)


class TelegramReporter:
    """Send SERP change summaries and optional SERP screenshots to Telegram."""

    def __init__(self, token: str | None, chat_id: str | None, config: dict) -> None:
        self.token = token
        self.chat_id = chat_id
        self.config = config.get("telegram") or {}
        self.monitoring = config.get("monitoring") or {}
        self.base_url = f"https://api.telegram.org/bot{token}" if token else ""

    def send(
        self,
        changes: Iterable[MentionChange],
        demo_mode: bool = False,
        run_datetime: str | None = None,
        country: str = "BR",
        language: str = "pt",
    ) -> None:
        changes = list(changes)
        if not self.config.get("enabled", True):
            LOGGER.info("Telegram reporting skipped because it is disabled")
            return

        alert_sentiments = set(self.config.get("alert_on_sentiments", ["negative", "risky"]))
        notable = [
            change
            for change in changes
            if change.status == "disappeared"
            or change.is_new_url
            or change.is_changed
            or change.mention.sentiment in alert_sentiments
        ]
        if not notable:
            LOGGER.info("No meaningful SERP changes for Telegram report")
            return

        text = self._format_message(
            notable[: int(self.config.get("max_message_mentions", 20))],
            demo_mode=demo_mode,
            run_datetime=run_datetime,
            country=country,
            language=language,
        )
        LOGGER.info("Prepared Telegram report with %d notable changes", len(notable))
        if demo_mode:
            LOGGER.info("DEMO MODE - no live Google data")

        if not self.token or not self.chat_id:
            LOGGER.info("Telegram delivery skipped because credentials are missing")
            return

        with httpx.Client(timeout=30) as client:
            client.post(f"{self.base_url}/sendMessage", data={"chat_id": self.chat_id, "text": text}).raise_for_status()
            if self.config.get("send_screenshots", True):
                sent_paths: set[str] = set()
                for change in notable:
                    path = change.mention.screenshot_path
                    if path and path not in sent_paths and Path(path).exists():
                        sent_paths.add(path)
                        with Path(path).open("rb") as image:
                            client.post(
                                f"{self.base_url}/sendPhoto",
                                data={"chat_id": self.chat_id, "caption": f"SERP: {change.mention.query}"[:1000]},
                                files={"photo": image},
                            ).raise_for_status()

    def _format_message(
        self,
        changes: list[MentionChange],
        demo_mode: bool = False,
        run_datetime: str | None = None,
        country: str = "BR",
        language: str = "pt",
    ) -> str:
        grouped: dict[str, list[MentionChange]] = defaultdict(list)
        for change in changes:
            grouped[change.mention.query].append(change)

        lines = ["SERP Monitor Report", f"Region: {country} / {language}", f"Run date: {run_datetime or 'unknown'}"]
        if demo_mode:
            lines.append("DEMO MODE - no live Google data")
        if self.monitoring.get("dashboard_url"):
            lines.append(f"Dashboard: {self.monitoring['dashboard_url']}")

        for query, query_changes in grouped.items():
            lines.append(f"\nQuery: {query}")
            self._append_section(lines, "New URLs", [item for item in query_changes if item.is_new_url])
            self._append_section(lines, "Rank changes", [item for item in query_changes if item.is_changed])
            self._append_section(
                lines,
                "Risky mentions",
                [item for item in query_changes if item.mention.sentiment in {"negative", "risky"}],
            )
            self._append_section(lines, "Disappeared URLs", [item for item in query_changes if item.status == "disappeared"])
        return "\n".join(lines)

    @staticmethod
    def _append_section(lines: list[str], title: str, changes: list[MentionChange]) -> None:
        if not changes:
            return
        lines.append(f"- {title}:")
        for change in changes[:5]:
            rank = "-" if change.mention.rank is None else f"#{change.mention.rank}"
            previous = f" prev #{change.previous_rank}" if change.previous_rank is not None else ""
            delta = f" delta {change.rank_delta:+d}" if change.rank_delta is not None else ""
            lines.append(f"  {rank}{previous}{delta} {change.mention.title}\n  {change.mention.url}")
