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
    risk_level: str = "none"
    risk_keywords: str = ""
    negative_keywords: str = ""
    source_type: str = "organic"
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
                first_seen TEXT,
                last_seen TEXT,
                query TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT,
                snippet TEXT,
                rank INTEGER NOT NULL,
                domain TEXT,
                source_type TEXT NOT NULL DEFAULT 'organic',
                sentiment TEXT NOT NULL,
                risk_score REAL NOT NULL,
                risk_level TEXT NOT NULL DEFAULT 'none',
                risk_keywords TEXT,
                negative_keywords TEXT,
                screenshot_path TEXT,
                UNIQUE(run_id, query, url)
            );

            CREATE INDEX IF NOT EXISTS idx_mentions_query_url ON mentions(query, url);
            CREATE INDEX IF NOT EXISTS idx_mentions_collected_at ON mentions(collected_at);
            """
        )
        self._ensure_columns()
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_mentions_seen ON mentions(first_seen, last_seen)")
        self.connection.commit()

    def _ensure_columns(self) -> None:
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(mentions)").fetchall()}
        migrations = {
            "first_seen": "ALTER TABLE mentions ADD COLUMN first_seen TEXT",
            "last_seen": "ALTER TABLE mentions ADD COLUMN last_seen TEXT",
            "source_type": "ALTER TABLE mentions ADD COLUMN source_type TEXT NOT NULL DEFAULT 'organic'",
            "risk_level": "ALTER TABLE mentions ADD COLUMN risk_level TEXT NOT NULL DEFAULT 'none'",
            "risk_keywords": "ALTER TABLE mentions ADD COLUMN risk_keywords TEXT",
        }
        for column, statement in migrations.items():
            if column not in columns:
                LOGGER.info("Adding %s column to mentions table", column)
                self.connection.execute(statement)

        self.connection.execute("UPDATE mentions SET first_seen = collected_at WHERE first_seen IS NULL")
        self.connection.execute("UPDATE mentions SET last_seen = collected_at WHERE last_seen IS NULL")
        self.connection.execute("UPDATE mentions SET source_type = 'organic' WHERE source_type IS NULL OR source_type = ''")
        self.connection.execute("UPDATE mentions SET risk_keywords = negative_keywords WHERE risk_keywords IS NULL")
        self.connection.execute(
            """
            UPDATE mentions
            SET risk_level = CASE
                WHEN sentiment = 'negative' OR risk_score >= 0.75 THEN 'high'
                WHEN sentiment = 'risky' OR risk_score >= 0.35 THEN 'medium'
                WHEN risk_score > 0 THEN 'low'
                ELSE 'none'
            END
            WHERE risk_level IS NULL OR risk_level = '' OR risk_level = 'none'
            """
        )

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

    def first_seen(self, query: str, url: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT first_seen FROM mentions
            WHERE query = ? AND url = ?
            ORDER BY first_seen ASC, collected_at ASC, id ASC
            LIMIT 1
            """,
            (query, url),
        ).fetchone()
        return str(row["first_seen"]) if row and row["first_seen"] else None

    def save_mentions(self, run_id: str, mentions: Iterable[Mention]) -> list[MentionChange]:
        collected_at = datetime.now(timezone.utc).isoformat()
        changes: list[MentionChange] = []

        for mention in mentions:
            previous_rank = self.previous_rank(mention.query, mention.url)
            first_seen = self.first_seen(mention.query, mention.url) or collected_at
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
                    run_id, collected_at, first_seen, last_seen, query, url, title, snippet,
                    rank, domain, source_type, sentiment, risk_score, risk_level,
                    risk_keywords, negative_keywords, screenshot_path
                ) VALUES (
                    :run_id, :collected_at, :first_seen, :last_seen, :query, :url, :title, :snippet,
                    :rank, :domain, :source_type, :sentiment, :risk_score, :risk_level,
                    :risk_keywords, :negative_keywords, :screenshot_path
                )
                """,
                {
                    "run_id": run_id,
                    "collected_at": collected_at,
                    "first_seen": first_seen,
                    "last_seen": collected_at,
                    **asdict(mention),
                },
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
