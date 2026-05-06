from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from scrapling.fetchers import StealthyFetcher

from ..models import Listing
from ..storage import Storage

logger = logging.getLogger(__name__)

SOURCE = "myhome"
BASE_URL_TEMPLATE = (
    "https://www.myhome.ge/ru/nedvizhimost/prodazha/staroe-zdanie/kvartira/"
    "batumi/aeroportis-ubani/"
    "?deal_types=1"
    "&real_estate_types=1"
    "&currency_id=1"
    "&CardView=3"
    "&statuses=1%2C2%2C3"
    "&conditions=1%2C2%2C3%2C4%2C6%2C7%2C8%2C5"
    "&cities=15"
    "&urbans=77%2C73%2C72%2C74%2C75%2C76%2C71"
    "&districts=15%2C9%2C8%2C10%2C11%2C13%2C7"
    "&page={}"
    "&owner_type=physical"
)

NEXT_DATA_RE = re.compile(
    r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)


def _parse_next_data(html: str) -> dict[str, Any] | None:
    match = NEXT_DATA_RE.search(html)
    if not match:
        return None
    return json.loads(match.group(1))


def parse_myhome_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning("MyHome: cannot parse last_updated=%r", raw)
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


def extract_listing_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    queries = data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
    for query in queries:
        state = query.get("state", {}) if isinstance(query, dict) else {}
        state_data = state.get("data")
        if not isinstance(state_data, dict):
            continue
        possible_listings = state_data.get("data", {}).get("data")
        if (
            isinstance(possible_listings, list)
            and possible_listings
            and isinstance(possible_listings[0], dict)
            and "price" in possible_listings[0]
        ):
            return possible_listings
    return []


def _format_price(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"${int(value):,}"
    return str(value) if value not in {None, ""} else "Нет цены"


def _format_square_price(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        return f"${int(value):,}/м²"
    if value not in {None, ""}:
        return str(value)
    return None


def _image_urls(item: dict[str, Any]) -> list[str]:
    images_data = item.get("images", []) or []
    urls: list[str] = []
    for image in images_data:
        if not isinstance(image, dict):
            continue
        image_url = image.get("large") or image.get("medium") or image.get("thumb")
        if image_url and isinstance(image_url, str) and image_url.startswith("http"):
            urls.append(image_url)
    return list(dict.fromkeys(urls))


def build_listing_from_item(item: dict[str, Any]) -> Listing | None:
    item_id = str(item.get("id", "")).strip()
    if not item_id:
        return None

    url_link = f"https://www.myhome.ge/ru/pr/{item_id}/"

    price_info = item.get("price", {}).get("2", {}) if isinstance(item.get("price"), dict) else {}
    usd_price = price_info.get("price_total", "Нет цены") if isinstance(price_info, dict) else "Нет цены"
    sq_price = price_info.get("price_square", "") if isinstance(price_info, dict) else ""
    price_string = _format_price(usd_price)
    sq_price_string = _format_square_price(sq_price)
    if sq_price_string:
        price_string += f" ({sq_price_string})"

    rooms = item.get("room")
    bedrooms = item.get("bedroom")
    layout_str = get_layout_string(rooms, bedrooms)
    layout_line = f"🛏️ <b>Планировка:</b> {layout_str}\n" if layout_str else ""

    area = item.get("area", "Н/у")
    floor = item.get("floor", "-") or "-"
    total_floors = item.get("total_floors", "-") or "-"
    full_address = ", ".join(
        filter(None, [item.get("city_name"), item.get("district_name"), item.get("address")])
    ) or "Адрес не указан"

    caption = (
        f"📍 <b>Адрес:</b> {full_address}\n"
        f"💰 <b>Цена:</b> {price_string}\n{layout_line}"
        f"📏 <b>Площадь:</b> {area} кв.м. | 🏢 <b>Этаж:</b> {floor}/{total_floors}\n"
        f"🔗 <a href=\"{url_link}\">Смотреть объявление на MyHome</a>"
    )

    return Listing(
        source=SOURCE,
        external_id=item_id,
        url=url_link,
        caption=caption,
        address=full_address,
        price=price_string,
        price_per_m2=sq_price_string,
        area=str(area),
        floor=str(floor),
        total_floors=str(total_floors),
        layout=layout_str,
        published_at=parse_myhome_datetime(item.get("last_updated")),
        photo_urls=_image_urls(item),
    )


def fetch_listings(
    storage: Storage | None = None,
    hours: int = 24,
    max_pages: int = 9,
    include_seen: bool = False,
    max_listings: int | None = None,
) -> list[Listing]:
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    logger.info("MyHome: searching listings updated after %s", cutoff_time.isoformat())

    session_seen_ids: set[str] = set()
    candidates: list[Listing] = []
    stop_pagination = False

    for page_num in range(1, max_pages + 1):
        if stop_pagination:
            break
        url = BASE_URL_TEMPLATE.format(page_num)
        logger.info("MyHome: fetch search page=%s", page_num)

        try:
            page = StealthyFetcher.fetch(url, headless=True, timeout=240000)
            data = _parse_next_data(page.html_content)
            if not data:
                logger.warning("MyHome: no __NEXT_DATA__ on search page=%s", page_num)
                break

            items = extract_listing_items(data)
            if not items:
                logger.info("MyHome: no listings on search page=%s", page_num)
                break

            for item in items:
                item_id = str(item.get("id", "")).strip()
                if not item_id or item_id in session_seen_ids:
                    continue
                session_seen_ids.add(item_id)

                is_strictly_vip = bool(item.get("is_vip") or item.get("is_vip_plus") or item.get("is_super_vip"))
                if is_strictly_vip:
                    logger.info("MyHome: skip vip id=%s", item_id)
                    continue

                if not include_seen and storage and storage.is_seen(SOURCE, item_id):
                    logger.info("MyHome: skip seen id=%s", item_id)
                    continue

                item_date = parse_myhome_datetime(item.get("last_updated"))
                if not item_date:
                    logger.info("MyHome: skip id=%s reason=no-date", item_id)
                    continue

                if item_date <= cutoff_time:
                    if item.get("is_promoted", False):
                        logger.info("MyHome: skip old promoted id=%s date=%s", item_id, item_date.isoformat())
                        continue
                    stop_pagination = True
                    logger.info("MyHome: stop pagination at old non-promoted id=%s date=%s", item_id, item_date.isoformat())
                    break

                listing = build_listing_from_item(item)
                if listing:
                    candidates.append(listing)
                    logger.info(
                        "MyHome: candidate id=%s date=%s photos=%s url=%s",
                        listing.external_id,
                        item_date.isoformat(),
                        listing.photos_count,
                        listing.url,
                    )
                    if max_listings is not None and len(candidates) >= max_listings:
                        stop_pagination = True
                        logger.info("MyHome: reached max_listings=%s", max_listings)
                        break
        except Exception:
            logger.exception("MyHome: search page failed page=%s", page_num)
            break

    mode = "including seen" if include_seen else "fresh unseen"
    logger.info("MyHome: finished with %s %s listings", len(candidates), mode)
    return candidates
