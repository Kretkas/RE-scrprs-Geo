from __future__ import annotations

import json
import logging
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import time
import asyncio

import httpx

from ..models import Listing
from ..storage import Storage

logger = logging.getLogger(__name__)

SOURCE = "ss"
SEARCH_URL = "https://api-gateway.ss.ge/v1/RealEstate/LegendSearch"
SESSION_URL = "https://home.ss.ge/ru/"

HEADERS = {
    "Accept": "application/json",
    "Origin": "https://home.ss.ge",
    "Referer": "https://home.ss.ge/",
    "os": "web",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
    ),
}

_token_cache = {"token": None, "expires_at": 0, "cookies": None}

def get_token() -> tuple[str, httpx.Cookies]:
    if time.time() < _token_cache["expires_at"] - 60 and _token_cache["cookies"]:
        return _token_cache["token"], _token_cache["cookies"]

    logger.info("SS.ge API: refreshing token")
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            session_resp = client.get(
                SESSION_URL,
                headers=HEADERS,
            )
        session_resp.raise_for_status()
        cookies = session_resp.cookies
        token = cookies.get("ss-session-token")
        if not token:
            raise RuntimeError("SS.ge did not provide ss-session-token")
    except Exception as e:
        logger.error("SS.ge API: token refresh failed: %s", e)
        raise

    _token_cache["token"] = token
    _token_cache["cookies"] = cookies
    _token_cache["expires_at"] = time.time() + 3600
    logger.info("SS.ge API: new anonymous session token obtained")
    return _token_cache["token"], _token_cache["cookies"]

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

    photo_urls = []
    for img in item.get("appImages", []):
        if isinstance(img, dict) and "fileName" in img:
            photo_urls.append(img["fileName"])

    return Listing(
        source=SOURCE,
        external_id=item_id,
        url=url_link,
        caption=caption,
        address=f"Батуми, {full_address}",
        price=price_string,
        price_per_m2="",
        area=str(area),
        floor=str(floor),
        total_floors=str(total_floors),
        layout=layout_str,
        published_at=parse_ss_datetime(item.get("orderDate")),
        photo_urls=photo_urls,
    )

async def _fetch_ss_api_page(page: int) -> list[dict]:
    try:
        token, cookies = get_token()
    except Exception:
        return []

    body = {
        "realEstateType": 5,
        "realEstateDealType": 4,
        "cityIdList": [96],
        "subdistrictIds": [57, 58, 59, 63, 64, 65, 66, 91],
        "currencyId": 2,
        "advancedSearch": {
            "individualEntityOnly": True
        },
        "page": page,
        "pageSize": 20
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                SEARCH_URL,
                json=body,
                headers={**HEADERS, "Authorization": f"Bearer {token}"},
                cookies=cookies,
            )
            resp.raise_for_status()
    except httpx.TimeoutException:
        logger.error("SS.ge API: timeout on page %d", page)
        return []
    except httpx.HTTPStatusError as e:
        logger.error("SS.ge API: HTTP %d %s", e.response.status_code, e.response.text)
        return []
    except httpx.RequestError as e:
        logger.error("SS.ge API: request error: %s", e)
        return []

    data = resp.json()
    items = data.get("realStateItemModel", [])
    logger.info("SS.ge API: fetched %d items (page %d)", len(items), page)
    return items

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
        logger.info("SS: fetch search page=%s", page_num)

        items = asyncio.run(_fetch_ss_api_page(page_num))
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

    mode = "including seen" if include_seen else "fresh unseen"
    logger.info("SS: collected %s %s candidates", len(candidates), mode)
    return candidates
