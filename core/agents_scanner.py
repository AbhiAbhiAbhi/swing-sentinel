"""
Scanner Agent
Runs every morning to identify swing trade candidates via Chartink screener.
Chartink pre-filters stocks matching all technical indicators.
yfinance is used only for trade plan calculation on the filtered set.
"""
import json
import logging
import os
from datetime import datetime
import pytz
from typing import Dict, List

try:
    from core.chartink_fetcher import fetch_chartink_stocks
    from core.data_fetcher import fetch_fii_dii_flow, fetch_nifty_levels, fetch_stock_technicals
    from core.trade_plan import calculate_rr, calculate_trade_plan
    from core.risk_filters import apply_risk_filters
    from core.sectors import fetch_sector_pulse
except ImportError:
    from core_chartink_fetcher import fetch_chartink_stocks
    from core_data_fetcher import fetch_fii_dii_flow, fetch_nifty_levels, fetch_stock_technicals
    from core_trade_plan import calculate_rr, calculate_trade_plan
    from core_risk_filters import apply_risk_filters
    from core_sectors import fetch_sector_pulse

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def run_morning_scanner():
    """
    Execute complete morning scanning workflow.

    Steps:
    1. Fetch market context (Nifty levels, FII/DII)
    2. Call Chartink screener — returns stocks matching all technical conditions
    3. For each matched stock fetch yfinance data for trade plan calculation
    4. Generate top 3 priority actions
    5. Save brief + update dashboard + send Telegram alert
    """

    print("\n" + "=" * 60)
    print(f"[Scanner] Starting morning scan: {datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # Step 1: Market Context
    print("\n[1/6] Fetching market levels...")
    nifty_data = fetch_nifty_levels()
    fii_dii = fetch_fii_dii_flow(days=5)
    sentiment = determine_sentiment(nifty_data, fii_dii)
    print(f"  Nifty 50  : {nifty_data['level']} ({nifty_data['change_pct']:+.2f}%)")
    print(f"  FII Flow  : {fii_dii.get('fii_today', 0):+.0f} Cr")
    print(f"  Sentiment : {sentiment}")

    # Step 2: Chartink scan — pre-filters by all technical indicators
    print("\n[2/6] Running Chartink screener scan...")
    chartink_stocks = fetch_chartink_stocks()

    if not chartink_stocks:
        print("  [!] Chartink returned 0 matches — market closed or no setups today.")
        _send_empty_brief(nifty_data, sentiment)
        return

    print(f"  Chartink matched: {len(chartink_stocks)} stocks")

    # Step 3: Fetch yfinance data for trade plan on each matched stock
    print("\n[3/6] Fetching trade plan data (yfinance)...")
    scan_results = []

    # Single source of truth for safety gates (spec checklist #1): fetch sector pulse once
    # so apply_risk_filters can evaluate Gate #9 (Sector x Nifty regime) per stock.
    try:
        sector_pulse = fetch_sector_pulse()
    except Exception as exc:
        logger.warning("[scanner] sector pulse fetch failed: %s", exc)
        sector_pulse = {}

    for stock in chartink_stocks:
        symbol = stock["symbol"]
        try:
            print(f"  {symbol}...", end=" ")
            tech_data = fetch_stock_technicals(symbol)
            if not tech_data:
                print("no data")
                continue

            plan = calculate_trade_plan(tech_data)

            entry_mid = (plan.get("entry_zone_min", 0) + plan.get("entry_zone_max", 0)) / 2
            rr_data = calculate_rr({
                "price": entry_mid,
                "target": plan.get("target_2", 0),
                "sl": plan.get("stop_loss", 0),
            })

            # Canonical safety gates (single source of truth) — replaces the per-check
            # fundamental/liquidity/overextension duplication in generate_priority_actions.
            passed, reasons, verdict, regime_mult = apply_risk_filters(
                symbol, tech_data, sector_pulse=sector_pulse)
            if verdict == "SKIP":
                print(f"skip ({'; '.join(reasons)[:50]})")
                continue

            scan_results.append({
                "symbol": symbol,
                "name": stock.get("name", symbol),
                "price": tech_data["price"],
                "change_pct": tech_data["change_pct"],
                # Chartink already confirmed these pass — store for display
                "rsi": tech_data["rsi"],
                "ema20": tech_data["ema20"],
                "macd": tech_data["macd"],
                "vol_ratio": tech_data["volume_ratio"],
                # Trade plan
                "entry_min": plan.get("entry_zone_min", 0),
                "entry_max": plan.get("entry_zone_max", 0),
                "target_1": plan.get("target_1", 0),
                "target_2": plan.get("target_2", 0),
                "sl": plan.get("stop_loss", 0),
                "rr": rr_data if isinstance(rr_data, str) else plan.get("rr_ratio", "N/A"),
                "setup": plan.get("setup_type", "—"),
                # Canonical verdict from apply_risk_filters (PASS / WATCH / WARNING)
                "verdict": verdict,
                "reasons": reasons,
                "regime_multiplier": regime_mult,
                # Advanced Indicators
                "weekly_trend": tech_data.get("weekly_trend", "BULLISH"),
                "base_days": tech_data.get("base_days", 0),
                "base_status": tech_data.get("base_status", "VOLATILE"),
                "false_breakout_risk": tech_data.get("false_breakout_risk", "LOW"),
                "false_breakout_desc": tech_data.get("false_breakout_desc", ""),
                "atr_pct": tech_data.get("atr_pct", 3.0),
                "adx": tech_data.get("adx", 25.0),
                "macd_crossover_days_ago": tech_data.get("macd_crossover_days_ago", -1),
                "resistance_1": tech_data.get("resistance_1", 0.0),
                "avg_volume_20d": tech_data.get("avg_volume_20d", 0),
                "return_20d": tech_data.get("return_20d", 0.0),
            })
            print(f"ok  entry ₹{plan.get('entry_zone_min', 0):.0f}–{plan.get('entry_zone_max', 0):.0f}  "
                  f"SL ₹{plan.get('stop_loss', 0):.0f}  R:R {plan.get('rr_ratio', '?')}")

        except Exception as exc:
            print(f"ERROR ({str(exc)[:40]})")
            continue

    if not scan_results:
        print("  [!] Trade plan calculation failed for all matched stocks.")
        _send_empty_brief(nifty_data, sentiment)
        return

    print(f"  Processed : {len(scan_results)} stocks with trade plans")

    # Step 4: Generate Priority Actions
    print("\n[4/6] Generating priority actions...")
    actions = generate_priority_actions(scan_results)
    for a in actions:
        print(f"  [{a['priority']}] {a['symbol']} — {a['action'][:60]}")

    # Step 5: Compile Brief
    brief = {
        "date": datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d"),
        "time": datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%H:%M"),
        "market_context": {
            "nifty": nifty_data,
            "fii_dii": fii_dii,
            "sentiment": sentiment,
        },
        "actions": actions,
        "entry_ready": scan_results,
        "setup_forming": [],
        "skip": [],
        "total_scanned": len(chartink_stocks),
        "source": "Chartink screener",
    }

    # Step 6: Save + Alert
    print("\n[5/6] Saving brief and updating dashboard...")
    save_brief(brief)
    update_dashboard_data(scan_results, brief)

    print("\n[6/6] Sending Telegram notification...")
    send_alert(brief)

    print("\n" + "=" * 60)
    print(f"[Scanner] Done — {len(scan_results)} trade setups found")
    print("=" * 60 + "\n")

    return brief


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def determine_sentiment(nifty_data: Dict, fii_dii: Dict) -> str:
    fii_net = sum(fii_dii.get("fii_last_5_days", [0, 0, 0, 0, 0]))
    if fii_net > 5000 and nifty_data["change_pct"] > 0:
        return "🟢 Bullish"
    elif fii_net < -5000:
        return "🔴 Cautious"
    return "⚪ Neutral"


def generate_priority_actions(results: List[Dict]) -> List[Dict]:
    actions = []

    if not results:
        return actions

    # Load overrides from positions.csv to identify ON_HOLD or confirmation-locked symbols
    on_hold_symbols = set()
    confirmation_locked_symbols = set()
    path = "data/positions.csv"
    if os.path.exists(path):
        try:
            import pandas as pd
            df = pd.read_csv(path)
            if not df.empty:
                df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
                df["Status"] = df["Status"].fillna("").astype(str).str.strip().str.upper()
                df["Fundamental_Status"] = df["Fundamental_Status"].fillna("").astype(str).str.strip().str.upper()
                
                # Exclude watchlist OPEN positions that are ON_HOLD
                on_hold = df[(df["Status"] == "OPEN") & (df["Fundamental_Status"] == "ON_HOLD")]["Symbol"].tolist()
                on_hold_symbols.update(on_hold)
                
                # Exclude watchlist OPEN positions locked by a breakout trigger (price below trigger)
                for _, r in df[df["Status"] == "OPEN"].iterrows():
                    sym = str(r["Symbol"]).upper()
                    cp_trigger = float(r.get("Confirmation_Price") or 0.0)
                    if cp_trigger > 0:
                        # Find current price from technical results to see if still locked
                        s_res = next((x for x in results if x["symbol"].upper() == sym), None)
                        s_price = float(s_res["price"]) if s_res else 0.0
                        if s_price < cp_trigger:
                            confirmation_locked_symbols.add(sym)
        except Exception as exc:
            logger.warning("[scanner/priority] Override load failed: %s", exc)

    # Filter results to only evaluate eligible candidates
    eligible_results = []
    for r in results:
        sym = r["symbol"].upper()
        if sym in on_hold_symbols or sym in confirmation_locked_symbols:
            continue
            
        # Avoid chasing "No Man's Land" setups:
        # If the stock has run up above the pullback entry support zone (entry_max) 
        # but is still below the breakout resistance trigger (resistance_1),
        # then it has no clean technical trigger yet. Exclude it from Priority briefings.
        price = float(r.get("price") or 0.0)
        entry_max = float(r.get("entry_max") or 0.0)
        res_1 = float(r.get("resistance_1") or r.get("target_1") or 0.0)
        
        if price > 0 and entry_max > 0:
            is_chasing_pullback = price > (entry_max * 1.025)
            is_below_breakout = res_1 > 0 and price < res_1
            
            if is_chasing_pullback and is_below_breakout:
                logger.info("[scanner/priority] Excluded %s from Top 3 priority list: In No Man's Land (Price ₹%.2f is between Pullback Support ₹%.2f and Breakout Resistance ₹%.2f)", sym, price, entry_max, res_1)
                continue
                
        # Safety gates (fundamental strength, liquidity, overextension, weekly trend,
        # institutional flow, regime alignment, etc.) are now applied upstream in
        # run_morning_scanner() via apply_risk_filters — the single source of truth
        # (spec checklist #1). Only priority-list-specific exclusions remain above.
                
        eligible_results.append(r)

    eval_list = eligible_results

    if not eval_list:
        return actions

    # P1: Best R:R
    best_rr = max(eval_list, key=lambda x: _extract_rr(x["rr"]))
    actions.append({
        "priority": "P1",
        "symbol": best_rr["symbol"],
        "action": (
            f"Place GTT for {best_rr['symbol']} @ ₹{best_rr['entry_min']:.0f}–{best_rr['entry_max']:.0f} "
            f"→ R:R 1:{_extract_rr(best_rr['rr']):.1f}. SL ₹{best_rr['sl']:.0f}."
        ),
    })

    # P2: Closest to entry zone
    if len(eval_list) > 1:
        nearest = min(eval_list, key=lambda x: abs(x["price"] - x["entry_min"]))
        if nearest["symbol"] != best_rr["symbol"]:
            actions.append({
                "priority": "P2",
                "symbol": nearest["symbol"],
                "action": (
                    f"Watch {nearest['symbol']} @ ₹{nearest['price']:.0f} — "
                    f"approaching entry ₹{nearest['entry_min']:.0f}–{nearest['entry_max']:.0f}."
                ),
            })

    # P3: Highest target 2 upside %
    if len(eval_list) > 2:
        highest_upside = max(
            [r for r in eval_list if r["symbol"] not in {a["symbol"] for a in actions}],
            key=lambda x: ((x["target_2"] - x["price"]) / x["price"] if x["price"] > 0 else 0),
            default=None,
        )
        if highest_upside:
            upside_pct = ((highest_upside["target_2"] - highest_upside["price"]) / highest_upside["price"] * 100)
            actions.append({
                "priority": "P3",
                "symbol": highest_upside["symbol"],
                "action": (
                    f"Best upside: {highest_upside['symbol']} target ₹{highest_upside['target_2']:.0f} "
                    f"({upside_pct:+.1f}%). Entry ₹{highest_upside['entry_min']:.0f}–{highest_upside['entry_max']:.0f}."
                ),
            })

    return actions[:3]


def _extract_rr(rr) -> float:
    try:
        if isinstance(rr, str) and ":" in rr:
            return float(rr.split(":")[1])
        return float(rr) if rr else 0.0
    except Exception:
        return 0.0


def save_brief(brief: Dict):
    os.makedirs("data/daily_briefs", exist_ok=True)
    date = brief["date"]

    with open(f"data/daily_briefs/{date}.json", "w", encoding="utf-8") as f:
        json.dump(brief, f, indent=2)

    with open(f"data/daily_briefs/{date}.md", "w", encoding="utf-8") as f:
        f.write(f"# Morning Brief — {date} {brief['time']}\n\n")
        f.write(f"## Market\n")
        f.write(f"- Nifty 50: {brief['market_context']['nifty']['level']} "
                f"({brief['market_context']['nifty']['change_pct']:+.2f}%)\n")
        f.write(f"- Sentiment: {brief['market_context']['sentiment']}\n\n")
        f.write(f"## Top Actions\n")
        for a in brief["actions"]:
            f.write(f"- **[{a['priority']}]** {a['action']}\n\n")
        f.write(f"## Stats\n")
        f.write(f"- Chartink matched: {brief['total_scanned']}\n")
        f.write(f"- Trade plans generated: {len(brief['entry_ready'])}\n")


def update_dashboard_data(scan_results: List[Dict], brief: Dict = None):
    import re
    html_path = "dashboard/swing_agent_app.html"
    if not os.path.exists(html_path):
        return
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    stocks_json = json.dumps(scan_results, indent=2).replace('\\', '\\\\')
    html = re.sub(
        r"const STATIC_STOCKS = \[.*?\];",
        f"const STATIC_STOCKS = {stocks_json};",
        html, flags=re.DOTALL, count=1
    )

    if brief:
        brief_json = json.dumps(brief, indent=2).replace('\\', '\\\\')
        html = re.sub(
            r"const STATIC_BRIEF\s*= \{.*?\};",
            f"const STATIC_BRIEF  = {brief_json};",
            html, flags=re.DOTALL, count=1
        )

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)


def _send_telegram(msg: str):
    """Send a Telegram message using TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars."""
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id or token == "your_bot_token_here":
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("[telegram] send failed: %s", exc)


def send_alert(brief: Dict):
    nifty = brief["market_context"]["nifty"]
    lines = [
        f"📊 *Morning Brief — {brief['date']} {brief['time']}*",
        f"Nifty 50: {nifty['level']} ({nifty['change_pct']:+.2f}%)",
        f"Sentiment: {brief['market_context']['sentiment']}",
        "",
        "*Top Actions:*",
    ]
    for a in brief.get("actions", []):
        lines.append(f"• [{a['priority']}] {a['action']}")
    _send_telegram("\n".join(lines))


def _send_empty_brief(nifty_data: Dict, sentiment: str):
    _send_telegram(
        f"📊 *Morning Brief — {datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d')}*\n\n"
        f"Nifty 50: {nifty_data['level']} ({nifty_data['change_pct']:+.2f}%)\n"
        f"Sentiment: {sentiment}\n\n"
        f"⚠️ No setups matched today — market may be closed or conditions not met."
    )


if __name__ == "__main__":
    run_morning_scanner()
