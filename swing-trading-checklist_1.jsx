import { useState } from "react";

const sections = [
  {
    id: "watchlist",
    label: "Watchlist Building",
    time: "Previous Evening (After 3:30 PM)",
    color: "#6366F1",
    icon: "🔍",
    items: [
      {
        id: "wl0",
        text: "One-time setup: install Python automation stack",
        code: {
          python: `# Run ONCE in CMD/Terminal — sets up everything you need
pip install yfinance pandas-ta plotly pandas numpy
pip install nsepython tradingview-ta
pip install pkscreener         # optional: NSE-specific screener

# Stack purpose:
#   yfinance    → fetch NSE/BSE OHLCV data (use .NS suffix for NSE)
#   pandas-ta   → 130+ technical indicators (no TA-Lib C deps)
#   plotly      → interactive candlestick charts (TradingView feel)
#   nsepython   → option chain, FII/DII, sector heatmaps
#   pkscreener  → ready-made NSE breakout scanner (CLI)`,
        },
      },
      {
        id: "wl1",
        text: "Run Chartink screener for breakout candidates",
        code: {
          chartink: `( {cash} ( 
  latest close > 50
  and daily volume > 500000
  and latest rsi(14) >= 40
  and latest rsi(14) <= 70
  and latest adx(14) >= 20
  and latest macd line(26,12,9) > latest macd signal(26,12,9)
  and latest ema(close,20) > latest ema(close,50)
  and latest close > latest ema(close,200)
  and latest atr(14) / latest close * 100 <= 5
) )`,
          python: `import yfinance as yf
import pandas_ta as ta
import pandas as pd

def screen(symbol):
    df = yf.download(f"{symbol}.NS", period="1y", progress=False)
    if len(df) < 200: return None

    df["EMA20"]  = ta.ema(df["Close"], 20)
    df["EMA50"]  = ta.ema(df["Close"], 50)
    df["EMA200"] = ta.ema(df["Close"], 200)
    df["RSI"]    = ta.rsi(df["Close"], 14)
    df["ADX"]    = ta.adx(df["High"], df["Low"], df["Close"])["ADX_14"]
    macd = ta.macd(df["Close"])
    df["MACD"], df["MACDsig"] = macd["MACD_12_26_9"], macd["MACDs_12_26_9"]
    df["ATRpct"] = ta.atr(df["High"], df["Low"], df["Close"], 14) / df["Close"] * 100

    last = df.iloc[-1]
    return last if (
        last["Close"] > 50 and last["Volume"] > 500000 and
        40 <= last["RSI"] <= 70 and last["ADX"] >= 20 and
        last["MACD"] > last["MACDsig"] and
        last["EMA20"] > last["EMA50"] and
        last["Close"] > last["EMA200"] and
        last["ATRpct"] <= 5
    ) else None

# Loop your universe (Nifty 500 etc.)
candidates = [s for s in NIFTY500 if screen(s) is not None]
print(f"Passed: {len(candidates)} stocks")`,
        },
      },
      {
        id: "wl2",
        text: "Identify stocks near 52-week highs",
        code: {
          python: `import yfinance as yf

def near_52w_high(symbol, threshold_pct=3):
    df = yf.download(f"{symbol}.NS", period="1y", progress=False)
    high_52w = df["High"].max()
    last = df["Close"].iloc[-1]
    dist_pct = (high_52w - last) / high_52w * 100
    return dist_pct <= threshold_pct, dist_pct

# Example
near, dist = near_52w_high("RELIANCE")
print(f"Near 52W high: {near} (distance: {dist:.2f}%)")`,
        },
      },
      {
        id: "wl3",
        text: "Detect 9/21 EMA bullish crossover",
        code: {
          python: `import yfinance as yf
import pandas_ta as ta

def ema_crossover(symbol):
    df = yf.download(f"{symbol}.NS", period="3mo", progress=False)
    df["EMA9"]  = ta.ema(df["Close"], 9)
    df["EMA21"] = ta.ema(df["Close"], 21)

    # Bullish crossover = EMA9 was below EMA21 yesterday, above today
    today = df.iloc[-1]
    yest  = df.iloc[-2]
    return today["EMA9"] > today["EMA21"] and yest["EMA9"] <= yest["EMA21"]

# Scan watchlist
for sym in ["RELIANCE", "TCS", "INFY", "HDFCBANK"]:
    if ema_crossover(sym):
        print(f"✓ Bullish crossover: {sym}")`,
        },
      },
      {
        id: "wl4",
        text: "Find RSI 40-55 continuation setups (uptrend pullback)",
        code: {
          python: `import yfinance as yf
import pandas_ta as ta

def rsi_pullback_setup(symbol):
    df = yf.download(f"{symbol}.NS", period="6mo", progress=False)
    df["RSI"]   = ta.rsi(df["Close"], 14)
    df["EMA50"] = ta.ema(df["Close"], 50)

    last = df.iloc[-1]
    in_uptrend = last["Close"] > last["EMA50"] and \\
                 last["EMA50"] > df["EMA50"].iloc[-20]
    pullback = 40 <= last["RSI"] <= 55
    return in_uptrend and pullback

# Quick scan
hits = [s for s in NIFTY500 if rsi_pullback_setup(s)]
print(f"Pullback setups: {hits}")`,
        },
      },
      {
        id: "wl5",
        text: "Mark exact breakout level + stop-loss zone for each candidate",
      },
      {
        id: "wl6",
        text: "Save shortlist (3-5 max) with alert levels",
        code: {
          python: `import pandas as pd
from datetime import date

# Save shortlist with entry / SL / target levels
shortlist = pd.DataFrame([
    {"symbol": "RELIANCE", "entry": 1350, "sl": 1320, "t1": 1395, "t2": 1440},
    {"symbol": "TCS",      "entry": 4200, "sl": 4110, "t1": 4335, "t2": 4470},
    {"symbol": "INFY",     "entry": 1820, "sl": 1785, "t1": 1872, "t2": 1925},
])
shortlist["date"] = date.today()
shortlist["rr"]   = (shortlist["t1"] - shortlist["entry"]) / \\
                   (shortlist["entry"] - shortlist["sl"])
shortlist.to_csv("watchlist.csv", mode="a", header=False, index=False)
print(shortlist)`,
        },
      },
    ],
  },
  {
    id: "pre-market",
    label: "Pre-Market Prep",
    time: "8:00 – 9:15 AM",
    color: "#F59E0B",
    icon: "☀️",
    items: [
      {
        id: "pm1",
        text: "Check GIFT Nifty direction (live from 6:30 AM)",
        code: {
          python: `import yfinance as yf

# GIFT Nifty ticker on Yahoo
gift = yf.Ticker("^NSEI").history(period="2d")
prev_close = gift["Close"].iloc[-2]
current    = gift["Close"].iloc[-1]
gap_pct    = (current - prev_close) / prev_close * 100

print(f"Previous Close: {prev_close:.2f}")
print(f"Current Level:  {current:.2f}")
print(f"Gap %:          {gap_pct:+.2f}%")

if   gap_pct > 0.5:  print("📈 Strong gap-up expected")
elif gap_pct < -0.5: print("📉 Strong gap-down expected")
else:                print("➡  Flat open expected")`,
          formula: `GAP % = (GIFT Nifty − Previous Nifty Close) / Previous Close × 100

Interpretation:
  > +0.5%       Strong gap-up expected
  −0.2 to +0.2  Flat open expected
  < −0.5%       Strong gap-down expected

~70% of large gaps (>1%) partially fill within 30-45 min.`,
        },
      },
      {
        id: "pm2",
        text: "Check global cues (US close, Dow, Nasdaq, S&P)",
        code: {
          python: `import yfinance as yf

# Fetch yesterday's US market closes
tickers = {"DOW": "^DJI", "NASDAQ": "^IXIC", "S&P 500": "^GSPC"}

for name, tick in tickers.items():
    df = yf.Ticker(tick).history(period="2d")
    chg = (df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1) * 100
    print(f"{name:10} {df['Close'].iloc[-1]:>10,.2f}  ({chg:+.2f}%)")`,
        },
      },
      {
        id: "pm3",
        text: "Check USD/INR — weakness signals FII outflows",
        code: {
          python: `import yfinance as yf

inr = yf.Ticker("INR=X").history(period="5d")
current = inr["Close"].iloc[-1]
week_chg = (current / inr["Close"].iloc[0] - 1) * 100

print(f"USD/INR: ₹{current:.4f}")
print(f"5-day change: {week_chg:+.2f}%")
# Rule: INR weakening >0.5% in a week → expect FII selling pressure`,
        },
      },
      {
        id: "pm4",
        text: "Review FII/DII data from previous session",
        code: {
          python: `from nsepython import nse_fii_dii

# Fetches official FII/DII activity from NSE
data = nse_fii_dii()
print(data)
# Output: net buy/sell amounts in cash + F&O for both FII and DII`,
        },
      },
      { id: "pm5", text: "Note major news: RBI, budget, geopolitical events" },
      { id: "pm6", text: "Determine market bias: bullish / bearish / neutral" },
      { id: "pm7", text: "Re-check watchlist — still aligned with market direction?" },
    ],
  },
  {
    id: "chart-analysis",
    label: "Chart Analysis",
    time: "Before Entry",
    color: "#10B981",
    icon: "📊",
    items: [
      {
        id: "ca1",
        text: "Render interactive candlestick chart (weekly + daily)",
        code: {
          python: `import yfinance as yf
import pandas_ta as ta
import plotly.graph_objects as go

df = yf.download("RELIANCE.NS", period="6mo", progress=False)
df["EMA20"]  = ta.ema(df["Close"], 20)
df["EMA50"]  = ta.ema(df["Close"], 50)
df["EMA200"] = ta.ema(df["Close"], 200)

fig = go.Figure([
    go.Candlestick(x=df.index, open=df["Open"], high=df["High"],
                   low=df["Low"], close=df["Close"], name="Price"),
    go.Scatter(x=df.index, y=df["EMA20"],  line=dict(color="orange"), name="EMA20"),
    go.Scatter(x=df.index, y=df["EMA50"],  line=dict(color="blue"),   name="EMA50"),
    go.Scatter(x=df.index, y=df["EMA200"], line=dict(color="purple"), name="EMA200"),
])
fig.update_layout(title="RELIANCE — Daily", xaxis_rangeslider_visible=False,
                  template="plotly_dark", height=600)
fig.show()    # Opens interactive chart in browser`,
        },
      },
      {
        id: "ca2",
        text: "Detect chart pattern: flat base / bull flag",
        code: {
          python: `import pandas_ta as ta

def is_flat_base(df, length=15, max_range=8, max_atr=2.5):
    """Tight consolidation + volatility contraction = flat base."""
    recent = df.tail(length)
    rng_pct = (recent["High"].max() - recent["Low"].min()) / recent["Low"].min() * 100
    atr_pct = (ta.atr(recent["High"], recent["Low"], recent["Close"], 14) /
               recent["Close"] * 100).mean()
    return rng_pct < max_range and atr_pct < max_atr, rng_pct, atr_pct

is_base, rng, atr = is_flat_base(df, length=15)
if is_base:
    print(f"✓ Flat base detected (range: {rng:.2f}%, ATR: {atr:.2f}%)")
else:
    print(f"✗ Not a clean base (range: {rng:.2f}%)")`,
        },
      },
      {
        id: "ca3",
        text: "Check volatility contraction (Minervini concept)",
        code: {
          python: `import pandas_ta as ta

def volatility_contracting(df):
    atr = ta.atr(df["High"], df["Low"], df["Close"], 14)
    return atr.iloc[-1] < atr.iloc[-6] < atr.iloc[-11]

if volatility_contracting(df):
    print("✓ ATR compressing → breakout fuel building")`,
          formula: `Volatility Contraction (VCP):
  ATR(14) today  <  ATR(14) 5 days ago  <  ATR(14) 10 days ago

Shrinking ATR + tight range = energy compressing.
Breakout from a VCP pattern is typically more explosive
than from a wide-range consolidation.`,
        },
      },
      {
        id: "ca4",
        text: "Check sector strength — relative strength vs sector index",
        code: {
          python: `import yfinance as yf

# Stock vs its sector (IT example)
stock  = yf.download("INFY.NS",   period="6mo", progress=False)["Close"]
sector = yf.download("^CNXIT",    period="6mo", progress=False)["Close"]

# RS line normalized to 20 days ago
rs = (stock / stock.iloc[-20]) / (sector / sector.iloc[-20])
trend = "rising ✓" if rs.iloc[-1] > rs.iloc[-5] else "falling ✗"

print(f"RS Ratio: {rs.iloc[-1]:.3f} ({trend})")
# RS > 1.0 AND rising = stock outperforming sector — strong setup`,
        },
      },
      {
        id: "ca5",
        text: "Confirm RSI: no bearish divergence, not overbought (>75)",
        code: {
          python: `import pandas_ta as ta

def bearish_divergence(df, lookback=14):
    """Price makes new high, RSI fails to make new high."""
    df["RSI"] = ta.rsi(df["Close"], 14)
    recent = df.tail(lookback)

    price_hh = recent["High"].iloc[-1] >= recent["High"].max() * 0.995
    rsi_lh   = recent["RSI"].iloc[-1]  <  recent["RSI"].max()  * 0.95
    return price_hh and rsi_lh

if bearish_divergence(df):
    print("⚠ Bearish divergence — SKIP this trade")
elif df["RSI"].iloc[-1] > 75:
    print("⚠ Overbought (RSI > 75) — wait for cooldown")
else:
    print(f"✓ RSI healthy: {df['RSI'].iloc[-1]:.1f}")`,
        },
      },
      {
        id: "ca6",
        text: "Confirm stock not in F&O ban list (NSE updates daily)",
        code: {
          python: `import requests

def in_fno_ban(symbol):
    url = "https://www.nseindia.com/api/fiidiiTradeReact"   # NSE API
    headers = {"User-Agent": "Mozilla/5.0"}
    # Use nsepython for reliable banned list fetch:
    from nsepython import nse_get_fno_lot_sizes, fnolist
    banned = fnolist()    # current F&O ban securities
    return symbol.upper() in banned

print("Banned:", in_fno_ban("RELIANCE"))`,
        },
      },
      {
        id: "ca7",
        text: "Check upcoming earnings / dividend / ex-date within 5 days",
        code: {
          python: `import yfinance as yf
from datetime import datetime, timedelta

def upcoming_events(symbol, days=5):
    tk = yf.Ticker(f"{symbol}.NS")
    cal = tk.calendar
    next_date = cal.get("Earnings Date")
    if next_date:
        dt = next_date[0] if isinstance(next_date, list) else next_date
        days_away = (dt - datetime.now().date()).days
        if 0 <= days_away <= days:
            return f"⚠ Earnings in {days_away} days — SKIP"
    return "✓ No events in window"

print(upcoming_events("RELIANCE"))`,
        },
      },
    ],
  },
  {
    id: "position-entry",
    label: "Position Sizing & Entry",
    time: "At Signal",
    color: "#3B82F6",
    icon: "🎯",
    items: [
      {
        id: "pe1",
        text: "Calculate risk per share and full position size",
        code: {
          python: `def position_size(capital, risk_pct, entry, stop_loss):
    risk_amount    = capital * (risk_pct / 100)
    risk_per_share = entry - stop_loss
    quantity       = int(risk_amount / risk_per_share)
    deployed       = quantity * entry
    return {
        "quantity":       quantity,
        "risk_per_share": round(risk_per_share, 2),
        "risk_amount":    round(risk_amount, 2),
        "deployed":       round(deployed, 2),
        "deployed_pct":   round(deployed / capital * 100, 2),
    }

# Example: ₹5L capital, 1% risk, entry 500, SL 485
pos = position_size(capital=500000, risk_pct=1, entry=500, stop_loss=485)
print(pos)
# {'quantity': 333, 'risk_per_share': 15.0, 'risk_amount': 5000.0,
#  'deployed': 166500.0, 'deployed_pct': 33.3}`,
          formula: `Risk per Share = Entry Price − Stop Loss
Risk Amount    = Total Capital × Risk %
Quantity       = Risk Amount ÷ Risk per Share
Capital Deployed = Quantity × Entry

Example (₹5,00,000 capital, 1% risk):
  Entry: ₹500, SL: ₹485 → Risk/share: ₹15
  Risk Amount: ₹5,000
  Quantity: 5000/15 = 333 shares
  Deployed: 333 × 500 = ₹1,66,500 (33% of capital)

⚠ If deployed exceeds available capital, REDUCE qty.
   Never widen SL to "fit" the position.`,
        },
      },
      {
        id: "pe2",
        text: "Verify Risk:Reward ≥ 2:1 before entering",
        code: {
          python: `def rr_check(entry, sl, target, min_rr=2.0):
    risk   = entry - sl
    reward = target - entry
    rr = reward / risk
    return rr, rr >= min_rr

rr, ok = rr_check(entry=500, sl=485, target=540)
print(f"R:R = {rr:.2f}  →  {'✓ ENTER' if ok else '✗ SKIP'}")
# R:R = 2.67  →  ✓ ENTER`,
          formula: `R:R = (Target − Entry) ÷ (Entry − Stop Loss)

Why R:R ≥ 2?
  With 40% win rate and 1:2 R:R, you still profit:
  (0.4 × 2) − (0.6 × 1) = +0.2 per ₹1 risked

If R:R < 2 → SKIP. Don't tweak SL to "fit".`,
        },
      },
      {
        id: "pe3",
        text: "Check chase % — entry within 3% of breakout level",
        code: {
          python: `def chase_check(breakout_level, current_price, max_chase=3):
    chase_pct = (current_price - breakout_level) / breakout_level * 100
    return chase_pct, chase_pct <= max_chase

chase, ok = chase_check(breakout_level=500, current_price=518)
print(f"Chase: {chase:.2f}%  →  {'✓ OK' if ok else '✗ TOO LATE'}")
# Chase: 3.60%  →  ✗ TOO LATE — wait for pullback`,
        },
      },
      {
        id: "pe4",
        text: "Confirm volume is 1.5-2x the 20-day average",
        code: {
          python: `def volume_spike(df, mult=1.5):
    avg = df["Volume"].rolling(20).mean().iloc[-1]
    today = df["Volume"].iloc[-1]
    ratio = today / avg
    return ratio, ratio >= mult

ratio, ok = volume_spike(df)
print(f"Volume ratio: {ratio:.2f}x  →  {'✓' if ok else '✗ Weak volume'}")`,
        },
      },
      { id: "pe5", text: "Nifty not in strong downtrend" },
      { id: "pe6", text: "Not entering during F&O expiry day (Thursday)" },
      { id: "pe7", text: "Max 3-5 open positions total — within limit?" },
    ],
  },
  {
    id: "risk-management",
    label: "Risk Management",
    time: "Immediately After Entry",
    color: "#EF4444",
    icon: "🛡️",
    items: [
      {
        id: "rm1",
        text: "Calculate stop-loss using structure OR ATR (use wider)",
        code: {
          python: `import pandas_ta as ta

def stop_loss(df, entry, base_low, atr_mult=2):
    structure_sl = base_low * 0.995          # 0.5% below base
    atr = ta.atr(df["High"], df["Low"], df["Close"], 14).iloc[-1]
    atr_sl = entry - (atr_mult * atr)
    final = min(structure_sl, atr_sl)        # wider stop = safer
    sl_pct = (entry - final) / entry * 100
    return {"structure": round(structure_sl, 2),
            "atr": round(atr_sl, 2),
            "final": round(final, 2),
            "sl_pct": round(sl_pct, 2)}

print(stop_loss(df, entry=500, base_low=482, atr_mult=2))
# {'structure': 479.59, 'atr': 484.0, 'final': 479.59, 'sl_pct': 4.08}`,
          formula: `Two methods — use the WIDER (safer):

1) STRUCTURE-BASED: SL = Base Low × 0.995
2) VOLATILITY-BASED: SL = Entry − (2 × ATR14)

Never set SL by "how much I'm willing to lose".
SL must respect market structure & noise.`,
        },
      },
      { id: "rm2", text: "Place GTT order on Zerodha/Groww (not mental stop)" },
      {
        id: "rm3",
        text: "Set Target 1 (1.5x risk) and Target 2 (base height)",
        code: {
          python: `def targets(entry, sl, base_high, base_low):
    risk = entry - sl
    t1 = entry + (1.5 * risk)                # book 50% here
    base_height = base_high - base_low
    t2 = entry + base_height                  # measured move
    return {"target_1": round(t1, 2),
            "target_2": round(t2, 2),
            "risk": round(risk, 2),
            "rr_t1": 1.5,
            "rr_t2": round(base_height / risk, 2)}

print(targets(entry=500, sl=485, base_high=500, base_low=460))
# {'target_1': 522.5, 'target_2': 540, 'risk': 15, 'rr_t1': 1.5, 'rr_t2': 2.67}`,
          formula: `Target 1 = Entry + (1.5 × Risk)   → book 50%, move SL to BE
Target 2 = Entry + (Base High − Base Low)   → measured move

At T1:
  • Book 50% of position
  • Move SL on remaining 50% to breakeven
  • Worst case from here = 0`,
        },
      },
      {
        id: "rm4",
        text: "Log trade to journal (entry, SL, targets, thesis)",
        code: {
          python: `import pandas as pd
from datetime import datetime

def log_trade(symbol, entry, sl, t1, t2, qty, thesis):
    trade = pd.DataFrame([{
        "date":   datetime.now(),
        "symbol": symbol,
        "entry":  entry, "sl": sl,
        "t1":     t1,    "t2": t2,
        "qty":    qty,
        "risk":   (entry - sl) * qty,
        "thesis": thesis,
        "status": "OPEN",
    }])
    trade.to_csv("trades.csv", mode="a", header=False, index=False)
    print(f"✓ Logged: {symbol} @ ₹{entry}")

log_trade("RELIANCE", 500, 485, 522.5, 540, 333, "Flat base breakout + volume")`,
        },
      },
    ],
  },
  {
    id: "trade-management",
    label: "Trade Management",
    time: "While Holding",
    color: "#8B5CF6",
    icon: "⚙️",
    items: [
      {
        id: "tm1",
        text: "Daily morning brief — scan all open positions",
        code: {
          python: `import yfinance as yf
import pandas_ta as ta
import pandas as pd

def morning_brief(watchlist):
    print(f"=== MORNING BRIEF — {pd.Timestamp.now():%d %b %Y} ===\\n")
    for sym in watchlist:
        df = yf.download(f"{sym}.NS", period="3mo", progress=False)
        if df.empty: continue
        last = df.iloc[-1]
        rsi  = ta.rsi(df["Close"], 14).iloc[-1]
        chg  = (last["Close"] / df["Close"].iloc[-2] - 1) * 100
        vol_x = last["Volume"] / df["Volume"].rolling(20).mean().iloc[-1]
        print(f"{sym:10}  ₹{last['Close']:>8.2f}  ({chg:+5.2f}%)  "
              f"RSI:{rsi:>5.1f}  Vol:{vol_x:.1f}x")

morning_brief(["RELIANCE", "TCS", "INFY", "HDFCBANK", "TATAMOTORS"])`,
        },
      },
      { id: "tm2", text: "If price closes back inside base → exit, no overrides" },
      { id: "tm3", text: "Move stop to breakeven after Target 1 is hit" },
      {
        id: "tm4",
        text: "Trail stop using Chandelier Exit (volatility-aware)",
        code: {
          python: `import pandas_ta as ta

def chandelier_exit(df, period=22, mult=3):
    """Trail stop = (Highest High in period) − (mult × ATR)"""
    hh  = df["High"].rolling(period).max()
    atr = ta.atr(df["High"], df["Low"], df["Close"], period)
    return hh - (mult * atr)

df["TrailSL"] = chandelier_exit(df)

# Exit signal: today closes below trail stop
if df["Close"].iloc[-1] < df["TrailSL"].iloc[-2]:
    print(f"🚨 EXIT — close ₹{df['Close'].iloc[-1]:.2f} "
          f"< trail ₹{df['TrailSL'].iloc[-2]:.2f}")
else:
    print(f"✓ Hold — trail at ₹{df['TrailSL'].iloc[-1]:.2f}")`,
          formula: `Chandelier Exit = Highest High(22) − (3 × ATR(22))

Rules:
  • Update SL at end of day, not intraday
  • Only RAISE stops, never lower
  • Exit fully on daily CLOSE below trail
  • Smoother than EMA trailing (fewer whipsaws)`,
        },
      },
      { id: "tm5", text: "Don't average down on a losing trade — ever" },
      { id: "tm6", text: "Check if broader market thesis still holds" },
      { id: "tm7", text: "Avoid holding through scheduled results announcements" },
    ],
  },
  {
    id: "exit-review",
    label: "Exit & Review",
    time: "After Closing",
    color: "#EC4899",
    icon: "📝",
    items: [
      {
        id: "er1",
        text: "Mark trade closed in journal with actual exit + P&L",
        code: {
          python: `import pandas as pd
from datetime import datetime

def close_trade(symbol, exit_price, exit_reason):
    df = pd.read_csv("trades.csv")
    idx = df[(df["symbol"]==symbol) & (df["status"]=="OPEN")].index[-1]

    entry, qty = df.at[idx, "entry"], df.at[idx, "qty"]
    pnl = (exit_price - entry) * qty
    df.at[idx, "exit"]   = exit_price
    df.at[idx, "exit_dt"] = datetime.now()
    df.at[idx, "pnl"]    = pnl
    df.at[idx, "reason"] = exit_reason
    df.at[idx, "status"] = "CLOSED"
    df.to_csv("trades.csv", index=False)
    print(f"✓ {symbol}: ₹{pnl:+,.0f} ({(pnl/(entry*qty))*100:+.2f}%)")

close_trade("RELIANCE", 538, "T2 hit")`,
        },
      },
      { id: "er2", text: "Was the setup valid? Did I follow the checklist?" },
      { id: "er3", text: "What did I do well? What could be improved?" },
      {
        id: "er4",
        text: "Compute system metrics over last 20+ trades",
        code: {
          python: `import pandas as pd

def edge_metrics(csv_path="trades.csv"):
    df = pd.read_csv(csv_path)
    df = df[df["status"] == "CLOSED"].tail(50)    # last 50 closed

    wins   = df[df["pnl"] > 0]
    losses = df[df["pnl"] < 0]
    n      = len(df)

    win_rate      = len(wins) / n * 100
    avg_win       = wins["pnl"].mean()
    avg_loss      = abs(losses["pnl"].mean())
    payoff        = avg_win / avg_loss
    profit_factor = wins["pnl"].sum() / abs(losses["pnl"].sum())
    expectancy    = (win_rate/100 * avg_win) - ((1 - win_rate/100) * avg_loss)

    print(f"Trades:          {n}")
    print(f"Win Rate:        {win_rate:.1f}%")
    print(f"Avg Win:         ₹{avg_win:,.0f}")
    print(f"Avg Loss:        ₹{avg_loss:,.0f}")
    print(f"Payoff Ratio:    {payoff:.2f}")
    print(f"Profit Factor:   {profit_factor:.2f}    (>1.5 healthy, >2 excellent)")
    print(f"Expectancy:      ₹{expectancy:,.0f}/trade")

edge_metrics()`,
          formula: `WIN RATE       = Wins / Total Trades × 100
PAYOFF         = Avg Win / Avg Loss
PROFIT FACTOR  = Gross Profit / Gross Loss
                 >1.5 healthy  |  >2.0 excellent  |  <1.0 losing
EXPECTANCY     = (Win% × Avg Win) − (Loss% × Avg Loss)

Example: 45% wins, AvgWin ₹4500, AvgLoss ₹2000
  Payoff     = 2.25
  Expectancy = (0.45 × 4500) − (0.55 × 2000) = +₹925/trade  →  Profitable`,
        },
      },
      { id: "er5", text: "Review weekly P&L — am I overtrading?" },
      { id: "er6", text: "Update watchlist for next session" },
    ],
  },
];

