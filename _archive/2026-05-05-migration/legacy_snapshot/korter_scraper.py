import sys
import json
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from scrapling.fetchers import StealthyFetcher
from telegram_utils import send_telegram_message, send_telegram_media_group

def get_detail_data(url_link):
    try:
        page = StealthyFetcher.fetch(url_link, headless=True, timeout=60000)
        match = re.search(r'window\.INITIAL_STATE\s*=\s*(\{.*?\});', page.html_content, re.DOTALL)
        if not match: return None
        
        data = json.loads(match.group(1))
        layout = data.get('layoutLandingStore', {}).get('layout', {})
        
        images = []
        for img in layout.get('images', []):
            if 'mediaSrc' in img and 'default' in img['mediaSrc']:
                src = img['mediaSrc']['default'].get('x2') or img['mediaSrc']['default'].get('x1')
                if src:
                    if src.startswith('//'):
                        src = 'https:' + src
                    images.append(src)
        
        return {
            'images': images,
            'publishTime': layout.get('publishTime'),
            'floorsByHouse': layout.get('floorsByHouse', []),
            'floorNumbers': layout.get('floorNumbers', []),
            'bedrooms': layout.get('bedroomCount'),
            'rooms': layout.get('roomCount'),
            'roominess': layout.get('roominess')
        }
    except Exception as e:
        print(f"Ошибка получения деталей с {url_link}: {e}")
        return None

def get_korter_layout_string(rooms, bedrooms, roominess):
    if roominess == 'STUDIO':
        return "Студия"
    try:
        r = int(rooms) if rooms is not None else 0
        b = int(bedrooms) if bedrooms is not None else 0
        
        if r == 1 and b == 0:
            return "Студия"
        elif r > 0 and b > 0:
            living_rooms = r - b
            if living_rooms >= 0:
                return f"{b}+{living_rooms}"
            else:
                return f"{r}-комн."
        elif r > 0:
            return f"{r}-комн."

    except (ValueError, TypeError):
        pass
        
    return ""

def parse_iso_time(time_str):
    if not time_str: return None
    try:
        clean_str = time_str.split('+')[0].split('.')[0]
        dt = datetime.strptime(clean_str, '%Y-%m-%dT%H:%M:%S')
        return dt.replace(tzinfo=timezone.utc)
    except Exception as e:
        print(f"⚠️ Ошибка парсинга времени '{time_str}': {e}")
        return None

def main():
    count_429 = 0
    base_url_template = "https://korter.ge/ru/продажа-квартир-батуми?market_types=secondary&seller_type=owner&page={}"
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)
    print(f"Ищем квартиры на Korter, ОПУБЛИКОВАННЫЕ после: {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")

