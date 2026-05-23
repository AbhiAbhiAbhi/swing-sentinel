"""Data models for the multi-agent orchestration pipeline."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class Signal(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class TrendDirection(str, Enum):
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    SIDEWAYS = "SIDEWAYS"


@dataclass
class TechnicalSnapshot:
    """Technical indicator values at a point in time."""

    symbol: str
    price: float = 0.0
    change_pct: float = 0.0

    # EMAs
    ema9: float = 0.0
    ema21: float = 0.0
    ema50: float = 0.0
    ema200: float = 0.0

    # RSI / MACD
    rsi: float = 50.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0

    # Volatility
    atr: float = 0.0
    atr_pct: float = 0.0

    # Volume
    volume: int = 0
    avg_volume: int = 0
    volume_ratio: float = 1.0

    # Levels
    support_1: float = 0.0
    resistance_1: float = 0.0
    resistance_2: float = 0.0
    high_52w: float = 0.0
    low_52w: float = 0.0

    # Derived
    trend: TrendDirection = TrendDirection.SIDEWAYS
    above_200_ema: bool = False
    weekly_trend: str = "BULLISH"
    base_status: str = "VOLATILE"
    false_breakout_risk: str = "LOW"

    # EMA cross
    ema9_cross_ema21: str = "none"
    ema9_cross_days_ago: int = -1

    # RSI pullback
    rsi_pullback_zone: bool = False

    # ADX
    adx: float = 0.0


@dataclass
class FundamentalSnapshot:
    """Fundamental data for a stock."""

    symbol: str
    name: str = ""
    market_cap_cr: float = 0.0
    pe_ratio: float = 0.0
    sector: str = "Unknown"
    passes_filter: bool = True
    rejection_reasons: List[str] = field(default_factory=list)


@dataclass
class TradeSignal:
    """A buy/sell signal emitted by the Signal Generator."""

    symbol: str
    signal: Signal
    confidence: float  # 0.0 to 1.0
    technical_score: float  # -1.0 to +1.0
    reasons: List[str] = field(default_factory=list)


@dataclass
class RiskAssessment:
    """Risk/reward profile for a single trade."""

    symbol: str
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    target_3: float
    risk_reward_ratio: float
    position_size_shares: int
    position_value: float
    risk_amount: float
    risk_pct_of_portfolio: float
    atr: float
    setup_type: str = ""


@dataclass
class TradeRecommendation:
    """Final recommendation combining all agent outputs."""

    symbol: str
    name: str
    signal: Signal
    confidence: float
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    target_3: float
    risk_reward_ratio: float
    position_size_shares: int
    position_value: float
    risk_amount: float

    sector: str = "Unknown"
    trend: TrendDirection = TrendDirection.SIDEWAYS
    weekly_trend: str = ""
    rsi: float = 50.0
    adx: float = 0.0
    volume_ratio: float = 1.0
    setup_type: str = ""
    base_status: str = ""
    false_breakout_risk: str = "LOW"
    reasons: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """Serialise for JSON API response."""
        return {
            "symbol": self.symbol,
            "name": self.name,
            "signal": self.signal.value,
            "confidence": round(self.confidence, 2),
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "target_1": self.target_1,
            "target_2": self.target_2,
            "target_3": self.target_3,
            "risk_reward_ratio": self.risk_reward_ratio,
            "position_size_shares": self.position_size_shares,
            "position_value": round(self.position_value, 2),
            "risk_amount": round(self.risk_amount, 2),
            "sector": self.sector,
            "trend": self.trend.value,
            "weekly_trend": self.weekly_trend,
            "rsi": round(self.rsi, 1),
            "adx": round(self.adx, 1),
            "volume_ratio": round(self.volume_ratio, 2),
            "setup_type": self.setup_type,
            "base_status": self.base_status,
            "false_breakout_risk": self.false_breakout_risk,
            "reasons": self.reasons,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class OrchestratorResult:
    """Aggregated output of the full pipeline."""

    recommendations: List[TradeRecommendation]
    scanned_count: int
    filtered_count: int
    signal_count: int
    market_context: Optional[dict] = None
    timestamp: datetime = field(default_factory=datetime.now)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "recommendations": [r.to_dict() for r in self.recommendations],
            "scanned_count": self.scanned_count,
            "filtered_count": self.filtered_count,
            "signal_count": self.signal_count,
            "market_context": self.market_context,
            "timestamp": self.timestamp.isoformat(),
            "errors": self.errors,
        }
