#!/usr/bin/env python3
"""
TradingView MCP Server
======================
Tools
-----
screener          — Screen stocks with TA filters (TV Screener API, no auth)
get_indicators    — All TA indicators for one symbol (TV Screener API, no auth)
get_ohlcv         — Historical OHLCV + computed indicators (yfinance, no auth)
add_alert         — Create a price/indicator alert      (browser, needs auth)
list_alerts       — List active alerts                  (TV REST API, needs auth)
delete_alert      — Remove an alert by id               (TV REST API, needs auth)
paper_trade       — Execute a paper-trade order         (browser, needs auth)
get_positions     — Open paper positions + P&L          (browser, needs auth)
add_drawing       — Add a chart drawing                 (browser, needs auth)
get_watchlist     — Get watchlist symbols               (TV REST API, needs auth)
add_to_watchlist  — Add a symbol to a watchlist         (TV REST API, needs auth)

Authentication
--------------
Set TV_SESSION to the value of the `sessionid` cookie from a logged-in browser
session (DevTools → Application → Cookies → tradingview.com → sessionid).
OR set TV_PASSWORD and the browser tools will log in automatically.

Run
---
  pip install -r requirements.txt
  playwright install chromium
  python server.py            # stdio transport (used by Claude Code MCP)
"""

import asyncio
import json
import os
from typing import Any

import httpx
import pandas as pd
import yfinance as yf
import ta
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

# ═══════════════════════════════════════════════════════════════════════════ #
#  Config
# ═══════════════════════════════════════════════════════════════════════════ #
TV_USERNAME = os.getenv("TV_USERNAME", "sufficientHunt88897")
TV_PASSWORD = os.getenv("TV_PASSWORD", "")
TV_SESSION  = os.getenv("TV_SESSION", "")
TV_HEADLESS = os.getenv("TV_HEADLESS", "true").lower() != "false"

TV_COOKIE_DOMAIN = ".tradingview.com"
TV_BASE          = "https://www.tradingview.com"
TV_SCANNER_BASE  = "https://scanner.tradingview.com"

mcp = FastMCP(
    "tradingview",
    instructions="TradingView: screener, indicators, OHLCV, alerts, paper trading, drawings",
)

# ═══════════════════════════════════════════════════════════════════════════ #
#  Screener constants
# ═══════════════════════════════════════════════════════════════════════════ #
_MARKETS: dict[str, str] = {
    "india": "india", "nse": "india", "bse": "india",
    "us": "america", "usa": "america", "america": "america",
    "crypto": "crypto", "forex": "forex",
    "euronext": "euronext", "uk": "uk",
}

# Maps user-friendly interval strings → TV screener column suffix
_INTERVAL_SUFFIX: dict[str, str] = {
    "1m": "|1", "3m": "|3", "5m": "|5", "15m": "|15",
    "30m": "|30", "1h": "|60", "2h": "|120", "4h": "|240",
    "1d": "", "1w": "|1W", "1M": "|1M",
}

# Maps user-friendly operator names → TV filter operation strings
_OP_MAP: dict[str, str] = {
    "above": "greater", "greater": "greater", ">": "greater",
    "below": "less",    "less": "less",       "<": "less",
    "equal": "equal",   "=": "equal",         "==": "equal",
    "between":   "in_range",     "in_range": "in_range",
    "not_between": "not_in_range",
    "cross_above":  "crosses_above", "crosses_above":  "crosses_above",
    "cross_below":  "crosses_below", "crosses_below":  "crosses_below",
    "cross": "crosses",
    "match": "match",
}

_BASE_COLUMNS: list[str] = [
    "name", "description",
    "close", "open", "high", "low", "volume",
    "change", "change_abs",
    "RSI", "RSI[1]",
    "MACD.macd", "MACD.signal", "MACD.hist",
    "EMA5", "EMA10", "EMA20", "EMA50", "EMA100", "EMA200",
    "SMA5", "SMA10", "SMA20", "SMA50", "SMA100", "SMA200",
    "BB.upper", "BB.lower",
    "Stoch.K", "Stoch.D",
    "ADX", "ADX+DI", "ADX-DI",
    "CCI20", "AO", "Mom",
    "VWAP", "ATR", "P.SAR",
    "Recommend.All", "Recommend.MA", "Recommend.Other",
    "price_52_week_high", "price_52_week_low",
    "High.1M", "Low.1M",
    "Perf.W", "Perf.1M", "Perf.3M", "Perf.Y",
    "market_cap_basic", "sector", "industry", "exchange",
]

