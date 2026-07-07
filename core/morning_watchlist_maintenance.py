import os
import sys
import logging
import json
from datetime import datetime
import pandas as pd
import pytz

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("watchlist_maintenance")

# Set up paths
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

try:
    from core.data_fetcher import fetch_stock_technicals, NSE_TICKERS
    from core.trade_plan import calculate_trade_plan
    from core.risk_filters import apply_structural_safety_gates, apply_risk_filters, filter_fundamental_strength
    from core.sectors import fetch_sector_pulse
    from core.expiry_grading import grade_setup, expiry_context
    from core.prune_logic import evaluate_prune
except ImportError:
    # Fallback to direct imports if run from inside core/
    try:
        from core_data_fetcher import fetch_stock_technicals, NSE_TICKERS
        from core_trade_plan import calculate_trade_plan
        from core_risk_filters import apply_structural_safety_gates, apply_risk_filters, filter_fundamental_strength
        from core_sectors import fetch_sector_pulse
        from expiry_grading import grade_setup, expiry_context
        from core_prune_logic import evaluate_prune
    except ImportError:
        # Fallback to local files if path is configured otherwise
        from data_fetcher import fetch_stock_technicals, NSE_TICKERS
        from trade_plan import calculate_trade_plan
        from risk_filters import apply_structural_safety_gates, apply_risk_filters, filter_fundamental_strength
        from sectors import fetch_sector_pulse
        from expiry_grading import grade_setup, expiry_context
        from prune_logic import evaluate_prune


def _truthy(val) -> bool:
    """CSV booleans come back as strings 'True'/'False' — handle both."""
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("true", "1", "yes")


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


def refresh_trade_levels(df_positions, idx, tech, gate=None):
    # Plan levels (Entry/SL/T1/T2) are LOCKED at first scan — the refresh scan must
    # NOT recompute them. `plan` is still computed here only to feed setup grading
    # against today's technicals (monitoring), not to overwrite the trade plan.
    plan = calculate_trade_plan(tech, is_refresh=True)

    # Persist freshly-recomputed gate/score inputs so the Analysis-tab matrix
    # (driven by the OPEN watchlist) re-evaluates the 9 safety gates + quality
    # score against today's data instead of stale buy-time snapshots.
    today_str = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
    df_positions.at[idx, "Cur_Weekly_Trend"]       = tech.get("weekly_trend", "")
    df_positions.at[idx, "Cur_Return_20d"]         = tech.get("return_20d", "")
    df_positions.at[idx, "Cur_ADX"]                = tech.get("adx", "")
    df_positions.at[idx, "Cur_EMA20"]              = tech.get("ema20", "")
    df_positions.at[idx, "Cur_EMA50"]              = tech.get("ema50", "")
    df_positions.at[idx, "Cur_ATR_Pct"]            = tech.get("atr_pct", "")
    df_positions.at[idx, "Cur_Base_Status"]        = tech.get("base_status", "")
    df_positions.at[idx, "Cur_Base_Days"]          = tech.get("base_days", "")
    df_positions.at[idx, "Cur_Vol_Ratio"]          = tech.get("volume_ratio", "")
    df_positions.at[idx, "Cur_False_Breakout_Risk"] = tech.get("false_breakout_risk", "")
    df_positions.at[idx, "Cur_Scan_Date"]          = today_str
    if gate is not None:
        df_positions.at[idx, "Cur_Verdict"]     = gate.get("verdict", "")
        df_positions.at[idx, "Cur_Reasons"]     = json.dumps(gate.get("reasons", []))
        df_positions.at[idx, "Cur_Regime_Mult"] = gate.get("regime_mult", "")


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

    # Auto-update Fundamental_Status based on the strength filter
    try:
        symbol_str = str(df_positions.at[idx, "Symbol"])
        passed_fund, _ = filter_fundamental_strength(symbol_str)
        df_positions.at[idx, "Fundamental_Status"] = "APPROVED" if passed_fund else "ON_HOLD"
    except Exception as fund_err:
        logger.warning("Failed to auto-update Fundamental_Status for %s: %s", df_positions.at[idx, "Symbol"], fund_err)


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


