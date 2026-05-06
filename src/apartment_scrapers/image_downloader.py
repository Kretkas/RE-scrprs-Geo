from __future__ import annotations

import logging
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

from .models import Listing

logger = logging.getLogger(__name__)

SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


@dataclass(frozen=True)
class DownloadedImage:
    url: str
    path: Path | None
    ok: bool
    size_bytes: int = 0
    content_type: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ListingImageDownloadResult:
    listing: Listing
    listing_dir: Path
    images: list[DownloadedImage]

    @property
    def ok_images(self) -> list[DownloadedImage]:
        return [image for image in self.images if image.ok and image.path]

    @property
    def failed_images(self) -> list[DownloadedImage]:
        return [image for image in self.images if not image.ok]


class ImageDownloader:
    def __init__(
        self,
        runtime_dir: Path,
        run_id: str,
        max_images: int = 10,
        timeout_seconds: int = 30,
        max_bytes_per_image: int = 15 * 1024 * 1024,
    ) -> None:
        self.runtime_dir = runtime_dir
        self.run_id = run_id
        self.max_images = max_images
        self.timeout_seconds = timeout_seconds
        self.max_bytes_per_image = max_bytes_per_image
        self.root_dir = runtime_dir / "images" / run_id
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Referer": "https://home.ss.ge/",
            }
        )

    @staticmethod
    def safe_name(value: str) -> str:
        return SAFE_NAME_RE.sub("_", value).strip("._") or "unknown"

    def cleanup_old_runs(self, older_than_hours: int = 24) -> int:
        images_root = self.runtime_dir / "images"
        if not images_root.exists():
            return 0

        cutoff = time.time() - older_than_hours * 3600
        removed = 0
        for child in images_root.iterdir():
            if not child.is_dir():
                continue
            try:
                if child.stat().st_mtime < cutoff:
                    shutil.rmtree(child)
                    removed += 1
                    logger.info("ImageDownloader: removed old image run dir=%s", child)
            except FileNotFoundError:
                continue
        return removed

    def listing_dir(self, listing: Listing) -> Path:
        source = self.safe_name(listing.source)
        external_id = self.safe_name(listing.external_id)
        return self.root_dir / f"{source}_{external_id}"

    def extension_for(self, url: str, content_type: str | None) -> str:
        if content_type:
            clean_content_type = content_type.split(";")[0].strip().lower()
            if clean_content_type in CONTENT_TYPE_EXTENSIONS:
                return CONTENT_TYPE_EXTENSIONS[clean_content_type]

        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            return ".jpg" if suffix == ".jpeg" else suffix
        return ".jpg"

    def download_one(self, url: str, destination: Path) -> DownloadedImage:
        try:
            with self.session.get(url, timeout=self.timeout_seconds, stream=True) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type")
                if content_type and not content_type.lower().startswith("image/"):
                    return DownloadedImage(
                        url=url,
                        path=None,
                        ok=False,
                        content_type=content_type,
                        error=f"non-image content-type: {content_type}",
                    )

                size = 0
                with destination.open("wb") as file:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > self.max_bytes_per_image:
                            return DownloadedImage(
                                url=url,
                                path=None,
                                ok=False,
                                size_bytes=size,
                                content_type=content_type,
                                error=f"image exceeds max size {self.max_bytes_per_image}",
                            )
                        file.write(chunk)

                if size == 0:
                    destination.unlink(missing_ok=True)
                    return DownloadedImage(
                        url=url,
                        path=None,
                        ok=False,
                        content_type=content_type,
                        error="empty response body",
                    )

                return DownloadedImage(
                    url=url,
                    path=destination,
                    ok=True,
                    size_bytes=size,
                    content_type=content_type,
                )
        except Exception as exc:
            destination.unlink(missing_ok=True)
            return DownloadedImage(url=url, path=None, ok=False, error=str(exc))

    def download_listing_images(self, listing: Listing) -> ListingImageDownloadResult:
        urls = list(dict.fromkeys(listing.photo_urls))[: self.max_images]
        target_dir = self.listing_dir(listing)
        target_dir.mkdir(parents=True, exist_ok=True)

        images: list[DownloadedImage] = []
        for index, url in enumerate(urls, start=1):
            extension = self.extension_for(url, None)
            destination = target_dir / f"img_{index:02d}{extension}"
            result = self.download_one(url, destination)
            if result.ok and result.content_type:
                expected_extension = self.extension_for(url, result.content_type)
                if destination.suffix != expected_extension and result.path:
                    renamed = destination.with_suffix(expected_extension)
                    destination.rename(renamed)
                    result = DownloadedImage(
                        url=result.url,
                        path=renamed,
                        ok=result.ok,
                        size_bytes=result.size_bytes,
                        content_type=result.content_type,
                        error=result.error,
                    )
            images.append(result)

        logger.info(
            "ImageDownloader: listing source=%s id=%s urls=%s downloaded=%s failed=%s dir=%s",
            listing.source,
            listing.external_id,
            len(urls),
            len([image for image in images if image.ok]),
            len([image for image in images if not image.ok]),
            target_dir,
        )
        return ListingImageDownloadResult(listing=listing, listing_dir=target_dir, images=images)

    def cleanup_listing(self, result: ListingImageDownloadResult) -> None:
        if result.listing_dir.exists():
            shutil.rmtree(result.listing_dir)
            logger.info(
                "ImageDownloader: cleaned listing dir source=%s id=%s dir=%s",
                result.listing.source,
                result.listing.external_id,
                result.listing_dir,
            )

    def cleanup_run(self) -> None:
        if self.root_dir.exists():
            shutil.rmtree(self.root_dir)
            logger.info("ImageDownloader: cleaned run dir=%s", self.root_dir)