# ═══════════════════════════════════════════════════════════════════════════ #
#  Shared HTTP helpers
# ═══════════════════════════════════════════════════════════════════════════ #
def _tv_headers(extra: dict | None = None) -> dict[str, str]:
    h = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Origin":  TV_BASE,
        "Referer": TV_BASE + "/",
    }
    if TV_SESSION:
        h["Cookie"] = f"sessionid={TV_SESSION}"
    if extra:
        h.update(extra)
    return h


async def _get_csrf() -> str:
    """Fetch CSRF token from TradingView (required for POST/DELETE with session)."""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(TV_BASE + "/", headers=_tv_headers())
        for k, v in r.cookies.items():
            if "csrf" in k.lower():
                return v
    return ""


# ═══════════════════════════════════════════════════════════════════════════ #
#  Browser helpers (Playwright)
# ═══════════════════════════════════════════════════════════════════════════ #
_pw_instance   = None
_browser       = None
_browser_ctx   = None


async def _ensure_browser():
    global _pw_instance, _browser, _browser_ctx
    if _browser_ctx is not None:
        return _browser_ctx

    from playwright.async_api import async_playwright
    _pw_instance = await async_playwright().__aenter__()
    _browser = await _pw_instance.chromium.launch(
        headless=TV_HEADLESS,
        args=["--disable-blink-features=AutomationControlled"],
    )
    _browser_ctx = await _browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
    )
    if TV_SESSION:
        await _browser_ctx.add_cookies([{
            "name":     "sessionid",
            "value":    TV_SESSION,
            "domain":   TV_COOKIE_DOMAIN,
            "path":     "/",
            "secure":   True,
            "sameSite": "None",
        }])
    return _browser_ctx


async def _new_tv_page(path: str = "/"):
    ctx = await _ensure_browser()
    page = await ctx.new_page()
    await page.goto(TV_BASE + path, wait_until="domcontentloaded", timeout=60_000)

    # If no session cookie, attempt password login
    if not TV_SESSION and TV_PASSWORD:
        try:
            btn = page.get_by_role("button", name="Sign in")
            if await btn.is_visible(timeout=4_000):
                await btn.click()
                await page.wait_for_selector('[name="username"]', timeout=8_000)
                await page.fill('[name="username"]', TV_USERNAME)
                await page.fill('[name="password"]', TV_PASSWORD)
                await page.get_by_role("button", name="Sign in").click()
                await page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
    return page


def _require_auth() -> str | None:
    if not TV_SESSION and not TV_PASSWORD:
        return (
            "Authentication required. Set TV_SESSION (recommended) or TV_PASSWORD "
            "in the .env file. See .env.example for instructions."
        )
    return None


# ═══════════════════════════════════════════════════════════════════════════ #
#  TOOL 1 — screener
# ═══════════════════════════════════════════════════════════════════════════ #
@mcp.tool()
async def screener(
    market: str = "india",
    conditions: str = "[]",
    interval: str = "1d",
    sort_by: str = "volume",
    sort_order: str = "desc",
    limit: int = 50,
    extra_columns: str = "[]",
) -> str:
    """
    Screen stocks on TradingView using technical-analysis filters.

    Args:
        market: Market to scan — india / nse / bse / us / crypto / forex
        conditions: JSON array of filter objects. Supported formats:
            {"indicator":"RSI",       "op":"below",       "value":30}
            {"indicator":"close",     "op":"above",       "value":"EMA200"}
            {"indicator":"MACD.macd", "op":"cross_above", "value":"MACD.signal"}
            {"indicator":"RSI",       "op":"between",     "value":[30,70]}
            ops: above/greater/> | below/less/< | equal | between |
                 cross_above | cross_below | cross | not_between | match
        interval: Timeframe — 1m 5m 15m 30m 1h 2h 4h 1d 1w 1M  (default: 1d)
        sort_by: Column to sort results by (default: "volume")
        sort_order: "asc" or "desc"
        limit: Number of results (1–200, default: 50)
        extra_columns: JSON array of additional TV columns to include, e.g.
            ["High.1M","Low.1M","change|1W"]
    Returns:
        JSON array of matching stocks with their indicator values.
    """
    market_key = _MARKETS.get(market.lower(), "america")
    sfx        = _INTERVAL_SUFFIX.get(interval, "")

    try:
        raw_conds = json.loads(conditions)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"conditions parse error: {e}"})

    # Build TV filter list
    tv_filters: list[dict] = []
    for c in raw_conds:
        ind = c.get("indicator", "")
        op  = _OP_MAP.get(str(c.get("op", "")).lower(), c.get("op", "greater"))
        val = c.get("value")
        lhs = ind + sfx if sfx and not ind.endswith(sfx) else ind
        tv_filters.append({"left": lhs, "operation": op, "right": val})

    # Columns: apply interval suffix to per-bar indicators, not meta fields
    _META = {"name", "description", "sector", "industry", "exchange",
             "market_cap_basic", "price_52_week_high", "price_52_week_low",
             "High.1M", "Low.1M", "Perf.W", "Perf.1M", "Perf.3M", "Perf.Y"}
    columns = [
        (col + sfx if sfx and col not in _META and not col.endswith(sfx) else col)
        for col in _BASE_COLUMNS
    ]
    try:
        extra = json.loads(extra_columns)
        columns.extend(extra)
    except Exception:
        pass

    payload: dict[str, Any] = {
        "filter":  tv_filters,
        "columns": columns,
        "options": {"lang": "en"},
        "sort":    {"sortBy": sort_by + sfx if sfx and sort_by not in _META else sort_by,
                    "sortOrder": sort_order},
        "range":   [0, min(int(limit), 200)],
    }

    url = f"{TV_SCANNER_BASE}/{market_key}/scan"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload, headers=_tv_headers())
            if not resp.is_success:
                return json.dumps({"error": f"TV API {resp.status_code}: {resp.text[:300]}"})
            data = resp.json()
    except Exception as e:
        return json.dumps({"error": str(e)})

    rows = []
    for item in data.get("data", []):
        row = {"symbol": item.get("s", "")}
        for i, col in enumerate(columns):
            # Strip the interval suffix from key names for readability
            key = col.replace(sfx, "") if sfx else col
            row[key] = item["d"][i] if i < len(item.get("d", [])) else None
        rows.append(row)

    return json.dumps({"total": data.get("totalCount", len(rows)), "results": rows}, indent=2)


