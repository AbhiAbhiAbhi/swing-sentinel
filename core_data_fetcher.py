"""
Data Fetcher Module
Pulls live market data from NSE via yfinance
"""
import yfinance as yf
import pandas as pd
from datetime import datetime
from typing import Dict, List

NSE_TICKERS = {
    'RELIANCE': 'RELIANCE.NS', 'HDFCBANK': 'HDFCBANK.NS', 'ICICIBANK': 'ICICIBANK.NS',
    'SBIN': 'SBIN.NS', 'INFY': 'INFY.NS', 'TCS': 'TCS.NS', 'WIPRO': 'WIPRO.NS',
    'MARUTI': 'MARUTI.NS', 'TATAMOTORS': 'TATAMOTORS.NS', 'LT': 'LT.NS',
    'BHARTIARTL': 'BHARTIARTL.NS', 'BEL': 'BEL.NS', 'TATAPOWER': 'TATAPOWER.NS',
    'POWERGRID': 'POWERGRID.NS', 'HDFCBANK': 'HDFCBANK.NS', 'KOTAKBANK': 'KOTAKBANK.NS',
    'AXISBANK': 'AXISBANK.NS', 'BAJFINANCE': 'BAJFINANCE.NS', 'HINDUNILVR': 'HINDUNILVR.NS',
    'ITC': 'ITC.NS', 'NESTLEIND': 'NESTLEIND.NS', 'SUNPHARMA': 'SUNPHARMA.NS',
}


def fetch_stock_technicals(symbol: str) -> Dict:
    ticker = NSE_TICKERS.get(symbol.upper(), f"{symbol}.NS")
    try:
        df = yf.Ticker(ticker).history(period="200d")
        if df.empty:
            return {}

        close  = df['Close']
        volume = df['Volume']
        price  = float(close.iloc[-1])

        ema20  = float(close.ewm(span=20).mean().iloc[-1])
        ema50  = float(close.ewm(span=50).mean().iloc[-1])
        ema200 = float(close.ewm(span=200).mean().iloc[-1])
        rsi    = _rsi(close)
        macd_d = _macd(close)
        atr    = _atr(df)
        obv    = _obv(close, volume)

        high_52w     = float(close.tail(252).max())
        low_52w      = float(close.tail(252).min())
        avg_vol_20   = float(volume.tail(20).mean())
        vol_ratio    = float(volume.iloc[-1] / avg_vol_20) if avg_vol_20 else 1.0
        price_yest   = float(close.iloc[-2])
        change_pct   = ((price - price_yest) / price_yest * 100) if price_yest else 0
        resistance_1 = float(df['High'].tail(20).max())
        support_1    = float(df['Low'].tail(20).min())
        resistance_2 = float(df['High'].tail(60).max())

        return {
            'symbol': symbol, 'price': round(price, 2), 'change_pct': round(change_pct, 2),
            'ema20': round(ema20, 2), 'ema50': round(ema50, 2), 'ema200': round(ema200, 2),
            'rsi': round(rsi, 2), 'macd': round(macd_d['macd'], 2),
            'macd_signal': round(macd_d['signal'], 2), 'macd_histogram': round(macd_d['histogram'], 2),
            'atr': round(atr, 2), 'obv': round(obv, 0),
            'volume': int(volume.iloc[-1]), 'avg_volume_20d': int(avg_vol_20),
            'volume_ratio': round(vol_ratio, 2),
            'high_52w': round(high_52w, 2), 'low_52w': round(low_52w, 2),
            'resistance_1': round(resistance_1, 2), 'resistance_2': round(resistance_2, 2),
            'support_1': round(support_1, 2),
            'timestamp': datetime.now().isoformat(),
        }
    except Exception as e:
        print(f"[data_fetcher] Error for {symbol}: {e}")
        return {}


def fetch_nifty_levels() -> Dict:
    try:
        df = yf.Ticker("^NSEI").history(period="2d")
        if df.empty:
            return {'level': 0, 'change': 0, 'change_pct': 0, 'status': 'error'}
        close = float(df['Close'].iloc[-1])
        prev  = float(df['Close'].iloc[-2]) if len(df) > 1 else close
        chg   = close - prev
        return {'level': round(close, 2), 'change': round(chg, 2),
                'change_pct': round(chg / prev * 100 if prev else 0, 2), 'status': 'ok'}
    except Exception as e:
        print(f"[data_fetcher] Nifty error: {e}")
        return {'level': 0, 'change': 0, 'change_pct': 0, 'status': 'error'}


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
    obv = [0.0]
    for i in range(1, len(prices)):
        if prices.iloc[i] > prices.iloc[i - 1]:
            obv.append(obv[-1] + volumes.iloc[i])
        elif prices.iloc[i] < prices.iloc[i - 1]:
            obv.append(obv[-1] - volumes.iloc[i])
        else:
            obv.append(obv[-1])
    return obv[-1]
