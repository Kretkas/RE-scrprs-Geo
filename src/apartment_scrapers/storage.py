from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import Listing


SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
    sent_at TEXT,
    status TEXT NOT NULL DEFAULT 'seen',
    photos_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS send_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER,
    attempted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL,
    telegram_message_ids TEXT,
    error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(listing_id) REFERENCES listings(id)
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    stats_json TEXT
);
"""


class Storage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def is_seen(self, source: str, external_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM listings WHERE source = ? AND external_id = ? LIMIT 1",
                (source, external_id),
            ).fetchone()
        return row is not None

    def get_listing_id(self, source: str, external_id: str) -> int | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM listings WHERE source = ? AND external_id = ? LIMIT 1",
                (source, external_id),
            ).fetchone()
        return int(row["id"]) if row else None

    def upsert_listing(self, listing: Listing, status: str = "seen") -> int:
        self.initialize()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO listings (source, external_id, url, status, photos_count)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, external_id) DO UPDATE SET
                    url = excluded.url,
                    last_seen_at = CURRENT_TIMESTAMP,
                    photos_count = excluded.photos_count,
                    error = NULL
                """,
                (
                    listing.source,
                    listing.external_id,
                    listing.url,
                    status,
                    listing.photos_count,
                ),
            )
            row = conn.execute(
                "SELECT id FROM listings WHERE source = ? AND external_id = ?",
                (listing.source, listing.external_id),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"Failed to upsert listing {listing.source}:{listing.external_id}")
        return int(row["id"])

    def mark_sent(
        self,
        source: str,
        external_id: str,
        telegram_message_ids: list[int] | None = None,
        retry_count: int = 0,
    ) -> None:
        listing_id = self.get_listing_id(source, external_id)
        if listing_id is None:
            raise KeyError(f"Listing not found: {source}:{external_id}")

        message_ids_json = json.dumps(telegram_message_ids or [], ensure_ascii=False)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE listings
                SET status = 'sent', sent_at = CURRENT_TIMESTAMP, error = NULL
                WHERE id = ?
                """,
                (listing_id,),
            )
            conn.execute(
                """
                INSERT INTO send_attempts (listing_id, status, telegram_message_ids, retry_count)
                VALUES (?, 'sent', ?, ?)
                """,
                (listing_id, message_ids_json, retry_count),
            )

    def mark_failed(
        self,
        source: str,
        external_id: str,
        error: str,
        status: str = "failed",
        retry_count: int = 0,
    ) -> None:
        listing_id = self.get_listing_id(source, external_id)
        if listing_id is None:
            raise KeyError(f"Listing not found: {source}:{external_id}")

        with self.connect() as conn:
            conn.execute(
                "UPDATE listings SET status = ?, error = ? WHERE id = ?",
                (status, error, listing_id),
            )
            conn.execute(
                """
                INSERT INTO send_attempts (listing_id, status, error, retry_count)
                VALUES (?, ?, ?, ?)
                """,
                (listing_id, status, error, retry_count),
            )

    def get_counts(self) -> dict[str, Any]:
        with self.connect() as conn:
            by_source = conn.execute(
                "SELECT source, COUNT(*) AS count FROM listings GROUP BY source ORDER BY source"
            ).fetchall()
            by_status = conn.execute(
                "SELECT status, COUNT(*) AS count FROM listings GROUP BY status ORDER BY status"
            ).fetchall()
            by_source_status = conn.execute(
                """
                SELECT source, status, COUNT(*) AS count
                FROM listings
                GROUP BY source, status
                ORDER BY source, status
                """
            ).fetchall()

        return {
            "by_source": {row["source"]: int(row["count"]) for row in by_source},
            "by_status": {row["status"]: int(row["count"]) for row in by_status},
            "by_source_status": {
                f"{row['source']}:{row['status']}": int(row["count"])
                for row in by_source_status
            },
        }
