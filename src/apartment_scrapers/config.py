from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}

@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    logs_dir: Path = PROJECT_ROOT / "logs"
    runtime_dir: Path = PROJECT_ROOT / "runtime"
    dry_run: bool = True
    log_level: str = "INFO"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    send_delay_seconds: float = 4.0
    max_photos_per_listing: int = 10
    active_sources: list[str] = None
    limit_myhome: int | None = None
    limit_ss: int | None = None
    limit_korter: int | None = None

    def __post_init__(self):
        if self.active_sources is None:
            object.__setattr__(self, 'active_sources', ["myhome", "ss", "korter"])

    @classmethod
    def from_env(cls) -> "Settings":
        active_sources_raw = os.getenv("SCRAPERS_ACTIVE_SOURCES", "myhome,ss,korter")
        active_sources = [s.strip() for s in active_sources_raw.split(",") if s.strip()]

        def _int_env(name: str) -> int | None:
            val = os.getenv(name)
            return int(val) if val and val.isdigit() else None

        return cls(
            dry_run=_bool_env("SCRAPERS_DRY_RUN", True),
            log_level=os.getenv("SCRAPERS_LOG_LEVEL", "INFO"),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            send_delay_seconds=float(os.getenv("SCRAPERS_SEND_DELAY_SECONDS", "4")),
            max_photos_per_listing=int(os.getenv("SCRAPERS_MAX_PHOTOS_PER_LISTING", "10")),
            active_sources=active_sources,
            limit_myhome=_int_env("SCRAPERS_LIMIT_MYHOME"),
            limit_ss=_int_env("SCRAPERS_LIMIT_SS"),
            limit_korter=_int_env("SCRAPERS_LIMIT_KORTER"),
        )

    def with_dry_run(self, dry_run: bool) -> "Settings":
        return replace(self, dry_run=dry_run)

    def validate_for_send(self) -> None:
        missing = []
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.telegram_chat_id:
            missing.append("TELEGRAM_CHAT_ID")
        if missing:
            raise ValueError("Missing required Telegram config: " + ", ".join(missing))
