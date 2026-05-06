# Фактический список файлов старого проекта

Старый проект:

`/Users/uladkucapalau/.openclaw/workspace/projects/1. Парсер квартир`

Получено из одобренной команды инвентаризации.

## Файлы и папки

```text
file 13292 logs/analysis.md
file 91435 logs/app.log
file 7421 myhome_scraper.py
file 4755 run_scrapers.sh
file 4077 seen_myhome_ids.txt
file 5547 seen_ss_ids.txt
file 11507 ss_scraper.py
file 3510 telegram_utils.py
dir  logs
dir  Бля, точно работает
file 8452 Бля, точно работает/korter_scraper.py
file 7050 Бля, точно работает/myhome_scraper.py
file 11200 Бля, точно работает/ss_scraper.py
```

## Замечания

- В выводе команды начало первой строки пришло обрезанным: `craper.py`. Вероятно, это `korter_scraper.py`, но перед копированием лучше проверить точечно через `read` или повторный безопасный список при доступном exec.
- Кроме основных файлов есть папка `Бля, точно работает` — похоже на рабочую/резервную версию трёх парсеров. Её важно не потерять при legacy-копии.
- Есть `logs/analysis.md` — это может быть полезная аналитика, её стоит скопировать как документацию.
- `logs/app.log` большой и как рабочий лог в новый проект переносить не нужно, но можно скопировать в `legacy_snapshot/logs/` если хочешь сохранить полную историю.
