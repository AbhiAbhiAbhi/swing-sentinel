"""
Data Fetcher Module
Pulls live market data from NSE via yfinance
"""
import time
from datetime import datetime
from typing import Dict

import numpy as np
import pandas as pd
import yfinance as yf

_GLOBAL_CACHE: Dict = {}
_GLOBAL_TTL   = 300  # 5 minutes

NSE_TICKERS = {
    'RELIANCE': 'RELIANCE.NS', 'HDFCBANK': 'HDFCBANK.NS', 'ICICIBANK': 'ICICIBANK.NS',
    'SBIN': 'SBIN.NS', 'INFY': 'INFY.NS', 'TCS': 'TCS.NS', 'WIPRO': 'WIPRO.NS',
    'MARUTI': 'MARUTI.NS', 'TATAMOTORS': 'TATAMOTORS.NS', 'LT': 'LT.NS',
    'BHARTIARTL': 'BHARTIARTL.NS', 'BEL': 'BEL.NS', 'TATAPOWER': 'TATAPOWER.NS',
    'POWERGRID': 'POWERGRID.NS', 'KOTAKBANK': 'KOTAKBANK.NS',
    'AXISBANK': 'AXISBANK.NS', 'BAJFINANCE': 'BAJFINANCE.NS', 'HINDUNILVR': 'HINDUNILVR.NS',
    'ITC': 'ITC.NS', 'NESTLEIND': 'NESTLEIND.NS', 'SUNPHARMA': 'SUNPHARMA.NS',
}


def fetch_stock_technicals(symbol: str) -> Dict:
    ticker = NSE_TICKERS.get(symbol.upper(), f"{symbol}.NS")
    try:
        df = yf.Ticker(ticker).history(period="200d")
        if df.empty:
            return {}

        # Drop rows where Close is NaN (happens when today's bar is partially populated)
        df = df.dropna(subset=["Close"])
        if df.empty:
            return {}

        close  = df['Close']
        volume = df['Volume']
        price  = float(close.iloc[-1])

        ema9_s  = close.ewm(span=9).mean()
        ema21_s = close.ewm(span=21).mean()
        ema9    = float(ema9_s.iloc[-1])
        ema21   = float(ema21_s.iloc[-1])
        ema20   = float(close.ewm(span=20).mean().iloc[-1])
        ema50   = float(close.ewm(span=50).mean().iloc[-1])
        ema200  = float(close.ewm(span=200).mean().iloc[-1])
        rsi     = _rsi(close)
        macd_d  = _macd(close)
        atr     = _atr(df)
        obv     = _obv(close, volume)

        high_52w     = float(close.tail(252).max())
        low_52w      = float(close.tail(252).min())
        avg_vol_20   = float(volume.tail(20).mean())
        vol_ratio    = float(volume.iloc[-1] / avg_vol_20) if avg_vol_20 else 1.0
        price_yest   = float(close.iloc[-2])
        change_pct   = ((price - price_yest) / price_yest * 100) if price_yest else 0
        resistance_1 = float(df['High'].tail(20).max())
        support_1    = float(df['Low'].tail(20).min())
        resistance_2 = float(df['High'].tail(60).max())

        # Risk-filter inputs (used by core_risk_filters.py)
        recent_returns = close.pct_change().tail(60)
        worst_60d_pct  = float(recent_returns.min()) if not recent_returns.dropna().empty else 0.0
        bars_count     = len(df)
        first_bar_iso  = df.index[0].strftime("%Y-%m-%d") if len(df) else ""

        near_52w_high  = price >= high_52w * 0.95
        dist_52w_pct   = round((high_52w - price) / high_52w * 100, 2) if high_52w else 0.0
        # EMA 9/21 cross: yesterday 9<21, today 9>21 (golden) or vice-versa (death)
        ema9_prev  = float(ema9_s.iloc[-2])
        ema21_prev = float(ema21_s.iloc[-2])
        if ema9_prev < ema21_prev and ema9 > ema21:
            ema9_cross_ema21 = "golden"
        elif ema9_prev > ema21_prev and ema9 < ema21:
            ema9_cross_ema21 = "death"
        else:
            ema9_cross_ema21 = "none"

        return {
            'symbol': symbol, 'price': round(price, 2), 'change_pct': round(change_pct, 2),
            'ema9': round(ema9, 2), 'ema21': round(ema21, 2),
            'ema20': round(ema20, 2), 'ema50': round(ema50, 2), 'ema200': round(ema200, 2),
            'rsi': round(rsi, 2), 'macd': round(macd_d['macd'], 2),
            'macd_signal': round(macd_d['signal'], 2), 'macd_histogram': round(macd_d['histogram'], 2),
            'atr': round(atr, 2), 'atr_pct': round(atr / price * 100, 2) if price else 0, 'obv': round(obv, 0),
            'volume': int(volume.iloc[-1]), 'avg_volume_20d': int(avg_vol_20),
            'volume_ratio': round(vol_ratio, 2),
            'high_52w': round(high_52w, 2), 'low_52w': round(low_52w, 2),
            'resistance_1': round(resistance_1, 2), 'resistance_2': round(resistance_2, 2),
            'support_1': round(support_1, 2),
            'worst_60d_pct': round(worst_60d_pct, 4),
            'bars_count':    bars_count,
            'first_bar':     first_bar_iso,
            'near_52w_high': near_52w_high,
            'dist_52w_pct':  dist_52w_pct,
            'ema9_cross_ema21': ema9_cross_ema21,
            'timestamp': datetime.now().isoformat(),
        }
    except Exception as e:
        print(f"[data_fetcher] Error for {symbol}: {e}")
        return {}


