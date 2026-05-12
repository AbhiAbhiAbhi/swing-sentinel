"""
Chartink Fetcher Module
Scans NSE stocks via Chartink screener API using technical indicator conditions
"""
import requests
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

CHARTINK_SCREENER_URL = "https://chartink.com/screener/"
CHARTINK_PROCESS_URL = "https://chartink.com/screener/process"

SESSION_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
}


def build_scan_clause() -> str:
    """
    Returns Chartink DSL conditions matching the 35-point framework technical criteria:
      - Price above EMA 20, EMA 20 > EMA 50, Price above EMA 200 (uptrend structure)
      - RSI(14) between 45 and 65 (momentum, not overbought)
      - MACD line above signal line (bullish crossover)
      - MACD histogram > 0 (green histogram)
      - ADX(14) > 20 (trend is strong enough)
      - Volume > 5 lakh shares (liquid)
      - Turnover > ₹5 Cr (close * volume > 50,000,000)
      - Within 15% of 52-week high (price near highs, not beaten down)
    """
    return (
        "( {cash} ( "
        "latest close > latest ema(close,20) and "
        "latest ema(close,20) > latest ema(close,50) and "
        "latest close > latest ema(close,200) and "
        "latest rsi(14) > 45 and latest rsi(14) < 65 and "
        "latest macd line(26,12,9) > latest macd signal(26,12,9) and "
        "latest macd histogram(26,12,9) > 0 and "
        "latest adx(14) > 20 and "
        "latest volume > 500000 and "
        "latest close * latest volume > 50000000 and "
        "latest close >= 0.85 * latest max(high,52) "
        ") )"
    )


def get_csrf_token(session: requests.Session) -> str:
    """
    Fetch Chartink screener page and extract CSRF token from cookies.
    Chartink sets a XSRF-TOKEN cookie on GET which must be sent as
    X-CSRF-TOKEN header on subsequent POST requests.
    """
    resp = session.get(CHARTINK_SCREENER_URL, headers=SESSION_HEADERS, timeout=15)
    resp.raise_for_status()

    # Chartink sets XSRF-TOKEN cookie
    csrf = session.cookies.get("XSRF-TOKEN")
    if not csrf:
        # Fallback: look for csrf-token meta tag in HTML
        import re
        match = re.search(r'meta name="csrf-token" content="([^"]+)"', resp.text)
        if match:
            csrf = match.group(1)

    if not csrf:
        raise RuntimeError("Could not extract CSRF token from Chartink")

    # URL-decode the token (cookies often URL-encode the value)
    from urllib.parse import unquote
    return unquote(csrf)


def fetch_chartink_stocks() -> List[Dict]:
    """
    Run the Chartink screener scan and return matching stocks.

    Returns:
        List of dicts, each with at minimum:
          { 'symbol': 'RELIANCE', 'name': 'Reliance Industries', 'close': 1345.2, ... }
        Returns empty list on error or if market is closed / no matches.
    """
    session = requests.Session()

    try:
        logger.info("[Chartink] Fetching CSRF token...")
        csrf_token = get_csrf_token(session)
        logger.info("[Chartink] CSRF token obtained")

        scan_clause = build_scan_clause()

        post_headers = {
            **SESSION_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": CHARTINK_SCREENER_URL,
            "X-CSRF-TOKEN": csrf_token,
        }

        payload = {"scan_clause": scan_clause}

        logger.info("[Chartink] Running screener scan...")
        resp = session.post(
            CHARTINK_PROCESS_URL,
            headers=post_headers,
            data=payload,
            timeout=30,
        )
        resp.raise_for_status()

        result = resp.json()

    except requests.exceptions.RequestException as exc:
        logger.error("[Chartink] Network error: %s", exc)
        return []
    except Exception as exc:
        logger.error("[Chartink] Unexpected error: %s", exc)
        return []

    raw_stocks = result.get("data", [])

    if not raw_stocks:
        logger.warning("[Chartink] Scan returned 0 stocks — market may be closed or no matches today")
        return []

    stocks = []
    for row in raw_stocks:
        # Chartink returns fields like: nsecode, company_name, close, volume, ...
        symbol = row.get("nsecode", "").strip().upper()
        if not symbol:
            continue
        stocks.append({
            "symbol": symbol,
            "name": row.get("company_name", symbol),
            "close": float(row.get("close", 0)),
            "volume": int(row.get("volume", 0)),
            "change_pct": float(row.get("per_chg", 0)),
        })

    logger.info("[Chartink] Scan matched %d stocks", len(stocks))
    return stocks


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    matches = fetch_chartink_stocks()
    if matches:
        print(f"\nChartink matched {len(matches)} stocks:\n")
        for s in matches:
            print(f"  {s['symbol']:15s} ₹{s['close']:>8.2f}  {s['change_pct']:+.2f}%  vol {s['volume']:,}")
    else:
        print("No matches returned.")
