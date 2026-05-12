"""
Trade Plan Calculator
Generates entry zone, targets, and stop loss from technical levels
"""
from typing import Dict


def calculate_trade_plan(stock_data: Dict) -> Dict:
    price        = stock_data.get('price', 0)
    ema20        = stock_data.get('ema20', 0)
    ema50        = stock_data.get('ema50', 0)
    support_1    = stock_data.get('support_1', 0)
    resistance_1 = stock_data.get('resistance_1', 0)
    resistance_2 = stock_data.get('resistance_2', 0)
    atr          = stock_data.get('atr', price * 0.02)

    plan: Dict = {}

    # ── Pullback entry: price above EMA20 and EMA20 > EMA50 ──────────────
    if price >= ema20 > 0 and ema20 > ema50:
        plan['setup_type']      = 'PULLBACK_ENTRY'
        plan['entry_zone_min']  = round(ema20 * 0.99, 2)
        plan['entry_zone_max']  = round(ema20 * 1.005, 2)
        plan['target_1']        = round(resistance_1, 2) if resistance_1 > price else round(price * 1.05, 2)
        plan['target_2']        = round(resistance_2, 2) if resistance_2 > plan['target_1'] else round(price * 1.10, 2)
        plan['stop_loss']       = round(max(support_1 - atr, ema50 * 0.98), 2)

    # ── Breakout: price just above resistance ────────────────────────────
    elif resistance_1 > 0 and price > resistance_1 and (resistance_2 == 0 or price < resistance_2):
        move = max(price - support_1, price * 0.03)
        plan['setup_type']      = 'BREAKOUT'
        plan['entry_zone_min']  = round(resistance_1, 2)
        plan['entry_zone_max']  = round(resistance_1 * 1.01, 2)
        plan['target_1']        = round(price + move * 0.5, 2)
        plan['target_2']        = round(price + move, 2)
        plan['stop_loss']       = round(support_1, 2) if support_1 else round(price * 0.96, 2)

    # ── Support bounce ───────────────────────────────────────────────────
    elif support_1 > 0 and price <= support_1 * 1.02:
        plan['setup_type']      = 'SUPPORT_BOUNCE'
        plan['entry_zone_min']  = round(support_1 * 0.99, 2)
        plan['entry_zone_max']  = round(support_1 * 1.005, 2)
        plan['target_1']        = round(ema20, 2) if ema20 > price else round(price * 1.04, 2)
        plan['target_2']        = round(resistance_1, 2) if resistance_1 > plan['target_1'] else round(price * 1.08, 2)
        plan['stop_loss']       = round(support_1 - atr, 2)

    # ── Consolidation / default ──────────────────────────────────────────
    else:
        plan['setup_type']      = 'CONSOLIDATION'
        plan['entry_zone_min']  = round(ema20 * 0.99, 2) if ema20 else round(price * 0.99, 2)
        plan['entry_zone_max']  = round(ema50 * 1.005, 2) if ema50 else round(price * 1.005, 2)
        plan['target_1']        = round(resistance_1, 2) if resistance_1 > price else round(price * 1.05, 2)
        plan['target_2']        = round(resistance_2, 2) if resistance_2 > plan['target_1'] else round(price * 1.10, 2)
        plan['stop_loss']       = round(support_1, 2) if support_1 else round(price * 0.95, 2)

    # ── R:R ──────────────────────────────────────────────────────────────
    entry_mid    = (plan['entry_zone_min'] + plan['entry_zone_max']) / 2
    risk         = entry_mid - plan['stop_loss']
    reward       = plan['target_2'] - entry_mid
    plan['rr_ratio'] = round(reward / risk, 1) if risk > 0 else 0

    return plan


def calculate_rr(stock_data: Dict) -> str:
    plan = calculate_trade_plan(stock_data)
    rr   = plan.get('rr_ratio', 0)
    return f"1:{rr}" if rr else "N/A"
