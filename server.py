"""
Swing Sentinel — Local Server
Run : python server.py
Open: http://localhost:5000
"""
import csv
import json
import logging
import os
from datetime import datetime

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

app = Flask(__name__, static_folder="dashboard")

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

# ── Kite helper ──────────────────────────────────────────────────────────────
try:
    from core.kite import get_kite, place_gtt
except ImportError:
    try:
        from core_kite import get_kite, place_gtt
    except ImportError:
        def get_kite():   return None
        def place_gtt(*a, **kw): return None

# ── Import helpers (flat dev layout OR deployed core/ folder) ──────────────
try:
    from core.chartink_fetcher import fetch_chartink_stocks
    from core.data_fetcher import (
        fetch_fii_dii_flow,
        fetch_nifty_levels,
        fetch_stock_technicals,
    )
    from core.trade_plan import calculate_rr, calculate_trade_plan
except ImportError:
    from core_chartink_fetcher import fetch_chartink_stocks
    from core_data_fetcher import (
        fetch_fii_dii_flow,
        fetch_nifty_levels,
        fetch_stock_technicals,
    )
    from core_trade_plan import calculate_rr, calculate_trade_plan


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("dashboard", "swing_agent_app.html")


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


@app.route("/api/scan", methods=["POST"])
def api_scan():
    """
    Full scan: Chartink (works intraday and EOD) → yfinance trade plans.
    If Chartink returns 0 matches, falls back to the last saved scan.
    """
    try:
        logger.info("[scan] Starting…")

        nifty = fetch_nifty_levels()
        fii   = fetch_fii_dii_flow(days=5)

        chartink_stocks = fetch_chartink_stocks()

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

        # Sort by volume (descending) and take top 30 to keep yfinance calls fast
        chartink_stocks.sort(key=lambda x: x.get("volume", 0), reverse=True)
        chartink_stocks = chartink_stocks[:30]
        logger.info("[scan] Processing top %d stocks via yfinance…", len(chartink_stocks))

        scan_results = []
        for i, stock in enumerate(chartink_stocks, 1):
            symbol = stock["symbol"]
            logger.info("[scan] %d/%d  %s", i, len(chartink_stocks), symbol)
            try:
                tech = fetch_stock_technicals(symbol)
                if not tech:
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
                    "verdict":    "entry",
                })
            except Exception as exc:
                logger.warning("[scan] %s skipped: %s", symbol, exc)

        result = {
            "status":        "success",
            "date":          _now_date(),
            "time":          _now_time(),
            "stocks":        scan_results,
            "actions":       _build_actions(scan_results),
            "total_scanned": len(chartink_stocks),
            "market":        {"nifty": nifty, "fii_dii": fii, "sentiment": _sentiment(nifty, fii)},
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
    path  = f"data/daily_briefs/{today}.json"
    if os.path.exists(path):
        with open(path) as f:
            return jsonify({"found": True, "data": json.load(f)})
    return jsonify({"found": False})


@app.route("/api/positions/add", methods=["POST"])
def api_positions_add():
    """Add a new position from the Buy button on a stock card."""
    try:
        data = request.get_json(force=True, silent=True)
        if not data or not data.get("symbol"):
            return jsonify({"status": "error", "message": "symbol required"}), 400

        path    = "data/positions.csv"
        os.makedirs("data", exist_ok=True)
        is_new  = not os.path.exists(path)

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
            "GTT_Id":       "",
        }

        with open(path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            if is_new:
                writer.writeheader()
            writer.writerow(row)

        # Place Kite GTT (SL + T2 two-leg OCO) — silently skipped if not connected
        gtt_id = place_gtt(
            symbol=row["Symbol"],
            qty=int(row["Quantity"]),
            last_price=float(row["Entry_Price"]),
            sl=float(row["Current_SL"]),
            target=float(row["Target_2"]),
        )
        if gtt_id:
            _write_gtt_id(path, row["Symbol"], gtt_id)
            row["gtt_id"] = gtt_id

        logger.info("[positions] Added %s @ %s  GTT=%s", row["Symbol"], row["Entry_Price"], gtt_id or "—")
        return jsonify({"status": "ok", "position": row})

    except Exception as exc:
        logger.error("[positions/add] %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/positions/add-all", methods=["POST"])
def api_positions_add_all():
    """Add all stocks from the current scan to positions (qty=1, entry=entry_min)."""
    try:
        data   = request.get_json(force=True, silent=True) or {}
        stocks = data.get("stocks", [])
        if not stocks:
            return jsonify({"status": "error", "message": "no stocks provided"}), 400

        path   = "data/positions.csv"
        os.makedirs("data", exist_ok=True)
        is_new = not os.path.exists(path)

        fields = ["Symbol","Name","Entry_Price","Quantity","Target_1","Target_2","Current_SL","Setup","Entry_Date","Status","GTT_Id"]
        added  = 0
        rows_added = []
        with open(path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            if is_new:
                writer.writeheader()
            for s in stocks:
                if not s.get("symbol"):
                    continue
                row = {
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
                    "GTT_Id":       "",
                }
                writer.writerow(row)
                rows_added.append(row)
                added += 1

        # Place Kite GTTs after CSV write
        for row in rows_added:
            gtt_id = place_gtt(
                symbol=row["Symbol"],
                qty=int(row["Quantity"]),
                last_price=float(row["Entry_Price"]),
                sl=float(row["Current_SL"]),
                target=float(row["Target_2"]),
            )
            if gtt_id:
                _write_gtt_id(path, row["Symbol"], gtt_id)

        logger.info("[positions] Bulk added %d stocks", added)
        return jsonify({"status": "ok", "added": added})

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
    path = "data/backtest_results.json"
    if not os.path.exists(path):
        return jsonify({"error": "Run `python backtest.py` to generate."}), 404
    return send_from_directory("data", "backtest_results.json")


@app.route("/api/results")
def api_results():
    """Aggregate strategy performance: win rate, per-setup breakdown, closed positions."""
    path = "data/positions.csv"
    if not os.path.exists(path):
        return jsonify(_empty_results())
    try:
        import pandas as pd
        df = pd.read_csv(path)
        if df.empty:
            return jsonify(_empty_results())

        # Ensure outcome columns exist (read-only — won't write)
        for col in ("Outcome", "Entry_Hit_Date", "T1_Hit_Date", "T2_Hit_Date",
                    "SL_Hit_Date", "Closing_Price", "Setup"):
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

        # Per-setup breakdown
        by_setup = {}
        for setup in df["Setup"].dropna().astype(str).unique():
            if not setup:
                continue
            grp        = df[df["Setup"] == setup]
            grp_closed = grp[grp["Status"].astype(str).str.upper() == "CLOSED"]
            grp_wins   = len(grp_closed[grp_closed["Outcome"] == "T2_WIN"])
            grp_loss   = len(grp_closed[grp_closed["Outcome"] == "SL_LOSS"])
            grp_total  = len(grp_closed)

            # Average % P&L for closed trades in this setup
            pnls = []
            for _, r in grp_closed.iterrows():
                try:
                    ep = float(r.get("Entry_Price", 0))
                    cp = float(r.get("Closing_Price", 0))
                    if ep and cp:
                        pnls.append((cp - ep) / ep * 100)
                except Exception:
                    pass

            by_setup[setup] = {
                "total":       int(len(grp)),
                "closed":      int(grp_total),
                "wins":        int(grp_wins),
                "losses":      int(grp_loss),
                "win_rate":    round(grp_wins / grp_total, 3) if grp_total else 0,
                "avg_pnl_pct": round(sum(pnls) / len(pnls), 2) if pnls else 0,
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
        })
    except Exception as exc:
        logger.error("[results] %s", exc)
        return jsonify({**_empty_results(), "error": str(exc)})


def _empty_results():
    return {
        "total": 0, "open": 0, "closed": 0,
        "wins": 0, "losses": 0, "win_rate": 0, "avg_days_held": 0,
        "by_setup": {}, "closed_positions": [],
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
    path = "data/positions.csv"
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

    for idx, row in df.iterrows():
        pos    = row.to_dict()
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

        try:
            tech = fetch_stock_technicals(sym)
            cur  = tech.get("price", 0) if tech else 0
        except Exception:
            cur = 0

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

        # Send Telegram once per crossing — also record hit date + lock outcome
        if entry_hit and not _truthy(pos.get("Entry_Notified")):
            _tg_send(
                f"🎯 <b>ENTRY READY — {sym}</b>\n"
                f"{name}\n"
                f"Now ₹{cur:.2f} (entry zone ≈ ₹{ep:.2f})\n"
                f"Time to consider opening the position."
            )
            df.at[idx, "Entry_Notified"] = True
            df.at[idx, "Entry_Hit_Date"] = today_str
            csv_dirty = True

        if t1_hit and not _truthy(pos.get("T1_Notified")):
            _tg_send(
                f"🟡 <b>T1 HIT — {sym}</b>\n"
                f"{name}\n"
                f"Entry ₹{ep:.2f} → Now ₹{cur:.2f} (+{pct}%)\n"
                f"Target 1 was ₹{t1:.2f} ✅\n"
                f"Consider booking partial profits."
            )
            df.at[idx, "T1_Notified"]  = True
            df.at[idx, "T1_Hit_Date"]  = today_str
            # Don't close on T1 — user typically books partial and lets T2 ride.
            # Mark intermediate outcome so dashboard can show "T1 booked, T2 pending".
            if not str(pos.get("Outcome", "")).strip():
                df.at[idx, "Outcome"] = "T1_HIT"
            csv_dirty = True

        if t2_hit and not _truthy(pos.get("T2_Notified")):
            _tg_send(
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
            _tg_send(
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

    if csv_dirty:
        df.to_csv(path, index=False)

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
    os.makedirs("data/daily_briefs", exist_ok=True)
    with open(f"data/daily_briefs/{result['date']}.json", "w") as f:
        json.dump(result, f, indent=2)


def _load_latest_brief():
    folder = "data/daily_briefs"
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
    print("  Scan API  : POST /api/scan")
    print("  Market    : GET  /api/market")
    print("  Positions : GET  /api/positions")
    print("  Poller    : every 1 min during market hours")
    print("=" * 38 + "\n")
    _start_scheduler()
    app.run(debug=False, port=5000, host="0.0.0.0", use_reloader=False)
