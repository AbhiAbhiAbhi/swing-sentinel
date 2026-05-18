"""
News pipeline — RSS aggregation + FinBERT sentiment + symbol/sector mapping.

Pulls headlines from configurable Indian-market RSS feeds, scores each headline
with FinBERT (finance-tuned BERT), and groups results into:
  - overall  : market-wide sentiment + headlines
  - sectors  : per-sector headlines (BANK, IT, PHARMA, ...)
  - stocks   : per-symbol headlines (matched against SECTOR_MAP universe + watchlist)

All knobs (feed URLs, thresholds, time window, per-stock/sector enable lists)
are read from data/news_config.json which is created on first run with sensible
defaults and is fully editable from the dashboard filter panel.

Caches:
  - In-memory aggregate (TTL from config.refresh_minutes)
  - Disk cache of sentiment scores keyed by (model, headline-hash) to avoid
    re-running FinBERT on the same headline across restarts
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Config / cache paths ─────────────────────────────────────────────────────
CONFIG_PATH      = os.path.join("data", "news_config.json")
SENT_CACHE_PATH  = os.path.join("data", "news_sentiment_cache.json")
POSITIONS_CSV    = os.path.join("data", "positions.csv")

# ── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_FEEDS: List[Dict[str, Any]] = [
    {"name": "Moneycontrol — Markets",        "url": "https://www.moneycontrol.com/rss/marketreports.xml",  "enabled": True},
    {"name": "Moneycontrol — Business",       "url": "https://www.moneycontrol.com/rss/business.xml",       "enabled": True},
    {"name": "Moneycontrol — Latest News",    "url": "https://www.moneycontrol.com/rss/latestnews.xml",     "enabled": True},
    {"name": "Economic Times — Markets",      "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "enabled": True},
    {"name": "Economic Times — Stocks",       "url": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms", "enabled": True},
    {"name": "Business Standard — Markets",   "url": "https://www.business-standard.com/rss/markets-106.rss", "enabled": True},
    {"name": "Mint — Markets",                "url": "https://www.livemint.com/rss/markets",                "enabled": False},
    {"name": "Reuters India — Business",      "url": "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best", "enabled": False},
]

DEFAULT_CONFIG: Dict[str, Any] = {
    "feeds":               DEFAULT_FEEDS,
    "time_window_hours":   24,         # only consider headlines newer than this
    "refresh_minutes":     15,         # aggregate cache TTL
    "max_headlines":       150,        # cap per refresh (FinBERT runtime guard)
    "positive_threshold":  0.55,       # finbert positive score
    "negative_threshold":  0.55,       # finbert negative score
    "enabled_sectors":     [],         # [] = all sectors
    "enabled_stocks":      [],         # [] = use full universe (positions + SECTOR_MAP)
    "use_watchlist_only":  False,      # if true, only look up positions.csv symbols
    "model":               "ProsusAI/finbert",
}

# Light keyword sentiment fallback (only used if FinBERT import fails)
_POS_WORDS = {
    "surge","jump","rally","gain","gains","rise","rises","record","high","up",
    "beat","beats","upgrade","upgraded","outperform","strong","robust","boost",
    "boosts","wins","win","approves","approved","positive","profit","profits",
    "growth","expansion","launch","launches","bullish","buy","accumulate",
}
_NEG_WORDS = {
    "fall","falls","plunge","plunges","drop","drops","crash","crashes","slump",
    "down","loss","losses","miss","misses","downgrade","downgraded","weak",
    "warns","warning","cut","cuts","decline","declines","probe","fraud","fine",
    "penalty","bearish","sell","exit","sells","ban","banned","rejected","strike",
}


# ── Aggregate cache ──────────────────────────────────────────────────────────
_AGG_LOCK    = threading.Lock()
_AGG_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None, "config_hash": ""}


# ─────────────────────────────────────────────────────────────────────────────
# Config persistence
# ─────────────────────────────────────────────────────────────────────────────
def load_config() -> Dict[str, Any]:
    """Load news_config.json, creating it with defaults if missing."""
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))   # deep copy
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as exc:
        logger.warning("[news] config read failed (%s) — using defaults", exc)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    # Merge any missing keys from defaults (forward-compatible)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    os.makedirs("data", exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    # Invalidate aggregate cache so next /api/news call reflects new config
    with _AGG_LOCK:
        _AGG_CACHE["ts"] = 0.0
        _AGG_CACHE["data"] = None


# ─────────────────────────────────────────────────────────────────────────────
# Sentiment — FinBERT (with keyword fallback)
# ─────────────────────────────────────────────────────────────────────────────
_FINBERT: Dict[str, Any] = {"loaded": False, "pipe": None, "failed": False, "name": ""}
_SENT_CACHE: Dict[str, Dict[str, float]] = {}
_SENT_CACHE_LOADED = False
_SENT_CACHE_LOCK = threading.Lock()


def _load_sent_cache() -> None:
    global _SENT_CACHE, _SENT_CACHE_LOADED
    if _SENT_CACHE_LOADED:
        return
    if os.path.exists(SENT_CACHE_PATH):
        try:
            with open(SENT_CACHE_PATH, "r", encoding="utf-8") as f:
                _SENT_CACHE = json.load(f)
        except Exception:
            _SENT_CACHE = {}
    _SENT_CACHE_LOADED = True


def _save_sent_cache() -> None:
    try:
        os.makedirs("data", exist_ok=True)
        with open(SENT_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_SENT_CACHE, f)
    except Exception as exc:
        logger.warning("[news] sent cache save failed: %s", exc)


def _ensure_finbert(model_name: str) -> Optional[Any]:
    """Lazy-load FinBERT pipeline. Returns None on failure (caller falls back)."""
    if _FINBERT["failed"]:
        return None
    if _FINBERT["loaded"] and _FINBERT["name"] == model_name:
        return _FINBERT["pipe"]
    try:
        from transformers import pipeline   # type: ignore
        logger.info("[news] Loading FinBERT (%s)… first run downloads ~440MB", model_name)
        pipe = pipeline("text-classification", model=model_name, top_k=None)
        _FINBERT["pipe"]   = pipe
        _FINBERT["loaded"] = True
        _FINBERT["name"]   = model_name
        logger.info("[news] FinBERT ready")
        return pipe
    except Exception as exc:
        logger.warning("[news] FinBERT unavailable (%s) — falling back to keyword sentiment", exc)
        _FINBERT["failed"] = True
        return None


def _hash_headline(text: str, model: str) -> str:
    return hashlib.md5(f"{model}::{text}".encode("utf-8")).hexdigest()


def _keyword_sentiment(text: str) -> Dict[str, float]:
    words = re.findall(r"[a-zA-Z]+", text.lower())
    pos = sum(1 for w in words if w in _POS_WORDS)
    neg = sum(1 for w in words if w in _NEG_WORDS)
    if pos == 0 and neg == 0:
        return {"label": "neutral", "score": 0.5, "positive": 0.34, "negative": 0.33, "neutral": 0.33}
    total = pos + neg
    p = pos / total if total else 0.0
    n = neg / total if total else 0.0
    label = "positive" if p > n else "negative" if n > p else "neutral"
    return {
        "label":    label,
        "score":    max(p, n),
        "positive": round(p, 3),
        "negative": round(n, 3),
        "neutral":  round(max(0.0, 1.0 - total / max(len(words), 1)), 3),
    }


def _score_one_finbert(pipe: Any, text: str) -> Dict[str, float]:
    """Run FinBERT on a single headline → {label, score, positive, negative, neutral}."""
    out = pipe(text[:512])
    # pipeline with top_k=None returns [[{label, score}, {label, score}, ...]]
    rows = out[0] if isinstance(out, list) and out and isinstance(out[0], list) else out
    by_label = {r["label"].lower(): float(r["score"]) for r in rows}
    p = by_label.get("positive", 0.0)
    n = by_label.get("negative", 0.0)
    u = by_label.get("neutral",  0.0)
    if p >= n and p >= u:
        label, score = "positive", p
    elif n >= p and n >= u:
        label, score = "negative", n
    else:
        label, score = "neutral", u
    return {
        "label":    label,
        "score":    round(score, 4),
        "positive": round(p, 4),
        "negative": round(n, 4),
        "neutral":  round(u, 4),
    }


def score_headlines(texts: List[str], model: str) -> List[Dict[str, float]]:
    """Batch-score with caching. Returns list aligned with `texts`."""
    _load_sent_cache()
    results: List[Optional[Dict[str, float]]] = [None] * len(texts)
    pending: List[Tuple[int, str]] = []

    for i, t in enumerate(texts):
        key = _hash_headline(t, model)
        if key in _SENT_CACHE:
            results[i] = _SENT_CACHE[key]
        else:
            pending.append((i, t))

    if pending:
        pipe = _ensure_finbert(model)
        for idx, txt in pending:
            if pipe is not None:
                try:
                    sc = _score_one_finbert(pipe, txt)
                except Exception as exc:
                    logger.warning("[news] FinBERT score failed (%s) — keyword fallback", exc)
                    sc = _keyword_sentiment(txt)
            else:
                sc = _keyword_sentiment(txt)
            results[idx] = sc
            with _SENT_CACHE_LOCK:
                _SENT_CACHE[_hash_headline(txt, model)] = sc
        _save_sent_cache()

    return [r or {"label":"neutral","score":0.5,"positive":0.33,"negative":0.33,"neutral":0.34} for r in results]


# ─────────────────────────────────────────────────────────────────────────────
# Symbol / sector mapping
# ─────────────────────────────────────────────────────────────────────────────
# Light alias table for the largest tickers — keyword matcher would otherwise
# miss articles that refer to the company by full name only (no ticker).
COMMON_NAMES: Dict[str, List[str]] = {
    "RELIANCE":    ["reliance industries", "reliance"],
    "TCS":         ["tata consultancy", "tcs"],
    "INFY":        ["infosys"],
    "HDFCBANK":    ["hdfc bank"],
    "ICICIBANK":   ["icici bank"],
    "SBIN":        ["state bank of india", "sbi"],
    "AXISBANK":    ["axis bank"],
    "KOTAKBANK":   ["kotak mahindra", "kotak bank"],
    "ITC":         ["itc ltd", "itc limited"],
    "HINDUNILVR":  ["hindustan unilever", "hul"],
    "BHARTIARTL":  ["bharti airtel", "airtel"],
    "LT":          ["larsen & toubro", "l&t", "larsen and toubro"],
    "ASIANPAINT":  ["asian paints"],
    "MARUTI":      ["maruti suzuki"],
    "BAJFINANCE":  ["bajaj finance"],
    "BAJAJFINSV":  ["bajaj finserv"],
    "TATAMOTORS":  ["tata motors"],
    "TATASTEEL":   ["tata steel"],
    "M&M":         ["mahindra & mahindra", "mahindra and mahindra"],
    "SUNPHARMA":   ["sun pharma"],
    "ULTRACEMCO":  ["ultratech cement"],
    "NESTLEIND":   ["nestle india", "nestle"],
    "WIPRO":       ["wipro"],
    "HCLTECH":     ["hcl tech", "hcltech"],
    "ADANIENT":    ["adani enterprises"],
    "ADANIPORTS":  ["adani ports"],
    "ONGC":        ["oil and natural gas", "ongc"],
    "POWERGRID":   ["power grid"],
    "NTPC":        ["ntpc"],
    "JSWSTEEL":    ["jsw steel"],
    "TITAN":       ["titan company"],
    "ZOMATO":      ["zomato"],
    "PAYTM":       ["paytm", "one97"],
    "DMART":       ["avenue supermarts", "dmart"],
    "NYKAA":       ["fsn e-commerce", "nykaa"],
    "POLICYBZR":   ["policybazaar"],
    "DRREDDY":     ["dr. reddy", "dr reddy"],
    "DIVISLAB":    ["divi's lab", "divis lab"],
    "CIPLA":       ["cipla"],
    "COALINDIA":   ["coal india"],
    "GRASIM":      ["grasim industries"],
    "HEROMOTOCO":  ["hero motocorp"],
    "TECHM":       ["tech mahindra"],
    "TATACONSUM":  ["tata consumer"],
    "EICHERMOT":   ["eicher motors", "royal enfield"],
    "INDUSINDBK":  ["indusind bank"],
    "VEDL":        ["vedanta"],
    "JINDALSTEL":  ["jindal steel"],
    "LICI":        ["life insurance corporation", "lic"],
    "DABUR":       ["dabur"],
    "BRITANNIA":   ["britannia"],
    "BAJAJ-AUTO":  ["bajaj auto"],
}


def _watchlist_symbols() -> List[Dict[str, str]]:
    """Read positions.csv → [{symbol, name}]. Empty list if file missing."""
    try:
        import pandas as pd
        if not os.path.exists(POSITIONS_CSV):
            return []
        df = pd.read_csv(POSITIONS_CSV)
        if df.empty:
            return []
        cols = {c.lower(): c for c in df.columns}
        sym_col  = cols.get("symbol", "Symbol")
        name_col = cols.get("name",   "Name") if "name" in cols else None
        out = []
        for _, row in df.iterrows():
            sym = str(row.get(sym_col, "")).strip().upper()
            if not sym or sym == "NAN":
                continue
            nm = str(row.get(name_col, sym)).strip() if name_col else sym
            out.append({"symbol": sym, "name": nm})
        # Dedup keeping first occurrence
        seen, dedup = set(), []
        for r in out:
            if r["symbol"] in seen:
                continue
            seen.add(r["symbol"])
            dedup.append(r)
        return dedup
    except Exception as exc:
        logger.warning("[news] watchlist read failed: %s", exc)
        return []


def _build_universe(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Build {SYMBOL: {symbol, name, sector, patterns}} of stocks we want to match
    news against. Universe = watchlist + SECTOR_MAP (unless use_watchlist_only).
    """
    try:
        from core_sectors import SECTOR_MAP, get_sector
    except ImportError:
        try:
            from core.sectors import SECTOR_MAP, get_sector       # type: ignore
        except ImportError:
            SECTOR_MAP = {}
            def get_sector(s): return "OTHERS"   # noqa: E306

    universe: Dict[str, Dict[str, Any]] = {}

    # Watchlist symbols (always included)
    for row in _watchlist_symbols():
        sym = row["symbol"]
        universe[sym] = {"symbol": sym, "name": row["name"], "sector": get_sector(sym)}

    # Full SECTOR_MAP universe (unless watchlist-only)
    if not cfg.get("use_watchlist_only"):
        for sym, sector in SECTOR_MAP.items():
            if sym not in universe:
                universe[sym] = {"symbol": sym, "name": sym, "sector": sector}

    # Apply explicit allow-list if set
    allow = set(s.upper() for s in cfg.get("enabled_stocks") or [])
    if allow:
        universe = {s: v for s, v in universe.items() if s in allow}

    # Pre-compile regex patterns for each symbol (symbol + aliases)
    for sym, info in universe.items():
        patterns: List[str] = [sym]
        for alias in COMMON_NAMES.get(sym, []):
            patterns.append(alias)
        # Company name as alias (lowercased, first word ≥ 3 chars)
        name = info["name"].strip()
        if name and name.upper() != sym:
            # Use the leading meaningful chunk (e.g. "Reliance Industries Ltd" → "reliance industries")
            cleaned = re.sub(r"\b(ltd|limited|industries|corp|corporation|co\.?|inc\.?|plc)\b", "", name, flags=re.I).strip()
            if len(cleaned) >= 3:
                patterns.append(cleaned.lower())
        # Build a single combined regex for speed
        parts = [re.escape(p) for p in patterns if p]
        info["regex"] = re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE) if parts else None

    return universe


