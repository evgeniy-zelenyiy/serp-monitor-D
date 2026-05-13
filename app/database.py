"""SQLite persistence for SERP monitoring history."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Mention:
    query: str
    url: str
    title: str
    snippet: str
    rank: int
    domain: str
    sentiment: str = "neutral"
    risk_score: float = 0.0
    negative_keywords: str = ""
    screenshot_path: str | None = None


@dataclass(frozen=True)
class MentionChange:
    mention: Mention
    is_new_url: bool
    previous_rank: int | None
    rank_delta: int | None


class SerpDatabase:
    """Thin repository layer for SQLite operations."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row

    def initialize(self) -> None:
        LOGGER.info("Initializing SQLite database at %s", self.path)
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS mentions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                query TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT,
                snippet TEXT,
                rank INTEGER NOT NULL,
                domain TEXT,
                sentiment TEXT NOT NULL,
                risk_score REAL NOT NULL,
                negative_keywords TEXT,
                screenshot_path TEXT,
                UNIQUE(run_id, query, url)
            );

            CREATE INDEX IF NOT EXISTS idx_mentions_query_url ON mentions(query, url);
            CREATE INDEX IF NOT EXISTS idx_mentions_collected_at ON mentions(collected_at);
            """
        )
        self.connection.commit()

    def previous_rank(self, query: str, url: str) -> int | None:
        row = self.connection.execute(
            """
            SELECT rank FROM mentions
            WHERE query = ? AND url = ?
            ORDER BY collected_at DESC, id DESC
            LIMIT 1
            """,
            (query, url),
        ).fetchone()
        return int(row["rank"]) if row else None

    def save_mentions(self, run_id: str, mentions: Iterable[Mention]) -> list[MentionChange]:
        collected_at = datetime.now(timezone.utc).isoformat()
        changes: list[MentionChange] = []

        for mention in mentions:
            previous_rank = self.previous_rank(mention.query, mention.url)
            rank_delta = None if previous_rank is None else previous_rank - mention.rank
            changes.append(
                MentionChange(
                    mention=mention,
                    is_new_url=previous_rank is None,
                    previous_rank=previous_rank,
                    rank_delta=rank_delta,
                )
            )
            self.connection.execute(
                """
                INSERT OR IGNORE INTO mentions (
                    run_id, collected_at, query, url, title, snippet, rank, domain,
                    sentiment, risk_score, negative_keywords, screenshot_path
                ) VALUES (
                    :run_id, :collected_at, :query, :url, :title, :snippet, :rank, :domain,
                    :sentiment, :risk_score, :negative_keywords, :screenshot_path
                )
                """,
                {"run_id": run_id, "collected_at": collected_at, **asdict(mention)},
            )
        self.connection.commit()
        return changes

    def latest_mentions(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT * FROM mentions
            WHERE run_id = (SELECT run_id FROM mentions ORDER BY collected_at DESC LIMIT 1)
            ORDER BY query, rank
            """
        ).fetchall()

    def close(self) -> None:
        self.connection.close()
