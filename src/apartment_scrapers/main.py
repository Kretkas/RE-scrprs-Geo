from __future__ import annotations

import argparse
import logging
import sys

from .config import Settings
from .logging_config import configure_logging
from .orchestrator import Orchestrator
from .storage import Storage
from .telegram_sender import TelegramSender

logger = logging.getLogger(__name__)

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apartment Scrapers 2.0")
    parser.add_argument("--source", action="append", choices=["myhome", "ss", "korter"], help="Run only selected source. Can be used multiple times.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of listings to process globally.")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode: do not send Telegram messages.")
    parser.add_argument("--send", action="store_true", help="Allow real Telegram sending. Requires env config.")
    parser.add_argument("--include-seen", action="store_true", help="Include listings already present in SQLite. Useful for diagnostics only.")
    return parser.parse_args(argv)

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = Settings.from_env()
    if args.dry_run:
        settings = settings.with_dry_run(True)
    if args.send:
        settings = settings.with_dry_run(False)

    run_id = configure_logging(settings.logs_dir, settings.log_level)
    logger.info("Boot Scrapers 2.0 run_id=%s", run_id)

    storage = Storage(settings.data_dir / "scrapers.db")
    sender = TelegramSender(settings)
    orchestrator = Orchestrator(settings, storage, sender, run_id=run_id)
    return orchestrator.run(sources=args.source or settings.active_sources, limit=args.limit, include_seen=args.include_seen)

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