def send_summary_notification(kept, deleted, absent, trailed=None, manual=False, database_updated=False):
    ist = pytz.timezone("Asia/Kolkata")
    now_str = datetime.now(ist).strftime("%Y-%m-%d %H:%M")
    source = "Manual Trigger" if manual else "Daily Scheduler"
    persistence_line = (
        "Database refreshed successfully."
        if database_updated
        else "Database checked; no changes required."
    )
    
    lines = [
        f"🌅 <b>Keep & refresh Scan ({source})</b>",
        f"<i>Time: {now_str} IST</i>",
        f"<b>{persistence_line}</b>",
        "Fresh Analysis data is available when the dashboard reloads.",
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
        
    if trailed:
        lines.append("📈 <b>Trailed Stop Loss Adjustments (Portfolio):</b>")
        for t in trailed:
            lines.append(
                f"• <b>{t['symbol']}</b>: LTP ₹{t['price']:.2f} | SL ₹{t['old_sl']:.2f} ➔ ₹{t['new_sl']:.2f} "
                f"(HH: ₹{t['highest_high']:.2f}, ATR: {t['atr']:.2f})"
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
        
    if not kept and not deleted and not absent and not trailed:
        lines.append("ℹ️ No active candidates found to maintain.")
        
    message = "\n".join(lines)
    send_telegram_alert(message)


def run_morning_maintenance(manual_trigger=False) -> dict:
    """
    Executes morning maintenance workflow for all candidates with OPEN status in positions.csv.
    """
    logger.info("Starting Keep & refresh Scan workflow (manual=%s)...", manual_trigger)
    
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
        send_summary_notification([], [], [], manual_trigger, database_updated=False)
        return {"status": "success", "message": "No positions found", "kept": [], "deleted": []}
        
    # Ensure Absent_Cycles, Prune_Reason, and Prune_Date columns exist
    if "Absent_Cycles" not in df_positions.columns:
        df_positions["Absent_Cycles"] = 0
    df_positions["Absent_Cycles"] = df_positions["Absent_Cycles"].fillna(0).astype(int)
    
    cur_cols = [
        "Prune_Reason", "Prune_Date",
        "Cur_Weekly_Trend", "Cur_Return_20d", "Cur_ADX", "Cur_EMA20", "Cur_EMA50",
        "Cur_ATR_Pct", "Cur_Base_Status", "Cur_Base_Days", "Cur_Vol_Ratio",
        "Cur_False_Breakout_Risk", "Cur_Scan_Date", "Cur_Verdict", "Cur_Reasons",
        "Cur_Regime_Mult",
        # Locked at first scan; ensured here (object dtype) so reads/backfill never
        # hit the float-into-str-col error that silently mass-prunes the watchlist.
        "Risk_Per_Share", "Rupee_Risk",
        "Highest_High_Since_Entry", "T1_Hit_Date",
    ]
    # Force object dtype so per-cell writes accept both floats (return_20d, adx,
    # EMAs, regime mult) and strings (verdict, reasons JSON) — pandas 3.0 raises
    # on assigning a float into a str-dtype column otherwise.
    for col in cur_cols:
        if col not in df_positions.columns:
            df_positions[col] = pd.Series([""] * len(df_positions), dtype=object)
        else:
            df_positions[col] = df_positions[col].astype(object)

    csv_updated = False

    kept_symbols = []
    deleted_symbols = []
    absent_pending_symbols = []
    trailed_symbols = []

    # Gate #9 (Sector × Nifty regime) inputs — fetched once per run, not per stock.
    try:
        sector_pulse = fetch_sector_pulse()
    except Exception as exc:
        logger.warning("fetch_sector_pulse failed: %s", exc)
        sector_pulse = {}

    for idx, row in df_positions.iterrows():
        status = str(row["Status"]).upper()
        if status not in ("OPEN", "BOUGHT"):
            continue
            
        sym = str(row["Symbol"]).strip().upper()
        
        if status == "OPEN":
            logger.info("Evaluating watchlist candidate: %s", sym)
            try:
                # 1. Run time-based checks first (which don't require technicals)
                state, reason = evaluate_prune({}, row.to_dict())
                if state == "PRUNE":
                    df_positions.at[idx, "Status"] = "PRUNED"
                    df_positions.at[idx, "Prune_Reason"] = reason
                    df_positions.at[idx, "Prune_Date"] = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
                    csv_updated = True
                    deleted_symbols.append({
                        "symbol": sym,
                        "reason": reason
                    })
                    logger.info("Pruned (Time-based): %s (%s)", sym, reason)
                    continue

                # 2. Fetch technicals for technical prune rules
                tech = fetch_stock_technicals(sym)
                if tech:
                    # Run the canonical evaluate_prune logic for technical rules
                    state, reason = evaluate_prune(tech, row.to_dict())
                    if state == "RE-EVALUATE":
                        # Re-run the canonical stacked gates (verdict/reasons + Gate #9
                        # regime multiplier) so the Analysis-tab matrix reflects today's
                        # eligibility, then overwrite levels (adapt to progression of
                        # trend-anchor moving averages).
                        gate = None
                        passed = True
                        reasons = []
                        verdict = "PASS"
                        regime_mult = 1.0
                        try:
                            passed, reasons, verdict, regime_mult = apply_risk_filters(sym, tech, sector_pulse)
                            gate = {"verdict": verdict, "reasons": reasons, "regime_mult": regime_mult}
                        except Exception as gate_err:
                            logger.warning("apply_risk_filters failed for %s: %s", sym, gate_err)
                        
                        if not passed or verdict == "SKIP":
                            reason_str = "; ".join(reasons) if reasons else "failed safety gates"
                            df_positions.at[idx, "Status"] = "PRUNED"
                            df_positions.at[idx, "Prune_Reason"] = f"Safety gates failed: {reason_str}"
                            df_positions.at[idx, "Prune_Date"] = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
                            csv_updated = True
                            deleted_symbols.append({
                                "symbol": sym,
                                "reason": f"Safety gates failed: {reason_str}"
                            })
                            logger.info("Pruned (Safety Gates): %s (%s)", sym, reason_str)
                            continue

                        refresh_trade_levels(df_positions, idx, tech, gate)
                        csv_updated = True
                        
                        # Fetch updated row details for the summary
                        updated_row = df_positions.loc[idx]
                        kept_symbols.append({
                            "symbol": sym,
                            "price": tech.get("price", 0),
                            "entry": updated_row.get("Entry_Price", 0),
                            "sl": updated_row.get("Current_SL", 0),
                            "t2": updated_row.get("Target_2", 0),
                            "grade": updated_row.get("Setup_Grade", "C"),
                            "score": updated_row.get("Setup_Score", 0.0),
                            "setup": updated_row.get("Setup", "")
                        })
                        logger.info("Kept & refreshed: %s (passed structural checks)", sym)
                    else:
                        # Failed structural checks -> Mark as PRUNED instead of deleting
                        df_positions.at[idx, "Status"] = "PRUNED"
                        df_positions.at[idx, "Prune_Reason"] = reason
                        df_positions.at[idx, "Prune_Date"] = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
                        csv_updated = True
                        deleted_symbols.append({
                            "symbol": sym,
                            "reason": reason
                        })
                        logger.info("Pruned: %s (%s)", sym, reason)
                else:
                    # Fetch failed (tech is empty) -> Increment Absent_Cycles
                    absent_cycles = int(df_positions.at[idx, "Absent_Cycles"]) + 1
                    if absent_cycles > 1:
                        df_positions.at[idx, "Status"] = "PRUNED"
                        df_positions.at[idx, "Prune_Reason"] = f"Absent from data feed (failed yfinance fetch for {absent_cycles} cycles)"
                        df_positions.at[idx, "Prune_Date"] = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
                        csv_updated = True
                        deleted_symbols.append({
                            "symbol": sym,
                            "reason": f"Absent from data feed (failed yfinance fetch for {absent_cycles} cycles)"
                        })
                        logger.info("Pruned: %s (absent & failed to fetch for %d cycles)", sym, absent_cycles)
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
                    df_positions.at[idx, "Status"] = "PRUNED"
                    df_positions.at[idx, "Prune_Reason"] = f"Error during analysis ({exc})"
                    df_positions.at[idx, "Prune_Date"] = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
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
        
        elif status == "BOUGHT":
            logger.info("Evaluating active position trailing SL: %s", sym)
            try:
                tech = fetch_stock_technicals(sym)
                if tech:
                    # Trailing SL calculations (Option A)
                    # 1. Fetch latest daily ATR
                    atr = float(tech.get("atr", 0))
                    
                    # 2. Robust highest high since entry check
                    raw_hh = row.get("Highest_High_Since_Entry")
                    try:
                        if raw_hh in (None, "", "NaN") or pd.isna(raw_hh):
                            highest_high = 0.0
                        else:
                            highest_high = float(raw_hh)
                    except (ValueError, TypeError):
                        highest_high = 0.0
                        
                    entry_date_str = str(row.get("Entry_Date", "")).strip()
                    ep = float(row.get("Entry_Price", 0))
                    cur_price = float(tech.get("price", 0))
                    
                    if entry_date_str and entry_date_str != "nan":
                        try:
                            import yfinance as yf
                            ticker_key = NSE_TICKERS.get(sym.upper(), f"{sym}.NS")
                            hist = yf.Ticker(ticker_key).history(start=entry_date_str)
                            if not hist.empty:
                                highs = hist["High"].dropna()
                                if not highs.empty:
                                    highest_high = max(highest_high, float(highs.max()))
                        except Exception as ex:
                            logger.warning("Failed to query yfinance history since entry %s for %s: %s", entry_date_str, sym, ex)
                            
                    # Safety check: highest high can never be less than current spot price or entry price
                    highest_high = max(highest_high, cur_price, ep)
                    df_positions.at[idx, "Highest_High_Since_Entry"] = highest_high
                    
                    # 3. Calculate Chandelier Exit stop
                    multiplier = 2.0 if str(row.get("Setup", "")).upper() == "BREAKOUT" else 2.5
                    computed_chandelier = highest_high - (multiplier * atr)
                    
                    # 4. Cost-Basis Lock: check if Target 1 has been cleared
                    t1_hit_date_val = row.get("T1_Hit_Date")
                    has_t1_date = t1_hit_date_val not in ("", None, "NaN") and not pd.isna(t1_hit_date_val)
                    t1_cleared = (cur_price >= float(row.get("Target_1", 999999))) or _truthy(row.get("T1_Notified")) or has_t1_date
                    
                    current_sl = float(row.get("Current_SL", 0))
                    target_floor = current_sl
                    if t1_cleared and ep > 0:
                        target_floor = max(target_floor, ep)
                        
                    # 5. One-Way Ratchet Rule: stop loss can ONLY move upward
                    final_sl = max(computed_chandelier, target_floor)
                    final_sl = round(final_sl, 2)
                    
                    if final_sl > current_sl:
                        df_positions.at[idx, "Current_SL"] = final_sl
                        csv_updated = True
                        trailed_symbols.append({
                            "symbol": sym,
                            "price": cur_price,
                            "old_sl": current_sl,
                            "new_sl": final_sl,
                            "highest_high": highest_high,
                            "atr": atr
                        })
                        logger.info("Trailed Stop Loss for %s: ₹%s ➔ ₹%s", sym, current_sl, final_sl)
                        
                        # Sync with Kite GTT
                        raw_gtt = row.get("gtt_id") or row.get("GTT_Id")
                        if raw_gtt not in (None, "", "NaN") and not pd.isna(raw_gtt):
                            try:
                                gtt_id_clean = int(float(str(raw_gtt)))
                                try:
                                    from core_kite import modify_gtt
                                except ImportError:
                                    from core.core_kite import modify_gtt
                                
                                qty = int(row.get("Quantity", 1))
                                t2 = float(row.get("Target_2", 0))
                                modify_gtt(trigger_id=gtt_id_clean, symbol=sym, qty=qty, last_price=cur_price, sl=final_sl, target=t2)
                            except Exception as gtt_err:
                                logger.warning("Failed to modify Kite GTT for %s during morning maintenance: %s", sym, gtt_err)
                    else:
                        logger.info("Stop Loss for %s kept flat at ₹%s (computed Chandelier was ₹%s)", sym, current_sl, round(computed_chandelier, 2))
                else:
                    logger.warning("Could not fetch technicals for active position %s", sym)
            except Exception as exc:
                logger.error("Error updating trailing SL for active position %s: %s", sym, exc)
        
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
    send_summary_notification(
        kept_symbols,
        deleted_symbols,
        absent_pending_symbols,
        trailed=trailed_symbols,
        manual=manual_trigger,
        database_updated=csv_updated,
    )
    
    return {
        "status": "success",
        "kept": kept_symbols,
        "deleted": deleted_symbols,
        "absent_pending": absent_pending_symbols,
        "trailed": trailed_symbols
    }


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=os.path.join(_ROOT, ".env"))
    except ImportError:
        pass
    run_morning_maintenance()