# ═══════════════════════════════════════════════════════════════════════════ #
#  TOOL 2 — get_indicators
# ═══════════════════════════════════════════════════════════════════════════ #
@mcp.tool()
async def get_indicators(
    symbol: str,
    market: str = "india",
    interval: str = "1d",
) -> str:
    """
    Fetch all TradingView technical indicators for a single symbol.

    Args:
        symbol: Ticker without exchange prefix, e.g. "RELIANCE", "INFY", "AAPL"
        market: india / us / crypto / forex
        interval: 1m 5m 15m 30m 1h 4h 1d 1w 1M  (default: 1d)

    Returns:
        JSON dict of RSI, MACD, EMAs, BBands, ADX, Stoch, Recommend, etc.
        Recommend.All:  +1 = Strong Buy, -1 = Strong Sell, 0 = Neutral.
    """
    market_key = _MARKETS.get(market.lower(), "india")
    sfx        = _INTERVAL_SUFFIX.get(interval, "")

    _META = {"name", "description", "sector", "industry", "exchange",
             "market_cap_basic", "price_52_week_high", "price_52_week_low",
             "High.1M", "Low.1M", "Perf.W", "Perf.1M", "Perf.3M", "Perf.Y"}
    columns = [
        (col + sfx if sfx and col not in _META and not col.endswith(sfx) else col)
        for col in _BASE_COLUMNS
    ]

    payload: dict[str, Any] = {
        "symbols":  {"tickers": [symbol.upper()], "query": {"types": []}},
        "columns":  columns,
    }

    url = f"{TV_SCANNER_BASE}/{market_key}/scan"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload, headers=_tv_headers())
            if not resp.is_success:
                return json.dumps({"error": f"TV API {resp.status_code}: {resp.text[:200]}"})
            data = resp.json()
    except Exception as e:
        return json.dumps({"error": str(e)})

    items = data.get("data", [])
    if not items:
        return json.dumps({"error": f"Symbol '{symbol}' not found in market '{market}'"})

    row: dict[str, Any] = {"symbol": items[0].get("s", symbol)}
    for i, col in enumerate(columns):
        key = col.replace(sfx, "") if sfx else col
        row[key] = items[0]["d"][i] if i < len(items[0].get("d", [])) else None

    # Interpret Recommend.All
    rec = row.get("Recommend.All")
    if rec is not None:
        if   rec >=  0.5: row["_signal"] = "Strong Buy"
        elif rec >=  0.1: row["_signal"] = "Buy"
        elif rec <= -0.5: row["_signal"] = "Strong Sell"
        elif rec <= -0.1: row["_signal"] = "Sell"
        else:             row["_signal"] = "Neutral"

    return json.dumps(row, indent=2)


