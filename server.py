"""
Swing Sentinel — Local Server
Run : python server.py
Open: http://localhost:5000
"""
import json
import logging
import os
from datetime import datetime

from flask import Flask, jsonify, send_from_directory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="dashboard")

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
    Full scan: Chartink → yfinance trade plans.
    Blocking — may take 30–90 s depending on number of matches.
    """
    try:
        logger.info("[scan] Starting…")

        nifty = fetch_nifty_levels()
        fii   = fetch_fii_dii_flow(days=5)

        chartink_stocks = fetch_chartink_stocks()

        if not chartink_stocks:
            result = {
                "status":        "no_results",
                "message":       "Chartink returned 0 matches — market may be closed or no setups today.",
                "date":          _now_date(),
                "time":          _now_time(),
                "stocks":        [],
                "actions":       [],
                "total_scanned": 0,
                "market":        {"nifty": nifty, "fii_dii": fii, "sentiment": _sentiment(nifty, fii)},
            }
            _persist(result)
            return jsonify(result)

        scan_results = []
        for stock in chartink_stocks:
            symbol = stock["symbol"]
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


@app.route("/api/brief/latest")
def api_brief_latest():
    """Return today's saved scan result (fast, no external calls)."""
    today = _now_date()
    path  = f"data/daily_briefs/{today}.json"
    if os.path.exists(path):
        with open(path) as f:
            return jsonify({"found": True, "data": json.load(f)})
    return jsonify({"found": False})


@app.route("/api/positions")
def api_positions():
    """Open positions with live P&L (fetches current price per symbol)."""
    path = "data/positions.csv"
    if not os.path.exists(path):
        return jsonify({"positions": []})
    try:
        import pandas as pd
        df = pd.read_csv(path)
        if df.empty:
            return jsonify({"positions": []})
        positions = []
        for _, row in df.iterrows():
            pos = row.to_dict()
            try:
                tech = fetch_stock_technicals(str(pos.get("Symbol", "")))
                cur  = tech.get("price", 0) if tech else 0
            except Exception:
                cur = 0
            ep = float(pos.get("Entry_Price", 0))
            qty = float(pos.get("Quantity", 0))
            pos["current_price"] = cur
            pos["pnl"]           = round((cur - ep) * qty, 2) if cur else 0
            pos["pnl_pct"]       = round(((cur - ep) / ep * 100) if ep else 0, 2)
            positions.append(pos)
        return jsonify({"positions": positions})
    except Exception as exc:
        return jsonify({"positions": [], "error": str(exc)})


# ── Helpers ─────────────────────────────────────────────────────────────────

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


def _persist(result: dict):
    os.makedirs("data/daily_briefs", exist_ok=True)
    with open(f"data/daily_briefs/{result['date']}.json", "w") as f:
        json.dump(result, f, indent=2)


def _now_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now_time() -> str:
    return datetime.now().strftime("%H:%M")


# ── Entry ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== Swing Sentinel - Local Server ===")
    print("  Dashboard : http://localhost:5000")
    print("  Scan API  : POST /api/scan")
    print("  Market    : GET  /api/market")
    print("  Positions : GET  /api/positions")
    print("=" * 38 + "\n")
    app.run(debug=False, port=5000, host="0.0.0.0")
