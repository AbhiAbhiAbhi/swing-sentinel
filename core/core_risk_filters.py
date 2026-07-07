"""
Pre-trade risk filters — applied AFTER Chartink scan returns candidates.

Each filter takes the live tech dict (from fetch_stock_technicals) and the
raw history DataFrame, returns (passed: bool, reason: str).

If ANY filter fails, the stock is excluded from /api/scan results and shown
in `filtered_out` with the reason — fully transparent.

Conservative thresholds (block obvious risks, don't over-prune):
  - Earnings within 5 trading days        → skip
  - IPO age less than 180 days            → skip
  - ATR / price > 5.0%                    → skip (too volatile)
  - Any single-day drop ≤ -8% in 60 days  → skip (recent crash)
  - Sector index below its EMA20          → skip (weak sector)
"""
import logging
import os
import json
import threading
from datetime import datetime, timedelta
from typing import Optional, Tuple
import pytz

logger = logging.getLogger(__name__)

# Thread locks for cache files to ensure parallel safety
_shareholding_lock = threading.Lock()
_fundamentals_lock = threading.Lock()
_earnings_lock = threading.Lock()

# ── Thresholds (Conservative) ───────────────────────────────────────────────

EARNINGS_WINDOW_DAYS  = 5
IPO_MIN_AGE_DAYS      = 180
MAX_ATR_PCT           = 0.05   # 5%
WORST_60D_DROP_PCT    = -0.08  # -8%

# ── NSE event calendar cache ─────────────────────────────────────────────────

_NSE_CAL: dict = {}          # symbol -> nearest upcoming result date
_NSE_CAL_TS: Optional[datetime] = None
_NSE_CAL_TTL = 3600          # seconds; re-fetch once per hour

_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/market-data/event-calendar",
    "Connection":      "keep-alive",
    "DNT":             "1",
}


def _parse_nse_date(s: str):
    """Parse common NSE date formats, return date or None."""
    from datetime import date as _date
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s.strip()[:12], fmt).date()
        except ValueError:
            continue
    return None


