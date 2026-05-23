"""
Signal Generator Agent
Combines technical scores with fundamental screening to emit BUY/SELL signals.
"""

from typing import Any, Dict, List

from core.agents.base_agent import BaseAgent
from core.agents.config import OrchestrationConfig
from core.agents.models import FundamentalSnapshot, Signal, TechnicalSnapshot, TradeSignal
from core.agents.technical_analyst import TechnicalAnalystAgent


class SignalGeneratorAgent(BaseAgent):
    """Stage 4 — produce ranked actionable signals.

    Scoring rubric (total -1.0 to +1.0):
      >= 0.35  -> STRONG_BUY
      >= 0.15  -> BUY
      <= -0.35 -> STRONG_SELL
      <= -0.15 -> SELL
      else     -> HOLD
    """

    def __init__(self, config: OrchestrationConfig = None) -> None:
        super().__init__("SignalGenerator")
        self.cfg = config or OrchestrationConfig()
        self._tech_analyst = TechnicalAnalystAgent(self.cfg)

    def execute(self, **kwargs: Any) -> List[TradeSignal]:
        snapshots: List[TechnicalSnapshot] = kwargs.get("snapshots", [])
        fundamentals: List[FundamentalSnapshot] = kwargs.get("fundamentals", [])
        self.log(f"Generating signals for {len(snapshots)} stocks")

        fund_map: Dict[str, FundamentalSnapshot] = {f.symbol: f for f in fundamentals}

        signals: List[TradeSignal] = []
        for snap in snapshots:
            fund = fund_map.get(snap.symbol)
            # Skip stocks that failed fundamental/risk screening
            if fund is not None and not fund.passes_filter:
                continue

            signal = self._generate(snap, fund)
            if signal.signal in (Signal.STRONG_BUY, Signal.BUY):
                signals.append(signal)

        signals.sort(key=lambda s: s.confidence, reverse=True)
        self.log(f"Generated {len(signals)} actionable BUY signals")
        return signals

    def _generate(
        self, snap: TechnicalSnapshot, fund: FundamentalSnapshot = None
    ) -> TradeSignal:
        score, reasons = self._tech_analyst.score_setup(snap)

        # Small bonus for sector alignment
        if fund and fund.sector not in ("OTHERS", "Unknown"):
            score += 0.02
            reasons.append(f"Sector identified: {fund.sector}")

        signal = self._score_to_signal(score)
        confidence = min(1.0, abs(score))

        return TradeSignal(
            symbol=snap.symbol,
            signal=signal,
            confidence=confidence,
            technical_score=score,
            reasons=reasons,
        )

    @staticmethod
    def _score_to_signal(score: float) -> Signal:
        if score >= 0.35:
            return Signal.STRONG_BUY
        if score >= 0.15:
            return Signal.BUY
        if score <= -0.35:
            return Signal.STRONG_SELL
        if score <= -0.15:
            return Signal.SELL
        return Signal.HOLD
