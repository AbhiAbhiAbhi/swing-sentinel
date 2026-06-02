import os
import sys
import logging
import json
from datetime import datetime
import pandas as pd

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("watchlist_maintenance")

# Set up paths
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

try:
    from core.data_fetcher import fetch_stock_technicals
    from core.trade_plan import calculate_trade_plan
    from core.risk_filters import apply_structural_safety_gates
    from core.expiry_grading import grade_setup, expiry_context
except ImportError:
    # Fallback to direct imports if run from inside core/
    try:
        from core_data_fetcher import fetch_stock_technicals
        from core_trade_plan import calculate_trade_plan
        from core_risk_filters import apply_structural_safety_gates
        from expiry_grading import grade_setup, expiry_context
    except ImportError:
        # Fallback to local files if path is configured otherwise
        from data_fetcher import fetch_stock_technicals
        from trade_plan import calculate_trade_plan
        from risk_filters import apply_structural_safety_gates
        from expiry_grading import grade_setup, expiry_context

_universes_cache_data = None
_universes_cache_mtime = 0

def get_index_membership_local(symbol: str) -> list:
    global _universes_cache_data, _universes_cache_mtime
    symbol = symbol.strip().upper()
    cache_path = os.path.join(_ROOT, "data", "universes_cache.json")
    if not os.path.exists(cache_path):
        return []
    
    try:
        mtime = os.path.getmtime(cache_path)
        if _universes_cache_data is None or mtime > _universes_cache_mtime:
            with open(cache_path, "r") as f:
                _universes_cache_data = json.load(f)
            _universes_cache_mtime = mtime
    except Exception as exc:
        logger.warning("Failed to load universes_cache.json: %s", exc)
        return []
        
    if not _universes_cache_data:
        return []
        
    memberships = []
    
    key_mapping = {
        "nifty50": "NIFTY50",
        "niftynext50": "NIFTY NEXT 50",
        "nifty100": "NIFTY100",
        "nifty200": "NIFTY200",
        "nifty500": "NIFTY500",
        "fno": "F&O"
    }
    
    for key, label in key_mapping.items():
        if key in _universes_cache_data:
            symbols_list = [s.strip().upper() for s in _universes_cache_data[key]]
            if symbol in symbols_list:
                memberships.append(label)
                
    return memberships


def refresh_trade_levels(df_positions, idx, tech):
    plan = calculate_trade_plan(tech)
    df_positions.at[idx, "Entry_Price"] = plan.get("entry_zone_min", df_positions.at[idx, "Entry_Price"])
    df_positions.at[idx, "Target_1"] = plan.get("target_1", df_positions.at[idx, "Target_1"])
    df_positions.at[idx, "Target_2"] = plan.get("target_2", df_positions.at[idx, "Target_2"])
    df_positions.at[idx, "Current_SL"] = plan.get("stop_loss", df_positions.at[idx, "Current_SL"])
    
    setup_grade = "C"
    setup_score = 0.0
    if grade_setup and expiry_context:
        try:
            g_res = grade_setup(tech, plan)
            setup_grade = g_res.get("grade", "C")
            setup_score = g_res.get("score", 0.0)
            
            is_fno = "F&O" in get_index_membership_local(str(df_positions.at[idx, "Symbol"]))
            exp_info = expiry_context(is_fno=is_fno, grade=setup_grade)
            if exp_info:
                df_positions.at[idx, "Expiry_Multiplier"] = exp_info.get("multiplier", "")
                df_positions.at[idx, "Expiry_Reason"] = exp_info.get("reason", "")
        except Exception as grading_err:
            logger.warning("Grading failed for %s: %s", df_positions.at[idx, "Symbol"], grading_err)
            
    df_positions.at[idx, "Setup_Grade"] = setup_grade
    df_positions.at[idx, "Setup_Score"] = setup_score
    df_positions.at[idx, "Absent_Cycles"] = 0


def send_telegram_alert(msg: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id or token == "your_bot_token_here":
        logger.info("Telegram not configured. Message: \n%s", msg)
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
        logger.info("Telegram notification sent successfully.")
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)


def send_summary_notification(kept, deleted, absent, manual=False):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    source = "Manual Trigger" if manual else "Daily Scheduler"
    
    lines = [
        f"🌅 <b>Watchlist Morning Maintenance ({source})</b>",
        f"<i>Time: {now_str} IST</i>",
        ""
    ]
    
    if kept:
        lines.append("✅ <b>Kept & Refreshed Level Adjustments:</b>")
        for k in kept:
            lines.append(
                f"• <b>{k['symbol']}</b>: LTP ₹{k['price']:.2f} | Entry ₹{k['entry']:.2f} | "
                f"SL ₹{k['sl']:.2f} | T2 ₹{k['t2']:.2f} "
                f"(Grade {k['grade']}:{k['score']:.1f}, {k['setup']})"
            )
        lines.append("")
        
    if deleted:
        lines.append("❌ <b>Deleted / Pruned from Watchlist:</b>")
        for d in deleted:
            lines.append(f"• <b>{d['symbol']}</b>: {d['reason']}")
        lines.append("")
        
    if absent:
        lines.append("⚠️ <b>Absent / Fetch Failures (Pending Delete):</b>")
        for a in absent:
            lines.append(f"• <b>{a['symbol']}</b>: cycle {a['cycle']}/2 failed")
        lines.append("")
        
    if not kept and not deleted and not absent:
        lines.append("ℹ️ No active OPEN watchlist candidates found to maintain.")
        
    message = "\n".join(lines)
    send_telegram_alert(message)