# ═══════════════════════════════════════════════════════════════════════════ #
#  TOOL 3 — get_ohlcv
# ═══════════════════════════════════════════════════════════════════════════ #
@mcp.tool()
async def get_ohlcv(
    symbol: str,
    interval: str = "1d",
    period: str = "6mo",
    include_indicators: bool = True,
    rows: int = 100,
) -> str:
    """
    Fetch historical OHLCV candles for a symbol, with optional TA indicators.

    Args:
        symbol: Yahoo Finance ticker, e.g. "RELIANCE.NS", "INFY.NS", "AAPL"
                For Indian stocks append ".NS" (NSE) or ".BO" (BSE).
        interval: 1m 2m 5m 15m 30m 60m 90m 1h 1d 5d 1wk 1mo 3mo
        period:   1d 5d 1mo 3mo 6mo 1y 2y 5y 10y ytd max  (default: 6mo)
        include_indicators: Compute RSI, MACD, BBands, EMA, SMA, ATR, Stoch (default: true)
        rows: Number of most-recent rows to return (default: 100, max: 500)

    Returns:
        JSON with columns: date, open, high, low, close, volume + indicator columns.
    """
    loop = asyncio.get_event_loop()
    try:
        df: pd.DataFrame = await loop.run_in_executor(
            None, lambda: yf.Ticker(symbol).history(period=period, interval=interval)
        )
    except Exception as e:
        return json.dumps({"error": str(e)})

    if df.empty:
        return json.dumps({"error": f"No data for '{symbol}'"})

    df.index = df.index.tz_localize(None) if hasattr(df.index, "tz") and df.index.tz else df.index
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]

    if include_indicators and len(df) >= 26:
        c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

        # Trend
        for n in (5, 10, 20, 50, 200):
            df[f"EMA{n}"] = ta.trend.EMAIndicator(c, window=n).ema_indicator().round(4)
            df[f"SMA{n}"] = c.rolling(n).mean().round(4)

        # Momentum
        df["RSI14"] = ta.momentum.RSIIndicator(c, window=14).rsi().round(2)
        _macd = ta.trend.MACD(c)
        df["MACD"]        = _macd.macd().round(4)
        df["MACD_signal"] = _macd.macd_signal().round(4)
        df["MACD_hist"]   = _macd.macd_diff().round(4)
        _stoch = ta.momentum.StochasticOscillator(h, l, c)
        df["Stoch_K"] = _stoch.stoch().round(2)
        df["Stoch_D"] = _stoch.stoch_signal().round(2)
        df["CCI20"]   = ta.trend.CCIIndicator(h, l, c, window=20).cci().round(2)

        # Volatility
        _bb = ta.volatility.BollingerBands(c)
        df["BB_upper"] = _bb.bollinger_hband().round(4)
        df["BB_mid"]   = _bb.bollinger_mavg().round(4)
        df["BB_lower"] = _bb.bollinger_lband().round(4)
        df["ATR14"]    = ta.volatility.AverageTrueRange(h, l, c, window=14).average_true_range().round(4)

        # Trend strength
        _adx = ta.trend.ADXIndicator(h, l, c, window=14)
        df["ADX"]    = _adx.adx().round(2)
        df["ADX_DI+"] = _adx.adx_pos().round(2)
        df["ADX_DI-"] = _adx.adx_neg().round(2)

        # Volume
        if len(df) >= 2:
            df["VWAP"] = (v * (h + l + c) / 3).cumsum() / v.cumsum()
            df["VWAP"] = df["VWAP"].round(4)

    df = df.tail(min(int(rows), 500))
    df.index = df.index.strftime("%Y-%m-%d %H:%M:%S")
    df.index.name = "date"
    df = df.reset_index()
    df = df.where(pd.notnull(df), None)

    return json.dumps({
        "symbol":   symbol,
        "interval": interval,
        "rows":     len(df),
        "data":     df.to_dict(orient="records"),
    }, indent=2)


