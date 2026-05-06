import sys
import json
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from scrapling.fetchers import StealthyFetcher
from bs4 import BeautifulSoup
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from telegram_utils import send_telegram_message, send_telegram_media_group

SEEN_IDS_FILE = "seen_ss_ids.txt"

def get_images_from_apartment(url):
    try:
        page = StealthyFetcher.fetch(url, headless=True, timeout=60000)
        images = []
        match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', page.html_content, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                app_data = data.get('props', {}).get('pageProps', {}).get('applicationData', {})
                app_images = app_data.get('appImages', [])
                if app_images:
                    for img in app_images:
                        img_url = img.get('fileName')
                        if img_url and 'http' in img_url:
                            images.append(img_url)
            except Exception as e:
                print(f"DEBUG: Ошибка парсинга JSON для {url}: {e}")
        
        if not images:
            soup = BeautifulSoup(page.html_content, 'html.parser')
            meta_images = soup.find_all('meta', property='og:image')
            for meta in meta_images:
                content = meta.get('content')
                if content and 'http' in content:
                    images.append(content)
                    
        return list(dict.fromkeys(images))[:10]
    except Exception as e:
        print(f"CRITICAL: КРИТИЧЕСКАЯ ОШИБКА в get_images_from_apartment для {url}: {e}")
        return []

def load_seen_ids():
    if not os.path.exists(SEEN_IDS_FILE):
        return set()
    with open(SEEN_IDS_FILE, 'r') as f:
        return set(line.strip() for line in f)

def save_new_ids(new_ids):
    with open(SEEN_IDS_FILE, 'a') as f:
        for item_id in new_ids:
            f.write(str(item_id) + '\n')

def get_layout_string(rooms, bedrooms):
    if rooms is None: return None
    try:
        rooms = int(rooms)
        bedrooms = int(bedrooms) if bedrooms is not None else 0
    except (ValueError, TypeError): return None

    if rooms == 1 and bedrooms == 0: return "Студия"
    if rooms == 1 and bedrooms == 1: return "Студия"

    if rooms > 1 and bedrooms > 0 and rooms > bedrooms:
        return f"{bedrooms}+{rooms - bedrooms}"

    if rooms > 1 and rooms == bedrooms:
        return f"{bedrooms} спальни"

    if rooms > 0:
        return f"{rooms}-комн."
        
    return None