// ---------- Code block ----------
function CodeBlock({ code, color }) {
  const tabs = [];
  if (code.python)   tabs.push({ key: "python",   label: "Python" });
  if (code.chartink) tabs.push({ key: "chartink", label: "Chartink" });
  if (code.formula)  tabs.push({ key: "formula",  label: "Formula" });

  const [active, setActive] = useState(tabs[0].key);
  const [copied, setCopied] = useState(false);

  const handleCopy = (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(code[active]);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const renderCode = (text) =>
    text.split("\n").map((line, i) => {
      const isComment = line.trim().startsWith("#") || line.trim().startsWith("//");
      return (
        <div key={i} style={{
          color: isComment ? "#64748B" : "#CBD5E1",
          fontStyle: isComment ? "italic" : "normal",
        }}>{line || "\u00A0"}</div>
      );
    });

  return (
    <div onClick={(e) => e.stopPropagation()} style={{
      marginTop: "12px", background: "#08080F",
      border: "1px solid #1E2035", borderRadius: "6px", overflow: "hidden",
    }}>
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        borderBottom: "1px solid #1E2035", background: "#0D0D18", padding: "0 8px",
      }}>
        <div style={{ display: "flex" }}>
          {tabs.map((tab) => (
            <button key={tab.key} onClick={(e) => { e.stopPropagation(); setActive(tab.key); }}
              style={{
                background: "transparent", border: "none",
                color: active === tab.key ? color : "#64748B",
                padding: "8px 12px", fontSize: "11px", letterSpacing: "1px",
                cursor: "pointer",
                borderBottom: active === tab.key ? `2px solid ${color}` : "2px solid transparent",
                fontFamily: "inherit", transition: "all 0.2s",
              }}>
              {tab.label.toUpperCase()}
            </button>
          ))}
        </div>
        <button onClick={handleCopy} style={{
          background: copied ? color + "33" : "transparent",
          border: `1px solid ${copied ? color + "66" : "#2D2D45"}`,
          color: copied ? color : "#94A3B8",
          padding: "4px 10px", fontSize: "10px", letterSpacing: "1px",
          cursor: "pointer", borderRadius: "4px", fontFamily: "inherit",
          transition: "all 0.2s",
        }}>
          {copied ? "✓ COPIED" : "COPY"}
        </button>
      </div>
      <pre style={{
        margin: 0, padding: "14px 16px", fontSize: "12px", lineHeight: "1.6",
        fontFamily: "'DM Mono', 'Courier New', monospace",
        overflowX: "auto", whiteSpace: "pre",
      }}>
        {renderCode(code[active])}
      </pre>
    </div>
  );
}

