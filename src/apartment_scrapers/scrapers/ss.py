from __future__ import annotations

import json
import logging
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from bs4 import BeautifulSoup
from scrapling.fetchers import StealthyFetcher

from ..models import Listing
from ..storage import Storage

logger = logging.getLogger(__name__)

SOURCE = "ss"
BASE_URL_TEMPLATE = (
    "https://home.ss.ge/ru/недвижимость/l/Квартира/Продается"
    "?cityIdList=96"
    "&subdistrictIds=57%2C58%2C59%2C63%2C64%2C65%2C66%2C91"
    "&currencyId=2"
    "&advancedSearch=%7B%22individualEntityOnly%22%3Atrue%7D"
    "&order=1"
    "&page={}"
)

NEXT_DATA_RE = re.compile(
    r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)


@dataclass(frozen=True)
class ImageExtractionResult:
    urls: list[str]
    source: str
    error: str | None = None


def _unique_urls(urls: list[str]) -> list[str]:
    return list(dict.fromkeys(url for url in urls if url and url.startswith("http")))


def _parse_next_data(html: str) -> dict[str, Any] | None:
    match = NEXT_DATA_RE.search(html)
    if not match:
        return None
    return json.loads(match.group(1))


def parse_ss_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    clean = raw.split("+")[0].split(".")[0].replace("T", " ")
    try:
        return datetime.strptime(clean, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning("SS: cannot parse orderDate=%r", raw)
        return None


def get_layout_string(rooms: Any, bedrooms: Any) -> str | None:
    if rooms is None:
        return None
    try:
        rooms_int = int(rooms)
        bedrooms_int = int(bedrooms) if bedrooms is not None else 0
    except (ValueError, TypeError):
        return None

    if rooms_int == 1 and bedrooms_int in {0, 1}:
        return "Студия"
    if rooms_int > 1 and bedrooms_int > 0 and rooms_int > bedrooms_int:
        return f"{bedrooms_int}+{rooms_int - bedrooms_int}"
    if rooms_int > 1 and rooms_int == bedrooms_int:
        return f"{bedrooms_int} спальни"
    if rooms_int > 0:
        return f"{rooms_int}-комн."
    return None


def extract_images_from_html(html: str) -> ImageExtractionResult:
    next_data_images: list[str] = []
    try:
        data = _parse_next_data(html)
        if data:
            app_data = data.get("props", {}).get("pageProps", {}).get("applicationData", {})
            app_images = app_data.get("appImages", []) or []
            for img in app_images:
                img_url = img.get("fileName") if isinstance(img, dict) else None
                if img_url:
                    next_data_images.append(img_url)
    except Exception as exc:
        logger.warning("SS: image __NEXT_DATA__ parse failed: %s", exc)

    next_data_images = _unique_urls(next_data_images)
    if next_data_images:
        return ImageExtractionResult(urls=next_data_images, source="__NEXT_DATA__")

    soup = BeautifulSoup(html, "html.parser")
    og_images = _unique_urls(
        [meta.get("content", "") for meta in soup.find_all("meta", property="og:image")]
    )
    if og_images:
        return ImageExtractionResult(urls=og_images, source="og:image")

    return ImageExtractionResult(urls=[], source="none", error="no images found in __NEXT_DATA__ or og:image")


def fetch_images_for_listing(url: str) -> ImageExtractionResult:
    try:
        page = StealthyFetcher.fetch(url, headless=True, timeout=60000)
        return extract_images_from_html(page.html_content)
    except Exception as exc:
        logger.exception("SS: detail fetch/image extraction failed url=%s", url)
        return ImageExtractionResult(urls=[], source="error", error=str(exc))


def build_listing_from_item(item: dict[str, Any]) -> Listing | None:
    item_id = str(item.get("applicationId", "")).strip()
    if not item_id:
        return None

    detail_url_part = item.get("detailUrl", "")
    if not detail_url_part:
        logger.info("SS: skip id=%s reason=no-detail-url", item_id)
        return None
    url_link = f"https://home.ss.ge/ru/недвижимость/{urllib.parse.quote(detail_url_part)}"

    price_info = item.get("price", {}) or {}
    usd_price = price_info.get("priceUsd", "Нет цены")
    sq_price = price_info.get("unitPriceUsd", "")
    if isinstance(usd_price, (int, float)):
        usd_price = f"${int(usd_price):,}"
    if isinstance(sq_price, (int, float)):
        sq_price = f"${int(sq_price):,}"
    price_string = f"{usd_price}"
    if sq_price:
        price_string += f" ({sq_price}/м²)"

    rooms = item.get("rooms")
    if rooms is None:
        title = item.get("title", "")
        rooms_match = re.search(r"(\d+)-комнатная", title)
        if rooms_match:
            rooms = rooms_match.group(1)

    bedrooms = item.get("bedrooms")
    if bedrooms is None:
        bedrooms = item.get("numberOfBedrooms")

    layout_str = get_layout_string(rooms, bedrooms)
    layout_line = f"🛏️ <b>Планировка:</b> {layout_str}\n" if layout_str else ""

    area = item.get("totalArea", "Н/у")
    floor = item.get("floorNumber", "-") or "-"
    total_floors = item.get("totalAmountOfFloor", "-") or "-"

    address_info = item.get("address", {}) or {}
    district = address_info.get("subdistrictTitle", "")
    street = address_info.get("streetTitle", "")
    street_num = address_info.get("streetNumber", "")
    full_street = (
        f"{street} {street_num}"
        if street and street_num
        else street or (f"дом {street_num}" if street_num else "")
    )
    full_address = ", ".join(filter(None, [district, full_street])) or "Адрес не указан"

    caption = (
        f"📍 <b>Адрес:</b> Батуми, {full_address}\n"
        f"💰 <b>Цена:</b> {price_string}\n{layout_line}"
        f"📏 <b>Площадь:</b> {area} кв.м. | 🏢 <b>Этаж:</b> {floor}/{total_floors}\n"
        f"🔗 <a href=\"{url_link}\">Смотреть объявление на SS</a>"
    )

    return Listing(
        source=SOURCE,
        external_id=item_id,
        url=url_link,
        caption=caption,
        address=f"Батуми, {full_address}",
        price=price_string,
        area=str(area),
        floor=str(floor),
        total_floors=str(total_floors),
        layout=layout_str,
        published_at=parse_ss_datetime(item.get("orderDate")),
        photo_urls=[],
    )


def fetch_listings(
    storage: Storage | None = None,
    hours: int = 24,
    max_pages: int = 9,
    max_workers: int = 5,
    include_seen: bool = False,
    max_listings: int | None = None,
) -> list[Listing]:
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    logger.info("SS: searching listings published after %s", cutoff_time.isoformat())

    session_seen_ids: set[str] = set()
    candidates: list[Listing] = []
    stop_pagination = False

    for page_num in range(1, max_pages + 1):
        if stop_pagination:
            break
        url = BASE_URL_TEMPLATE.format(page_num)
        logger.info("SS: fetch search page=%s", page_num)

        try:
            page = StealthyFetcher.fetch(url, headless=True, timeout=120000)
            data = _parse_next_data(page.html_content)
            if not data:
                logger.warning("SS: no __NEXT_DATA__ on search page=%s", page_num)
                break

            items = data["props"]["pageProps"]["applicationList"].get("realStateItemModel", [])
            if not items:
                logger.info("SS: no items on search page=%s", page_num)
                break

            for item in items:
                item_id = str(item.get("applicationId", "")).strip()
                if not item_id or item_id in session_seen_ids:
                    continue
                session_seen_ids.add(item_id)

                if not include_seen and storage and storage.is_seen(SOURCE, item_id):
                    logger.info("SS: skip seen id=%s", item_id)
                    continue

                item_date = parse_ss_datetime(item.get("orderDate"))
                if not item_date:
                    logger.info("SS: skip id=%s reason=no-date", item_id)
                    continue

                is_vip = (item.get("vipStatus", 0) or 0) > 0
                if item_date <= cutoff_time:
                    if is_vip:
                        logger.info("SS: skip old vip id=%s date=%s", item_id, item_date.isoformat())
                        continue
                    stop_pagination = True
                    logger.info("SS: stop pagination at old non-vip id=%s date=%s", item_id, item_date.isoformat())
                    break

                listing = build_listing_from_item(item)
                if listing:
                    candidates.append(listing)
                    logger.info("SS: candidate id=%s date=%s url=%s", listing.external_id, item_date.isoformat(), listing.url)
                    if max_listings is not None and len(candidates) >= max_listings:
                        stop_pagination = True
                        logger.info("SS: reached max_listings=%s", max_listings)
                        break
        except Exception:
            logger.exception("SS: search page failed page=%s", page_num)
            break

    mode = "including seen" if include_seen else "fresh unseen"
    logger.info("SS: collected %s %s candidates; fetching photos", len(candidates), mode)
    if not candidates:
        return []

    listing_by_id = {listing.external_id: listing for listing in candidates}
    image_results: dict[str, ImageExtractionResult] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_id = {
            executor.submit(fetch_images_for_listing, listing.url): listing.external_id
            for listing in candidates
        }
        for future in as_completed(future_to_id):
            external_id = future_to_id[future]
            try:
                result = future.result()
            except Exception as exc:
                logger.exception("SS: unexpected image future failure id=%s", external_id)
                result = ImageExtractionResult(urls=[], source="future_error", error=str(exc))
            image_results[external_id] = result

    listings: list[Listing] = []
    for external_id, listing in listing_by_id.items():
        result = image_results.get(external_id, ImageExtractionResult([], "missing", "image result missing"))
        with_photos = Listing(
            source=listing.source,
            external_id=listing.external_id,
            url=listing.url,
            caption=listing.caption,
            address=listing.address,
            price=listing.price,
            price_per_m2=listing.price_per_m2,
            area=listing.area,
            floor=listing.floor,
            total_floors=listing.total_floors,
            layout=listing.layout,
            published_at=listing.published_at,
            photo_urls=result.urls,
        )
        logger.info(
            "SS: photos id=%s count=%s source=%s error=%s",
            external_id,
            len(result.urls),
            result.source,
            result.error,
        )
        listings.append(with_photos)

    logger.info("SS: finished with %s listings", len(listings))
    return listings
