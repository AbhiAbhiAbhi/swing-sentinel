"""
Market Scanner Agent
Scans the stock universe using Chartink (preferred) or manual yfinance scan.
Returns raw technical dicts for downstream agents.
"""

import logging
from typing import Any, Dict, List

from core.agents.base_agent import BaseAgent
from core.agents.config import OrchestrationConfig

logger = logging.getLogger(__name__)

# Import existing modules — handles both flat and package layouts
try:
    from core.core_chartink_fetcher import fetch_chartink_stocks
    from core.core_data_fetcher import fetch_stock_technicals
except ImportError:
    from core_chartink_fetcher import fetch_chartink_stocks
    from core_data_fetcher import fetch_stock_technicals


class MarketScannerAgent(BaseAgent):
    """Stage 1 — scan the market and return candidate stock dicts.

    Two modes:
      * **Chartink** (default) — calls Chartink screener which pre-filters by
        RSI, MACD, EMA alignment, ADX, volume.  Then enriches each match with
        full yfinance technicals for the downstream pipeline.
      * **Manual** — iterates over ``config.tickers`` and pulls yfinance data
        directly.  Useful when Chartink is unavailable or for custom lists.
    """

    def __init__(self, config: OrchestrationConfig = None) -> None:
        super().__init__("MarketScanner")
        self.cfg = config or OrchestrationConfig()

    def execute(self, **kwargs: Any) -> List[Dict]:
        """Return a list of ``fetch_stock_technicals`` dicts."""
        tickers: List[str] = kwargs.get("tickers", self.cfg.tickers)
        use_chartink: bool = kwargs.get("use_chartink", self.cfg.use_chartink)
        chartink_params: dict = kwargs.get("chartink_params", self.cfg.chartink_params)

        if use_chartink:
            return self._scan_chartink(chartink_params)
        return self._scan_manual(tickers)

    # ── Chartink path ──────────────────────────────────────────────────────

    def _scan_chartink(self, params: dict) -> List[Dict]:
        self.log(f"Running Chartink screener scan (universe={params.get('universe', 'cash')})")
        try:
            matched = fetch_chartink_stocks(params)
        except Exception as exc:
            self.log(f"Chartink scan failed ({exc}), falling back to manual scan")
            return self._scan_manual(self.cfg.tickers)

        if not matched:
            self.log("Chartink returned 0 matches — trying manual scan")
            return self._scan_manual(self.cfg.tickers)

        self.log(f"Chartink matched {len(matched)} stocks — enriching with yfinance")
        results: List[Dict] = []
        for stock in matched:
            symbol = stock.get("symbol", "")
            if not symbol:
                continue
            tech = fetch_stock_technicals(symbol)
            if tech:
                results.append(tech)

        self.log(f"Enriched {len(results)}/{len(matched)} stocks with full technicals")
        return results

    # ── Manual path ────────────────────────────────────────────────────────

    def _scan_manual(self, tickers: List[str]) -> List[Dict]:
        self.log(f"Manual scan: {len(tickers)} tickers via yfinance")
        results: List[Dict] = []
        for symbol in tickers:
            try:
                tech = fetch_stock_technicals(symbol)
                if not tech:
                    continue
                price = tech.get("price", 0)
                avg_vol = tech.get("avg_volume_20d", 0)
                if price < self.cfg.min_price or price > self.cfg.max_price:
                    continue
                if avg_vol < self.cfg.min_avg_volume:
                    continue
                results.append(tech)
            except Exception:
                logger.debug("Skipping %s — fetch error", symbol)

        self.log(f"Manual scan produced {len(results)} candidates")
        return results
