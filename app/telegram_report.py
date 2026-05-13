"""Telegram alert delivery."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import httpx

from app.database import MentionChange

LOGGER = logging.getLogger(__name__)


class TelegramReporter:
    """Send run summaries and optional screenshots to Telegram."""

    def __init__(self, token: str | None, chat_id: str | None, config: dict) -> None:
        self.token = token
        self.chat_id = chat_id
        self.config = config["telegram"]
        self.base_url = f"https://api.telegram.org/bot{token}" if token else ""

    def send(self, changes: Iterable[MentionChange]) -> None:
        changes = list(changes)
        if not self.config.get("enabled", True) or not self.token or not self.chat_id:
            LOGGER.info("Telegram reporting skipped because it is disabled or credentials are missing")
            return

        alert_sentiments = set(self.config.get("alert_on_sentiments", ["negative", "risky"]))
        notable = [
            change
            for change in changes
            if change.is_new_url or change.rank_delta not in (None, 0) or change.mention.sentiment in alert_sentiments
        ]
        if not notable:
            LOGGER.info("No notable changes for Telegram report")
            return

        text = self._format_message(notable[: int(self.config.get("max_message_mentions", 20))])
        with httpx.Client(timeout=30) as client:
            client.post(f"{self.base_url}/sendMessage", data={"chat_id": self.chat_id, "text": text}).raise_for_status()
            if self.config.get("send_screenshots", True):
                for change in notable:
                    path = change.mention.screenshot_path
                    if path and Path(path).exists() and change.mention.sentiment in alert_sentiments:
                        with Path(path).open("rb") as image:
                            client.post(
                                f"{self.base_url}/sendPhoto",
                                data={"chat_id": self.chat_id, "caption": change.mention.title[:1000]},
                                files={"photo": image},
                            ).raise_for_status()

    @staticmethod
    def _format_message(changes: list[MentionChange]) -> str:
        lines = ["SERP monitor report"]
        for change in changes:
            marker = "NEW" if change.is_new_url else f"rank {change.previous_rank}→{change.mention.rank}"
            lines.append(
                f"\n[{change.mention.sentiment.upper()}] {marker}\n"
                f"Query: {change.mention.query}\n"
                f"#{change.mention.rank} {change.mention.title}\n"
                f"{change.mention.url}"
            )
        return "\n".join(lines)
