from __future__ import annotations

import json
import logging
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

from scrapling.fetchers import StealthyFetcher

from ..models import Listing
from ..storage import Storage

logger = logging.getLogger(__name__)

SOURCE = "korter"
BASE_URL_TEMPLATE = (
    "https://korter.ge/ru/продажа-квартир-батуми"
    "?market_types=secondary"
    "&seller_type=owner"
    "&page={}"
)

INITIAL_STATE_RE = re.compile(r"window\.INITIAL_STATE\s*=\s*(\{.*?\});", re.DOTALL)


def _parse_initial_state(html: str) -> dict[str, Any] | None:
    match = INITIAL_STATE_RE.search(html)
    if not match:
        return None
    return json.loads(match.group(1))


def _unique_urls(urls: list[str]) -> list[str]:
    return list(dict.fromkeys(url for url in urls if url and url.startswith("http")))


def parse_korter_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        clean = raw.split("+")[0].split(".")[0]
        return datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning("Korter: cannot parse publishTime=%r", raw)
        return None


def get_layout_string(rooms: Any, bedrooms: Any, roominess: Any) -> str | None:
    if roominess == "STUDIO":
        return "Студия"
    try:
        rooms_int = int(rooms) if rooms is not None else 0
        bedrooms_int = int(bedrooms) if bedrooms is not None else 0
    except (ValueError, TypeError):
        return None

    if rooms_int == 1 and bedrooms_int == 0:
        return "Студия"
    if rooms_int > 0 and bedrooms_int > 0:
        living_rooms = rooms_int - bedrooms_int
        if living_rooms >= 0:
            return f"{bedrooms_int}+{living_rooms}"
        return f"{rooms_int}-комн."
    if rooms_int > 0:
        return f"{rooms_int}-комн."
    return None


def _format_price(value: Any, currency: Any) -> str:
    if isinstance(value, (int, float)):
        if currency == "USD":
            return f"${int(value):,}"
        return f"{int(value):,} {currency or ''}".strip()
    if value not in {None, ""}:
        return str(value)
    return "Нет цены"


def _detail_url_from_item(item: dict[str, Any]) -> str | None:
    link_part = item.get("link")
    if not isinstance(link_part, str) or not link_part:
        return None
    if link_part.startswith("http"):
        return link_part
    return f"https://korter.ge{urllib.parse.quote(link_part)}"


def _external_id_from_item(item: dict[str, Any], url: str) -> str | None:
    for key in ("id", "apartmentId", "layoutId"):
        value = item.get(key)
        if value not in {None, ""}:
            return str(value)
    match = re.search(r"(\d{5,})", url)
    if match:
        return match.group(1)
    return None


def fetch_detail_data(url: str) -> dict[str, Any] | None:
    try:
        page = StealthyFetcher.fetch(url, headless=True, timeout=60000)
        data = _parse_initial_state(page.html_content)
        if not data:
            logger.warning("Korter: no INITIAL_STATE on detail url=%s", url)
            return None

        layout = data.get("layoutLandingStore", {}).get("layout", {})
        images: list[str] = []
        for img in layout.get("images", []) or []:
            if not isinstance(img, dict):
                continue
            media_src = img.get("mediaSrc", {})
            default_media = media_src.get("default", {}) if isinstance(media_src, dict) else {}
            src = default_media.get("x2") or default_media.get("x1") if isinstance(default_media, dict) else None
            if isinstance(src, str):
                if src.startswith("//"):
                    src = "https:" + src
                images.append(src)

        return {
            "images": _unique_urls(images),
            "publishTime": layout.get("publishTime"),
            "floorsByHouse": layout.get("floorsByHouse", []),
            "floorNumbers": layout.get("floorNumbers", []),
            "bedrooms": layout.get("bedroomCount"),
            "rooms": layout.get("roomCount"),
            "roominess": layout.get("roominess"),
        }
    except Exception:
        logger.exception("Korter: detail fetch failed url=%s", url)
        return None


