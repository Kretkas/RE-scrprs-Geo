from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import PROJECT_ROOT
from .storage import Storage


@dataclass(frozen=True)
class LegacySeenSource:
    source: str
    path: Path


def read_ids(path: Path) -> list[str]:
    if not path.exists():
        return []

    seen: set[str] = set()
    ordered: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        item_id = raw_line.strip()
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        ordered.append(item_id)
    return ordered


def import_legacy_seen(storage: Storage, sources: list[LegacySeenSource]) -> dict[str, dict[str, int]]:
    storage.initialize()
    summary: dict[str, dict[str, int]] = {}

    with storage.connect() as conn:
        for source in sources:
            ids = read_ids(source.path)
            inserted = 0
            existing = 0

            for external_id in ids:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO listings (source, external_id, url, status, photos_count)
                    VALUES (?, ?, ?, 'legacy_seen', 0)
                    """,
                    (source.source, external_id, f"legacy://{source.source}/{external_id}"),
                )
                if cursor.rowcount == 1:
                    inserted += 1
                else:
                    existing += 1

            summary[source.source] = {
                "read": len(ids),
                "inserted": inserted,
                "existing": existing,
            }

    return summary


def default_sources(project_root: Path = PROJECT_ROOT) -> list[LegacySeenSource]:
    legacy = project_root / "legacy_snapshot"
    return [
        LegacySeenSource("myhome", legacy / "seen_myhome_ids.txt"),
        LegacySeenSource("ss", legacy / "seen_ss_ids.txt"),
    ]


def count_by_source(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT source, COUNT(*) FROM listings GROUP BY source ORDER BY source"
        ).fetchall()
    return {source: count for source, count in rows}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import legacy seen_*.txt IDs into Scrapers 2.0 SQLite database.")
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "scrapers.db")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    storage = Storage(args.db)
    summary = import_legacy_seen(storage, default_sources())
    counts = count_by_source(args.db)

    print("Legacy seen import summary:")
    for source, stats in summary.items():
        print(
            f"- {source}: read={stats['read']} inserted={stats['inserted']} existing={stats['existing']}"
        )

    print("Database counts:")
    for source, count in counts.items():
        print(f"- {source}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
