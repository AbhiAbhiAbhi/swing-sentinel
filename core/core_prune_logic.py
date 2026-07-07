"""
core_prune_logic.py
===================
Decides whether an OPEN watchlist candidate (one that is *being analysed* but
not yet BOUGHT) should be PRUNED from the Analysis tab.

DESIGN PRINCIPLE
----------------
Pruning is driven ENTIRELY by the candidate's own Analysis-tab data points
(recomputed daily from `fetch_stock_technicals`), NEVER by whether the stock
appeared in the daily scan. The scan filters and the Analysis-tab thesis are
two different rulesets; absence from the scan says nothing about whether the
analysed setup is still valid.

OUTCOMES
--------
"PRUNE"        -> the original thesis is *terminally* dead. Two cases only:
                    1. Trend structure broke (price/EMA alignment lost)
                    2. A false breakout already fired (the move happened & failed)
                  Pruned rows are kept in positions.csv with Status="PRUNED"
                  so the dashboard can show them in a separate section.

"RE-EVALUATE"  -> everything else. The setup is either fully intact, or in a
                  recoverable/suspended state (base loosened, weekly wobble,
                  drifted out of zone, earnings nearing). These remain on the
                  Analysis tab in the normal list.

The function is pure (no I/O) and tolerant of missing / string-y CSV values,
so it is safe to call on every row during the daily cadence pass.
"""

from typing import Tuple, Optional
from datetime import datetime
import pytz


# ── helpers ──────────────────────────────────────────────────────────────────

def _f(v, default: float = 0.0) -> float:
    """Coerce a possibly-string / NaN / None value to float."""
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _s(v, default: str = "") -> str:
    """Coerce to an upper-cased, stripped string."""
    try:
        if v is None:
            return default
        return str(v).strip().upper()
    except Exception:
        return default


def _get_days_elapsed(date_str: str) -> Optional[int]:
    """Calculate the number of calendar days elapsed since date_str (YYYY-MM-DD) in Asia/Kolkata timezone."""
    if not date_str or date_str.lower() in ("nan", "none", ""):
        return None
    try:
        tz = pytz.timezone("Asia/Kolkata")
        today = datetime.now(tz).date()
        entry_date = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
        return (today - entry_date).days
    except Exception:
        return None


# ── individual terminal checks ───────────────────────────────────────────────

def _trend_broke(tech: dict) -> Tuple[bool, str]:
    """
    Terminal reason #1 — trend structure has broken.

    The premise of a swing-long is price riding above its short EMAs in an
    uptrend. When that alignment is lost, the reason the stock was shortlisted
    no longer exists.

    We treat the trend as broken if ANY of:
      - price closes below EMA20
      - EMA20 has crossed below EMA50 (medium-term structure gone)
      - the explicit ema9/ema21 cross flag has turned bearish

    `ema_aligned` (if present, written by some code paths) is honoured as an
    authoritative override when it is explicitly False.
    """
    # Authoritative flag if present
    if "ema_aligned" in tech and tech.get("ema_aligned") is False:
        return True, "Trend broke: EMA alignment lost (price no longer above rising short EMAs)."

    price = _f(tech.get("price"))
    ema20 = _f(tech.get("ema20"))
    ema50 = _f(tech.get("ema50"))

    # Only judge when we actually have the numbers (avoid false prunes on N/A)
    if price > 0 and ema20 > 0:
        if price < ema20:
            return True, (
                f"Trend broke: price (Rs {price:.1f}) closed below EMA20 "
                f"(Rs {ema20:.1f}) — short-term structure lost."
            )
    if ema20 > 0 and ema50 > 0:
        if ema20 < ema50:
            return True, (
                f"Trend broke: EMA20 (Rs {ema20:.1f}) crossed below EMA50 "
                f"(Rs {ema50:.1f}) — medium-term structure lost."
            )

    # Explicit short-EMA cross flag, when provided. fetch_stock_technicals emits
    # "death" for a bearish EMA9/EMA21 cross; other code paths may use legacy
    # flags ("BEARISH"/"DOWN"/"FALSE"/"NO") — accept all of them.
    cross = _s(tech.get("ema9_cross_ema21"))
    if cross in ("DEATH", "BEARISH", "DOWN", "FALSE", "NO"):
        return True, "Trend broke: EMA9 crossed below EMA21 (short-term momentum flipped)."

    return False, ""


def _false_breakout_fired(tech: dict) -> Tuple[bool, str]:
    """
    Terminal reason #2 — a false breakout has already fired.

    `false_breakout_risk == "HIGH"` means the anticipated move already happened
    and FAILED (failed breakout / rejection wick / dry-volume breakout). Waiting
    for a re-entry on a stock that just trapped buyers is a *new* thesis, not the
    one we shortlisted — so the original candidate is terminal.
    """
    if _s(tech.get("false_breakout_risk")) == "HIGH":
        desc = str(tech.get("false_breakout_desc") or "").strip()
        detail = f" {desc}" if desc else ""
        return True, f"False breakout fired: the anticipated move already failed.{detail}"
    return False, ""


def _age_limit_reached(row: Optional[dict]) -> Tuple[bool, str]:
    """
    Time-based prune rule:
    Prune from Analysis tab (status OPEN) if it has been 7 or more days since Entry_Date
    and it has not been bought (still in OPEN status), OR if the stock never hit the entry zone.
    """
    if not row:
        return False, ""

    entry_date_str = _s(row.get("Entry_Date"))
    if not entry_date_str or entry_date_str in ("NAN", "NONE", ""):
        return False, ""

    days = _get_days_elapsed(entry_date_str)
    if days is not None and days >= 7:
        hit_date_str = _s(row.get("Entry_Hit_Date"))
        has_hit = hit_date_str and hit_date_str not in ("NAN", "NONE", "")
        if not has_hit:
            return True, f"Stock did not hit entry zone within 7 days (elapsed: {days} days since entry on {entry_date_str})."
        else:
            return True, f"Stock remained in Analysis tab for 7+ days without being bought (elapsed: {days} days since entry on {entry_date_str})."

    return False, ""


# ── public API ───────────────────────────────────────────────────────────────

def evaluate_prune(tech: dict, row: Optional[dict] = None) -> Tuple[str, str]:
    """
    Decide the fate of one OPEN analysis candidate.

    Parameters
    ----------
    tech : dict
        Freshly recomputed Analysis-tab data points for the symbol
        (output of fetch_stock_technicals). If empty/None, we cannot judge technical rules,
        but we can still judge time-based rules if row context is provided.
    row : dict, optional
        The positions.csv row dictionary containing metadata like Entry_Date and Entry_Hit_Date.

    Returns
    -------
    (state, reason) : Tuple[str, str]
        state  : "PRUNE" | "RE-EVALUATE"
        reason : human-readable reason when PRUNE, else "".
    """
    # 1. Check time-based rules first if row context is provided
    if row:
        hit, reason = _age_limit_reached(row)
        if hit:
            return "PRUNE", reason

    # 2. No data this cycle (scan miss, fetch timeout, yfinance error) is NOT a
    # reason to prune for technical rules — we simply cannot judge, so hold in re-evaluate.
    if not tech:
        return "RE-EVALUATE", ""

    for check in (_trend_broke, _false_breakout_fired):
        hit, reason = check(tech)
        if hit:
            return "PRUNE", reason

    # Intact, or in a recoverable/suspended state — single re-evaluate bucket.
    return "RE-EVALUATE", ""
