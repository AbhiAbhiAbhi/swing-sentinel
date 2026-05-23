"""
Risk Manager Agent
Calculates stop-loss, targets, and position sizing using the existing
trade-plan calculator (core_trade_plan.py) enriched with portfolio-level
risk budgeting.
"""

import math
from typing import Any, Dict, List, Optional

from core.agents.base_agent import BaseAgent
from core.agents.config import OrchestrationConfig
from core.agents.models import RiskAssessment, TechnicalSnapshot, TradeSignal

# Reuse the existing trade-plan logic
try:
    from core.core_trade_plan import calculate_trade_plan
except ImportError:
    from core_trade_plan import calculate_trade_plan


class RiskManagerAgent(BaseAgent):
    """Stage 5 — risk/reward analysis and position sizing.

    Reuses ``calculate_trade_plan`` for setup-specific stop-loss and target
    calculations, then layers portfolio-level risk budgeting on top.
    """

    def __init__(self, config: OrchestrationConfig = None) -> None:
        super().__init__("RiskManager")
        self.cfg = config or OrchestrationConfig()

    def execute(self, **kwargs: Any) -> List[RiskAssessment]:
        signals: List[TradeSignal] = kwargs.get("signals", [])
        stocks: List[Dict] = kwargs.get("stocks", [])
        snapshots: List[TechnicalSnapshot] = kwargs.get("snapshots", [])
        portfolio_value: float = kwargs.get(
            "portfolio_value", self.cfg.portfolio_value
        )

        self.log(f"Assessing risk for {len(signals)} signals (portfolio ₹{portfolio_value:,.0f})")

        stock_map: Dict[str, Dict] = {s.get("symbol", ""): s for s in stocks}
        snap_map: Dict[str, TechnicalSnapshot] = {s.symbol: s for s in snapshots}
        results: List[RiskAssessment] = []

        for signal in signals:
            tech = stock_map.get(signal.symbol, {})
            snap = snap_map.get(signal.symbol)
            assessment = self._assess(signal, tech, snap, portfolio_value)
            if assessment is not None:
                results.append(assessment)

        self.log(f"Produced {len(results)} risk-assessed trades")
        return results

    def _assess(
        self,
        signal: TradeSignal,
        tech: Dict,
        snap: Optional[TechnicalSnapshot],
        portfolio_value: float,
    ) -> Optional[RiskAssessment]:
        if not tech:
            return None

        # Use the existing trade plan calculator for setup-specific SL/targets
        plan = calculate_trade_plan(tech)
        entry_min = plan.get("entry_zone_min", 0)
        entry_max = plan.get("entry_zone_max", 0)
        entry = (entry_min + entry_max) / 2 if (entry_min and entry_max) else tech.get("price", 0)
        stop_loss = plan.get("stop_loss", 0)
        target_1 = plan.get("target_1", 0)
        target_2 = plan.get("target_2", 0)
        setup_type = plan.get("setup_type", "")
        rr_ratio = plan.get("rr_ratio", 0)

        if entry <= 0 or stop_loss <= 0 or stop_loss >= entry:
            return None

        # Derive target_3 from ATR
        atr = tech.get("atr", 0) or (entry * 0.02)
        target_3 = round(entry + self.cfg.target_atr_multiplier * atr, 2)
        target_3 = max(target_3, target_2 * 1.02) if target_2 else target_3

        # Risk-reward validation
        risk_per_share = entry - stop_loss
        if risk_per_share <= 0:
            return None

        if rr_ratio < self.cfg.min_risk_reward:
            return None

        # Position sizing — risk-budget method
        max_risk_amount = portfolio_value * (self.cfg.max_risk_per_trade_pct / 100)
        position_size = math.floor(max_risk_amount / risk_per_share)
        if position_size <= 0:
            return None

        # Cap by max per-position allocation
        max_pos_value = portfolio_value / self.cfg.max_positions
        if position_size * entry > max_pos_value:
            position_size = math.floor(max_pos_value / entry)

        if position_size <= 0:
            return None

        position_value = position_size * entry
        risk_amount = position_size * risk_per_share
        risk_pct = (risk_amount / portfolio_value) * 100

        return RiskAssessment(
            symbol=signal.symbol,
            entry_price=round(entry, 2),
            stop_loss=round(stop_loss, 2),
            target_1=round(target_1, 2),
            target_2=round(target_2, 2),
            target_3=round(target_3, 2),
            risk_reward_ratio=round(rr_ratio, 1),
            position_size_shares=position_size,
            position_value=round(position_value, 2),
            risk_amount=round(risk_amount, 2),
            risk_pct_of_portfolio=round(risk_pct, 2),
            atr=round(atr, 2),
            setup_type=setup_type,
        )
