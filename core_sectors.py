"""
Sector mapping + sector-index pulse.
Symbol → sector key  AND  sector key → yfinance index ticker.

Used by:
  - Risk filter (sector_in_uptrend) to skip stocks in weak sectors
  - /api/sectors endpoint to feed the Sector Pulse dashboard widget
  - Stock card tagging (so dashboard can group Trade Setups by sector)
"""
import logging
import time
from typing import Dict

logger = logging.getLogger(__name__)

# ── Sector index tickers (yfinance) ─────────────────────────────────────────

SECTOR_INDEX: Dict[str, str] = {
    "BANK":       "^NSEBANK",
    "IT":         "^CNXIT",
    "PHARMA":     "^CNXPHARMA",
    "AUTO":       "^CNXAUTO",
    "METAL":      "^CNXMETAL",
    "FMCG":       "^CNXFMCG",
    "ENERGY":     "^CNXENERGY",
    "FINANCE":    "^CNXFIN",
    "REALTY":     "^CNXREALTY",
    "MEDIA":      "^CNXMEDIA",
    "PSUBANK":    "^CNXPSUBANK",
    "INFRA":      "^CNXINFRA",
    "HEALTHCARE": "^CNXPHARMA",     # alias — same index, surfaced for clarity
}

# ── Symbol → Sector ─────────────────────────────────────────────────────────
# Covers Nifty 200 + commonly-screened mid/small caps. Unknown symbols
# default to "OTHERS" (not filtered, just ungrouped on the dashboard).

