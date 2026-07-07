"""
Swing Sentinel — Local Server
Run : python server.py
Open: http://localhost:5000
"""
import json
import logging
import os
from datetime import datetime, timedelta
import pytz

# Resolve paths relative to the project root (one level up from core/)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import requests as _requests
from flask import Flask, jsonify, redirect, request, send_from_directory

# Load .env if present (python-dotenv optional — falls back gracefully)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(_ROOT, ".env"))
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
    from core.risk_filters import apply_risk_filters, fetch_screener_shareholding, apply_structural_safety_gates
    from core.sectors       import get_sector, fetch_sector_pulse
except ImportError:
    try:
        from core_risk_filters import apply_risk_filters, fetch_screener_shareholding, apply_structural_safety_gates
        from core_sectors      import get_sector, fetch_sector_pulse
    except ImportError:
        def apply_risk_filters(sym, tech, sector_pulse=None): return True, [], "PASS", 1.0
        def get_sector(sym): return "OTHERS"
        def fetch_sector_pulse(): return {}
        class GateResult:
            def __init__(self, passed, fail_reason=""):
                self.passed = passed
                self.fail_reason = fail_reason
        def apply_structural_safety_gates(tech): return GateResult(True)


_universes_cache_data = None
_universes_cache_mtime = 0

_scheduler = None
_maintenance_time = "08:30"
_get_stocks_time = "16:00"
_scan_filters = {
    "universe": "cash",
    "min_price": 50,
    "rsi_min": 40,
    "rsi_max": 70,
    "adx_min": 20,
    "min_volume_lakh": 5,
    "require_macd": True,
    "require_ema_alignment": True,
    "require_ema200": True,
    "max_atr_pct": 5,
    "max_1d_drop_pct": -8,
    "min_ipo_days": 180,
    "earnings_window_days": 3,
    "block_weak_sectors": True,
    "top_n": 30,
}

try:
    from core.prune_logic import evaluate_prune
except ImportError:
    try:
        from core_prune_logic import evaluate_prune
    except ImportError:
        def evaluate_prune(tech, row=None): return "RE-EVALUATE", ""

try:
    from core.morning_watchlist_maintenance import run_morning_maintenance
except ImportError:
    try:
        from morning_watchlist_maintenance import run_morning_maintenance
    except ImportError:
        def run_morning_maintenance(manual_trigger=False):
            return {"status": "error", "message": "Import failed"}

def _load_schedule_config():
    global _maintenance_time, _get_stocks_time, _scan_filters
    config_path = os.path.join(_ROOT, "data", "schedule_config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                data = json.load(f)
                _maintenance_time = data.get("maintenance_time", "08:30")
                _get_stocks_time = data.get("get_stocks_time", "16:00")
                saved_filters = data.get("scan_filters")
                if isinstance(saved_filters, dict):
                    _scan_filters = {**_scan_filters, **saved_filters}
        except Exception as e:
            logger.warning("[server] Failed to load schedule_config.json: %s", e)


def _save_schedule_config():
    config_path = os.path.join(_ROOT, "data", "schedule_config.json")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w") as f:
        json.dump({
            "maintenance_time": _maintenance_time,
            "get_stocks_time": _get_stocks_time,
            "scan_filters": _scan_filters,
        }, f, indent=2)


def _load_maintenance_time():
    _load_schedule_config()

def get_index_membership(symbol: str) -> list:
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
        logger.warning("[server] Failed to load universes_cache.json: %s", exc)
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
        "niftymidcap150": "NIFTY MIDCAP 150",
        "niftysmallcap250": "NIFTY SMALLCAP 250",
        "fnolist": "F&O"
    }
    
    for key, pretty_name in key_mapping.items():
        symbols_list = _universes_cache_data.get(key, [])
        if symbols_list and symbol in [s.strip().upper() for s in symbols_list]:
            memberships.append(pretty_name)
            
    return memberships


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
        fetch_prices_bulk_dated,
        fetch_stock_technicals,
        NSE_TICKERS,
    )
    from core.trade_plan import calculate_rr, calculate_trade_plan, position_risk
    from core.risk_filters import fetch_earnings_date
except ImportError:
    from core_chartink_fetcher import fetch_chartink_stocks
    from core_data_fetcher import (
        fetch_fii_dii_flow,
        fetch_global_markets,
        fetch_nifty_levels,
        fetch_prices_bulk,
        fetch_prices_bulk_dated,
        fetch_stock_technicals,
        NSE_TICKERS,
    )
    from core_trade_plan import calculate_rr, calculate_trade_plan, position_risk
    from core_risk_filters import fetch_earnings_date

# ── Setup grading + expiry ───────────────────────────────────────────
try:
    from core.expiry_grading import grade_setup, expiry_context
except ImportError:
    try:
        from expiry_grading import grade_setup, expiry_context
    except ImportError:
        grade_setup = None
        expiry_context = None



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


@app.route("/preview")
def preview():
    """Serve the premium warm light theme preview dashboard with no-cache headers."""
    resp = send_from_directory(os.path.join(_ROOT, "dashboard"), "swing_agent_preview.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"]        = "no-cache"
    resp.headers["Expires"]       = "0"
    return resp


@app.route("/review")
def review():
    """Serve the agent review sandbox dashboard with no-cache headers."""
    resp = send_from_directory(os.path.join(_ROOT, "dashboard"), "swing_agent_review.html")
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


