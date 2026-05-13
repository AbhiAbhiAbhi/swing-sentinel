"""
Chartink Fetcher Module
Scans NSE stocks via Chartink screener API using technical indicator conditions
"""
import logging
import re
from typing import Dict, List
from urllib.parse import unquote

import requests

logger = logging.getLogger(__name__)

CHARTINK_SCREENER_URL = "https://chartink.com/screener/"
CHARTINK_PROCESS_URL  = "https://chartink.com/screener/process"

# Mimic a real browser session — required by Chartink
BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",  # exclude br — requests can't decode brotli
}


def build_scan_clause() -> str:
    """
    Chartink DSL for swing trade candidates — 7 core conditions:
      - Price above EMA20, EMA20 > EMA50, Price above EMA200  (uptrend structure)
      - RSI(14) between 40 and 70                              (momentum, not extreme)
      - MACD line above signal line                            (bullish crossover)
      - ADX(14) > 20                                           (trend is strong)
      - Volume > 5 lakh                                        (liquid stock)
    """
    return (
        "( {cash} ( "
        "latest close > latest ema(close,20) and "
        "latest ema(close,20) > latest ema(close,50) and "
        "latest close > latest ema(close,200) and "
        "latest rsi(14) > 40 and latest rsi(14) < 70 and "
        "latest macd line(26,12,9) > latest macd signal(26,12,9) and "
        "latest adx(14) > 20 and "
        "latest volume > 500000 "
        ") )"
    )


def _make_session() -> tuple:
    """
    Open a session on Chartink, return (session, csrf_token).

    Chartink uses Laravel which sets an XSRF-TOKEN cookie (URL-encoded).
    For AJAX POST requests it expects that value (URL-decoded) in the
    X-XSRF-TOKEN header AND keeps the laravel_session cookie intact.
    """
    session = requests.Session()

    # Step 1 — GET the screener page to receive cookies
    get_headers = {
        **BASE_HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    resp = session.get(CHARTINK_SCREENER_URL, headers=get_headers, timeout=20)
    resp.raise_for_status()

    # Step 2 — Extract CSRF token: try cookie first, then meta tag
    raw_token = session.cookies.get("XSRF-TOKEN") or session.cookies.get("xsrf-token")
    if not raw_token:
        m = re.search(r'meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', resp.text)
        if m:
            raw_token = m.group(1)

    if not raw_token:
        raise RuntimeError(
            "Could not find CSRF token. Chartink may have changed its auth flow."
        )

    # Laravel URL-encodes the cookie value — decode it
    csrf_token = unquote(raw_token)
    logger.debug("[Chartink] CSRF token: %s…", csrf_token[:20])
    return session, csrf_token


def fetch_chartink_stocks() -> List[Dict]:
    """
    Run the Chartink screener scan and return matching stocks.

    Returns a list of dicts:
      [{ 'symbol': 'RELIANCE', 'name': '...', 'close': 1345.2,
         'volume': 1200000, 'change_pct': 0.34 }, ...]

    Returns [] on any error or if no stocks match.
    """
    try:
        logger.info("[Chartink] Opening session…")
        session, csrf_token = _make_session()

        post_headers = {
            **BASE_HEADERS,
            "Accept":           "application/json, text/javascript, */*; q=0.01",
            "Content-Type":     "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer":          CHARTINK_SCREENER_URL,
            "Origin":           "https://chartink.com",
            "X-Requested-With": "XMLHttpRequest",
            # Laravel validates via X-XSRF-TOKEN (decoded cookie value)
            "X-XSRF-TOKEN":     csrf_token,
        }

        logger.info("[Chartink] POSTing scan…")
        resp = session.post(
            CHARTINK_PROCESS_URL,
            headers=post_headers,
            data={"scan_clause": build_scan_clause()},
            timeout=30,
        )

        logger.info("[Chartink] Response: HTTP %s, %d bytes", resp.status_code, len(resp.content))

        if resp.status_code != 200:
            logger.error("[Chartink] HTTP %s — body: %s", resp.status_code, resp.text[:300])
            resp.raise_for_status()

        if not resp.text.strip():
            logger.error("[Chartink] Empty response body")
            return []

        try:
            result = resp.json()
        except Exception as json_exc:
            logger.error("[Chartink] JSON parse failed: %s — body: %s", json_exc, resp.text[:300])
            return []

    except requests.exceptions.RequestException as exc:
        logger.error("[Chartink] Request error: %s", exc)
        return []
    except Exception as exc:
        logger.error("[Chartink] Unexpected error: %s", exc)
        return []

    raw = result.get("data", [])
    if not raw:
        logger.warning(
            "[Chartink] 0 stocks returned — market may be closed or no matches today"
        )
        return []

    # ETF suffixes / known ETF symbols to exclude from swing scan
    ETF_SUFFIXES = ("BEES", "CASE", "IETF", "GETF")
    ETF_SYMBOLS  = {"LIQUIDBEES", "TATAGOLD", "GOLDBEES", "JUNIORBEES", "NIFTYBEES",
                    "BANKBEES", "ITBEES", "PSUBNKBEES", "HNGSNGBEES", "INFRABEES",
                    "SMALLCAP", "MIDCAP", "CPSEETF", "BHARAT22ETF", "MON100"}

    stocks = []
    for row in raw:
        symbol = (row.get("nsecode") or row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        if symbol in ETF_SYMBOLS or any(symbol.endswith(s) for s in ETF_SUFFIXES):
            logger.debug("[Chartink] Skipping ETF: %s", symbol)
            continue
        stocks.append({
            "symbol":     symbol,
            "name":       row.get("company_name") or row.get("name") or symbol,
            "close":      float(row.get("close") or 0),
            "volume":     int(row.get("volume") or 0),
            "change_pct": float(row.get("per_chg") or row.get("change_pct") or 0),
        })

    logger.info("[Chartink] Matched %d stocks", len(stocks))
    return stocks


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    matches = fetch_chartink_stocks()
    if matches:
        print(f"\nChartink matched {len(matches)} stocks:\n")
        for s in matches:
            print(f"  {s['symbol']:15s}  Rs.{s['close']:>8.2f}  {s['change_pct']:+.2f}%  vol {s['volume']:,}")
    else:
        print("No matches returned.")
