# Inventory — Scrapers 2.0

Актуальная инвентаризация после чистки рабочей папки.

Дата: 2026-05-05 14:13

## Рабочий корень

```text
Scrapers 2.0/
  .env                 # локальные секреты Telegram; не печатать и не коммитить
  .env.example         # шаблон переменных окружения
  .venv/               # локальное Python-окружение
  README.md            # рабочая инструкция проекта
  SETUP.md             # краткая настройка окружения
  INVENTORY.md         # этот файл
  requirements.txt     # зависимости Python
  run_scrapers_2.sh    # основной wrapper для ручного/планового запуска
  data/
  logs/
  runtime/
  src/
  _archive/
```

## Рабочие данные

```text
data/
  .gitkeep
  scrapers.db          # основная SQLite-база состояния
```

Бэкапы базы вынесены в архив:

```text
_archive/2026-05-05-migration/db_backups/
```

## Рабочий код

```text
src/apartment_scrapers/
  __init__.py
  config.py
  image_downloader.py
  logging_config.py
  main.py
  migrate_legacy_seen.py
  models.py
  orchestrator.py
  storage.py
  telegram_sender.py
  scrapers/
    __init__.py
    myhome.py
    ss.py
    korter.py
```

Backup-версии кода вынесены в архив:

```text
_archive/2026-05-05-migration/code_backups/
```

## Активные логи

В рабочей папке оставлены только актуальные/полезные логи:

```text
logs/
  .gitkeep
  app.log
  errors.log
  run_scrapers_2.log
  runs/
    20260505_124042.log   # общий all-source dry-run
    20260505_125825.log   # полный production run
```

Ранние тестовые run-логи вынесены в архив:

```text
_archive/2026-05-05-migration/logs/runs/
```

## Архив истории

Вся история миграции и исправлений собрана здесь:

```text
_archive/2026-05-05-migration/
  README_backups/          # старые README до финальной чистки
  code_backups/            # backup-копии изменённых файлов кода
  db_backups/              # SQLite-бэкапы перед тестами/боевыми отправками
  legacy_snapshot/         # снимок старого проекта
  logs/runs/               # ранние тестовые run-логи
  notes/                   # старая FILE_LIST.md и предыдущая INVENTORY.md
  requirements_backups/    # backup-версии requirements/SETUP
  scripts/                 # старый setup.sh
```

Количество файлов в архиве на момент проверки: `84`.

## Что было убрано из рабочего дерева

- Россыпь `*.bak*` из корня, `src/` и `data/`.
- Старый `legacy_snapshot/` из корня.
- Старые `FILE_LIST.md` и `INVENTORY.md`.
- Ранние тестовые run-логи.
- `.DS_Store` вне `.venv`.
- Проектные `__pycache__` вне `.venv`.

## Проверка после чистки

Выполнено успешно:

```bash
bash -n run_scrapers_2.sh
PYTHONPATH=src .venv/bin/python -m compileall -q src/apartment_scrapers
./run_scrapers_2.sh --help
```

`./run_scrapers_2.sh --help` показал ожидаемые режимы:

```text
--send
--dry-run
```

## Правила на будущее

- Рабочий корень держать коротким и понятным.
- Новые backup-файлы после исправлений переносить в `_archive/YYYY-MM-DD-.../` или отдельный осмысленный архив.
- Не удалять `data/scrapers.db` без отдельного явного решения.
- Не удалять `_archive/2026-05-05-migration/`: это история восстановления и отката.
- Перед реальными Telegram-отправками по-прежнему нужно явное подтверждение.