// ---------- Main ----------
export default function SwingTradingChecklist() {
  const [checked, setChecked] = useState({});
  const [expanded, setExpanded] = useState({});
  const [activeSection, setActiveSection] = useState("watchlist");

  const toggle = (id) => setChecked((p) => ({ ...p, [id]: !p[id] }));
  const toggleExpand = (id, e) => {
    e.stopPropagation();
    setExpanded((p) => ({ ...p, [id]: !p[id] }));
  };

  const totalItems = sections.reduce((a, s) => a + s.items.length, 0);
  const checkedCount = Object.values(checked).filter(Boolean).length;
  const progress = Math.round((checkedCount / totalItems) * 100);
  const sectionProgress = (s) => Math.round(s.items.filter((i) => checked[i.id]).length / s.items.length * 100);
  const resetAll = () => { setChecked({}); setExpanded({}); };
  const active = sections.find((s) => s.id === activeSection);

  return (
    <div style={{
      minHeight: "100vh", background: "#0A0A0F", color: "#E2E8F0",
      fontFamily: "'DM Mono', 'Courier New', monospace",
      display: "flex", flexDirection: "column",
    }}>
      <div style={{
        background: "linear-gradient(135deg, #0F0F1A 0%, #1A1A2E 100%)",
        borderBottom: "1px solid #1E2035", padding: "24px 32px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        flexWrap: "wrap", gap: "16px",
      }}>
        <div>
          <div style={{ fontSize: "11px", letterSpacing: "3px", color: "#6366F1", marginBottom: "6px", textTransform: "uppercase" }}>
            Indian Markets • NSE / BSE • Python Stack
          </div>
          <h1 style={{ margin: 0, fontSize: "22px", fontWeight: "700", color: "#F1F5F9", letterSpacing: "-0.5px" }}>
            Swing Trading Checklist
          </h1>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "20px" }}>
          <div style={{ position: "relative", width: "64px", height: "64px" }}>
            <svg viewBox="0 0 64 64" style={{ transform: "rotate(-90deg)", width: "64px", height: "64px" }}>
              <circle cx="32" cy="32" r="26" fill="none" stroke="#1E2035" strokeWidth="5" />
              <circle cx="32" cy="32" r="26" fill="none" stroke="#6366F1" strokeWidth="5"
                strokeDasharray={`${2 * Math.PI * 26}`}
                strokeDashoffset={`${2 * Math.PI * 26 * (1 - progress / 100)}`}
                strokeLinecap="round" style={{ transition: "stroke-dashoffset 0.4s ease" }} />
            </svg>
            <div style={{
              position: "absolute", inset: 0, display: "flex",
              alignItems: "center", justifyContent: "center",
              fontSize: "12px", fontWeight: "700", color: "#A5B4FC",
            }}>{progress}%</div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: "22px", fontWeight: "700", color: "#F1F5F9" }}>
              {checkedCount}<span style={{ color: "#475569", fontSize: "16px" }}>/{totalItems}</span>
            </div>
            <div style={{ fontSize: "11px", color: "#64748B", letterSpacing: "1px" }}>COMPLETED</div>
          </div>
          <button onClick={resetAll} style={{
            background: "transparent", border: "1px solid #2D2D45",
            color: "#94A3B8", padding: "8px 14px", borderRadius: "6px",
            cursor: "pointer", fontSize: "11px", letterSpacing: "1px",
            fontFamily: "inherit",
          }}>RESET</button>
        </div>
      </div>

      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        <div style={{
          width: "230px", minWidth: "230px", background: "#0D0D18",
          borderRight: "1px solid #1E2035", padding: "16px 0", overflowY: "auto",
        }}>
          {sections.map((section, idx) => {
            const pct = sectionProgress(section);
            const isActive = activeSection === section.id;
            const hasCode = section.items.some((i) => i.code);
            return (
              <button key={section.id} onClick={() => setActiveSection(section.id)}
                style={{
                  width: "100%",
                  background: isActive ? "#151525" : "transparent",
                  border: "none",
                  borderLeft: isActive ? `3px solid ${section.color}` : "3px solid transparent",
                  cursor: "pointer", padding: "12px 16px", textAlign: "left",
                  display: "flex", alignItems: "center", gap: "10px",
                  fontFamily: "inherit",
                }}>
                <span style={{
                  fontSize: "10px",
                  color: isActive ? section.color : "#334155",
                  fontWeight: "600", minWidth: "14px",
                }}>{String(idx + 1).padStart(2, "0")}</span>
                <span style={{ fontSize: "16px" }}>{section.icon}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontSize: "12px",
                    fontWeight: isActive ? "600" : "400",
                    color: isActive ? "#F1F5F9" : "#64748B",
                    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                    display: "flex", alignItems: "center", gap: "6px",
                  }}>
                    {section.label}
                    {hasCode && <span style={{ fontSize: "9px", color: section.color }}>{"<>"}</span>}
                  </div>
                  <div style={{
                    marginTop: "4px", height: "3px",
                    background: "#1E2035", borderRadius: "2px", overflow: "hidden",
                  }}>
                    <div style={{
                      height: "100%", width: `${pct}%`,
                      background: section.color, borderRadius: "2px",
                      transition: "width 0.3s ease",
                    }} />
                  </div>
                </div>
                <span style={{ fontSize: "10px", color: "#475569", minWidth: "24px", textAlign: "right" }}>
                  {pct}%
                </span>
              </button>
            );
          })}
        </div>

        <div style={{ flex: 1, overflowY: "auto", padding: "28px 32px" }}>
          {active && (
            <div>
              <div style={{
                display: "flex", alignItems: "flex-start",
                justifyContent: "space-between", marginBottom: "24px",
                flexWrap: "wrap", gap: "8px",
              }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "6px" }}>
                    <span style={{ fontSize: "24px" }}>{active.icon}</span>
                    <h2 style={{ margin: 0, fontSize: "20px", fontWeight: "700", color: "#F1F5F9" }}>
                      {active.label}
                    </h2>
                  </div>
                  <div style={{
                    display: "inline-block", background: "#151525",
                    border: `1px solid ${active.color}33`, color: active.color,
                    fontSize: "11px", letterSpacing: "2px",
                    padding: "3px 10px", borderRadius: "4px",
                  }}>{active.time}</div>
                </div>
                <div style={{ fontSize: "13px", color: "#475569" }}>
                  {active.items.filter((i) => checked[i.id]).length} / {active.items.length} done
                </div>
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                {active.items.map((item, idx) => {
                  const isDone = !!checked[item.id];
                  const isExpanded = !!expanded[item.id];
                  const hasCode = !!item.code;
                  return (
                    <div key={item.id} style={{
                      background: isDone ? "#0F1F18" : "#111120",
                      border: `1px solid ${isDone ? active.color + "44" : "#1E2035"}`,
                      borderRadius: "8px", transition: "all 0.2s",
                    }}>
                      <div onClick={() => toggle(item.id)} style={{
                        display: "flex", alignItems: "center", gap: "14px",
                        padding: "14px 18px", cursor: "pointer", userSelect: "none",
                      }}>
                        <div style={{
                          width: "20px", height: "20px", minWidth: "20px",
                          border: `2px solid ${isDone ? active.color : "#2D2D55"}`,
                          borderRadius: "4px",
                          background: isDone ? active.color : "transparent",
                          display: "flex", alignItems: "center", justifyContent: "center",
                          fontSize: "12px",
                        }}>{isDone && <span style={{ color: "#000", fontWeight: "700" }}>✓</span>}</div>
                        <span style={{
                          fontSize: "11px",
                          color: isDone ? active.color + "88" : "#2D2D55",
                          minWidth: "18px", fontWeight: "600",
                        }}>{String(idx + 1).padStart(2, "0")}</span>
                        <span style={{
                          fontSize: "13px",
                          color: isDone ? "#64748B" : "#CBD5E1",
                          textDecoration: isDone ? "line-through" : "none",
                          flex: 1, lineHeight: "1.5",
                        }}>{item.text}</span>
                        {hasCode && (
                          <button onClick={(e) => toggleExpand(item.id, e)} style={{
                            background: isExpanded ? active.color + "22" : "transparent",
                            border: `1px solid ${isExpanded ? active.color + "66" : "#2D2D45"}`,
                            color: isExpanded ? active.color : "#94A3B8",
                            padding: "4px 10px", fontSize: "10px", letterSpacing: "1px",
                            cursor: "pointer", borderRadius: "4px",
                            fontFamily: "inherit", whiteSpace: "nowrap",
                          }}>{isExpanded ? "▲ HIDE" : "<> CODE"}</button>
                        )}
                      </div>
                      {hasCode && isExpanded && (
                        <div style={{ padding: "0 18px 16px 18px" }}>
                          <CodeBlock code={item.code} color={active.color} />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              <div style={{
                display: "flex", justifyContent: "space-between",
                marginTop: "32px", paddingTop: "20px",
                borderTop: "1px solid #1E2035",
              }}>
                {(() => {
                  const idx = sections.findIndex((s) => s.id === activeSection);
                  const prev = sections[idx - 1];
                  const next = sections[idx + 1];
                  return (
                    <>
                      {prev ? (
                        <button onClick={() => setActiveSection(prev.id)} style={{
                          background: "transparent", border: "1px solid #2D2D45",
                          color: "#94A3B8", padding: "8px 16px", borderRadius: "6px",
                          cursor: "pointer", fontSize: "12px", letterSpacing: "1px",
                          fontFamily: "inherit",
                        }}>← {prev.label}</button>
                      ) : <div />}
                      {next && (
                        <button onClick={() => setActiveSection(next.id)} style={{
                          background: active.color + "22",
                          border: `1px solid ${active.color}55`,
                          color: active.color, padding: "8px 16px", borderRadius: "6px",
                          cursor: "pointer", fontSize: "12px", letterSpacing: "1px",
                          fontFamily: "inherit",
                        }}>{next.label} →</button>
                      )}
                    </>
                  );
                })()}
              </div>
            </div>
          )}
        </div>
      </div>

      <div style={{
        borderTop: "1px solid #1E2035", padding: "10px 32px",
        display: "flex", justifyContent: "space-between", alignItems: "center",
        background: "#0D0D18", fontSize: "11px", color: "#334155",
        letterSpacing: "1px",
      }}>
        <span>PYTHON • CHARTINK • RISK MATH</span>
        <span>NO TRADINGVIEW API NEEDED</span>
      </div>
    </div>
  );
}
