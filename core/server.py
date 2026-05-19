"""
Swing Sentinel — Local Server
Run : python server.py
Open: http://localhost:5000
"""
import json
import logging
import os
from datetime import datetime

# Resolve paths relative to the project root (one level up from core/)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import requests as _requests
from flask import Flask, jsonify, redirect, request, send_from_directory

# Load .env if present (python-dotenv optional — falls back gracefully)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=os.path.join(_ROOT, "dashboard"))

# ── Telegram ──────────────────────────────────────────────────────────────────

def _tg_send(msg: str):
    """Send a Telegram message. Silently skips if credentials not configured."""
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id or token == "your_bot_token_here":
        return
    try:
        _requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("[telegram] send failed: %s", exc)

# ── Risk filters + sectors ───────────────────────────────────────────────────
try:
    from core.risk_filters import apply_risk_filters
    from core.sectors       import get_sector, fetch_sector_pulse
except ImportError:
    try:
        from core_risk_filters import apply_risk_filters
        from core_sectors      import get_sector, fetch_sector_pulse
    except ImportError:
        def apply_risk_filters(sym, tech, sector_pulse=None): return True, []
        def get_sector(sym): return "OTHERS"
        def fetch_sector_pulse(): return {}


# ── Kite helper ──────────────────────────────────────────────────────────────
try:
    from core.kite import get_kite, place_gtt
except ImportError:
    try:
        from core_kite import get_kite, place_gtt
    except ImportError:
        def get_kite():   return None
        def place_gtt(*a, **kw): return None

# ── News pipeline ────────────────────────────────────────────────────────────
try:
    from core.news_pipeline import get_news as _news_get, load_config as _news_load_cfg, save_config as _news_save_cfg, DEFAULT_CONFIG as _news_defaults
except ImportError:
    try:
        from core_news_pipeline import get_news as _news_get, load_config as _news_load_cfg, save_config as _news_save_cfg, DEFAULT_CONFIG as _news_defaults
    except ImportError:
        _news_get = None
        _news_load_cfg = None
        _news_save_cfg = None
        _news_defaults = {}

# ── Import helpers (flat dev layout OR deployed core/ folder) ──────────────
try:
    from core.chartink_fetcher import fetch_chartink_stocks
    from core.data_fetcher import (
        fetch_fii_dii_flow,
        fetch_global_markets,
        fetch_nifty_levels,
        fetch_prices_bulk,
        fetch_stock_technicals,
    )
    from core.trade_plan import calculate_rr, calculate_trade_plan
except ImportError:
    from core_chartink_fetcher import fetch_chartink_stocks
    from core_data_fetcher import (
        fetch_fii_dii_flow,
        fetch_global_markets,
        fetch_nifty_levels,
        fetch_prices_bulk,
        fetch_stock_technicals,
    )
    from core_trade_plan import calculate_rr, calculate_trade_plan


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the dashboard with no-cache headers so the browser can't pin a stale build."""
    resp = send_from_directory(os.path.join(_ROOT, "dashboard"), "swing_agent_app.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"]        = "no-cache"
    resp.headers["Expires"]       = "0"
    return resp


@app.route("/checklist")
def checklist():
    """Serve the interactive swing-trading checklist."""
    resp = send_from_directory(os.path.join(_ROOT, "dashboard"), "checklist.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"]        = "no-cache"
    resp.headers["Expires"]       = "0"
    return resp


@app.route("/api/kite/status")
def api_kite_status():
    """Check Kite connection status."""
    kite = get_kite()
    if not kite:
        return jsonify({"connected": False, "login_url": _kite_login_url()})
    try:
        profile = kite.profile()
        return jsonify({"connected": True, "user": profile.get("user_name", "")})
    except Exception:
        return jsonify({"connected": False, "login_url": _kite_login_url()})


@app.route("/api/kite/login")
def api_kite_login():
    """Redirect browser to Kite login page."""
    url = _kite_login_url()
    if not url:
        return jsonify({"status": "error", "message": "KITE_API_KEY not set in .env"}), 400
    return redirect(url)


@app.route("/api/kite/callback")
def api_kite_callback():
    """Kite OAuth callback — exchange request_token for access_token and save to .env."""
    req_token = request.args.get("request_token")
    if not req_token:
        return "Missing request_token", 400
    try:
        from kiteconnect import KiteConnect
        api_key    = os.getenv("KITE_API_KEY", "")
        api_secret = os.getenv("KITE_API_SECRET", "")
        kite = KiteConnect(api_key=api_key)
        data = kite.generate_session(req_token, api_secret=api_secret)
        _save_env_token(data["access_token"])
        logger.info("[kite] Access token saved for user %s", data.get("user_name", "?"))
    except Exception as exc:
        logger.error("[kite/callback] %s", exc)
        return f"Kite login failed: {exc}", 500
    return redirect("/")


@app.route("/api/sectors")
def api_sectors():
    """Live sector index trend + strength — feeds the Sector Pulse widget."""
    try:
        return jsonify({"status": "ok", "sectors": fetch_sector_pulse()})
    except Exception as exc:
        return jsonify({"status": "error", "sectors": {}, "message": str(exc)}), 500


@app.route("/api/market")
def api_market():
    """Fast endpoint: Nifty level + FII/DII + sentiment (~2–4 s)."""
    try:
        nifty = fetch_nifty_levels()
        fii   = fetch_fii_dii_flow(days=5)
        return jsonify({
            "status":    "ok",
            "nifty":     nifty,
            "fii_dii":   fii,
            "sentiment": _sentiment(nifty, fii),
        })
    except Exception as exc:
        logger.error("[market] %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/global")
def api_global():
    """US indices + USD/INR with 5-min cache."""
    try:
        return jsonify({"status": "ok", **fetch_global_markets()})
    except Exception as exc:
        logger.error("[global] %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/fo-ban")
