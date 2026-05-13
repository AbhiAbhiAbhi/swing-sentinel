"""
Trade Plan Calculator
Hybrid target method:
  T1 = 20-day resistance (real supply zone; fallback: entry + 1.5×ATR)
  T2 = max(entry + 3×ATR, T1 × 1.02)  — ATR-based, always above T1
  SL = below support or EMA50, anchored by ATR
"""
from typing import Dict


def calculate_trade_plan(stock_data: Dict) -> Dict:
    price        = stock_data.get('price', 0)
    ema20        = stock_data.get('ema20', 0)
    ema50        = stock_data.get('ema50', 0)
    support_1    = stock_data.get('support_1', 0)
    resistance_1 = stock_data.get('resistance_1', 0)
    resistance_2 = stock_data.get('resistance_2', 0)
    atr          = stock_data.get('atr') or price * 0.02

    # ── Entry zone: pullback to EMA20 ────────────────────────────────────
    entry_min = round(ema20 * 0.99, 2)  if ema20 else round(price * 0.99, 2)
    entry_max = round(ema20 * 1.005, 2) if ema20 else round(price * 1.005, 2)
    entry_mid = (entry_min + entry_max) / 2

    # ── Stop loss: below support or EMA50, always below price and entry ──
    sl_support = (support_1 - atr * 0.5) if support_1 else 0
    sl_ema50   = (ema50 * 0.98) if ema50 else 0
    sl_raw     = max(sl_support, sl_ema50) if (sl_support or sl_ema50) else 0
    # Hard ceiling: SL must be below both current price and entry zone
    sl_ceiling = min(price, entry_min) * 0.985
    sl = round(min(sl_raw, sl_ceiling, entry_mid - atr) if sl_raw else min(sl_ceiling, entry_mid - atr), 2)

    # ── T1: nearest resistance (institutional supply zone) ───────────────
    # Use 20-day high if it's above entry; else fall back to 1.5×ATR projection
    if resistance_1 > entry_mid:
        t1 = round(resistance_1, 2)
    else:
        t1 = round(entry_mid + 1.5 * atr, 2)

    # ── T2: ATR-based ride target, always above T1 ───────────────────────
    # 3×ATR projection captures a full swing; if 60-day resistance is higher, use it
    atr_t2 = round(entry_mid + 3.0 * atr, 2)
    res_t2 = round(resistance_2, 2) if resistance_2 > t1 else 0
    t2 = max(atr_t2, res_t2, round(t1 * 1.02, 2))  # always > T1

    # ── Setup label ──────────────────────────────────────────────────────
    if ema20 > 0 and ema20 > ema50 and price >= ema20:
        setup = 'PULLBACK'
    elif resistance_1 > 0 and price > resistance_1:
        setup = 'BREAKOUT'
    elif support_1 > 0 and price <= support_1 * 1.02:
        setup = 'SUPPORT_BOUNCE'
    else:
        setup = 'CONSOLIDATION'

    # ── R:R ──────────────────────────────────────────────────────────────
    risk   = entry_mid - sl
    reward = t2 - entry_mid
    rr     = round(reward / risk, 1) if risk > 0 else 0

    return {
        'setup_type':     setup,
        'entry_zone_min': entry_min,
        'entry_zone_max': entry_max,
        'stop_loss':      sl,
        'target_1':       t1,
        'target_2':       t2,
        'rr_ratio':       rr,
    }


def calculate_rr(stock_data: Dict) -> str:
    plan = calculate_trade_plan(stock_data)
    rr   = plan.get('rr_ratio', 0)
    return f"1:{rr}" if rr else "N/A"
