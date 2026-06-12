"""Telegram digest delivery."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Iterable

import httpx

from app.database import MentionChange

LOGGER = logging.getLogger(__name__)
MAX_TELEGRAM_TEXT_LENGTH = 4096
TELEGRAM_TEXT_CHUNK_SIZE = 3900
RISK_SENTIMENTS = {"negative", "risky"}
PERSONAL_BRAND_QUERIES = ("dmytro rukin", "dmitry rukin")
DEFAULT_RISK_KEYWORDS = [
    "fraud",
    "scam",
    "fraude",
    "golpe",
    "acusação",
    "investigação",
    "crime",
    "processo",
    "reclamação",
    "pirâmide",
    "defamatory",
    "allegation",
    "fake",
    "незаконний",
    "шахрайство",
    "наклеп",
    "наклепні",
]


class TelegramReporter:
    """Send compact SERP digest alerts to Telegram."""

    def __init__(self, token: str | None, chat_id: str | None, config: dict) -> None:
        self.token = token
        self.chat_id = chat_id
        self.config = config.get("telegram") or {}
        self.monitoring = config.get("monitoring") or {}
        self.base_url = f"https://api.telegram.org/bot{token}" if token else ""
        self.risk_keywords = [keyword.lower() for keyword in self.config.get("risk_keywords", DEFAULT_RISK_KEYWORDS)]

    def send(
        self,
        changes: Iterable[MentionChange],
        demo_mode: bool = False,
        run_datetime: str | None = None,
        country: str = "BR",
        language: str = "pt",
    ) -> None:
        if not self.config.get("enabled", True):
            LOGGER.info("Telegram reporting skipped because it is disabled")
            return

        digest_items = self._important_changes(list(changes))
        if not digest_items:
            LOGGER.info("No important SERP changes today.")
            return

        text = self._format_digest(digest_items, run_datetime, country, language)
        LOGGER.info("Prepared Telegram digest with %d important SERP changes", len(digest_items))
        if demo_mode:
            LOGGER.info("DEMO MODE - no live Google data")

        if not self.token or not self.chat_id:
            LOGGER.info("Telegram delivery skipped because credentials are missing")
            return

        with httpx.Client(timeout=30) as client:
            self._send_text(client, text)

    def _important_changes(self, changes: list[MentionChange]) -> list[dict]:
        items: list[dict] = []
        for change in changes:
            category = self._category(change)
            if not category:
                continue
            keywords = self._matched_risk_keywords(change)
            items.append(
                {
                    "category": category,
                    "change": change,
                    "keywords": keywords,
                    "priority": self._priority(change, bool(keywords)),
                }
            )
        return sorted(items, key=self._sort_key)

    def _category(self, change: MentionChange) -> str | None:
        mention = change.mention
        risky = self._is_risky(change)
        moved_up = change.rank_delta is not None and change.rank_delta > 0
        dropped = change.rank_delta is not None and change.rank_delta < 0

        if change.status == "disappeared":
            return "DISAPPEARED"
        if change.is_new_url and risky:
            return "RISKY NEW"
        if risky and moved_up:
            return "RANK UP RISK"
        if risky and change.is_changed:
            return "RISKY CHANGED"
        if change.is_new_url:
            return "NEW URL"
        if mention.sentiment == "positive" and dropped:
            return "POSITIVE DROPPED"
        return None

    def _is_risky(self, change: MentionChange) -> bool:
        mention = change.mention
        return (
            mention.sentiment in RISK_SENTIMENTS
            or mention.risk_level in {"medium", "high"}
            or bool(mention.risk_keywords or mention.negative_keywords)
            or bool(self._matched_risk_keywords(change))
        )

    def _matched_risk_keywords(self, change: MentionChange) -> list[str]:
        text = f"{change.mention.title}\n{change.mention.snippet}".lower()
        return [keyword for keyword in self.risk_keywords if re.search(rf"\b{re.escape(keyword)}\b", text)]

    @staticmethod
    def _priority(change: MentionChange, risky: bool) -> str:
        query = change.mention.query.lower()
        if any(term in query for term in PERSONAL_BRAND_QUERIES):
            return "HIGH"
        if "lafinteca" in query:
            return "HIGH" if risky else "MEDIUM"
        return "MEDIUM" if risky else "LOW"

    @staticmethod
    def _sort_key(item: dict) -> tuple[int, int]:
        category_order = {
            "RISKY NEW": 0,
            "RANK UP RISK": 1,
            "RISKY CHANGED": 2,
            "POSITIVE DROPPED": 3,
            "DISAPPEARED": 4,
            "NEW URL": 5,
        }
        priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        return (category_order.get(item["category"], 99), priority_order.get(item["priority"], 99))

    def _format_digest(
        self,
        items: list[dict],
        run_datetime: str | None,
        country: str,
        language: str,
    ) -> str:
        date = self._date(run_datetime)
        counts = {
            "New URLs": sum(1 for item in items if item["change"].is_new_url),
            "Risky URLs": sum(1 for item in items if self._is_risky(item["change"])),
            "Risky moved up": sum(1 for item in items if item["category"] == "RANK UP RISK"),
            "Disappeared": sum(1 for item in items if item["category"] == "DISAPPEARED"),
            "Positive dropped": sum(1 for item in items if item["category"] == "POSITIVE DROPPED"),
        }
        dashboard_url = self.config.get("dashboard_url") or self.monitoring.get("dashboard_url") or ""
        max_items = max(1, int(self.config.get("max_message_mentions", 20)))

        lines = [
            "SERP Daily Alert",
            f"Date: {date}",
            f"Region: {country} / {language}",
            "",
            "Summary:",
        ]
        lines.extend(f"- {label}: {value}" for label, value in counts.items())
        lines.extend(["", "Needs attention:"])

        for index, item in enumerate(items[:max_items], start=1):
            lines.extend(self._format_item(index, item))

        if len(items) > max_items:
            lines.append(f"...and {len(items) - max_items} more important changes.")

        if dashboard_url:
            lines.extend(["", "Dashboard:", dashboard_url])
        return "\n".join(lines)

    def _format_item(self, index: int, item: dict) -> list[str]:
        change: MentionChange = item["change"]
        mention = change.mention
        lines = [f"{index}. [{item['category']}]", f"   Query: {mention.query}", f"   Domain: {mention.domain}"]

        if item["category"] in {"RANK UP RISK", "POSITIVE DROPPED", "RISKY CHANGED"} and change.previous_rank:
            lines.append(f"   Rank: #{change.previous_rank} → #{mention.rank}")
        elif mention.rank is not None:
            lines.append(f"   Rank: #{mention.rank}")
        elif change.previous_rank is not None:
            lines.append(f"   Rank: #{change.previous_rank} → disappeared")

        why = self._why(item)
        if why:
            lines.append(f"   Why: {why}")
        if item["category"] != "RANK UP RISK":
            lines.append(f"   URL: {mention.url}")
        lines.append("")
        return lines

    def _why(self, item: dict) -> str:
        change: MentionChange = item["change"]
        mention = change.mention
        parts = [f"priority {item['priority']}"]
        keywords = item["keywords"] or [word.strip() for word in mention.risk_keywords.split(",") if word.strip()]
        if keywords:
            parts.append(f"risk keywords: {', '.join(keywords[:5])}")
        elif mention.sentiment in RISK_SENTIMENTS:
            parts.append(f"sentiment: {mention.sentiment}")
        elif item["category"] == "DISAPPEARED":
            parts.append("URL disappeared from top-10")
        elif item["category"] == "POSITIVE DROPPED":
            parts.append("positive result dropped in rank")
        elif item["category"] == "NEW URL":
            parts.append("new URL entered top-10")
        return "; ".join(parts)

    @staticmethod
    def _date(run_datetime: str | None) -> str:
        if not run_datetime:
            return datetime.utcnow().date().isoformat()
        return run_datetime[:10]

    def _send_text(self, client: httpx.Client, text: str) -> bool:
        for chunk in self._split_text(text):
            try:
                client.post(f"{self.base_url}/sendMessage", data={"chat_id": self.chat_id, "text": chunk}).raise_for_status()
            except httpx.HTTPStatusError as exc:
                response_text = exc.response.text[:500] if exc.response is not None else ""
                LOGGER.error("Telegram message delivery failed: %s %s", exc, response_text)
                return False
            except httpx.HTTPError as exc:
                LOGGER.error("Telegram message delivery failed: %s", exc)
                return False
        return True

    @staticmethod
    def _split_text(text: str) -> list[str]:
        if len(text) <= MAX_TELEGRAM_TEXT_LENGTH:
            return [text]

        chunks: list[str] = []
        current: list[str] = []
        current_length = 0
        for line in text.splitlines():
            if len(line) > TELEGRAM_TEXT_CHUNK_SIZE:
                line = f"{line[: TELEGRAM_TEXT_CHUNK_SIZE - 15]}... [truncated]"
            line_length = len(line) + 1
            if current and current_length + line_length > TELEGRAM_TEXT_CHUNK_SIZE:
                chunks.append("\n".join(current))
                current = []
                current_length = 0
            current.append(line)
            current_length += line_length

        if current:
            chunks.append("\n".join(current))
        return chunks
