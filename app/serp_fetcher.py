"""Serper.dev Google SERP client with demo fallback data."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx

from app.database import Mention

LOGGER = logging.getLogger(__name__)


class SerperSerpFetcher:
    """Fetch organic Google results from Serper.dev or generate demo mentions."""

    BASE_URL = "https://google.serper.dev/search"

    def __init__(self, api_key: str | None, config: dict) -> None:
        self.api_key = api_key
        self.config = config
        self.demo_mode = not api_key

    def fetch_all(self) -> list[Mention]:
        if self.demo_mode:
            LOGGER.warning("Demo mode: SERPER_API_KEY is missing, using generated SERP results with no live Google data")
            return self._fetch_demo_results()

        monitoring = self.config.get("monitoring") or {}
        queries = monitoring.get("queries") or []
        LOGGER.info("Live mode with Serper.dev: fetching %d configured queries", len(queries))
        mentions: list[Mention] = []
        for query in queries:
            mentions.extend(self.fetch_query(query))
        return mentions

    def fetch_query(self, query: str) -> list[Mention]:
        monitoring = self.config.get("monitoring") or {}
        depth = int(monitoring.get("depth", 10))
        payload = {
            "q": query,
            "gl": str(monitoring.get("country", "BR")).lower(),
            "hl": monitoring.get("language", "pt"),
            "location": monitoring.get("location_name", "Brazil"),
            "num": depth,
        }
        headers = {
            "X-API-KEY": self.api_key or "",
            "Content-Type": "application/json",
        }

        LOGGER.info("Fetching Serper.dev organic results for query=%s", query)
        with httpx.Client(timeout=60) as client:
            response = client.post(self.BASE_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        organic_items = data.get("organic") or []
        return [self._to_mention(query, item, index) for index, item in enumerate(organic_items[:depth], start=1)]

    def _fetch_demo_results(self) -> list[Mention]:
        monitoring = self.config.get("monitoring") or {}
        queries = monitoring.get("queries") or ["Demo Brand"]
        max_results = max(1, int(monitoring.get("demo_results_per_query", 3)))

        mentions: list[Mention] = []
        for query in queries:
            mentions.extend(self._demo_mentions_for_query(query, max_results))
        return mentions

    def _demo_mentions_for_query(self, query: str, max_results: int) -> list[Mention]:
        slug = self._slugify(query)
        templates = [
            (
                "Official profile for {query}",
                "Reference result generated for workflow validation and SERP history storage.",
                "official-profile",
            ),
            (
                "Reclamação and fraude risk mention for {query}",
                "Demo reputation result containing reclamação, fraude and risco signals for sentiment testing.",
                "risk-mention",
            ),
            (
                "Positive industry profile mentioning {query}",
                "Demo positive result used to validate neutral and positive-looking SERP mentions.",
                "industry-profile",
            ),
        ]

        mentions: list[Mention] = []
        for index, (title, snippet, path) in enumerate(templates[:max_results], start=1):
            url = f"https://example.com/demo-serp/{slug}/{path}"
            mentions.append(
                Mention(
                    query=query,
                    url=url,
                    title=title.format(query=query),
                    snippet=snippet,
                    rank=index,
                    domain="example.com",
                    source_type="organic",
                )
            )
        return mentions

    @staticmethod
    def _slugify(value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
        return cleaned or "demo-query"

    @staticmethod
    def _to_mention(query: str, item: dict, fallback_rank: int) -> Mention:
        url = item.get("link") or item.get("url") or ""
        domain = urlparse(url).netloc.lower().removeprefix("www.")
        return Mention(
            query=query,
            url=url,
            title=item.get("title") or "",
            snippet=item.get("snippet") or "",
            rank=int(item.get("position") or fallback_rank),
            domain=domain,
            source_type="organic",
        )