# ─────────────────────────────────────────────────────────────────────────────
# Feed fetcher
# ─────────────────────────────────────────────────────────────────────────────
_FEEDPARSER: Dict[str, Any] = {"mod": None, "checked": False}


def _get_feedparser():
    if _FEEDPARSER["checked"]:
        return _FEEDPARSER["mod"]
    try:
        import feedparser   # type: ignore
        _FEEDPARSER["mod"] = feedparser
    except ImportError:
        logger.warning("[news] feedparser not installed — pip install feedparser")
        _FEEDPARSER["mod"] = None
    _FEEDPARSER["checked"] = True
    return _FEEDPARSER["mod"]


def _fetch_one_feed(feed: Dict[str, Any], cutoff_ts: float) -> List[Dict[str, Any]]:
    """Fetch + parse one RSS feed → list of {title, link, source, published_ts, summary}."""
    feedparser = _get_feedparser()
    if feedparser is None:
        return []

    items: List[Dict[str, Any]] = []
    try:
        parsed = feedparser.parse(feed["url"])
    except Exception as exc:
        logger.warning("[news] %s failed: %s", feed.get("name"), exc)
        return items

    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        pub_struct = entry.get("published_parsed") or entry.get("updated_parsed")
        if pub_struct:
            try:
                pub_ts = time.mktime(pub_struct)
            except Exception:
                pub_ts = time.time()
        else:
            pub_ts = time.time()
        if pub_ts < cutoff_ts:
            continue
        items.append({
            "title":        title,
            "link":         entry.get("link", ""),
            "source":       feed.get("name", "RSS"),
            "published_ts": pub_ts,
            "published":    datetime.fromtimestamp(pub_ts).isoformat(timespec="seconds"),
            "summary":      (entry.get("summary") or "")[:400],
        })
    return items


