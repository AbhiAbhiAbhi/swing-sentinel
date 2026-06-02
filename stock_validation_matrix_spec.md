# Stock Validation Matrix — Design Specification

**Status:** Design agreed, ready to implement
**Scope:** Analysis-tab Stock card scoring + handoff to Trading-tab position sizing
**Deferred (separate task):** SL ceiling fix in `calculate_trade_plan` (the −2.24% artifact)

---

## 1. Operating context (the constraints this design must respect)

These are the facts about *how you actually trade* that the matrix is built around. Every design choice below traces back to one of these.

1. **You buy at a calculated entry zone later, not at scan-day market price.**
   → The score must reflect what is still true *at fill time*, not signals that are only true at the scan instant.
2. **You scan daily. OPEN (waiting-for-entry) candidates are deleted on substantive scan rejection and refreshed (entry/SL/targets recalculated) on survival.**
   → The matrix runs every scan and must be stable — a stock must not flip STRONG↔WEAK on one noisy scan.
3. **Snapshot signals describe the scan instant — a moment you are not acting on.**
   → RSI-now, MACD-freshness, today's volume spike, today's false-breakout candle, and the live WATCH timing state must **not** drive the buy-decision score. They move to fill-time confirmation.
4. **Formulas can carry hidden artifacts** (proven by the −2.24% SL-ceiling finding).
   → Every score element must be *discriminating* (varies across eligible stocks), *structural* (survives to fill), and *non-redundant* (counted once, never gated-and-also-scored).

---

## 2. Architecture: GATE → SCORE

Two sequential stages. Not two scores shown side by side — a **binary gate**, then a **single 0–100 quality number** computed only for stocks that pass the gate.

```
Stock
  │
  ▼
[ SAFETY GATES ]  ── any fail ──▶  INELIGIBLE
  │                                show "BLOCKED: <reason>", no score
  │ all pass
  ▼
[ QUALITY SCORE 0–100 ]  ──▶  label (STRONG/MODERATE/WEAK)
  │                            └─▶ conviction multiplier ──▶ position size
  ▼
rank + Top Picks
```

**Why a gate, not a blended score:** a fatal flaw (promoter dumping, F&O ban, bearish weekly) must not be *averaged* against good technicals. A 95/100-technical stock with an active institutional exit is not a "73" — it is a no-touch. Averaging hides the landmine; gating surfaces it.

**Timing is NOT a scan-day axis for you.** Because you buy later at a calculated entry, "is price actionable right now" is a *fill-day* question, handled by the ENTRY-READY alert in `check_positions_and_notify` — not by the scan-day card.

---

## 3. SAFETY GATES (binary, structural)

Slow-moving disqualifiers that remain valid between scan and fill. **Any single fail → ineligible, quality score not computed.** Gates 1–8 are independent boolean checks; **Gate #9 (Sector × Nifty regime) is an interaction matrix** detailed separately below.

| # | Gate | Source field | Fail condition |
|---|------|--------------|----------------|
| 1 | Holding status | `Fundamental_Status` | `ON_HOLD` |
| 2 | Weekly trend | `weekly_trend` | `BEARISH` |
| 3 | Fundamental strength | `filter_fundamental_strength()` | negative EPS / PE / ROE / EBITDA |
| 4 | Institutional flow | `shareholding.classification` | `DISTRIBUTION` or consensus sell |
| 5 | Liquidity | `avg_volume_20d` | `< 100,000` shares |
| 6 | Overextended | `return_20d` | `> 25%` |
| 7 | Adversarial check | `debate.verdict` | `SKIP` |
| 8 | Data freshness | scan date | not from today's scan (**1-day window**, not the current 7-day `isDataStale`) |

### Gate #9 — Market-regime alignment (Sector × Nifty)

This is an **interaction matrix**, not two independent checks — the same sector state means different things under different Nifty regimes, so it cannot be expressed as additive score points. It outputs **either a hard skip (gate fail) or a regime sizing-multiplier** (see §6).

