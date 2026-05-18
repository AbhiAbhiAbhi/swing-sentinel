import { useState } from "react";

// Code samples: pine = Pine Script (TradingView), chartink = Chartink screener, formula = Math
const sections = [
  {
    id: "watchlist",
    label: "Watchlist Building",
    time: "Previous Evening (After 3:30 PM)",
    color: "#6366F1",
    icon: "📋",
    items: [
      {
        id: "wl1",
        text: "Run Chartink screener for breakout candidates",
        code: {
          chartink: `( {cash} (
  latest close > 1 day ago high * 1.005
  and daily volume > 1.5 * daily sma( volume, 20 )
  and latest close > latest sma( close, 50 )
  and market cap > 500
) )`,
          pine: `//@version=5
indicator("Breakout Scanner", overlay=true)
hh20   = ta.highest(high, 20)[1]   // 20-bar high (excluding today)
volAvg = ta.sma(volume, 20)
breakout = close > hh20 * 1.005 and volume > 1.5 * volAvg
plotshape(breakout, style=shape.triangleup,
  location=location.belowbar, color=color.lime, size=size.small)
alertcondition(breakout, "Breakout", "Stock breaking out")`,
        },
      },
      {
        id: "wl2",
        text: "Filter: Price > ₹100, Avg Volume > 5 lakh shares/day",
        code: {
          chartink: `( {cash} (
  latest close > 100
  and daily sma( volume, 20 ) > 500000
) )`,
          formula: `Two minimum-quality filters before adding to watchlist:

1) PRICE FLOOR:  Close > ₹100
   Why: Sub-₹100 stocks have wider spreads, higher
        manipulation risk, and noisy candle behavior.

2) LIQUIDITY FLOOR:  20-day avg volume > 5,00,000 shares
   Why: Ensures you can enter/exit a 1–2% position
        without moving price.

Position-size sanity check:
   Your quantity ≤ 5% of 20-day avg daily volume.
   Example: stock trades 8,00,000 shares/day → max 40,000.

If a stock fails either filter, drop it from the
list immediately. No exceptions for "great chart".`,
        },
      },
      {
        id: "wl3",
        text: "Identify stocks near 52-week highs or key resistance",
        code: {
          chartink: `( {cash} (
  latest close > latest high( 52 weeks ) * 0.97
  and latest close <= latest high( 52 weeks )
) )`,
          pine: `//@version=5
indicator("Near 52W High", overlay=true)
high52w = ta.highest(high, 252)   // ~252 trading days in a year
distFromHigh = (high52w - close) / high52w * 100
nearHigh = distFromHigh <= 3      // within 3% of 52W high
bgcolor(nearHigh ? color.new(color.green, 85) : na)`,
        },
      },
      {
        id: "wl4",
        text: "Look for 9 EMA crossing above 21 EMA on daily chart",
        code: {
          pine: `//@version=5
indicator("EMA 9/21 Crossover", overlay=true)
ema9  = ta.ema(close, 9)
ema21 = ta.ema(close, 21)

bullCross = ta.crossover(ema9, ema21)
bearCross = ta.crossunder(ema9, ema21)

plot(ema9,  color=color.aqua,   linewidth=2)
plot(ema21, color=color.orange, linewidth=2)
plotshape(bullCross, style=shape.triangleup,
  location=location.belowbar, color=color.green, size=size.small)
plotshape(bearCross, style=shape.triangledown,
  location=location.abovebar, color=color.red,   size=size.small)`,
          chartink: `( {cash} (
  latest ema( close, 9 ) > latest ema( close, 21 )
  and 1 day ago ema( close, 9 ) <= 1 day ago ema( close, 21 )
) )`,
        },
      },
      {
        id: "wl5",
        text: "Note stocks with RSI 40–55 in uptrends (continuation setups)",
        code: {
          chartink: `( {cash} (
  latest rsi( 14 ) >= 40
  and latest rsi( 14 ) <= 55
  and latest close > latest ema( close, 50 )
  and latest ema( close, 50 ) > 1 month ago ema( close, 50 )
) )`,
          pine: `//@version=5
indicator("RSI Pullback Zone", overlay=true)
rsi   = ta.rsi(close, 14)
ema50 = ta.ema(close, 50)

inUptrend     = close > ema50 and ema50 > ema50[20]
pullbackZone  = rsi >= 40 and rsi <= 55
goodSetup     = inUptrend and pullbackZone

bgcolor(goodSetup ? color.new(color.blue, 85) : na)`,
        },
      },
      { id: "wl6", text: "Mark exact breakout level and stop-loss zone on each chart" },
      { id: "wl7", text: "Set price alerts on TradingView / broker app" },
      { id: "wl8", text: "Shortlist 3–5 candidates max — don't overload watchlist" },
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
          formula: `GAP % = ( GIFT Nifty − Previous Nifty Close ) / Previous Nifty Close × 100

Interpretation:
  > +0.5%        →  Strong gap-up expected
  −0.2 to +0.2%  →  Flat open expected
  < −0.5%        →  Strong gap-down expected

Rule of thumb: ~70% of large gaps (>1%) partially fill
within the first 30–45 minutes.`,
        },
      },
      {
        id: "pm2",
        text: "Check US market close (Dow, Nasdaq, S&P)",
        code: {
          formula: `US close → next-day Indian market bias:

  S&P 500 change > +1%      →  Bullish open expected
  S&P −0.5% to +0.5%        →  Neutral / flat open
  S&P < −1%                 →  Bearish gap-down likely

Sector-specific correlation (5-yr historical):
  Nasdaq    →  Nifty IT       ≈  0.75
  Dow       →  Nifty 50       ≈  0.60
  S&P 500   →  Nifty 50       ≈  0.65
  Russell   →  Nifty SmallCap ≈  0.55

Transmission rule:
  US move of X%  →  Nifty typically gaps 0.5X to 0.7X
  Stronger transmission on Mondays (weekend catch-up)
  and the morning after Fed / CPI / NFP releases.`,
        },
      },
      {
        id: "pm3",
        text: "Check USD/INR — weakness signals FII outflows",
        code: {
          formula: `USD/INR direction → FII flow & sector bias:

  USDINR ↑ > 0.3%   →  INR weak
                        • FII selling pressure (bearish Nifty)
                        • IT / Pharma exporters benefit (intraday)

  USDINR ±0.1%      →  Neutral

  USDINR ↓ > 0.3%   →  INR strong
                        • FII inflows likely (bullish)
                        • IT / Pharma headwind

Historical sensitivity:
  USDINR ↑ 1%   ≈  Nifty 50  ↓ 0.4% (next 5 sessions)
  USDINR ↑ 1%   ≈  Nifty IT  ↑ 0.6% (short-term)

Sustained-level rule:
  USDINR > 84.50 for 5+ sessions → persistent FII
  selling regime → reduce position sizing by 30–50%.`,
        },
      },
      {
        id: "pm4",
        text: "Review FII/DII data from previous session",
        code: {
          formula: `FII / DII net flow interpretation (₹ crore):

  FII NET INFLOW (cash market):
    > +3000 cr      →  Strong bullish (large-cap leadership)
    +1000 to +3000  →  Mild bullish
    −1000 to +1000  →  Neutral
    −1000 to −3000  →  Mild bearish
    < −3000 cr      →  Crisis selling — avoid fresh longs

  DII OFFSET RULE:
    If FII < 0  AND  DII ≥ |FII|  →  Cushion present, mid-caps OK
    If FII < 0  AND  DII < |FII|  →  No buffer, expect downside

  3-day cumulative is more reliable than single-day:
    3-day FII > +5000 cr   →  Trend buying
    3-day FII < −5000 cr   →  Trend selling

Source: NSE / BSE provisional flows published ~6 PM daily.`,
        },
      },
      { id: "pm5", text: "Note major news: RBI, budget, geopolitical events" },
      {
        id: "pm6",
        text: "Determine market bias: bullish / bearish / neutral",
        code: {
          formula: `COMPOSITE MARKET BIAS SCORE
(sum +1 / 0 / −1 from each input — range: −5 to +5)

  INPUT             BULLISH (+1)          BEARISH (−1)
  ──────────────────────────────────────────────────────
  GIFT Nifty        > +0.3%               < −0.3%
  US markets        S&P > +0.5%           S&P < −0.5%
  USD/INR           Falling > 0.2%        Rising > 0.2%
  FII flow (prev)   > +1000 cr            < −1000 cr
  Nifty 21 EMA      Close > 21 EMA        Close < 21 EMA

Final interpretation:
  Score ≥ +3   →  STRONG BULLISH  — full size on longs
  Score +1/+2  →  MILD BULLISH    — full size, selective
  Score 0      →  NEUTRAL         — half size, A+ setups only
  Score −1/−2  →  MILD BEARISH    — quarter size or skip
  Score ≤ −3   →  STRONG BEARISH  — no fresh longs

Hard gate: do NOT enter new positions if score ≤ −2.
Existing positions: tighten stops, book partials.`,
        },
      },
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
        text: "Check weekly chart first — confirm key S/R zones",
        code: {
          pine: `//@version=5
indicator("Weekly Pivot S/R", overlay=true)

// Previous-week pivot levels
weekHigh  = request.security(syminfo.tickerid, "W", high[1])
weekLow   = request.security(syminfo.tickerid, "W", low[1])
weekClose = request.security(syminfo.tickerid, "W", close[1])

pivot = (weekHigh + weekLow + weekClose) / 3
r1 = 2 * pivot - weekLow
s1 = 2 * pivot - weekHigh
r2 = pivot + (weekHigh - weekLow)
s2 = pivot - (weekHigh - weekLow)

plot(pivot, "Pivot", color=color.yellow,  linewidth=2)
plot(r1,    "R1",    color=color.red,     linewidth=1)
plot(r2,    "R2",    color=color.red,     linewidth=1)
plot(s1,    "S1",    color=color.green,   linewidth=1)
plot(s2,    "S2",    color=color.green,   linewidth=1)`,
          chartink: `( {cash} (
  // Stocks near a 12-week swing high (resistance test)
  latest close >= weekly max( high, 12 ) * 0.97
  and latest close <= weekly max( high, 12 )
) )`,
        },
      },
      {
        id: "ca2",
        text: "Switch to daily — confirm trend direction",
        code: {
          pine: `//@version=5
indicator("Daily Trend Regime", overlay=true)
ema20  = ta.ema(close, 20)
ema50  = ta.ema(close, 50)
ema200 = ta.ema(close, 200)

// Bullish stack: 20 > 50 > 200, and short-term EMAs sloping up
stackUp   = ema20 > ema50 and ema50 > ema200
sloping   = ema20 > ema20[5] and ema50 > ema50[10]
uptrend   = stackUp and sloping

stackDown = ema20 < ema50 and ema50 < ema200
downtrend = stackDown and ema20 < ema20[5]

plot(ema20,  color=color.aqua,   linewidth=1)
plot(ema50,  color=color.orange, linewidth=2)
plot(ema200, color=color.purple, linewidth=2)
bgcolor(uptrend   ? color.new(color.green, 92) : na)
bgcolor(downtrend ? color.new(color.red,   92) : na)`,
          chartink: `( {cash} (
  latest ema( close, 20 ) > latest ema( close, 50 )
  and latest ema( close, 50 ) > latest ema( close, 200 )
  and latest close > latest ema( close, 20 )
  and latest ema( close, 20 ) > 1 week ago ema( close, 20 )
) )`,
        },
      },
      {
        id: "ca3",
        text: "Identify pattern: flat base / bull flag / ascending triangle",
        code: {
          pine: `//@version=5
indicator("Flat Base Detector", overlay=true)
length = 15

baseHigh = ta.highest(high, length)
baseLow  = ta.lowest(low,  length)
baseRange = (baseHigh - baseLow) / baseLow * 100  // % range

// Flat base = tight range (< 8%) + low volatility
atrPct    = ta.atr(14) / close * 100
isFlatBase = baseRange < 8 and atrPct < 2.5

bgcolor(isFlatBase ? color.new(color.yellow, 85) : na)
plot(baseHigh, color=color.red,   style=plot.style_linebr)
plot(baseLow,  color=color.green, style=plot.style_linebr)`,
        },
      },
      {
        id: "ca4",
        text: "Base should be 5–15 days long (longer = more powerful)",
        code: {
          formula: `Volatility Contraction (Mark Minervini concept):
  ATR(14) on day T  <  ATR(14) on day T-5  <  ATR(14) on day T-10

When ATR shrinks → volatility compressing → energy building →
breakout becomes more likely and explosive.

Base Score:
  Base length × (1 / Base range %) = strength
  Example: 15-day base with 5% range  →  Score = 3.0  (strong)
           7-day base with 10% range  →  Score = 0.7  (weak)`,
        },
      },
      {
        id: "ca5",
        text: "Check sector index — is the sector also trending up?",
        code: {
          pine: `//@version=5
indicator("Relative Strength vs Sector", overlay=false)
sectorSym = input.symbol("NSE:CNXIT", "Sector Index")
sector = request.security(sectorSym, "D", close)

// RS line: stock / sector, normalized
rs = (close / close[20]) / (sector / sector[20])
rsRising = rs > rs[5]

plot(rs, color=rsRising ? color.green : color.red, linewidth=2)
hline(1.0, "Neutral", color=color.gray)

// Trade only when RS > 1 AND rising`,
        },
      },
      {
        id: "ca6",
        text: "Confirm RSI: no bearish divergence, not overbought (>75)",
        code: {
          pine: `//@version=5
indicator("RSI Bearish Divergence", overlay=false)
rsi = ta.rsi(close, 14)

// Bearish divergence: price makes HH, RSI makes LH
priceHH = high > ta.highest(high, 14)[1]
rsiLH   = rsi < ta.highest(rsi,  14)[1]
bearDiv = priceHH and rsiLH

plot(rsi, color=color.purple, linewidth=2)
hline(70, "Overbought", color=color.red)
hline(30, "Oversold",   color=color.green)
plotshape(bearDiv, style=shape.triangledown,
  location=location.top, color=color.red, size=size.small,
  text="DIV")`,
        },
      },
      { id: "ca7", text: "Stock not in F&O ban list (NSE updates daily)" },
      { id: "ca8", text: "No upcoming results / dividend / ex-date within 5 days" },
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
        text: "Calculate risk per share: Entry price − Stop-loss price",
        code: {
          formula: `Risk per Share = Entry Price − Stop Loss Price

Example:
  Entry      = ₹500
  Stop Loss  = ₹485
  Risk/Share = ₹500 − ₹485 = ₹15

This is the rupee amount you stand to lose per share
if the trade hits stop loss.`,
        },
      },
      {
        id: "pe2",
        text: "Position size: Max 1–2% of total capital at risk per trade",
        code: {
          formula: `Risk Amount = Total Capital × Risk %

Example:
  Capital     = ₹5,00,000
  Risk %      = 1%
  Risk Amount = ₹5,00,000 × 0.01 = ₹5,000

This is the MAX rupees you can lose on this single trade.
Never exceed this — even for "high conviction" ideas.`,
        },
      },
      {
        id: "pe3",
        text: "Quantity = (Capital × Risk %) ÷ Risk per share",
        code: {
          formula: `Quantity = Risk Amount ÷ Risk per Share

Example:
  Risk Amount    = ₹5,000
  Risk per Share = ₹15
  Quantity       = 5000 ÷ 15 = 333 shares

Capital Deployed = 333 × ₹500 = ₹1,66,500
  (This is OK — capital deployed can be 30%+ of total,
   but rupee risk stays capped at 1%.)

⚠  If quantity × entry exceeds your available capital,
   you must REDUCE position size — never widen stop loss.`,
        },
      },
      {
        id: "pe4",
        text: "Risk:reward is at least 1:2 before entering",
        code: {
          formula: `Risk:Reward = (Target − Entry) ÷ (Entry − Stop Loss)

Example:
  Entry  = ₹500
  SL     = ₹485   →  Risk = ₹15
  Target = ₹540   →  Reward = ₹40
  R:R    = 40 / 15 = 2.67    ✓ Acceptable (>2)

Why R:R ≥ 2?
  With 40% win rate and 1:2 R:R, you still profit:
    (0.4 × 2) − (0.6 × 1) = 0.8 − 0.6 = +0.2 per ₹1 risked

If R:R < 2, SKIP the trade. Don't tweak the SL to "fit".`,
        },
      },
      {
        id: "pe5",
        text: "Price has broken above resistance / base clearly",
        code: {
          pine: `//@version=5
indicator("Confirmed Breakout", overlay=true)
length = input.int(20, "Base Lookback")
buffer = input.float(0.5, "Buffer %") / 100

baseHigh    = ta.highest(high, length)[1]      // exclude today
breakLevel  = baseHigh * (1 + buffer)

// CLOSE-basis confirmation (ignore intraday wicks)
breakout = close > breakLevel and close[1] <= breakLevel
plotshape(breakout, style=shape.triangleup,
  location=location.belowbar, color=color.lime, size=size.small,
  text="BO")
plot(baseHigh, color=color.red, style=plot.style_linebr)
alertcondition(breakout, "Confirmed Breakout",
  "Daily close above base by buffer %")`,
          chartink: `( {cash} (
  // Today's close broke 20-day high (excl. today) by 0.5%+
  latest close > 1 day ago max( high, 20 ) * 1.005
  and 1 day ago close <= 1 day ago max( high, 20 )
  and daily volume > 1.5 * daily sma( volume, 20 )
) )`,
          formula: `Breakout confirmation rules (avoid false breaks):

1) CLOSE basis, not intraday wick
   Intraday spike above level then close back below → REJECT.

2) Buffer of 0.3–0.5% above resistance
   Avoids "kissing" the level and reversing.

3) Volume confirmation: ≥ 1.5× 20-day average
   No-volume breakouts fail 65%+ of the time.

4) Wait for the 2nd green candle (optional, stricter)
   Fewer trades but higher win rate.

Risk = Entry − Base High  (tight, typically 2–3%)
Avoid if risk > 4% — base is too wide, R:R math breaks.`,
        },
      },
      {
        id: "pe6",
        text: "Volume is 1.5x–2x the 20-day average volume",
        code: {
          pine: `//@version=5
indicator("Volume Spike", overlay=false)
volAvg   = ta.sma(volume, 20)
volRatio = volume / volAvg

spikeHigh = volRatio >= 2.0     // strong confirmation
spikeMed  = volRatio >= 1.5     // minimum threshold

plot(volume,   style=plot.style_columns,
  color = spikeHigh ? color.lime
        : spikeMed  ? color.yellow
        : color.gray)
plot(volAvg, color=color.orange, linewidth=2)`,
          chartink: `( {cash} (
  daily volume > 1.5 * daily sma( volume, 20 )
  and latest close > 1 day ago high
) )`,
        },
      },
      {
        id: "pe7",
        text: "Nifty is not in a strong downtrend",
        code: {
          pine: `//@version=5
indicator("Nifty Regime Filter", overlay=false)
nifty       = request.security("NSE:NIFTY", "D", close)
niftyEma50  = request.security("NSE:NIFTY", "D", ta.ema(close, 50))
niftyEma200 = request.security("NSE:NIFTY", "D", ta.ema(close, 200))

// Healthy: Nifty above both EMAs, 50 > 200
healthy   = nifty > niftyEma50 and niftyEma50 > niftyEma200
unhealthy = nifty < niftyEma50 and nifty < niftyEma200

regime = healthy ? 1 : unhealthy ? -1 : 0
plot(regime, "Regime", style=plot.style_columns,
  color = healthy ? color.green : unhealthy ? color.red : color.gray)
hline(0, "Neutral")

// Trade rule: take fresh swing longs ONLY when regime == 1`,
          formula: `Nifty regime gate for swing longs:

  Nifty close > 50 DMA  AND  50 DMA > 200 DMA   →  GREEN
    • Take all valid setups, full size

  Nifty close > 200 DMA  but  < 50 DMA          →  AMBER
    • Half size, only A+ setups (RS > 1.1, clean base)

  Nifty close < 200 DMA                         →  RED
    • No fresh swing longs
    • Tighten existing stops, book partials

Historical: ~80% of swing winners come from GREEN regime.
The remaining 20% don't justify trading in RED.`,
        },
      },
      {
        id: "pe8",
        text: "Not entering during F&O expiry day (Thursday)",
        code: {
          pine: `//@version=5
indicator("Expiry Day Block", overlay=true)

// NSE weekly Nifty expiry: every Thursday
isExpiry = dayofweek == dayofweek.thursday

// Pre-expiry caution (Wed afternoon often choppy too)
isPreExpiry = dayofweek == dayofweek.wednesday and hour >= 13

bgcolor(isExpiry    ? color.new(color.red,    88) : na, title="Expiry")
bgcolor(isPreExpiry ? color.new(color.orange, 92) : na, title="Pre-Expiry")
plotshape(isExpiry and barstate.isconfirmed,
  style=shape.xcross, location=location.top,
  color=color.red, text="EXP", size=size.tiny)`,
          formula: `F&O expiry — avoid fresh swing entries:

  Weekly Nifty expiry:   Every Thursday
  Weekly Bank Nifty:     Every Wednesday (historical)
  Monthly expiry:        Last Thursday of the month
                         (Wed–Thu–Fri all noisy)

Why avoid?
  • Index pinning around major strikes distorts cash market
  • Whippy intraday action triggers fresh SLs
  • Spike-and-reverse moves on rollover unwinds
  • Liquidity drops after 3:00 PM block-deal window

Safe entry days:  Monday, Tuesday  (Wednesday OK if not
expiry week). Use Friday for partial profit-booking only.`,
        },
      },
      {
        id: "pe9",
        text: "Entry price is within 3% of breakout level (no chasing)",
        code: {
          formula: `Chase % = (Current Price − Breakout Level) / Breakout Level × 100

Decision:
  Chase % ≤ 3%   →  OK to enter
  Chase % > 3%   →  WAIT for pullback or skip

Example:
  Breakout Level = ₹500
  Current Price  = ₹518
  Chase %        = (518 − 500) / 500 × 100 = 3.6%   ✗ Too late

Late entries kill R:R because stop loss stays at the
base while your entry price drifts up.`,
        },
      },
      { id: "pe10", text: "Max 3–5 open positions total — am I within limit?" },
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
        text: "Stop-loss order placed below base / key S/R level (2–4%)",
        code: {
          pine: `//@version=5
indicator("ATR Stop Loss", overlay=true)
atr = ta.atr(14)
mult = input.float(2.0, "ATR Multiplier")

stopLoss = close - mult * atr
plot(stopLoss, color=color.red, style=plot.style_linebr,
  title="Stop Loss")`,
          formula: `Two methods — use the WIDER of the two:

1) STRUCTURE-BASED (preferred for swings):
   SL = Base Low − 0.5%
   Example: Base low ₹482 → SL = ₹479.5

2) VOLATILITY-BASED (ATR):
   SL = Entry − ( 2 × ATR(14) )
   Example: Entry ₹500, ATR ₹8 → SL = ₹500 − ₹16 = ₹484

Use the wider one to avoid getting stopped on noise.
Never set SL based on "how much I'm willing to lose" —
it must respect market structure.`,
        },
      },
      { id: "rm2", text: "GTT order set on Zerodha / Groww — not a mental stop" },
      {
        id: "rm3",
        text: "Target 1 set at 1.5x risk (book 50% here)",
        code: {
          formula: `Target 1 = Entry + ( 1.5 × Risk per Share )

Example:
  Entry = ₹500, SL = ₹485
  Risk  = ₹15
  T1    = 500 + (1.5 × 15) = ₹522.50

At T1:
  • Book 50% of position
  • Move SL on remaining 50% to breakeven (₹500)
  • Now your worst case = 0 (psychologically priceless)`,
        },
      },
      {
        id: "rm4",
        text: "Target 2 set using base height projection",
        code: {
          formula: `Target 2 = Breakout Level + ( Base High − Base Low )

Example:
  Base High = ₹500  (= breakout level)
  Base Low  = ₹460
  Base Height = ₹40
  T2 = ₹500 + ₹40 = ₹540

This is the "measured move" — markets often travel a
distance equal to the base they broke out of.

For longer bases (>15 days), targets can extend to
1.5× or 2× the base height.`,
        },
      },
      { id: "rm5", text: "Trade logged in journal: entry, SL, targets, thesis" },
    ],
  },
  {
    id: "trade-management",
    label: "Trade Management",
    time: "While Holding",
    color: "#8B5CF6",
    icon: "⏱️",
    items: [
      { id: "tm1", text: "Review open positions after market close (3:30 PM)" },
      {
        id: "tm2",
        text: "If price closes back inside base — exit, no overrides",
        code: {
          pine: `//@version=5
indicator("Failed Breakout Exit", overlay=true)
length = input.int(20, "Base Length")

baseHigh = ta.highest(high, length)[length]
baseLow  = ta.lowest(low,   length)[length]

// We assume position opened on a recent breakout above baseHigh
brokeOut    = high > baseHigh
inPosition  = ta.barssince(brokeOut) < 15      // still within trade window

// Exit signal: price closes back inside the base zone
failedBreak = inPosition and close < baseHigh
exitNow     = failedBreak and close[1] >= baseHigh

plot(baseHigh, color=color.red, style=plot.style_linebr)
plotshape(exitNow, style=shape.xcross,
  location=location.abovebar, color=color.red, size=size.normal,
  text="EXIT")
alertcondition(exitNow, "Failed Breakout",
  "Price closed back inside base — exit position")`,
          chartink: `( {cash} (
  // Failed-breakout watchlist filter:
  // broke out in the last 5 sessions, but is now back
  // below the breakout level on a daily close
  5 days ago close > 5 days ago max( high, 20 )
  and latest close < 5 days ago max( high, 20 )
) )`,
        },
      },
      {
        id: "tm3",
        text: "Move stop to breakeven after Target 1 is hit",
        code: {
          formula: `Stop-loss promotion ladder (one-way ratchet):

  Trade state              →  Stop-loss action
  ─────────────────────────────────────────────
  Just entered             →  Initial SL below base
  Price reaches T1 (1.5R)  →  Move SL to ENTRY (breakeven)
  Price reaches 2R         →  Move SL to T1 level (lock 1.5R)
  Price reaches 3R         →  Switch to 21-EMA trail
  Each new closing high    →  Re-trail using EMA rule

Why this works:
  • After T1: worst case = 0 (no losers from here)
  • Locks in profit progressively as price extends
  • Lets winners run via EMA trail

Critical: ONLY raise stops, never lower.
If volatility expands mid-trade, accept smaller next
position size — NEVER widen the stop on a live trade.`,
        },
      },
      {
        id: "tm4",
        text: "Trail stop-loss using 21 EMA on daily as guide",
        code: {
          pine: `//@version=5
indicator("21 EMA Trailing Stop", overlay=true)
ema21 = ta.ema(close, 21)

// Trail stop just below 21 EMA, with buffer
buffer = input.float(1.5, "Buffer %") / 100
trailStop = ema21 * (1 - buffer)

plot(ema21,    color=color.aqua, linewidth=2)
plot(trailStop, color=color.red,  linewidth=1,
  style=plot.style_linebr, title="Trail Stop")

// Exit signal
exitSignal = close < trailStop and close[1] >= trailStop[1]
plotshape(exitSignal, style=shape.xcross,
  location=location.abovebar, color=color.red, size=size.normal)`,
          formula: `Two trailing methods — pick one and stick to it:

1) EMA TRAIL (smooth, fewer whipsaws):
   New SL = max( old SL, 21 EMA × 0.985 )

2) CHANDELIER EXIT (volatility-aware):
   New SL = max( old SL, Highest High(22) − 3 × ATR(22) )

Rules:
  • Only RAISE stops, never lower
  • Update SL at end of day, not intraday
  • Exit fully on daily close below the trail`,
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
        text: "Update trade journal: actual exit, P&L, holding period",
        code: {
          formula: `Trade journal — log these fields on every close:

  ENTRY:        Date, price, quantity, ₹ deployed
  EXIT:         Date, price, reason (T1 / T2 / SL / trail / time)
  HOLDING:      Days held (calendar and trading)
  P&L:          ₹ amount and % return on deployed capital
  R-MULTIPLE:   (Exit − Entry) ÷ (Entry − Initial SL)
                  +2R  =  hit 2x risk target
                  −1R  =  full stop loss
                  0R   =  breakeven exit

Setup tags (for later analysis):
  Pattern:   flat-base / bull-flag / asc-triangle / 52w-high
  Sector:    IT / Banks / Auto / Pharma / ...
  Regime:    GREEN / AMBER / RED  (Nifty bias at entry)
  Planned R:R  vs  Achieved R:R

Why R-multiple matters more than ₹ P&L:
  Tracking in R units normalizes across position sizes
  and reveals which setups truly have an edge.`,
        },
      },
      { id: "er2", text: "Was the setup valid? Did I follow the checklist?" },
      { id: "er3", text: "What did I do well? What could be improved?" },
      {
        id: "er4",
        text: "Review weekly P&L — am I overtrading?",
        code: {
          formula: `Overtrading detection — weekly review:

  TRADE FREQUENCY:
    > 5 swing trades / week    →  Likely overtrading
    3–5 trades / week          →  Healthy active
    1–3 trades / week          →  Selective (ideal)
    < 1 trade / 2 weeks        →  Possibly too cautious

  QUALITY FLAGS:
    Win rate drops > 10% vs 20-trade baseline  →  revenge trading
    Avg holding < 3 days                       →  scalping in disguise
    Avg R captured < 1                         →  setups not panning out

  DRAWDOWN BRAKES:
    Weekly P&L < −2% of capital   →  Halve position size next week
    Weekly P&L < −4% of capital   →  PAUSE entries for 5 trading days
    Monthly P&L < −6%             →  Stop, review system, paper trade

  TIME DISCIPLINE:
    Max ~30 min/day on screens outside trade management.
    Checking quotes > 10x/day  →  reduce size, that's a
    signal of poor sizing or unclear thesis.`,
        },
      },
      {
        id: "er5",
        text: "Win rate and avg risk:reward over last 20 trades?",
        code: {
          formula: `Key edge metrics (calculate over last 20+ trades):

WIN RATE = Winning Trades / Total Trades × 100

AVG WIN    = Sum of all wins   / Number of winners
AVG LOSS   = Sum of all losses / Number of losers
PAYOFF RATIO = Avg Win / Avg Loss

PROFIT FACTOR = Gross Profit / Gross Loss
  • > 1.5  →  Healthy strategy
  • > 2.0  →  Excellent
  • < 1.0  →  Losing money — stop & fix

EXPECTANCY (₹ earned per ₹1 risked):
  E = (Win% × Avg Win) − (Loss% × Avg Loss)

Example:
  20 trades, 9 wins (45%), 11 losses (55%)
  Avg Win = ₹4,500, Avg Loss = ₹2,000
  Payoff  = 4500/2000 = 2.25
  E = (0.45 × 4500) − (0.55 × 2000)
    = 2025 − 1100 = +₹925 per trade
  →  Profitable system, keep executing.`,
        },
      },
      { id: "er6", text: "Update watchlist for next session" },
    ],
  },
];

