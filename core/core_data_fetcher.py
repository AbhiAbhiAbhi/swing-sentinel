"""
Data Fetcher Module
Pulls live market data from NSE via yfinance
"""
import math
import time
from datetime import datetime
from typing import Dict, Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

_GLOBAL_CACHE: Dict = {}
_GLOBAL_TTL   = 300  # 5 minutes

# How many trading days back to look for a fresh EMA 9/21 crossover.
# A swing trade is still "fresh" for a few days after the cross — this
# window lets the dashboard badge the setup until momentum is well past.
EMA_CROSS_LOOKBACK = 5

NSE_TICKERS = {
    'RELIANCE': 'RELIANCE.NS', 'HDFCBANK': 'HDFCBANK.NS', 'ICICIBANK': 'ICICIBANK.NS',
    'SBIN': 'SBIN.NS', 'INFY': 'INFY.NS', 'TCS': 'TCS.NS', 'WIPRO': 'WIPRO.NS',
    'MARUTI': 'MARUTI.NS', 'TATAMOTORS': 'TATAMOTORS.NS', 'LT': 'LT.NS',
    'BHARTIARTL': 'BHARTIARTL.NS', 'BEL': 'BEL.NS', 'TATAPOWER': 'TATAPOWER.NS',
    'POWERGRID': 'POWERGRID.NS', 'KOTAKBANK': 'KOTAKBANK.NS',
    'AXISBANK': 'AXISBANK.NS', 'BAJFINANCE': 'BAJFINANCE.NS', 'HINDUNILVR': 'HINDUNILVR.NS',
    'ITC': 'ITC.NS', 'NESTLEIND': 'NESTLEIND.NS', 'SUNPHARMA': 'SUNPHARMA.NS',
}


def check_bse_corporate_action(symbol: str, target_date: str) -> bool:
    """
    Query BSE corporate actions API to verify if a corporate action (demerger, spin-off, split, bonus, rights)
    occurred for a given symbol on (or within 1 day of) the target_date (format: 'YYYY-MM-DD').
    """
    import requests
    from datetime import datetime, timedelta
    
    try:
        dt = datetime.strptime(target_date, "%Y-%m-%d")
        start_date = (dt - timedelta(days=2)).strftime("%Y%m%d")
        end_date = (dt + timedelta(days=2)).strftime("%Y%m%d")
        
        url = "https://api.bseindia.com/BseIndiaAPI/api/DefaultData/w"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.bseindia.com/"
        }
        
        params = {
            "Fdate": start_date,
            "TDate": end_date,
            "Purposecode": "",
            "ddlcategorys": "E",
            "ddlindustrys": "",
            "scripcode": "",
            "segment": "0",
            "strSearch": ""
        }
        
        r = requests.get(url, headers=headers, params=params, timeout=5)
        if r.status_code != 200:
            return False
            
        data = r.json()
        if not isinstance(data, list):
            return False
            
        sym_upper = symbol.strip().upper()
        restructuring_purposes = [
            "SPIN OFF", "DEMERGER", "AMALGAMATION", "SCHEME OF ARRANGEMENT",
            "REDUCTION OF CAPITAL", "RIGHTS", "BONUS", "SPLIT", "STOCK SPLIT"
        ]
        
        for item in data:
            item_short = str(item.get("short_name", "")).strip().upper()
            item_long = str(item.get("long_name", "")).strip().upper()
            item_purpose = str(item.get("Purpose", "")).strip().upper()
            
            if sym_upper == item_short or sym_upper in item_long:
                if any(p in item_purpose for p in restructuring_purposes):
                    print(f"[bse_corp_action] Found matching corporate action for {symbol} on {item.get('Ex_date')}: {item.get('Purpose')}")
                    return True
                    
    except Exception as e:
        print(f"[bse_corp_action] Error checking corporate action for {symbol}: {e}")
        
    return False