**Leg 1 — Sector vs its own 20 DMA** (map the stock's sector → its index via `SECTOR_MAP`, read `fetch_sector_pulse()`):
- Above 20 DMA → **Green**
- Within 2% below (`-2% ≤ pct_from_ema20 < 0`) → **Amber** (reduce size)
- More than 2% below (`pct_from_ema20 < -2%`) → **Red**

**Leg 2 — Broad Nifty regime** (`fetch_nifty_levels().regime`): **GREEN / AMBER / RED**.

**The 3×3 combination matrix** (4 corners are user-specified; Amber cells interpolate by the rule *worse axis dominates, Amber = half-size*):

| Nifty ↓ \ Sector → | **Green** (>20DMA) | **Amber** (≤2% below) | **Red** (>2% below) |
|---|:---:|:---:|:---:|
| **Green** | **1.0×** — best case, full position | 0.75× — reduce | **SKIP** — rotation trap (sector being rotated out of) |
| **Amber** | 0.75× | 0.5× | **SKIP** |
| **Red** | 0.5–0.75× — sector outperformance, valid setup | 0.5× | **HARD SKIP** |

**User-specified corners (authoritative):**
- Nifty Green + Sector Green → full position (1.0×)
- Nifty Red + Sector Green → sector outperformance, valid at 50–75% size
- Nifty Green + Sector Red → trap (rotation out of sector) → **skip**
- Nifty Red + Sector Red → **hard skip**

**Behaviour:**
- The two **SKIP / HARD SKIP** cells → **gate fail** (ineligible; show "BLOCKED: regime misalignment").
- All surviving cells → emit a **regime multiplier** (1.0 / 0.75 / 0.5) that stacks in `recalcPosSize` (see §6). This is why regime lives at the gate/sizing layer, **not** in the 0–100 quality score — its interaction logic can't be expressed as additive points.

**⚠ Backend reconciliation required:** `core_risk_filters.filter_weak_sector()` currently downgrades sector weakness to a *warning, not a skip* (deliberate, to keep early-leader setups). This matrix's Case 3 (Nifty Green + Sector Red → skip) **conflicts** with that. Per user direction, the **matrix is authoritative** — align `filter_weak_sector` to hard-skip on sector-Red under a non-Red Nifty, so the scan filter and the card agree. (Distinguishing a true leader from a sympathy-follower would need per-stock sector-relative strength, which `/api/scan` does not compute — so the conservative skip is the correct default.)

### Consolidation note (important)
These checks currently exist in **three** places with inconsistent thresholds:
- `agents_scanner.generate_priority_actions()` (fundamental, liquidity, overextension)
- `core_risk_filters.apply_risk_filters()` (all of them, as the canonical stack)
- `computeCompositeScore()` early-returns (client-side duplicate)

**The matrix consolidates these into ONE gate list with ONE source of truth.** Eliminate the duplicates; do not let the client recompute what `apply_risk_filters` already decided. The server's `verdict` + `reasons` should be the authority the card reads.

### Freshness change for daily cadence
`isDataStale` is currently a **7-day** window — sized for a weekly trader. Because you scan and act daily, change to **1 trading day** (Friday's scan remains valid Monday — skip weekends). A 2-day-old card must not present as fresh.

---

## 4. QUALITY SCORE (0–100, structural-only)

Computed **only if all safety gates pass.** Every category is structural (survives to fill), discriminating (varies across eligible stocks), and counted once.

| Category | Weight | Source field(s) | Scoring rule |
|----------|:------:|-----------------|--------------|
| **Setup type** | 25 | `setup` | BREAKOUT = 25 · PULLBACK = 18 · SUPPORT_BOUNCE = 18 · CONSOLIDATION = 12 |
| **Trend structure** | 20 | `price`, `ema20`, `ema50` | Price > EMA20 > EMA50 alignment depth (daily only — weekly is gated, do not re-award) |
| **Base quality** | 15 | `base_status`, `base_days` | STABLE_BASE = 15 · CONSOLIDATING = 9 · VOLATILE = 0 |
| **Trend strength** | 15 | `adx` | ADX > 25 = 15 · 20–25 = 9 · < 20 = 0 |
| **Institutional accumulation** | 15 | `shareholding.consensus_score` | ACCUMULATION = 15 · NEUTRAL = 7.5 · **missing data = 7.5 (neutral)** |
| **Volatility fit** | 10 | `atr_pct` | 2–4% = 10 (ideal) · 1.5–2% or 4–6% = 6 · < 1.5% or > 6% = 0 |
| **TOTAL** | **100** | | |

### Weighting rationale
- **Setup type is the heaviest (25)** because the backtest *earned* it: BREAKOUT 92% WR, CONSOLIDATION 75%, PULLBACK 49%. It is the single highest-signal, most-stable variable. The score should tilt toward the setups that actually win.
- **Institutional accumulation scores neutral (7.5) when missing**, not zero, because `fetch_screener_shareholding` (screener.in scrape) frequently returns empty — a scrape failure must not unfairly tank an otherwise strong stock.
- **Volatility fit (10) encodes the −2.24% lesson**: penalize ATR so low the structural stop gets pinned tight by the ceiling. *Mild* weight only — the real fix is the deferred SL-formula change, not compensating for it in the score.

### Categories deliberately REMOVED (and why)
| Removed | Reason |
|---------|--------|
| `relative_strength` (was 5 pts) | `/api/scan` never sends it → always 0 → dead category. (Only `/api/plan` sends it.) |
| `acid_test_*` (was 5 pts) | Never sent by `/api/scan` → always 0 → dead category. |
| `ema_aligned` (was 10 pts) | Chartink pre-filters for EMA alignment → always true → non-discriminating free points. |
| False-breakout points | Already a safety gate **and** a snapshot field → double-counted + wrong-instant. |
| MACD freshness | Decays by the day → false by the time you fill → snapshot signal. |
| Weekly-trend points | Already a gate → re-awarding it is double-counting. |

---

## 5. Snapshot signals → fill-time confirmation (NOT scan-day score)

These describe the scan instant only. They are **excluded from the buy-decision score** and surfaced instead when the ENTRY-READY alert fires (i.e. when price reaches the entry zone — the moment they are actually true and actionable):

- RSI position (40–65 sweet spot)
- MACD crossover freshness
- Today's volume spike / `vol_ratio`
- Today's false-breakout candle
- Live timing state (`verdict`: WATCH_SUPPORT / WATCH_RESISTANCE / WARNING)

**Hook that already exists:** `check_positions_and_notify()` fires "ENTRY READY" Telegram alerts when an OPEN position's price reaches its entry zone. That alert is the correct place to show the fill-time confirmation panel.

---

## 6. Score → label → position sizing (one number, flowing through)

| Quality score | Label | Conviction multiplier |
|:-------------:|:-----:|:---------------------:|
| 80–100 | STRONG | 1.0× |
| 65–79 | MODERATE | 0.75× |
| 50–64 | WEAK | 0.5× |
| < 50 or any gate fail | SKIP | 0× (do not take) |

### Position-sizing integration (`recalcPosSize`)
```
base_qty   = floor( (capital × risk_pct) / (entry − SL) )
final_qty  = base_qty × conviction_mult × expiry_mult × regime_mult
final_qty  = clamp(final_qty, 0, floor(0.20 × capital / entry))   # 20% allocation cap
```
- `regime_mult` comes from Gate #9 (1.0 / 0.75 / 0.5); SKIP/HARD-SKIP cells never reach sizing (gated out).
- One quality number drives **both** ranking and sizing — no competing labels, no "STRONG but 48/100" contradiction.
- Composes naturally with the existing `expiry_mult` (same mechanism), and with a low-quality/WATCH stock automatically getting smaller size instead of being silently capped at 50 in a ranking that sizing then ignores.

---

## 7. Daily-cadence rules for OPEN (waiting-for-entry) candidates

On each daily scan, for every OPEN candidate not yet filled:

| Scan result | Meaning | Action |
|-------------|---------|--------|
| Passes scan | Still valid | **Keep + refresh** entry/SL/T1/T2 to today's calculated values (EMA20 moves daily — stale entry zones must be recomputed) |
| Explicitly fails a gate (SKIP w/ reason: weakened, overextended, distribution, etc.) | Entry thesis broken before fill | **Delete** |
| Merely absent from today's scan (no explicit fail) | Possible scan noise / transient | **Keep one cycle** — do not delete on absence alone |

**Key distinction:** delete on *"scan says it now FAILS"* (substantive `verdict == SKIP` with a reason), **not** on *"scan didn't MENTION it today"* (absence). `apply_risk_filters` returns `verdict` + `reasons` — use the explicit fail, not the absence, to avoid pruning good candidates on a noisy scan.

**This rule applies ONLY to OPEN (un-filled) candidates.** BOUGHT positions are governed by exit logic (SL / target / thesis-break), never by entry-scanner rejection — rejecting on entry criteria would systematically cut winners (a stock that ran up trips the overextended filter precisely *because* it worked).

---

## 8. Implementation checklist

- [ ] Consolidate safety gates into one list; client reads server `verdict`/`reasons`, stops recomputing.
- [ ] Implement Gate #9 (Sector × Nifty 3×3 regime matrix) → hard-skip cells + regime multiplier; reconcile `filter_weak_sector` to match (sector-Red hard-skips under non-Red Nifty).
- [ ] Change `isDataStale` 7-day → 1-trading-day window.
- [ ] Rewrite `computeCompositeScore`: remove dead categories (relative_strength, acid_test, ema_aligned, false-breakout points, MACD-freshness, weekly-trend points); implement the 6-category / 100-pt structural score above.
- [ ] Make `getStockValidation` the *explanation grid* only — it must not compute a competing STRONG/MODERATE label. One headline number from `computeCompositeScore`.
- [ ] Add conviction-multiplier tiers; wire into `recalcPosSize` alongside `expiry_mult`.
- [ ] Institutional accumulation: neutral (7.5) on missing shareholding data.
- [ ] Move snapshot signals to a fill-time confirmation panel tied to the ENTRY-READY alert.
- [ ] Daily OPEN-candidate logic: keep+refresh on pass, delete on explicit SKIP, keep-one-cycle on mere absence.

### Deferred (separate task — do NOT bundle here)
- [ ] **SL ceiling fix** in `calculate_trade_plan`: the `sl_ceiling = min(price, entry_min) × 0.985` pins low-ATR stops at exactly −2.24% from entry-mid, producing noise-width stops. Touches live + backtest + dashboard — bigger blast radius, handle on its own.

---

## 9. Known data-availability notes

- `relative_strength` — sent by `/api/plan` only, **not** `/api/scan`. If you want it in the scan score later, wire it into `process_single_stock` server-side first.
- `shareholding` (`fetch_screener_shareholding`) — screener.in scrape, frequently empty → drives the neutral-on-missing rule for the institutional category.
- `verdict` — `process_single_stock` *does* return it from `apply_risk_filters`, but the dashboard currently ignores it. Surface it for the OPEN-candidate delete/keep logic and (later) the fill-time timing state.
- `false_breakout_risk` — only ever `HIGH` or `LOW` from `core_data_fetcher`; any `MEDIUM` branch is dead code.
