import sys
import json
import re
import time
from datetime import datetime, timedelta
from scrapling.fetchers import StealthyFetcher
import os
from telegram_utils import send_telegram_message, send_telegram_media_group

SEEN_IDS_FILE = "realtor_scrapers/seen_myhome_ids.txt"

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
    if rooms > 1 and bedrooms > 0 and rooms > bedrooms: return f"{bedrooms}+{rooms - bedrooms}"
    if rooms > 1 and rooms == bedrooms: return f"{bedrooms} спальни"
    if rooms > 0: return f"{rooms}-комн."
    return None

def main():
    base_url_template = "https://www.myhome.ge/ru/nedvizhimost/prodazha/staroe-zdanie/kvartira/batumi/aeroportis-ubani/?deal_types=1&real_estate_types=1&currency_id=1&CardView=3&statuses=1%2C2%2C3&conditions=1%2C2%2C3%2C4%2C6%2C7%2C8%2C5&cities=15&urbans=77%2C73%2C72%2C74%2C75%2C76%2C71&districts=15%2C9%2C8%2C10%2C11%2C13%2C7&page={}&owner_type=physical"
    cutoff_time = datetime.now() - timedelta(hours=24)
    print(f"Ищем свежие квартиры, опубликованные после: {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    historic_seen_ids = load_seen_ids()
    print(f"Загружено {len(historic_seen_ids)} ранее виденных ID из файла.")

    session_seen_ids, results, newly_sent_ids = set(), [], []
    stop_pagination = False
    
    for page_num in range(1, 10):
        if stop_pagination: break
        url = base_url_template.format(page_num)
        print(f"Загрузка страницы {page_num}...")
        
        try:
            page = StealthyFetcher.fetch(url, headless=True, timeout=240000)
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page.html_content)
            if not match: break
                
            data = json.loads(match.group(1))
            queries = data['props']['pageProps']['dehydratedState']['queries']
            
            listings = []
            for q in queries:
                if 'data' in q['state'] and isinstance(q['state']['data'], dict) and 'data' in q['state']['data']:
                    possible_listings = q['state']['data']['data']['data']
                    if possible_listings and isinstance(possible_listings, list) and 'price' in possible_listings[0]:
                        listings = possible_listings
                        break
            if not listings: break
                
            for item in listings:
                item_id = str(item.get('id', ''))
                if not item_id or item_id in session_seen_ids: continue
                session_seen_ids.add(item_id)

                is_strictly_vip = bool(item.get('is_vip') or item.get('is_vip_plus') or item.get('is_super_vip'))
                if is_strictly_vip or item_id in historic_seen_ids: continue

                date_str = item.get('last_updated')
                if not date_str: continue
                try:
                    item_date = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                except ValueError: continue
                
                if item_date <= cutoff_time:
                    if item.get('is_promoted', False): continue
                    else: stop_pagination = True; break
                
                url_link = f'https://www.myhome.ge/ru/pr/{item_id}/'
                price_info = item.get('price', {}).get('2', {})
                usd_price = price_info.get('price_total', 'Нет цены')
                sq_price = price_info.get('price_square', '')
                price_string = f'${usd_price:,}' if isinstance(usd_price, int) else str(usd_price)
                if isinstance(sq_price, int): price_string += f" (${sq_price:,}/м²)"
                
                # ИСПРАВЛЕННАЯ ЛОГИКА
                rooms = item.get('room') 
                bedrooms = item.get('bedroom')
                layout_str = get_layout_string(rooms, bedrooms)
                layout_line = f"🛏️ <b>Планировка:</b> {layout_str}\n" if layout_str else ""
                
                area = item.get('area', 'Н/у')
                floor, total_floors = item.get('floor', '-'), item.get('total_floors', '-')
                full_address = ', '.join(filter(None, [item.get('city_name'), item.get('district_name'), item.get('address')]))
                
                images_data = item.get('images', [])
                image_urls = [img['large'] for img in images_data if 'large' in img] if images_data else []
                
                caption = (
                    f"📍 <b>Адрес:</b> {full_address}\n"
                    f"💰 <b>Цена:</b> {price_string}\n{layout_line}"
                    f"📏 <b>Площадь:</b> {area} кв.м. | 🏢 <b>Этаж:</b> {floor}/{total_floors}\n"
                    f"🔗 <a href=\"{url_link}\">Смотреть объявление на MyHome</a>"
                )
                
                results.append({'id': item_id, 'caption': caption, 'images': image_urls})
                
        except Exception as e:
            print(f"Ошибка на странице {page_num}: {e}")
            break

    total = len(results)
    print(f"Парсинг завершен! Найдено {total} свежих и уникальных квартир.")
    
    if total == 0:
        send_telegram_message("🔍 За последние 24 часа новых квартир по вашему фильтру на MyHome не найдено.")
    else:
        send_telegram_message(f"🟢 <b>Новые квартиры с MYHOME</b>\nЗа последние 24 часа найдено: <b>{total}</b>")
        time.sleep(2)
        
        for i, item in enumerate(results):
            print(f"Отправка квартиры {i+1} из {total} (ID: {item['id']})...")
            time.sleep(4) # Пауза перед отправкой
            if send_telegram_media_group(item['caption'], item['images']):
                newly_sent_ids.append(item['id']) 
            
    if newly_sent_ids:
        print(f"Сохраняем {len(newly_sent_ids)} новых ID в файл...")
        save_new_ids(newly_sent_ids)
        print("✅ Новые ID успешно сохранены.")

    print("✅ Все квартиры успешно отправлены в канал.")

if __name__ == '__main__':
    main()
