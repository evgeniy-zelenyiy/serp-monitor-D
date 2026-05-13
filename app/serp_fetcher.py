"""DataForSEO Google SERP client with demo fallback data."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx

from app.database import Mention

LOGGER = logging.getLogger(__name__)


class DataForSeoSerpFetcher:
    """Fetch organic Google results from DataForSEO or generate demo mentions."""

    BASE_URL = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"

    def __init__(self, login: str, password: str, config: dict) -> None:
        self.login = login
        self.password = password
        self.config = config
        self.demo_mode = not (login and password)

    def fetch_all(self) -> list[Mention]:
        if self.demo_mode:
            LOGGER.warning("DEMO MODE - no live Google data. DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD are missing.")
            return self._fetch_demo_results()

        monitoring = self.config.get("monitoring") or {}
        mentions: list[Mention] = []
        for query in monitoring.get("queries", []):
            mentions.extend(self.fetch_query(query))
        return mentions

    def fetch_query(self, query: str) -> list[Mention]:
        monitoring = self.config.get("monitoring") or {}
        payload = [
            {
                "keyword": query,
                "location_code": monitoring.get("location_code", 2076),
                "language_code": monitoring.get("language_code", "pt"),
                "device": monitoring.get("device", "desktop"),
                "os": monitoring.get("os", "windows"),
                "depth": monitoring.get("depth", 20),
            }
        ]
        LOGGER.info("Fetching Google SERP for query=%s", query)
        with httpx.Client(timeout=60) as client:
            response = client.post(self.BASE_URL, auth=(self.login, self.password), json=payload)
            response.raise_for_status()
            data = response.json()

        task = data.get("tasks", [{}])[0]
        if task.get("status_code") not in (20000, 20100):
            raise RuntimeError(f"DataForSEO task failed: {task.get('status_message')}")

        items = task.get("result", [{}])[0].get("items", [])
        organic_items = [item for item in items if item.get("type") == "organic"]
        return [self._to_mention(query, item) for item in organic_items]

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
                )
            )
        return mentions

    @staticmethod
    def _slugify(value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
        return cleaned or "demo-query"

    @staticmethod
    def _to_mention(query: str, item: dict) -> Mention:
        url = item.get("url", "")
        domain = urlparse(url).netloc.lower().removeprefix("www.")
        return Mention(
            query=query,
            url=url,
            title=item.get("title") or "",
            snippet=item.get("description") or "",
            rank=int(item.get("rank_group") or item.get("rank_absolute") or 0),
            domain=domain,
        )