SECTOR_MAP: Dict[str, str] = {
    # ── BANK ──────────────────────────────────────────────────────────
    "HDFCBANK":"BANK","ICICIBANK":"BANK","SBIN":"BANK","KOTAKBANK":"BANK",
    "AXISBANK":"BANK","INDUSINDBK":"BANK","BANKBARODA":"BANK","PNB":"BANK",
    "CANBK":"BANK","FEDERALBNK":"BANK","IDFCFIRSTB":"BANK","BANDHANBNK":"BANK",
    "AUBANK":"BANK","RBLBANK":"BANK","YESBANK":"BANK","UNIONBANK":"BANK",
    "IOB":"BANK","CENTRALBK":"BANK","UCOBANK":"BANK","INDIANB":"BANK",
    "BANKINDIA":"BANK","MAHABANK":"BANK","KARURVYSYA":"BANK","CSBBANK":"BANK",

    # ── IT ────────────────────────────────────────────────────────────
    "TCS":"IT","INFY":"IT","WIPRO":"IT","HCLTECH":"IT","TECHM":"IT",
    "LTIM":"IT","PERSISTENT":"IT","MPHASIS":"IT","COFORGE":"IT","LTTS":"IT",
    "OFSS":"IT","BIRLASOFT":"IT","TATAELXSI":"IT","KPITTECH":"IT","CYIENT":"IT",
    "ZENSARTECH":"IT","INTELLECT":"IT","HAPPSTMNDS":"IT","NEWGEN":"IT",

    # ── PHARMA / HEALTHCARE ───────────────────────────────────────────
    "SUNPHARMA":"PHARMA","DRREDDY":"PHARMA","CIPLA":"PHARMA","DIVISLAB":"PHARMA",
    "AUROPHARMA":"PHARMA","LUPIN":"PHARMA","TORNTPHARM":"PHARMA","ZYDUSLIFE":"PHARMA",
    "APOLLOHOSP":"PHARMA","MAXHEALTH":"PHARMA","FORTIS":"PHARMA","GLENMARK":"PHARMA",
    "BIOCON":"PHARMA","ALKEM":"PHARMA","IPCALAB":"PHARMA","ABBOTINDIA":"PHARMA",
    "PFIZER":"PHARMA","GLAXO":"PHARMA","SANOFI":"PHARMA","NATCOPHARM":"PHARMA",
    "AJANTPHARM":"PHARMA","SAILIFE":"PHARMA","LAURUSLABS":"PHARMA","GRANULES":"PHARMA",
    "MANKIND":"PHARMA","ERIS":"PHARMA","JBCHEPHARM":"PHARMA","SUVENPHAR":"PHARMA",
    "WOCKPHARMA":"PHARMA","NEULANDLAB":"PHARMA",

    # ── AUTO ──────────────────────────────────────────────────────────
    "MARUTI":"AUTO","TATAMOTORS":"AUTO","M&M":"AUTO","BAJAJ-AUTO":"AUTO",
    "EICHERMOT":"AUTO","HEROMOTOCO":"AUTO","TVSMOTOR":"AUTO","ASHOKLEY":"AUTO",
    "MOTHERSON":"AUTO","BOSCHLTD":"AUTO","MRF":"AUTO","BALKRISIND":"AUTO",
    "EXIDEIND":"AUTO","SUNDARMFIN":"AUTO","BHARATFORG":"AUTO","ENDURANCE":"AUTO",
    "SONACOMS":"AUTO","TIINDIA":"AUTO","UNOMINDA":"AUTO","CEATLTD":"AUTO",
    "APOLLOTYRE":"AUTO","JKTYRE":"AUTO","SAMVARDHANA":"AUTO",

    # ── METAL ─────────────────────────────────────────────────────────
    "TATASTEEL":"METAL","JSWSTEEL":"METAL","HINDALCO":"METAL","JINDALSTEL":"METAL",
    "VEDL":"METAL","SAIL":"METAL","NMDC":"METAL","COALINDIA":"METAL",
    "NATIONALUM":"METAL","HINDZINC":"METAL","RATNAMANI":"METAL","APL":"METAL",
    "WELCORP":"METAL","JINDALSAW":"METAL","JSL":"METAL","MOIL":"METAL",
    "GRAVITA":"METAL",

    # ── FMCG ──────────────────────────────────────────────────────────
    "HINDUNILVR":"FMCG","ITC":"FMCG","NESTLEIND":"FMCG","BRITANNIA":"FMCG",
    "DABUR":"FMCG","GODREJCP":"FMCG","COLPAL":"FMCG","MARICO":"FMCG",
    "TATACONSUM":"FMCG","UNITDSPR":"FMCG","UBL":"FMCG","RADICO":"FMCG",
    "EMAMILTD":"FMCG","JYOTHYLAB":"FMCG","BAJAJCON":"FMCG","VBL":"FMCG",
    "HATSUN":"FMCG","BIKAJI":"FMCG","HONASA":"FMCG",

    # ── ENERGY (Oil & Gas) ────────────────────────────────────────────
    "RELIANCE":"ENERGY","ONGC":"ENERGY","IOC":"ENERGY","BPCL":"ENERGY",
    "HINDPETRO":"ENERGY","GAIL":"ENERGY","OIL":"ENERGY","PETRONET":"ENERGY",
    "GSPL":"ENERGY","MGL":"ENERGY","IGL":"ENERGY","CASTROLIND":"ENERGY",
    "CHENNPETRO":"ENERGY","ADANIGAS":"ENERGY","GUJGASLTD":"ENERGY",

    # ── FINANCE (Non-bank) ────────────────────────────────────────────
    "BAJFINANCE":"FINANCE","BAJAJFINSV":"FINANCE","SHRIRAMFIN":"FINANCE",
    "CHOLAFIN":"FINANCE","SBILIFE":"FINANCE","HDFCLIFE":"FINANCE","ICICIPRULI":"FINANCE",
    "ICICIGI":"FINANCE","LICI":"FINANCE","HDFCAMC":"FINANCE","MUTHOOTFIN":"FINANCE",
    "MFSL":"FINANCE","PFC":"FINANCE","RECLTD":"FINANCE","POWERGRID":"FINANCE",
    "IRFC":"FINANCE","PNBHOUSING":"FINANCE","CANFINHOME":"FINANCE","BAJAJHLDNG":"FINANCE",
    "JIOFIN":"FINANCE","IIFL":"FINANCE","IIFLFIN":"FINANCE","IFCI":"FINANCE",
    "SBICARD":"FINANCE","STARHEALTH":"FINANCE","NIVABUPA":"FINANCE",

    # ── REALTY ────────────────────────────────────────────────────────
    "DLF":"REALTY","LODHA":"REALTY","OBEROIRLTY":"REALTY","GODREJPROP":"REALTY",
    "PRESTIGE":"REALTY","BRIGADE":"REALTY","SOBHA":"REALTY","PHOENIXLTD":"REALTY",
    "MAHLIFE":"REALTY","SUNTECK":"REALTY","KOLTEPATIL":"REALTY",

    # ── MEDIA / TELECOM ──────────────────────────────────────────────
    "BHARTIARTL":"MEDIA","SUNTV":"MEDIA","PVRINOX":"MEDIA","ZEEL":"MEDIA",
    "NETWORK18":"MEDIA","TV18BRDCST":"MEDIA","DISHTV":"MEDIA","SAREGAMA":"MEDIA",

    # ── PSU BANK (overlap with BANK; PSU-specific) ───────────────────
    # (PSU banks already listed under BANK — PSUBANK index covers them collectively)

    # ── INFRA / CAPITAL GOODS / DEFENSE ──────────────────────────────
    "LT":"INFRA","SIEMENS":"INFRA","ABB":"INFRA","HAVELLS":"INFRA",
    "BHEL":"INFRA","CUMMINSIND":"INFRA","ABBOTINDIA":"INFRA","THERMAX":"INFRA",
    "VOLTAS":"INFRA","CGPOWER":"INFRA","KEC":"INFRA","KALPATPOWR":"INFRA",
    "GMRINFRA":"INFRA","NCC":"INFRA","IRB":"INFRA","KNRCON":"INFRA",
    "BEL":"INFRA","HAL":"INFRA","BDL":"INFRA","MAZDOCK":"INFRA",
    "COCHINSHIP":"INFRA","GRSE":"INFRA","SOLARINDS":"INFRA","ADANIENT":"INFRA",
    "ADANIPORTS":"INFRA","ADANIGREEN":"INFRA","ADANIPOWER":"INFRA","ADANIENSOL":"INFRA",
    "ATGL":"INFRA","ULTRACEMCO":"INFRA","SHREECEM":"INFRA","AMBUJACEM":"INFRA",
    "ACC":"INFRA","DALBHARAT":"INFRA","RAMCOCEM":"INFRA","JKCEMENT":"INFRA",
    "POLYCAB":"INFRA","KEI":"INFRA","FINCABLES":"INFRA","ASTRAL":"INFRA",
    "SRF":"INFRA","PIDILITIND":"INFRA","BERGEPAINT":"INFRA","ASIANPAINT":"INFRA",
    "GRASIM":"INFRA","CROMPTON":"INFRA","RVNL":"INFRA","IRCTC":"INFRA",
    "RAILTEL":"INFRA","CONCOR":"INFRA","SCI":"INFRA","TITAGARH":"INFRA",
    "JKPAPER":"INFRA","DEEPAKNTR":"INFRA","NTPC":"INFRA","TATAPOWER":"INFRA",
    "NHPC":"INFRA","SJVN":"INFRA","JSWENERGY":"INFRA","TORRENTPOWER":"INFRA",
    "CESC":"INFRA",

    # ── Consumer Durables / Retail / Discretionary ──────────────────
    "TITAN":"FMCG","TRENT":"FMCG","DMART":"FMCG","ABFRL":"FMCG",
    "PAGEIND":"FMCG","KALYANKJIL":"FMCG","NYKAA":"FMCG","ZOMATO":"FMCG",
    "JUBLFOOD":"FMCG","DEVYANI":"FMCG","WESTLIFE":"FMCG","SAPPHIRE":"FMCG",
    "BATAINDIA":"FMCG","RELAXO":"FMCG","CAMPUS":"FMCG","METROBRAND":"FMCG",
    "MAKEMYTRIP":"FMCG","IRCTC":"FMCG","INDIGO":"FMCG","INTERGLOBE":"FMCG",

    # ── Chemicals (lump into INFRA for simplicity unless we add CHEMICAL sector) ──
    "UPL":"INFRA","TATACHEM":"INFRA","AARTIIND":"INFRA","NAVINFLUOR":"INFRA",
    "PIIND":"INFRA","COROMANDEL":"INFRA","CHAMBLFERT":"INFRA","GNFC":"INFRA",
    "GSFC":"INFRA","RCF":"INFRA","NFL":"INFRA","FACT":"INFRA",
}