def main():
    count_429 = 0
    base_url_template = "https://home.ss.ge/ru/недвижимость/l/Квартира/Продается?cityIdList=96&subdistrictIds=57%2C58%2C59%2C63%2C64%2C65%2C66%2C91&currencyId=2&advancedSearch=%7B%22individualEntityOnly%22%3Atrue%7D&order=1&page={}"
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)
    print(f"Ищем свежие квартиры ss.ge, опубликованные после: {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    historic_seen_ids = load_seen_ids()
    print(f"Загружено {len(historic_seen_ids)} ранее виденных ID из файла.")

    session_seen_ids, results, newly_sent_ids = set(), [], []
    stop_pagination = False
    
    for page_num in range(1, 10):
        if stop_pagination: break
        url = base_url_template.format(page_num)
        print(f"Загрузка страницы поиска {page_num}...")
        
        try:
            page = StealthyFetcher.fetch(url, headless=True, timeout=120000)
            match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', page.html_content, re.DOTALL)
            if not match: break
                
            data = json.loads(match.group(1))
            items = data['props']['pageProps']['applicationList'].get('realStateItemModel', [])
            if not items: break
                
            for item in items:
                item_id = str(item.get('applicationId', ''))
                if not item_id or item_id in session_seen_ids or item_id in historic_seen_ids: continue
                session_seen_ids.add(item_id)
                
                vip_status = item.get('vipStatus', 0)
                is_vip = vip_status > 0
                
                date_str_raw = item.get('orderDate')
                if not date_str_raw: continue
                date_str = date_str_raw.split('+')[0].split('.')[0].replace('T', ' ')
                try: item_date = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                except ValueError: continue
                    
                if item_date <= cutoff_time:
                    if is_vip: continue
                    else: stop_pagination = True; break
                
                detail_url_part = item.get('detailUrl', '')
                if not detail_url_part: continue
                url_link = f"https://home.ss.ge/ru/недвижимость/{urllib.parse.quote(detail_url_part)}"
                
                price_info = item.get('price', {})
                usd_price = price_info.get('priceUsd', 'Нет цены')
                sq_price = price_info.get('unitPriceUsd', '')
                if isinstance(usd_price, (int, float)): usd_price = f'${int(usd_price):,}'
                if isinstance(sq_price, (int, float)): sq_price = f'${int(sq_price):,}'
                price_string = f"{usd_price}"
                if sq_price: price_string += f" ({sq_price}/м²)"

                # --- НАЧАЛО: Усиленная логика получения комнат и спален ---
                rooms = item.get('rooms')
                if rooms is None:
                    title = item.get('title', '')
                    rooms_match = re.search(r'(\d+)-комнатная', title)
                    if rooms_match:
                        try:
                            rooms = int(rooms_match.group(1))
                        except (ValueError, TypeError):
                            rooms = None
                
                bedrooms = item.get('bedrooms')
                if bedrooms is None:
                    bedrooms = item.get('numberOfBedrooms')
                # --- КОНЕЦ: Усиленная логика ---

                layout_str = get_layout_string(rooms, bedrooms)
                layout_line = f"🛏️ <b>Планировка:</b> {layout_str}\n" if layout_str else ""
                
                area = item.get('totalArea', 'Н/у')
                floor, total_floors = item.get('floorNumber', '-'), item.get('totalAmountOfFloor', '-')
                if floor is None: floor = '-'
                if total_floors is None: total_floors = '-'
                
                address_info = item.get('address', {})
                district = address_info.get('subdistrictTitle', '')
                street = address_info.get('streetTitle', '')
                street_num = address_info.get('streetNumber', '')
                full_street = f"{street} {street_num}" if street and street_num else street or (f"дом {street_num}" if street_num else "")
                full_address = ', '.join(filter(None, [district, full_street])) or "Адрес не указан"
                
                caption = (
                    f"📍 <b>Адрес:</b> Батуми, {full_address}\n"
                    f"💰 <b>Цена:</b> {price_string}\n{layout_line}"
                    f"📏 <b>Площадь:</b> {area} кв.м. | 🏢 <b>Этаж:</b> {floor}/{total_floors}\n"
                    f"🔗 <a href=\"{url_link}\">Смотреть объявление на SS</a>"
                )
                
                results.append({
                    'id': item_id,
                    'caption': caption,
                    'url': url_link
                })
                
        except Exception as e:
            print(f"Ошибка на странице поиска {page_num}: {e}")
            break

    total = len(results)
    print(f"Парсинг списка завершен! Найдено {total} свежих квартир. Начинаем сбор фото и отправку...")
    
    if total == 0:
        _, n429 = send_telegram_message("🔍 За последние 24 часа новых квартир на SS.ge не найдено.")
        count_429 += n429
    else:
        _, n429 = send_telegram_message(f"🩷 <b>Новые квартиры с SS.GE</b>\nЗа последние 24 часа найдено: <b>{total}</b>")
        count_429 += n429
        time.sleep(2)
        
        # === ЭТАП 1: Параллельный сбор фото ===
        # Все 5 потоков скачивают фото одновременно — это быстро и не трогает Telegram API
        print(f"Начинаем параллельный сбор фото для {total} квартир...")
        collected = [] # Сюда складываем пары (item, image_urls) — без отправки

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_item = {
                executor.submit(get_images_from_apartment, item['url']): item
                for item in results
            }
            for future in as_completed(future_to_item):
                item = future_to_item[future]
                try:
                    image_urls = future.result()
                except Exception as exc:
                    print(f"CRITICAL: Ошибка при получении изображений для ID {item['id']}: {exc}")
                    image_urls = []
                collected.append((item, image_urls))

        # === ЭТАП 2: Строго последовательная отправка ===
        # Только один запрос к Telegram за раз, с паузой 4 секунды между ними
        print(f"Сбор фото завершён. Начинаем последовательную отправку {len(collected)} квартир...")
        for i, (item, image_urls) in enumerate(collected):
            print(f"({i+1}/{total}) Собрано {len(image_urls)} фото для ID {item['id']}. Отправка...")
            time.sleep(4) # Проактивная пауза — даём Telegram API отдышаться
            success, n429 = send_telegram_media_group(item['caption'], image_urls)
            count_429 += n429
            if success:
                newly_sent_ids.append(item['id'])
            else:
                print(f"CRITICAL: Не удалось отправить квартиру ID {item['id']} даже как текст.")
            
    if newly_sent_ids:
        print(f"Сохраняем {len(newly_sent_ids)} новых ID в файл...")
        save_new_ids(newly_sent_ids)
        print("✅ Новые ID успешно сохранены.")

    print("✅ Все квартиры ss.ge успешно обработаны и отправлены в канал.")

    stats_line = f"SS:{total}:{count_429}\n"
    with open("/tmp/scraper_stats.txt", "a") as f:
        f.write(stats_line)

if __name__ == '__main__':
    main()