# --- ЭТАП 1: Собираем "сырые" данные со страниц листинга (без деталей) ---
    candidates = [] # сюда складываем item + url_link, без детальных запросов
    stop_pagination = False

    for page_num in range(1, 4):
        if stop_pagination: break
        url = base_url_template.format(page_num)
        print(f"Загрузка страницы поиска Korter {page_num}...")

        try:
            page = StealthyFetcher.fetch(url, headless=True, timeout=120000)
            match = re.search(r'window\.INITIAL_STATE\s*=\s*(\{.*?\});', page.html_content, re.DOTALL)
            if not match: break

            data = json.loads(match.group(1))
            apartments = data.get('apartmentListingStore', {}).get('apartments', [])
            if not apartments: break

            for item in apartments:
                link_part = item.get('link', '')
                if not link_part: continue
                url_link = f"https://korter.ge{urllib.parse.quote(link_part)}"
                candidates.append({'item': item, 'url_link': url_link})

        except Exception as e:
            print(f"Ошибка на странице поиска {page_num}: {e}")
            break
            
    # --- ЭТАП 2: Параллельный сбор деталей ---
    if not candidates:
        print("❌ Не удалось собрать кандидатов — сайт недоступен или изменилась структура страницы.")
        stats_line = f"Korter:0:0\n"
        with open("/tmp/scraper_stats.txt", "a") as f:
            f.write(stats_line)
        sys.exit(1) # выход с кодом ошибки — run_scrapers.sh поймёт что что-то пошло не так

    print(f"Найдено {len(candidates)} кандидатов. Запускаем параллельный сбор деталей...")
    results = []
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_candidate = {
            executor.submit(get_detail_data, c['url_link']): c
            for c in candidates
        }
        for future in as_completed(future_to_candidate):
            c = future_to_candidate[future]
            item = c['item']
            url_link = c['url_link']

            try:
                detail_data = future.result()
            except Exception as exc:
                print(f"Ошибка при получении деталей для {url_link}: {exc}")
                continue

            if not detail_data: continue

            # Фильтр по времени публикации
            publish_dt = parse_iso_time(detail_data.get('publishTime'))
            if not publish_dt or publish_dt <= cutoff_time:
                continue

            # --- Формируем caption (логика остаётся прежней) ---
            price = item.get('price', 0)
            currency = item.get('currency', 'USD')
            price_string = f"${price:,}" if currency == 'USD' else f"{price:,} {currency}"

            area_val = item.get('area')
            if isinstance(price, (int, float)) and price > 0 and isinstance(area_val, (int, float)) and area_val > 0:
                price_sqm = round(price / area_val)
                price_string += f" (${price_sqm:,}/м²)"

            area = area_val if area_val is not None else 'Не указана'
            floors = item.get('floorNumbers', [])
            floor = floors[0] if floors else '-'
            address_part = item.get('address', 'Адрес не указан')
            building_info = item.get('building', {})
            building_name = building_info.get('name', '')

            total_floors_list = detail_data.get('floorsByHouse', [])
            total_floors = total_floors_list[0].get('floorCount', '-') if isinstance(total_floors_list, list) and len(total_floors_list) > 0 else '-'

            full_address = f"{address_part} ({building_name})" if building_name else address_part

            layout_str = get_korter_layout_string(
                detail_data.get('rooms'),
                detail_data.get('bedrooms'),
                detail_data.get('roominess')
            )
            layout_caption = f"🛋️ <b>Планировка:</b> {layout_str}\n" if layout_str else ""

            caption = (
                f"📍 <b>Адрес:</b> Батуми, {full_address}\n"
                f"💰 <b>Цена:</b> {price_string}\n"
                f"{layout_caption}"
                f"📏 <b>Площадь:</b> {area} кв.м. | 🏢 <b>Этаж:</b> {floor}/{total_floors}\n"
                f"🔗 <a href='{url_link}'>Смотреть объявление на Korter</a>"
            )

            results.append({
                'caption': caption,
                'images': detail_data['images']
            })

    print(f"Сбор деталей занял: {time.time() - t0:.1f} сек.")

    total = len(results)
    print(f"Парсинг Korter завершен! Найдено {total} свежих квартир.")
    
    if total == 0:
        _, n429 = send_telegram_message("🔍 За последние 24 часа новых квартир на Korter.ge не найдено.")
        count_429 += n429
    else:
        _, n429 = send_telegram_message(f"🟠 <b>Новые квартиры с KORTER</b>\nЗа последние 24 часа найдено: <b>{total}</b>")
        count_429 += n429
        time.sleep(2)
        
        for i, item in enumerate(results):
            print(f"Отправка квартиры {i+1} из {total}...")
            time.sleep(4) # Пауза для предотвращения флуда Telegram API
            _, n429_media = send_telegram_media_group(item['caption'], item['images'])
            count_429 += n429_media

    print("✅ Все квартиры Korter успешно отправлены в канал.")

    stats_line = f"Korter:{total}:{count_429}\n"
    with open("/tmp/scraper_stats.txt", "a") as f:
        f.write(stats_line)

if __name__ == '__main__':
    main()