# ── Sector pulse (live index data) ──────────────────────────────────────────

_PULSE_CACHE: dict = {"ts": 0, "data": {}}
_PULSE_TTL_SEC = 300   # 5 minutes


def get_sector(symbol: str) -> str:
    """Return sector key for a symbol, or 'OTHERS' if unmapped."""
    return SECTOR_MAP.get(symbol.upper(), "OTHERS")


def fetch_sector_pulse() -> Dict[str, dict]:
    """
    Return {sector_key: {level, change_pct, above_ema20, trend}} for all sectors.
    Cached for 5 minutes to avoid hammering yfinance.
    """
    now = time.time()
    if _PULSE_CACHE["data"] and (now - _PULSE_CACHE["ts"]) < _PULSE_TTL_SEC:
        return _PULSE_CACHE["data"]

    import yfinance as yf
    result: Dict[str, dict] = {}
    for sector, ticker in SECTOR_INDEX.items():
        try:
            df = yf.Ticker(ticker).history(period="60d")
            df = df.dropna(subset=["Close"])
            if df.empty or len(df) < 21:
                continue
            close   = df["Close"]
            level   = float(close.iloc[-1])
            prev    = float(close.iloc[-2])
            change  = round((level - prev) / prev * 100, 2) if prev else 0.0
            ema20   = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
            above   = level > ema20
            trend   = "STRONG" if above and change >= 0 else "WEAK" if not above else "NEUTRAL"
            result[sector] = {
                "level":       round(level, 2),
                "change_pct":  change,
                "above_ema20": above,
                "trend":       trend,
                "ema20":       round(ema20, 2),
            }
        except Exception as exc:
            logger.warning("[sectors] %s (%s) failed: %s", sector, ticker, exc)

    _PULSE_CACHE["ts"]   = now
    _PULSE_CACHE["data"] = result
    return result


def is_sector_in_uptrend(symbol: str, pulse: Dict[str, dict] = None) -> bool:
    """Return True if the stock's sector index is currently above its EMA20."""
    sector = get_sector(symbol)
    if sector == "OTHERS":
        return True   # don't penalize unmapped stocks
    if pulse is None:
        pulse = fetch_sector_pulse()
    info = pulse.get(sector)
    if not info:
        return True   # data fetch failed — fail-open
    return bool(info.get("above_ema20"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("Fetching sector pulse...")
    pulse = fetch_sector_pulse()
    for sector, info in sorted(pulse.items(), key=lambda x: -x[1]["change_pct"]):
        arrow = "↑" if info["above_ema20"] else "↓"
        print(f"  {sector:12s}  {arrow}  {info['level']:>10.2f}  {info['change_pct']:+6.2f}%  ({info['trend']})")
