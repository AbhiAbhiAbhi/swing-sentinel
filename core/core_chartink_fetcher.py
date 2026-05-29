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

# Chartink scan-group tokens. Keys are what the UI/preset stores; values are
# the token Chartink expects inside the scan clause (with curly braces).
# Whitelisted server-side to keep the DSL safe from arbitrary input.
import json
import os
import time
import io

UNIVERSE_TOKENS: dict = {
    "cash":              "cash",              # All NSE cash market
    "nifty50":           "nifty50",
    "niftynext50":       "niftynext50",
    "nifty100":          "nifty100",
    "nifty200":          "nifty200",
    "nifty500":          "nifty500",
    "niftymidcap150":    "niftymidcap150",
    "niftysmallcap250":  "niftysmallcap250",
    "fnolist":           "fnolist",           # F&O securities
}

# Default swing-trade scan conditions — must stay in sync with FILTER_DEFAULTS in swing_agent_app.html
DEFAULT_SCAN_PARAMS: dict = {
    "universe":             "cash",
    "min_price":            50,
    "rsi_min":              40,
    "rsi_max":              70,
    "adx_min":              20,
    "min_volume_lakh":       5,
    "require_macd":         True,
    "require_ema_alignment": True,
    "require_ema200":       True,
}


def _get_universe_symbols(universe_name: str) -> set:
    """
    Get dynamic list of symbols for a specific index/universe.
    Caches the results locally in data/universes_cache.json for 24 hours
    to ensure scans remain extremely fast and don't hit external APIs constantly.
    """
    import pandas as pd
    
    universe_name = str(universe_name).lower().strip()
    if universe_name == "cash":
        return set()
        
    cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    cache_path = os.path.join(cache_dir, "universes_cache.json")
    os.makedirs(cache_dir, exist_ok=True)
    
    cache_data = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                cache_data = json.load(f)
        except Exception as e:
            logger.warning("[universe] Cache read error: %s", e)
            
    # Check if cache is fresh (less than 24 hours old)
    last_updated = cache_data.get("last_updated", 0)
    current_time = time.time()
    
    if cache_data and (current_time - last_updated) < 86400 and universe_name in cache_data:
        return set(cache_data[universe_name])
        
    logger.info("[universe] Cache stale or missing universe '%s'. Re-fetching from official sources...", universe_name)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    
    new_cache = {
        "last_updated": current_time,
        "nifty50": [],
        "niftynext50": [],
        "nifty100": [],
        "nifty200": [],
        "nifty500": [],
        "niftymidcap150": [],
        "niftysmallcap250": [],
        "fnolist": []
    }
    
    # Preserve existing cache data if some fetch fails
    for k in new_cache:
        if k != "last_updated" and k in cache_data:
            new_cache[k] = cache_data[k]
            
    # 1. Fetch Nifty CSVs from NSE India
    nifty_urls = {
        "nifty50": "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
        "niftynext50": "https://archives.nseindia.com/content/indices/ind_niftynext50list.csv",
        "nifty100": "https://archives.nseindia.com/content/indices/ind_nifty100list.csv",
        "nifty200": "https://archives.nseindia.com/content/indices/ind_nifty200list.csv",
        "nifty500": "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
        "niftymidcap150": "https://archives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
        "niftysmallcap250": "https://archives.nseindia.com/content/indices/ind_niftysmallcap250list.csv",
    }
    
    for key, url in nifty_urls.items():
        try:
            logger.info("[universe] Fetching %s list...", key)
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                df = pd.read_csv(io.StringIO(resp.text))
                if "Symbol" in df.columns:
                    symbols = [s.strip().upper() for s in df["Symbol"].dropna().tolist() if s.strip()]
                    new_cache[key] = symbols
                    logger.info("[universe]   -> Loaded %d symbols for %s", len(symbols), key)
            else:
                logger.warning("[universe] Failed to fetch %s: Status %d", key, resp.status_code)
        except Exception as exc:
            logger.error("[universe] Error fetching %s: %s", key, exc)
            
    # 2. Fetch F&O List from Kite instruments API
    try:
        logger.info("[universe] Fetching F&O list from Kite instruments...")
        resp = requests.get("https://api.kite.trade/instruments", timeout=20)
        if resp.status_code == 200:
            df = pd.read_csv(io.StringIO(resp.text))
            nfo_df = df[df["exchange"] == "NFO"]
            if not nfo_df.empty:
                fno_symbols = sorted([s.strip().upper() for s in nfo_df["name"].dropna().unique().tolist() if s.strip()])
                new_cache["fnolist"] = fno_symbols
                logger.info("[universe]   -> Loaded %d unique F&O symbols", len(fno_symbols))
        else:
            logger.warning("[universe] Failed to fetch F&O list from Kite: Status %d", resp.status_code)
    except Exception as exc:
        logger.error("[universe] Error fetching F&O list: %s", exc)
        
    # Write to local cache
    try:
        tmp_path = f"{cache_path}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(new_cache, f, indent=2)
        os.replace(tmp_path, cache_path)
        logger.info("[universe] Cache successfully updated in %s", cache_path)
    except Exception as exc:
        logger.error("[universe] Failed to write cache file: %s", exc)
        
    return set(new_cache.get(universe_name, []))


def build_scan_clause(params: dict = None) -> str:
    """
    Chartink DSL for swing trade candidates. All thresholds are driven by
    the `params` dict so the UI can override them without touching this code.
    Missing keys fall back to DEFAULT_SCAN_PARAMS.
    """
    p = {**DEFAULT_SCAN_PARAMS, **(params or {})}
    
    # Always query cash segment from Chartink, as specific index segments (e.g. nifty50)
    # are not supported in their free/public screener POST API.
    # Python-side post-filtering will be done on the returned symbols.
    universe    = "cash"
    
    min_price   = p["min_price"]
    rsi_min     = p["rsi_min"]
    rsi_max     = p["rsi_max"]
    adx_min     = p["adx_min"]
    min_vol     = int(p["min_volume_lakh"] * 100_000)

    conds = [
        f"latest close >= {min_price}",
        "latest close > latest ema(close,20)",
    ]
    if p["require_ema_alignment"]:
        conds.append("latest ema(close,20) > latest ema(close,50)")
    if p["require_ema200"]:
        conds.append("latest close > latest ema(close,200)")
    conds.append(f"latest rsi(14) > {rsi_min} and latest rsi(14) < {rsi_max}")
    if p["require_macd"]:
        conds.append("latest macd line(26,12,9) > latest macd signal(26,12,9)")
    conds.append(f"latest adx(14) > {adx_min}")
    conds.append(f"latest volume > {min_vol}")

    return "( {" + universe + "} ( " + " and ".join(conds) + " ) )"


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


def fetch_chartink_stocks(params: dict = None) -> List[Dict]:
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
            data={"scan_clause": build_scan_clause(params)},
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

    # Get the selected universe name and load dynamic symbols list for post-filtering
    universe_name = str((params or {}).get("universe", "cash")).lower().strip()
    universe_symbols = _get_universe_symbols(universe_name)

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

        # Apply python-side post-filtering based on the selected index/universe
        if universe_name != "cash" and symbol not in universe_symbols:
            logger.debug("[Chartink] Skipping stock outside universe %s: %s", universe_name, symbol)
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
