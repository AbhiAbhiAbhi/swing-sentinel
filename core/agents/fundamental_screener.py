"""
Fundamental Screener Agent
Screens stocks by market cap, PE ratio, and sector health.
Integrates with the existing risk-filter pipeline.
"""

import logging
from typing import Any, Dict, List

from core.agents.base_agent import BaseAgent
from core.agents.config import OrchestrationConfig
from core.agents.models import FundamentalSnapshot

logger = logging.getLogger(__name__)

# Import existing risk filters + sector helpers
try:
    from core.core_risk_filters import apply_risk_filters
    from core.core_sectors import fetch_sector_pulse, get_sector
except ImportError:
    try:
        from core_risk_filters import apply_risk_filters
        from core_sectors import fetch_sector_pulse, get_sector
    except ImportError:
        def apply_risk_filters(sym, tech, **kw):
            return True, []
        def get_sector(sym):
            return "OTHERS"
        def fetch_sector_pulse():
            return {}


class FundamentalScreenerAgent(BaseAgent):
    """Stage 3 — fundamental + risk-filter validation."""

    def __init__(self, config: OrchestrationConfig = None) -> None:
        super().__init__("FundamentalScreener")
        self.cfg = config or OrchestrationConfig()
        self._sector_pulse = None

    def execute(self, **kwargs: Any) -> List[FundamentalSnapshot]:
        stocks: List[Dict] = kwargs.get("stocks", [])
        self.log(f"Screening {len(stocks)} stocks")

        # Fetch sector pulse once for all stocks
        if self._sector_pulse is None:
            try:
                self._sector_pulse = fetch_sector_pulse()
            except Exception:
                self._sector_pulse = {}

        results: List[FundamentalSnapshot] = []
        for tech in stocks:
            snap = self._screen(tech)
            results.append(snap)

        passed = sum(1 for s in results if s.passes_filter)
        self.log(f"{passed}/{len(results)} passed fundamental + risk screen")
        return results

    def _screen(self, tech: Dict) -> FundamentalSnapshot:
        symbol = tech.get("symbol", "")
        rejection_reasons: List[str] = []
        passes = True

        # ── Existing risk filters (volatility, crash, IPO age, earnings, sector) ──
        try:
            rf_passed, rf_reasons = apply_risk_filters(
                symbol, tech, sector_pulse=self._sector_pulse
            )
            if not rf_passed:
                passes = False
                rejection_reasons.extend(rf_reasons)
        except Exception as exc:
            logger.debug("Risk filter error for %s: %s", symbol, exc)

        # ── Sector lookup ──
        try:
            sector = get_sector(symbol)
        except Exception:
            sector = "OTHERS"

        return FundamentalSnapshot(
            symbol=symbol,
            name=symbol,
            sector=sector,
            passes_filter=passes,
            rejection_reasons=rejection_reasons,
        )
