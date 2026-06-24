"""
One-shot daily scan runner — used by GitHub Actions cron.
Loads .env, calls run_get_stocks_job() once, exits.
"""
import logging
import sys
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("get_stocks_once")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(_ROOT, ".env"))
except ImportError:
    pass

# Add core/ to sys.path so we can import server
sys.path.append(os.path.join(_ROOT, "core"))

from server import run_get_stocks_job

def main() -> int:
    try:
        logger.info("Starting daily scan (get_stocks)...")
        outcome = run_get_stocks_job()
        logger.info("Daily scan completed: %s", outcome)
        return 0
    except Exception as exc:
        logger.exception("Daily scan failed: %s", exc)
        return 1

if __name__ == "__main__":
    sys.exit(main())
