"""Sentiment and risk analysis for SERP mentions."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from typing import Iterable

from openai import OpenAI

from app.database import Mention

LOGGER = logging.getLogger(__name__)


class SentimentAnalyzer:
    """Classify mention sentiment using Portuguese keyword signals and optional OpenAI."""

    VALID_LABELS = {"positive", "neutral", "negative", "risky"}

    def __init__(self, config: dict, api_key: str | None) -> None:
        sentiment_config = config.get("sentiment") or {}
        self.use_openai = bool(sentiment_config.get("use_openai", True) and api_key)
        self.model = sentiment_config.get("openai_model", "gpt-4o-mini")
        self.negative_keywords = [word.lower() for word in sentiment_config.get("negative_keywords_pt", [])]
        self.client = OpenAI(api_key=api_key) if self.use_openai else None

    def analyze_many(self, mentions: Iterable[Mention]) -> list[Mention]:
        return [self.analyze(mention) for mention in mentions]

    def analyze(self, mention: Mention) -> Mention:
        text = f"{mention.title}\n{mention.snippet}".lower()
        matched = [keyword for keyword in self.negative_keywords if re.search(rf"\b{re.escape(keyword)}\b", text)]
        heuristic_sentiment = "risky" if matched else "neutral"
        risk_score = min(1.0, 0.25 + (0.15 * len(matched))) if matched else 0.0

        if not self.client:
            return replace(
                mention,
                sentiment=heuristic_sentiment,
                risk_score=risk_score,
                negative_keywords=", ".join(matched),
            )

        try:
            result = self._classify_with_openai(mention, matched)
            label = result.get("sentiment", heuristic_sentiment)
            if label not in self.VALID_LABELS:
                label = heuristic_sentiment
            return replace(
                mention,
                sentiment=label,
                risk_score=max(float(result.get("risk_score", 0.0)), risk_score),
                negative_keywords=", ".join(sorted(set(matched + result.get("negative_keywords", [])))),
            )
        except Exception:  # noqa: BLE001 - keep monitoring running if AI classification fails.
            LOGGER.exception("OpenAI sentiment classification failed for %s", mention.url)
            return replace(
                mention,
                sentiment=heuristic_sentiment,
                risk_score=risk_score,
                negative_keywords=", ".join(matched),
            )

    def _classify_with_openai(self, mention: Mention, matched_keywords: list[str]) -> dict:
        prompt = {
            "url": mention.url,
            "title": mention.title,
            "snippet": mention.snippet,
            "matched_portuguese_negative_keywords": matched_keywords,
        }
        response = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify reputation/SERM risk for a Google result. "
                        "Return JSON with sentiment as positive, neutral, negative, or risky; "
                        "risk_score from 0 to 1; and negative_keywords as an array."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0,
        )
        return json.loads(response.choices[0].message.content or "{}")
