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
from typing import Dict, List

import pandas as pd

from core.chartink_fetcher import fetch_chartink_stocks
from core.data_fetcher import fetch_fii_dii_flow, fetch_nifty_levels, fetch_stock_technicals
from core.telegram_bot import format_morning_brief, send_telegram_message
from core.trade_plan import calculate_rr, calculate_trade_plan

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
    print(f"[Scanner] Starting morning scan: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
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
                # All Chartink-matched stocks are entry-ready
                "verdict": "entry",
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
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M"),
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

    # P1: Best R:R
    best_rr = max(results, key=lambda x: _extract_rr(x["rr"]))
    actions.append({
        "priority": "P1",
        "symbol": best_rr["symbol"],
        "action": (
            f"Place GTT for {best_rr['symbol']} @ ₹{best_rr['entry_min']:.0f}–{best_rr['entry_max']:.0f} "
            f"→ R:R 1:{_extract_rr(best_rr['rr']):.1f}. SL ₹{best_rr['sl']:.0f}."
        ),
    })

    # P2: Closest to entry zone
    if len(results) > 1:
        nearest = min(results, key=lambda x: abs(x["price"] - x["entry_min"]))
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
    if len(results) > 2:
        highest_upside = max(
            [r for r in results if r["symbol"] not in {a["symbol"] for a in actions}],
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

    with open(f"data/daily_briefs/{date}.json", "w") as f:
        json.dump(brief, f, indent=2)

    with open(f"data/daily_briefs/{date}.md", "w") as f:
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
    with open(html_path, "r") as f:
        html = f.read()

    stocks_json = json.dumps(scan_results, indent=2)
    html = re.sub(
        r"const stocks = \[.*?\];",
        f"const stocks = {stocks_json};",
        html, flags=re.DOTALL, count=1
    )

    if brief:
        brief_json = json.dumps(brief, indent=2)
        html = re.sub(
            r"const brief = \{.*?\};",
            f"const brief = {brief_json};",
            html, flags=re.DOTALL, count=1
        )

    with open(html_path, "w") as f:
        f.write(html)


def send_alert(brief: Dict):
    message = format_morning_brief(brief)
    send_telegram_message(message, parse_mode="Markdown")


def _send_empty_brief(nifty_data: Dict, sentiment: str):
    msg = (
        f"📊 *Morning Brief — {datetime.now().strftime('%Y-%m-%d')}*\n\n"
        f"Nifty 50: {nifty_data['level']} ({nifty_data['change_pct']:+.2f}%)\n"
        f"Sentiment: {sentiment}\n\n"
        f"⚠️ No setups matched today — market may be closed or conditions not met."
    )
    send_telegram_message(msg, parse_mode="Markdown")


if __name__ == "__main__":
    run_morning_scanner()