def _fetch_all_feeds(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    cutoff = time.time() - max(1, int(cfg.get("time_window_hours", 24))) * 3600
    rows: List[Dict[str, Any]] = []
    for feed in cfg.get("feeds", []):
        if not feed.get("enabled"):
            continue
        rows.extend(_fetch_one_feed(feed, cutoff))
    # Dedup by (title, source-host) — Indian outlets cross-post
    seen = set()
    dedup: List[Dict[str, Any]] = []
    for r in rows:
        key = re.sub(r"\s+", " ", r["title"].lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)
    # Newest first, cap
    dedup.sort(key=lambda x: x["published_ts"], reverse=True)
    return dedup[: int(cfg.get("max_headlines", 150))]


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation
# ─────────────────────────────────────────────────────────────────────────────
def _trend_from_counts(pos: int, neg: int, neu: int) -> str:
    total = pos + neg + neu
    if total == 0:
        return "neutral"
    if pos >= neg and (pos - neg) / total >= 0.10:
        return "positive"
    if neg > pos and (neg - pos) / total >= 0.10:
        return "negative"
    return "neutral"


def _label_with_threshold(score: Dict[str, float], pos_thr: float, neg_thr: float) -> str:
    if score["positive"] >= pos_thr and score["positive"] >= score["negative"]:
        return "positive"
    if score["negative"] >= neg_thr and score["negative"] > score["positive"]:
        return "negative"
    return "neutral"


def _config_hash(cfg: Dict[str, Any]) -> str:
    return hashlib.md5(json.dumps(cfg, sort_keys=True).encode()).hexdigest()


def _aggregate(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Run a full fetch → score → group cycle. Returns the aggregate payload."""
    headlines = _fetch_all_feeds(cfg)
    if not headlines:
        return {
            "generated_at":     datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "model":            cfg.get("model"),
            "time_window_hours": cfg.get("time_window_hours"),
            "headlines_total":  0,
            "overall":          {"trend":"neutral","positive":0,"negative":0,"neutral":0,"headlines":[]},
            "sectors":          {},
            "stocks":           {},
        }

    # Score
    titles = [h["title"] for h in headlines]
    scores = score_headlines(titles, cfg.get("model") or DEFAULT_CONFIG["model"])
    pos_thr = float(cfg.get("positive_threshold", 0.55))
    neg_thr = float(cfg.get("negative_threshold", 0.55))
    for h, sc in zip(headlines, scores):
        h["sentiment"] = {**sc, "label": _label_with_threshold(sc, pos_thr, neg_thr)}

    # Universe for symbol/sector match
    universe = _build_universe(cfg)
    enabled_sectors = set(s.upper() for s in cfg.get("enabled_sectors") or [])

    sector_buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    stock_buckets:  Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    stock_meta:     Dict[str, Dict[str, str]]      = {}

    for h in headlines:
        text = f"{h['title']} {h.get('summary','')}"
        matched_syms: List[str] = []
        matched_sectors: set = set()
        for sym, info in universe.items():
            rx = info.get("regex")
            if rx and rx.search(text):
                matched_syms.append(sym)
                if info["sector"] and info["sector"] != "OTHERS":
                    matched_sectors.add(info["sector"])
        for sym in matched_syms:
            stock_buckets[sym].append(h)
            stock_meta.setdefault(sym, {"symbol": sym, "name": universe[sym]["name"], "sector": universe[sym]["sector"]})
        for sec in matched_sectors:
            if enabled_sectors and sec not in enabled_sectors:
                continue
            sector_buckets[sec].append(h)

    # Overall counts
    pos = sum(1 for h in headlines if h["sentiment"]["label"] == "positive")
    neg = sum(1 for h in headlines if h["sentiment"]["label"] == "negative")
    neu = sum(1 for h in headlines if h["sentiment"]["label"] == "neutral")

    def _bucket_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        p = sum(1 for x in items if x["sentiment"]["label"] == "positive")
        n = sum(1 for x in items if x["sentiment"]["label"] == "negative")
        u = sum(1 for x in items if x["sentiment"]["label"] == "neutral")
        return {
            "trend":    _trend_from_counts(p, n, u),
            "positive": p,
            "negative": n,
            "neutral":  u,
            "count":    len(items),
            "headlines": items[:8],   # cap per bucket for payload size
        }

    return {
        "generated_at":      datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "model":             cfg.get("model"),
        "model_loaded":      _FINBERT["loaded"] and not _FINBERT["failed"],
        "time_window_hours": cfg.get("time_window_hours"),
        "headlines_total":   len(headlines),
        "overall": {
            "trend":     _trend_from_counts(pos, neg, neu),
            "positive":  pos,
            "negative":  neg,
            "neutral":   neu,
            "count":     len(headlines),
            "headlines": headlines[:25],
        },
        "sectors": {
            sec: _bucket_summary(items)
            for sec, items in sorted(sector_buckets.items(), key=lambda kv: -len(kv[1]))
        },
        "stocks": {
            sym: {**_bucket_summary(items), **stock_meta.get(sym, {})}
            for sym, items in sorted(stock_buckets.items(), key=lambda kv: -len(kv[1]))
        },
    }


def get_news(force: bool = False) -> Dict[str, Any]:
    """Public entrypoint. Returns cached aggregate if within TTL, else refreshes."""
    cfg  = load_config()
    chash = _config_hash(cfg)
    ttl  = max(1, int(cfg.get("refresh_minutes", 15))) * 60
    now  = time.time()
    with _AGG_LOCK:
        if (not force
            and _AGG_CACHE["data"]
            and _AGG_CACHE["config_hash"] == chash
            and (now - _AGG_CACHE["ts"]) < ttl):
            return _AGG_CACHE["data"]
    data = _aggregate(cfg)
    with _AGG_LOCK:
        _AGG_CACHE["ts"]          = now
        _AGG_CACHE["data"]        = data
        _AGG_CACHE["config_hash"] = chash
    return data


# ── CLI for quick smoke test ─────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print("Fetching news…")
    data = get_news(force=True)
    print(f"Headlines: {data['headlines_total']}  Overall trend: {data['overall']['trend']}")
    print(f"  +{data['overall']['positive']}  -{data['overall']['negative']}  ={data['overall']['neutral']}")
    print(f"\nTop sectors:")
    for sec, info in list(data["sectors"].items())[:5]:
        print(f"  {sec:10s}  {info['trend']:8s}  +{info['positive']} -{info['negative']} ={info['neutral']}  ({info['count']} headlines)")
    print(f"\nTop stocks:")
    for sym, info in list(data["stocks"].items())[:5]:
        print(f"  {sym:12s}  {info['trend']:8s}  +{info['positive']} -{info['negative']} ={info['neutral']}  ({info['count']} headlines)")