def fetch_prices_bulk(symbols: list) -> Dict[str, float]:
    """
    Fetch live prices for many NSE symbols in ONE yfinance call.
    Returns {symbol: latest_close_price}. Missing/errored symbols are absent.

    Used by check_positions_and_notify() — 11× faster than calling
    fetch_stock_technicals() per position when we only need the price.
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

    out: Dict[str, float] = {}
    for sym in symbols:
        try:
            # Multi-symbol return: data[<ticker>][<field>]
            # Single-symbol return: data[<field>]  (no outer level)
            if len(symbols) == 1:
                close = data["Close"].dropna()
            else:
                close = data[f"{sym}.NS"]["Close"].dropna()
            if len(close):
                out[sym] = float(close.iloc[-1])
        except Exception:
            continue
    return out


def fetch_nifty_levels() -> Dict:
    try:
        df = yf.Ticker("^NSEI").history(period="1y")
        if df.empty:
            return {'level': 0, 'change': 0, 'change_pct': 0, 'status': 'error',
                    'ema50': 0, 'ema200': 0, 'regime': 'UNKNOWN'}
        df = df.dropna(subset=["Close"])
        close  = df['Close']
        price  = float(close.iloc[-1])
        prev   = float(close.iloc[-2]) if len(close) > 1 else price
        chg    = price - prev
        ema50  = float(close.ewm(span=50).mean().iloc[-1])
        ema200 = float(close.ewm(span=200).mean().iloc[-1])
        if price > ema50 > ema200:
            regime = "GREEN"
        elif price < ema50 < ema200:
            regime = "RED"
        else:
            regime = "AMBER"
        return {
            'level': round(price, 2), 'change': round(chg, 2),
            'change_pct': round(chg / prev * 100 if prev else 0, 2),
            'status': 'ok', 'ema50': round(ema50, 2), 'ema200': round(ema200, 2),
            'regime': regime,
        }
    except Exception as e:
        print(f"[data_fetcher] Nifty error: {e}")
        return {'level': 0, 'change': 0, 'change_pct': 0, 'status': 'error',
                'ema50': 0, 'ema200': 0, 'regime': 'UNKNOWN'}


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

    _GLOBAL_CACHE.clear()
    _GLOBAL_CACHE.update(result)
    _GLOBAL_CACHE['_ts'] = now
    return result


def fetch_fii_dii_flow(days: int = 5) -> Dict:
    return {
        'fii_today': 0, 'dii_today': 0,
        'fii_last_5_days': [0] * 5, 'dii_last_5_days': [0] * 5,
        'fii_trend': 'unknown', 'note': 'Live FII data unavailable',
    }


# ── Indicator helpers ────────────────────────────────────────────────────────

def _rsi(prices: pd.Series, period: int = 14) -> float:
    if len(prices) < period:
        return 50.0
    delta  = prices.diff()
    gain   = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss   = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs     = gain / loss.replace(0, float('nan'))
    rsi    = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not rsi.empty else 50.0


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