def fetch_stock_technicals(symbol: str, df: Optional[pd.DataFrame] = None) -> Dict:
    ticker = NSE_TICKERS.get(symbol.upper(), f"{symbol}.NS")
    try:
        if df is None or df.empty:
            df = yf.Ticker(ticker).history(period="200d")
        if df.empty:
            return {}

        # Drop rows where Close is NaN (happens when today's bar is partially populated)
        df = df.dropna(subset=["Close"])
        if df.empty:
            return {}

        # ── Demerger & Corporate Action Price Adjustments ─────────────────────
        # Identify massive overnight gap downs (<= -15%) representing unadjusted
        # corporate actions (demergers/spin-offs) and dynamically scale historical 
        # prices before the event ONLY if verified against BSE corporate actions.
        n_rows = len(df)
        if n_rows > 1:
            df = df.copy()  # Avoid SettingWithCopyWarning
            for i in range(1, n_rows):
                prev_close = df['Close'].iloc[i - 1]
                curr_open = df['Open'].iloc[i]
                if prev_close > 0:
                    gap_pct = (curr_open - prev_close) / prev_close
                    if gap_pct <= -0.15:
                        gap_date = df.index[i].strftime("%Y-%m-%d")
                        # Verify if a real corporate action occurred on BSE
                        if check_bse_corporate_action(symbol, gap_date):
                            factor = curr_open / prev_close
                            # Scale Open, High, Low, and Close for all prior history
                            for col in ['Open', 'High', 'Low', 'Close']:
                                df.iloc[0:i, df.columns.get_loc(col)] *= factor


        close  = df['Close']
        volume = df['Volume']
        price  = float(close.iloc[-1])

        ema9_s  = close.ewm(span=9).mean()
        ema21_s = close.ewm(span=21).mean()
        ema50_s = close.ewm(span=50).mean()
        ema9    = float(ema9_s.iloc[-1])
        ema21   = float(ema21_s.iloc[-1])
        ema20   = float(close.ewm(span=20).mean().iloc[-1])
        ema50   = float(ema50_s.iloc[-1])
        ema200  = float(close.ewm(span=200).mean().iloc[-1])
        rsi     = _rsi(close)
        macd_d  = _macd(close)
        atr     = _atr(df)
        obv     = _obv(close, volume)
        adx     = _adx(df)

        # Golden & Bearish crossover check: macd crossover in last 4 days
        macd_crossover_days_ago = -1
        macd_bearish_crossover_days_ago = -1
        ema_f = close.ewm(span=12).mean()
        ema_s = close.ewm(span=26).mean()
        macd_line = ema_f - ema_s
        signal_line = macd_line.ewm(span=9).mean()
        for d in range(4):
            older = -(d + 2)
            newer = -(d + 1)
            if abs(older) > len(macd_line):
                break
            o_m, o_s = float(macd_line.iloc[older]), float(signal_line.iloc[older])
            n_m, n_s = float(macd_line.iloc[newer]), float(signal_line.iloc[newer])
            if o_m <= o_s and n_m > n_s and macd_crossover_days_ago == -1:
                macd_crossover_days_ago = d
            if o_m >= o_s and n_m < n_s and macd_bearish_crossover_days_ago == -1:
                macd_bearish_crossover_days_ago = d
            if macd_crossover_days_ago != -1 and macd_bearish_crossover_days_ago != -1:
                break

        high_52w     = float(close.tail(252).max())
        low_52w      = float(close.tail(252).min())
        avg_vol_20   = float(volume.tail(20).mean())
        vol_ratio    = float(volume.iloc[-1] / avg_vol_20) if avg_vol_20 else 1.0
        price_yest   = float(close.iloc[-2])
        change_pct   = ((price - price_yest) / price_yest * 100) if price_yest else 0
        resistance_1 = float(df['High'].iloc[:-1].tail(20).max())
        support_1    = float(df['Low'].iloc[:-1].tail(20).min())
        resistance_2 = float(df['High'].iloc[:-1].tail(60).max())

        # Risk-filter inputs (used by core_risk_filters.py)
        recent_returns = close.pct_change().tail(30)
        worst_60d_pct  = float(recent_returns.min()) if not recent_returns.dropna().empty else 0.0
        price_20d_ago  = float(close.iloc[-21]) if len(close) >= 21 else float(close.iloc[0])
        return_20d     = (price - price_20d_ago) / price_20d_ago if price_20d_ago else 0.0
        bars_count     = len(df)
        first_bar_iso  = df.index[0].strftime("%Y-%m-%d") if len(df) else ""

        near_52w_high  = price >= high_52w * 0.95
        dist_52w_pct   = round((high_52w - price) / high_52w * 100, 2) if high_52w else 0.0
        # EMA 9/21 cross: scan the last EMA_CROSS_LOOKBACK bars and report the
        # most recent crossover, with days_ago (0 = today, 1 = yesterday, …).
        # days_ago = -1 means no cross within the window.
        ema9_cross_ema21    = "none"
        ema9_cross_days_ago = -1
        for d in range(EMA_CROSS_LOOKBACK):
            older = -(d + 2); newer = -(d + 1)
            if abs(older) > len(ema9_s):
                break
            o9, o21 = float(ema9_s.iloc[older]), float(ema21_s.iloc[older])
            n9, n21 = float(ema9_s.iloc[newer]), float(ema21_s.iloc[newer])
            if o9 < o21 and n9 > n21:
                ema9_cross_ema21, ema9_cross_days_ago = "golden", d
                break
            if o9 > o21 and n9 < n21:
                ema9_cross_ema21, ema9_cross_days_ago = "death", d
                break

        # RSI 40-55 continuation pullback (checklist wl4): price > EMA50 AND
        # EMA50 rising vs 20 bars ago AND RSI in the 40-55 reload zone.
        ema50_20b_ago = float(ema50_s.iloc[-21]) if len(ema50_s) >= 21 else ema50
        in_uptrend    = price > ema50 and ema50 > ema50_20b_ago
        rsi_pullback_zone = bool(in_uptrend and 40 <= rsi <= 55)

        # ── 1. Weekly Trend (resampled Weekly EMA 30) ──
        try:
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            weekly_df = df['Close'].resample('W').last().dropna()
            if len(weekly_df) >= 5:
                weekly_ema30 = weekly_df.ewm(span=30, min_periods=1).mean()
                curr_weekly_ema = float(weekly_ema30.iloc[-1])
                weekly_trend = "BULLISH" if price >= curr_weekly_ema else "BEARISH"
            else:
                weekly_trend = "BULLISH" if price >= ema50 else "BEARISH"
        except Exception:
            weekly_trend = "BULLISH" if price >= ema50 else "BEARISH"

        # ── 2. Daily Pattern Base Duration ──
        _rev    = close.iloc[::-1].reset_index(drop=True)
        _cmin   = _rev.expanding().min()
        _cmax   = _rev.expanding().max()
        _spread = (_cmax - _cmin) / _cmin.replace(0, float("nan"))
        base_days = int((_spread <= 0.125).cumprod().sum())
        
        if base_days >= 20:
            base_status = "STABLE_BASE"
        elif base_days >= 5:
            base_status = "CONSOLIDATING"
        else:
            base_status = "VOLATILE"

        # ── 3. False Breakout Risk (rejections, traps, dry volume breakouts) ──
        false_breakout = False
        fb_desc = "Low risk. Price action is stable."

        today_high = float(df['High'].iloc[-1])
        today_low  = float(df['Low'].iloc[-1])
        today_open = float(df['Open'].iloc[-1])

        if today_high >= resistance_1:
            if price < resistance_1:
                false_breakout = True
                fb_desc = f"Failed Breakout: Price hit high of ₹{today_high:.1f} but closed below resistance ₹{resistance_1:.1f}."
            elif today_high > today_low and (today_high - max(today_open, price)) > 0.6 * (today_high - today_low):
                false_breakout = True
                fb_desc = f"Rejection Wick: Strong supply wick at resistance (High ₹{today_high:.1f})."
            elif price >= resistance_1 and vol_ratio < 1.0:
                false_breakout = True
                fb_desc = f"Low Volume Breakout: Breakout occurred but volume ratio ({vol_ratio:.2f}x) is below average."

        false_breakout_risk = "HIGH" if false_breakout else "LOW"

        # Compute volume ratios for the last 5 days
        avg_vol_20_series = volume.rolling(20).mean()
        vol_ratios_5d = []
        for d in range(5):
            idx = -(5 - d)
            if abs(idx) <= len(volume) and avg_vol_20_series.iloc[idx] > 0:
                vol_ratios_5d.append(round(float(volume.iloc[idx] / avg_vol_20_series.iloc[idx]), 2))
            else:
                vol_ratios_5d.append(1.0)

        return {
            'symbol': symbol, 'price': round(price, 2), 'change_pct': round(change_pct, 2),
            'ema9': round(ema9, 2), 'ema21': round(ema21, 2),
            'ema20': round(ema20, 2), 'ema50': round(ema50, 2), 'ema200': round(ema200, 2),
            'rsi': round(rsi, 2), 'macd': round(macd_d['macd'], 2),
            'macd_signal': round(macd_d['signal'], 2), 'macd_histogram': round(macd_d['histogram'], 2),
            'macd_crossover_days_ago': macd_crossover_days_ago,
            'macd_bearish_crossover_days_ago': macd_bearish_crossover_days_ago,
            'atr': round(atr, 2), 'atr_pct': round(atr / price * 100, 2) if price else 0, 'obv': round(obv, 0),
            'adx': round(adx, 2),
            'volume': int(volume.iloc[-1]), 'avg_volume_20d': int(avg_vol_20),
            'volume_ratio': round(vol_ratio, 2),
            'vol_ratios_5d': vol_ratios_5d,
            'high_52w': round(high_52w, 2), 'low_52w': round(low_52w, 2),
            'resistance_1': round(resistance_1, 2), 'resistance_2': round(resistance_2, 2),
            'support_1': round(support_1, 2),
            'worst_60d_pct': round(worst_60d_pct, 4),
            'return_20d': round(return_20d * 100, 2),
            'bars_count':    bars_count,
            'first_bar':     first_bar_iso,
            'near_52w_high': near_52w_high,
            'dist_52w_pct':  dist_52w_pct,
            'ema9_cross_ema21': ema9_cross_ema21,
            'ema9_cross_days_ago': ema9_cross_days_ago,
            'rsi_pullback_zone': rsi_pullback_zone,
            'weekly_trend': weekly_trend,
            'base_days': base_days,
            'base_status': base_status,
            'false_breakout_risk': false_breakout_risk,
            'false_breakout_desc': fb_desc,
            'last_bar_date': df.index[-1].strftime("%Y-%m-%d") if len(df) else "",
            'timestamp': datetime.now().isoformat(),
        }
    except Exception as e:
        print(f"[data_fetcher] Error for {symbol}: {e}")
        return {}


