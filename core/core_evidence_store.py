"""Cache layer for historical evidence JSONs.

Files: data/historical_evidence/{SYMBOL}_{SETUP}_{STRATEGY_VERSION}.json
Atomic tmp+os.replace writes (same pattern as core_post_mortem.save_post_mortem).
A failed refresh never deletes the last known good result — it is marked
error_stale in place instead.
"""
import json
import os
import re
from datetime import datetime, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE_DIR = os.path.join(_ROOT, "data", "historical_evidence")

_UNSAFE = re.compile(r'[\\/:*?"<>|\s]')


def _safe(part):
    return _UNSAFE.sub("_", str(part).strip().upper())


def cache_path(symbol, setup_type, sver, root=None):
    base = os.path.join(root, "data", "historical_evidence") if root else EVIDENCE_DIR
    return os.path.join(base, f"{_safe(symbol)}_{_safe(setup_type)}_{sver}.json")


def read_cache(symbol, setup_type, sver, root=None):
    path = cache_path(symbol, setup_type, sver, root)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def write_cache(result, root=None):
    path = cache_path(result["symbol"], result["setup_type"],
                      result["strategy_version"], root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, default=str)
    os.replace(tmp, path)
    return path


def last_completed_session(now=None):
    """Most recent completed NSE trading day (weekday heuristic — a holiday
    false-positive only causes a harmless idempotent refresh)."""
    now = now or datetime.now()
    d = now.date()
    if now.hour < 15 or (now.hour == 15 and now.minute < 35):
        d -= timedelta(days=1)
    while d.weekday() >= 5:   # Sat/Sun
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def is_stale(result, now=None):
    """(stale?, reason). Missing/errored results are handled by callers."""
    if not result:
        return True, "missing"
    as_of = str(result.get("market_data_as_of") or "")
    if not as_of:
        return True, "no market_data_as_of"
    last = last_completed_session(now)
    if as_of < last:
        return True, f"data as of {as_of}, last completed session {last}"
    return False, None


def mark_error(symbol, setup_type, sver, err, root=None):
    """Record a refresh failure WITHOUT losing the last good result."""
    existing = read_cache(symbol, setup_type, sver, root)
    if existing:
        existing["status"] = "error_stale"
        existing["stale_reason"] = str(err)[:300]
        write_cache(existing, root)
        return existing
    stub = {
        "schema_version": 1,
        "symbol": str(symbol).upper(),
        "setup_type": setup_type,
        "strategy_version": sver,
        "generated_at": datetime.now().astimezone().isoformat(),
        "market_data_as_of": None,
        "summary": {},
        "episodes": [],
        "status": "error",
        "stale_reason": str(err)[:300],
    }
    write_cache(stub, root)
    return stub
