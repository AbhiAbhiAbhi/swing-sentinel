"""
Swing Trade Orchestrator
Coordinates the five-agent pipeline and produces final ranked recommendations.

Pipeline:
  1. MarketScannerAgent      → raw tech dicts (Chartink or yfinance)
  2. TechnicalAnalystAgent   → TechnicalSnapshot list
  3. FundamentalScreenerAgent→ FundamentalSnapshot list (risk filters)
  4. SignalGeneratorAgent     → TradeSignal list (BUY only)
  5. RiskManagerAgent         → RiskAssessment list (sized + validated)

  Final assembly → ranked TradeRecommendation list
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from core.agents.config import OrchestrationConfig
from core.agents.fundamental_screener import FundamentalScreenerAgent
from core.agents.market_scanner import MarketScannerAgent
from core.agents.models import (
    OrchestratorResult,
    TechnicalSnapshot,
    TradeRecommendation,
    TrendDirection,
)
from core.agents.risk_manager import RiskManagerAgent
from core.agents.signal_generator import SignalGeneratorAgent
from core.agents.technical_analyst import TechnicalAnalystAgent

logger = logging.getLogger(__name__)

# Market context helper — best effort
try:
    from core.core_data_fetcher import fetch_fii_dii_flow, fetch_nifty_levels
except ImportError:
    try:
        from core_data_fetcher import fetch_fii_dii_flow, fetch_nifty_levels
    except ImportError:
        def fetch_nifty_levels():
            return {}
        def fetch_fii_dii_flow(**kw):
            return {}

# Sector helper
try:
    from core.core_sectors import get_sector
except ImportError:
    try:
        from core_sectors import get_sector
    except ImportError:
        def get_sector(sym):
            return "OTHERS"


class SwingTradeOrchestrator:
    """Runs the full multi-agent pipeline and returns ``OrchestratorResult``."""

    def __init__(self, config: OrchestrationConfig = None) -> None:
        self.cfg = config or OrchestrationConfig()
        self._scanner = MarketScannerAgent(self.cfg)
        self._tech_analyst = TechnicalAnalystAgent(self.cfg)
        self._fund_screener = FundamentalScreenerAgent(self.cfg)
        self._signal_gen = SignalGeneratorAgent(self.cfg)
        self._risk_mgr = RiskManagerAgent(self.cfg)

    def run(
        self,
        tickers: Optional[List[str]] = None,
        portfolio_value: Optional[float] = None,
        max_recommendations: int = 10,
        use_chartink: bool = True,
        chartink_params: Optional[dict] = None,
    ) -> OrchestratorResult:
        """Execute the full multi-agent pipeline."""
        errors: List[str] = []
        pf_value = portfolio_value or self.cfg.portfolio_value

        logger.info("=" * 60)
        logger.info("[Orchestrator] Starting multi-agent swing trade analysis")
        logger.info("Portfolio: ₹%s | Chartink: %s", f"{pf_value:,.0f}", use_chartink)
        logger.info("=" * 60)

        # ── Market context (best-effort) ──
        market_ctx = self._fetch_market_context()

        # ── Stage 1: Market Scanner ──
        logger.info("--- Stage 1/5: Market Scanning ---")
        try:
            candidates = self._scanner.execute(
                tickers=tickers or self.cfg.tickers,
                use_chartink=use_chartink,
                chartink_params=chartink_params or self.cfg.chartink_params,
            )
        except Exception as exc:
            errors.append(f"Market Scanner failed: {exc}")
            return OrchestratorResult(
                recommendations=[], scanned_count=0,
                filtered_count=0, signal_count=0,
                market_context=market_ctx, errors=errors,
            )

        if not candidates:
            logger.info("No candidates from scanner")
            return OrchestratorResult(
                recommendations=[], scanned_count=0,
                filtered_count=0, signal_count=0,
                market_context=market_ctx,
            )

        # ── Stage 2: Technical Analysis ──
        logger.info("--- Stage 2/5: Technical Analysis ---")
        try:
            snapshots = self._tech_analyst.execute(stocks=candidates)
        except Exception as exc:
            errors.append(f"Technical Analyst failed: {exc}")
            snapshots = []

        # ── Stage 3: Fundamental Screening ──
        logger.info("--- Stage 3/5: Fundamental Screening ---")
        try:
            fund_results = self._fund_screener.execute(stocks=candidates)
        except Exception as exc:
            errors.append(f"Fundamental Screener failed: {exc}")
            fund_results = []

        # ── Stage 4: Signal Generation ──
        logger.info("--- Stage 4/5: Signal Generation ---")
        try:
            signals = self._signal_gen.execute(
                snapshots=snapshots, fundamentals=fund_results
            )
        except Exception as exc:
            errors.append(f"Signal Generator failed: {exc}")
            signals = []

        if not signals:
            logger.info("No actionable signals generated")
            return OrchestratorResult(
                recommendations=[],
                scanned_count=len(candidates),
                filtered_count=sum(1 for f in fund_results if f.passes_filter),
                signal_count=0,
                market_context=market_ctx,
                errors=errors,
            )

        # ── Stage 5: Risk Management ──
        logger.info("--- Stage 5/5: Risk Management ---")
        try:
            risk_assessments = self._risk_mgr.execute(
                signals=signals,
                stocks=candidates,
                snapshots=snapshots,
                portfolio_value=pf_value,
            )
        except Exception as exc:
            errors.append(f"Risk Manager failed: {exc}")
            risk_assessments = []

        # ── Assemble recommendations ──
        logger.info("--- Assembling final recommendations ---")
        recommendations = self._assemble(
            signals, snapshots, fund_results, risk_assessments, candidates
        )
        recommendations.sort(key=lambda r: r.confidence, reverse=True)
        recommendations = recommendations[:max_recommendations]

        logger.info("=" * 60)
        logger.info(
            "[Orchestrator] Complete — Scanned: %d | Signals: %d | Picks: %d",
            len(candidates), len(signals), len(recommendations),
        )
        logger.info("=" * 60)

        return OrchestratorResult(
            recommendations=recommendations,
            scanned_count=len(candidates),
            filtered_count=sum(1 for f in fund_results if f.passes_filter),
            signal_count=len(signals),
            market_context=market_ctx,
            timestamp=datetime.now(),
            errors=errors,
        )

    # ── Assembly ───────────────────────────────────────────────────────────

    @staticmethod
    def _assemble(signals, snapshots, fund_results, risk_assessments, candidates):
        snap_map: Dict[str, TechnicalSnapshot] = {s.symbol: s for s in snapshots}
        fund_map = {f.symbol: f for f in fund_results}
        risk_map = {r.symbol: r for r in risk_assessments}
        signal_map = {s.symbol: s for s in signals}

        recs: List[TradeRecommendation] = []
        for symbol, sig in signal_map.items():
            risk = risk_map.get(symbol)
            if risk is None:
                continue
            snap = snap_map.get(symbol)
            fund = fund_map.get(symbol)

            try:
                sector = get_sector(symbol)
            except Exception:
                sector = fund.sector if fund else "OTHERS"

            recs.append(TradeRecommendation(
                symbol=symbol,
                name=fund.name if fund else symbol,
                signal=sig.signal,
                confidence=sig.confidence,
                entry_price=risk.entry_price,
                stop_loss=risk.stop_loss,
                target_1=risk.target_1,
                target_2=risk.target_2,
                target_3=risk.target_3,
                risk_reward_ratio=risk.risk_reward_ratio,
                position_size_shares=risk.position_size_shares,
                position_value=risk.position_value,
                risk_amount=risk.risk_amount,
                sector=sector,
                trend=snap.trend if snap else TrendDirection.SIDEWAYS,
                weekly_trend=snap.weekly_trend if snap else "",
                rsi=snap.rsi if snap else 50,
                adx=snap.adx if snap else 0,
                volume_ratio=snap.volume_ratio if snap else 1.0,
                setup_type=risk.setup_type,
                base_status=snap.base_status if snap else "",
                false_breakout_risk=snap.false_breakout_risk if snap else "LOW",
                reasons=sig.reasons,
                timestamp=datetime.now(),
            ))
        return recs

    # ── Market context ─────────────────────────────────────────────────────

    @staticmethod
    def _fetch_market_context() -> Optional[dict]:
        try:
            nifty = fetch_nifty_levels()
            fii_dii = fetch_fii_dii_flow(days=5)
            return {"nifty": nifty, "fii_dii": fii_dii}
        except Exception as exc:
            logger.debug("Market context unavailable: %s", exc)
            return None
