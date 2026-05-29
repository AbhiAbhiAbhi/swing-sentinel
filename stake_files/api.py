"""
Consensus Scanner API
---------------------
A small FastAPI service your existing web app calls over HTTP.
Framework-agnostic: your frontend (React/Vue/PHP/Node/anything) just makes
a fetch() to these endpoints.

Run locally:   uvicorn api:app --reload --port 8000
Then from your web app:  GET http://localhost:8000/scan/HINDALCO
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from scoring import QuarterData, ConsensusScorer, apply_weekly_overlay
import data_sources as ds

app = FastAPI(title="Institutional Consensus Scanner", version="1.0")

# Allow your web app's origin to call this. Restrict in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # <-- set to your web app domain in prod
    allow_methods=["*"],
    allow_headers=["*"],
)

scorer = ConsensusScorer()


# ----- Request/response models -----
class QuarterIn(BaseModel):
    fii_pct: float
    dii_pct: float
    promoter_pct: float
    fii_investor_count: Optional[int] = None
    mf_scheme_count: Optional[int] = None


class ScoreRequest(BaseModel):
    symbol: str
    q2: QuarterIn   # oldest
    q1: QuarterIn   # previous
    q0: QuarterIn   # latest
    pledge_rising: bool = False
    # optional manual overlay signals: 'confirms' | 'contradicts' | 'neutral'
    weekly_fii_dii_signal: str = "neutral"
    bulk_deal_signal: str = "neutral"
    monthly_mf_signal: str = "neutral"


# ----- Endpoints -----
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/score")
def score(req: ScoreRequest):
    """
    Fully deterministic scoring from supplied quarterly data.
    This endpoint ALWAYS works (no external dependency) — use it as the
    reliable core. Feed it data from your DB, paid API, or the auto-fetch route.
    """
    def conv(q: QuarterIn) -> QuarterData:
        return QuarterData(**q.dict())

    result = scorer.score(conv(req.q2), conv(req.q1), conv(req.q0),
                          pledge_rising=req.pledge_rising)
    overlay = apply_weekly_overlay(
        result.classification,
        req.weekly_fii_dii_signal,
        req.bulk_deal_signal,
        req.monthly_mf_signal,
    )
    return {
        "symbol": req.symbol.upper(),
        "quarterly_classification": result.classification,
        "final_score": result.final_score,
        "raw_score": result.raw_score,
        "persistence": result.persistence,
        "changes": {
            "fii": result.fii_change,
            "dii": result.dii_change,
            "net": result.net_change,
            "promoter": result.promoter_change,
        },
        "components": result.components,
        "breakdown": result.breakdown,
        "final_call": overlay["final_call"],
        "overlay_note": overlay["note"],
    }


@app.get("/flows/daily")
def daily_flows():
    """Market-wide daily FII/DII net cash (free NSE feed, best-effort)."""
    try:
        return {"data": ds.fetch_fii_dii_daily()}
    except ds.DataSourceError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/deals/{symbol}")
def bulk_deals(symbol: str):
    """Recent bulk deals for a symbol (free NSE feed, best-effort)."""
    return {"symbol": symbol.upper(), "deals": ds.fetch_bulk_deals(symbol)}


@app.get("/scan/{symbol}")
def scan(symbol: str):
    """
    Convenience auto-scan. Tries to fetch quarterly data automatically.
    If the free quarterly fetch can't supply clean data (the common case),
    returns status 'needs_quarterly_data' so your web app can prompt for it
    or pull from your DB / paid provider.
    """
    sh = ds.fetch_quarterly_shareholding(symbol)
    if not sh or "_raw" in sh:
        return {
            "symbol": symbol.upper(),
            "status": "needs_quarterly_data",
            "message": ("Free quarterly feed unavailable/unparsed. POST the 3 "
                        "quarters to /score, or wire a paid provider in "
                        "data_sources.fetch_shareholding_from_paid_provider()."),
            "free_data_available": {
                "daily_flows": "/flows/daily",
                "bulk_deals": f"/deals/{symbol.upper()}",
            },
        }
    # If a paid provider is wired and returns clean quarters, score here.
    raise HTTPException(status_code=501, detail="Wire paid provider to auto-score.")
