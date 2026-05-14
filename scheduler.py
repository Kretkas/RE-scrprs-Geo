import schedule
import time
import logging
import subprocess
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("Scheduler")

def run_scrapers():
    logger.info("Starting scrapers run...")
    try:
        # We invoke the main orchestrator script directly
        env = os.environ.copy()
        result = subprocess.run(
            ["python", "-m", "src.apartment_scrapers.main", "--send"],
            env=env,
            check=True,
            capture_output=False
        )
        logger.info("Scrapers run finished with code %s.", result.returncode)
    except subprocess.CalledProcessError as e:
        logger.error("Scrapers run failed with code %s.", e.returncode)
    except Exception as e:
        logger.exception("Unexpected error running scrapers: %s", e)

def main():
    logger.info("Starting Docker internal scheduler.")
    logger.info("Configured times (UTC): 04:00 (08:00 TBS) and 22:35 (02:35 TBS).")

    # Run times in UTC (Server default)
    # Tbilisi is UTC+4.
    # 08:00 TBS -> 04:00 UTC
    schedule.every().day.at("04:00").do(run_scrapers)
    
    # 02:35 TBS -> 22:35 UTC (previous day relative to TBS morning)
    schedule.every().day.at("22:35").do(run_scrapers)

    # Note: If you want to run immediately on container start, uncomment the next line:
    # run_scrapers()

    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()
