# RE-scrprs-Geo

Парсеры квартир для Batumi. Проект мигрировал на GitHub.

Репозиторий:
```text
https://github.com/Kretkas/RE-scrprs-Geo
```

## Статус

**Актуально на 2026-05-05 13:46.**

Scrapers 2.0 функционально готов:

- SS.ge перенесён и проверен.
- MyHome перенесён и проверен.
- Korter перенесён и проверен.
- Общий dry-run всех источников прошёл успешно.
- Полный ручной боевой запуск прошёл успешно.
- SQLite фиксирует отправленные объявления и попытки отправки.
- Telegram retry на `429` работает.
- Фото отправляются через локальную загрузку и Telegram media group.
- Лимит Telegram-альбома соблюдается: максимум 10 фото на объявление.

Осталось по желанию:

- создать расписание с нуля, например ежедневный запуск в 10:00;
- при следующем реальном запуске проследить новый fallback для `PHOTO_INVALID_DIMENSIONS`.

## Установка и запуск

Рекомендуемый Python: **3.13** (используется локальное виртуальное окружение, глобальный pyenv менять не нужно).

Склонировать репозиторий и перейти в папку:

```bash
git clone https://github.com/Kretkas/RE-scrprs-Geo.git
cd RE-scrprs-Geo
```

### Установка в одну команду

```bash
bash scripts/setup.sh
```
Скрипт создаст `.venv`, установит `requirements.txt`, скачает браузеры Chromium (Playwright/Patchright) и запустит проверочный dry-run.

### Ручная установка

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m patchright install chromium
```

### Запуск парсеров

Безопасная проверка без Telegram и без записи новых объявлений в SQLite:

```bash
./run_scrapers_2.sh --dry-run
```

Боевой запуск всех источников:

```bash
./run_scrapers_2.sh --send
```

Без аргументов `run_scrapers_2.sh` работает как `--send`.

### Зависимости (Dependency intent)

- `scrapling` — browser-like scraping / anti-bot resilience.
- `curl_cffi`, `playwright`, `patchright`, `msgspec`, `browserforge` — explicit Scrapling browser/stealth stack dependencies.
- `beautifulsoup4`, `lxml` — HTML parsing.
- `requests` — compatibility with legacy Telegram code.
- `httpx` — future cleaner Telegram/client implementation.
- `tenacity` — robust retries/backoff.
- `python-dotenv`, `pydantic`, `pydantic-settings` — structured config.
- `typer`, `rich` — future CLI and readable console output.
- `loguru` — optional richer logging if standard logging becomes too clunky.

## CLI напрямую

Общий запуск через Python:

```bash
PYTHONPATH=src .venv/bin/python -m apartment_scrapers.main
```

Примеры:

```bash
# Все источники, dry-run
PYTHONPATH=src .venv/bin/python -m apartment_scrapers.main --source myhome --source ss --source korter --dry-run

# Один источник, dry-run
PYTHONPATH=src .venv/bin/python -m apartment_scrapers.main --source ss --dry-run

# Один источник, максимум 3 объявления, реальная отправка
PYTHONPATH=src .venv/bin/python -m apartment_scrapers.main --source myhome --limit 3 --send

# Диагностика с уже виденными объявлениями
PYTHONPATH=src .venv/bin/python -m apartment_scrapers.main --source korter --limit 3 --include-seen --dry-run
```

Флаги:

- `--dry-run` — не отправляет Telegram и не записывает новые объявления в SQLite.
- `--send` — разрешает реальную отправку в Telegram.
- `--source myhome|ss|korter` — выбрать источник; можно указать несколько раз.
- `--limit N` — ограничить количество объявлений.
- `--include-seen` — включить объявления, уже присутствующие в SQLite; только для диагностики.

## Что делает система

Для каждого источника порядок такой:

1. Собрать свежие объявления за последние 24 часа.
2. Отфильтровать уже виденные через SQLite.
3. Отправить шапку источника.
4. Отправить объявления с фото.
5. Записать результат в SQLite.
6. Перейти к следующему источнику.

Источники обрабатываются последовательно:

```text
MyHome → SS.ge → Korter
```

## Сообщения в Telegram

Если объявления найдены, отправляется шапка:

```text
🟢 Новые квартиры с MYHOME
За последние 24 часа найдено: N

🩷 Новые квартиры с SS.GE
За последние 24 часа найдено: N