# ═══════════════════════════════════════════════════════════════════════════ #
#  TOOL 4 — add_alert  (browser)
# ═══════════════════════════════════════════════════════════════════════════ #
@mcp.tool()
async def add_alert(
    symbol: str,
    condition: str,
    value: float,
    message: str = "",
    expiry_hours: int = 0,
    notify_popup: bool = True,
    notify_email: bool = False,
    webhook_url: str = "",
) -> str:
    """
    Create a price or indicator alert on TradingView (requires auth).

    Args:
        symbol: Full symbol with exchange prefix, e.g. "NSE:RELIANCE", "NASDAQ:AAPL"
        condition: One of:
            crossing_up    — price crosses above value
            crossing_down  — price crosses below value
            greater_than   — price > value
            less_than      — price < value
            inside_channel — price between value and a second level (use message to note both)
        value: The threshold price or level
        message: Custom alert message/note
        expiry_hours: Hours until alert expires (0 = never)
        notify_popup: Show browser popup (default: true)
        notify_email: Send email notification (default: false)
        webhook_url: Optional webhook URL to call when alert fires

    Returns:
        JSON with status and alert details.
    """
    err = _require_auth()
    if err:
        return json.dumps({"error": err})

    # Map condition to TV alert condition name
    _cond_map = {
        "crossing_up":   "crossing_up",
        "crossing_down": "crossing_down",
        "greater_than":  "greater_than",
        "less_than":     "less_than",
        "inside_channel": "inside_channel",
        "crossing":      "crossing",
    }
    tv_cond = _cond_map.get(condition.lower(), "greater_than")

    try:
        page = await _new_tv_page(f"/chart/?symbol={symbol.upper()}")
        await page.wait_for_timeout(3000)

        # Click the Alert button (clock icon in top toolbar)
        alert_btn = page.locator('[data-name="alerts"]').first
        if not await alert_btn.is_visible(timeout=8_000):
            # Try the keyboard shortcut
            await page.keyboard.press("Alt+a")
        else:
            await alert_btn.click()

        await page.wait_for_timeout(1500)

        # Click "Create Alert" in the alerts panel
        create_btn = page.get_by_role("button", name="Create Alert").first
        if await create_btn.is_visible(timeout=6_000):
            await create_btn.click()
        else:
            # Try the + button
            await page.locator('button[data-name="add-alert"]').first.click()

        await page.wait_for_timeout(1500)

        # In the "Create Alert" dialog — set condition
        # First dropdown: symbol (already pre-filled from URL)
        # Second dropdown: condition type
        cond_selector = page.locator('[data-name="alert-dialog"] select, [data-name="alert-dialog"] [role="listbox"]').nth(1)
        if await cond_selector.is_visible(timeout=4_000):
            await cond_selector.select_option(label=tv_cond.replace("_", " ").title())

        # Price/value input
        price_input = page.locator('[data-name="alert-dialog"] input[inputmode="decimal"]').first
        if await price_input.is_visible(timeout=4_000):
            await price_input.fill(str(value))

        # Message
        if message:
            msg_area = page.locator('[data-name="alert-dialog"] textarea[name="message"]').first
            if await msg_area.is_visible(timeout=3_000):
                await msg_area.fill(message)

        # Webhook URL
        if webhook_url:
            wh_checkbox = page.locator('[data-name="alert-dialog"] input[type="checkbox"]').filter(has_text="Webhook")
            if await wh_checkbox.is_visible(timeout=2_000):
                await wh_checkbox.check()
                wh_input = page.locator('[data-name="alert-dialog"] input[placeholder*="http"]').first
                await wh_input.fill(webhook_url)

        # Email notification
        if notify_email:
            email_cb = page.locator('[data-name="alert-dialog"]').get_by_label("Send email", exact=False)
            if await email_cb.is_visible(timeout=2_000):
                await email_cb.check()

        # Save
        save_btn = page.get_by_role("button", name="Create").last
        if not await save_btn.is_visible(timeout=3_000):
            save_btn = page.get_by_role("button", name="Save").last
        await save_btn.click()
        await page.wait_for_timeout(2000)

        await page.close()
        return json.dumps({
            "status":  "created",
            "symbol":  symbol.upper(),
            "condition": condition,
            "value":   value,
            "message": message,
        })
    except Exception as e:
        return json.dumps({"error": f"Browser automation error: {e}"})