// ---------- Code block component ----------
function CodeBlock({ code, color }) {
  const tabs = [];
  if (code.pine) tabs.push({ key: "pine", label: "Pine Script" });
  if (code.chartink) tabs.push({ key: "chartink", label: "Chartink" });
  if (code.formula) tabs.push({ key: "formula", label: "Formula" });

  const [activeTab, setActiveTab] = useState(tabs[0].key);
  const [copied, setCopied] = useState(false);

  const handleCopy = (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(code[activeTab]);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  // Subtle syntax dimming for comments
  const renderCode = (text) => {
    return text.split("\n").map((line, i) => {
      const isComment = line.trim().startsWith("//") || line.trim().startsWith("#");
      return (
        <div key={i} style={{
          color: isComment ? "#475569" : "#CBD5E1",
          fontStyle: isComment ? "italic" : "normal",
        }}>
          {line || " "}
        </div>
      );
    });
  };

  return (
    <div
      onClick={(e) => e.stopPropagation()}
      style={{
        marginTop: "12px",
        background: "#08080F",
        border: "1px solid #1E2035",
        borderRadius: "6px",
        overflow: "hidden",
      }}
    >
      {/* Tab bar */}
      <div style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        borderBottom: "1px solid #1E2035",
        background: "#0D0D18",
        padding: "0 8px",
      }}>
        <div style={{ display: "flex" }}>
          {tabs.map((tab) => (
            <button
              key={tab.key}
              onClick={(e) => { e.stopPropagation(); setActiveTab(tab.key); }}
              style={{
                background: "transparent",
                border: "none",
                color: activeTab === tab.key ? color : "#64748B",
                padding: "8px 12px",
                fontSize: "11px",
                letterSpacing: "1px",
                cursor: "pointer",
                borderBottom: activeTab === tab.key
                  ? `2px solid ${color}`
                  : "2px solid transparent",
                fontFamily: "inherit",
                transition: "all 0.2s",
              }}
            >
              {tab.label.toUpperCase()}
            </button>
          ))}
        </div>
        <button
          onClick={handleCopy}
          style={{
            background: copied ? color + "33" : "transparent",
            border: `1px solid ${copied ? color + "66" : "#2D2D45"}`,
            color: copied ? color : "#94A3B8",
            padding: "4px 10px",
            fontSize: "10px",
            letterSpacing: "1px",
            cursor: "pointer",
            borderRadius: "4px",
            fontFamily: "inherit",
            transition: "all 0.2s",
          }}
        >
          {copied ? "✓ COPIED" : "COPY"}
        </button>
      </div>

      {/* Code body */}
      <pre style={{
        margin: 0,
        padding: "14px 16px",
        fontSize: "12px",
        lineHeight: "1.6",
        fontFamily: "'DM Mono', 'Courier New', monospace",
        overflowX: "auto",
        whiteSpace: "pre",
      }}>
        {renderCode(code[activeTab])}
      </pre>
    </div>
  );
}

