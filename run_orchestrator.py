#!/usr/bin/env python3
"""
CLI entry point for the Multi-Agent Swing Trade Orchestrator.

Usage:
  python run_orchestrator.py                         # Chartink scan, Nifty universe
  python run_orchestrator.py --tickers RELIANCE TCS  # Specific stocks (yfinance)
  python run_orchestrator.py --portfolio 500000      # Custom portfolio size
  python run_orchestrator.py --no-chartink           # Skip Chartink, use yfinance only
  python run_orchestrator.py -v                      # Verbose logging
"""

import argparse
import logging
import sys
import os

# Ensure project root is on sys.path so "core.*" imports resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.agents.config import OrchestrationConfig
from core.agents.display import display_results
from core.agents.orchestrator import SwingTradeOrchestrator


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="run_orchestrator",
        description="Multi-agent orchestration for Indian stock swing trading",
    )
    parser.add_argument(
        "--tickers", nargs="+",
        help="Specific NSE symbols to analyse (e.g. RELIANCE TCS INFY)",
    )
    parser.add_argument(
        "--portfolio", type=float, default=1_000_000,
        help="Portfolio value in INR (default: 10,00,000)",
    )
    parser.add_argument(
        "--max-picks", type=int, default=10,
        help="Maximum number of recommendations (default: 10)",
    )
    parser.add_argument(
        "--risk-per-trade", type=float, default=2.0,
        help="Max risk per trade as %% of portfolio (default: 2.0)",
    )
    parser.add_argument(
        "--no-chartink", action="store_true",
        help="Skip Chartink screener — use yfinance-only manual scan",
    )
    parser.add_argument(
        "--universe", choices=["cash", "nifty50", "nifty100", "nifty200", "nifty500", "fnolist"],
        default="cash",
        help="Chartink scan universe (default: cash)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose/debug logging",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(name)-30s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )

    config = OrchestrationConfig(
        portfolio_value=args.portfolio,
        max_risk_per_trade_pct=args.risk_per_trade,
    )
    config.chartink_params["universe"] = args.universe

    use_chartink = not args.no_chartink
    tickers = args.tickers if args.tickers else None

    # If user supplies specific tickers, force manual scan
    if tickers:
        use_chartink = False

    orchestrator = SwingTradeOrchestrator(config)
    result = orchestrator.run(
        tickers=tickers,
        portfolio_value=args.portfolio,
        max_recommendations=args.max_picks,
        use_chartink=use_chartink,
        chartink_params=config.chartink_params,
    )

    display_results(result)
    return result


if __name__ == "__main__":
    main()