def build_listing_from_item(item: dict[str, Any], detail_data: dict[str, Any], url: str) -> Listing | None:
    external_id = _external_id_from_item(item, url)
    if not external_id:
        logger.info("Korter: skip url=%s reason=no-external-id", url)
        return None

    price = item.get("price")
    currency = item.get("currency", "USD")
    price_string = _format_price(price, currency)

    area_value = item.get("area")
    price_per_m2: str | None = None
    if isinstance(price, (int, float)) and price > 0 and isinstance(area_value, (int, float)) and area_value > 0:
        price_per_m2 = f"${round(price / area_value):,}/м²"
        price_string += f" ({price_per_m2})"

    item_floors = item.get("floorNumbers", []) or detail_data.get("floorNumbers", []) or []
    floor = item_floors[0] if isinstance(item_floors, list) and item_floors else "-"

    total_floors_list = detail_data.get("floorsByHouse", [])
    total_floors = "-"
    if isinstance(total_floors_list, list) and total_floors_list and isinstance(total_floors_list[0], dict):
        total_floors = total_floors_list[0].get("floorCount", "-") or "-"

    address_part = item.get("address") or "Адрес не указан"
    building_info = item.get("building", {})
    building_name = building_info.get("name", "") if isinstance(building_info, dict) else ""
    full_address = f"{address_part} ({building_name})" if building_name else str(address_part)

    layout_str = get_layout_string(
        detail_data.get("rooms"),
        detail_data.get("bedrooms"),
        detail_data.get("roominess"),
    )
    layout_line = f"🛋️ <b>Планировка:</b> {layout_str}\n" if layout_str else ""

    area = area_value if area_value is not None else "Не указана"
    caption = (
        f"📍 <b>Адрес:</b> Батуми, {full_address}\n"
        f"💰 <b>Цена:</b> {price_string}\n{layout_line}"
        f"📏 <b>Площадь:</b> {area} кв.м. | 🏢 <b>Этаж:</b> {floor}/{total_floors}\n"
        f"🔗 <a href=\"{url}\">Смотреть объявление на Korter</a>"
    )

    return Listing(
        source=SOURCE,
        external_id=external_id,
        url=url,
        caption=caption,
        address=f"Батуми, {full_address}",
        price=price_string,
        price_per_m2=price_per_m2,
        area=str(area),
        floor=str(floor),
        total_floors=str(total_floors),
        layout=layout_str,
        published_at=parse_korter_datetime(detail_data.get("publishTime")),
        photo_urls=detail_data.get("images", []),
    )


def fetch_search_candidates(max_pages: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for page_num in range(1, max_pages + 1):
        url = BASE_URL_TEMPLATE.format(page_num)
        logger.info("Korter: fetch search page=%s", page_num)
        try:
            page = StealthyFetcher.fetch(url, headless=True, timeout=120000)
            data = _parse_initial_state(page.html_content)
            if not data:
                logger.warning("Korter: no INITIAL_STATE on search page=%s", page_num)
                break

            apartments = data.get("apartmentListingStore", {}).get("apartments", [])
            if not apartments:
                logger.info("Korter: no apartments on search page=%s", page_num)
                break

            for item in apartments:
                if not isinstance(item, dict):
                    continue
                detail_url = _detail_url_from_item(item)
                if not detail_url or detail_url in seen_urls:
                    continue
                seen_urls.add(detail_url)
                candidates.append({"item": item, "url": detail_url})
        except Exception:
            logger.exception("Korter: search page failed page=%s", page_num)
            break

    return candidates


def fetch_listings(
    storage: Storage | None = None,
    hours: int = 24,
    max_pages: int = 3,
    max_workers: int = 5,
    include_seen: bool = False,
    max_listings: int | None = None,
) -> list[Listing]:
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    logger.info("Korter: searching listings published after %s", cutoff_time.isoformat())

    candidates = fetch_search_candidates(max_pages=max_pages)
    if not candidates:
        logger.warning("Korter: no search candidates collected")
        return []

    logger.info("Korter: collected %s search candidates; fetching details", len(candidates))
    listings: list[Listing] = []
    session_seen_ids: set[str] = set()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_candidate = {
            executor.submit(fetch_detail_data, candidate["url"]): candidate
            for candidate in candidates
        }
        for future in as_completed(future_to_candidate):
            candidate = future_to_candidate[future]
            item = candidate["item"]
            url = candidate["url"]

            try:
                detail_data = future.result()
            except Exception as exc:
                logger.exception("Korter: unexpected detail future failure url=%s", url)
                detail_data = None
            if not detail_data:
                continue

            listing = build_listing_from_item(item, detail_data, url)
            if not listing:
                continue
            if listing.external_id in session_seen_ids:
                continue
            session_seen_ids.add(listing.external_id)

            if not include_seen and storage and storage.is_seen(SOURCE, listing.external_id):
                logger.info("Korter: skip seen id=%s", listing.external_id)
                continue

            item_date = listing.published_at
            if not item_date:
                logger.info("Korter: skip id=%s reason=no-date", listing.external_id)
                continue
            if item_date <= cutoff_time:
                logger.info("Korter: skip old id=%s date=%s", listing.external_id, item_date.isoformat())
                continue

            listings.append(listing)
            logger.info(
                "Korter: candidate id=%s date=%s photos=%s url=%s",
                listing.external_id,
                item_date.isoformat(),
                listing.photos_count,
                listing.url,
            )
            if max_listings is not None and len(listings) >= max_listings:
                logger.info("Korter: reached max_listings=%s", max_listings)
                break

    mode = "including seen" if include_seen else "fresh unseen"
    logger.info("Korter: finished with %s %s listings", len(listings), mode)
    return listings