def fetch_history_window(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    Fetch daily OHLC bars for one NSE symbol over an absolute date window
    (used by the PRUNED counterfactual backfill, which needs bars anchored to
    Prune_Date rather than the period=... relative fetches above).

    start_date/end_date: "YYYY-MM-DD" (end exclusive, per yfinance).
    Returns an ascending-indexed DataFrame with High/Low/Close, or an empty
    DataFrame on any failure — never raises.
    """
    ticker = NSE_TICKERS.get(symbol.upper(), f"{symbol}.NS")
    try:
        df = yf.Ticker(ticker).history(start=start_date, end=end_date)
        if df is None or df.empty:
            return pd.DataFrame()
        return df.dropna(subset=["Close"]).sort_index()
    except Exception as e:
        print(f"[history_window] Error for {symbol} ({start_date}..{end_date}): {e}")
        return pd.DataFrame()


def fetch_prices_bulk_dated(symbols: list) -> Dict[str, tuple]:
    """
    Fetch live prices for many NSE symbols in ONE yfinance call, tagged with the
    date of the bar each price came from.
    Returns {symbol: (latest_close_price, "YYYY-MM-DD")}. Missing/errored symbols
    are absent.

    The bar date lets callers tell whether a price is from *today's* trading
    session or a stale prior close (weekend / holiday / pre-open). Treating a
    stale close as a live price made the dashboard show targets as "hit" on days
    the market never traded.
    """
    if not symbols:
        return {}
    tickers = [f"{s}.NS" for s in symbols]
    try:
        data = yf.download(
            tickers, period="2d", group_by="ticker",
            progress=False, threads=True, auto_adjust=False,
        )
    except Exception as e:
        print(f"[bulk_prices] yfinance error: {e}")
        return {}

    out: Dict[str, tuple] = {}
    for sym in symbols:
        try:
            # Multi-symbol return: data[<ticker>][<field>]
            # Single-symbol return: data[<field>]  (no outer level)
            ticker_key = f"{sym}.NS"
            if isinstance(data.columns, pd.MultiIndex):
                if ticker_key in data.columns.levels[0]:
                    close = data[ticker_key]["Close"].dropna()
                else:
                    continue
            else:
                close = data["Close"].dropna()

            if len(close):
                bar_date = close.index[-1].strftime("%Y-%m-%d")
                out[sym] = (float(close.iloc[-1]), bar_date)
        except Exception:
            continue
    return out


def fetch_prices_bulk(symbols: list) -> Dict[str, float]:
    """
    Fetch live prices for many NSE symbols in ONE yfinance call.
    Returns {symbol: latest_close_price}. Missing/errored symbols are absent.

    Thin wrapper over fetch_prices_bulk_dated() for callers that don't need the
    bar date. Used by check_positions_and_notify() — 11× faster than calling
    fetch_stock_technicals() per position when we only need the price.
    """
    return {sym: price for sym, (price, _date) in fetch_prices_bulk_dated(symbols).items()}


def fetch_prices_and_changes_bulk(symbols: list) -> Dict[str, Dict]:
    """
    Fetch live prices, daily returns, and volume ratios for many NSE symbols in ONE yfinance call.
    Returns {symbol: {price, change_pct, volume_ratio}}.
    """
    if not symbols:
        return {}
    tickers = [f"{s}.NS" for s in symbols]
    try:
        data = yf.download(
            tickers, period="5d", group_by="ticker",
            progress=False, threads=True, auto_adjust=False,
        )
    except Exception as e:
        print(f"[bulk_prices] yfinance error: {e}")
        return {}

    out = {}
    for sym in symbols:
        try:
            ticker_key = f"{sym}.NS"
            if isinstance(data.columns, pd.MultiIndex):
                if ticker_key in data.columns.levels[0]:
                    df_sym = data[ticker_key].dropna(subset=["Close"])
                else:
                    continue
            else:
                df_sym = data.dropna(subset=["Close"])
            
            if len(df_sym) >= 2:
                price = float(df_sym["Close"].iloc[-1])
                price_yest = float(df_sym["Close"].iloc[-2])
                change_pct = ((price - price_yest) / price_yest * 100) if price_yest else 0.0
                
                # Volume ratio over the 5d average
                vol = float(df_sym["Volume"].iloc[-1]) if "Volume" in df_sym.columns else 0.0
                avg_vol = float(df_sym["Volume"].mean()) if "Volume" in df_sym.columns and len(df_sym) > 0 else 1.0
                vol_ratio = vol / avg_vol if avg_vol else 1.0
                
                out[sym] = {
                    "price": round(price, 2),
                    "change_pct": round(change_pct, 2),
                    "volume_ratio": round(vol_ratio, 2)
                }
        except Exception:
            continue
    return out


def fetch_nifty_levels() -> Dict:
    try:
        df = yf.Ticker("^NSEI").history(period="1y")
        if df.empty:
            return {'level': 0, 'change': 0, 'change_pct': 0, 'status': 'error',
                    'ema20': 0, 'ema50': 0, 'ema200': 0, 'regime': 'UNKNOWN',
                    'nifty_crossover': False, 'nifty_crossover_days_ago': -1,
                    'signals': {'s1': False, 's2': False, 's3': False}}
        df = df.dropna(subset=["Close"])
        close  = df['Close']
        price  = float(close.iloc[-1])
        prev   = float(close.iloc[-2]) if len(close) > 1 else price
        chg    = price - prev
        
        # Calculate EMAs
        ema20_s = close.ewm(span=20).mean()
        ema50_s = close.ewm(span=50).mean()
        
        ema20  = float(ema20_s.iloc[-1])
        ema50  = float(ema50_s.iloc[-1])
        ema200 = float(close.ewm(span=200).mean().iloc[-1])
        
        # Trend Signals
        s1 = price > ema20
        s2 = price > ema50
        s3 = ema20 > ema50
        
        # Dynamic Crossover Detection (EMA20 vs EMA50 within last 5 sessions)
        crossover = False
        crossover_days = -1
        for d in range(5):
            older = -(d + 2)
            newer = -(d + 1)
            if abs(older) <= len(ema20_s):
                o20, o50 = float(ema20_s.iloc[older]), float(ema50_s.iloc[older])
                n20, n50 = float(ema20_s.iloc[newer]), float(ema50_s.iloc[newer])
                if (o20 <= o50 and n20 > n50) or (o20 >= o50 and n20 < n50):
                    crossover = True
                    crossover_days = d
                    break

        bullish_signals = sum([s1, s2, s3])
        if bullish_signals == 3:
            regime = "GREEN"
        elif bullish_signals == 2:
            regime = "AMBER"
        else:
            regime = "RED"
            
        # Fetch India VIX
        india_vix = 0.0
        try:
            vix_df = yf.Ticker("^INDIAVIX").history(period="2d")
            if not vix_df.empty:
                india_vix = float(vix_df['Close'].iloc[-1])
        except Exception as vix_exc:
            print(f"[data_fetcher] India VIX error: {vix_exc}")

        return {
            'level': round(price, 2), 'change': round(chg, 2),
            'change_pct': round(chg / prev * 100 if prev else 0, 2),
            'status': 'ok',
            'ema20': round(ema20, 2),
            'ema50': round(ema50, 2),
            'ema200': round(ema200, 2),
            'regime': regime,
            'nifty_crossover': crossover,
            'nifty_crossover_days_ago': crossover_days,
            'signals': {'s1': s1, 's2': s2, 's3': s3},
            'vix': round(india_vix, 2)
        }
    except Exception as e:
        print(f"[data_fetcher] Nifty error: {e}")
        return {'level': 0, 'change': 0, 'change_pct': 0, 'status': 'error',
                'ema20': 0, 'ema50': 0, 'ema200': 0, 'regime': 'UNKNOWN',
                'nifty_crossover': False, 'nifty_crossover_days_ago': -1,
                'signals': {'s1': False, 's2': False, 's3': False},
                'vix': 0.0}


def fetch_global_markets() -> Dict:
    """Fetch US indices + USD/INR with 5-minute in-process cache."""
    now = time.time()
    if _GLOBAL_CACHE.get('_ts', 0) + _GLOBAL_TTL > now:
        return {k: v for k, v in _GLOBAL_CACHE.items() if not k.startswith('_')}

    tickers = {
        'sp500':   '^GSPC',
        'nasdaq':  '^IXIC',
        'dow':     '^DJI',
        'usdinr':  'USDINR=X',
    }
    result: Dict = {}
    for key, sym in tickers.items():
        try:
            df = yf.Ticker(sym).history(period="2d")
            df = df.dropna(subset=["Close"])
            if df.empty:
                result[key] = {'price': 0, 'change_pct': 0, 'status': 'error'}
                continue
            price = float(df['Close'].iloc[-1])
            prev  = float(df['Close'].iloc[-2]) if len(df) > 1 else price
            chg   = round((price - prev) / prev * 100, 2) if prev else 0
            result[key] = {'price': round(price, 2), 'change_pct': chg, 'status': 'ok'}
        except Exception as e:
            print(f"[global_markets] {sym} error: {e}")
            result[key] = {'price': 0, 'change_pct': 0, 'status': 'error'}

    result['gift_nifty'] = _fetch_gift_nifty()

    _GLOBAL_CACHE.clear()
    _GLOBAL_CACHE.update(result)
    _GLOBAL_CACHE['_ts'] = now
    return result


_TV_SCANNER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Origin":  "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
}


def _fetch_gift_nifty() -> Dict:
    """
    GIFT Nifty live quote via TradingView's public Scanner API.
    Symbol: NSEIX:NIFTY1! (continuous front-month future on NSE IX).
    No auth, no chart side-effects. Result is cached by fetch_global_markets.
    """
    payload = {
        "symbols": {"tickers": ["NSEIX:NIFTY1!"], "query": {"types": []}},
        "columns": ["close", "change", "change_abs", "open", "high", "low"],
    }
    try:
        r = requests.post(
            "https://scanner.tradingview.com/global/scan",
            json=payload, headers=_TV_SCANNER_HEADERS, timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        rows = data.get("data") or []
        if not rows:
            return {'price': 0, 'change_pct': 0, 'status': 'error',
                    'note': 'GIFT Nifty not returned by TV scanner'}
        d = rows[0].get("d") or []
        price = float(d[0]) if len(d) > 0 and d[0] is not None else 0
        chg   = float(d[1]) if len(d) > 1 and d[1] is not None else 0
        return {'price': round(price, 2), 'change_pct': round(chg, 2),
                'status': 'ok', 'source': 'tv:NSEIX:NIFTY1!'}
    except Exception as e:
        print(f"[gift_nifty] fetch error: {e}")
        return {'price': 0, 'change_pct': 0, 'status': 'error', 'note': str(e)}


_FII_CACHE: Dict = {}
_FII_TTL = 15 * 60  # 15 minutes — NSE publishes once per session

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/reports/fii-dii",
}


def _empty_fii() -> Dict:
    return {
        'fii_today': 0, 'dii_today': 0,
        'fii_last_5_days': [0] * 5, 'dii_last_5_days': [0] * 5,
        'fii_trend': 'unknown', 'note': 'Live FII data unavailable',
    }


def fetch_fii_dii_flow(days: int = 5) -> Dict:
    """
    Fetch latest FII/DII cash-market net flow from NSE.
    Returns net values in Rupees Crore. Cached 15 min.
    `fii_last_5_days`/`dii_last_5_days` carry today's value in slot 0
    and zeros for older days (NSE doesn't expose a stable history JSON).
    """
    now = time.time()
    if _FII_CACHE.get('_ts', 0) + _FII_TTL > now and _FII_CACHE.get('fii_today') is not None:
        return {k: v for k, v in _FII_CACHE.items() if not k.startswith('_')}

    try:
        sess = requests.Session()
        sess.headers.update(_NSE_HEADERS)
        # Warm cookies — NSE rejects naked API calls
        try:
            sess.get("https://www.nseindia.com/reports/fii-dii", timeout=8)
        except Exception:
            pass
        r = sess.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=10)
        r.raise_for_status()
        rows = r.json()
        if not isinstance(rows, list):
            return _empty_fii()

        def _net(row: Dict) -> float:
            for k in ('netValue', 'net', 'netBuySell', 'net_value'):
                v = row.get(k)
                if v not in (None, ''):
                    try:
                        return float(str(v).replace(',', ''))
                    except (TypeError, ValueError):
                        continue
            try:
                buy  = float(str(row.get('buyValue', 0)).replace(',', ''))
                sell = float(str(row.get('sellValue', 0)).replace(',', ''))
                return buy - sell
            except (TypeError, ValueError):
                return 0.0

        fii_net = dii_net = 0.0
        for row in rows:
            cat = str(row.get('category', '')).upper()
            if cat.startswith('FII') or cat.startswith('FPI'):
                fii_net = _net(row)
            elif cat.startswith('DII'):
                dii_net = _net(row)

        result = {
            'fii_today':       round(fii_net, 2),
            'dii_today':       round(dii_net, 2),
            'fii_last_5_days': [round(fii_net, 2), 0, 0, 0, 0],
            'dii_last_5_days': [round(dii_net, 2), 0, 0, 0, 0],
            'fii_trend': 'positive' if fii_net > 0 else 'negative' if fii_net < 0 else 'flat',
            'note': 'NSE cash-market net (Cr) — only latest session available',
        }
        _FII_CACHE.clear()
        _FII_CACHE.update(result)
        _FII_CACHE['_ts'] = now
        return result
    except Exception as e:
        print(f"[fii_dii] fetch error: {e}")
        return _empty_fii()


# ── Indicator helpers ────────────────────────────────────────────────────────

def _rsi(prices: pd.Series, period: int = 14) -> float:
    if len(prices) < period:
        return 50.0
    delta = prices.diff()
    gain  = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss  = (-delta.where(delta < 0, 0.0)).rolling(period).mean()

    g = float(gain.iloc[-1]) if not gain.empty else 0.0
    l = float(loss.iloc[-1]) if not loss.empty else 0.0

    if math.isnan(g) or math.isnan(l):
        return 50.0
    if l == 0.0:
        return 100.0 if g > 0.0 else 50.0
    rs = g / l
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(float(rsi), 2)


def _macd(prices: pd.Series, fast=12, slow=26, sig=9) -> Dict:
    ema_f = prices.ewm(span=fast).mean()
    ema_s = prices.ewm(span=slow).mean()
    macd  = ema_f - ema_s
    signal = macd.ewm(span=sig).mean()
    return {'macd': float(macd.iloc[-1]), 'signal': float(signal.iloc[-1]),
            'histogram': float((macd - signal).iloc[-1])}


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    hi, lo, cl = df['High'], df['Low'], df['Close']
    tr = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def _obv(prices: pd.Series, volumes: pd.Series) -> float:
    direction = np.sign(prices.diff().fillna(0))
    return float((direction * volumes).cumsum().iloc[-1])


def _adx(df: pd.DataFrame, period: int = 14) -> float:
    try:
        hi, lo, cl = df['High'], df['Low'], df['Close']
        n = len(df)
        if n < period * 2:
            return 20.0
        
        # True Range
        tr = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1/period, adjust=False).mean()
        
        up_move = hi.diff()
        down_move = -lo.diff()
        
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        
        plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / atr.replace(0, float('nan'))
        minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / atr.replace(0, float('nan'))
        
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float('nan'))
        adx = dx.ewm(alpha=1/period, adjust=False).mean()
        
        return float(adx.iloc[-1]) if not adx.dropna().empty else 20.0
    except Exception:
        return 20.0