def _warm_nse_calendar() -> dict:
    """Fetch NSE event calendar once per hour. Returns {SYMBOL: date} dict."""
    global _NSE_CAL, _NSE_CAL_TS
    now = datetime.now()
    if _NSE_CAL_TS and (now - _NSE_CAL_TS).total_seconds() < _NSE_CAL_TTL:
        return _NSE_CAL

    try:
        import requests as _req
        sess = _req.Session()
        sess.headers.update(_NSE_HEADERS)
        # Seed session cookies — NSE requires a prior page visit
        sess.get("https://www.nseindia.com/", timeout=8)
        
        # TEMPORARY FIX: Add a short delay to bypass NSE bot detection. 
        # NOTE: This is unreliable. In the future, migrate to a premium source or library like jugaad-data.
        import time
        time.sleep(2)
        
        resp = sess.get(
            "https://www.nseindia.com/api/event-calendar",
            params={"index": "equities"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        cal: dict = {}
        today = now.date()
        _RESULT_KEYWORDS = ("result", "quarterly", "financial", "annual", "half year")
        for ev in data:
            sym = ev.get("symbol", "").upper().strip()
            if not sym:
                continue
            purpose = ev.get("purpose", "").lower()
            if not any(k in purpose for k in _RESULT_KEYWORDS):
                continue
            # Try multiple field names NSE has used across API versions
            raw_date = (
                ev.get("bfMtngDate") or ev.get("date") or
                ev.get("bdDt") or ev.get("meetingDate") or ""
            ).strip()
            if not raw_date:
                continue
            edate = _parse_nse_date(raw_date)
            if edate and edate >= today:
                if sym not in cal or edate < cal[sym]:
                    cal[sym] = edate

        _NSE_CAL = cal
        _NSE_CAL_TS = now
        logger.info("[nse_cal] loaded %d upcoming results events", len(cal))
    except Exception as exc:
        logger.warning("[nse_cal] fetch failed: %s", exc)
        # Cache failure for a short time (2 minutes) instead of 1 hour to allow quick recovery
        _NSE_CAL_TS = now - timedelta(seconds=_NSE_CAL_TTL - 120)

    return _NSE_CAL


def _fetch_earnings_yfinance(symbol: str):
    """Return earliest upcoming earnings date from yfinance, or None."""
    try:
        import yfinance as yf
        cal = yf.Ticker(f"{symbol}.NS").calendar
        if not cal:
            return None

        earnings_date = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if isinstance(ed, list) and ed:
                earnings_date = ed[0]
            elif ed:
                earnings_date = ed
        else:
            try:
                earnings_date = cal.loc["Earnings Date"].iloc[0]
            except Exception:
                pass

        if not earnings_date:
            return None

        edate = earnings_date.date() if hasattr(earnings_date, "date") else earnings_date
        today = datetime.now(pytz.timezone("Asia/Kolkata")).date()
        return edate if edate >= today else None
    except Exception as exc:
        logger.debug("[yfinance_earnings] %s: %s", symbol, exc)
        return None


def fetch_earnings_date(symbol: str) -> Tuple[Optional[int], str, str]:
    """Return (days_until, date_str, source) for the nearest upcoming results.

    days_until is None if no data found.
    source is "NSE", "yfinance", or "unknown".
    """
    sym = symbol.strip().upper()
    today = datetime.now(pytz.timezone("Asia/Kolkata")).date()

    # Source 1: NSE event calendar (single bulk fetch, cached hourly)
    nse_cal = _warm_nse_calendar()
    edate = nse_cal.get(sym)
    if edate:
        return (edate - today).days, str(edate), "NSE"

    # Source 2: Cache / yfinance fallback
    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    EARNINGS_CACHE_FILE = os.path.join(_ROOT, "data", "earnings_cache.json")
    
    cached_entry = None
    with _earnings_lock:
        if os.path.exists(EARNINGS_CACHE_FILE):
            try:
                with open(EARNINGS_CACHE_FILE, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                    cached_entry = cache.get(sym, {}).get("upcoming")
            except Exception:
                pass
                
    if cached_entry:
        try:
            fetched_at = datetime.fromisoformat(cached_entry.get("fetched_at", "2000-01-01"))
            # Cache earnings for 3 days
            if datetime.now() - fetched_at < timedelta(days=3):
                date_str = cached_entry.get("date")
                if date_str:
                    edate = datetime.strptime(date_str, "%Y-%m-%d").date()
                    if edate >= today:
                        return (edate - today).days, date_str, "yfinance_cache"
                else:
                    return None, "", "yfinance_cache"
        except Exception:
            pass

    # Fresh fetch from yfinance
    edate = _fetch_earnings_yfinance(sym)
    date_str = str(edate) if edate else ""
    
    # Save to cache
    with _earnings_lock:
        cache = {}
        if os.path.exists(EARNINGS_CACHE_FILE):
            try:
                with open(EARNINGS_CACHE_FILE, "r", encoding="utf-8") as f:
                    cache = json.load(f)
            except Exception:
                pass
        if sym not in cache:
            cache[sym] = {}
        cache[sym]["upcoming"] = {
            "date": date_str,
            "fetched_at": datetime.now().isoformat()
        }
        try:
            os.makedirs(os.path.dirname(EARNINGS_CACHE_FILE), exist_ok=True)
            with open(EARNINGS_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2)
        except Exception:
            pass

    if edate:
        return (edate - today).days, date_str, "yfinance"

    return None, "", "unknown"


# ── Individual filters ──────────────────────────────────────────────────────

def filter_volatility(tech: dict, max_atr_pct: float = MAX_ATR_PCT) -> Tuple[bool, str]:
    """Reject if ATR is more than max_atr_pct of the current price."""
    price = tech.get("price", 0)
    atr   = tech.get("atr", 0)
    if not price or not atr:
        return True, ""
    ratio = atr / price
    if ratio > max_atr_pct:
        return False, f"high volatility (ATR {ratio*100:.1f}% of price)"
    return True, ""


def filter_recent_crash(tech: dict, worst_pct: float = WORST_60D_DROP_PCT) -> Tuple[bool, str]:
    """Reject if any single-day return in the last 30 daily bars was <= worst_pct,
    unless the stock is in a confirmed strong uptrend (bullish recovery bypass).
    """
    price = tech.get("price", 0)
    ema21 = tech.get("ema21", 0)
    ema50 = tech.get("ema50", 0)
    
    # Recovery bypass: trend alignment + rail-hugging support check
    if price > 0 and ema21 > 0 and ema50 > 0:
        distance = abs(price - ema21) / ema21 * 100
        if price > ema21 > ema50 and distance <= 2.0:
            logger.info("[crash_bypass] Recovery confirmed. Price is safely trend-aligned and near support (%.2f%% from EMA21)", distance)
            return True, ""  # Recovery confirmed -> suppress crash flag
            
    worst = tech.get("worst_60d_pct", 0)
    if worst and worst <= worst_pct:
        return False, f"recent crash ({worst*100:.1f}% drop in last 30d)"
    return True, ""


def filter_trend_distance_alignment(tech: dict) -> Tuple[bool, str]:
    """Ensure price is trend-aligned (Price > EMA21 > EMA50) and near support
    (Price is within 2% of EMA21), and not extended.
    """
    price = tech.get("price", 0)
    ema21 = tech.get("ema21", 0)
    ema50 = tech.get("ema50", 0)
    
    if price > 0 and ema21 > 0 and ema50 > 0:
        # 1. Trend Alignment Check
        if not (price > ema21 > ema50):
            return False, f"trend alignment failed (Price ₹{price:.2f} must be > EMA21 ₹{ema21:.2f} > EMA50 ₹{ema50:.2f})"
            
        # 2. Distance Check: Price within 2% of EMA21
        distance = abs(price - ema21) / ema21 * 100
        if distance > 2.0:
            return False, f"extended from support (Price is {distance:.1f}% away from EMA21 support, limit 2%)"
            
    return True, ""



def filter_ipo_age(tech: dict, min_days: int = IPO_MIN_AGE_DAYS) -> Tuple[bool, str]:
    """Reject if the stock has fewer than min_days of trading history."""
    bars = tech.get("bars_count", 999)
    if bars < min_days:
        first_bar = tech.get("first_bar", "")
        return False, f"recent IPO ({bars} bars{', since '+first_bar if first_bar else ''})"
    return True, ""


def filter_earnings_soon(symbol: str, window_days: int = EARNINGS_WINDOW_DAYS) -> Tuple[bool, str]:
    """Reject if earnings announcement is scheduled within window_days calendar days."""
    days_until, date_str, source = fetch_earnings_date(symbol)
    if days_until is not None and 0 <= days_until <= window_days:
        return False, f"earnings in {days_until}d ({date_str}) [{source}]"
    return True, ""



def fetch_cached_debate_verdict(symbol: str) -> dict:
    """Find the latest cached debate file for symbol, return parsed dict or empty dict."""
    try:
        _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
                "judge_rationale": data.get("judge_rationale")
            }
    except Exception:
        return {}


def filter_weak_sector(symbol: str, sector_pulse: Optional[dict] = None) -> Tuple[bool, str]:
    """Reject if the stock's sector index is more than 2% below its EMA20 (RED sector - rotation trap)."""
    try:
        from core_sectors import get_sector
        sector = get_sector(symbol)
        if sector == "OTHERS":
            return True, ""   # don't penalize unmapped stocks
        if sector_pulse and sector in sector_pulse:
            info = sector_pulse[sector]
            pct = info.get("pct_from_ema20")
            if pct is not None and pct < -2.0:
                try:
                    from core_data_fetcher import fetch_nifty_levels
                except ImportError:
                    from core.data_fetcher import fetch_nifty_levels
                nifty_data = fetch_nifty_levels()
                nifty_regime = nifty_data.get("regime", "GREEN").upper()
                if nifty_regime == "RED":
                    return False, f"weak sector RED under RED broad market regime (HARD SKIP: Nifty RED, Sector RED - {sector} {pct:.2f}% below EMA20)"
                else:
                    return False, f"sector rotation trap (SKIP: Nifty {nifty_regime}, Sector RED - {sector} {pct:.2f}% below EMA20)"
    except Exception as exc:
        logger.debug("[filter_weak_sector] %s: %s", symbol, exc)
    return True, ""



def fetch_past_earnings_date(symbol: str) -> Optional[Tuple[int, str]]:
    """Return (days_ago, date_str) for the most recent past earnings date (within the last 30 days)."""
    sym = symbol.strip().upper()
    today = datetime.now(pytz.timezone("Asia/Kolkata")).date()
    
    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    EARNINGS_CACHE_FILE = os.path.join(_ROOT, "data", "earnings_cache.json")
    
    cached_entry = None
    with _earnings_lock:
        if os.path.exists(EARNINGS_CACHE_FILE):
            try:
                with open(EARNINGS_CACHE_FILE, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                    cached_entry = cache.get(sym, {}).get("past")
            except Exception:
                pass
                
    if cached_entry:
        try:
            fetched_at = datetime.fromisoformat(cached_entry.get("fetched_at", "2000-01-01"))
            # Cache past earnings for 3 days
            if datetime.now() - fetched_at < timedelta(days=3):
                date_str = cached_entry.get("date")
                if date_str:
                    past_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                    days_ago = (today - past_date).days
                    if 0 <= days_ago <= 30:
                        return days_ago, date_str
                    else:
                        return None
                else:
                    return None
        except Exception:
            pass

    # Fresh fetch
    date_str = None
    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{sym}.NS")
        df_earn = ticker.earnings_dates
        if df_earn is not None and not df_earn.empty:
            dates = [d.date() if hasattr(d, "date") else d for d in df_earn.index]
            past_dates = [d for d in dates if d <= today]
            if past_dates:
                newest_past = max(past_dates)
                date_str = str(newest_past)
    except Exception as exc:
        logger.debug("[past_earnings] %s: %s", sym, exc)

    # Save to cache
    with _earnings_lock:
        cache = {}
        if os.path.exists(EARNINGS_CACHE_FILE):
            try:
                with open(EARNINGS_CACHE_FILE, "r", encoding="utf-8") as f:
                    cache = json.load(f)
            except Exception:
                pass
        if sym not in cache:
            cache[sym] = {}
        cache[sym]["past"] = {
            "date": date_str or "",
            "fetched_at": datetime.now().isoformat()
        }
        try:
            os.makedirs(os.path.dirname(EARNINGS_CACHE_FILE), exist_ok=True)
            with open(EARNINGS_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2)
        except Exception:
            pass

    if date_str:
        past_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        days_ago = (today - past_date).days
        if 0 <= days_ago <= 30:
            return days_ago, date_str

    return None


def filter_post_earnings_cooling(symbol: str, cooling_days: int = 5) -> Tuple[bool, str]:
    """Reject if the stock had earnings within the last cooling_days calendar days (to let the dust settle)."""
    res = fetch_past_earnings_date(symbol)
    if res:
        days_ago, date_str = res
        if 0 <= days_ago <= cooling_days:
            return False, f"recent earnings ({days_ago}d ago on {date_str})"
    return True, ""


def fetch_screener_shareholding(symbol: str) -> dict:
    """Fetch and cache quarterly shareholding pattern for a symbol from screener.in.
    Returns parsed dictionary or empty dict.
    """
    import os
    import json
    import requests
    from bs4 import BeautifulSoup
    from datetime import datetime, timedelta

    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    SHAREHOLDING_CACHE_FILE = os.path.join(_ROOT, "data", "shareholding_cache.json")
    
    symbol = symbol.strip().upper()
    clean_symbol = symbol.split(".")[0]
    
    # 1. Cache Check
    cache = {}
    cached_entry = None
    with _shareholding_lock:
        if os.path.exists(SHAREHOLDING_CACHE_FILE):
            try:
                with open(SHAREHOLDING_CACHE_FILE, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                    cached_entry = cache.get(clean_symbol)
            except Exception:
                pass
            
    if cached_entry:
        try:
            fetched_at = datetime.fromisoformat(cached_entry.get("fetched_at", "2000-01-01"))
            # Cache for 24 hours
            if datetime.now() - fetched_at < timedelta(hours=24):
                return cached_entry
        except Exception:
            pass

    # 2. Scrape from Screener.in with retry & rate-limiting protection
    try:
        import time
        import random

        url = f"https://www.screener.in/company/{clean_symbol}/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5"
        }
        
        html_text = None
        for attempt in range(2):
            try:
                # Short jitter to prevent scraping bursts triggering rate limits
                time.sleep(random.uniform(0.3, 0.8))
                r = requests.get(url, headers=headers, timeout=8)
                if r.status_code == 200:
                    html_text = r.text
                    break
                elif r.status_code == 429:
                    # Throttled, sleep longer and retry
                    time.sleep(2.0)
            except Exception:
                if attempt == 1:
                    raise
                time.sleep(1.0)
                
        if html_text:
            soup = BeautifulSoup(html_text, 'html.parser')
            sh = soup.find('section', id='shareholding')
            if sh:
                table = sh.find('table')
                if table:
                    headers_list = [th.text.strip() for th in table.find_all('th')]
                    quarters = [h for h in headers_list if h]
                    
                    row_data = {}
                    for tr in table.find_all('tr'):
                        tds = [td.text.strip() for td in tr.find_all('td')]
                        if not tds:
                            continue
                        row_name = tds[0].replace("\xa0+", "").strip().lower()
                        percentages = []
                        for val in tds[1:]:
                            try:
                                pct = float(val.replace("%", "").strip())
                            except ValueError:
                                pct = 0.0
                            percentages.append(pct)
                        row_data[row_name] = percentages
                        
                    promoters = row_data.get("promoters", [])
                    fiis = row_data.get("fiis", [])
                    diis = row_data.get("diis", [])
                    
                    if len(quarters) >= 2 and len(promoters) >= 2 and len(fiis) >= 2 and len(diis) >= 2:
                        # Extract 3 quarters if available; fallback to duplicating Q1 if only 2 quarters exist
                        latest_idx = -1
                        prev_idx = -2
                        prior_idx = -3 if len(quarters) >= 3 else -2
                        
                        fii_latest = fiis[latest_idx]
                        fii_prev = fiis[prev_idx]
                        fii_prior = fiis[prior_idx]
                        
                        dii_latest = diis[latest_idx]
                        dii_prev = diis[prev_idx]
                        dii_prior = diis[prior_idx]
                        
                        prom_latest = promoters[latest_idx]
                        prom_prev = promoters[prev_idx]
                        prom_prior = promoters[prior_idx]
                        
                        fii_change = round(fii_latest - fii_prev, 3)
                        dii_change = round(dii_latest - dii_prev, 3)
                        prom_change = round(prom_latest - prom_prev, 3)
                        
                        # Score with Institutional ConsensusScorer
                        try:
                            from core.scoring import ConsensusScorer, QuarterData
                        except ImportError:
                            from scoring import ConsensusScorer, QuarterData
                            
                        scorer = ConsensusScorer()
                        q0 = QuarterData(fii_pct=fii_latest, dii_pct=dii_latest, promoter_pct=prom_latest)
                        q1 = QuarterData(fii_pct=fii_prev, dii_pct=dii_prev, promoter_pct=prom_prev)
                        q2 = QuarterData(fii_pct=fii_prior, dii_pct=dii_prior, promoter_pct=prom_prior)
                        
                        score_res = scorer.score(q2, q1, q0, pledge_rising=False)
                        
                        entry = {
                            "status": "success",
                            "latest_quarter": quarters[latest_idx],
                            "prev_quarter": quarters[prev_idx],
                            "prior_quarter": quarters[prior_idx] if len(quarters) >= 3 else "N/A",
                            "fii_latest": fii_latest,
                            "fii_prev": fii_prev,
                            "fii_prior": fii_prior,
                            "fii_change": fii_change,
                            "dii_latest": dii_latest,
                            "dii_prev": dii_prev,
                            "dii_prior": dii_prior,
                            "dii_change": dii_change,
                            "promoters_latest": prom_latest,
                            "promoters_prev": prom_prev,
                            "promoters_prior": prom_prior,
                            "promoters_change": prom_change,
                            
                            # Consensus Scoring Details
                            "consensus_score": round(score_res.final_score, 2),
                            "raw_score": round(score_res.raw_score, 2),
                            "persistence": score_res.persistence,
                            "classification": score_res.classification,
                            "breakdown": score_res.breakdown,
                            "components": score_res.components,
                            
                            "fetched_at": datetime.now().isoformat()
                        }
                        
                        with _shareholding_lock:
                            cache = {}
                            if os.path.exists(SHAREHOLDING_CACHE_FILE):
                                try:
                                    with open(SHAREHOLDING_CACHE_FILE, "r", encoding="utf-8") as f:
                                        cache = json.load(f)
                                except Exception:
                                    pass
                            cache[clean_symbol] = entry
                            try:
                                os.makedirs(os.path.dirname(SHAREHOLDING_CACHE_FILE), exist_ok=True)
                                with open(SHAREHOLDING_CACHE_FILE, "w", encoding="utf-8") as f:
                                    json.dump(cache, f, indent=2)
                            except Exception:
                                pass
                            
                        return entry
    except Exception as e:
        logger.warning("[shareholding_fetch] Scrape failed for %s: %s", symbol, e)
        
    if cached_entry:
        return cached_entry
    return {}


def filter_institutional_dealings(symbol: str) -> Tuple[bool, str]:
    """Excludes stocks with institutional consensus exits under the 4-Step Scorer:
      - Reject if classification is 'DISTRIBUTION' (final score <= -3.0).
      - Reject if both FII and DII holdings reduced by more than 0.20% (Consensus Sell, agreement = -1) in the same quarter.
    """
    try:
        sh = fetch_screener_shareholding(symbol)
        if not sh or sh.get("status") != "success":
            return True, ""
            
        fii_chg = sh.get("fii_change", 0.0)
        dii_chg = sh.get("dii_change", 0.0)
        quarter = sh.get("latest_quarter", "latest quarter")
        
        classification = sh.get("classification", "NEUTRAL")
        final_score = sh.get("consensus_score", 0.0)
        c2 = sh.get("components", {}).get("agreement", 0)
        breakdown = sh.get("breakdown", "")
        
        # 1. Consensus Sell (both FII & DII down by > 0.20% in the latest quarter)
        if c2 == -1 or (fii_chg <= -0.20 and dii_chg <= -0.20):
            return False, f"Institutional Consensus Sell in {quarter} (FII down {fii_chg:+.2f}%, DII down {dii_chg:+.2f}%)"
            
        # 2. General DISTRIBUTION score (final score <= -3.0)
        if classification == "DISTRIBUTION" or final_score <= -3.0:
            return False, f"Institutional DISTRIBUTION in {quarter} (Consensus Score {final_score:.1f}; breakdown: {breakdown})"
            
    except Exception as exc:
        logger.warning("[risk_filters/dealings] Check failed for %s: %s", symbol, exc)
        
    return True, ""


def filter_fundamental_strength(symbol: str) -> Tuple[bool, str]:
    """Reject highly unprofitable companies (negative earnings) or poor capital
    efficiency (negative ROE / EBITDA margins) from entering the priority briefs/watchlists.
    """
    sym = symbol.strip().upper()
    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    FUNDAMENTALS_CACHE_FILE = os.path.join(_ROOT, "data", "fundamentals_cache.json")
    
    # 1. Check Cache
    cached_entry = None
    with _fundamentals_lock:
        if os.path.exists(FUNDAMENTALS_CACHE_FILE):
            try:
                with open(FUNDAMENTALS_CACHE_FILE, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                    cached_entry = cache.get(sym)
            except Exception:
                pass

    if cached_entry:
        try:
            fetched_at = datetime.fromisoformat(cached_entry.get("fetched_at", "2000-01-01"))
            # Cache fundamentals for 7 days (they change very slowly!)
            if datetime.now() - fetched_at < timedelta(days=7):
                eps = cached_entry.get("eps")
                pe = cached_entry.get("pe")
                roe = cached_entry.get("roe")
                ebitda = cached_entry.get("ebitda")
                
                is_unprofitable = (eps is not None and eps < 0) or (pe is not None and pe < 0)
                poor_efficiency = (roe is not None and roe < 0) or (ebitda is not None and ebitda < 0)
                if is_unprofitable or poor_efficiency:
                    reasons = []
                    if is_unprofitable:
                        pe_str = f"{pe:.1f}x" if pe is not None else "N/A"
                        reasons.append(f"Negative Earnings (EPS {eps}, PE {pe_str})")
                    if poor_efficiency:
                        roe_str = f"{roe*100:.1f}%" if roe is not None else "N/A"
                        ebitda_str = f"{ebitda*100:.1f}%" if ebitda is not None else "N/A"
                        reasons.append(f"Poor Capital Efficiency (ROE {roe_str}, EBITDA margin {ebitda_str})")
                    return False, f"fundamentally weak ({', '.join(reasons)})"
                return True, ""
        except Exception:
            pass

    # 2. Fresh Fetch from yfinance
    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{sym}.NS")
        info = ticker.info or {}
        
        eps = info.get("trailingEps")
        pe = info.get("trailingPE")
        roe = info.get("returnOnEquity")
        ebitda = info.get("ebitdaMargins")
        
        # Save to cache
        with _fundamentals_lock:
            cache = {}
            if os.path.exists(FUNDAMENTALS_CACHE_FILE):
                try:
                    with open(FUNDAMENTALS_CACHE_FILE, "r", encoding="utf-8") as f:
                        cache = json.load(f)
                except Exception:
                    pass
            cache[sym] = {
                "eps": eps,
                "pe": pe,
                "roe": roe,
                "ebitda": ebitda,
                "fetched_at": datetime.now().isoformat()
            }
            try:
                os.makedirs(os.path.dirname(FUNDAMENTALS_CACHE_FILE), exist_ok=True)
                with open(FUNDAMENTALS_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(cache, f, indent=2)
            except Exception:
                pass

        is_unprofitable = (eps is not None and eps < 0) or (pe is not None and pe < 0)
        poor_efficiency = (roe is not None and roe < 0) or (ebitda is not None and ebitda < 0)
        
        if is_unprofitable or poor_efficiency:
            reasons = []
            if is_unprofitable:
                pe_str = f"{pe:.1f}x" if pe is not None else "N/A"
                reasons.append(f"Negative Earnings (EPS {eps}, PE {pe_str})")
            if poor_efficiency:
                roe_str = f"{roe*100:.1f}%" if roe is not None else "N/A"
                ebitda_str = f"{ebitda*100:.1f}%" if ebitda is not None else "N/A"
                reasons.append(f"Poor Capital Efficiency (ROE {roe_str}, EBITDA margin {ebitda_str})")
            return False, f"fundamentally weak ({', '.join(reasons)})"
            
    except Exception as fund_err:
        logger.warning("[risk_filters/fundamentals] Check failed for %s: %s", sym, fund_err)
        
    return True, ""


def evaluate_nml_logic(symbol: str, tech: dict) -> Tuple[str, str]:
    """
    Evaluate the stock under the revamped No Man's Land (NML) ratio and range logic.
    Returns (status, reason) where status is: "SKIP", "WARNING", "WATCH_SUPPORT", "WATCH_RESISTANCE", "PASS"
    """
    try:
        try:
            from core.trade_plan import calculate_trade_plan
        except ImportError:
            from core_trade_plan import calculate_trade_plan
            
        plan = calculate_trade_plan(tech)
        price = float(tech.get("price") or 0.0)
        support = float(plan.get("entry_zone_max") or tech.get("support_1") or 0.0)
        resistance = float(tech.get("resistance_1") or plan.get("target_1") or 0.0)
        
        if price > 0 and support > 0 and resistance > support:
            range_width = resistance - support
            position_ratio = (price - support) / range_width
            range_pct = (range_width / price) * 100
            
            # Step 2: NML Zone Bounds check [0.35, 0.75]
            if position_ratio < 0.35:
                return "WATCH_SUPPORT", f"Near pullback support (ratio {position_ratio:.2f})"
                
            elif position_ratio > 0.75:
                return "WATCH_RESISTANCE", f"Near breakout resistance (ratio {position_ratio:.2f})"
                
            else: # ratio is between 0.35 and 0.75 (mid-zone)
                # Step 3: Apply narrow-range exemption
                if range_pct < 7.0:
                    # Tight consolidation near top or bottom -> WARNING (Wait for trigger)
                    if 0.60 <= position_ratio <= 0.75:
                        return "WARNING", f"Tight consolidation (WARNING, near top ratio {position_ratio:.2f}, range {range_pct:.1f}%) -> WAIT for breakout"
                    elif 0.35 <= position_ratio <= 0.45:
                        return "WARNING", f"Tight consolidation (WARNING, near bottom ratio {position_ratio:.2f}, range {range_pct:.1f}%) -> WAIT for pullback entry"
                    else:
                        # Dead centre (0.45 - 0.60) -> still SKIP
                        return "SKIP", f"No Man's Land (SKIP, dead centre ratio {position_ratio:.2f}, range {range_pct:.1f}%)"
                else:
                    # Wide range NML -> SKIP (hard reject)
                    return "SKIP", f"No Man's Land (SKIP, ratio {position_ratio:.2f}, range {range_pct:.1f}%)"
                    
    except Exception as exc:
        logger.warning("[nml_logic] Evaluation failed for %s: %s", symbol, exc)
        
    return "PASS", ""


def filter_no_mans_land(symbol: str, tech: dict) -> Tuple[bool, str]:
    """Avoid chasing 'No Man's Land' setups (backward-compatible wrapper)."""
    status, reason = evaluate_nml_logic(symbol, tech)
    if status == "SKIP":
        return False, reason
    return True, ""



def filter_low_liquidity(tech: dict, min_volume: int = 100000) -> Tuple[bool, str]:
    """Reject if the stock's 20-day average daily trading volume is below min_volume (shares)."""
    avg_vol = tech.get("avg_volume_20d", 0)
    if avg_vol and avg_vol < min_volume:
        return False, f"thin volume / low liquidity (20d avg daily volume {avg_vol:,} shares < {min_volume:,})"
    return True, ""


def filter_overextended_1m(tech: dict, max_runup_pct: float = 25.0) -> Tuple[bool, str]:
    """Reject if the stock has already run up more than max_runup_pct in the past month (20 trading days)."""
    runup = tech.get("return_20d", 0.0)
    if runup and runup > max_runup_pct:
        return False, f"overextended (moved +{runup:.1f}% in the past month, chasing is high-risk)"
    return True, ""


# ── Master filter ───────────────────────────────────────────────────────────

def apply_risk_filters(symbol: str, tech: dict,
                       sector_pulse: Optional[dict] = None,
                       thresholds: Optional[dict] = None) -> Tuple[bool, list, str, float]:
    """
    Run the complete, stacked filter stack and classify the stock into
    one of the four states: PASS, WATCH, WARNING, or SKIP.
    Also calculates the regime multiplier based on Sector × Nifty alignment (Gate #9).

    `thresholds` is an optional dict from the UI with parameters.
    """
    t = thresholds or {}
    max_atr  = t.get("max_atr_pct",          MAX_ATR_PCT * 100) / 100
    worst_60 = t.get("max_1d_drop_pct",       WORST_60D_DROP_PCT * 100) / 100
    min_ipo  = int(t.get("min_ipo_days",       60))  # Default is 60 daily bars as per V2.0
    earn_win = int(t.get("earnings_window_days", EARNINGS_WINDOW_DAYS))
    earn_cool = int(t.get("earnings_cooling_days", 5))
    block_sec = bool(t.get("block_weak_sectors", True))
    block_unprofitable = bool(t.get("block_unprofitable", True))
    block_no_mans_land = bool(t.get("block_no_mans_land", True))
    block_low_liquidity = bool(t.get("block_low_liquidity", True))
    block_overextended = bool(t.get("block_overextended", True))
    block_trend_alignment = bool(t.get("block_trend_alignment", True))

    hard_skips = []
    warnings = []
    watch_state = "PASS"
    regime_mult = 1.0

    # 1. Holding status check (Gate #1)
    passed_fund, _ = filter_fundamental_strength(symbol)
    if not passed_fund:
        hard_skips.append("fundamental status is ON_HOLD")


    # 2. Weekly trend check (Gate #2)
    w_trend = str(tech.get("weekly_trend", "UNKNOWN")).strip().upper()
    if w_trend == "BEARISH":
        hard_skips.append("bearish weekly trend")

    # 3. Fundamental strength (Gate #3)
    if block_unprofitable:
        passed, reason = filter_fundamental_strength(symbol)
        if not passed:
            hard_skips.append(reason)

    # 4. Institutional dealings sanity (FII / DII exit - Gate #4)
    passed, reason = filter_institutional_dealings(symbol)
    if not passed:
        hard_skips.append(reason)

    # 5. Liquidity check (Gate #5)
    if block_low_liquidity:
        passed, reason = filter_low_liquidity(tech)
        if not passed:
            hard_skips.append(reason)

    # 6. Overextended (Gate #6)
    if block_overextended:
        passed, reason = filter_overextended_1m(tech)
        if not passed:
            hard_skips.append(reason)

    # 7. Adversarial debate — DECOUPLED from safety gates.
    #    The LLM debate verdict is a judgment call, not a structural-safety block, so it no
    #    longer contributes a hard-skip here. It is surfaced independently in the dashboard
    #    (⚖️ JUDGE badge / Bull-vs-Bear Debate Chamber) via server.py's fetch_cached_debate_verdict.

    # 8. IPO check
    passed, reason = filter_ipo_age(tech, min_ipo)
    if not passed:
        hard_skips.append(reason)

    # 9. Earnings proximity
    passed, reason = filter_earnings_soon(symbol, earn_win)
    if not passed:
        hard_skips.append(reason)
    
    passed, reason = filter_post_earnings_cooling(symbol, earn_cool)
    if not passed:
        hard_skips.append(reason)

    # 10. Sector × Nifty regime alignment (Gate #9)
    if block_sec:
        # Fetch sector and calculate its pct_from_ema20
        from core_sectors import get_sector
        sector = get_sector(symbol)
        sec_info = sector_pulse.get(sector, {}) if sector_pulse else {}
        pct = sec_info.get("pct_from_ema20")
        
        if sector == "OTHERS" or pct is None:
            sector_status = "GREEN"
            pct = 0.0
        else:
            if pct >= 0:
                sector_status = "GREEN"
            elif pct >= -2.0:
                sector_status = "AMBER"
            else:
                sector_status = "RED"
                
        # Fetch broad Nifty regime
        try:
            from core_data_fetcher import fetch_nifty_levels
        except ImportError:
            from core.data_fetcher import fetch_nifty_levels
        nifty_data = fetch_nifty_levels()
        nifty_regime = nifty_data.get("regime", "GREEN").upper()
        
        # Core interaction matrix checks
        if sector_status == "RED":
            regime_mult = 0.0
            if nifty_regime == "RED":
                hard_skips.append(f"regime misalignment (HARD SKIP: Nifty RED, Sector RED - {sector} {pct:.2f}% below EMA20)")
            else:
                hard_skips.append(f"regime misalignment (SKIP: Nifty {nifty_regime}, Sector RED - {sector} {pct:.2f}% below EMA20)")
        else:
            # Surviving cells -> determine multiplier
            if nifty_regime == "GREEN":
                if sector_status == "GREEN":
                    regime_mult = 1.0
                else: # AMBER
                    regime_mult = 0.75
            elif nifty_regime == "AMBER":
                if sector_status == "GREEN":
                    regime_mult = 0.75
                else: # AMBER
                    regime_mult = 0.5
            elif nifty_regime == "RED":
                # Nifty RED + Sector Green -> sector outperformance, valid at 0.75x size
                if sector_status == "GREEN":
                    regime_mult = 0.75
                else: # AMBER
                    regime_mult = 0.5

    # 11. Trend & Distance Alignment
    if block_trend_alignment:
        passed, reason = filter_trend_distance_alignment(tech)
        if not passed:
            warnings.append(reason)

    # 12. CRASH FILTER
    passed, reason = filter_recent_crash(tech, worst_60)
    if not passed:
        hard_skips.append(reason)

    # 13. NML FILTER
    if block_no_mans_land:
        nml_status, nml_reason = evaluate_nml_logic(symbol, tech)
        if nml_status == "SKIP":
            hard_skips.append(nml_reason)
        elif nml_status == "WARNING":
            warnings.append(nml_reason)
        elif nml_status == "WATCH_SUPPORT":
            watch_state = "WATCH"
            warnings.append(nml_reason)
        elif nml_status == "WATCH_RESISTANCE":
            watch_state = "WATCH"
            warnings.append(nml_reason)

    # 14. Volatility check
    passed, reason = filter_volatility(tech, max_atr)
    if not passed:
        hard_skips.append(reason)

    # 15. Distance, R:R and Volume check
    try:
        try:
            from core.trade_plan import calculate_trade_plan
        except ImportError:
            from core_trade_plan import calculate_trade_plan
        plan = calculate_trade_plan(tech)
        price_val = float(tech.get("price") or 0.0)
        
        # Check A: Distance to Entry
        entry_max = float(plan.get("entry_zone_max") or 0.0)
        if price_val > 0 and entry_max > 0:
            distance_pct = ((price_val - entry_max) / price_val) * 100
            if distance_pct > 5.0:
                hard_skips.append(f"excessive distance to entry (price is {distance_pct:.1f}% above entry zone max of {entry_max})")
                
        # Check B: CMP Risk-Reward
        sl_val = float(plan.get("stop_loss") or 0.0)
        t2_val = float(plan.get("target_2") or 0.0)
        if price_val > 0 and sl_val > 0 and t2_val > 0:
            if price_val > sl_val:
                upside_pct = ((t2_val - price_val) / price_val) * 100
                risk = price_val - sl_val
                reward = t2_val - price_val
                cmp_rr = reward / risk if risk > 0 else 0.0
                
                if upside_pct < 2.0:
                    hard_skips.append(f"poor R:R at CMP (upside to target_2 is only {upside_pct:.1f}%)")
                elif cmp_rr < 1.0:
                    hard_skips.append(f"poor R:R at CMP (reward/risk ratio is 1:{cmp_rr:.1f} - target must be further or SL tighter)")
                    
        # Check C: Breakout Volume Quality
        setup_type = plan.get("setup_type", "")
        if setup_type == "BREAKOUT":
            vol_ratio = float(tech.get("volume_ratio") or tech.get("vol_ratio") or 1.0)
            if vol_ratio < 1.2:
                hard_skips.append(f"weak breakout volume (volume ratio {vol_ratio:.2f}x is below 1.2x)")
    except Exception as check_err:
        logger.warning("[risk_filters/new_gates] Evaluation failed for %s: %s", symbol, check_err)


    # ── Multi-Flag Stacking ──────────────────────────────────────────────────
    total_flags = hard_skips + warnings
    if len(total_flags) >= 3:
        hard_skips.append(f"Multi-flag rejection ({len(total_flags)} flags: {'; '.join(total_flags)})")

    # ── Determine Final Verdict and Passed State ─────────────────────────────
    if hard_skips:
        verdict = "SKIP"
        passed_all = False
        reasons = hard_skips
    elif warnings:
        is_watch = any("pullback support" in r.lower() or "breakout resistance" in r.lower() for r in warnings)
        verdict = "WATCH" if (watch_state == "WATCH" or is_watch) else "WARNING"
        passed_all = True
        reasons = warnings
    else:
        verdict = "PASS"
        passed_all = True
        reasons = []

    return passed_all, reasons, verdict, regime_mult


class GateResult:
    def __init__(self, passed: bool, fail_reason: str = ""):
        self.passed = passed
        self.fail_reason = fail_reason


def apply_structural_safety_gates(tech: dict) -> GateResult:
    """
    Run ONLY the slow-moving structural gates (Weekly trend, daily EMA alignment)
    to check if an absent candidate is still structurally healthy.
    """
    if not tech:
        return GateResult(False, "Failed to fetch technical data")
        
    price = tech.get("price", 0)
    ema21 = tech.get("ema21", 0)
    ema50 = tech.get("ema50", 0)
    w_trend = str(tech.get("weekly_trend", "UNKNOWN")).strip().upper()
    
    # 1. Weekly Trend Check (Weekly Trend Check - Gate #2)
    if w_trend == "BEARISH":
        return GateResult(False, "bearish weekly trend")
        
    # 2. Daily EMA Alignment Check (Price > EMA21 > EMA50)
    if price <= 0 or ema21 <= 0 or ema50 <= 0 or not (price > ema21 > ema50):
        return GateResult(False, f"trend alignment failed (Price ₹{price:.2f} must be > EMA21 ₹{ema21:.2f} > EMA50 ₹{ema50:.2f})")
        
    return GateResult(True)



