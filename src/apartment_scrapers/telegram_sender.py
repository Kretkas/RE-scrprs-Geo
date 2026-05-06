from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .config import Settings
from .image_downloader import ImageDownloader, ListingImageDownloadResult
from .models import Listing

logger = logging.getLogger(__name__)


@dataclass
class SendResult:
    success: bool
    status: str
    error: str | None = None
    retry_429: int = 0
    telegram_message_ids: list[int] | None = None


class TelegramSender:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()

    @property
    def api_base(self) -> str:
        if not self.settings.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not configured")
        return f"https://api.telegram.org/bot{self.settings.telegram_bot_token}"

    def _post_with_retries(
        self,
        method: str,
        *,
        data: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        timeout: int = 90,
        retries: int = 5,
    ) -> tuple[bool, dict[str, Any] | None, str | None, int]:
        retry_429 = 0
        url = f"{self.api_base}/{method}"
        last_error: str | None = None

        for attempt in range(1, retries + 1):
            opened_files: dict[str, Any] = {}
            try:
                if files:
                    for name, path in files.items():
                        opened_files[name] = Path(path).open("rb")

                response = self.session.post(
                    url,
                    data=data,
                    json=json_payload,
                    files=opened_files or None,
                    timeout=timeout,
                )

                try:
                    body = response.json()
                except ValueError:
                    body = None

                if response.status_code == 200:
                    return True, body, None, retry_429

                if response.status_code == 429:
                    retry_429 += 1
                    retry_after = 30
                    if body:
                        retry_after = int(body.get("parameters", {}).get("retry_after", retry_after))
                    logger.warning(
                        "Telegram %s got 429 attempt=%s/%s retry_after=%s",
                        method,
                        attempt,
                        retries,
                        retry_after,
                    )
                    time.sleep(retry_after + 1)
                    continue

                error = response.text
                last_error = error
                logger.warning(
                    "Telegram %s failed status=%s attempt=%s/%s body=%s",
                    method,
                    response.status_code,
                    attempt,
                    retries,
                    error,
                )
                time.sleep(min(5 * attempt, 30))
            except requests.RequestException as exc:
                last_error = str(exc)
                logger.warning(
                    "Telegram %s request exception attempt=%s/%s error=%s",
                    method,
                    attempt,
                    retries,
                    exc,
                )
                time.sleep(min(5 * attempt, 30))
            finally:
                for file_obj in opened_files.values():
                    file_obj.close()

        return False, None, last_error or f"{method} failed after {retries} attempts", retry_429

    @staticmethod
    def _message_ids(body: dict[str, Any] | None) -> list[int]:
        if not body or not body.get("ok"):
            return []
        result = body.get("result")
        if isinstance(result, list):
            return [int(item["message_id"]) for item in result if isinstance(item, dict) and "message_id" in item]
        if isinstance(result, dict) and "message_id" in result:
            return [int(result["message_id"])]
        return []

    def send_text(self, text: str) -> SendResult:
        self.settings.validate_for_send()
        ok, body, error, retry_429 = self._post_with_retries(
            "sendMessage",
            data={
                "chat_id": self.settings.telegram_chat_id,
                "text": text,
                "disable_web_page_preview": "true",
                "parse_mode": "HTML",
                "disable_notification": "true",
            },
            timeout=30,
        )
        return SendResult(
            success=ok,
            status="text_sent" if ok else "text_failed",
            error=error,
            retry_429=retry_429,
            telegram_message_ids=self._message_ids(body),
        )

    def send_photo(self, photo_path: Path, caption: str | None = None) -> SendResult:
        self.settings.validate_for_send()
        data = {
            "chat_id": self.settings.telegram_chat_id,
            "disable_notification": "true",
        }
        if caption:
            data["caption"] = caption
            data["parse_mode"] = "HTML"

        ok, body, error, retry_429 = self._post_with_retries(
            "sendPhoto",
            data=data,
            files={"photo": photo_path},
            timeout=90,
        )
        return SendResult(
            success=ok,
            status="photo_sent" if ok else "photo_failed",
            error=error,
            retry_429=retry_429,
            telegram_message_ids=self._message_ids(body),
        )

    def send_media_group_files(self, image_paths: list[Path], caption: str) -> SendResult:
        self.settings.validate_for_send()
        if not image_paths:
            return self.send_text(caption)
        if len(image_paths) == 1:
            return self.send_photo(image_paths[0], caption=caption)

        media = []
        files: dict[str, Path] = {}
        for index, image_path in enumerate(image_paths[:10]):
            attach_name = f"photo{index}"
            item: dict[str, Any] = {"type": "photo", "media": f"attach://{attach_name}"}
            if index == 0:
                item["caption"] = caption
                item["parse_mode"] = "HTML"
            media.append(item)
            files[attach_name] = image_path

        ok, body, error, retry_429 = self._post_with_retries(
            "sendMediaGroup",
            data={
                "chat_id": self.settings.telegram_chat_id,
                "media": json.dumps(media, ensure_ascii=False),
                "disable_notification": "true",
            },
            files=files,
            timeout=120,
        )
        return SendResult(
            success=ok,
            status="media_group_sent" if ok else "media_group_failed",
            error=error,
            retry_429=retry_429,
            telegram_message_ids=self._message_ids(body),
        )

    @staticmethod
    def _invalid_media_index(error: str | None) -> int | None:
        if not error or "PHOTO_INVALID_DIMENSIONS" not in error:
            return None
        match = re.search(r"message #(\d+)", error)
        if not match:
            return None
        return int(match.group(1)) - 1

    def send_downloaded_listing(self, listing: Listing, download_result: ListingImageDownloadResult) -> SendResult:
        image_paths = [image.path for image in download_result.ok_images if image.path]
        if not image_paths:
            logger.warning(
                "Telegram: no local images available; fallback to text source=%s id=%s failed_downloads=%s",
                listing.source,
                listing.external_id,
                len(download_result.failed_images),
            )
            return self.send_text(listing.caption)

        active_paths = image_paths[:10]
        total_retry_429 = 0
        removed_invalid_paths: list[Path] = []

        while len(active_paths) >= 2:
            result = self.send_media_group_files(active_paths, listing.caption)
            total_retry_429 += result.retry_429
            if result.success:
                result.retry_429 = total_retry_429
                if removed_invalid_paths:
                    result.status = "media_group_sent_filtered"
                    logger.info(
                        "Telegram: sent media group after filtering invalid photos source=%s id=%s photos=%s removed=%s messages=%s retry_429=%s",
                        listing.source,
                        listing.external_id,
                        len(active_paths),
                        len(removed_invalid_paths),
                        result.telegram_message_ids,
                        result.retry_429,
                    )
                else:
                    logger.info(
                        "Telegram: sent media group from local files source=%s id=%s photos=%s messages=%s retry_429=%s",
                        listing.source,
                        listing.external_id,
                        len(active_paths),
                        result.telegram_message_ids,
                        result.retry_429,
                    )
                return result

            invalid_index = self._invalid_media_index(result.error)
            if invalid_index is None or invalid_index < 0 or invalid_index >= len(active_paths):
                logger.warning(
                    "Telegram: media group failed; fallback to text, no individual photo spam source=%s id=%s error=%s",
                    listing.source,
                    listing.external_id,
                    result.error,
                )
                text_result = self.send_text(listing.caption)
                text_result.retry_429 += total_retry_429
                return text_result

            removed_path = active_paths.pop(invalid_index)
            removed_invalid_paths.append(removed_path)
            logger.warning(
                "Telegram: removed invalid photo from media group and will retry source=%s id=%s invalid_index=%s remaining=%s error=%s",
                listing.source,
                listing.external_id,
                invalid_index,
                len(active_paths),
                result.error,
            )

        logger.warning(
            "Telegram: not enough valid photos for media group; fallback to text source=%s id=%s removed_invalid=%s",
            listing.source,
            listing.external_id,
            len(removed_invalid_paths),
        )
        text_result = self.send_text(listing.caption)
        text_result.retry_429 += total_retry_429
        return text_result

    def send_listing(self, listing: Listing, image_downloader: ImageDownloader | None = None) -> SendResult:
        if self.settings.dry_run:
            if image_downloader:
                result = image_downloader.download_listing_images(listing)
                ok_count = len(result.ok_images)
                failed_count = len(result.failed_images)
                logger.info(
                    "dry-run: prepared local photos source=%s external_id=%s found_urls=%s downloaded=%s failed=%s url=%s",
                    listing.source,
                    listing.external_id,
                    listing.photos_count,
                    ok_count,
                    failed_count,
                    listing.url,
                )
                image_downloader.cleanup_listing(result)
                return SendResult(success=True, status="dry_run_downloaded")

            logger.info(
                "dry-run: would send listing source=%s external_id=%s photos=%s url=%s",
                listing.source,
                listing.external_id,
                listing.photos_count,
                listing.url,
            )
            return SendResult(success=True, status="dry_run")

        self.settings.validate_for_send()
        if not image_downloader:
            logger.warning("Telegram: no image downloader provided; sending text only source=%s id=%s", listing.source, listing.external_id)
            return self.send_text(listing.caption)

        download_result = image_downloader.download_listing_images(listing)
        try:
            return self.send_downloaded_listing(listing, download_result)
        finally:
            image_downloader.cleanup_listing(download_result)
