"""Configuration for the multi-agent orchestration system."""

from dataclasses import dataclass, field
from typing import List


# Default universe — same tickers that already ship in core_data_fetcher.NSE_TICKERS
DEFAULT_TICKERS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS", "WIPRO",
    "MARUTI", "TATAMOTORS", "LT", "BHARTIARTL", "BEL", "TATAPOWER",
    "POWERGRID", "KOTAKBANK", "AXISBANK", "BAJFINANCE", "HINDUNILVR",
    "ITC", "NESTLEIND", "SUNPHARMA",
    # Nifty Next 50 / popular mid-caps
    "HCLTECH", "TECHM", "TITAN", "BAJAJ-AUTO", "HEROMOTOCO",
    "CIPLA", "DRREDDY", "DIVISLAB", "TATASTEEL", "JSWSTEEL",
    "HINDALCO", "NTPC", "ONGC", "BPCL", "GAIL", "COALINDIA",
    "ADANIENT", "ADANIPORTS", "M&M", "EICHERMOT",
    "DLF", "HAL", "IRCTC", "VEDL", "SAIL", "TRENT",
    "ABB", "SIEMENS", "HAVELLS", "PIDILITIND",
]


@dataclass
class OrchestrationConfig:
    """Parameters for the multi-agent swing trade pipeline."""

    # ── Universe ──
    tickers: List[str] = field(default_factory=lambda: list(DEFAULT_TICKERS))
    use_chartink: bool = True  # prefer Chartink scan over manual ticker list

    # ── Technical thresholds ──
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    adx_trending: float = 25.0
    ema_short: int = 9
    ema_medium: int = 21
    ema_long: int = 50
    ema_trend: int = 200
    volume_surge_multiplier: float = 1.5

    # ── Fundamental thresholds ──
    min_market_cap_cr: float = 5000.0
    max_pe_ratio: float = 60.0

    # ── Risk management ──
    portfolio_value: float = 1_000_000.0   # ₹10 lakh default
    max_risk_per_trade_pct: float = 2.0
    max_positions: int = 10
    min_risk_reward: float = 2.0
    sl_atr_multiplier: float = 1.5
    target_atr_multiplier: float = 3.0

    # ── Scanner ──
    min_price: float = 50.0
    max_price: float = 50000.0
    min_avg_volume: int = 100_000

    # ── Chartink scan params (forwarded to fetch_chartink_stocks) ──
    chartink_params: dict = field(default_factory=lambda: {
        "universe": "cash",
        "min_price": 50,
        "rsi_min": 40,
        "rsi_max": 70,
        "adx_min": 20,
        "min_volume_lakh": 5,
        "require_macd": True,
        "require_ema_alignment": True,
        "require_ema200": True,
    })
