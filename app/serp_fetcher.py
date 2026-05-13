"""DataForSEO Google SERP client."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from app.database import Mention

LOGGER = logging.getLogger(__name__)


class DataForSeoSerpFetcher:
    """Fetch organic Google results from the DataForSEO SERP API."""

    BASE_URL = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"

    def __init__(self, login: str, password: str, config: dict) -> None:
        self.login = login
        self.password = password
        self.config = config

    def fetch_all(self) -> list[Mention]:
        if not self.login or not self.password:
            raise RuntimeError("DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD are required")

        mentions: list[Mention] = []
        for query in self.config["monitoring"]["queries"]:
            mentions.extend(self.fetch_query(query))
        return mentions

    def fetch_query(self, query: str) -> list[Mention]:
        monitoring = self.config["monitoring"]
        payload = [
            {
                "keyword": query,
                "location_code": monitoring["location_code"],
                "language_code": monitoring["language_code"],
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