# ═══════════════════════════════════════════════════════════════════════════ #
#  TOOL 5 — list_alerts  (REST API)
# ═══════════════════════════════════════════════════════════════════════════ #
@mcp.tool()
async def list_alerts() -> str:
    """
    List all active TradingView alerts for the authenticated user.

    Returns:
        JSON array of alert objects with id, name, symbol, condition, status.
    """
    err = _require_auth()
    if err:
        return json.dumps({"error": err})

    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{TV_BASE}/api/v1/alerts/",
                headers=_tv_headers({"Accept": "application/json"}),
            )
            r.raise_for_status()
            return json.dumps(r.json(), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ═══════════════════════════════════════════════════════════════════════════ #
#  TOOL 6 — delete_alert  (REST API)
# ═══════════════════════════════════════════════════════════════════════════ #
@mcp.tool()
async def delete_alert(alert_id: int) -> str:
    """
    Delete a TradingView alert by its ID.

    Args:
        alert_id: Numeric alert ID (get it from list_alerts).

    Returns:
        JSON with status.
    """
    err = _require_auth()
    if err:
        return json.dumps({"error": err})

    csrf = await _get_csrf()
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.delete(
                f"{TV_BASE}/api/v1/alerts/{alert_id}/",
                headers=_tv_headers({
                    "X-CSRFToken": csrf,
                    "Referer": f"{TV_BASE}/chart/",
                }),
            )
            r.raise_for_status()
            return json.dumps({"status": "deleted", "alert_id": alert_id})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ═══════════════════════════════════════════════════════════════════════════ #
#  TOOL 7 — paper_trade  (browser)
# ═══════════════════════════════════════════════════════════════════════════ #
@mcp.tool()
async def paper_trade(
    symbol: str,
    action: str,
    quantity: float,
    order_type: str = "market",
    price: float = 0.0,
    stop_loss: float = 0.0,
    take_profit: float = 0.0,
) -> str:
    """
    Execute a paper trade on TradingView's built-in paper trading broker.

    Args:
        symbol: Full symbol, e.g. "NSE:RELIANCE"
        action: "buy" or "sell"
        quantity: Number of units/shares
        order_type: "market" | "limit" | "stop" | "stop_limit"
        price: Limit/stop price (required for non-market orders)
        stop_loss: Stop-loss price (0 = no SL)
        take_profit: Take-profit price (0 = no TP)

    Returns:
        JSON with order status and details.
    """
    err = _require_auth()
    if err:
        return json.dumps({"error": err})

    if action.lower() not in ("buy", "sell"):
        return json.dumps({"error": "action must be 'buy' or 'sell'"})

    try:
        page = await _new_tv_page(f"/chart/?symbol={symbol.upper()}")
        await page.wait_for_timeout(4000)

        # Activate the trading panel — click "Trading Panel" button
        panel_btn = page.locator('[data-name="trading-panel-button"]').first
        if await panel_btn.is_visible(timeout=6_000):
            await panel_btn.click()
            await page.wait_for_timeout(2000)

        # Select the Paper Trading broker if not already active
        broker_select = page.locator('[data-name="broker-connect-button"]')
        if await broker_select.is_visible(timeout=4_000):
            await broker_select.click()
            await page.wait_for_timeout(1000)
            paper_opt = page.get_by_text("Paper Trading", exact=False).first
            if await paper_opt.is_visible(timeout=3_000):
                await paper_opt.click()
                await page.wait_for_timeout(2000)
                # Confirm/connect dialog
                confirm = page.get_by_role("button", name="Continue").first
                if await confirm.is_visible(timeout=3_000):
                    await confirm.click()
                    await page.wait_for_timeout(2000)

        # Order panel — find Buy / Sell button
        side_btn = page.locator(
            f'[data-name="order-panel"] button[data-side="{action.lower()}"],'
            f'[class*="orderPanel"] button:has-text("{action.capitalize()}")'
        ).first
        if await side_btn.is_visible(timeout=5_000):
            await side_btn.click()
            await page.wait_for_timeout(500)

        # Quantity
        qty_input = page.locator('[data-name="order-panel"] [data-name="qty-input"] input').first
        if await qty_input.is_visible(timeout=4_000):
            await qty_input.triple_click()
            await qty_input.fill(str(quantity))

        # Order type
        if order_type != "market":
            ot_select = page.locator('[data-name="order-panel"] [data-name="order-type-select"]').first
            if await ot_select.is_visible(timeout=3_000):
                await ot_select.click()
                await page.wait_for_timeout(400)
                await page.get_by_text(order_type.replace("_", " ").title(), exact=False).first.click()
                await page.wait_for_timeout(400)
            if price:
                p_input = page.locator('[data-name="order-panel"] [data-name="price-input"] input').first
                if await p_input.is_visible(timeout=3_000):
                    await p_input.triple_click()
                    await p_input.fill(str(price))

        # Stop-loss
        if stop_loss:
            sl_input = page.locator('[data-name="order-panel"] [data-name="sl-input"] input').first
            if await sl_input.is_visible(timeout=2_000):
                await sl_input.triple_click()
                await sl_input.fill(str(stop_loss))

        # Take-profit
        if take_profit:
            tp_input = page.locator('[data-name="order-panel"] [data-name="tp-input"] input').first
            if await tp_input.is_visible(timeout=2_000):
                await tp_input.triple_click()
                await tp_input.fill(str(take_profit))

        # Submit order
        submit_btn = page.locator(
            '[data-name="order-panel"] button[data-name="submit-btn"],'
            f'[class*="orderPanel"] button:has-text("{action.capitalize()}")'
        ).last
        await submit_btn.click()
        await page.wait_for_timeout(3000)

        # Try to read order confirmation
        confirm_text = ""
        try:
            confirm_el = page.locator('[class*="notification"], [class*="orderConfirm"]').first
            if await confirm_el.is_visible(timeout=3_000):
                confirm_text = await confirm_el.inner_text()
        except Exception:
            pass

        await page.close()
        return json.dumps({
            "status":       "submitted",
            "symbol":       symbol.upper(),
            "action":       action.lower(),
            "quantity":     quantity,
            "order_type":   order_type,
            "price":        price or "market",
            "stop_loss":    stop_loss or None,
            "take_profit":  take_profit or None,
            "confirmation": confirm_text or "Order submitted (check TV trading panel for status)",
        })
    except Exception as e:
        return json.dumps({"error": f"Browser automation error: {e}"})


# ═══════════════════════════════════════════════════════════════════════════ #
#  TOOL 8 — get_positions  (browser)
# ═══════════════════════════════════════════════════════════════════════════ #
@mcp.tool()
async def get_positions() -> str:
    """
    Fetch open paper-trading positions and account equity from TradingView.

    Returns:
        JSON with account balance, open positions, and unrealised P&L.
    """
    err = _require_auth()
    if err:
        return json.dumps({"error": err})

    try:
        page = await _new_tv_page("/chart/")
        await page.wait_for_timeout(4000)

        # Open trading panel
        panel_btn = page.locator('[data-name="trading-panel-button"]').first
        if await panel_btn.is_visible(timeout=6_000):
            await panel_btn.click()
            await page.wait_for_timeout(2000)

        # Click Positions tab
        pos_tab = page.get_by_role("tab", name="Positions").first
        if await pos_tab.is_visible(timeout=5_000):
            await pos_tab.click()
            await page.wait_for_timeout(1500)

        # Scrape position rows
        rows = await page.locator('[data-name="position-row"], [class*="positionRow"]').all()
        positions = []
        for row in rows:
            try:
                text = await row.inner_text()
                positions.append(text.strip())
            except Exception:
                pass

        # Scrape balance
        balance_el = page.locator('[data-name="account-summary-balance"], [class*="accountBalance"]').first
        balance = ""
        try:
            balance = await balance_el.inner_text(timeout=3_000)
        except Exception:
            pass

        await page.close()
        return json.dumps({
            "account_balance": balance,
            "open_positions":  positions,
            "count":           len(positions),
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Browser automation error: {e}"})


# ═══════════════════════════════════════════════════════════════════════════ #
#  TOOL 9 — add_drawing  (browser)
# ═══════════════════════════════════════════════════════════════════════════ #
@mcp.tool()
async def add_drawing(
    symbol: str,
    drawing_type: str,
    price1: float,
    price2: float = 0.0,
    bar_offset1: int = -20,
    bar_offset2: int = 0,
    color: str = "#2196F3",
    extend: bool = False,
) -> str:
    """
    Add a technical drawing to a TradingView chart.

    Args:
        symbol: Full symbol, e.g. "NSE:RELIANCE"
        drawing_type: One of:
            trend_line       — Trend line between two price/bar points
            horizontal_line  — Horizontal ray at price1
            horizontal_ray   — Horizontal ray at price1 (right-extending)
            vertical_line    — Vertical line at bar_offset1
            rectangle        — Rectangle from (bar_offset1, price1) to (bar_offset2, price2)
            fibonacci        — Fibonacci retracement from price1 to price2
            pitchfork        — Andrews pitchfork (needs 3 points, use price1/price2 as outer)
            channel          — Parallel channel
            text             — Text label at price1 (put text in color field)
        price1: First price level
        price2: Second price level (for multi-point drawings)
        bar_offset1: Bars from current bar for the first point (negative = past, default: -20)
        bar_offset2: Bars from current bar for the second point (default: 0 = current bar)
        color: Hex colour, e.g. "#FF0000"
        extend: Extend the line to the right (for trend_line)

    Returns:
        JSON with status.
    """
    err = _require_auth()
    if err:
        return json.dumps({"error": err})

    _tool_keys = {
        "trend_line":      "LineToolTrendLine",
        "horizontal_line": "LineToolHorzLine",
        "horizontal_ray":  "LineToolHorzRay",
        "vertical_line":   "LineToolVertLine",
        "rectangle":       "LineToolRectangle",
        "fibonacci":       "LineToolFibRetracement",
        "pitchfork":       "LineToolPitchfork",
        "channel":         "LineToolParallelChannel",
        "text":            "LineToolText",
    }
    tool_key = _tool_keys.get(drawing_type.lower(), "LineToolHorzLine")

    # Use TradingView's chart save API approach via browser
    try:
        page = await _new_tv_page(f"/chart/?symbol={symbol.upper()}")
        await page.wait_for_timeout(5000)

        # Open the left drawing toolbar if collapsed
        toolbar = page.locator('[data-name="left-toolbar"]').first
        if not await toolbar.is_visible(timeout=4_000):
            toggle = page.locator('[data-name="toolbar-toggle"]').first
            if await toggle.is_visible(timeout=2_000):
                await toggle.click()
                await page.wait_for_timeout(500)

        # Use TradingView's JS API to draw programmatically
        js_code = f"""
        (function() {{
            try {{
                const chart = window.tvWidget ? window.tvWidget.chart() : null;
                if (!chart) return 'chart_not_found';

                const now = Math.floor(Date.now() / 1000);
                const oneBar = 86400;  // 1 day in seconds

                const points = [];
                points.push({{ time: now + {bar_offset1} * oneBar, price: {price1} }});
                {'points.push({ time: now + ' + str(bar_offset2) + ' * oneBar, price: ' + str(price2 or price1) + ' });' if price2 else ''}

                const shape = chart.createMultipointShape(points, {{
                    shape: '{tool_key}',
                    overrides: {{
                        linecolor:  '{color}',
                        linewidth:  2,
                        extend:     {'true' if extend else 'false'},
                    }}
                }});
                return shape ? 'created:' + shape : 'failed';
            }} catch(e) {{
                return 'error:' + e.message;
            }}
        }})()
        """
        result = await page.evaluate(js_code)

        await page.close()
        if result and str(result).startswith("created"):
            return json.dumps({
                "status":       "created",
                "symbol":       symbol.upper(),
                "drawing_type": drawing_type,
                "price1":       price1,
                "price2":       price2,
                "shape_id":     str(result).split(":")[-1],
            })
        else:
            return json.dumps({
                "status":  "attempted",
                "result":  str(result),
                "note":    "If chart_not_found, ensure the chart is fully loaded. You may need to draw manually.",
            })
    except Exception as e:
        return json.dumps({"error": f"Browser automation error: {e}"})


# ═══════════════════════════════════════════════════════════════════════════ #
#  TOOL 10 — get_watchlist  (REST API)
# ═══════════════════════════════════════════════════════════════════════════ #
@mcp.tool()
async def get_watchlist() -> str:
    """
    Get all TradingView watchlists and their symbols for the authenticated user.

    Returns:
        JSON array of watchlists, each with id, name, and symbols list.
    """
    err = _require_auth()
    if err:
        return json.dumps({"error": err})

    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{TV_BASE}/api/v1/symbols_list/custom/",
                headers=_tv_headers({"Accept": "application/json"}),
            )
            r.raise_for_status()
            return json.dumps(r.json(), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ═══════════════════════════════════════════════════════════════════════════ #
#  TOOL 11 — add_to_watchlist  (REST API)
# ═══════════════════════════════════════════════════════════════════════════ #
@mcp.tool()
async def add_to_watchlist(
    symbol: str,
    watchlist_id: str = "",
) -> str:
    """
    Add a symbol to a TradingView watchlist.

    Args:
        symbol: Full symbol with exchange prefix, e.g. "NSE:RELIANCE"
        watchlist_id: Watchlist ID from get_watchlist() — leave blank to use
                      the default/first watchlist.

    Returns:
        JSON with status.
    """
    err = _require_auth()
    if err:
        return json.dumps({"error": err})

    # Resolve watchlist id if not provided
    if not watchlist_id:
        wl_data = json.loads(await get_watchlist())
        if "error" in wl_data:
            return json.dumps(wl_data)
        lists = wl_data if isinstance(wl_data, list) else wl_data.get("data", [])
        if not lists:
            return json.dumps({"error": "No watchlists found. Create one on TradingView first."})
        watchlist_id = str(lists[0].get("id", ""))

    csrf = await _get_csrf()
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            # Fetch current symbols in watchlist
            r = await c.get(
                f"{TV_BASE}/api/v1/symbols_list/custom/{watchlist_id}/",
                headers=_tv_headers({"Accept": "application/json"}),
            )
            r.raise_for_status()
            current = r.json()
            current_syms = current.get("symbols", []) if isinstance(current, dict) else []

            if symbol.upper() in current_syms:
                return json.dumps({"status": "already_in_watchlist", "symbol": symbol.upper()})

            current_syms.append(symbol.upper())
            put_r = await c.put(
                f"{TV_BASE}/api/v1/symbols_list/custom/{watchlist_id}/",
                json={"symbols": current_syms},
                headers=_tv_headers({
                    "Content-Type": "application/json",
                    "X-CSRFToken":  csrf,
                    "Referer":      f"{TV_BASE}/chart/",
                }),
            )
            put_r.raise_for_status()
            return json.dumps({
                "status":       "added",
                "symbol":       symbol.upper(),
                "watchlist_id": watchlist_id,
                "total_symbols": len(current_syms),
            })
    except Exception as e:
        return json.dumps({"error": str(e)})


# ═══════════════════════════════════════════════════════════════════════════ #
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════ #
if __name__ == "__main__":
    mcp.run()
