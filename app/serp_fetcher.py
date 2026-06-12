"""Serper.dev Google SERP client with demo fallback data."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx

from app.database import Mention

LOGGER = logging.getLogger(__name__)


class SerperQueryError(RuntimeError):
    """Raised when one Serper query fails but the run can continue."""

    def __init__(self, message: str, *, status_code: int | None = None, response_body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body

    @property
    def is_quota_error(self) -> bool:
        return self.status_code == 400 and "not enough credits" in self.response_body.lower()


class SerperQuotaError(RuntimeError):
    """Raised when Serper has no credits and no live snapshot can be collected."""


class SerperSerpFetcher:
    """Fetch organic Google results from Serper.dev or generate demo mentions."""

    BASE_URL = "https://google.serper.dev/search"

    def __init__(self, api_key: str | None, config: dict) -> None:
        self.api_key = api_key
        self.config = config
        self.demo_mode = not api_key

    def fetch_all(self) -> list[Mention]:
        monitoring = self.config.get("monitoring") or {}
        queries = monitoring.get("queries") or []
        if self.demo_mode:
            LOGGER.warning("Demo mode: SERPER_API_KEY is missing, using generated SERP results with no live Google data")
            return self._fetch_demo_results()

        LOGGER.info("Live mode with Serper.dev: fetching %d configured queries", len(queries))
        mentions: list[Mention] = []
        successful_queries: list[str] = []
        failed_queries: list[str] = []
        quota_failed_queries: list[str] = []
        for query in queries:
            try:
                mentions.extend(self.fetch_query(query))
                successful_queries.append(str(query))
            except SerperQueryError as exc:
                LOGGER.error("Serper.dev query failed for query=%s: %s", query, exc)
                failed_queries.append(str(query))
                if exc.is_quota_error:
                    quota_failed_queries.append(str(query))
            except Exception as exc:  # noqa: BLE001 - continue monitoring remaining queries.
                LOGGER.error("Serper.dev query failed for query=%s: %s", query, exc)
                failed_queries.append(str(query))
        LOGGER.info(
            "Serper.dev query summary: successful=%d failed=%d failed_queries=%s",
            len(successful_queries),
            len(failed_queries),
            failed_queries,
        )
        if queries and failed_queries and not successful_queries:
            if len(quota_failed_queries) == len(failed_queries):
                raise SerperQuotaError("All Serper.dev queries failed because the account has no remaining credits")
            raise RuntimeError(f"All Serper.dev queries failed: {failed_queries}")
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
        headers = {"X-API-KEY": self.api_key or "", "Content-Type": "application/json"}

        LOGGER.info("Fetching Serper.dev top-%d organic results for query=%s", depth, query)
        with httpx.Client(timeout=60) as client:
            try:
                response = client.post(self.BASE_URL, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                LOGGER.exception("Serper.dev request error for query=%s payload=%s", query, payload)
                raise SerperQueryError(str(exc)) from exc

            if response.status_code >= 400:
                LOGGER.error("Serper.dev non-200 response for query=%s", query)
                LOGGER.error("Serper.dev status_code=%s", response.status_code)
                LOGGER.error("Serper.dev response_body=%s", response.text)
                LOGGER.error("Serper.dev request_payload=%s", payload)
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise SerperQueryError(
                        str(exc),
                        status_code=response.status_code,
                        response_body=response.text,
                    ) from exc

            try:
                data = response.json()
            except ValueError as exc:
                LOGGER.exception("Serper.dev returned invalid JSON for query=%s payload=%s", query, payload)
                raise SerperQueryError(str(exc)) from exc

        organic_items = data.get("organic") or []
        return [self._to_mention(query, item, rank) for rank, item in enumerate(organic_items[:depth], start=1)]

    def _fetch_demo_results(self) -> list[Mention]:
        monitoring = self.config.get("monitoring") or {}
        queries = monitoring.get("queries") or ["Demo Brand"]
        max_results = max(1, int(monitoring.get("demo_results_per_query", monitoring.get("depth", 10))))

        mentions: list[Mention] = []
        for query in queries:
            mentions.extend(self._demo_mentions_for_query(query, max_results))
        return mentions

    def _demo_mentions_for_query(self, query: str, max_results: int) -> list[Mention]:
        slug = self._slugify(query)
        mentions: list[Mention] = []
        for rank in range(1, max_results + 1):
            risky = rank == 2
            path = "risk-mention" if risky else f"result-{rank}"
            title = (
                f"Reclamação and fraude risk mention for {query}"
                if risky
                else f"Demo organic result {rank} for {query}"
            )
            snippet = (
                "Demo reputation result containing reclamação, fraude and risco signals for sentiment testing."
                if risky
                else "Reference organic result generated for workflow validation and SERP snapshot storage."
            )
            url = f"https://example.com/demo-serp/{slug}/{path}"
            mentions.append(
                Mention(
                    query=query,
                    url=url,
                    title=title,
                    snippet=snippet,
                    rank=rank,
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
    def _to_mention(query: str, item: dict, organic_rank: int) -> Mention:
        url = item.get("link") or item.get("url") or ""
        domain = urlparse(url).netloc.lower().removeprefix("www.")
        return Mention(
            query=query,
            url=url,
            title=item.get("title") or "",
            snippet=item.get("snippet") or "",
            rank=organic_rank,
            domain=domain,
            source_type="organic",
        )
