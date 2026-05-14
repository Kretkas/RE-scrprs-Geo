from __future__ import annotations

import json
import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

import curl_cffi.requests as cffi_requests

from ..models import Listing
from ..storage import Storage

logger = logging.getLogger(__name__)

SOURCE = "korter"
BASE_URL_TEMPLATE = (
    "https://korter.ge/ru/продажа-квартир-батуми"
    "?market_types=secondary"
    "&seller_type=owner"
    "&sort=update_time_desc"
    "&page={}"
)


def fetch_korter_page(page: int = 1) -> str:
    url = BASE_URL_TEMPLATE.format(page)
    logger.info("Korter: fetch search page=%s", page)
    resp = cffi_requests.get(
        url,
        impersonate="chrome110",
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


def extract_initial_state(html: str) -> dict[str, Any]:
    marker = "window.INITIAL_STATE = "
    start = html.find(marker)
    if start == -1:
        raise ValueError("window.INITIAL_STATE not found in page")
    start += len(marker)

    depth = 0
    end = start
    for i, ch in enumerate(html[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if depth == 0:
            end = i
            break

    return json.loads(html[start:end + 1])


def get_pagination(state: dict[str, Any]) -> dict[str, Any]:
    return state.get("apartmentListingStore", {}).get("pagination", {})


def parse_korter_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        clean = raw.split("+")[0].split(".")[0]
        return datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning("Korter: cannot parse publishTime=%r", raw)
        return None


def get_layout_string(rooms: Any) -> str | None:
    try:
        rooms_int = int(rooms) if rooms is not None else 0
    except (ValueError, TypeError):
        return None

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


def build_listing_from_item(item: dict[str, Any]) -> Listing | None:
    if item.get("availableStatus") != "available":
        return None

    external_id = str(item.get("objectId"))
    if not external_id or external_id == "None":
        logger.info("Korter: skip reason=no-object-id")
        return None

    url = _detail_url_from_item(item)
    if not url:
        return None

    price = item.get("price")
    currency = item.get("currency", "USD")
    price_string = _format_price(price, currency)

    area_value = item.get("area")
    price_per_m2: str | None = None
    if isinstance(price, (int, float)) and price > 0 and isinstance(area_value, (int, float)) and area_value > 0:
        price_per_m2 = f"${round(price / area_value):,}/м²"
        price_string += f" ({price_per_m2})"

    item_floors = item.get("floorNumbers", [])
    floor = item_floors[0] if isinstance(item_floors, list) and item_floors else "-"

    address_part = item.get("address") or "Адрес не указан"
    building_info = item.get("building", {})
    building_name = building_info.get("name", "") if isinstance(building_info, dict) else ""
    full_address = f"{address_part} ({building_name})" if building_name else str(address_part)
    
    district = item.get("subLocalityNominative")
    if district:
        full_address = f"{full_address}, {district}"

    rooms = item.get("roomCount")
    layout_str = get_layout_string(rooms)
    layout_line = f"🛋️ <b>Планировка:</b> {layout_str}\n" if layout_str else ""

    area = area_value if area_value is not None else "Не указана"
    caption = (
        f"📍 <b>Адрес:</b> Батуми, {full_address}\n"
        f"💰 <b>Цена:</b> {price_string}\n{layout_line}"
        f"📏 <b>Площадь:</b> {area} кв.м. | 🏢 <b>Этаж:</b> {floor}\n"
        f"🔗 <a href=\"{url}\">Смотреть объявление на Korter</a>"
    )

    photo_urls = []
    media = item.get("mediaSrc", {}).get("default", {})
    if media:
        photo = media.get("x2") or media.get("x1")
        if photo:
            if photo.startswith("//"):
                photo = "https:" + photo
            photo_urls.append(photo)

    published_at = parse_korter_datetime(item.get("actualizeTime"))

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
        total_floors="-",  # Not available in listing data
        layout=layout_str,
        published_at=published_at,
        photo_urls=photo_urls,
    )


def fetch_listings(
    storage: Storage | None = None,
    hours: int = 24,
    max_pages: int = 3,
    max_workers: int = 5,  # Kept for compatibility, though not used anymore
    include_seen: bool = False,
    max_listings: int | None = None,
) -> list[Listing]:
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    logger.info("Korter: searching listings published after %s", cutoff_time.isoformat())

    listings: list[Listing] = []
    session_seen_ids: set[str] = set()
    
    try:
        html = fetch_korter_page(page=1)
        state = extract_initial_state(html)
        pagination = get_pagination(state)
        total_pages = pagination.get("totalPages", 1)
    except Exception as e:
        logger.error("Korter: initial page fetch failed: %s", e)
        return []

    if max_pages:
        total_pages = min(total_pages, max_pages)

    logger.info("Korter: %d pages total to process", total_pages)
    
    # Process page 1 since we already fetched it
    raw_apartments = state.get("apartmentListingStore", {}).get("apartments", [])
    
    # Process remaining pages
    for page in range(1, total_pages + 1):
        if page > 1:
            try:
                html = fetch_korter_page(page=page)
                state = extract_initial_state(html)
                raw_apartments = state.get("apartmentListingStore", {}).get("apartments", [])
            except Exception as e:
                logger.error("Korter: page %d failed: %s", page, e)
                break

        added_from_page = 0
        for item in raw_apartments:
            listing = build_listing_from_item(item)
            if not listing:
                continue
                
            if listing.external_id in session_seen_ids:
                continue
            session_seen_ids.add(listing.external_id)

            if not include_seen and storage and storage.is_seen(SOURCE, listing.external_id):
                logger.debug("Korter: skip seen id=%s", listing.external_id)
                continue

            item_date = listing.published_at
            if not item_date:
                logger.debug("Korter: skip id=%s reason=no-date", listing.external_id)
                continue
                
            if item_date <= cutoff_time:
                logger.debug("Korter: skip old id=%s date=%s", listing.external_id, item_date.isoformat())
                continue

            listings.append(listing)
            added_from_page += 1
            
            if max_listings is not None and len(listings) >= max_listings:
                logger.info("Korter: reached max_listings=%s", max_listings)
                break
                
        logger.info("Korter: page %d/%d, added %d fresh items", page, total_pages, added_from_page)
        
        if max_listings is not None and len(listings) >= max_listings:
            break

    mode = "including seen" if include_seen else "fresh unseen"
    logger.info("Korter: finished with %s %s listings", len(listings), mode)
    return listings
