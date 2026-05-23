"""
Technical Analyst Agent
Scores each stock's swing-trade setup quality based on multiple technical
indicators.  Builds a TechnicalSnapshot and a setup score in [-1, +1].

Reuses the raw dict returned by core_data_fetcher.fetch_stock_technicals().
"""

from typing import Any, Dict, List, Tuple

from core.agents.base_agent import BaseAgent
from core.agents.config import OrchestrationConfig
from core.agents.models import TechnicalSnapshot, TrendDirection


class TechnicalAnalystAgent(BaseAgent):
    """Stage 2 — analyse each candidate and score the setup."""

    def __init__(self, config: OrchestrationConfig = None) -> None:
        super().__init__("TechnicalAnalyst")
        self.cfg = config or OrchestrationConfig()

    def execute(self, **kwargs: Any) -> List[TechnicalSnapshot]:
        stocks: List[Dict] = kwargs.get("stocks", [])
        self.log(f"Analysing {len(stocks)} stocks")

        snapshots: List[TechnicalSnapshot] = []
        for tech in stocks:
            snap = self._build_snapshot(tech)
            if snap is not None:
                snapshots.append(snap)

        self.log(f"Produced {len(snapshots)} technical snapshots")
        return snapshots

    # ── Snapshot builder ───────────────────────────────────────────────────

    def _build_snapshot(self, tech: Dict) -> TechnicalSnapshot:
        price = tech.get("price", 0)
        ema9 = tech.get("ema9", 0)
        ema21 = tech.get("ema21", 0)
        ema50 = tech.get("ema50", 0)
        ema200 = tech.get("ema200", 0)

        above_200 = price > ema200 if ema200 else False
        trend = self._classify_trend(price, ema9, ema21, ema50, ema200)

        return TechnicalSnapshot(
            symbol=tech.get("symbol", ""),
            price=price,
            change_pct=tech.get("change_pct", 0),
            ema9=ema9,
            ema21=ema21,
            ema50=ema50,
            ema200=ema200,
            rsi=tech.get("rsi", 50),
            macd=tech.get("macd", 0),
            macd_signal=tech.get("macd_signal", 0),
            macd_histogram=tech.get("macd_histogram", 0),
            atr=tech.get("atr", 0),
            atr_pct=tech.get("atr_pct", 0),
            volume=tech.get("volume", 0),
            avg_volume=tech.get("avg_volume_20d", 0),
            volume_ratio=tech.get("volume_ratio", 1.0),
            support_1=tech.get("support_1", 0),
            resistance_1=tech.get("resistance_1", 0),
            resistance_2=tech.get("resistance_2", 0),
            high_52w=tech.get("high_52w", 0),
            low_52w=tech.get("low_52w", 0),
            trend=trend,
            above_200_ema=above_200,
            weekly_trend=tech.get("weekly_trend", "BULLISH"),
            base_status=tech.get("base_status", "VOLATILE"),
            false_breakout_risk=tech.get("false_breakout_risk", "LOW"),
            ema9_cross_ema21=tech.get("ema9_cross_ema21", "none"),
            ema9_cross_days_ago=tech.get("ema9_cross_days_ago", -1),
            rsi_pullback_zone=tech.get("rsi_pullback_zone", False),
            adx=tech.get("adx", 0) if "adx" in tech else 0,
        )

    # ── Setup scorer ───────────────────────────────────────────────────────

    def score_setup(self, snap: TechnicalSnapshot) -> Tuple[float, List[str]]:
        """Return (score, reasons) with score in [-1.0, +1.0]."""
        score = 0.0
        reasons: List[str] = []

        score, reasons = self._score_trend(snap, score, reasons)
        score, reasons = self._score_rsi(snap, score, reasons)
        score, reasons = self._score_macd(snap, score, reasons)
        score, reasons = self._score_volume(snap, score, reasons)
        score, reasons = self._score_weekly_trend(snap, score, reasons)
        score, reasons = self._score_base(snap, score, reasons)
        score, reasons = self._score_false_breakout(snap, score, reasons)
        score, reasons = self._score_ema_cross(snap, score, reasons)
        score, reasons = self._score_rsi_pullback(snap, score, reasons)

        return max(-1.0, min(1.0, score)), reasons

    # ── Individual scorers ─────────────────────────────────────────────────

    def _score_trend(self, s: TechnicalSnapshot, sc: float, r: List[str]) -> Tuple[float, List[str]]:
        if s.ema9 > s.ema21 > s.ema50:
            sc += 0.18
            r.append("Bullish EMA alignment (9 > 21 > 50)")
        elif s.ema9 < s.ema21 < s.ema50:
            sc -= 0.15
            r.append("Bearish EMA alignment")
        if s.above_200_ema:
            sc += 0.10
            r.append("Price above 200 EMA — long-term uptrend intact")
        else:
            sc -= 0.10
            r.append("Price below 200 EMA")
        return sc, r

    def _score_rsi(self, s: TechnicalSnapshot, sc: float, r: List[str]) -> Tuple[float, List[str]]:
        if s.rsi < self.cfg.rsi_oversold:
            sc += 0.12
            r.append(f"RSI oversold ({s.rsi:.1f}) — reversal potential")
        elif self.cfg.rsi_oversold <= s.rsi <= 50:
            sc += 0.05
            r.append(f"RSI in recovery zone ({s.rsi:.1f})")
        elif 50 < s.rsi < self.cfg.rsi_overbought:
            sc += 0.05
            r.append(f"RSI bullish ({s.rsi:.1f})")
        elif s.rsi >= self.cfg.rsi_overbought:
            sc -= 0.12
            r.append(f"RSI overbought ({s.rsi:.1f}) — pullback likely")
        return sc, r

    @staticmethod
    def _score_macd(s: TechnicalSnapshot, sc: float, r: List[str]) -> Tuple[float, List[str]]:
        if s.macd_histogram > 0 and s.macd > s.macd_signal:
            sc += 0.12
            r.append("MACD bullish — histogram positive, line above signal")
        elif s.macd_histogram > 0:
            sc += 0.04
            r.append("MACD histogram positive")
        elif s.macd_histogram < 0 and s.macd < s.macd_signal:
            sc -= 0.10
            r.append("MACD bearish crossover")
        return sc, r

    @staticmethod
    def _score_volume(s: TechnicalSnapshot, sc: float, r: List[str]) -> Tuple[float, List[str]]:
        if s.volume_ratio > 2.0:
            sc += 0.10
            r.append(f"Strong volume surge ({s.volume_ratio:.1f}x avg)")
        elif s.volume_ratio > 1.5:
            sc += 0.05
            r.append(f"Above-average volume ({s.volume_ratio:.1f}x)")
        elif s.volume_ratio < 0.5:
            sc -= 0.05
            r.append("Low volume — weak conviction")
        return sc, r

    @staticmethod
    def _score_weekly_trend(s: TechnicalSnapshot, sc: float, r: List[str]) -> Tuple[float, List[str]]:
        if s.weekly_trend == "BULLISH":
            sc += 0.08
            r.append("Weekly trend BULLISH — higher timeframe support")
        else:
            sc -= 0.08
            r.append("Weekly trend BEARISH — headwind from higher TF")
        return sc, r

    @staticmethod
    def _score_base(s: TechnicalSnapshot, sc: float, r: List[str]) -> Tuple[float, List[str]]:
        if s.base_status == "STABLE_BASE":
            sc += 0.08
            r.append("Stable base (20+ days) — breakout potential")
        elif s.base_status == "CONSOLIDATING":
            sc += 0.03
            r.append("Consolidating (5-20 days)")
        return sc, r

    @staticmethod
    def _score_false_breakout(s: TechnicalSnapshot, sc: float, r: List[str]) -> Tuple[float, List[str]]:
        if s.false_breakout_risk == "HIGH":
            sc -= 0.12
            r.append("High false-breakout risk — rejection wick or dry volume")
        return sc, r

    @staticmethod
    def _score_ema_cross(s: TechnicalSnapshot, sc: float, r: List[str]) -> Tuple[float, List[str]]:
        if s.ema9_cross_ema21 == "golden" and s.ema9_cross_days_ago <= 3:
            sc += 0.10
            r.append(f"Recent golden cross (EMA 9/21, {s.ema9_cross_days_ago}d ago)")
        elif s.ema9_cross_ema21 == "death" and s.ema9_cross_days_ago <= 3:
            sc -= 0.08
            r.append(f"Recent death cross (EMA 9/21, {s.ema9_cross_days_ago}d ago)")
        return sc, r

    @staticmethod
    def _score_rsi_pullback(s: TechnicalSnapshot, sc: float, r: List[str]) -> Tuple[float, List[str]]:
        if s.rsi_pullback_zone:
            sc += 0.08
            r.append("RSI pullback zone (40-55) in uptrend — continuation setup")
        return sc, r

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _classify_trend(price: float, ema9: float, ema21: float,
                        ema50: float, ema200: float) -> TrendDirection:
        if not all([ema9, ema21, ema50, ema200]):
            return TrendDirection.SIDEWAYS
        bullish = sum([price > ema9, ema9 > ema21, ema21 > ema50, price > ema200])
        bearish = sum([price < ema9, ema9 < ema21, ema21 < ema50, price < ema200])
        if bullish >= 3:
            return TrendDirection.UPTREND
        if bearish >= 3:
            return TrendDirection.DOWNTREND
        return TrendDirection.SIDEWAYS