🟣 Новые квартиры с KORTER
За последние 24 часа найдено: N
```

Если объявлений нет, для любого источника отправляется сообщение вида:

```text
🔍 За последние 24 часа новых квартир на MYHOME не найдено.
🔍 За последние 24 часа новых квартир на SS.GE не найдено.
🔍 За последние 24 часа новых квартир на KORTER не найдено.
```

В `--dry-run` эти сообщения только логируются.

## Фото и Telegram fallback

- Фото сначала скачиваются локально в `runtime/images/<run_id>/`.
- В Telegram отправляется media group.
- Если фото больше 10, берутся первые 10.
- После отправки временные папки очищаются.
- При `429 Too Many Requests` код ждёт `retry_after` и повторяет запрос.

Особый случай: `PHOTO_INVALID_DIMENSIONS`.

Если Telegram отклоняет альбом и сообщает, что конкретное фото имеет неверные размеры:

1. код определяет проблемное фото по `message #N`;
2. удаляет его из набора;
3. повторяет отправку именно альбомом;
4. если валидный альбом собрать невозможно — отправляет текст со ссылкой;
5. фото больше не рассыпаются отдельными сообщениями.

## Данные и состояние

SQLite-база:

```text
data/scrapers.db
```

Основные таблицы:

- `listings` — объявления, seen/sent статус, количество фото.
- `send_attempts` — попытки отправки, Telegram message IDs, retry count.
- `runs` — таблица под историю запусков; пока почти не используется.

Legacy seen-файлы импортированы:

- MyHome: 416 уникальных ID.
- SS.ge: 444 уникальных ID.

Старые `seen_*.txt` больше не используются в новой логике.

## Логи

Основные логи:

```text
logs/app.log
logs/errors.log
logs/runs/<run_id>.log
logs/run_scrapers_2.log
```

`run_id` создаётся на каждый запуск и позволяет найти отдельный лог в `logs/runs/`.

`run_scrapers_2.sh` дополнительно пишет wrapper-лог в:

```text
logs/run_scrapers_2.log
```

Wrapper-лог ротируется при размере больше 10 MB; старые wrapper-логи старше 30 дней удаляются.

## Проверенные запуски

### Индивидуальные проверки

- SS.ge dry-run прошёл успешно.
- SS.ge real send прошёл успешно.
- MyHome dry-run прошёл успешно.
- MyHome real send прошёл успешно.
- Korter dry-run прошёл успешно.
- Korter real send прошёл успешно.

### Общий dry-run

Команда:

```bash
PYTHONPATH=src .venv/bin/python -m compileall -q src/apartment_scrapers && PYTHONPATH=src .venv/bin/python -m apartment_scrapers.main --source myhome --source ss --source korter --dry-run
```

Результат:

- `run_id=20260505_124042`
- всего найдено 17 fresh unseen объявлений;
- MyHome: 9;
- SS.ge: 5;
- Korter: 3;
- Telegram не трогался;
- SQLite не писал новые объявления;
- временные фото очищены.

### Финальный ручной боевой запуск

Команда:

```bash
./run_scrapers_2.sh --send
```

Результат:

- `run_id=20260505_125825`
- exit status `0`
- всего отправлено 20 fresh unseen объявлений;
- MyHome: 10;
- SS.ge: 8;
- Korter: 2;
- SQLite подтвердил все отправки;
- временные фото очищены.

Состояние SQLite после запуска:

```text
korter sent=5
myhome legacy_seen=416
myhome sent=13
ss legacy_seen=441
ss sent=13
```

## Структура проекта

```text
RE-scrprs-Geo/
  .env
  .env.example
  .venv/
  README.md
  requirements.txt
  run_scrapers_2.sh
  scripts/
    setup.sh
  data/
    scrapers.db
  logs/
    app.log
    errors.log
    run_scrapers_2.log
    runs/
  runtime/
  src/
    apartment_scrapers/
      main.py
      config.py
      logging_config.py
      models.py
      storage.py
      telegram_sender.py
      image_downloader.py
      orchestrator.py
      migrate_legacy_seen.py
      scrapers/
        myhome.py
        ss.py
        korter.py
```

## Безопасность и правила работы

- Старый проект не редактировать.
- Перед изменением существующих файлов в `Scrapers 2.0` делать бэкап.
- Секреты не печатать и не хардкодить.
- Любая реальная Telegram-отправка — только после явного подтверждения.
- Любое расписание/cron/launchd — только после отдельного явного решения.

## Следующий шаг

Если нужно автоматизировать запуск, создать расписание с нуля.

Рекомендуемый вариант:

```text
каждый день в 10:00 → /Users/uladkucapalau/.openclaw/workspace/projects/RE-scrprs-Geo/run_scrapers_2.sh --send
```

Перед созданием расписания можно ещё раз вручную выполнить:

```bash
./run_scrapers_2.sh --dry-run
```