// ---------- Main component ----------
export default function SwingTradingChecklist() {
  const [checked, setChecked] = useState({});
  const [expanded, setExpanded] = useState({});
  const [activeSection, setActiveSection] = useState("watchlist");

  const toggle = (id) => setChecked((prev) => ({ ...prev, [id]: !prev[id] }));
  const toggleExpand = (id, e) => {
    e.stopPropagation();
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const totalItems = sections.reduce((acc, s) => acc + s.items.length, 0);
  const checkedCount = Object.values(checked).filter(Boolean).length;
  const progress = Math.round((checkedCount / totalItems) * 100);

  const sectionProgress = (section) => {
    const done = section.items.filter((i) => checked[i.id]).length;
    return Math.round((done / section.items.length) * 100);
  };

  const resetAll = () => { setChecked({}); setExpanded({}); };

  const active = sections.find((s) => s.id === activeSection);

  return (
    <div style={{
      minHeight: "100vh",
      background: "#0A0A0F",
      color: "#E2E8F0",
      fontFamily: "'DM Mono', 'Courier New', monospace",
      display: "flex",
      flexDirection: "column",
    }}>
      {/* Header */}
      <div style={{
        background: "linear-gradient(135deg, #0F0F1A 0%, #1A1A2E 100%)",
        borderBottom: "1px solid #1E2035",
        padding: "24px 32px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        flexWrap: "wrap",
        gap: "16px",
      }}>
        <div>
          <div style={{ fontSize: "11px", letterSpacing: "3px", color: "#6366F1", marginBottom: "6px", textTransform: "uppercase" }}>
            Indian Markets • NSE / BSE
          </div>
          <h1 style={{ margin: 0, fontSize: "22px", fontWeight: "700", color: "#F1F5F9", letterSpacing: "-0.5px" }}>
            Swing Trading Checklist + Formulas
          </h1>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "20px" }}>
          <div style={{ position: "relative", width: "64px", height: "64px" }}>
            <svg viewBox="0 0 64 64" style={{ transform: "rotate(-90deg)", width: "64px", height: "64px" }}>
              <circle cx="32" cy="32" r="26" fill="none" stroke="#1E2035" strokeWidth="5" />
              <circle
                cx="32" cy="32" r="26" fill="none"
                stroke="#6366F1" strokeWidth="5"
                strokeDasharray={`${2 * Math.PI * 26}`}
                strokeDashoffset={`${2 * Math.PI * 26 * (1 - progress / 100)}`}
                strokeLinecap="round"
                style={{ transition: "stroke-dashoffset 0.4s ease" }}
              />
            </svg>
            <div style={{
              position: "absolute", inset: 0, display: "flex",
              alignItems: "center", justifyContent: "center",
              fontSize: "12px", fontWeight: "700", color: "#A5B4FC",
            }}>
              {progress}%
            </div>
          </div>

          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: "22px", fontWeight: "700", color: "#F1F5F9" }}>
              {checkedCount}<span style={{ color: "#475569", fontSize: "16px" }}>/{totalItems}</span>
            </div>
            <div style={{ fontSize: "11px", color: "#64748B", letterSpacing: "1px" }}>COMPLETED</div>
          </div>

          <button onClick={resetAll} style={{
            background: "transparent",
            border: "1px solid #2D2D45",
            color: "#94A3B8",
            padding: "8px 14px",
            borderRadius: "6px",
            cursor: "pointer",
            fontSize: "11px",
            letterSpacing: "1px",
            fontFamily: "inherit",
          }}>
            RESET
          </button>
        </div>
      </div>

      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        {/* Sidebar */}
        <div style={{
          width: "230px",
          minWidth: "230px",
          background: "#0D0D18",
          borderRight: "1px solid #1E2035",
          padding: "16px 0",
          overflowY: "auto",
        }}>
          {sections.map((section, idx) => {
            const pct = sectionProgress(section);
            const isActive = activeSection === section.id;
            const hasCode = section.items.some((i) => i.code);
            return (
              <button
                key={section.id}
                onClick={() => setActiveSection(section.id)}
                style={{
                  width: "100%",
                  background: isActive ? "#151525" : "transparent",
                  border: "none",
                  borderLeft: isActive ? `3px solid ${section.color}` : "3px solid transparent",
                  cursor: "pointer",
                  padding: "12px 16px",
                  textAlign: "left",
                  display: "flex",
                  alignItems: "center",
                  gap: "10px",
                  fontFamily: "inherit",
                }}
              >
                <span style={{
                  fontSize: "10px",
                  color: isActive ? section.color : "#334155",
                  fontWeight: "600",
                  minWidth: "14px",
                }}>
                  {String(idx + 1).padStart(2, "0")}
                </span>
                <span style={{ fontSize: "16px" }}>{section.icon}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontSize: "12px",
                    fontWeight: isActive ? "600" : "400",
                    color: isActive ? "#F1F5F9" : "#64748B",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    display: "flex",
                    alignItems: "center",
                    gap: "6px",
                  }}>
                    {section.label}
                    {hasCode && <span style={{ fontSize: "9px", color: section.color }}>{"</>"}</span>}
                  </div>
                  <div style={{
                    marginTop: "4px",
                    height: "3px",
                    background: "#1E2035",
                    borderRadius: "2px",
                    overflow: "hidden",
                  }}>
                    <div style={{
                      height: "100%",
                      width: `${pct}%`,
                      background: section.color,
                      borderRadius: "2px",
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

        {/* Main content */}
        <div style={{ flex: 1, overflowY: "auto", padding: "28px 32px" }}>
          {active && (
            <div>
              {/* Section header */}
              <div style={{
                display: "flex",
                alignItems: "flex-start",
                justifyContent: "space-between",
                marginBottom: "24px",
                flexWrap: "wrap",
                gap: "8px",
              }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "6px" }}>
                    <span style={{ fontSize: "24px" }}>{active.icon}</span>
                    <h2 style={{ margin: 0, fontSize: "20px", fontWeight: "700", color: "#F1F5F9" }}>
                      {active.label}
                    </h2>
                  </div>
                  <div style={{
                    display: "inline-block",
                    background: "#151525",
                    border: `1px solid ${active.color}33`,
                    color: active.color,
                    fontSize: "11px",
                    letterSpacing: "2px",
                    padding: "3px 10px",
                    borderRadius: "4px",
                  }}>
                    {active.time}
                  </div>
                </div>
                <div style={{ fontSize: "13px", color: "#475569" }}>
                  {active.items.filter(i => checked[i.id]).length} / {active.items.length} done
                </div>
              </div>

              {/* Checklist items */}
              <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                {active.items.map((item, idx) => {
                  const isDone = !!checked[item.id];
                  const isExpanded = !!expanded[item.id];
                  const hasCode = !!item.code;
                  return (
                    <div
                      key={item.id}
                      style={{
                        background: isDone ? "#0F1F18" : "#111120",
                        border: `1px solid ${isDone ? active.color + "44" : "#1E2035"}`,
                        borderRadius: "8px",
                        transition: "all 0.2s",
                      }}
                    >
                      <div
                        onClick={() => toggle(item.id)}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "14px",
                          padding: "14px 18px",
                          cursor: "pointer",
                          userSelect: "none",
                        }}
                      >
                        <div style={{
                          width: "20px", height: "20px",
                          minWidth: "20px",
                          border: `2px solid ${isDone ? active.color : "#2D2D55"}`,
                          borderRadius: "4px",
                          background: isDone ? active.color : "transparent",
                          display: "flex", alignItems: "center", justifyContent: "center",
                          fontSize: "12px",
                        }}>
                          {isDone && <span style={{ color: "#000", fontWeight: "700" }}>✓</span>}
                        </div>

                        <span style={{
                          fontSize: "11px",
                          color: isDone ? active.color + "88" : "#2D2D55",
                          minWidth: "18px",
                          fontWeight: "600",
                        }}>
                          {String(idx + 1).padStart(2, "0")}
                        </span>

                        <span style={{
                          fontSize: "13px",
                          color: isDone ? "#64748B" : "#CBD5E1",
                          textDecoration: isDone ? "line-through" : "none",
                          flex: 1,
                          lineHeight: "1.5",
                        }}>
                          {item.text}
                        </span>

                        {hasCode && (
                          <button
                            onClick={(e) => toggleExpand(item.id, e)}
                            style={{
                              background: isExpanded ? active.color + "22" : "transparent",
                              border: `1px solid ${isExpanded ? active.color + "66" : "#2D2D45"}`,
                              color: isExpanded ? active.color : "#94A3B8",
                              padding: "4px 10px",
                              fontSize: "10px",
                              letterSpacing: "1px",
                              cursor: "pointer",
                              borderRadius: "4px",
                              fontFamily: "inherit",
                              whiteSpace: "nowrap",
                              transition: "all 0.2s",
                            }}
                          >
                            {isExpanded ? "▼ HIDE" : "</> CODE"}
                          </button>
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

              {/* Section nav */}
              <div style={{
                display: "flex",
                justifyContent: "space-between",
                marginTop: "32px",
                paddingTop: "20px",
                borderTop: "1px solid #1E2035",
              }}>
                {(() => {
                  const idx = sections.findIndex(s => s.id === activeSection);
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
                        }}>
                          ← {prev.label}
                        </button>
                      ) : <div />}
                      {next && (
                        <button onClick={() => setActiveSection(next.id)} style={{
                          background: active.color + "22",
                          border: `1px solid ${active.color}55`,
                          color: active.color, padding: "8px 16px", borderRadius: "6px",
                          cursor: "pointer", fontSize: "12px", letterSpacing: "1px",
                          fontFamily: "inherit",
                        }}>
                          {next.label} →
                        </button>
                      )}
                    </>
                  );
                })()}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Footer */}
      <div style={{
        borderTop: "1px solid #1E2035",
        padding: "10px 32px",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        background: "#0D0D18",
        fontSize: "11px",
        color: "#334155",
        letterSpacing: "1px",
      }}>
        <span>PINE SCRIPT v5 • CHARTINK • RISK MATH</span>
        <span>CLICK {"</>"} CODE ON ANY ITEM</span>
      </div>
    </div>
  );
}
