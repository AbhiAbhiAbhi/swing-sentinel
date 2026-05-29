# Institutional Consensus Scanner — Backend Service

A small, framework-agnostic API your existing web application calls over HTTP.
It scores any stock's institutional consensus (ACCUMULATION / NEUTRAL /
DISTRIBUTION) and serves free daily FII/DII flow + bulk-deal data.

## What's automated vs. what isn't (read this first)

| Piece | Automation status |
|-------|------------------|
| **Scoring engine** | 100% automatic, deterministic, zero external deps. Never breaks. |
| **Daily FII/DII flows** | Auto-fetched free from NSE (best-effort; rate-limited). |
| **Bulk / block deals** | Auto-fetched free from NSE (best-effort). |
| **Quarterly shareholding** (FII%/DII%/promoter%/investor counts) | **No reliable free API in India.** Options: (a) wire a paid provider, or (b) update a small DB table once a quarter and POST it to `/score`. |

The honest takeaway: full hands-off automation of the *quarterly* half needs a
paid data vendor (Trendlyne API, Tijori, etc.). The free tier auto-fetches the
fast feeds and auto-scores; you supply 5 numbers per stock once a quarter.

## Files
- `scoring.py` — the scoring engine (pure logic; mirrors the Excel workbook)
- `data_sources.py` — NSE adapters + paid-provider stub
- `api.py` — FastAPI app (the HTTP service)
- `requirements.txt` — dependencies

## Setup
```bash
pip install -r requirements.txt
uvicorn api:app --reload --port 8000
```
Open http://localhost:8000/docs for interactive API documentation.

## Endpoints
- `POST /score` — the reliable core. Send 3 quarters, get the full scored result. Always works.
- `GET /flows/daily` — market-wide daily FII/DII net cash (free).
- `GET /deals/{symbol}` — recent bulk deals for a symbol (free).
- `GET /scan/{symbol}` — convenience auto-scan; returns `needs_quarterly_data` until a paid provider is wired.
- `GET /health` — liveness check.

## Calling it from your web app (any framework)
```javascript
const res = await fetch("http://localhost:8000/score", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    symbol: "HINDALCO",
    q2: { fii_pct:28.20, dii_pct:23.17, promoter_pct:34.64, fii_investor_count:1130, mf_scheme_count:43 },
    q1: { fii_pct:28.20, dii_pct:23.17, promoter_pct:34.64, fii_investor_count:1145, mf_scheme_count:44 },
    q0: { fii_pct:30.00, dii_pct:21.35, promoter_pct:34.64, fii_investor_count:1160, mf_scheme_count:46 },
    weekly_fii_dii_signal: "neutral",
    bulk_deal_signal: "neutral",
    monthly_mf_signal: "neutral"
  })
});
const data = await res.json();
console.log(data.quarterly_classification, data.final_call);
```

## Going fully hands-off (the quarterly half)
1. Subscribe to a data vendor that exposes shareholding-pattern history.
2. Implement `fetch_shareholding_from_paid_provider()` in `data_sources.py`
   to return the last 3 quarters in the documented shape.
3. Wire it into the `/scan/{symbol}` endpoint where the stub raises `501`.
Then `/scan/{symbol}` becomes one-call, fully automatic per stock.

## Tuning
All thresholds live as class attributes in `scoring.ConsensusScorer`
(`NET_STRONG`, `DEAD_BAND`, `ACC_CUTOFF`, `DIST_CUTOFF`). Change once, applies everywhere.

## Scheduling the weekly refresh
Run a cron job hitting `/flows/daily` and `/deals/{symbol}` each evening and
store results; recompute `/score` overlays weekly. Example crontab:
```
0 20 * * 1-5  curl -s http://localhost:8000/flows/daily >> ~/flows.log
```

## Disclaimer
Personal screening framework, not financial advice. Free NSE endpoints are
unofficial and may change or rate-limit; build in retries and a manual fallback.
