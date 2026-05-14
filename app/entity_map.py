"""Build a simple entity map from SERP mentions."""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from app.database import MentionChange

LOGGER = logging.getLogger(__name__)


class EntityMapBuilder:
    """Create JSON graph of queries, domains, URLs, and sentiment distribution."""

    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def build(self, changes: Iterable[MentionChange]) -> dict:
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        sentiment_by_domain: dict[str, Counter] = defaultdict(Counter)

        for change in changes:
            mention = change.mention
            query_id = f"query:{mention.query}"
            domain_id = f"domain:{mention.domain}"
            url_id = f"url:{mention.url}"
            nodes.setdefault(query_id, {"id": query_id, "type": "query", "label": mention.query})
            nodes.setdefault(domain_id, {"id": domain_id, "type": "domain", "label": mention.domain})
            nodes[url_id] = {
                "id": url_id,
                "type": "url",
                "label": mention.title or mention.url,
                "url": mention.url,
                "source_type": mention.source_type,
                "rank": mention.rank,
                "sentiment": mention.sentiment,
                "risk_score": mention.risk_score,
                "risk_level": mention.risk_level,
                "risk_keywords": mention.risk_keywords,
            }
            edges.append({"source": query_id, "target": url_id, "type": "returns", "rank": mention.rank})
            edges.append({"source": url_id, "target": domain_id, "type": "hosted_on"})
            sentiment_by_domain[mention.domain][mention.sentiment] += 1

        for domain, sentiment_counts in sentiment_by_domain.items():
            nodes[f"domain:{domain}"]["sentiment_counts"] = dict(sentiment_counts)

        entity_map = {"nodes": list(nodes.values()), "edges": edges}
        self.output_path.write_text(json.dumps(entity_map, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("Wrote entity map to %s", self.output_path)
        return entity_map