@app.route("/api/presets", methods=["GET", "POST"])
def api_presets():
    """GET custom scanner presets from data/presets.json; POST { ...presets } to overwrite."""
    path = os.path.join(_ROOT, "data", "presets.json")
    os.makedirs(os.path.join(_ROOT, "data"), exist_ok=True)
    try:
        if request.method == "POST":
            presets = request.get_json(force=True, silent=True) or {}
            tmp = f"{path}.tmp"
            with open(tmp, "w") as f:
                json.dump(presets, f, indent=2)
            os.replace(tmp, path)
            logger.info("[presets] Saved custom presets: count=%d", len(presets))
            return jsonify({"status": "ok", "presets": presets})
        
        # GET method
        if os.path.exists(path):
            with open(path, "r") as f:
                presets = json.load(f)
        else:
            presets = {}
        return jsonify({"status": "ok", "presets": presets})
    except Exception as exc:
        logger.error("[presets] Failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500



@app.route("/api/debate/<symbol>", methods=["POST"])
def api_debate(symbol: str):
    """
    Triggers an adversarial Bull vs Bear debate for a specific stock ticker.
    Receives custom header-selected model overrides from the POST body.
    """
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return jsonify({"status": "error", "message": "Symbol is required"}), 400
    try:
        body = request.get_json(force=True, silent=True) or {}
        force = body.get("force_refresh", False)
        check_only = body.get("check_only", False)

        # 1. Fetch live technicals for this stock
        tech = fetch_stock_technicals(symbol)
        if not tech:
            return jsonify({"status": "error", "message": f"Could not fetch technical indicators for {symbol}"}), 404

        # 2. Get recent headlines for this stock from news cache
        stock_news = []
        if _news_get is not None:
            try:
                # Get the latest aggregated news (read cached, very fast)
                news_payload = _news_get(force=False)
                stock_news = news_payload.get("stocks", {}).get(symbol, {}).get("headlines", [])
            except Exception as news_exc:
                logger.warning("[server/debate] Failed to retrieve news cache for %s: %s", symbol, news_exc)

        # 3. Compile macro market context
        nifty = fetch_nifty_levels()
        fii   = fetch_fii_dii_flow(days=5)
        market_context = {
            "nifty": nifty,
            "fii_dii": fii,
            "sentiment": _sentiment(nifty, fii)
        }

        # 4. Extract LLM settings overrides.
        # Preferred: a nested {"override_config": {bull_agent, bear_agent, judge_agent}} payload.
        # Fallback: legacy flat fields (bull_provider/bull_model/bull_temperature, ...) so older
        # callers and the preview/review dashboards keep working.
        override_config = body.get("override_config") or {}
        if not override_config:
            if "bull_model" in body:
                override_config["bull_agent"] = {
                    "provider": body.get("bull_provider", "gemini"),
                    "model": body["bull_model"],
                    "temperature": float(body.get("bull_temperature", 0.4))
                }
            if "bear_model" in body:
                override_config["bear_agent"] = {
                    "provider": body.get("bear_provider", "gemini"),
                    "model": body["bear_model"],
                    "temperature": float(body.get("bear_temperature", 0.4))
                }
            if "judge_model" in body:
                override_config["judge_agent"] = {
                    "provider": body.get("judge_provider", "gemini"),
                    "model": body["judge_model"],
                    "temperature": float(body.get("judge_temperature", 0.2))
                }

        # 5. Execute debate
        # Check if the stock is an active position/watchlist row in positions.csv
        positions_path = os.path.join(_ROOT, "data", "positions.csv")
        trade_plan = None
        if os.path.exists(positions_path):
            try:
                import pandas as pd
                df_positions = pd.read_csv(positions_path)
                if not df_positions.empty and "Symbol" in df_positions.columns:
                    row_match = df_positions[df_positions["Symbol"].str.upper() == symbol.upper()]
                    if not row_match.empty:
                        position_row = row_match.iloc[0].to_dict()
                        status_str = str(position_row.get("Status")).strip().upper()
                        if status_str in ("OPEN", "BOUGHT", "HELD"):
                            entry_price = position_row.get("Entry_Price")
                            if entry_price and not pd.isna(entry_price):
                                entry_min = float(entry_price)
                                entry_max = round(entry_min / 0.99 * 1.005, 2)
                                stop_loss = float(position_row.get("Current_SL")) if not pd.isna(position_row.get("Current_SL")) else 0
                                target_1 = float(position_row.get("Target_1")) if not pd.isna(position_row.get("Target_1")) else 0
                                target_2 = float(position_row.get("Target_2")) if not pd.isna(position_row.get("Target_2")) else 0
                                
                                risk = (entry_min + entry_max) / 2 - stop_loss
                                reward = target_2 - (entry_min + entry_max) / 2
                                rr_ratio = round(reward / risk, 1) if risk > 0 else 0
                                
                                trade_plan = {
                                    "setup_type": position_row.get("Setup", "—"),
                                    "entry_zone_min": entry_min,
                                    "entry_zone_max": entry_max,
                                    "stop_loss": stop_loss,
                                    "target_1": target_1,
                                    "target_2": target_2,
                                    "rr_ratio": rr_ratio,
                                    "status": status_str
                                }
                                logger.info("[server/debate] Found locked trade plan for %s: %s", symbol, trade_plan)
            except Exception as pos_exc:
                logger.warning("[server/debate] Failed to read positions.csv: %s", pos_exc)

        try:
            from core.debate_orchestrator import run_adversarial_debate
        except ImportError:
            from debate_orchestrator import run_adversarial_debate
 
        result = run_adversarial_debate(
            symbol=symbol,
            technicals=tech,
            recent_news=stock_news,
            market_context=market_context,
            sector=get_sector(symbol),
            override_config=override_config,
            force_refresh=force,
            check_only=check_only,
            trade_plan=trade_plan
        )
 
        return jsonify(result)
    except Exception as exc:
        logger.error("[server/debate] Failed for %s: %s", symbol, exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/debate/config", methods=["GET", "POST"])
def api_debate_config():
    """GET debate configuration; POST to save changes."""
    try:
        from core.debate_orchestrator import load_debate_config, save_debate_config
    except ImportError:
        from debate_orchestrator import load_debate_config, save_debate_config

    try:
        if request.method == "POST":
            body = request.get_json(force=True, silent=True) or {}
            save_debate_config(body)
            return jsonify({"status": "ok", "config": body})
        return jsonify({"status": "ok", "config": load_debate_config()})
    except Exception as exc:
        logger.error("[server/debate/config] %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


def process_single_stock(stock, df_sym, sector_pulse, filters):
    symbol = stock["symbol"]
    try:
        # Call fetch_stock_technicals passing the pre-fetched df_sym
        tech = fetch_stock_technicals(symbol, df=df_sym)
        if not tech:
            return {"status": "error", "symbol": symbol, "reason": "No technical indicators available"}

        passed, reasons, verdict, regime_mult = apply_risk_filters(symbol, tech, sector_pulse=sector_pulse, thresholds=filters)
        if not passed:
            return {
                "status": "filtered",
                "symbol": symbol,
                "name": stock.get("name", symbol),
                "reasons": reasons,
                "verdict": verdict,
                "index_membership": get_index_membership(symbol),
                "regime_multiplier": regime_mult,
            }

        plan = calculate_trade_plan(tech)
        entry_mid = (plan.get("entry_zone_min", 0) + plan.get("entry_zone_max", 0)) / 2
        rr_raw    = calculate_rr({"price": entry_mid, "target": plan.get("target_2", 0), "sl": plan.get("stop_loss", 0)})
        earn_days_until, earn_date_str, earn_source = fetch_earnings_date(symbol)
        
        setup_grade = "C"
        setup_score = 0.0
        grading_breakdown = {}
        expiry_info = {}

        if grade_setup and expiry_context:
            try:
                g_res = grade_setup(tech, plan)
                setup_grade = g_res.get("grade", "C")
                setup_score = g_res.get("score", 0.0)
                grading_breakdown = g_res.get("breakdown", {})
                
                is_fno = "F&O" in get_index_membership(symbol)
                expiry_info = expiry_context(is_fno=is_fno, grade=setup_grade)
            except Exception as grading_err:
                logger.warning("[scan] Grading failed for %s: %s", symbol, grading_err)

        # Fill-time confirmation: granular NML timing state (same helper the ENTRY-READY alert uses)
        try:
            from core.risk_filters import evaluate_nml_logic
        except ImportError:
            from core_risk_filters import evaluate_nml_logic
        try:
            timing_status, timing_reason = evaluate_nml_logic(symbol, tech)
        except Exception:
            timing_status, timing_reason = "UNKNOWN", ""

        return {
            "status":     "success",
            "symbol":     symbol,
            "name":       stock.get("name", symbol),
            "price":      tech["price"],
            "change_pct": tech["change_pct"],
            "rsi":        tech["rsi"],
            "ema20":      tech["ema20"],
            "macd":       tech["macd"],
            "macd_crossover_days_ago": tech.get("macd_crossover_days_ago", -1),
            "vol_ratio":  tech["volume_ratio"],
            "vol_ratios_5d": tech.get("vol_ratios_5d", []),
            "avg_volume_20d": tech.get("avg_volume_20d", 0),
            "entry_min":  plan.get("entry_zone_min", 0),
            "entry_max":  plan.get("entry_zone_max", 0),
            "target_1":   plan.get("target_1", 0),
            "target_2":   plan.get("target_2", 0),
            "sl":         plan.get("stop_loss", 0),
            "rr":         rr_raw if isinstance(rr_raw, str) else plan.get("rr_ratio", "N/A"),
            "setup":      plan.get("setup_type", "—"),
            "setup_grade":       setup_grade,
            "setup_score":       setup_score,
            "grading_breakdown": grading_breakdown,
            "expiry_info":       expiry_info,
            "sector":     get_sector(symbol),
            "verdict":    verdict,
            "reasons":    reasons,
            "regime_multiplier": regime_mult,
            "index_membership": get_index_membership(symbol),
            # checklist-derived tags
            "atr_pct":          tech.get("atr_pct", 0),
            "near_52w_high":    tech.get("near_52w_high", False),
            "dist_52w_pct":     tech.get("dist_52w_pct", 0),
            "ema9_cross_ema21": tech.get("ema9_cross_ema21", "none"),
            "ema9_cross_days_ago": tech.get("ema9_cross_days_ago", -1),
            "rsi_pullback_zone": tech.get("rsi_pullback_zone", False),
            "high_52w":         tech.get("high_52w", 0),
            "weekly_trend":        tech.get("weekly_trend", "UNKNOWN"),
            "base_days":           tech.get("base_days", 0),
            "base_status":         tech.get("base_status", "UNKNOWN"),
            "false_breakout_risk": tech.get("false_breakout_risk", "LOW"),
            "false_breakout_desc": tech.get("false_breakout_desc", ""),
            "timing_state":        timing_status,
            "timing_reason":       timing_reason,
            "earnings_days_until": earn_days_until,
            "earnings_date":       earn_date_str,
            "earnings_source":     earn_source,
            "debate":              fetch_cached_debate_verdict(symbol),
            "shareholding":        fetch_screener_shareholding(symbol),
            # grading factor raw inputs (so dashboard can show per-factor breakdown)
            "adx":                 tech.get("adx", 0),
            "return_20d":          tech.get("return_20d", 0),
            "ema50":               tech.get("ema50", 0),
        }
    except Exception as exc:
        logger.warning("[scan] %s skipped in thread: %s", symbol, exc)
        return {"status": "error", "symbol": symbol, "reason": str(exc)}


def refresh_trade_levels(df_positions, idx, tech):
    # Plan levels (Entry/SL/T1/T2) are LOCKED at first scan — a re-scan must NOT
    # recompute them. We still recompute `plan` here only to feed setup grading
    # against today's technicals (monitoring, not the trade plan).
    plan = calculate_trade_plan(tech, is_refresh=True)

    setup_grade = "C"
    setup_score = 0.0
    if grade_setup and expiry_context:
        try:
            g_res = grade_setup(tech, plan)
            setup_grade = g_res.get("grade", "C")
            setup_score = g_res.get("score", 0.0)
            
            is_fno = "F&O" in get_index_membership(str(df_positions.at[idx, "Symbol"]))
            exp_info = expiry_context(is_fno=is_fno, grade=setup_grade)
            if exp_info:
                df_positions.at[idx, "Expiry_Multiplier"] = exp_info.get("multiplier", "")
                df_positions.at[idx, "Expiry_Reason"] = exp_info.get("reason", "")
        except Exception as grading_err:
            logger.warning("[scan/cadence] Grading failed for absent %s: %s", df_positions.at[idx, "Symbol"], grading_err)
            
    df_positions.at[idx, "Setup_Grade"] = setup_grade
    df_positions.at[idx, "Setup_Score"] = setup_score
    df_positions.at[idx, "Absent_Cycles"] = 0


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

        global _scan_filters
        body    = request.get_json(force=True, silent=True) or {}
        filters = body.get("filters", {})
        if isinstance(filters, dict) and filters:
            _scan_filters = {**_scan_filters, **filters}
            _save_schedule_config()
        top_n   = int(filters.get("top_n", 30))
        logger.info("[scan] filters=%s top_n=%d", filters, top_n)

        # Warm NSE calendar calendar cache on main thread to avoid parallel race condition
        try:
            fetch_earnings_date("RELIANCE")
        except Exception as e:
            logger.warning("[scan] Failed to warm NSE calendar: %s", e)

        nifty = fetch_nifty_levels()
        fii   = fetch_fii_dii_flow(days=5)

        chartink_stocks = fetch_chartink_stocks(params=filters)

        if not chartink_stocks:
            brief = _load_latest_brief()
            if brief:
                _hydrate_brief_missing_fields(brief)
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

        # Bulk download 200d history for all symbols to avoid sequential history downloads
        tickers = [NSE_TICKERS.get(s["symbol"].upper(), f"{s['symbol']}.NS") for s in chartink_stocks]
        logger.info("[scan] Bulk downloading 200d history for %d symbols...", len(tickers))
        import yfinance as yf
        try:
            bulk_data = yf.download(
                tickers, period="200d", group_by="ticker",
                progress=False, threads=True, auto_adjust=True
            )
        except Exception as e:
            logger.warning("[scan] Bulk download failed, will fetch individually: %s", e)
            bulk_data = None

        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        # Parallel execution with ThreadPoolExecutor
        max_workers = min(16, len(chartink_stocks)) if chartink_stocks else 1
        logger.info("[scan] Processing %d stocks in parallel with %d workers...", len(chartink_stocks), max_workers)
        
        results_by_idx = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for idx, stock in enumerate(chartink_stocks):
                symbol = stock["symbol"]
                ticker_key = NSE_TICKERS.get(symbol.upper(), f"{symbol}.NS")
                
                # Extract pre-fetched DataFrame for this ticker from bulk download
                df_sym = None
                if bulk_data is not None and not bulk_data.empty:
                    try:
                        if isinstance(bulk_data.columns, pd.MultiIndex):
                            if ticker_key in bulk_data.columns.levels[0]:
                                df_sym = bulk_data[ticker_key].dropna(subset=["Close"])
                        else:
                            df_sym = bulk_data.dropna(subset=["Close"])
                    except Exception as e:
                        logger.debug("[scan] Failed to extract bulk data for %s: %s", symbol, e)
                
                f = executor.submit(
                    process_single_stock,
                    stock=stock,
                    df_sym=df_sym,
                    sector_pulse=sector_pulse,
                    filters=filters
                )
                futures[f] = idx
                
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    res = future.result()
                    results_by_idx[idx] = res
                except Exception as exc:
                    logger.error("Stock index %d raised exception: %s", idx, exc)

        # Assemble ordered lists preserving original volume-sorted order
        scan_results = []
        filtered_out = []
        for idx in sorted(results_by_idx.keys()):
            res = results_by_idx[idx]
            status = res.get("status")
            if status == "success":
                res.pop("status", None)
                scan_results.append(res)
            elif status == "filtered":
                res.pop("status", None)
                filtered_out.append(res)

        # Map existing watchlist symbols to their Entry_Date
        entry_date_map = {}
        positions_path = os.path.join(_ROOT, "data", "positions.csv")
        if os.path.exists(positions_path):
            try:
                df_p = pd.read_csv(positions_path)
                if not df_p.empty and "Symbol" in df_p.columns and "Entry_Date" in df_p.columns:
                    for _, row in df_p.iterrows():
                        sym_upper = str(row["Symbol"]).strip().upper()
                        if pd.notna(row["Entry_Date"]):
                            entry_date_map[sym_upper] = str(row["Entry_Date"])
            except Exception as e:
                logger.warning("[server] Failed to build entry_date_map: %s", e)

        # Attach entry_date to scan_results and filtered_out
        today_str = _now_date()
        for s in scan_results:
            sym_upper = s["symbol"].upper()
            s["entry_date"] = entry_date_map.get(sym_upper, today_str)

        for s in filtered_out:
            sym_upper = s["symbol"].upper()
            s["entry_date"] = entry_date_map.get(sym_upper, today_str)

        # ── Daily Cadence Rules for OPEN watchlist candidates ──
        # Pruning is decided by evaluate_prune() on freshly-recomputed Analysis-tab
        # data points (fetch_stock_technicals) — NOT by scan presence/absence.
        # A stock absent from the scan is still re-judged on its own technicals;
        # if those can't be fetched, it is HELD (never pruned on missing data).
        # Pruned rows are NOT dropped — they get Status="PRUNED" + Prune_Reason +
        # Prune_Date and stay in the CSV for the separate dashboard section.
        positions_path = os.path.join(_ROOT, "data", "positions.csv")
        # OPEN-stock re-evaluation belongs exclusively to Keep & Refresh.
        if False and os.path.exists(positions_path):
            try:
                df_positions = pd.read_csv(positions_path)
                if not df_positions.empty and "Status" in df_positions.columns:
                    _ensure_cols(df_positions, ["Prune_Reason", "Prune_Date",
                                                "Risk_Per_Share", "Rupee_Risk"])
                    csv_updated = False

                    for idx, row in df_positions.iterrows():
                        if str(row["Status"]).strip().upper() != "OPEN":
                            continue

                        sym = str(row["Symbol"]).strip().upper()

                        # Backfill locked risk fields for rows added before this
                        # existed (derived from the already-locked Entry/SL/qty).
                        if _num(df_positions.at[idx, "Risk_Per_Share"]) is None:
                            rps, rupee = position_risk(
                                df_positions.at[idx, "Entry_Price"],
                                df_positions.at[idx, "Current_SL"],
                                df_positions.at[idx, "Quantity"],
                            )
                            if rps:
                                df_positions.at[idx, "Risk_Per_Share"] = rps
                                df_positions.at[idx, "Rupee_Risk"]     = rupee
                                csv_updated = True

                        # Recompute this candidate's OWN Analysis-tab data points.
                        # Prefer today's scan numbers if present (cheap), else fetch.
                        scan_match = next(
                            (s for s in scan_results if s["symbol"].upper() == sym), None
                        )
                        filtered_match = next(
                            (s for s in filtered_out if s["symbol"].upper() == sym), None
                        )
                        tech = scan_match or filtered_match
                        if tech is None:
                            try:
                                tech = fetch_stock_technicals(sym) or {}
                            except Exception as exc:
                                logger.warning("[cadence] tech fetch failed for %s: %s", sym, exc)
                                tech = {}

                        state, reason = evaluate_prune(tech, row.to_dict())

                        if state == "PRUNE":
                            df_positions.at[idx, "Status"]       = "PRUNED"
                            df_positions.at[idx, "Prune_Reason"] = reason
                            df_positions.at[idx, "Prune_Date"]   = _now_date()
                            csv_updated = True
                            logger.info("[cadence] PRUNED %s — %s", sym, reason)
                            continue

                        # RE-EVALUATE: candidate stays. Plan levels (Entry/SL/T1/T2)
                        # are LOCKED at first scan — never recompute on a re-scan. If
                        # it also passed today's fresh scan, OVERRIDE the scan card's
                        # freshly-computed levels WITH the locked CSV values so the
                        # scan card == the watchlist row. Only monitoring fields
                        # (grade/score/expiry) are refreshed in the CSV.
                        if scan_match:
                            lk_entry = _num(df_positions.at[idx, "Entry_Price"])
                            lk_sl    = _num(df_positions.at[idx, "Current_SL"])
                            lk_t1    = _num(df_positions.at[idx, "Target_1"])
                            lk_t2    = _num(df_positions.at[idx, "Target_2"])
                            if lk_entry:
                                scan_match["entry_min"] = lk_entry
                                scan_match["entry_max"] = round(lk_entry / 0.99 * 1.005, 2)
                            if lk_sl:
                                scan_match["sl"] = lk_sl
                            if lk_t1:
                                scan_match["target_1"] = lk_t1
                            if lk_t2:
                                scan_match["target_2"] = lk_t2
                            if lk_entry and lk_t2 and lk_sl:
                                entry_mid = (scan_match["entry_min"] + scan_match["entry_max"]) / 2
                                scan_match["rr"] = calculate_rr(
                                    {"price": entry_mid, "target": lk_t2, "sl": lk_sl}
                                )

                            df_positions.at[idx, "Setup_Grade"] = scan_match.get("setup_grade", df_positions.at[idx, "Setup_Grade"])
                            df_positions.at[idx, "Setup_Score"] = scan_match.get("setup_score", df_positions.at[idx, "Setup_Score"])
                            if scan_match.get("expiry_info"):
                                df_positions.at[idx, "Expiry_Multiplier"] = scan_match["expiry_info"].get("multiplier", "")
                                df_positions.at[idx, "Expiry_Reason"]     = scan_match["expiry_info"].get("reason", "")
                            elif scan_match.get("expiry_multiplier") is not None:
                                df_positions.at[idx, "Expiry_Multiplier"] = scan_match.get("expiry_multiplier", "")
                                df_positions.at[idx, "Expiry_Reason"]     = scan_match.get("expiry_reason", "")
                            csv_updated = True
                            logger.info("[cadence] Locked plan re-used for re-evaluate candidate %s", sym)

                    if csv_updated:
                        _save_positions_csv(df_positions, positions_path)
                        logger.info("[cadence] Applied Analysis-tab prune rules to positions.csv")
            except Exception as e:
                logger.error("[cadence] Error applying prune rules: %s", e)

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


@app.route("/api/plan/<symbol>")
def api_plan(symbol: str):
    """
    Single-ticker trade plan computed by the canonical server logic
    (core.trade_plan.calculate_trade_plan). Powers the Trading-tab SL/Target card
    so it shows the same numbers as the Watchlist for any ticker.
    """
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return jsonify({"status": "error", "message": "symbol required"}), 400
    try:
        tech = fetch_stock_technicals(symbol)
        if not tech:
            return jsonify({"status": "error", "message": f"No data for {symbol}"}), 404

        plan      = calculate_trade_plan(tech)
        entry_mid = (plan.get("entry_zone_min", 0) + plan.get("entry_zone_max", 0)) / 2
        rr_raw    = calculate_rr({"price": entry_mid, "target": plan.get("target_2", 0), "sl": plan.get("stop_loss", 0)})

        # Evaluate risk filters for verdict and regime multiplier
        try:
            sector_pulse = fetch_sector_pulse()
        except Exception:
            sector_pulse = {}
        passed, reasons, verdict, regime_mult = apply_risk_filters(symbol, tech, sector_pulse=sector_pulse)

        earn_days_until, earn_date_str, earn_source = fetch_earnings_date(symbol)

        setup_grade = "C"
        setup_score = 0.0
        grading_breakdown = {}
        expiry_info = {}

        if grade_setup and expiry_context:
            try:
                g_res = grade_setup(tech, plan)
                setup_grade = g_res.get("grade", "C")
                setup_score = g_res.get("score", 0.0)
                grading_breakdown = g_res.get("breakdown", {})
                
                is_fno = "F&O" in get_index_membership(symbol)
                expiry_info = expiry_context(is_fno=is_fno, grade=setup_grade)
            except Exception as grading_err:
                logger.warning("[plan] Grading failed for %s: %s", symbol, grading_err)

        return jsonify({
            "status":     "success",
            "symbol":     symbol,
            "price":      tech.get("price", 0),
            "change_pct": tech.get("change_pct", 0),
            "vol_ratio":  tech.get("volume_ratio", 1.0),
            "vol_ratios_5d": tech.get("vol_ratios_5d", []),
            "avg_volume_20d": tech.get("avg_volume_20d", 0),
            "atr":        tech.get("atr", 0),
            "atr_pct":    tech.get("atr_pct", 0),
            "rsi":        tech.get("rsi", 0),
            "ema20":      tech.get("ema20", 0),
            "ema50":      tech.get("ema50", 0),
            "setup":      plan.get("setup_type", "—"),
            "setup_grade":       setup_grade,
            "setup_score":       setup_score,
            "grading_breakdown": grading_breakdown,
            "expiry_info":       expiry_info,
            "entry_min":  plan.get("entry_zone_min", 0),
            "entry_max":  plan.get("entry_zone_max", 0),
            "sl":         plan.get("stop_loss", 0),
            "target_1":   plan.get("target_1", 0),
            "target_2":   plan.get("target_2", 0),
            "rr":         rr_raw if isinstance(rr_raw, str) else plan.get("rr_ratio", "N/A"),
            "weekly_trend":        tech.get("weekly_trend", "UNKNOWN"),
            "base_days":           tech.get("base_days", 0),
            "base_status":         tech.get("base_status", "UNKNOWN"),
            "false_breakout_risk": tech.get("false_breakout_risk", "LOW"),
            "false_breakout_desc": tech.get("false_breakout_desc", ""),
            "earnings_days_until": earn_days_until,
            "earnings_date":       earn_date_str,
            "earnings_source":     earn_source,
            "index_membership":    get_index_membership(symbol),
            "verdict":             verdict,
            "reasons":             reasons,
            "regime_multiplier":   regime_mult,
            "debate":              fetch_cached_debate_verdict(symbol),
            "shareholding":        fetch_screener_shareholding(symbol),
            # grading factor raw inputs
            "adx":                 tech.get("adx", 0),
            "return_20d":          tech.get("return_20d", 0),
            "relative_strength":   tech.get("relative_strength", 0),
        })
    except Exception as exc:
        logger.error("[plan] %s failed: %s", symbol, exc)
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
            data = json.load(f)
        # Refresh sector + hydrate any fields the brief was written before (e.g.
        # earnings_*) so the dashboard doesn't BLOCK on Unresolved earnings.
        _hydrate_brief_missing_fields(data)
        return jsonify({"found": True, "data": data})
    return jsonify({"found": False})


def _append_rows_to_csv(path: str, rows: list) -> tuple:
    """
    Append rows to positions.csv preserving column alignment + skipping symbols
    that already have an OPEN position. Pandas auto-fills missing columns with
    NaN so the existing schema stays intact.

    Returns (added_rows, skipped_symbols) tuple.
    """
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
            # Lock the per-position risk at first scan from this row's plan, so it
            # stays fixed (auditable) instead of being recomputed live each day.
            rps, rupee = position_risk(
                row.get("Entry_Price"), row.get("Current_SL"), row.get("Quantity", 0)
            )
            row["Risk_Per_Share"] = rps
            row["Rupee_Risk"]     = rupee
            added.append(row)
            open_syms.add(row["Symbol"])   # also dedupe within this single batch

    if added:
        new_df   = pd.DataFrame(added)
        combined = pd.concat([existing, new_df], ignore_index=True) if not existing.empty else new_df
        combined.to_csv(path, index=False)

    return added, skipped


def _ensure_cols(df, cols: list, default="") -> None:
    """Add missing columns to df in-place with the given default value."""
    for col in cols:
        if col not in df.columns:
            df[col] = pd.Series([default] * len(df), dtype=object)


def _num(val):
    """Coerce a possibly-blank/NaN CSV value to a float, else None (→ JSON null)."""
    try:
        if val is None or val == "" or (isinstance(val, float) and pd.isna(val)):
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_reasons(val):
    """Parse a Cur_Reasons JSON cell into a list; tolerate blanks/legacy strings."""
    if val is None or val == "" or (isinstance(val, float) and pd.isna(val)):
        return []
    try:
        parsed = json.loads(val)
        return parsed if isinstance(parsed, list) else [str(parsed)]
    except (TypeError, ValueError):
        return [str(val)]


def _save_positions_csv(df, path: str) -> None:
    """Atomically write positions DataFrame to CSV via a tmp file."""
    tmp = f"{path}.tmp"
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)


def fetch_cached_debate_verdict(symbol: str) -> dict:
    """Find the latest cached debate file for symbol, return parsed dict or empty dict."""
    try:
        CACHE_DIR = os.path.join(_ROOT, "data", "due_diligence")
        if not os.path.exists(CACHE_DIR):
            return {}
            
        symbol = symbol.strip().upper()
        files = os.listdir(CACHE_DIR)
        matches = [f for f in files if f.startswith(f"{symbol}_") and f.endswith(".json")]
        if not matches:
            return {}
            
        # Sort to get the latest by date string in filename
        matches.sort()
        latest_file = matches[-1]
        
        path = os.path.join(CACHE_DIR, latest_file)
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
            return {
                "verdict": data.get("verdict"),
                "conviction_score": data.get("conviction_score"),
                "judge_rationale": data.get("judge_rationale"),
                "top_red_flags": data.get("top_red_flags", []),
                "top_triggers": data.get("top_triggers", []),
                "bull_case": data.get("bull_case"),
                "bear_case": data.get("bear_case"),
            }
    except Exception as e:
        logger.warning("[debate_verdict] Failed to read cached debate for %s: %s", symbol, e)
    return {}


def _extract_snapshot(r) -> dict:
    """Extract buy-snapshot and post-mortem fields from a positions DataFrame row."""
    def _safe_float(key):
        v = r.get(key)
        return float(v) if pd.notna(v) and str(v).strip() else 0.0
    def _safe_int(key):
        v = r.get(key)
        return int(float(v)) if pd.notna(v) and str(v).strip() else 0
    def _safe_str(key):
        v = r.get(key)
        return str(v) if pd.notna(v) else ""
    return {
        "buy_weekly_trend":        str(r.get("Buy_Weekly_Trend", "UNKNOWN")),
        "buy_base_days":           _safe_int("Buy_Base_Days"),
        "buy_base_status":         str(r.get("Buy_Base_Status", "UNKNOWN")),
        "buy_false_breakout_risk": str(r.get("Buy_False_Breakout_Risk", "LOW")),
        "buy_false_breakout_desc": str(r.get("Buy_False_Breakout_Desc", "")),
        "buy_rsi":                 _safe_float("Buy_RSI"),
        "buy_atr_pct":             _safe_float("Buy_ATR_Pct"),
        "buy_vol_ratio":           _safe_float("Buy_Vol_Ratio"),
        "post_mortem_why":         _safe_str("Post_Mortem_Why"),
        "post_mortem_maximize":    _safe_str("Post_Mortem_Maximize"),
    }


@app.route("/api/positions/pruned")
def api_positions_pruned():
    """Return candidates that were pruned from analysis (Status == PRUNED)."""
    path = os.path.join(_ROOT, "data", "positions.csv")
    if not os.path.exists(path):
        return jsonify({"pruned": []})
    df = pd.read_csv(path)
    if df.empty or "Status" not in df.columns:
        return jsonify({"pruned": []})

    pruned = df[df["Status"].astype(str).str.upper() == "PRUNED"].copy()
    # NaN -> "" for clean JSON
    pruned = pruned.where(pd.notna(pruned), "")
    records = pruned.to_dict(orient="records")
    # newest first by Prune_Date when available
    records.sort(key=lambda r: str(r.get("Prune_Date", "")), reverse=True)
    return jsonify({"pruned": records, "count": len(records)})


@app.route("/api/positions/restore", methods=["POST"])
def api_positions_restore():
    """Move a PRUNED candidate back to OPEN (re-evaluate) state."""
    data = request.get_json(force=True, silent=True) or {}
    symbol = str(data.get("symbol", "")).strip().upper()
    if not symbol:
        return jsonify({"status": "error", "message": "symbol required"}), 400

    path = os.path.join(_ROOT, "data", "positions.csv")
    if not os.path.exists(path):
        return jsonify({"status": "error", "message": "no positions file"}), 404

    df = pd.read_csv(path)
    df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
    mask = (df["Symbol"] == symbol) & (df["Status"].astype(str).str.upper() == "PRUNED")
    if not mask.any():
        return jsonify({"status": "error", "message": f"{symbol} not in pruned list"}), 404

    idx = df[mask].index[-1]
    df.at[idx, "Status"]       = "OPEN"
    df.at[idx, "Prune_Reason"] = ""
    df.at[idx, "Prune_Date"]   = ""
    _save_positions_csv(df, path)
    logger.info("[positions/restore] %s restored to OPEN", symbol)
    return jsonify({"status": "ok", "message": f"{symbol} restored to analysis"})


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
            "Setup_Grade":  data.get("setup_grade", ""),
            "Setup_Score":  data.get("setup_score", ""),
            "Expiry_Multiplier": data.get("expiry_info", {}).get("multiplier", "") if data.get("expiry_info") else data.get("expiry_multiplier", ""),
            "Expiry_Reason": data.get("expiry_info", {}).get("reason", "") if data.get("expiry_info") else data.get("expiry_reason", ""),
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

        added, skipped = _add_stocks_to_positions(stocks)

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


def _add_stocks_to_positions(stocks):
    """Persist passed scan candidates as OPEN Analysis positions."""
    path = os.path.join(_ROOT, "data", "positions.csv")
    os.makedirs(os.path.join(_ROOT, "data"), exist_ok=True)
    rows = []
    for s in stocks:
        if not s.get("symbol"):
            continue
        rows.append({
            "Symbol": s["symbol"],
            "Name": s.get("name", s["symbol"]),
            "Entry_Price": s.get("entry_min", s.get("price", 0)),
            "Quantity": 1,
            "Target_1": s.get("target_1", 0),
            "Target_2": s.get("target_2", 0),
            "Current_SL": s.get("sl", 0),
            "Setup": s.get("setup", ""),
            "Entry_Date": _now_date(),
            "Status": "OPEN",
            "Setup_Grade": s.get("setup_grade", ""),
            "Setup_Score": s.get("setup_score", ""),
            "Expiry_Multiplier": s.get("expiry_info", {}).get("multiplier", "") if s.get("expiry_info") else s.get("expiry_multiplier", ""),
            "Expiry_Reason": s.get("expiry_info", {}).get("reason", "") if s.get("expiry_info") else s.get("expiry_reason", ""),
        })
    added, skipped = _append_rows_to_csv(path, rows)
    for row in added:
        gtt_id = place_gtt(
            symbol=row["Symbol"], qty=int(row["Quantity"]),
            last_price=float(row["Entry_Price"]),
            sl=float(row["Current_SL"]), target=float(row["Target_2"]),
        )
        if gtt_id:
            _write_gtt_id(path, row["Symbol"], gtt_id)
    return added, skipped


def run_get_stocks_job(filters=None):
    """Run the canonical scan, persist it, then add every passed stock."""
    _load_schedule_config()
    effective_filters = filters if isinstance(filters, dict) and filters else dict(_scan_filters)
    logger.info("[get-stocks] Starting scheduled scan")
    logger.info("[get-stocks] Using persisted scan/risk filters: %s", effective_filters)
    with app.test_request_context("/api/scan", method="POST", json={"filters": effective_filters}):
        response = api_scan()
    if isinstance(response, tuple):
        response, status_code = response[0], response[1]
        if status_code >= 400:
            result = response.get_json() or {}
            raise RuntimeError(result.get("message", "Scheduled scan failed"))
    result = response.get_json() or {}
    if result.get("status") != "success" or result.get("source") == "last_session":
        logger.info("[get-stocks] No fresh passed stocks to add")
        outcome = {"status": result.get("status", "no_results"), "added": 0, "skipped": 0}
        _tg_send(
            f"<b>Get Stocks completed</b>\n"
            f"Time: {_now_date()} {_now_time()} IST\n"
            f"No fresh passed stocks were found.\n"
            f"Dashboard scan cache checked; database unchanged."
        )
        return outcome
    added, skipped = _add_stocks_to_positions(result.get("stocks", []))
    logger.info("[get-stocks] Completed: %d added, %d skipped", len(added), len(skipped))
    outcome = {"status": "success", "added": len(added), "skipped": len(skipped),
               "scan_date": result.get("date")}
    added_symbols = ", ".join(row["Symbol"] for row in added) or "None"
    _tg_send(
        f"<b>Get Stocks completed</b>\n"
        f"Time: {_now_date()} {_now_time()} IST\n"
        f"Passed: {len(result.get('stocks', []))}\n"
        f"Added to Analysis: {len(added)}\n"
        f"Already OPEN: {len(skipped)}\n"
        f"Added symbols: {added_symbols}\n"
        f"Scan cache and positions database refreshed."
    )
    return outcome


@app.route("/api/schedule/maintenance", methods=["GET", "POST"])
def api_schedule_maintenance():
    global _maintenance_time, _scheduler
    config_path = os.path.join(_ROOT, "data", "schedule_config.json")
    
    if request.method == "POST":
        try:
            body = request.get_json(force=True, silent=True) or {}
            time_str = body.get("time", "").strip()
            
            # Validate time_str (format HH:MM)
            import re
            if not re.match(r"^\d{2}:\d{2}$", time_str):
                return jsonify({"status": "error", "message": "Invalid time format. Expected HH:MM"}), 400
                
            h, m = time_str.split(":")
            if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                return jsonify({"status": "error", "message": "Invalid time bounds. Hour 0-23, Minute 0-59"}), 400
                
            _maintenance_time = time_str
            
            _save_schedule_config()
                
            # Reschedule in scheduler if running
            if _scheduler:
                try:
                    from apscheduler.triggers.cron import CronTrigger
                    import pytz
                    ist = pytz.timezone("Asia/Kolkata")
                    job = _scheduler.get_job("watchlist_maintenance")
                    trigger = CronTrigger(day_of_week="mon-fri", hour=h, minute=m, timezone=ist)
                    if job:
                        job.reschedule(trigger=trigger)
                        logger.info("[server] Rescheduled Keep & refresh Scan job to %s IST", _maintenance_time)
                    else:
                        # Job does not exist yet, create it
                        _scheduler.add_job(
                            run_morning_maintenance,
                            trigger,
                            id="watchlist_maintenance",
                            max_instances=1,
                            coalesce=True,
                        )
                        logger.info("[server] Created Keep & refresh Scan job at %s IST", _maintenance_time)
                except Exception as e:
                    logger.warning("[server] Failed to reschedule job: %s", e)
                    
            return jsonify({"status": "ok", "time": _maintenance_time})
        except Exception as exc:
            logger.error("[server] Failed to update maintenance schedule: %s", exc)
            return jsonify({"status": "error", "message": str(exc)}), 500
            
    # GET
    _load_maintenance_time()
    return jsonify({"status": "ok", "time": _maintenance_time})


@app.route("/api/schedule/get-stocks", methods=["GET", "POST"])
def api_schedule_get_stocks():
    global _get_stocks_time, _scan_filters, _scheduler
    if request.method == "POST":
        try:
            body = request.get_json(force=True, silent=True) or {}
            time_str = body.get("time", "").strip()
            import re
            if not re.match(r"^\d{2}:\d{2}$", time_str):
                return jsonify({"status": "error", "message": "Invalid time format. Expected HH:MM"}), 400
            h, m = time_str.split(":")
            if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                return jsonify({"status": "error", "message": "Invalid time bounds"}), 400
            _get_stocks_time = time_str
            supplied_filters = body.get("filters")
            if isinstance(supplied_filters, dict) and supplied_filters:
                _scan_filters = {**_scan_filters, **supplied_filters}
            _save_schedule_config()
            if _scheduler:
                from apscheduler.triggers.cron import CronTrigger
                import pytz
                ist = pytz.timezone("Asia/Kolkata")
                trigger = CronTrigger(hour=h, minute=m, timezone=ist)
                job = _scheduler.get_job("get_stocks")
                if job:
                    job.reschedule(trigger=trigger)
                else:
                    _scheduler.add_job(run_get_stocks_job, trigger, id="get_stocks",
                                       max_instances=1, coalesce=True)
                    job = _scheduler.get_job("get_stocks")

                # A cron trigger set after second 00 of the selected minute would
                # otherwise wait until tomorrow. Treat that as an immediate run.
                now = datetime.now(ist)
                runs_immediately = now.hour == int(h) and now.minute == int(m)
                if runs_immediately and job:
                    job.modify(next_run_time=now + timedelta(seconds=1))
                    logger.info(
                        "[get-stocks] Schedule set during %s; queued immediate run",
                        time_str,
                    )
            else:
                runs_immediately = False
            return jsonify({
                "status": "ok",
                "time": _get_stocks_time,
                "runs_immediately": runs_immediately,
            })
        except Exception as exc:
            logger.error("[server] Failed to update Get Stocks schedule: %s", exc)
            return jsonify({"status": "error", "message": str(exc)}), 500
    _load_schedule_config()
    return jsonify({"status": "ok", "time": _get_stocks_time, "filters": _scan_filters})


@app.route("/api/schedule/get-stocks/run", methods=["POST"])
def api_schedule_get_stocks_run():
    try:
        return jsonify(run_get_stocks_job())
    except Exception as exc:
        logger.error("[get-stocks] Manual trigger failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/schedule/maintenance/run", methods=["POST"])
def api_schedule_maintenance_run():
    try:
        # Trigger morning maintenance script directly
        result = run_morning_maintenance(manual_trigger=True)
        return jsonify(result)
    except Exception as exc:
        logger.error("[server] Manual trigger failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/positions/remove", methods=["POST"])
def api_positions_remove():
    """Remove an OPEN position (symbol) from the active watchlist."""
    try:
        data = request.get_json(force=True, silent=True)
        if not data or not data.get("symbol"):
            return jsonify({"status": "error", "message": "symbol required"}), 400

        symbol = data["symbol"]
        path = os.path.join(_ROOT, "data", "positions.csv")
        if not os.path.exists(path):
            return jsonify({"status": "error", "message": "positions.csv does not exist"}), 404

        df = pd.read_csv(path)
        if df.empty:
            return jsonify({"status": "error", "message": "watchlist is empty"}), 400

        # Check if the symbol is in open positions (case-insensitive & stripped)
        open_mask = (df["Symbol"].astype(str).str.strip().str.upper() == symbol.strip().upper()) & (df["Status"].fillna("").astype(str).str.strip().str.upper() == "OPEN")
        if not open_mask.any():
            return jsonify({"status": "error", "message": f"{symbol} not found in active watchlist"}), 404

        # Remove matching open positions
        df = df[~open_mask]
        _save_positions_csv(df, path)
        logger.info("[positions] Removed %s from active watchlist", symbol)
        return jsonify({"status": "ok", "message": f"Removed {symbol} from watchlist"})

    except Exception as exc:
        logger.error("[positions/remove] %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/positions/buy", methods=["POST"])
def api_positions_buy():
    """Transition an OPEN watchlist position to BOUGHT status or directly create one if not found."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        symbol = data.get("symbol")
        if not symbol:
            return jsonify({"status": "error", "message": "symbol required"}), 400

        symbol = symbol.strip().upper()
        path = os.path.join(_ROOT, "data", "positions.csv")
        os.makedirs(os.path.join(_ROOT, "data"), exist_ok=True)

        if os.path.exists(path):
            df = pd.read_csv(path)
        else:
            df = pd.DataFrame(columns=[
                "Symbol", "Name", "Entry_Price", "Quantity", "Target_1", "Target_2",
                "Current_SL", "Setup", "Entry_Date", "Status", "Setup_Grade", "Setup_Score",
                "Expiry_Multiplier", "Expiry_Reason", "gtt_id"
            ])

        # Standardize strings for lookup
        if not df.empty:
            df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
            df["Status"] = df["Status"].fillna("").astype(str).str.strip().str.upper()

        # Locate the OPEN position for the symbol
        idx = None
        if not df.empty:
            mask = (df["Symbol"] == symbol) & (df["Status"] == "OPEN")
            if mask.any():
                idx = df[mask].index[-1]  # take the most recent open setup

        # Fetch real-time technicals
        tech = fetch_stock_technicals(symbol) or {}

        default_entry = float(df.at[idx, "Entry_Price"]) if (idx is not None) else float(tech.get("price", 0) or 0)
        market_price = float(tech.get("price", 0) or default_entry)
        entry_price = float(data.get("entry_price", market_price) or market_price)

        _ensure_cols(df, [
            "Buy_Weekly_Trend", "Buy_Base_Days", "Buy_Base_Status",
            "Buy_False_Breakout_Risk", "Buy_False_Breakout_Desc",
            "Buy_RSI", "Buy_ATR_Pct", "Buy_Vol_Ratio",
            "Setup_Grade", "Setup_Score", "Expiry_Multiplier", "Expiry_Reason",
            "gtt_id", "Risk_Per_Share", "Rupee_Risk"
        ])

        # Live setup grading and expiry sizing at execution time
        setup_grade = data.get("setup_grade") or "C"
        setup_score = float(data.get("setup_score") or 0.0)
        exp_mult = float(data.get("expiry_multiplier") or 1.0)
        exp_reason = data.get("expiry_reason") or "No expiry logic available"

        if (not data.get("setup_grade")) and grade_setup and expiry_context:
            try:
                plan = calculate_trade_plan(tech)
                g_res = grade_setup(tech, plan)
                setup_grade = g_res.get("grade", "C")
                setup_score = g_res.get("score", 0.0)
                is_fno = "F&O" in get_index_membership(symbol)
                exp_res = expiry_context(is_fno=is_fno, grade=setup_grade)
                exp_mult = exp_res.get("multiplier", 1.0)
                exp_reason = exp_res.get("reason", "")
            except Exception as grading_err:
                logger.warning("[positions/buy] Live grading failed for %s: %s", symbol, grading_err)

        if idx is not None:
            # Update status & capture snapshot
            df.at[idx, "Status"] = "BOUGHT"
            df.at[idx, "Entry_Price"] = entry_price
            df.at[idx, "Entry_Date"] = _now_date()
            if data.get("sl"):
                df.at[idx, "Current_SL"] = float(data["sl"])
            if data.get("target_1"):
                df.at[idx, "Target_1"] = float(data["target_1"])
            if data.get("target_2"):
                df.at[idx, "Target_2"] = float(data["target_2"])
            if data.get("quantity"):
                df.at[idx, "Quantity"] = float(data["quantity"])

            # Populate snapshot columns
            df.at[idx, "Buy_Weekly_Trend"] = str(tech.get("weekly_trend", "UNKNOWN"))
            df.at[idx, "Buy_Base_Days"] = int(tech.get("base_days", 0))
            df.at[idx, "Buy_Base_Status"] = str(tech.get("base_status", "UNKNOWN"))
            df.at[idx, "Buy_False_Breakout_Risk"] = str(tech.get("false_breakout_risk", "LOW"))
            df.at[idx, "Buy_False_Breakout_Desc"] = str(tech.get("false_breakout_desc", ""))
            df.at[idx, "Buy_RSI"] = float(tech.get("rsi", 0) or 0)
            df.at[idx, "Buy_ATR_Pct"] = float(tech.get("atr_pct", 0) or 0)
            df.at[idx, "Buy_Vol_Ratio"] = float(tech.get("volume_ratio", 0) or tech.get("vol_ratio", 0) or 0)
            df.at[idx, "Setup_Grade"] = setup_grade
            df.at[idx, "Setup_Score"] = setup_score
            df.at[idx, "Expiry_Multiplier"] = exp_mult
            df.at[idx, "Expiry_Reason"] = exp_reason
            # Re-lock per-position risk to the actual bought entry/SL/qty.
            rps, rupee = position_risk(
                df.at[idx, "Entry_Price"], df.at[idx, "Current_SL"], df.at[idx, "Quantity"]
            )
            df.at[idx, "Risk_Per_Share"] = rps
            df.at[idx, "Rupee_Risk"]     = rupee
        else:
            # Create a new BOUGHT row directly (direct execution)
            new_row = {
                "Symbol": symbol,
                "Name": data.get("name", symbol),
                "Entry_Price": entry_price,
                "Quantity": float(data.get("quantity", 1)),
                "Target_1": float(data.get("target_1", entry_price * 1.05)),
                "Target_2": float(data.get("target_2", entry_price * 1.10)),
                "Current_SL": float(data.get("sl", entry_price * 0.95)),
                "Setup": data.get("setup", "SWING"),
                "Entry_Date": _now_date(),
                "Status": "BOUGHT",
                "Setup_Grade": setup_grade,
                "Setup_Score": setup_score,
                "Expiry_Multiplier": exp_mult,
                "Expiry_Reason": exp_reason,
                "Buy_Weekly_Trend": str(tech.get("weekly_trend", "UNKNOWN")),
                "Buy_Base_Days": int(tech.get("base_days", 0)),
                "Buy_Base_Status": str(tech.get("base_status", "UNKNOWN")),
                "Buy_False_Breakout_Risk": str(tech.get("false_breakout_risk", "LOW")),
                "Buy_False_Breakout_Desc": str(tech.get("false_breakout_desc", "")),
                "Buy_RSI": float(tech.get("rsi", 0) or 0),
                "Buy_ATR_Pct": float(tech.get("atr_pct", 0) or 0),
                "Buy_Vol_Ratio": float(tech.get("volume_ratio", 0) or tech.get("vol_ratio", 0) or 0),
                "gtt_id": ""
            }
            new_row["Risk_Per_Share"], new_row["Rupee_Risk"] = position_risk(
                new_row["Entry_Price"], new_row["Current_SL"], new_row["Quantity"]
            )

            # Place GTT exit orders (silently skipped if not connected)
            gtt_id = place_gtt(
                symbol=symbol, qty=int(new_row["Quantity"]),
                last_price=float(new_row["Entry_Price"]),
                sl=float(new_row["Current_SL"]), target=float(new_row["Target_2"]),
            )
            if gtt_id:
                new_row["gtt_id"] = gtt_id

            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

        _save_positions_csv(df, path)

        # Telegram notification
        name = symbol
        if idx is not None and "Name" in df.columns:
            name = df.at[idx, "Name"]
        elif "name" in data:
            name = data["name"]

        msg = (
            f"🛍️ <b>TRADE EXECUTED — {symbol} bought!</b>\n"
            f"Name: {name}\n"
            f"Execution Price: ₹{entry_price:.2f}\n"
            f"• Setup Grade: <b>{setup_grade}</b> (Score: {setup_score})\n"
            f"• Expiry Multiplier: <b>{exp_mult}x</b> ({exp_reason})\n"
            f"Snapshot Technicals captured:\n"
            f"• Weekly Trend: <b>{tech.get('weekly_trend', 'UNKNOWN')}</b>\n"
            f"• Base Status: <b>{tech.get('base_status', 'UNKNOWN')}</b> ({tech.get('base_days', 0)} days)\n"
            f"• False Breakout Risk: <b>{tech.get('false_breakout_risk', 'LOW')}</b>\n"
            f"• RSI: <b>{tech.get('rsi', 0.0):.1f}</b> | ATR%: <b>{tech.get('atr_pct', 0.0):.2f}%</b>\n"
            f"Good luck! 🚀"
        )
        _tg_send(msg)

        logger.info("[positions] Executed buy for %s @ %s", symbol, entry_price)
        return jsonify({"status": "ok", "message": f"Successfully executed buy for {symbol}", "entry_price": entry_price})
    except Exception as exc:
        logger.error("[positions/buy] %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/positions/post-mortem", methods=["POST"])
def api_positions_post_mortem():
    """Save retrospective analysis for a closed or active position in the database."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        symbol = data.get("symbol")
        entry_date = data.get("entry_date")
        why = data.get("why", "").strip()
        maximize = data.get("maximize", "").strip()

        if not symbol or not entry_date:
            return jsonify({"status": "error", "message": "symbol and entry_date are required"}), 400

        symbol = symbol.strip().upper()
        entry_date = entry_date.strip()
        path = os.path.join(_ROOT, "data", "positions.csv")
        if not os.path.exists(path):
            return jsonify({"status": "error", "message": "Positions database file not found"}), 404

        df = pd.read_csv(path)
        if df.empty:
            return jsonify({"status": "error", "message": "Database is empty"}), 404

        # Standardize search fields
        df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
        df["Entry_Date"] = df["Entry_Date"].fillna("").astype(str).str.strip()

        # Match by Symbol and Entry_Date
        mask = (df["Symbol"] == symbol) & (df["Entry_Date"] == entry_date)
        if not mask.any():
            return jsonify({"status": "error", "message": f"No position found for {symbol} on {entry_date}"}), 404

        idx = df[mask].index[-1]  # Take the matching index

        _ensure_cols(df, ["Post_Mortem_Why", "Post_Mortem_Maximize"])

        df.at[idx, "Post_Mortem_Why"] = why
        df.at[idx, "Post_Mortem_Maximize"] = maximize

        _save_positions_csv(df, path)

        logger.info("[positions] Saved post-mortem retrospective for %s (%s)", symbol, entry_date)
        return jsonify({"status": "ok", "message": f"Post-mortem retrospective saved for {symbol}"})

    except Exception as exc:
        logger.error("[positions/post-mortem] %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/positions/update-override", methods=["POST"])
def api_positions_update_override():
    """Update or insert qualitative/fundamental overrides in positions.csv."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        symbol = data.get("symbol")
        if not symbol:
            return jsonify({"status": "error", "message": "symbol is required"}), 400
            
        symbol = symbol.strip().upper()
        notes = data.get("fundamental_notes", "").strip()
        try:
            from core.risk_filters import filter_fundamental_strength
        except ImportError:
            from core_risk_filters import filter_fundamental_strength
        passed_fund, _ = filter_fundamental_strength(symbol)
        status = "APPROVED" if passed_fund else "ON_HOLD"
        live_status_override = data.get("live_status")
        
        path = os.path.join(_ROOT, "data", "positions.csv")
        os.makedirs(os.path.join(_ROOT, "data"), exist_ok=True)
        
        # Load or create positions.csv
        if os.path.exists(path):
            df = pd.read_csv(path)
        else:
            df = pd.DataFrame(columns=["Symbol", "Status"])
            
        _ensure_cols(df, [
            "Fundamental_Notes", "Fundamental_Status", "Live_Status",
            "Name", "Entry_Price", "Quantity", "Target_1", "Target_2",
            "Current_SL", "Setup", "Status", "Setup_Grade", "Setup_Score",
            "Expiry_Multiplier", "Expiry_Reason"
        ])
        
        # Standardize Symbol search
        df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
        
        # Check if symbol exists in open or bought positions
        mask = (df["Symbol"] == symbol) & (df["Status"].astype(str).str.upper().isin(["OPEN", "BOUGHT"]))
        
        if mask.any():
            # Update the latest matching row
            idx = df[mask].index[-1]
            if notes is not None:
                df.at[idx, "Fundamental_Notes"] = notes
            if status is not None:
                df.at[idx, "Fundamental_Status"] = status
            if live_status_override is not None:
                df.at[idx, "Live_Status"] = live_status_override
                
            _save_positions_csv(df, path)
            logger.info("[positions/update-override] Updated overrides for %s", symbol)
            return jsonify({"status": "ok", "message": f"Overrides updated for {symbol}"})
        else:
            # If it doesn't exist, we insert it as an OPEN watchlist position
            tech = fetch_stock_technicals(symbol) or {}
            price = tech.get("price", 0.0)
            
            plan = calculate_trade_plan(tech)
            
            new_row = {
                "Symbol": symbol,
                "Name": tech.get("name", symbol),
                "Entry_Price": price,
                "Quantity": 0,  # 0 indicates watchlist/placeholder position
                "Target_1": plan.get("target_1", 0),
                "Target_2": plan.get("target_2", 0),
                "Current_SL": plan.get("stop_loss", 0),
                "Setup": plan.get("setup_type", "SWING"),
                "Entry_Date": _now_date(),
                "Status": "OPEN",
                "Fundamental_Notes": notes,
                "Fundamental_Status": status or "APPROVED",
                "Live_Status": live_status_override or "WAITING"
            }
            
            new_df = pd.DataFrame([new_row])
            combined = pd.concat([df, new_df], ignore_index=True)
            _save_positions_csv(combined, path)
            logger.info("[positions/update-override] Created new watchlist row with overrides for %s", symbol)
            return jsonify({"status": "ok", "message": f"Created new watchlist item with overrides for {symbol}"})
            
    except Exception as exc:
        logger.error("[positions/update-override] Failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/qualitative/<symbol>")
def api_qualitative(symbol: str):
    """Exposes qualitative indicators, longBusinessSummary (Moat), news, and financials from yfinance/news cache."""
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return jsonify({"status": "error", "message": "Symbol is required"}), 400
    try:
        # 1. Fetch yfinance details
        import yfinance as yf
        ticker = yf.Ticker(f"{symbol}.NS")
        info = ticker.info or {}
        
        profile = info.get("longBusinessSummary") or "Corporate profile summary not available."
        
        # 2. Financial growth & margin indicators
        rev_growth = info.get("revenueGrowth")
        revenue_growth_pct = round(rev_growth * 100, 1) if rev_growth is not None else None
        
        ebitda_margins = info.get("ebitdaMargins")
        ebitda_margin_pct = round(ebitda_margins * 100, 1) if ebitda_margins is not None else None
        
        # Star calculation (0-5 stars)
        growth_stars = 2
        if revenue_growth_pct is not None:
            if revenue_growth_pct >= 20: growth_stars = 5
            elif revenue_growth_pct >= 10: growth_stars = 4
            elif revenue_growth_pct >= 5: growth_stars = 3
            elif revenue_growth_pct < 0: growth_stars = 1
            
        moat_stars = 2
        if ebitda_margin_pct is not None:
            if ebitda_margin_pct >= 20: moat_stars = 5
            elif ebitda_margin_pct >= 12: moat_stars = 4
            elif ebitda_margin_pct >= 5: moat_stars = 3
            elif ebitda_margin_pct < 0: moat_stars = 1
            
        # 3. Retrieve recent news headlines from pre-market news cache
        corporate_actions = []
        stock_news = []
        if _news_get is not None:
            try:
                news_payload = _news_get(force=False)
                stock_news = news_payload.get("stocks", {}).get(symbol, {}).get("headlines", [])
            except Exception as news_exc:
                logger.warning("[server/qualitative] Failed to retrieve news cache for %s: %s", symbol, news_exc)
                
        # Fallback to general yfinance news if pre-market news is empty
        if not stock_news:
            try:
                yf_news = ticker.news or []
                for n in yf_news:
                    title = n.get("title", "")
                    link = n.get("link", "#")
                    publisher = n.get("publisher", "yfinance")
                    pub_sec = n.get("providerPublishTime")
                    pubDate = datetime.fromtimestamp(pub_sec).strftime("%Y-%m-%d") if pub_sec else ""
                    stock_news.append({
                        "title": title,
                        "link": link,
                        "source": publisher,
                        "pubDate": pubDate
                    })
            except Exception as yf_news_exc:
                logger.warning("[server/qualitative] Failed to retrieve yfinance news for %s: %s", symbol, yf_news_exc)
                
        # Format corporate actions list
        for h in stock_news:
            title = h.get("title", "")
            title_lower = title.lower()
            
            # Classify event types
            event_type = "NEWS"
            if "block" in title_lower or "bulk" in title_lower:
                event_type = "BULK / BLOCK DEAL"
            elif "acquir" in title_lower or "takeover" in title_lower or "buyout" in title_lower:
                event_type = "ACQUISITION / RESTRUCTURING"
            elif "dividend" in title_lower or "bonus" in title_lower or "split" in title_lower:
                event_type = "CORPORATE ACTION"
            elif "earnings" in title_lower or "result" in title_lower or "quarter" in title_lower:
                event_type = "EARNINGS DISCLOSURE"
            elif "promoter" in title_lower or "stake" in title_lower:
                event_type = "SHAREHOLDING CHANGE"
                
            corporate_actions.append({
                "event_type": event_type,
                "publisher": h.get("source") or h.get("publisher") or "Exchange Filing",
                "title": title,
                "link": h.get("link", "#")
            })
            
        # 4. Get Nifty macro sentiment
        nifty_sentiment = "NEUTRAL"
        try:
            nifty = fetch_nifty_levels()
            fii = fetch_fii_dii_flow(days=5)
            nifty_sentiment = _sentiment(nifty, fii)
        except Exception:
            pass
            
        return jsonify({
            "status": "success",
            "symbol": symbol,
            "profile": profile,
            "macro_sentiment": nifty_sentiment,
            "revenue_growth_pct": revenue_growth_pct,
            "growth_stars": growth_stars,
            "ebitda_margin_pct": ebitda_margin_pct,
            "moat_stars": moat_stars,
            "corporate_actions": corporate_actions
        })
        
    except Exception as exc:
        logger.error("[qualitative] Failed for %s: %s", symbol, exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/positions/close", methods=["POST"])
def api_positions_close():
    """Manually close an active position in positions.csv (simulated exit)."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        symbol = data.get("symbol")
        entry_date = data.get("entry_date")
        exit_price = data.get("exit_price")
        outcome = data.get("outcome", "MANUAL_EXIT")
        why = data.get("why", "").strip()
        maximize = data.get("maximize", "").strip()

        if not symbol or not entry_date or exit_price is None:
            return jsonify({"status": "error", "message": "symbol, entry_date, and exit_price are required"}), 400

        symbol = symbol.strip().upper()
        entry_date = entry_date.strip()
        exit_price = float(exit_price)
        path = os.path.join(_ROOT, "data", "positions.csv")
        if not os.path.exists(path):
            return jsonify({"status": "error", "message": "Positions database file not found"}), 404

        df = pd.read_csv(path)
        if df.empty:
            return jsonify({"status": "error", "message": "Database is empty"}), 404

        # Standardize fields
        df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
        df["Entry_Date"] = df["Entry_Date"].fillna("").astype(str).str.strip()
        df["Status"] = df["Status"].fillna("").astype(str).str.strip().str.upper()

        # Match active bought position
        mask = (df["Symbol"] == symbol) & (df["Entry_Date"] == entry_date) & (df["Status"] == "BOUGHT")
        if not mask.any():
            return jsonify({"status": "error", "message": f"No active position found for {symbol} on {entry_date}"}), 404

        idx = df[mask].index[-1]

        _ensure_cols(df, ["Closing_Price", "Outcome", "Status", "T2_Hit_Date", "SL_Hit_Date", "Post_Mortem_Why", "Post_Mortem_Maximize", "T2_Notified", "SL_Notified"])

        today_str = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")

        df.at[idx, "Closing_Price"] = exit_price
        df.at[idx, "Outcome"] = outcome
        df.at[idx, "Status"] = "CLOSED"
        
        if "WIN" in outcome or outcome == "T2_WIN" or outcome == "T1_HIT":
            df.at[idx, "T2_Hit_Date"] = today_str
            df.at[idx, "T2_Notified"] = True
        else:
            df.at[idx, "SL_Hit_Date"] = today_str
            df.at[idx, "SL_Notified"] = True

        df.at[idx, "Post_Mortem_Why"] = why
        df.at[idx, "Post_Mortem_Maximize"] = maximize

        _save_positions_csv(df, path)

        # Telegram notification
        name = df.at[idx, "Name"] if "Name" in df.columns else symbol
        msg = (
            f"🏁 <b>TRADE CLOSED — {symbol} exited!</b>\n"
            f"Name: {name}\n"
            f"Exit Price: ₹{exit_price:.2f}\n"
            f"Outcome: <b>{outcome}</b>\n"
            f"Retrospective Notes recorded. 📊"
        )
        _tg_send(msg)

        logger.info("[positions] Closed position manually for %s @ %s", symbol, exit_price)
        return jsonify({"status": "ok", "message": f"Successfully closed position for {symbol}"})

    except Exception as exc:
        logger.error("[positions/close] %s", exc)
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
    Strategy performance for watchlist setups (OPEN) and active portfolio positions (BOUGHT):
      - Closed trades drive realized win rate / avg P&L
      - Bought trades drive live unrealized P&L + active counts
      - by_setup breaks down both
    """
    path = os.path.join(_ROOT, "data", "positions.csv")
    if not os.path.exists(path):
        return jsonify(_empty_results())
    try:
        df = pd.read_csv(path)
        if df.empty:
            return jsonify(_empty_results())

        try:
            from core.risk_filters import filter_fundamental_strength
        except ImportError:
            from core_risk_filters import filter_fundamental_strength

        # Ensure columns exist (read-only — changes won't be persisted)
        _ensure_cols(df, [
            "Outcome", "Entry_Hit_Date", "T1_Hit_Date", "T2_Hit_Date",
            "SL_Hit_Date", "Closing_Price", "Setup", "Entry_Notified",
            "T1_Notified", "T2_Notified", "SL_Notified",
            "Buy_Weekly_Trend", "Buy_Base_Days", "Buy_Base_Status",
            "Buy_False_Breakout_Risk", "Buy_False_Breakout_Desc",
            "Buy_RSI", "Buy_ATR_Pct", "Buy_Vol_Ratio",
            "Post_Mortem_Why", "Post_Mortem_Maximize",
            "Fundamental_Status", "Fundamental_Notes", "Live_Status",
            "Setup_Grade", "Setup_Score", "Expiry_Multiplier", "Expiry_Reason",
            "Cur_Weekly_Trend", "Cur_Return_20d", "Cur_ADX", "Cur_EMA20", "Cur_EMA50",
            "Cur_ATR_Pct", "Cur_Base_Status", "Cur_Base_Days", "Cur_Vol_Ratio",
            "Cur_False_Breakout_Risk", "Cur_Scan_Date", "Cur_Verdict", "Cur_Reasons",
            "Cur_Regime_Mult",
        ])

        total = len(df)
        closed_df = df[df["Status"].astype(str).str.upper() == "CLOSED"]
        open_df   = df[df["Status"].astype(str).str.upper() == "OPEN"]
        bought_df = df[df["Status"].astype(str).str.upper() == "BOUGHT"]

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

        # ── Live prices (with bar date) for active symbols (OPEN + BOUGHT) in bulk ──
        active_symbols = pd.concat([open_df["Symbol"], bought_df["Symbol"]]).astype(str).unique().tolist() if (not open_df.empty or not bought_df.empty) else []
        price_map    = fetch_prices_bulk_dated(active_symbols) if active_symbols else {}
        today        = datetime.now(pytz.timezone("Asia/Kolkata")).date()
        today_str    = today.strftime("%Y-%m-%d")

        # Build watchlist_positions list (OPEN status)
        watchlist_list = []
        for _, r in open_df.iterrows():
            try:
                sym = str(r.get("Symbol", ""))
                ep  = float(r.get("Entry_Price", 0))
                t1  = float(r.get("Target_1", 0))
                t2  = float(r.get("Target_2", 0))
                sl  = float(r.get("Current_SL", 0))
                cp, bar_date = price_map.get(sym, (0.0, ""))
                cp  = float(cp or 0)
                price_stale = bool(cp) and bar_date != today_str
                pnl_pct = round((cp - ep) / ep * 100, 2) if ep and cp else 0

                entry_date_str = str(r.get("Entry_Date", ""))
                days_held = 0
                if entry_date_str:
                    try:
                        days_held = (today - datetime.strptime(entry_date_str, "%Y-%m-%d").date()).days
                    except Exception:
                        days_held = 0

                live_at_entry = bool(cp and ep and cp <= ep * 1.005)
                live_above_t1 = bool(cp and t1 and cp >= t1)
                live_below_sl = bool(cp and sl and cp <= sl)
                # The entry is LOCKED at first scan (never re-priced upward), so a
                # candidate that has run >5% above its locked entry is being chased.
                # Gate on a fresh bar so a stale last-close can't trigger it.
                live_chasing  = bool(cp and ep and not price_stale and cp >= ep * 1.05)

                # Watchlist candidates are NOT owned, so a price above T1/T2 is not a
                # "target hit" — it means the setup already ran past entry. Flag it as
                # EXTENDED (don't chase) rather than painting a misleading "T1 ✓".
                if   live_above_t1 or live_chasing: live_status = "EXTENDED"
                elif live_below_sl: live_status = "BELOW_SL"
                elif live_at_entry: live_status = "AT_ENTRY"
                else:               live_status = "WAITING"

                watchlist_list.append({
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
                    # Per-position risk LOCKED at first scan (see position_risk()).
                    "risk_per_share": _num(r.get("Risk_Per_Share")),
                    "rupee_risk":     _num(r.get("Rupee_Risk")),
                    "live_status": str(r.get("Live_Status", "") or live_status),
                    # Not owned → never highlight T1/T2 cells as a "hit" (see live_status EXTENDED).
                    "above_t1":    False,
                    "above_t2":    False,
                    "at_entry":    live_at_entry,
                    "below_sl":    live_below_sl,
                    "price_stale": price_stale,
                    "price_date":  bar_date,
                    "t1_notified":    _truthy(r.get("T1_Notified")),
                    "entry_notified": _truthy(r.get("Entry_Notified")),
                    "entry_date":  entry_date_str,
                    "index_membership": get_index_membership(sym),
                    "fundamental_status": "APPROVED" if filter_fundamental_strength(sym)[0] else "ON_HOLD",
                    "fundamental_notes": str(r.get("Fundamental_Notes", "") or ""),
                    "debate": fetch_cached_debate_verdict(sym),
                    "shareholding": fetch_screener_shareholding(sym),
                    "setup_grade": str(r.get("Setup_Grade", "")),
                    "setup_score": str(r.get("Setup_Score", "")),
                    "expiry_multiplier": str(r.get("Expiry_Multiplier", "")),
                    "expiry_reason": str(r.get("Expiry_Reason", "")),
                    # Freshly-recomputed gate/score inputs from the daily Keep & Refresh
                    # pass, so the Analysis-tab matrix re-evaluates against today's data.
                    "weekly_trend":        str(r.get("Cur_Weekly_Trend", "") or ""),
                    "return_20d":          _num(r.get("Cur_Return_20d")),
                    "adx":                 _num(r.get("Cur_ADX")),
                    "ema20":               _num(r.get("Cur_EMA20")),
                    "ema50":               _num(r.get("Cur_EMA50")),
                    "atr_pct":             _num(r.get("Cur_ATR_Pct")),
                    "base_status":         str(r.get("Cur_Base_Status", "") or ""),
                    "base_days":           _num(r.get("Cur_Base_Days")),
                    "vol_ratio":           _num(r.get("Cur_Vol_Ratio")),
                    "false_breakout_risk": str(r.get("Cur_False_Breakout_Risk", "") or ""),
                    "verdict":             str(r.get("Cur_Verdict", "") or ""),
                    "reasons":             _parse_reasons(r.get("Cur_Reasons")),
                    "regime_multiplier":   _num(r.get("Cur_Regime_Mult")),
                    "scan_date":           str(r.get("Cur_Scan_Date", "") or ""),
                })
            except Exception:
                continue

        # Build active_positions list (BOUGHT status)
        active_list  = []
        active_pnls  = []
        active_wins   = 0   # BOUGHT positions currently in profit
        active_losses = 0   # BOUGHT positions currently in loss
        t1_hit_active = 0   # BOUGHT positions that have hit T1
        entry_hit_active = 0 # BOUGHT positions where entry zone reached

        for _, r in bought_df.iterrows():
            try:
                sym = str(r.get("Symbol", ""))
                ep  = float(r.get("Entry_Price", 0))
                t1  = float(r.get("Target_1", 0))
                t2  = float(r.get("Target_2", 0))
                sl  = float(r.get("Current_SL", 0))
                cp, bar_date = price_map.get(sym, (0.0, ""))
                cp  = float(cp or 0)
                price_stale = bool(cp) and bar_date != today_str
                pnl_pct = round((cp - ep) / ep * 100, 2) if ep and cp else 0

                entry_date_str = str(r.get("Entry_Date", ""))
                days_held = 0
                if entry_date_str:
                    try:
                        days_held = (today - datetime.strptime(entry_date_str, "%Y-%m-%d").date()).days
                    except Exception:
                        days_held = 0

                live_at_entry = bool(cp and ep and cp <= ep * 1.005)
                live_above_t1 = bool(cp and t1 and cp >= t1)
                live_above_t2 = bool(cp and t2 and cp >= t2)
                live_below_sl = bool(cp and sl and cp <= sl)

                if   live_above_t2: live_status = "T2_REACHED"
                elif live_above_t1: live_status = "T1_REACHED"
                elif live_below_sl: live_status = "BELOW_SL"
                elif live_at_entry: live_status = "AT_ENTRY"
                else:               live_status = "WAITING"

                if pnl_pct > 0: active_wins += 1
                elif pnl_pct < 0: active_losses += 1
                if live_above_t1:  t1_hit_active += 1
                if live_at_entry:  entry_hit_active += 1

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
                    "live_status": str(r.get("Live_Status", "") or live_status),
                    "above_t1":    live_above_t1,
                    "above_t2":    live_above_t2,
                    "at_entry":    live_at_entry,
                    "below_sl":    live_below_sl,
                    "price_stale": price_stale,
                    "price_date":  bar_date,
                    "t1_notified":    _truthy(r.get("T1_Notified")),
                    "entry_notified": _truthy(r.get("Entry_Notified")),
                    "entry_date":  entry_date_str,
                    "index_membership": get_index_membership(sym),
                    "fundamental_status": "APPROVED" if filter_fundamental_strength(sym)[0] else "ON_HOLD",
                    "fundamental_notes": str(r.get("Fundamental_Notes", "") or ""),
                    "debate": fetch_cached_debate_verdict(sym),
                    "shareholding": fetch_screener_shareholding(sym),
                    "setup_grade": str(r.get("Setup_Grade", "")),
                    "setup_score": str(r.get("Setup_Score", "")),
                    "expiry_multiplier": str(r.get("Expiry_Multiplier", "")),
                    "expiry_reason": str(r.get("Expiry_Reason", "")),
                    **_extract_snapshot(r),
                })
            except Exception:
                continue

        active_list.sort(key=lambda x: x["pnl_pct"], reverse=True)
        avg_unrealized = round(sum(active_pnls) / len(active_pnls), 2) if active_pnls else 0

        # Per-setup breakdown — now includes BOTH closed + open/bought
        by_setup = {}
        for setup in df["Setup"].dropna().astype(str).unique():
            if not setup:
                continue
            grp        = df[df["Setup"] == setup]
            grp_closed = grp[grp["Status"].astype(str).str.upper() == "CLOSED"]
            grp_open   = grp[grp["Status"].astype(str).str.upper().isin(["OPEN", "BOUGHT"])]
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

            # Unrealized P&L (open + bought) — uses live prices
            unr_pnls = []
            for _, r in grp_open.iterrows():
                try:
                    sym = str(r.get("Symbol", ""))
                    ep  = float(r.get("Entry_Price", 0))
                    cp  = float((price_map.get(sym) or (0.0, ""))[0] or 0)
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
                    "index_membership": get_index_membership(str(r.get("Symbol", ""))),
                    **_extract_snapshot(r),
                })
            except Exception:
                continue
        # Sort by exit_date desc
        closed_list.sort(key=lambda x: x.get("exit_date", ""), reverse=True)

        return jsonify({
            "total":            total,
            "open":             len(open_df) + len(bought_df),
            "closed":           closed,
            "wins":             wins,
            "losses":           losses,
            "win_rate":         win_rate,
            "avg_days_held":    avg_days_held,
            "by_setup":         by_setup,
            "closed_positions": closed_list,
            # ── Split lists returned for portfolio vs watchlist decoupling ──
            "watchlist_positions": watchlist_list,
            "active_positions":    active_list,
            "active_count":       len(bought_df),
            "active_in_profit":   active_wins,
            "active_in_loss":     active_losses,
            "active_entry_hit":   entry_hit_active,
            "active_t1_hit":      t1_hit_active,
            "avg_unrealized_pct": avg_unrealized,
        })
    except Exception as exc:
        logger.error("[results] %s", exc)
        return jsonify({**_empty_results(), "error": str(exc)})


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
    Entry alerts for OPEN setups, and Target/SL alerts for BOUGHT active positions,
    fires Telegram alert on first crossing, and persists notification state back to CSV.
    Returns enriched positions list.

    Called by:
      - GET /api/positions (on-demand from dashboard)
      - Background scheduler (every minute during market hours)
    """
    path = os.path.join(_ROOT, "data", "positions.csv")
    if not os.path.exists(path):
        return []

    df = pd.read_csv(path)
    if df.empty:
        return []

    # Ensure all notification-state + outcome columns exist with correct dtypes
    for col in ("Entry_Notified", "T1_Notified", "T2_Notified", "SL_Notified"):
        if col not in df.columns:
            df[col] = False
    _ensure_cols(df, [
        "Entry_Hit_Date", "T1_Hit_Date", "T2_Hit_Date", "SL_Hit_Date", "Outcome",
        "Setup_Grade", "Setup_Score", "Expiry_Multiplier", "Expiry_Reason",
        "Highest_High_Since_Entry"
    ])
    # Force object/mixed dtype so pandas allows assigning strings to empty/NaN columns without casting errors
    for col in (
        "Entry_Hit_Date", "T1_Hit_Date", "T2_Hit_Date", "SL_Hit_Date", "Outcome",
        "Setup_Grade", "Expiry_Multiplier", "Expiry_Reason", "Highest_High_Since_Entry"
    ):
        if col in df.columns:
            df[col] = df[col].astype(object)
    if "Closing_Price" not in df.columns:
        df["Closing_Price"] = 0.0

    today_str = _now_date()
    csv_dirty = False
    positions = []
    pending_alerts: list[str] = []

    # ── Bulk-fetch live prices for all OPEN and BOUGHT positions (one bulk yfinance call) ──
    active_symbols = (
        df.loc[df["Status"].astype(str).str.upper().eq("BOUGHT"), "Symbol"]
          .astype(str).unique().tolist()
    )
    price_map = fetch_prices_bulk_dated(active_symbols) if active_symbols else {}

    for idx, row in df.iterrows():
        pos = row.to_dict()
        # Convert NaN to None/empty string for JSON serialization
        for key, val in pos.items():
            try:
                if pd.isna(val):
                    pos[key] = ""
            except (TypeError, ValueError):
                pass
        status = str(pos.get("Status", "OPEN")).upper()

        if status != "BOUGHT":
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
        cur, bar_date = price_map.get(sym, (0.0, ""))
        cur  = float(cur or 0)
        # Only act on a price from *today's* trading session. Off-hours (weekend /
        # holiday / pre-open) yfinance returns the prior close — treating that as a
        # live crossing stamped today's date onto stale data and fired weekend alerts.
        price_fresh = bool(cur) and bar_date == today_str

        ep  = float(pos.get("Entry_Price", 0))
        qty = float(pos.get("Quantity", 0))
        t1  = float(pos.get("Target_1", 0))
        t2  = float(pos.get("Target_2", 0))
        sl  = float(pos.get("Current_SL", 0))

        pos["current_price"] = cur
        pos["pnl"]           = round((cur - ep) * qty, 2) if cur else 0
        pos["pnl_pct"]       = round(((cur - ep) / ep * 100) if ep else 0, 2)
        pct                  = round(((cur - ep) / ep * 100) if ep else 0, 1)

        if status == "OPEN":
            # Watched stock setup: monitor only for entry ready zone.
            # Gated on price_fresh so a stale prior close can't fire a weekend alert.
            entry_hit = bool(price_fresh and ep and cur <= ep * 1.005)
            pos["entry_hit"] = entry_hit
            pos["t1_hit"]    = False
            pos["t2_hit"]    = False
            pos["sl_hit"]    = False

            entry_done = _truthy(pos.get("Entry_Notified"))
            if entry_hit and not entry_done:
                confirm_lines = []
                try:
                    tech_info = fetch_stock_technicals(sym)
                    if tech_info:
                        # RSI Sweet Spot: 40-65
                        rsi = tech_info.get("rsi", 0.0)
                        rsi_ok = "✅" if 40 <= rsi <= 65 else "⚠️"
                        confirm_lines.append(f"{rsi_ok} <b>RSI</b>: {rsi:.1f} (Sweet Spot: 40-65)")

                        # MACD Freshness
                        macd_days = tech_info.get("macd_crossover_days_ago", -1)
                        macd_ok = "✅" if 0 <= macd_days <= 5 else "ℹ️"
                        macd_text = f"{macd_days}d ago" if macd_days >= 0 else "no fresh crossover"
                        confirm_lines.append(f"{macd_ok} <b>MACD Cross</b>: {macd_text}")

                        # Vol Ratio
                        vol_r = tech_info.get("volume_ratio", 1.0)
                        vol_ok = "✅" if vol_r >= 1.5 else "⚠️"
                        confirm_lines.append(f"{vol_ok} <b>Vol Ratio</b>: {vol_r:.2f}x (Ideal >= 1.5x)")

                        # False Breakout Risk
                        fb_risk = tech_info.get("false_breakout_risk", "LOW")
                        fb_ok = "✅" if fb_risk == "LOW" else "⚠️"
                        confirm_lines.append(f"{fb_ok} <b>False Breakout Risk</b>: {fb_risk}")

                        # Live Timing State (NML)
                        try:
                            from core.risk_filters import evaluate_nml_logic
                        except ImportError:
                            from core_risk_filters import evaluate_nml_logic
                        timing_status, timing_reason = evaluate_nml_logic(sym, tech_info)
                        timing_ok = "✅" if timing_status in ("WATCH_SUPPORT", "WATCH_RESISTANCE") else "⚠️"
                        confirm_lines.append(f"{timing_ok} <b>Timing (NML)</b>: {timing_status} ({timing_reason or 'PASS'})")
                except Exception as ex:
                    logger.warning("[server/positions] Failed to compute fill-time confirmation signals for %s: %s", sym, ex)

                confirm_panel = "\n".join(confirm_lines)
                confirm_panel_text = f"\n\n<b>🔍 Fill-Time Confirmation Panel:</b>\n{confirm_panel}" if confirm_lines else ""

                pending_alerts.append(
                    f"🎯 <b>ENTRY READY — {sym}</b>\n"
                    f"{name}\n"
                    f"Now ₹{cur:.2f} (entry zone ≈ ₹{ep:.2f})\n"
                    f"Time to consider opening the position.{confirm_panel_text}"
                )
                df.at[idx, "Entry_Notified"] = True
                df.at[idx, "Entry_Hit_Date"] = today_str
                csv_dirty = True

        elif status == "BOUGHT":
            # Active portfolio position: monitor for profit/loss targets.
            # Gated on price_fresh so an off-hours stale close can't stamp today's
            # date onto a target/SL crossing or fire a weekend alert.
            pos["entry_hit"] = True  # already bought and active

            # Update highest high since entry (intraday tracking)
            raw_hh = pos.get("Highest_High_Since_Entry")
            try:
                if raw_hh in (None, "", "NaN") or pd.isna(raw_hh):
                    highest_high = max(ep, cur) if cur else ep
                else:
                    highest_high = float(raw_hh)
            except (ValueError, TypeError):
                highest_high = max(ep, cur) if cur else ep

            if cur and cur > highest_high:
                highest_high = cur
                df.at[idx, "Highest_High_Since_Entry"] = highest_high
                pos["Highest_High_Since_Entry"] = highest_high
                csv_dirty = True

            t1_hit = bool(price_fresh and t1 and cur >= t1)
            t2_hit = bool(price_fresh and t2 and cur >= t2)
            sl_hit = bool(price_fresh and sl and cur <= sl)
            pos["t1_hit"] = t1_hit
            pos["t2_hit"] = t2_hit
            pos["sl_hit"] = sl_hit

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
                pos["T1_Notified"] = True
                pos["T1_Hit_Date"] = today_str
                if not str(pos.get("Outcome", "")).strip():
                    df.at[idx, "Outcome"] = "T1_HIT"
                    pos["Outcome"] = "T1_HIT"
                csv_dirty = True

            # Cost-Basis Lock: snap SL to cost basis if Target 1 is cleared
            t1_hit_date_val = pos.get("T1_Hit_Date")
            has_t1_date = t1_hit_date_val not in ("", None, "NaN") and not pd.isna(t1_hit_date_val)
            t1_cleared = t1_hit or _truthy(pos.get("T1_Notified")) or has_t1_date
            target_sl = sl
            if t1_cleared and ep > 0:
                target_sl = max(target_sl, ep)

            if target_sl > sl:
                df.at[idx, "Current_SL"] = target_sl
                pos["Current_SL"] = target_sl
                sl = target_sl  # Update local stop loss variable for downstream checks (e.g. sl_hit)
                sl_hit = bool(price_fresh and sl and cur <= sl)  # Recalculate sl_hit against the new snapped SL
                pos["sl_hit"] = sl_hit
                csv_dirty = True

                # Modify GTT order on Kite if gtt_id exists
                raw_gtt = pos.get("gtt_id") or pos.get("GTT_Id")
                if raw_gtt not in (None, "", "NaN") and not pd.isna(raw_gtt):
                    try:
                        gtt_id_clean = int(float(str(raw_gtt)))
                        try:
                            from core_kite import modify_gtt
                        except ImportError:
                            from core.core_kite import modify_gtt
                        
                        qty = int(pos.get("Quantity", 1))
                        modify_gtt(trigger_id=gtt_id_clean, symbol=sym, qty=qty, last_price=cur, sl=target_sl, target=t2)
                    except Exception as gtt_err:
                        logger.warning("[server/positions] Failed to modify GTT for %s: %s", sym, gtt_err)


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


# Fields the dashboard reads off each stock that may be absent from older
# briefs. When a stale brief is served back, hydrate them from yfinance so
# downstream UI (e.g. Analysis tab's VCP row) doesn't show N/A.
_HYDRATE_FIELDS = (
    "atr_pct", "near_52w_high", "ema9_cross_ema21", "ema9_cross_days_ago",
    "rsi_pullback_zone", "high_52w", "dist_52w_pct", "weekly_trend",
    "base_days", "base_status", "false_breakout_risk", "false_breakout_desc",
    "macd_crossover_days_ago",
)


def _hydrate_brief_missing_fields(brief: dict) -> None:
    stocks = brief.get("stocks") or []
    # Always refresh sector from current mapping (mapping can change after brief was written)
    for s in stocks:
        sym = s.get("symbol", "")
        if sym:
            s["sector"] = get_sector(sym)
    needs = [s for s in stocks if any(s.get(f) in (None,) for f in _HYDRATE_FIELDS)]
    if needs:
        logger.info("[scan] hydrating %d stale stock(s) with %s", len(needs), list(_HYDRATE_FIELDS))
        for s in needs:
            try:
                tech = fetch_stock_technicals(s.get("symbol", ""))
                if not tech:
                    continue
                for f in _HYDRATE_FIELDS:
                    if s.get(f) is None and f in tech:
                        s[f] = tech[f]
            except Exception as exc:
                logger.warning("[scan] hydrate %s failed: %s", s.get("symbol"), exc)

    # Earnings fields were added later — hydrate them if the brief was written
    # before that change (otherwise dashboard treats them as Unresolved and BLOCKs)
    earn_needs = [s for s in stocks if "earnings_days_until" not in s]
    if earn_needs:
        logger.info("[scan] hydrating earnings for %d stale stock(s)", len(earn_needs))
        for s in earn_needs:
            try:
                days_until, date_str, source = fetch_earnings_date(s.get("symbol", ""))
                s["earnings_days_until"] = days_until
                s["earnings_date"]       = date_str
                s["earnings_source"]     = source
            except Exception as exc:
                logger.warning("[scan] earnings hydrate %s failed: %s", s.get("symbol"), exc)
                s["earnings_days_until"] = None
                s["earnings_date"]       = ""
                s["earnings_source"]     = "unknown"

    # Timing state (NML) for the fill-time confirmation panel — added later; hydrate
    # stale briefs via the same helper the ENTRY-READY alert uses.
    timing_needs = [s for s in stocks if "timing_state" not in s]
    if timing_needs:
        try:
            from core.risk_filters import evaluate_nml_logic
        except ImportError:
            from core_risk_filters import evaluate_nml_logic
        logger.info("[scan] hydrating timing state for %d stale stock(s)", len(timing_needs))
        for s in timing_needs:
            try:
                tech = fetch_stock_technicals(s.get("symbol", ""))
                if not tech:
                    continue
                t_status, t_reason = evaluate_nml_logic(s.get("symbol", ""), tech)
                s["timing_state"]  = t_status
                s["timing_reason"] = t_reason
            except Exception as exc:
                logger.warning("[scan] timing hydrate %s failed: %s", s.get("symbol"), exc)
                s["timing_state"]  = "UNKNOWN"
                s["timing_reason"] = ""


def _now_date() -> str:
    return datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")


def _now_time() -> str:
    return datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%H:%M")


# ── Background poller ───────────────────────────────────────────────────────

def _poll_job():
    """Cron job: check BOUGHT portfolio positions for target/SL crossings."""
    try:
        positions = check_positions_and_notify()
        bought_count = sum(1 for p in positions if str(p.get("Status", "")).upper() == "BOUGHT")
        logger.info("[poll] checked %d BOUGHT positions", bought_count)
    except Exception as exc:
        logger.error("[poll] error: %s", exc)


def _start_scheduler():
    """Start a background scheduler that polls positions every minute 9:15–15:30 IST Mon-Fri."""
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        import pytz
    except ImportError:
        logger.warning("[poll] apscheduler/pytz not installed — alerts only fire when dashboard is open")
        return

    ist = pytz.timezone("Asia/Kolkata")
    _scheduler = BackgroundScheduler(timezone=ist)
    # NSE market hours: 9:15 AM – 3:30 PM IST, Mon–Fri
    for job_id, hour, minute in (
        ("position_poller_open", "9", "15-59"),
        ("position_poller_midday", "10-14", "*"),
        ("position_poller_close", "15", "0-30"),
    ):
        _scheduler.add_job(
            _poll_job,
            CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=ist),
            id=job_id,
            max_instances=1,
            coalesce=True,
        )
    # Keep & refresh Scan before market hours (Mon-Fri)
    _load_schedule_config()
    try:
        scan_h, scan_m = _get_stocks_time.split(":")
        _scheduler.add_job(
            run_get_stocks_job,
            CronTrigger(hour=scan_h, minute=scan_m, timezone=ist),
            id="get_stocks",
            max_instances=1,
            coalesce=True,
        )
        logger.info("[get-stocks] scheduled daily scan at %s IST", _get_stocks_time)
    except Exception as e:
        logger.error("[get-stocks] failed to schedule daily scan: %s", e)
    try:
        h, m = _maintenance_time.split(":")
        _scheduler.add_job(
            run_morning_maintenance,
            CronTrigger(day_of_week="mon-fri", hour=h, minute=m, timezone=ist),
            id="watchlist_maintenance",
            max_instances=1,
            coalesce=True,
        )
        logger.info("[poll] scheduled Keep & refresh Scan at %s IST", _maintenance_time)
    except Exception as e:
        logger.error("[poll] failed to schedule Keep & refresh Scan: %s", e)

    _scheduler.start()
    logger.info("[poll] background scheduler started — polling every minute, Mon-Fri 9:15-15:30 IST")


# ── Entry ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== Swing Sentinel - Local Server ===")
    print("  Dashboard : http://localhost:5000")
    print("  Checklist : http://localhost:5000/checklist")
    print("  Scan API  : POST /api/scan")
    print("  Market    : GET  /api/market")
    print("  Positions : GET  /api/positions")
    print("  Poller    : every 1 min during market hours")
    print("=" * 38 + "\n")
    _start_scheduler()
    app.run(debug=False, port=5000, host="0.0.0.0", use_reloader=False)