def run_morning_maintenance(manual_trigger=False) -> dict:
    """
    Executes morning maintenance workflow for all candidates with OPEN status in positions.csv.
    """
    logger.info("Starting morning watchlist maintenance workflow (manual=%s)...", manual_trigger)
    
    positions_path = os.path.join(_ROOT, "data", "positions.csv")
    if not os.path.exists(positions_path):
        logger.warning("positions.csv does not exist. Skipping.")
        return {"status": "error", "message": "positions.csv does not exist"}
        
    try:
        df_positions = pd.read_csv(positions_path)
    except Exception as e:
        logger.error("Failed to read positions.csv: %s", e)
        return {"status": "error", "message": f"Failed to read positions: {e}"}
        
    if df_positions.empty or "Status" not in df_positions.columns:
        logger.info("No positions to maintain.")
        return {"status": "success", "message": "No positions found", "kept": [], "deleted": []}
        
    # Ensure Absent_Cycles column exists
    if "Absent_Cycles" not in df_positions.columns:
        df_positions["Absent_Cycles"] = 0
    df_positions["Absent_Cycles"] = df_positions["Absent_Cycles"].fillna(0).astype(int)
    
    rows_to_drop = []
    csv_updated = False
    
    kept_symbols = []
    deleted_symbols = []
    absent_pending_symbols = []
    
    for idx, row in df_positions.iterrows():
        if str(row["Status"]).upper() != "OPEN":
            continue
            
        sym = str(row["Symbol"]).strip().upper()
        logger.info("Evaluating watchlist candidate: %s", sym)
        
        try:
            tech = fetch_stock_technicals(sym)
            if tech:
                # Run the slow-moving structural gates (Weekly trend, daily EMA alignment)
                gate_result = apply_structural_safety_gates(tech)
                if gate_result.passed:
                    # Overwrite levels (adapt to progression of trend-anchor moving averages)
                    refresh_trade_levels(df_positions, idx, tech)
                    csv_updated = True
                    
                    # Fetch updated row details for the summary
                    updated_row = df_positions.loc[idx]
                    kept_symbols.append({
                        "symbol": sym,
                        "price": tech.get("price", 0),
                        "entry": updated_row.get("Entry_Price", 0),
                        "sl": updated_row.get("Current_SL", 0),
                        "t1": updated_row.get("Target_1", 0),
                        "t2": updated_row.get("Target_2", 0),
                        "grade": updated_row.get("Setup_Grade", "C"),
                        "score": updated_row.get("Setup_Score", 0.0),
                        "setup": updated_row.get("Setup", "")
                    })
                    logger.info("Kept & refreshed: %s (passed structural safety gates)", sym)
                else:
                    # Failed structural safety gates -> Delete
                    rows_to_drop.append(idx)
                    csv_updated = True
                    deleted_symbols.append({
                        "symbol": sym,
                        "reason": gate_result.fail_reason
                    })
                    logger.info("Deleted: %s (failed structural safety gates: %s)", sym, gate_result.fail_reason)
            else:
                # Fetch failed (tech is empty) -> Increment Absent_Cycles
                absent_cycles = int(df_positions.at[idx, "Absent_Cycles"]) + 1
                if absent_cycles > 1:
                    rows_to_drop.append(idx)
                    csv_updated = True
                    deleted_symbols.append({
                        "symbol": sym,
                        "reason": f"Absent from data feed (failed yfinance fetch for {absent_cycles} cycles)"
                    })
                    logger.info("Deleted: %s (absent & failed to fetch for %d cycles)", sym, absent_cycles)
                else:
                    df_positions.at[idx, "Absent_Cycles"] = absent_cycles
                    csv_updated = True
                    absent_pending_symbols.append({
                        "symbol": sym,
                        "cycle": absent_cycles
                    })
                    logger.info("Kept temporarily: %s (fetch failed, absent cycle %d/2)", sym, absent_cycles)
        except Exception as exc:
            logger.error("Error evaluating candidate %s: %s", sym, exc)
            # Increment Absent_Cycles
            absent_cycles = int(df_positions.at[idx, "Absent_Cycles"]) + 1
            if absent_cycles > 1:
                rows_to_drop.append(idx)
                csv_updated = True
                deleted_symbols.append({
                    "symbol": sym,
                    "reason": f"Error during analysis ({exc})"
                })
            else:
                df_positions.at[idx, "Absent_Cycles"] = absent_cycles
                csv_updated = True
                absent_pending_symbols.append({
                    "symbol": sym,
                    "cycle": absent_cycles
                })
                
    if rows_to_drop:
        df_positions = df_positions.drop(rows_to_drop).reset_index(drop=True)
        
    if csv_updated:
        # Atomic write
        tmp_path = f"{positions_path}.tmp"
        try:
            df_positions.to_csv(tmp_path, index=False)
            os.replace(tmp_path, positions_path)
            logger.info("Successfully updated positions.csv")
        except Exception as e:
            logger.error("Failed to write updated positions.csv: %s", e)
            return {"status": "error", "message": f"Failed to save positions: {e}"}
            
    # Send Telegram summary
    send_summary_notification(kept_symbols, deleted_symbols, absent_pending_symbols, manual_trigger)
    
    return {
        "status": "success",
        "kept": kept_symbols,
        "deleted": deleted_symbols,
        "absent_pending": absent_pending_symbols
    }


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    run_morning_maintenance()