def api_fo_ban():
    """NSE F&O securities currently in ban period (scrapes NSE website)."""
    try:
        r = _requests.get(
            "https://www.nseindia.com/api/fo-banlist",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Referer": "https://www.nseindia.com/",
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        # NSE returns {"data": [{"symbol": "X", ...}, ...]}
        symbols = [item.get("symbol", "") for item in data.get("data", []) if item.get("symbol")]
        return jsonify({"status": "ok", "count": len(symbols), "symbols": symbols})
    except Exception as exc:
        logger.warning("[fo-ban] %s", exc)
        return jsonify({"status": "error", "count": 0, "symbols": [], "message": str(exc)})


@app.route("/api/news")
def api_news():
    """Aggregated pre-market news: overall / sectors / stocks with FinBERT sentiment."""
    if _news_get is None:
        return jsonify({"status": "error", "message": "news pipeline not installed"}), 501
    try:
        force = request.args.get("force", "").lower() in ("1", "true", "yes")
        data = _news_get(force=force)
        return jsonify({"status": "ok", **data})
    except Exception as exc:
        logger.error("[news] %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/news/config", methods=["GET", "POST"])
def api_news_config():
    """GET current news config; POST { ...config } to update + invalidate cache."""
    if _news_load_cfg is None:
        return jsonify({"status": "error", "message": "news pipeline not installed"}), 501
    try:
        if request.method == "POST":
            body = request.get_json(force=True, silent=True) or {}
            cfg  = _news_load_cfg()
            # Whitelist editable keys to avoid clients writing arbitrary data
            editable = {
                "feeds", "time_window_hours", "refresh_minutes", "max_headlines",
                "positive_threshold", "negative_threshold",
                "enabled_sectors", "enabled_stocks", "use_watchlist_only", "model",
            }
            for k, v in body.items():
                if k in editable:
                    cfg[k] = v
            _news_save_cfg(cfg)
            logger.info("[news] config updated: keys=%s", sorted(body.keys()))
            return jsonify({"status": "ok", "config": cfg})
        return jsonify({"status": "ok", "config": _news_load_cfg(), "defaults": _news_defaults})
    except Exception as exc:
        logger.error("[news/config] %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/scan", methods=["POST"])
def api_scan():
    """
    Full scan: Chartink (works intraday and EOD) → yfinance trade plans.
    Accepts an optional JSON body: { "filters": { ... } } — all Chartink DSL
    thresholds and post-scan risk-filter thresholds are driven from this dict.
    If Chartink returns 0 matches, falls back to the last saved scan.
    """
    try:
        logger.info("[scan] Starting…")

        body    = request.get_json(force=True, silent=True) or {}
        filters = body.get("filters", {})
        top_n   = int(filters.get("top_n", 30))
        logger.info("[scan] filters=%s top_n=%d", filters, top_n)

        nifty = fetch_nifty_levels()
        fii   = fetch_fii_dii_flow(days=5)

        chartink_stocks = fetch_chartink_stocks(params=filters)

        if not chartink_stocks:
            brief = _load_latest_brief()
            if brief:
                brief["source"]  = "last_session"
                brief["message"] = f"No matches today. Showing last scan from {brief.get('date','?')} {brief.get('time','')}"
                logger.info("[scan] 0 matches — serving last brief (%s)", brief.get("date"))
                return jsonify(brief)
            return jsonify({
                "status":        "no_results",
                "message":       "No stocks matched today's conditions and no previous scan was found.",
                "date":          _now_date(),
                "time":          _now_time(),
                "stocks":        [],
                "actions":       [],
                "total_scanned": 0,
                "market":        {"nifty": nifty, "fii_dii": fii, "sentiment": _sentiment(nifty, fii)},
            })

        # Sort by volume (descending) and cap at top_n to keep yfinance calls fast
        chartink_stocks.sort(key=lambda x: x.get("volume", 0), reverse=True)
        chartink_stocks = chartink_stocks[:top_n]
        logger.info("[scan] Processing top %d stocks via yfinance…", len(chartink_stocks))

        # Pre-fetch sector pulse once (cached for 5min) — passed to every risk-filter call
        try:
            sector_pulse = fetch_sector_pulse()
        except Exception:
            sector_pulse = {}

        scan_results  = []
        filtered_out  = []   # [{symbol, name, reasons}]
        for i, stock in enumerate(chartink_stocks, 1):
            symbol = stock["symbol"]
            logger.info("[scan] %d/%d  %s", i, len(chartink_stocks), symbol)
            try:
                tech = fetch_stock_technicals(symbol)
                if not tech:
                    continue

                # ── Risk gating ───────────────────────────────────────────────
                passed, reasons = apply_risk_filters(symbol, tech, sector_pulse=sector_pulse, thresholds=filters)
                if not passed:
                    logger.info("[scan]   %s skipped → %s", symbol, "; ".join(reasons))
                    filtered_out.append({
                        "symbol":  symbol,
                        "name":    stock.get("name", symbol),
                        "reasons": reasons,
                    })
                    continue

                plan = calculate_trade_plan(tech)
                entry_mid = (plan.get("entry_zone_min", 0) + plan.get("entry_zone_max", 0)) / 2
                rr_raw    = calculate_rr({"price": entry_mid, "target": plan.get("target_2", 0), "sl": plan.get("stop_loss", 0)})
                scan_results.append({
                    "symbol":     symbol,
                    "name":       stock.get("name", symbol),
                    "price":      tech["price"],
                    "change_pct": tech["change_pct"],
                    "rsi":        tech["rsi"],
                    "ema20":      tech["ema20"],
                    "macd":       tech["macd"],
                    "vol_ratio":  tech["volume_ratio"],
                    "entry_min":  plan.get("entry_zone_min", 0),
                    "entry_max":  plan.get("entry_zone_max", 0),
                    "target_1":   plan.get("target_1", 0),
                    "target_2":   plan.get("target_2", 0),
                    "sl":         plan.get("stop_loss", 0),
                    "rr":         rr_raw if isinstance(rr_raw, str) else plan.get("rr_ratio", "N/A"),
                    "setup":      plan.get("setup_type", "—"),
                    "sector":     get_sector(symbol),
                    "verdict":    "entry",
                    # checklist-derived tags
                    "atr_pct":          tech.get("atr_pct", 0),
                    "near_52w_high":    tech.get("near_52w_high", False),
                    "dist_52w_pct":     tech.get("dist_52w_pct", 0),
                    "ema9_cross_ema21": tech.get("ema9_cross_ema21", "none"),
                    "high_52w":         tech.get("high_52w", 0),
                })
            except Exception as exc:
                logger.warning("[scan] %s skipped: %s", symbol, exc)

        result = {
            "status":        "success",
            "date":          _now_date(),
            "time":          _now_time(),
            "stocks":        scan_results,
            "filtered_out":  filtered_out,
            "actions":       _build_actions(scan_results),
            "total_scanned": len(chartink_stocks),
            "market":        {"nifty": nifty, "fii_dii": fii, "sentiment": _sentiment(nifty, fii)},
            "sectors":       sector_pulse,
        }
        _persist(result)
        logger.info("[scan] Done — %d setups", len(scan_results))
        return jsonify(result)

    except Exception as exc:
        logger.error("[scan] Failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/telegram/test")
def api_telegram_test():
    """Send a test Telegram message to verify credentials."""
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id or token == "your_bot_token_here":
        return jsonify({"status": "error", "message": "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set in .env"}), 400
    _tg_send("✅ <b>Swing Sentinel connected!</b>\nTelegram notifications are working.")
    return jsonify({"status": "ok", "message": "Test message sent"})


@app.route("/api/brief/latest")
def api_brief_latest():
    """Return today's saved scan result (fast, no external calls)."""
    today = _now_date()
    path  = os.path.join(_ROOT, "data", "daily_briefs", f"{today}.json")
    if os.path.exists(path):
        with open(path) as f:
            return jsonify({"found": True, "data": json.load(f)})
    return jsonify({"found": False})


def _append_rows_to_csv(path: str, rows: list) -> tuple:
    """
    Append rows to positions.csv preserving column alignment + skipping symbols
    that already have an OPEN position. Pandas auto-fills missing columns with
    NaN so the existing schema stays intact.

    Returns (added_rows, skipped_symbols) tuple.
    """
    import pandas as pd
    if not rows:
        return [], []

    existing  = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame()
    open_syms = set()
    if not existing.empty and "Status" in existing.columns:
        open_syms = set(
            existing.loc[existing["Status"].astype(str).str.upper() == "OPEN", "Symbol"]
                    .astype(str).tolist()
        )

    added, skipped = [], []
    for row in rows:
        if row.get("Symbol") in open_syms:
            skipped.append(row["Symbol"])
        else:
            added.append(row)
            open_syms.add(row["Symbol"])   # also dedupe within this single batch

    if added:
        new_df   = pd.DataFrame(added)
        combined = pd.concat([existing, new_df], ignore_index=True) if not existing.empty else new_df
        combined.to_csv(path, index=False)

    return added, skipped


@app.route("/api/positions/add", methods=["POST"])
def api_positions_add():
    """Add a new position from the Watchlist button on a stock card."""
    try:
        data = request.get_json(force=True, silent=True)
        if not data or not data.get("symbol"):
            return jsonify({"status": "error", "message": "symbol required"}), 400

        path = os.path.join(_ROOT, "data", "positions.csv")
        os.makedirs(os.path.join(_ROOT, "data"), exist_ok=True)

        row = {
            "Symbol":       data["symbol"],
            "Name":         data.get("name", data["symbol"]),
            "Entry_Price":  data.get("entry_price", 0),
            "Quantity":     data.get("quantity", 1),
            "Target_1":     data.get("target_1", 0),
            "Target_2":     data.get("target_2", 0),
            "Current_SL":   data.get("sl", 0),
            "Setup":        data.get("setup", ""),
            "Entry_Date":   _now_date(),
            "Status":       "OPEN",
        }
        added, skipped = _append_rows_to_csv(path, [row])
        if not added:
            logger.info("[positions] %s already on watchlist — skipped", row["Symbol"])
            return jsonify({
                "status":   "duplicate",
                "message":  f"{row['Symbol']} is already on your watchlist",
                "skipped":  skipped,
            }), 200

        # Place Kite GTT (silently skipped if not connected)
        gtt_id = place_gtt(
            symbol=row["Symbol"], qty=int(row["Quantity"]),
            last_price=float(row["Entry_Price"]),
            sl=float(row["Current_SL"]), target=float(row["Target_2"]),
        )
        if gtt_id:
            _write_gtt_id(path, row["Symbol"], gtt_id)
            row["gtt_id"] = gtt_id

        logger.info("[positions] Added %s @ %s  GTT=%s", row["Symbol"], row["Entry_Price"], gtt_id or "-")
        return jsonify({"status": "ok", "position": row})

    except Exception as exc:
        logger.error("[positions/add] %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/positions/add-all", methods=["POST"])
def api_positions_add_all():
    """Add all stocks from the current scan to watchlist (qty=1, entry=entry_min)."""
    try:
        data   = request.get_json(force=True, silent=True) or {}
        stocks = data.get("stocks", [])
        if not stocks:
            return jsonify({"status": "error", "message": "no stocks provided"}), 400

        path = os.path.join(_ROOT, "data", "positions.csv")
        os.makedirs(os.path.join(_ROOT, "data"), exist_ok=True)

        rows = []
        for s in stocks:
            if not s.get("symbol"):
                continue
            rows.append({
                "Symbol":       s["symbol"],
                "Name":         s.get("name", s["symbol"]),
                "Entry_Price":  s.get("entry_min", s.get("price", 0)),
                "Quantity":     1,
                "Target_1":     s.get("target_1", 0),
                "Target_2":     s.get("target_2", 0),
                "Current_SL":   s.get("sl", 0),
                "Setup":        s.get("setup", ""),
                "Entry_Date":   _now_date(),
                "Status":       "OPEN",
            })
        added, skipped = _append_rows_to_csv(path, rows)

        # Place Kite GTTs only for newly-added rows
        for row in added:
            gtt_id = place_gtt(
                symbol=row["Symbol"], qty=int(row["Quantity"]),
                last_price=float(row["Entry_Price"]),
                sl=float(row["Current_SL"]), target=float(row["Target_2"]),
            )
            if gtt_id:
                _write_gtt_id(path, row["Symbol"], gtt_id)

        logger.info("[positions] Bulk add: +%d new, %d skipped (already on watchlist)",
                    len(added), len(skipped))
        return jsonify({
            "status":  "ok",
            "added":   len(added),
            "skipped": len(skipped),
            "skipped_symbols": skipped,
        })

    except Exception as exc:
        logger.error("[positions/add-all] %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/positions")
def api_positions():
    """All positions with live P&L + target-hit status. Triggers Telegram alerts on first hit."""
    try:
        positions = check_positions_and_notify()
        return jsonify({"positions": positions})
    except Exception as exc:
        return jsonify({"positions": [], "error": str(exc)})


@app.route("/data/backtest_results.json")
def api_backtest():
    """Serve the JSON file produced by `python backtest.py`."""
    path = os.path.join(_ROOT, "data", "backtest_results.json")
    if not os.path.exists(path):
        return jsonify({"error": "Run `python backtest.py` to generate."}), 404
    return send_from_directory(os.path.join(_ROOT, "data"), "backtest_results.json")


@app.route("/api/results")
def api_results():
    """
    Strategy performance for EVERY watchlist entry (open + closed):
      - Closed trades drive realized win rate / avg P&L
      - Open trades drive live unrealized P&L + active counts
      - by_setup breaks down both
    """
    path = os.path.join(_ROOT, "data", "positions.csv")
    if not os.path.exists(path):
        return jsonify(_empty_results())
    try:
        import pandas as pd
        df = pd.read_csv(path)
        if df.empty:
            return jsonify(_empty_results())

        # Ensure all expected columns exist (read-only — won't write)
        for col in ("Outcome", "Entry_Hit_Date", "T1_Hit_Date", "T2_Hit_Date",
                    "SL_Hit_Date", "Closing_Price", "Setup", "Entry_Notified",
                    "T1_Notified", "T2_Notified", "SL_Notified"):
            if col not in df.columns:
                df[col] = ""

        total = len(df)
        closed_df = df[df["Status"].astype(str).str.upper() == "CLOSED"]
        open_df   = df[df["Status"].astype(str).str.upper() == "OPEN"]

        wins   = len(closed_df[closed_df["Outcome"] == "T2_WIN"])
        losses = len(closed_df[closed_df["Outcome"] == "SL_LOSS"])
        closed = len(closed_df)
        win_rate = round(wins / closed, 3) if closed else 0

        # Average days held for closed trades
        days_list = []
        for _, r in closed_df.iterrows():
            entry_date = str(r.get("Entry_Date", ""))
            exit_date  = str(r.get("T2_Hit_Date") or r.get("SL_Hit_Date") or "")
            if entry_date and exit_date:
                try:
                    d1 = datetime.strptime(entry_date, "%Y-%m-%d")
                    d2 = datetime.strptime(exit_date,  "%Y-%m-%d")
                    days_list.append((d2 - d1).days)
                except Exception:
                    pass
        avg_days_held = round(sum(days_list) / len(days_list), 1) if days_list else 0

        # ── Live prices for OPEN positions (one bulk yfinance call) ──
        open_symbols = open_df["Symbol"].astype(str).unique().tolist()
        price_map    = fetch_prices_bulk(open_symbols) if open_symbols else {}

        # Build active_positions with unrealized P&L
        today        = datetime.now().date()
        active_list  = []
        active_pnls  = []
        active_wins  = 0    # OPEN positions currently in profit
        active_loss  = 0    # OPEN positions currently in loss
        t1_hit_open  = 0    # OPEN positions that have hit T1 (riding for T2)
        entry_hit_open = 0  # OPEN positions where entry zone reached

        for _, r in open_df.iterrows():
            try:
                sym = str(r.get("Symbol", ""))
                ep  = float(r.get("Entry_Price", 0))
                t1  = float(r.get("Target_1", 0))
                t2  = float(r.get("Target_2", 0))
                sl  = float(r.get("Current_SL", 0))
                cp  = float(price_map.get(sym, 0) or 0)
                pnl_pct = round((cp - ep) / ep * 100, 2) if ep and cp else 0

                # Days held so far
                entry_date_str = str(r.get("Entry_Date", ""))
                days_held = 0
                if entry_date_str:
                    try:
                        days_held = (today - datetime.strptime(entry_date_str, "%Y-%m-%d").date()).days
                    except Exception:
                        days_held = 0

                # ── LIVE state derived from current price (independent of notifications) ──
                live_at_entry = bool(cp and ep and cp <= ep * 1.005)
                live_above_t1 = bool(cp and t1 and cp >= t1)
                live_above_t2 = bool(cp and t2 and cp >= t2)
                live_below_sl = bool(cp and sl and cp <= sl)

                # Pick a single label for the status badge (priority: T2 > T1 > SL > Entry > Waiting)
                if   live_above_t2: live_status = "T2_REACHED"
                elif live_above_t1: live_status = "T1_REACHED"
                elif live_below_sl: live_status = "BELOW_SL"
                elif live_at_entry: live_status = "AT_ENTRY"
                else:               live_status = "WAITING"

                # Notification flags (alerts already sent) — separate from live state
                t1_done    = _truthy(r.get("T1_Notified"))
                entry_done = _truthy(r.get("Entry_Notified"))

                if pnl_pct > 0: active_wins += 1
                elif pnl_pct < 0: active_loss += 1
                # Count LIVE T1/Entry (price-based, not notification-based) for the
                # summary cards — these reflect what's actually happening, not what
                # alerts have fired.
                if live_above_t1:  t1_hit_open    += 1
                if live_at_entry:  entry_hit_open += 1

                active_pnls.append(pnl_pct)
                active_list.append({
                    "symbol":      sym,
                    "name":        str(r.get("Name", sym)),
                    "setup":       str(r.get("Setup", "")),
                    "entry":       ep,
                    "current":     cp,
                    "target_1":    t1,
                    "target_2":    t2,
                    "sl":          sl,
                    "pnl_pct":     pnl_pct,
                    "days_held":   days_held,
                    # Live state (current price vs target) — for visual badge
                    "live_status": live_status,
                    "above_t1":    live_above_t1,
                    "above_t2":    live_above_t2,
                    "at_entry":    live_at_entry,
                    "below_sl":    live_below_sl,
                    # Notification state (have alerts fired) — for "did Telegram fire" logic
                    "t1_notified":    t1_done,
                    "entry_notified": entry_done,
                    "entry_date":  entry_date_str,
                })
            except Exception:
                continue

        active_list.sort(key=lambda x: x["pnl_pct"], reverse=True)
        avg_unrealized = round(sum(active_pnls) / len(active_pnls), 2) if active_pnls else 0

        # Per-setup breakdown — now includes BOTH closed + open
        by_setup = {}
        for setup in df["Setup"].dropna().astype(str).unique():
            if not setup:
                continue
            grp        = df[df["Setup"] == setup]
            grp_closed = grp[grp["Status"].astype(str).str.upper() == "CLOSED"]
            grp_open   = grp[grp["Status"].astype(str).str.upper() == "OPEN"]
            grp_wins   = len(grp_closed[grp_closed["Outcome"] == "T2_WIN"])
            grp_loss   = len(grp_closed[grp_closed["Outcome"] == "SL_LOSS"])
            grp_total  = len(grp_closed)

            # Realized P&L (closed)
            pnls = []
            for _, r in grp_closed.iterrows():
                try:
                    ep = float(r.get("Entry_Price", 0))
                    cp = float(r.get("Closing_Price", 0))
                    if ep and cp:
                        pnls.append((cp - ep) / ep * 100)
                except Exception:
                    pass

            # Unrealized P&L (open) — uses live prices
            unr_pnls = []
            for _, r in grp_open.iterrows():
                try:
                    sym = str(r.get("Symbol", ""))
                    ep  = float(r.get("Entry_Price", 0))
                    cp  = float(price_map.get(sym, 0) or 0)
                    if ep and cp:
                        unr_pnls.append((cp - ep) / ep * 100)
                except Exception:
                    pass

            by_setup[setup] = {
                "total":            int(len(grp)),
                "open":             int(len(grp_open)),
                "closed":           int(grp_total),
                "wins":             int(grp_wins),
                "losses":           int(grp_loss),
                "win_rate":         round(grp_wins / grp_total, 3) if grp_total else 0,
                "avg_pnl_pct":      round(sum(pnls) / len(pnls), 2) if pnls else 0,
                "avg_unrealized":   round(sum(unr_pnls) / len(unr_pnls), 2) if unr_pnls else 0,
            }

        # Closed positions list (most recent first)
        closed_list = []
        for _, r in closed_df.iterrows():
            try:
                ep   = float(r.get("Entry_Price", 0))
                cp   = float(r.get("Closing_Price", 0))
                pnl_pct = round((cp - ep) / ep * 100, 2) if ep and cp else 0
                exit_date = str(r.get("T2_Hit_Date") or r.get("SL_Hit_Date") or "")
                days_held = 0
                if str(r.get("Entry_Date", "")) and exit_date:
                    try:
                        days_held = (datetime.strptime(exit_date, "%Y-%m-%d")
                                     - datetime.strptime(str(r["Entry_Date"]), "%Y-%m-%d")).days
                    except Exception:
                        days_held = 0
                closed_list.append({
                    "symbol":    str(r.get("Symbol", "")),
                    "name":      str(r.get("Name", "")),
                    "setup":     str(r.get("Setup", "")),
                    "entry":     ep,
                    "exit":      cp,
                    "pnl_pct":   pnl_pct,
                    "days_held": days_held,
                    "outcome":   str(r.get("Outcome", "")),
                    "entry_date": str(r.get("Entry_Date", "")),
                    "exit_date":  exit_date,
                })
            except Exception:
                continue
        # Sort by exit_date desc
        closed_list.sort(key=lambda x: x.get("exit_date", ""), reverse=True)

        return jsonify({
            "total":            total,
            "open":             len(open_df),
            "closed":           closed,
            "wins":             wins,
            "losses":           losses,
            "win_rate":         win_rate,
            "avg_days_held":    avg_days_held,
            "by_setup":         by_setup,
            "closed_positions": closed_list,
            # ── Live data for OPEN positions ──────────────────────────────
            "active_count":       len(open_df),
            "active_in_profit":   active_wins,
            "active_in_loss":     active_loss,
            "active_entry_hit":   entry_hit_open,
            "active_t1_hit":      t1_hit_open,
            "avg_unrealized_pct": avg_unrealized,
            "active_positions":   active_list,
        })
    except Exception as exc:
        logger.error("[results] %s", exc)
        return jsonify({**_empty_results(), "error": str(exc)})


# ── TradingView proxy ────────────────────────────────────────────────────────
_TV_BASE    = "https://www.tradingview.com"
_TV_SCANNER = "https://scanner.tradingview.com"

def _tv_h(extra: dict | None = None) -> dict:
    h = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Origin":  _TV_BASE,
        "Referer": _TV_BASE + "/",
    }
    sess = os.getenv("TV_SESSION", "").strip()
    if sess:
        h["Cookie"] = f"sessionid={sess}"
    if extra:
        h.update(extra)
    return h


