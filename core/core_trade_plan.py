"""
Trade Plan Calculator
Setup-specific SL (invalidation-based) + hybrid ATR targets:
  PULLBACK      → SL = EMA20 − 1×ATR   (setup invalid if price closes below EMA20)
  BREAKOUT      → SL = 20d low − 0.5×ATR  (below consolidation base)
  SUPPORT_BOUNCE→ SL = support − 1×ATR  (support broken = setup done)
  CONSOLIDATION → SL = EMA50 − 1×ATR   (trend anchor)

  T1 = 20-day resistance or entry + 1.5×ATR (whichever is above entry)
  T2 = max(entry + 3×ATR, 60d resistance, T1 × 1.02)
"""
from typing import Dict


def calculate_trade_plan(stock_data: Dict, is_refresh: bool = False) -> Dict:
    """
    Calculate entry zone, setup-specific SL, targets and R:R.

    :param is_refresh: True when recalculating an already-active position (a live
        Keep & Refresh cycle). In that case the hard SL ceiling is evaluated against
        current spot only (price * 0.985), ignoring the static entry_min entry-zone
        artifact that otherwise pins refreshed pullback stops to a flat ~-2.24%.
    """
    price        = stock_data.get('price', 0)
    ema20        = stock_data.get('ema20', 0)
    ema50        = stock_data.get('ema50', 0)
    support_1    = stock_data.get('support_1', 0)
    resistance_1 = stock_data.get('resistance_1', 0)
    resistance_2 = stock_data.get('resistance_2', 0)
    atr          = stock_data.get('atr') or price * 0.02

    # ── Detect setup ─────────────────────────────────────────────────────
    if resistance_1 > 0 and price >= resistance_1:
        setup = 'BREAKOUT'
    elif support_1 > 0 and price <= support_1 * 1.02:
        setup = 'SUPPORT_BOUNCE'
    elif ema20 > 0 and ema50 > 0 and price >= ema50 and ema20 > ema50:
        rsi = stock_data.get('rsi', 0)
        is_pullback = (
            price <= ema20 * 1.025 or
            (ema50 <= price <= ema20) or
            (40 <= rsi <= 60) or
            stock_data.get('rsi_pullback_zone', False)
        )
        setup = 'PULLBACK' if is_pullback else 'CONSOLIDATION'
    else:
        setup = 'CONSOLIDATION'

    # ── Entry zone ───────────────────────────────────────────────────────
    entry_min = round(ema20 * 0.99, 2)  if ema20 else round(price * 0.99, 2)
    entry_max = round(ema20 * 1.005, 2) if ema20 else round(price * 1.005, 2)
    entry_mid = (entry_min + entry_max) / 2

    # ── Setup-specific SL (invalidation level) ───────────────────────────
    if setup == 'PULLBACK':
        sl_raw = (ema20 - atr) if ema20 else (entry_mid - atr)
    elif setup == 'BREAKOUT':
        sl_raw = (support_1 - atr * 0.5) if support_1 else (entry_mid - atr)
    elif setup == 'SUPPORT_BOUNCE':
        sl_raw = (support_1 - atr) if support_1 else (entry_mid - atr)
    else:  # CONSOLIDATION
        sl_raw = (ema50 - atr) if ema50 else (entry_mid - atr)

    # Hard ceiling: SL must always be below current price (and entry zone on fresh entries)
    if is_refresh:
        # Active-position refresh: evaluate strictly against live spot so the real
        # invalidation level flows through instead of pinning to the stale entry_min.
        sl_ceiling = price * 0.985
    else:
        sl_ceiling = min(price, entry_min) * 0.985
    sl = round(min(sl_raw, sl_ceiling), 2)

    # ── T1: nearest resistance or ATR fallback ───────────────────────────
    t1 = round(resistance_1, 2) if resistance_1 > entry_mid else round(entry_mid + 1.5 * atr, 2)

    # ── T2: ATR-based, always above T1 ───────────────────────────────────
    atr_t2 = round(entry_mid + 3.0 * atr, 2)
    res_t2 = round(resistance_2, 2) if resistance_2 > t1 else 0
    t2     = max(atr_t2, res_t2, round(t1 * 1.02, 2))

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


def position_risk(entry, sl, qty=0):
    """
    Per-position risk, persisted once at first scan and then LOCKED (never
    recomputed on a re-scan). Mirrors the dashboard sizer:
      risk_per_share = max(0, entry - sl)   (swing_agent_app.html)
      rupee_risk     = risk_per_share * qty (actual capital at risk)

    Returns (risk_per_share, rupee_risk) as rounded floats. Either value is 0.0
    when its inputs are missing/blank (entry<=sl, or qty unset).
    """
    def _f(v):
        try:
            if v is None or v == "":
                return 0.0
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    entry_f, sl_f, qty_f = _f(entry), _f(sl), _f(qty)
    risk_per_share = round(max(0.0, entry_f - sl_f), 2) if (entry_f > 0 and sl_f > 0) else 0.0
    rupee_risk     = round(risk_per_share * qty_f, 2)
    return risk_per_share, rupee_risk
