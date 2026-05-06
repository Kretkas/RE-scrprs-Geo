import time
import requests

BOT_TOKEN = "8790493486:AAGXyObOuLQm_MYIwNL0yPZc9UNy1_2kEEY"
CHAT_ID = "-1003877222247"


def send_telegram_message(text, retries=5):
    retry_429 = 0
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHAT_ID,
        'text': text,
        'disable_web_page_preview': True,
        'parse_mode': 'HTML',
        'disable_notification': True
    }

    for i in range(retries):
        try:
            response = requests.post(url, data=payload, timeout=30)
            if response.status_code == 200:
                return True, retry_429
            elif response.status_code == 429:
                retry_429 += 1
                retry_after = int(response.headers.get('Retry-After', 30))
                print(f"⚠️ Получен статус 429. Жду {retry_after} сек. перед повторной отправкой текста...")
                time.sleep(retry_after + 1)
            else:
                print(f"❌ Непредвиденная ошибка отправки текста: {response.status_code} - {response.text}")
                time.sleep(5 * (i + 1))
        except requests.exceptions.RequestException as e:
            print(f"❌ Исключение при отправке текста: {e}")
            time.sleep(5 * (i + 1))

    print(f"CRITICAL: Не удалось отправить текст после {retries} попыток.")
    return False, retry_429


def send_telegram_media_group(caption, image_urls, retries=5):
    retry_429 = 0
    if not image_urls:
        success, n429 = send_telegram_message(caption)
        return success, retry_429 + n429

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMediaGroup"
    media = []
    unique_urls = list(dict.fromkeys(image_urls))[:10]

    for i, img_url in enumerate(unique_urls):
        item = {"type": "photo", "media": img_url}
        if i == 0:
            item["caption"] = caption
            item["parse_mode"] = "HTML"
        media.append(item)

    payload = {'chat_id': CHAT_ID, 'media': media, 'disable_notification': True}

    for i in range(retries):
        try:
            response = requests.post(url, json=payload, timeout=60)
            if response.status_code == 200:
                return True, retry_429
            elif response.status_code == 429:
                retry_429 += 1
                retry_after = int(response.json().get('parameters', {}).get('retry_after', 30))
                print(f"⚠️ Получен статус 429. Жду {retry_after} сек. перед повторной отправкой медиа...")
                time.sleep(retry_after + 1)
            else:
                print(f"❌ Непредвиденная ошибка отправки медиа: {response.status_code} - {response.text}")
                print("Пробуем отправить просто текстом...")
                success, n429 = send_telegram_message(caption)
                return success, retry_429 + n429
        except requests.exceptions.RequestException as e:
            print(f"❌ Исключение при отправке медиа: {e}")
            time.sleep(5 * (i + 1))

    print(f"CRITICAL: Не удалось отправить медиа-группу после {retries} попыток. Отправляю как текст.")
    success, n429 = send_telegram_message(caption)
    return success, retry_429 + n429
