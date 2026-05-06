# Setup

## Python

Scrapers 2.0 uses a project-local virtual environment.

Recommended Python: **3.13**.

Do not change global `pyenv` just for this project. The old parser currently runs on Python 3.9.7 and should stay untouched until migration is complete.

## One-command setup

```bash
bash scripts/setup.sh
```

The script will:

- create `.venv` with Python 3.13 if needed;
- install `requirements.txt`;
- install Playwright Chromium;
- install Patchright Chromium;
- run a dry-run smoke-test.

## Manual setup

```bash
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m patchright install chromium
```

## Run smoke-test

```bash
PYTHONPATH=src .venv/bin/python -m apartment_scrapers.main --dry-run
```

## Dependency intent

- `scrapling` — browser-like scraping / anti-bot resilience.
- `curl_cffi`, `playwright`, `patchright`, `msgspec`, `browserforge` — explicit Scrapling browser/stealth stack dependencies.
- `beautifulsoup4`, `lxml` — HTML parsing.
- `requests` — compatibility with legacy Telegram code.
- `httpx` — future cleaner Telegram/client implementation.
- `tenacity` — robust retries/backoff.
- `python-dotenv`, `pydantic`, `pydantic-settings` — structured config.
- `typer`, `rich` — future CLI and readable console output.
- `loguru` — optional richer logging if standard logging becomes too clunky.
