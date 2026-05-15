"""
One-shot position poller — used by GitHub Actions cron.
Loads .env (or GitHub Secrets via env), calls check_positions_and_notify() once, exits.
"""
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("poll_once")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # GitHub Actions sets env vars directly via secrets

# Import the canonical check function from server.py — no logic duplication
from server import check_positions_and_notify


def main() -> int:
    try:
        positions = check_positions_and_notify()
        open_count = sum(1 for p in positions
                         if str(p.get("Status", "OPEN")).upper() == "OPEN")
        closed_count = len(positions) - open_count
        logger.info("poll complete: %d open, %d closed (total %d)",
                    open_count, closed_count, len(positions))
        return 0
    except Exception as exc:
        logger.exception("poll failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
