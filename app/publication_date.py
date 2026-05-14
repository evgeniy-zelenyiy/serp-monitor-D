"""Best-effort publication date extraction for SERP result URLs."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from datetime import datetime
from html import unescape
from typing import Iterable

import httpx

from app.database import Mention

LOGGER = logging.getLogger(__name__)

META_PATTERNS = [
    ("json_ld_datePublished", re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)),
    ("meta_article_published_time", re.compile(r'<meta[^>]+(?:property|name)=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']', re.I)),
    ("meta_date_published", re.compile(r'<meta[^>]+(?:property|name)=["\'](?:datePublished|publish_date|pubdate)["\'][^>]+content=["\']([^"\']+)["\']', re.I)),
    ("time_datetime", re.compile(r'<time[^>]+datetime=["\']([^"\']+)["\']', re.I)),
]
VISIBLE_DATE = re.compile(r"\b(20\d{2}[-/.][01]?\d[-/.][0-3]?\d|[0-3]?\d[-/.][01]?\d[-/.]20\d{2})\b")


class PublicationDateExtractor:
    """Extract article publication dates without failing the monitor."""

    def __init__(self, enabled: bool = True, timeout: float = 12.0) -> None:
        self.enabled = enabled
        self.timeout = timeout

    def enrich_many(self, mentions: Iterable[Mention]) -> list[Mention]:
        mentions = list(mentions)
        if not self.enabled:
            return mentions
        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 SERPMonitor/1.0"}) as client:
            return [self.enrich(mention, client) for mention in mentions]

    def enrich(self, mention: Mention, client: httpx.Client) -> Mention:
        if not mention.url or mention.url.startswith("https://example.com/demo-serp/"):
            return mention
        try:
            response = client.get(mention.url)
            response.raise_for_status()
            date_value, source, confidence = self.extract(response.text)
            if not date_value:
                return mention
            return Mention(
                **{
                    **asdict(mention),
                    "date_published": date_value,
                    "date_published_source": source,
                    "date_published_confidence": confidence,
                }
            )
        except Exception:  # noqa: BLE001 - publication date is optional enrichment.
            LOGGER.info("Publication date extraction skipped for %s", mention.url)
            return mention

    def extract(self, page_html: str) -> tuple[str | None, str | None, float | None]:
        json_ld = self._json_ld_date(page_html)
        if json_ld:
            return json_ld, "schema.org datePublished", 0.9

        for source, pattern in META_PATTERNS[1:]:
            match = pattern.search(page_html)
            if match:
                normalized = self._normalize(match.group(1))
                if normalized:
                    return normalized, source, 0.8

        visible = VISIBLE_DATE.search(unescape(re.sub(r"<[^>]+>", " ", page_html)))
        if visible:
            normalized = self._normalize(visible.group(1))
            if normalized:
                return normalized, "visible_date_pattern", 0.45
        return None, None, None

    def _json_ld_date(self, page_html: str) -> str | None:
        for match in META_PATTERNS[0][1].finditer(page_html):
            raw = unescape(match.group(1)).strip()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            found = self._find_date_published(payload)
            if found:
                return self._normalize(found)
        return None

    def _find_date_published(self, value: object) -> str | None:
        if isinstance(value, dict):
            if value.get("datePublished"):
                return str(value["datePublished"])
            for nested in value.values():
                found = self._find_date_published(nested)
                if found:
                    return found
        if isinstance(value, list):
            for item in value:
                found = self._find_date_published(item)
                if found:
                    return found
        return None

    @staticmethod
    def _normalize(value: str) -> str | None:
        value = value.strip()
        if not value:
            return None
        for candidate in (value, value.replace("/", "-"), value.replace(".", "-")):
            try:
                return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date().isoformat()
            except ValueError:
                pass
        return value[:40]