def _tv_csrf() -> str:
    try:
        r = _requests.get(_TV_BASE + "/", headers=_tv_h(), timeout=10)
        for k, v in r.cookies.items():
            if "csrf" in k.lower():
                return v
    except Exception:
        pass
    return ""


@app.route("/api/tv/alerts")
def api_tv_alerts():
    """List active TradingView alerts for the authenticated user."""
    if not os.getenv("TV_SESSION", "").strip():
        return jsonify({"error": "TV_SESSION not set in .env"}), 401
    try:
        r = _requests.get(
            f"{_TV_BASE}/api/v1/alerts/",
            headers=_tv_h({"Accept": "application/json"}),
            timeout=15,
        )
        r.raise_for_status()
        return jsonify(r.json())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/tv/alert/<int:alert_id>", methods=["DELETE"])
def api_tv_delete_alert(alert_id):
    """Delete a TradingView alert by ID."""
    if not os.getenv("TV_SESSION", "").strip():
        return jsonify({"error": "TV_SESSION not set in .env"}), 401
    csrf = _tv_csrf()
    try:
        r = _requests.delete(
            f"{_TV_BASE}/api/v1/alerts/{alert_id}/",
            headers=_tv_h({"X-CSRFToken": csrf, "Referer": f"{_TV_BASE}/chart/"}),
            timeout=15,
        )
        r.raise_for_status()
        return jsonify({"status": "deleted", "alert_id": alert_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/tv/watchlist", methods=["GET", "POST"])
def api_tv_watchlist():
    """GET: list TV watchlists. POST {symbol}: add symbol to default watchlist."""
    if not os.getenv("TV_SESSION", "").strip():
        return jsonify({"error": "TV_SESSION not set in .env"}), 401

    if request.method == "GET":
        try:
            r = _requests.get(
                f"{_TV_BASE}/api/v1/symbols_list/custom/",
                headers=_tv_h({"Accept": "application/json"}),
                timeout=15,
            )
            r.raise_for_status()
            return jsonify(r.json())
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # POST — add a symbol
    body   = request.get_json(force=True, silent=True) or {}
    symbol = body.get("symbol", "").strip().upper()
    wl_id  = body.get("watchlist_id", "")
    if not symbol:
        return jsonify({"error": "symbol required"}), 400
    try:
        if not wl_id:
            r = _requests.get(
                f"{_TV_BASE}/api/v1/symbols_list/custom/",
                headers=_tv_h({"Accept": "application/json"}),
                timeout=15,
            )
            r.raise_for_status()
            lists = r.json()
            arr = lists if isinstance(lists, list) else lists.get("data", lists.get("results", []))
            if not arr:
                return jsonify({"error": "No watchlists found. Create one on TradingView first."}), 404
            wl_id = str(arr[0].get("id", ""))

        csrf = _tv_csrf()
        r = _requests.get(
            f"{_TV_BASE}/api/v1/symbols_list/custom/{wl_id}/",
            headers=_tv_h({"Accept": "application/json"}),
            timeout=15,
        )
        r.raise_for_status()
        current = r.json()
        syms = current.get("symbols", []) if isinstance(current, dict) else []
        if symbol in syms:
            return jsonify({"status": "already_in_watchlist", "symbol": symbol})
        post_r = _requests.post(
            f"{_TV_BASE}/api/v1/symbols_list/custom/{wl_id}/append/",
            json=[symbol],
            headers=_tv_h({
                "Content-Type":     "application/json",
                "X-CSRFToken":      csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Referer":          f"{_TV_BASE}/chart/",
            }),
            timeout=15,
        )
        post_r.raise_for_status()
        return jsonify({"status": "added", "symbol": symbol, "watchlist_id": wl_id, "total": len(syms) + 1})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/tv/screener", methods=["POST"])
def api_tv_screener():
    """Run TradingView screener with conditions. No auth required."""
    body   = request.get_json(force=True, silent=True) or {}
    market = body.get("market", "india")
    conds  = body.get("conditions", [])
    limit  = min(int(body.get("limit", 50)), 200)

    _mkts = {
        "india": "india", "nse": "india", "bse": "india",
        "us": "america", "usa": "america",
    }
    _ops = {
        "above": "greater", ">": "greater",
        "below": "less",    "<": "less",
        "equal": "equal",   "between": "in_range",
        "cross_above": "crosses_above", "cross_below": "crosses_below",
    }
    tv_filters = [
        {"left": c.get("indicator", ""), "operation": _ops.get(str(c.get("op", "above")).lower(), "greater"), "right": c.get("value")}
        for c in conds
    ]
    columns = ["name", "description", "close", "volume", "change", "RSI", "EMA20", "EMA50", "EMA200", "market_cap_basic", "sector"]
    payload = {
        "filter":  tv_filters,
        "columns": columns,
        "sort":    {"sortBy": "volume", "sortOrder": "desc"},
        "range":   [0, limit],
    }
    try:
        r = _requests.post(
            f"{_TV_SCANNER}/{_mkts.get(market.lower(), 'india')}/scan",
            json=payload,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Origin":  _TV_BASE,
                "Referer": _TV_BASE + "/",
            },
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        rows = []
        for item in data.get("data", []):
            row = {"symbol": item.get("s", "")}
            for i, col in enumerate(columns):
                row[col] = item["d"][i] if i < len(item.get("d", [])) else None
            rows.append(row)
        return jsonify({"total": data.get("totalCount", len(rows)), "results": rows})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _empty_results():
    return {
        "total": 0, "open": 0, "closed": 0,
        "wins": 0, "losses": 0, "win_rate": 0, "avg_days_held": 0,
        "by_setup": {}, "closed_positions": [],
        "active_count": 0, "active_in_profit": 0, "active_in_loss": 0,
        "active_entry_hit": 0, "active_t1_hit": 0,
        "avg_unrealized_pct": 0, "active_positions": [],
    }


def check_positions_and_notify() -> list:
    """
    Core watchlist-monitor: reads positions.csv, fetches live prices, detects
    Entry / T1 / T2 / SL crossings, fires Telegram alert on first crossing,
    and persists notification state back to CSV. Returns enriched positions list.

    Called by:
      - GET /api/positions (on-demand from dashboard)
      - Background scheduler (every minute during market hours)
    """
    path = os.path.join(_ROOT, "data", "positions.csv")
    if not os.path.exists(path):
        return []

    import pandas as pd
    df = pd.read_csv(path)
    if df.empty:
        return []

    # Ensure all notification-state + outcome columns exist with correct dtypes
    for col in ("Entry_Notified", "T1_Notified", "T2_Notified", "SL_Notified"):
        if col not in df.columns:
            df[col] = False
    # Date / outcome columns — object dtype so they accept strings or floats
    for col in ("Entry_Hit_Date", "T1_Hit_Date", "T2_Hit_Date", "SL_Hit_Date", "Outcome"):
        if col not in df.columns:
            df[col] = pd.Series([""] * len(df), dtype=object)
    if "Closing_Price" not in df.columns:
        df["Closing_Price"] = 0.0   # float dtype — will hold the actual exit price

    today_str = _now_date()
    csv_dirty = False
    positions = []
    # Buffer alerts and only send them AFTER the CSV write succeeds — otherwise
    # a locked/unwritable positions.csv (Excel, OneDrive sync) would let Telegram
    # fire every poll cycle while the notified-flag never persists.
    pending_alerts: list[str] = []

    # ── Bulk-fetch live prices for all OPEN positions (one yfinance call) ──
    open_symbols = (
        df.loc[df["Status"].astype(str).str.upper() == "OPEN", "Symbol"]
          .astype(str).unique().tolist()
    )
    price_map = fetch_prices_bulk(open_symbols) if open_symbols else {}

    for idx, row in df.iterrows():
        pos    = row.to_dict()
        # Convert NaN to None/empty string for JSON serialization
        for key, val in pos.items():
            try:
                if pd.isna(val):
                    pos[key] = ""
            except (TypeError, ValueError):
                pass
        status = str(pos.get("Status", "OPEN")).upper()

        # Skip closed positions — don't waste yfinance calls or fire stale alerts
        if status != "OPEN":
            pos["current_price"] = 0
            pos["pnl"]           = 0
            pos["pnl_pct"]       = 0
            pos["entry_hit"]     = False
            pos["t1_hit"]        = False
            pos["t2_hit"]        = False
            pos["sl_hit"]        = False
            positions.append(pos)
            continue

        sym  = str(pos.get("Symbol", "?"))
        name = pos.get("Name", sym)
        cur  = float(price_map.get(sym, 0) or 0)   # bulk fetch — 11x faster than per-row

        ep  = float(pos.get("Entry_Price", 0))
        qty = float(pos.get("Quantity", 0))
        t1  = float(pos.get("Target_1", 0))
        t2  = float(pos.get("Target_2", 0))
        sl  = float(pos.get("Current_SL", 0))

        # Crossing detection
        entry_hit = bool(cur and ep and cur <= ep * 1.005)   # price in/below entry zone
        t1_hit    = bool(cur and t1 and cur >= t1)
        t2_hit    = bool(cur and t2 and cur >= t2)
        sl_hit    = bool(cur and sl and cur <= sl)

        pos["current_price"] = cur
        pos["pnl"]           = round((cur - ep) * qty, 2) if cur else 0
        pos["pnl_pct"]       = round(((cur - ep) / ep * 100) if ep else 0, 2)
        pos["entry_hit"]     = entry_hit
        pos["t1_hit"]        = t1_hit
        pos["t2_hit"]        = t2_hit
        pos["sl_hit"]        = sl_hit
        pct                  = round(((cur - ep) / ep * 100) if ep else 0, 1)

        # ── Entry alert always fires independently ───────────────────────
        entry_done = _truthy(pos.get("Entry_Notified"))
        if entry_hit and not entry_done:
            pending_alerts.append(
                f"🎯 <b>ENTRY READY — {sym}</b>\n"
                f"{name}\n"
                f"Now ₹{cur:.2f} (entry zone ≈ ₹{ep:.2f})\n"
                f"Time to consider opening the position."
            )
            df.at[idx, "Entry_Notified"] = True
            df.at[idx, "Entry_Hit_Date"] = today_str
            entry_done = True   # so T1/T2/SL gate sees it within this same iteration
            csv_dirty = True

        # ── T1/T2/SL alerts are GATED behind Entry ───────────────────────
        # If the stock never pulled back into the entry zone, we never "entered"
        # the watchlist position, so target/SL alerts would be misleading
        # ("Book profits on what?"). Skip them until Entry fires first.
        if not entry_done:
            positions.append(pos)
            continue

        if t1_hit and not _truthy(pos.get("T1_Notified")):
            pending_alerts.append(
                f"🟡 <b>T1 HIT — {sym}</b>\n"
                f"{name}\n"
                f"Entry ₹{ep:.2f} → Now ₹{cur:.2f} (+{pct}%)\n"
                f"Target 1 was ₹{t1:.2f} ✅\n"
                f"Consider booking partial profits."
            )
            df.at[idx, "T1_Notified"]  = True
            df.at[idx, "T1_Hit_Date"]  = today_str
            if not str(pos.get("Outcome", "")).strip():
                df.at[idx, "Outcome"] = "T1_HIT"
            csv_dirty = True

        if t2_hit and not _truthy(pos.get("T2_Notified")):
            pending_alerts.append(
                f"🟢 <b>T2 HIT — {sym}</b>\n"
                f"{name}\n"
                f"Entry ₹{ep:.2f} → Now ₹{cur:.2f} (+{pct}%)\n"
                f"Target 2 was ₹{t2:.2f} ✅✅\n"
                f"Full target reached!"
            )
            df.at[idx, "T2_Notified"]   = True
            df.at[idx, "T2_Hit_Date"]   = today_str
            df.at[idx, "Closing_Price"] = cur
            df.at[idx, "Outcome"]       = "T2_WIN"
            df.at[idx, "Status"]        = "CLOSED"
            csv_dirty = True

        if sl_hit and not _truthy(pos.get("SL_Notified")):
            pending_alerts.append(
                f"🔴 <b>SL HIT — {sym}</b>\n"
                f"{name}\n"
                f"Entry ₹{ep:.2f} → Now ₹{cur:.2f} ({pct:+}%)\n"
                f"Stop loss was ₹{sl:.2f} ⛔\n"
                f"Position invalidated — exit."
            )
            df.at[idx, "SL_Notified"]   = True
            df.at[idx, "SL_Hit_Date"]   = today_str
            df.at[idx, "Closing_Price"] = cur
            df.at[idx, "Outcome"]       = "SL_LOSS"
            df.at[idx, "Status"]        = "CLOSED"
            csv_dirty = True

        positions.append(pos)

    # Persist state BEFORE firing Telegram. If the write raises (e.g., the file
    # is locked by Excel or OneDrive), the exception propagates, no alerts go
    # out, and the next poll retries — preventing the per-minute alert spam.
    if csv_dirty:
        tmp_path = f"{path}.tmp"
        df.to_csv(tmp_path, index=False)
        os.replace(tmp_path, path)

    for msg in pending_alerts:
        _tg_send(msg)

    return positions


# ── Helpers ─────────────────────────────────────────────────────────────────

def _truthy(val) -> bool:
    """CSV booleans come back as strings 'True'/'False' — handle both."""
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("true", "1", "yes")


def _sentiment(nifty: dict, fii: dict) -> str:
    fii_net = sum(fii.get("fii_last_5_days", [0] * 5))
    if fii_net > 5000 and nifty.get("change_pct", 0) > 0:
        return "🟢 Bullish"
    if fii_net < -5000:
        return "🔴 Cautious"
    return "⚪ Neutral"


def _rr_num(rr) -> float:
    try:
        return float(str(rr).split(":")[-1])
    except Exception:
        return 0.0


def _build_actions(results: list) -> list:
    if not results:
        return []
    actions = []
    used: set = set()

    best = max(results, key=lambda x: _rr_num(x["rr"]))
    actions.append({
        "priority": "P1", "symbol": best["symbol"],
        "action": (f"Place GTT for {best['symbol']} @ ₹{best['entry_min']:.0f}–{best['entry_max']:.0f}"
                   f" → R:R 1:{_rr_num(best['rr']):.1f}. SL ₹{best['sl']:.0f}."),
    })
    used.add(best["symbol"])

    rest = [r for r in results if r["symbol"] not in used]
    if rest:
        near = min(rest, key=lambda x: abs(x["price"] - x["entry_min"]))
        actions.append({
            "priority": "P2", "symbol": near["symbol"],
            "action": (f"Watch {near['symbol']} @ ₹{near['price']:.0f}"
                       f" — entry zone ₹{near['entry_min']:.0f}–{near['entry_max']:.0f}."),
        })
        used.add(near["symbol"])

    rest2 = [r for r in results if r["symbol"] not in used]
    if rest2:
        top = max(rest2, key=lambda x: (x["target_2"] - x["price"]) / x["price"] if x["price"] else 0)
        up  = ((top["target_2"] - top["price"]) / top["price"] * 100) if top["price"] else 0
        actions.append({
            "priority": "P3", "symbol": top["symbol"],
            "action": (f"Best upside: {top['symbol']} → T2 ₹{top['target_2']:.0f} ({up:+.1f}%)."
                       f" Entry ₹{top['entry_min']:.0f}–{top['entry_max']:.0f}."),
        })

    return actions[:3]


def _kite_login_url() -> str:
    try:
        from kiteconnect import KiteConnect
        api_key = os.getenv("KITE_API_KEY", "").strip()
        if not api_key or api_key == "your_api_key_here":
            return ""
        return KiteConnect(api_key=api_key).login_url()
    except Exception:
        return ""


def _save_env_token(token: str):
    """Write/update KITE_ACCESS_TOKEN in .env and in the current process env."""
    env_path = ".env"
    lines    = open(env_path).readlines() if os.path.exists(env_path) else []
    found    = False
    new_lines = []
    for line in lines:
        if line.startswith("KITE_ACCESS_TOKEN="):
            new_lines.append(f"KITE_ACCESS_TOKEN={token}\n")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"KITE_ACCESS_TOKEN={token}\n")
    with open(env_path, "w") as f:
        f.writelines(new_lines)
    os.environ["KITE_ACCESS_TOKEN"] = token


def _write_gtt_id(csv_path: str, symbol: str, gtt_id: int):
    """Update the GTT_Id cell for the last row matching symbol in positions CSV."""
    try:
        import pandas as pd
        df = pd.read_csv(csv_path)
        if "GTT_Id" not in df.columns:
            df["GTT_Id"] = ""
        mask = df["Symbol"] == symbol
        if mask.any():
            last_idx = df[mask].index[-1]
            df.at[last_idx, "GTT_Id"] = gtt_id
            df.to_csv(csv_path, index=False)
    except Exception as exc:
        logger.warning("[kite] could not write GTT_Id for %s: %s", symbol, exc)


def _persist(result: dict):
    _briefs = os.path.join(_ROOT, "data", "daily_briefs")
    os.makedirs(_briefs, exist_ok=True)
    with open(os.path.join(_briefs, f"{result['date']}.json"), "w") as f:
        json.dump(result, f, indent=2)


def _load_latest_brief():
    folder = os.path.join(_ROOT, "data", "daily_briefs")
    if not os.path.exists(folder):
        return None
    files = sorted([f for f in os.listdir(folder) if f.endswith(".json")], reverse=True)
    for fname in files:
        try:
            with open(os.path.join(folder, fname)) as fh:
                return json.load(fh)
        except Exception:
            continue
    return None


def _now_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now_time() -> str:
    return datetime.now().strftime("%H:%M")


# ── Background poller ───────────────────────────────────────────────────────

def _poll_job():
    """Cron job: check open positions every minute, fire alerts on threshold crossings."""
    try:
        positions = check_positions_and_notify()
        open_count = sum(1 for p in positions if str(p.get("Status", "OPEN")).upper() == "OPEN")
        logger.info("[poll] checked %d open positions", open_count)
    except Exception as exc:
        logger.error("[poll] error: %s", exc)


def _start_scheduler():
    """Start a background scheduler that polls positions every minute 9:15–15:30 IST Mon-Fri."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        import pytz
    except ImportError:
        logger.warning("[poll] apscheduler/pytz not installed — alerts only fire when dashboard is open")
        return

    ist = pytz.timezone("Asia/Kolkata")
    sched = BackgroundScheduler(timezone=ist)
    # NSE market hours: 9:15 AM – 3:30 PM IST, Mon–Fri
    sched.add_job(
        _poll_job,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*", timezone=ist),
        id="position_poller",
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    logger.info("[poll] background scheduler started — polling every minute, Mon-Fri 9:15-15:30 IST")


# ── Entry ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== Swing Sentinel - Local Server ===")
    print("  Dashboard : http://localhost:5000")
    print("  Checklist : http://localhost:5000/checklist")
    print("  Scan API  : POST /api/scan")
    print("  Market    : GET  /api/market")
    print("  Positions : GET  /api/positions")
    print("  TV Alerts : GET  /api/tv/alerts")
    print("  TV Watch  : GET  /api/tv/watchlist")
    print("  Poller    : every 1 min during market hours")
    print("=" * 38 + "\n")
    _start_scheduler()
    app.run(debug=False, port=5000, host="0.0.0.0", use_reloader=False)
