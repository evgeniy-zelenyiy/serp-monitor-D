"""SQLite persistence for SERP snapshot history."""

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
    rank: int | None
    domain: str
    sentiment: str = "neutral"
    risk_score: float = 0.0
    risk_level: str = "none"
    risk_keywords: str = ""
    negative_keywords: str = ""
    source_type: str = "organic"
    date_published: str | None = None
    date_published_source: str | None = None
    date_published_confidence: float | None = None
    screenshot_path: str | None = None


@dataclass(frozen=True)
class MentionChange:
    mention: Mention
    status: str
    is_new_url: bool
    is_existing: bool
    is_changed: bool
    previous_rank: int | None
    rank_delta: int | None
    first_seen: str | None
    last_seen: str | None
    disappeared_at: str | None = None


class SerpDatabase:
    """Repository layer for SERP runs and per-query top-10 snapshots."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row

    def initialize(self) -> None:
        LOGGER.info("Initializing SQLite database at %s", self.path)
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                run_datetime TEXT NOT NULL,
                country TEXT NOT NULL,
                language TEXT NOT NULL,
                device TEXT NOT NULL,
                mode TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS serp_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                run_datetime TEXT NOT NULL,
                query TEXT NOT NULL,
                country TEXT NOT NULL,
                language TEXT NOT NULL,
                device TEXT NOT NULL,
                url TEXT NOT NULL,
                domain TEXT,
                title TEXT,
                snippet TEXT,
                rank INTEGER,
                source_type TEXT NOT NULL DEFAULT 'organic',
                sentiment TEXT NOT NULL DEFAULT 'neutral',
                risk_score REAL NOT NULL DEFAULT 0.0,
                risk_level TEXT NOT NULL DEFAULT 'none',
                risk_keywords TEXT,
                negative_keywords TEXT,
                date_published TEXT,
                date_published_source TEXT,
                date_published_confidence REAL,
                first_seen TEXT,
                last_seen TEXT,
                previous_rank INTEGER,
                rank_delta INTEGER,
                disappeared_at TEXT,
                status TEXT NOT NULL,
                screenshot_path TEXT,
                UNIQUE(run_id, query, url, status)
            );

            CREATE INDEX IF NOT EXISTS idx_runs_datetime ON runs(run_datetime);
            CREATE INDEX IF NOT EXISTS idx_snapshots_query_url ON serp_snapshots(query, url);
            CREATE INDEX IF NOT EXISTS idx_snapshots_run_query ON serp_snapshots(run_id, query, rank);
            CREATE INDEX IF NOT EXISTS idx_snapshots_status ON serp_snapshots(status);
            """
        )
        self._ensure_legacy_mentions_table()
        self.connection.commit()

    def _ensure_legacy_mentions_table(self) -> None:
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
                rank INTEGER,
                domain TEXT,
                source_type TEXT NOT NULL DEFAULT 'organic',
                sentiment TEXT NOT NULL DEFAULT 'neutral',
                risk_score REAL NOT NULL DEFAULT 0.0,
                risk_level TEXT NOT NULL DEFAULT 'none',
                risk_keywords TEXT,
                negative_keywords TEXT,
                screenshot_path TEXT,
                UNIQUE(run_id, query, url)
            );
            """
        )

    def save_run(self, run_id: str, run_datetime: str, country: str, language: str, device: str, mode: str) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO runs (run_id, run_datetime, country, language, device, mode)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, run_datetime, country, language, device, mode),
        )
        self.connection.commit()

    def previous_snapshot(self, query: str, url: str, before_run_datetime: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT * FROM serp_snapshots
            WHERE query = ? AND url = ? AND run_datetime < ?
            ORDER BY run_datetime DESC, id DESC
            LIMIT 1
            """,
            (query, url, before_run_datetime),
        ).fetchone()

    def first_seen(self, query: str, url: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT first_seen FROM serp_snapshots
            WHERE query = ? AND url = ?
            ORDER BY first_seen ASC, run_datetime ASC, id ASC
            LIMIT 1
            """,
            (query, url),
        ).fetchone()
        return str(row["first_seen"]) if row and row["first_seen"] else None

    def latest_query_rows(self, query: str, before_run_datetime: str) -> list[sqlite3.Row]:
        previous_run = self.connection.execute(
            """
            SELECT run_id FROM serp_snapshots
            WHERE query = ? AND run_datetime < ?
            ORDER BY run_datetime DESC
            LIMIT 1
            """,
            (query, before_run_datetime),
        ).fetchone()
        if not previous_run:
            return []
        return self.connection.execute(
            """
            SELECT * FROM serp_snapshots
            WHERE query = ? AND run_id = ? AND status != 'disappeared'
            """,
            (query, previous_run["run_id"]),
        ).fetchall()

    def save_serp_snapshot(
        self,
        run_id: str,
        run_datetime: str,
        query: str,
        country: str,
        language: str,
        device: str,
        mode: str,
        mentions: Iterable[Mention],
        screenshot_path: str | None,
        track_disappeared: bool = True,
    ) -> list[MentionChange]:
        self.save_run(run_id, run_datetime, country, language, device, mode)
        mentions = list(mentions)
        current_urls = {mention.url for mention in mentions}
        changes: list[MentionChange] = []

        for mention in mentions:
            previous = self.previous_snapshot(query, mention.url, run_datetime)
            previous_rank = int(previous["rank"]) if previous and previous["rank"] is not None else None
            first_seen = self.first_seen(query, mention.url) or run_datetime
            last_seen = run_datetime
            is_new = previous is None
            is_changed = previous_rank is not None and mention.rank is not None and previous_rank != mention.rank
            status = "new" if is_new else "changed" if is_changed else "existing"
            rank_delta = None if previous_rank is None or mention.rank is None else previous_rank - mention.rank
            stored = self._with_screenshot(mention, screenshot_path)
            changes.append(
                MentionChange(
                    mention=stored,
                    status=status,
                    is_new_url=is_new,
                    is_existing=not is_new,
                    is_changed=is_changed,
                    previous_rank=previous_rank,
                    rank_delta=rank_delta,
                    first_seen=first_seen,
                    last_seen=last_seen,
                )
            )
            self._insert_snapshot(
                run_id,
                run_datetime,
                query,
                country,
                language,
                device,
                stored,
                first_seen,
                last_seen,
                previous_rank,
                rank_delta,
                None,
                status,
            )
            self._insert_legacy_mention(run_id, run_datetime, stored, first_seen, last_seen)

        if track_disappeared:
            for previous in self.latest_query_rows(query, run_datetime):
                if previous["url"] in current_urls:
                    continue
                mention = self._mention_from_row(previous)
                rank = int(previous["rank"]) if previous["rank"] is not None else None
                change = MentionChange(
                    mention=mention,
                    status="disappeared",
                    is_new_url=False,
                    is_existing=True,
                    is_changed=False,
                    previous_rank=rank,
                    rank_delta=None,
                    first_seen=previous["first_seen"],
                    last_seen=previous["last_seen"],
                    disappeared_at=run_datetime,
                )
                changes.append(change)
                self._insert_snapshot(
                    run_id,
                    run_datetime,
                    query,
                    country,
                    language,
                    device,
                    mention,
                    previous["first_seen"],
                    previous["last_seen"],
                    rank,
                    None,
                    run_datetime,
                    "disappeared",
                )

        self.connection.commit()
        return changes

    @staticmethod
    def _with_screenshot(mention: Mention, screenshot_path: str | None) -> Mention:
        if not screenshot_path:
            return mention
        return Mention(**{**asdict(mention), "screenshot_path": screenshot_path})

    def _insert_snapshot(
        self,
        run_id: str,
        run_datetime: str,
        query: str,
        country: str,
        language: str,
        device: str,
        mention: Mention,
        first_seen: str | None,
        last_seen: str | None,
        previous_rank: int | None,
        rank_delta: int | None,
        disappeared_at: str | None,
        status: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO serp_snapshots (
                run_id, run_datetime, query, country, language, device, url, domain, title,
                snippet, rank, source_type, sentiment, risk_score, risk_level, risk_keywords,
                negative_keywords, date_published, date_published_source, date_published_confidence,
                first_seen, last_seen, previous_rank, rank_delta, disappeared_at, status, screenshot_path
            ) VALUES (
                :run_id, :run_datetime, :query, :country, :language, :device, :url, :domain, :title,
                :snippet, :rank, :source_type, :sentiment, :risk_score, :risk_level, :risk_keywords,
                :negative_keywords, :date_published, :date_published_source, :date_published_confidence,
                :first_seen, :last_seen, :previous_rank, :rank_delta, :disappeared_at, :status, :screenshot_path
            )
            """,
            {
                "run_id": run_id,
                "run_datetime": run_datetime,
                "query": query,
                "country": country,
                "language": language,
                "device": device,
                "first_seen": first_seen,
                "last_seen": last_seen,
                "previous_rank": previous_rank,
                "rank_delta": rank_delta,
                "disappeared_at": disappeared_at,
                "status": status,
                **asdict(mention),
            },
        )

    def _insert_legacy_mention(self, run_id: str, collected_at: str, mention: Mention, first_seen: str, last_seen: str) -> None:
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
            {"run_id": run_id, "collected_at": collected_at, "first_seen": first_seen, "last_seen": last_seen, **asdict(mention)},
        )

    @staticmethod
    def _mention_from_row(row: sqlite3.Row) -> Mention:
        return Mention(
            query=row["query"],
            url=row["url"],
            title=row["title"] or row["url"],
            snippet=row["snippet"] or "",
            rank=int(row["rank"]) if row["rank"] is not None else None,
            domain=row["domain"] or "",
            sentiment=row["sentiment"] or "neutral",
            risk_score=float(row["risk_score"] or 0.0),
            risk_level=row["risk_level"] or "none",
            risk_keywords=row["risk_keywords"] or row["negative_keywords"] or "",
            negative_keywords=row["negative_keywords"] or "",
            source_type=row["source_type"] or "organic",
            date_published=row["date_published"],
            date_published_source=row["date_published_source"],
            date_published_confidence=row["date_published_confidence"],
            screenshot_path=row["screenshot_path"],
        )

    def save_mentions(self, run_id: str, mentions: Iterable[Mention]) -> list[MentionChange]:
        run_datetime = datetime.now(timezone.utc).isoformat()
        changes: list[MentionChange] = []
        for query in sorted({mention.query for mention in mentions}):
            group = [mention for mention in mentions if mention.query == query]
            changes.extend(self.save_serp_snapshot(run_id, run_datetime, query, "", "", "desktop", "legacy", group, None))
        return changes

    def latest_mentions(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT * FROM serp_snapshots
            WHERE run_id = (SELECT run_id FROM serp_snapshots ORDER BY run_datetime DESC LIMIT 1)
            ORDER BY query, rank
            """
        ).fetchall()

    def close(self) -> None:
        self.connection.close()
