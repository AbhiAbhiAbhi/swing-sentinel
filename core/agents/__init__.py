"""
Multi-Agent Orchestration for Indian Stock Swing Trading.

Agents:
  MarketScannerAgent     – filters the stock universe via Chartink + yfinance
  TechnicalAnalystAgent  – deep technical analysis & setup scoring
  FundamentalScreenerAgent – market-cap / PE / sector health checks
  SignalGeneratorAgent   – combines scores into BUY/SELL signals
  RiskManagerAgent       – ATR-based stops, targets, position sizing

Orchestrator:
  SwingTradeOrchestrator – coordinates the 5-stage pipeline
"""
