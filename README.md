# RE-scrprs-Geo

Apartment scrapers for Batumi. 
Repository:
```text
https://github.com/Kretkas/RE-scrprs-Geo
```

## Installation and Usage

Recommended Python: **3.13** (a local virtual environment is used, no need to change the global pyenv).

Clone the repository and enter the directory:

```bash
git clone https://github.com/Kretkas/RE-scrprs-Geo.git
cd RE-scrprs-Geo
```

### One-command Installation

```bash
bash scripts/setup.sh
```
The script will create a `.venv`, install `requirements.txt`, download Chromium browsers (Playwright/Patchright), and run a verification dry-run.

### Manual Installation

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m patchright install chromium
```

### Running the Scrapers

Safe verification without Telegram or writing new listings to SQLite:

```bash
./run_scrapers_2.sh --dry-run
```

Production run across all sources:

```bash
./run_scrapers_2.sh --send
```

Without arguments, `run_scrapers_2.sh` defaults to `--send`.

### Dependencies (Dependency intent)

- `scrapling` — browser-like scraping / anti-bot resilience (currently used for SS.ge and Korter; MyHome uses direct JSON API).
- `curl_cffi`, `playwright`, `patchright`, `msgspec`, `browserforge` — explicit Scrapling browser/stealth stack dependencies.
- `beautifulsoup4`, `lxml` — HTML parsing.
- `requests` — compatibility with legacy Telegram code.
- `httpx` — future cleaner Telegram/client implementation.
- `tenacity` — robust retries/backoff.
- `python-dotenv`, `pydantic`, `pydantic-settings` — structured config.
- `typer`, `rich` — future CLI and readable console output.
- `loguru` — optional richer logging if standard logging becomes too clunky.

## Direct CLI Usage

General execution via Python:

```bash
PYTHONPATH=src .venv/bin/python -m apartment_scrapers.main
```

Examples:

```bash
# All sources, dry-run
PYTHONPATH=src .venv/bin/python -m apartment_scrapers.main --source myhome --source ss --source korter --dry-run

# Single source, dry-run
PYTHONPATH=src .venv/bin/python -m apartment_scrapers.main --source ss --dry-run

# Single source, max 3 listings, actual dispatch
PYTHONPATH=src .venv/bin/python -m apartment_scrapers.main --source myhome --limit 3 --send

# Diagnostics including already seen listings
PYTHONPATH=src .venv/bin/python -m apartment_scrapers.main --source korter --limit 3 --include-seen --dry-run
```

Flags:

- `--dry-run` — prevents Telegram dispatch and avoids writing new listings to SQLite.
- `--send` — allows actual dispatch to Telegram.
- `--source myhome|ss|korter` — selects a source; can be specified multiple times.
- `--limit N` — caps the number of listings processed.
- `--include-seen` — includes listings already present in SQLite; for diagnostics only.

## How the System Works

For each source, the workflow is:

1. Fetch fresh listings from the past 24 hours.
2. Filter out already seen listings via SQLite.
3. Send the source header message.
4. Send the listings along with their photos.
5. Record the outcome in SQLite.
6. Proceed to the next source.

Sources are processed sequentially:

```text
MyHome → SS.ge → Korter
```

## Telegram Messages

If listings are found, a header is sent:

```text
🟢 New apartments from MYHOME
Found in the last 24 hours: N

🩷 New apartments from SS.GE
Found in the last 24 hours: N

🟣 New apartments from KORTER
Found in the last 24 hours: N
```

If no listings are found for a source, a message like this is sent:

```text
🔍 No new apartments found on MYHOME in the last 24 hours.
🔍 No new apartments found on SS.GE in the last 24 hours.
🔍 No new apartments found on KORTER in the last 24 hours.
```

In `--dry-run` mode, these messages are only logged.

## Photos and Telegram Fallback

- Photos are first downloaded locally to `runtime/images/<run_id>/`.
- A media group is sent to Telegram.
- If there are more than 10 photos, only the first 10 are used.
- Temporary folders are cleaned up after dispatch.
- Upon encountering `429 Too Many Requests`, the code waits for the `retry_after` duration and retries.

Special case: `PHOTO_INVALID_DIMENSIONS`.

If Telegram rejects the album because a specific photo has invalid dimensions:

1. the code identifies the problematic photo via `message #N`;
2. removes it from the set;
3. retries sending the remainder as an album;
4. if a valid album cannot be formed, it sends a text message with the link instead;
5. photos are no longer fragmented into separate individual messages.

## Data and State

SQLite database:

```text
data/scrapers.db
```

Primary tables:

- `listings` — listing records, seen/sent statuses, and photo counts.
- `send_attempts` — dispatch attempts, Telegram message IDs, and retry counts.
- `runs` — execution history table; largely unused at the moment.

Legacy seen files have been imported:

- MyHome: 416 unique IDs.
- SS.ge: 444 unique IDs.

The old `seen_*.txt` files are no longer utilized by the new logic.

## Logs

Primary logs:

```text
logs/app.log
logs/errors.log
logs/runs/<run_id>.log
logs/run_scrapers_2.log
```

A `run_id` is generated for each execution, making it easy to find specific logs under `logs/runs/`.

`run_scrapers_2.sh` also writes a wrapper log to:

```text
logs/run_scrapers_2.log
```

The wrapper log is rotated when it exceeds 10 MB; old wrapper logs older than 30 days are pruned.

## Verified Runs

### Individual Tests

- SS.ge dry-run completed successfully.
- SS.ge real send completed successfully.
- MyHome dry-run completed successfully.
- MyHome real send completed successfully.
- Korter dry-run completed successfully.
- Korter real send completed successfully.

### Global Dry-run

Command:

```bash
PYTHONPATH=src .venv/bin/python -m compileall -q src/apartment_scrapers && PYTHONPATH=src .venv/bin/python -m apartment_scrapers.main --source myhome --source ss --source korter --dry-run
```

Outcome:

- `run_id=20260505_124042`
- 17 fresh unseen listings found in total;
- MyHome: 9;
- SS.ge: 5;
- Korter: 3;
- Telegram was untouched;
- SQLite didn't persist new listings;
- temporary photos were cleaned up.

### Final Manual Production Run

Command:

```bash
./run_scrapers_2.sh --send
```

Outcome:

- `run_id=20260505_125825`
- exit status `0`
- 20 fresh unseen listings sent in total;
- MyHome: 10;
- SS.ge: 8;
- Korter: 2;
- SQLite acknowledged all dispatches;
- temporary photos were cleaned up.

SQLite state after the run:

```text
korter sent=5
myhome legacy_seen=416
myhome sent=13
ss legacy_seen=441
ss sent=13
```

## Project Structure

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

## Security and Operating Guidelines

- Do not edit the legacy project.
- Back up existing files in `Scrapers 2.0` before modifying them.
- Never print or hardcode secrets.
- Any real Telegram dispatch must only occur after explicit confirmation.
- Any scheduling/cron/launchd setup should only proceed following a separate deliberate decision.

## Next Steps

If execution automation is desired, set up scheduling from scratch.

Recommended configuration:

```text
daily at 10:00 → /Users/uladkucapalau/.openclaw/workspace/projects/RE-scrprs-Geo/run_scrapers_2.sh --send
```

Before setting up the schedule, you might want to perform another manual verification:

```bash
./run_scrapers_2.sh --dry-run
```
