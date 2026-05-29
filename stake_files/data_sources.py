"""
Data source adapters.
---------------------
Each adapter returns clean data OR raises DataSourceError so the API can
fall back gracefully. NSE endpoints are unofficial and rate-limited, so
everything here is defensive: retries, headers, and a clear failure path.

IMPORTANT REALITY CHECK
-----------------------
* Daily FII/DII flows + bulk/block deals: free via NSE (best-effort).
* Quarterly shareholding (FII%/DII%/promoter%/counts): NO reliable free API.
  fetch_quarterly_shareholding() tries NSE, but you should plan to either:
    (a) plug in a PAID provider (Trendlyne/Tijori) in the marked spot, or
    (b) feed it from a small DB table you update once a quarter.
"""
import time
import requests
from typing import Optional

NSE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


class DataSourceError(Exception):
    pass


def _nse_session() -> requests.Session:
    """NSE requires you to hit the homepage first to get cookies."""
    sess = requests.Session()
    sess.headers.update(NSE_HEADERS)
    try:
        sess.get("https://www.nseindia.com", timeout=10)
    except requests.RequestException as e:
        raise DataSourceError(f"Could not establish NSE session: {e}")
    return sess


def _get_json(sess: requests.Session, url: str, retries: int = 3) -> dict:
    last_err = None
    for attempt in range(retries):
        try:
            resp = sess.get(url, timeout=12)
            if resp.status_code == 200:
                return resp.json()
            last_err = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            last_err = str(e)
        time.sleep(1.5 * (attempt + 1))  # backoff
    raise DataSourceError(f"Failed to fetch {url}: {last_err}")


# ---------------------------------------------------------------------------
# 1. Daily market-wide FII/DII cash flows (FREE, best-effort)
# ---------------------------------------------------------------------------
def fetch_fii_dii_daily() -> list[dict]:
    """
    Returns recent daily FII & DII net cash figures (₹ Cr).
    """
    sess = _nse_session()
    data = _get_json(sess, "https://www.nseindia.com/api/fiidiiTradeReact")
    out = []
    for row in data:
        out.append({
            "date": row.get("date"),
            "category": row.get("category"),       # 'DII **' or 'FII/FPI *'
            "buy": float(row.get("buyValue", 0) or 0),
            "sell": float(row.get("sellValue", 0) or 0),
            "net": float(row.get("netValue", 0) or 0),
        })
    return out


# ---------------------------------------------------------------------------
# 2. Bulk / block deals for a symbol (FREE, best-effort)
# ---------------------------------------------------------------------------
def fetch_bulk_deals(symbol: str, days: int = 30) -> list[dict]:
    """
    Returns recent bulk deals. NSE serves a rolling window; filter by symbol.
    """
    sess = _nse_session()
    # historical bulk deals endpoint (date range handled server-side)
    url = "https://www.nseindia.com/api/historical/bulk-deals"
    try:
        data = _get_json(sess, url)
        records = data.get("data", data if isinstance(data, list) else [])
    except DataSourceError:
        return []  # bulk deals are optional; don't fail the whole scan
    sym = symbol.upper()
    out = []
    for r in records:
        if str(r.get("BD_SYMBOL", r.get("symbol", ""))).upper() == sym:
            out.append({
                "date": r.get("BD_DT_DATE", r.get("date")),
                "client": r.get("BD_CLIENT_NAME", r.get("clientName")),
                "buy_sell": r.get("BD_BUY_SELL", r.get("buySell")),
                "qty": r.get("BD_QTY_TRD", r.get("quantity")),
                "price": r.get("BD_TP_WATP", r.get("price")),
            })
    return out


# ---------------------------------------------------------------------------
# 3. Quarterly shareholding pattern  (THE HARD ONE)
# ---------------------------------------------------------------------------
def fetch_quarterly_shareholding(symbol: str) -> Optional[dict]:
    """
    Attempts NSE corporate-info shareholding pattern.
    Returns the latest quarter's aggregate %s if parseable, else None.

    >>> PLUG A PAID PROVIDER HERE for reliable history & investor counts. <<<
    NSE's free feed does NOT give FII/FPI investor counts or MF scheme counts
    reliably, which Component 3 (breadth) needs.
    """
    sess = _nse_session()
    url = (f"https://www.nseindia.com/api/corp-info?"
           f"symbol={symbol.upper()}&corpType=shareholding&market=equity")
    try:
        data = _get_json(sess, url)
    except DataSourceError:
        return None
    # NSE's structure varies; this is a best-effort parse.
    # Real deployments should map the paid provider's clean schema instead.
    return {"_raw": data, "_note": "Parse/replace with paid provider schema"}


# ---------------------------------------------------------------------------
# Paid-provider stub — implement against your subscription
# ---------------------------------------------------------------------------
def fetch_shareholding_from_paid_provider(symbol: str, api_key: str) -> list[dict]:
    """
    Recommended path for FULL automation of the quarterly half.
    Expected return: list of last 3 quarters, newest last, each:
      {fii_pct, dii_pct, promoter_pct, fii_investor_count, mf_scheme_count, quarter}
    Implement using e.g. Trendlyne / Tijori / your chosen vendor.
    """
    raise NotImplementedError(
        "Wire this to your paid data vendor. Until then, the API accepts "
        "quarterly figures via POST body or a local store."
    )
