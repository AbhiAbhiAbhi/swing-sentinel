# Stock Card Historical and Live Evidence Specification

## Purpose

Add an explainable **Historical and Live Evidence** layer to each Stock Validation Card in the **Analysis** tab.

The feature answers two different questions without mixing their data:

1. **Historical market simulation:** “When this same strategy setup occurred in this stock's historical market data, what happened?”
2. **Live Swing Sentinel record:** “How did Swing Sentinel actually perform when it selected and managed comparable trades?”

This feature is an evidence and ranking aid. It must **not** override hard safety gates or automatically place an order.

The intended decision flow is:

```text
Hard safety gates
  -> Soft risk and macro regime
  -> Quality score
  -> Historical + live evidence
  -> Trade sizing and portfolio allocation
  -> BUY NOW / ARM LIMIT / WATCH / SKIP / INSUFFICIENT DATA
```

## Scope

### In scope

- A compact historical and live evidence panel in every Analysis-tab stock card.
- A backend endpoint/service that generates or retrieves cached per-stock historical simulations.
- A comparable-trade summary drawn from `data/positions.csv` and completed Swing Sentinel trades.
- Recalculation rules integrated with the existing **Keep & Refresh** workflow.
- Strategy-versioned cache files and clear stale/insufficient-data states.
- Independent-trade-episode de-duplication.

### Out of scope for the first version

- Automated order placement.
- Treating an LLM debate as historical evidence.
- Intraday backtesting; use daily OHLCV data initially.
- Optimising rules until they fit one stock's history.
- Combining historical-simulation results and live-trade results into one win rate.

## Design principles

1. **Safety before score.** A card with a hard blocker is `SKIP` even if historical evidence is strong.
2. **Unknown is not pass.** Missing, stale, or failed data must be visible as `INSUFFICIENT DATA`.
3. **Rules, not today's rupee levels, are backtested.** Historical trades use the same entry/stop/target formulas as the current strategy, while their actual rupee levels naturally differ.
4. **Execution must be realistic.** Limit orders may not fill; gap-through-stop exits do not receive the ideal stop price.
5. **One market move is one episode.** Repeated daily scans of the same continuing trend must not inflate sample counts.
6. **Historical and live evidence stay separate.** Historical evidence validates rules; live evidence validates implementation and real execution.
7. **Cache aggressively.** Never block card rendering on a remote market-data fetch or an LLM call.

## Card layout and user experience

### Card header: always visible

```text
SYMBOL · ₹CurrentPrice                                      PASS / WATCH / SKIP / INSUFFICIENT DATA
Action: ARM LIMIT · Entry ₹250–253 · Qty 120 · SL ₹238 · Planned loss ₹1,560
```

Status definitions:

| Status | Meaning | Permitted action |
|---|---|---|
| `PASS` | All hard gates pass and no material soft warning requires reduced risk. | `BUY NOW` if price is in the entry zone; otherwise `ARM LIMIT`. |
| `WATCH` | Hard gates pass, but soft risk or confirmation requirement exists. | Do not buy at full size; use reduced risk, wait for trigger, or keep a limit order armed only if policy allows. |
| `SKIP` | At least one hard gate fails. | Do not buy or arm a new order. |
| `INSUFFICIENT DATA` | Required data is missing, stale, or analysis has not completed. | Do not buy until refreshed or deliberately overridden. |

### Card section order

1. **Decision summary** — status, proposed action, top three reasons.
2. **Hard Safety Gates** — collapsed by default; blockers visible when collapsed.
3. **Soft Risk & Macro Regime** — collapsed by default; market permission and allowed risk visible in summary.
4. **Quality Score** — collapsed by default; ranking only after hard gates pass.
5. **Historical & Live Evidence** — collapsed by default; summary visible when card is eligible.
6. **Sizing & Allocation** — collapsed by default; final approved quantity and binding constraint.
7. **Debate Chamber** — on demand; advisory evidence, not a replacement for rules.
8. **Footer actions** — refresh validation, run/retry evidence, run debate, buy/arm only when eligible.

### Compact evidence panel

The collapsed panel should render immediately from cache:

```text
Historical setup evidence                         LIVE RECORD
12 mo · 9 independent episodes · weak sample      4 comparable completed trades
Filled 7/9 · Expectancy +0.24R                     Expectancy +0.11R
Worst loss −1.7R · Median hold 8 sessions          Too few trades for confidence
Updated: 12 Jul 2026, 16:10 IST                    Updated: 12 Jul 2026, 15:30 IST
```

If no usable result exists:

```text
Historical setup evidence: INSUFFICIENT DATA
No cached simulation for strategy v1.3. Run is queued after market close.
```

The expanded view includes trade episodes, filters, definitions, performance metrics, coverage dates, strategy version, source timestamps, and reasons a signal did not become a filled trade.

## Safety, macro, quality, sizing, and evidence responsibilities

### 1. Hard Safety Gates

Hard gates answer whether the trade may exist at all. Any failure produces `SKIP`.

Initial hard-gate candidates:

- Daily and weekly trend/invalidation rule failed.
- Required market data, entry plan, stop, or target is missing/stale.
- Stop is invalid: stop is above/at entry or lacks a valid setup-specific invalidation.
- Liquidity is below the configured minimum.
- Sector is `RED` or the sector/Nifty combination is a configured hard-block cell.
- Earnings or another binary event is inside the configured no-trade window.
- Current price is too far above the entry zone.
- R:R at the intended entry is below the configured minimum.
- Mandatory breakout volume rule fails for a breakout.
- Server-side risk filter returns `SKIP`.

### 2. Soft Risk and Macro Regime

Soft risks adjust the permitted risk; they do not silently become green passes.

Examples:

- Nifty or sector is `AMBER`.
- India VIX/equivalent volatility is elevated.
- Relative strength is positive but weakening.
- Entry volume is only marginally above its threshold.
- Price is near overhead resistance.
- Overnight gap risk is elevated but no hard event block exists.
- Debate result is `WATCH`.

Display a deterministic **market permission** value:

| Permission | Example meaning |
|---|---|
| `FULL_RISK` | Broad market and sector aligned; no soft reduction. |
| `REDUCED_RISK` | Market/sector or other soft risks require 0.5x–0.75x risk budget. |
| `NO_NEW_RISK` | Existing positions may be managed, but new entries are not permitted. |
| `BLOCKED` | Maps to `SKIP`; a hard macro rule failed. |

### 3. Quality Score

The Quality Score ranks only eligible candidates. It must not make a blocked trade eligible.

Suggested non-overlapping components:

- Setup structure and base quality.
- Trend and relative strength versus Nifty and sector.
- Momentum confirmation beyond minimum gate thresholds.
- Entry quality: distance to planned entry, stop quality, and R:R above the minimum.
- Catalyst/fundamental context where reliable data exists.

Use a base score plus transparent adjustments:

```text
Base technical quality       82/100
Macro adjustment             −12
Debate confidence adjustment  −5
Final ranking score          65/100
```

Do not award large points for a condition already used as a hard gate. For example, breakout volume can be a hard minimum of 1.2x, but the quality score may reward only the strength above that minimum.

### 4. Sizing and Allocation

Sizing is the final execution approval, not a separate optional calculator.

```text
Risk budget                   ₹5,000
Stop distance                 ₹24/share
Risk-based quantity           208
Regime reduction (0.75x)      156
Sector allocation cap         120   <- binding constraint
Liquidity cap                 500
Final approved quantity       120
Planned loss                  ₹2,880
Gap-stress loss               ₹4,320
```

The final quantity must be the minimum of:

- Risk-budget quantity.
- Capital allocation cap.
- Total portfolio open-risk cap.
- Sector/theme concentration cap.
- Correlated-position cap.
- Liquidity/order-size cap.
- Lot-size or integer-share constraint.
- Macro/soft-risk reduction.

The card becomes `OPEN TO BUY` only when a non-zero valid quantity exists and all other action rules pass.

### 5. Debate Chamber

The Debate Chamber is an adversarial research layer.

- `SKIP`: may be configured as a hard block.
- `WATCH`: soft risk; reduce size or wait for a trigger.
- `BUY`: confirmation only; it cannot override a hard gate.
- `NOT RUN` or unavailable: show explicitly, never as `PASS`.

Require the result to expose top red flags and exact falsification triggers, e.g. “cancel if daily close below ₹244” or “do not enter before results.”

## Historical market simulation

### Objective

For the selected stock, replay the **current strategy rules** on historical daily OHLCV data to find completed, independent episodes of the same setup type.

It answers:

> Under the versioned rules currently used by this card, how did this stock's comparable setups perform historically?

### Default history coverage

- Start at **12 calendar months** of daily data.
- Include 250 additional trading-day warm-up bars for EMA200 and other indicators.
- If fewer than 15 independent episodes are found, extend to **24 months**.
- If still fewer than 15, return a valid result with `sample_quality: weak`, not a fake confident conclusion.
- Never require the system to find a target number of trades by relaxing rules.

Suggested sample labels:

| Independent episodes | Label |
|---:|---|
| 0–4 | `insufficient` |
| 5–14 | `weak` |
| 15–29 | `usable` |
| 30+ | `stronger` |

### What “same setup” means

The historical match uses the current **strategy version**, not today's price levels.

Example for a current pullback card:

```text
Current trade: Entry ₹250–253, SL ₹238, T1 ₹278, T2 ₹292

Historical replay:
- detect PULLBACK using the same trend, RSI, volume, and macro filters;
- calculate each past day's entry zone with the same formula;
- calculate each past day's SL with the same ATR/support formula;
- calculate T1/T2 using the same target formula;
- simulate the defined entry and exit rules.
```

Historical rupee entry, stop, and target values must be derived using only data available on that historical date. This prevents look-ahead bias.

### Required simulation rules

#### Signal and entry

- Evaluate the setup only using data available at the close of signal day `D`.
- Calculate the historical entry zone, stop, targets, and gates as of `D`.
- Do not assume a fill at a convenient midpoint.
- For `BUY NOW`-style simulation: enter at the next session open plus configured buy slippage.
- For `ARM LIMIT`-style simulation: place a limit at the defined entry-zone value; count a fill only if a subsequent daily low reaches that level.
- Use a configurable maximum wait, initially five trading sessions.
- If not filled in that period, record `NOT_FILLED`; do not call it a win or loss.
- Do not enter above the configured maximum entry price.

#### Stop and gap handling

- Use the setup-specific initial invalidation stop produced by the strategy.
- If the next session opens below the stop, exit at the opening price minus sell slippage, not at the ideal stop.
- If price trades through the stop during the day, use the configured conservative daily-bar assumption (stop price minus sell slippage).
- If both a stop and target are touched in the same daily bar and no intraday sequence exists, apply the conservative policy: **stop first**.
- Persist a `gap_through_stop` flag and the realised R loss.

#### Target and time exit

The exact exit plan must be versioned. Recommended first version:

- Exit 50% at T1.
- On T1, move the remaining stop to breakeven or use a documented trailing rule.
- Exit the remainder at T2 or its trailing-stop rule.
- Exit any remaining quantity after a maximum holding period, initially 20 sessions, at the close minus sell slippage.

If the existing application instead uses a full exit at T2, preserve that behaviour for the first historical version. Do not introduce a different historical exit model from the live plan.

#### Costs

Initial implementation must at least support configurable:

- Buy and sell slippage.
- Brokerage.
- STT.
- Exchange transaction charges.
- GST.
- SEBI charges.
- Stamp duty.

Store gross and net P&L/R separately. Display net, after-cost metrics by default.

#### Filters and macro context

Historical replay should apply only filters that can be reconstructed reliably for the historical date.

- Trend, indicator, volume, price, ATR, relative-strength, and sector-index history are suitable.
- Historical earnings calendar, precise institutional data, news sentiment, and event data may be unavailable or unreliable. Mark these fields as `not_reconstructed`; do not pretend they passed.
- An evidence result with critical unavailable filters should carry `coverage_notes` and possibly an `incomplete_context` flag.

## Independent trade episodes and de-duplication

### Problem

During one sustained uptrend, the scanner can find the same stock on many consecutive days.

Example:

```text
Day 1: ABC qualifies as PULLBACK
Day 2: ABC still qualifies as PULLBACK
Day 3: ABC still qualifies as PULLBACK
Day 4: ABC still qualifies as PULLBACK
```

Counting all four as four independent historical opportunities is misleading. They are usually the same underlying market move. A single trend could then create many correlated winners and falsely raise the win rate.

### Required rule

For each symbol and setup family, permit **one active historical episode at a time**.

1. Create an episode at the first valid signal.
2. Simulate whether its order filled and how it exited or expired.
3. Ignore later same-setup signals while the episode is active (including the wait-to-fill period and the filled trade holding period).
4. The next eligible episode can begin only after the previous episode has ended and a configurable cool-down has passed, initially five trading sessions.
5. Start a new episode immediately if the setup family changes materially, e.g. `PULLBACK` to a separately qualified `BREAKOUT`, provided portfolio rules allow it. Keep this configurable and visible.

Example result:

```text
Without de-duplication: 18 PULLBACK signals, 15 wins
With episode rule:       5 independent episodes, 3 wins
```

The card must report both values where useful:

```text
Signals observed: 18
Independent episodes: 5
```

Only independent episodes are used for win rate, expectancy, and sample-quality labels.

## Live Swing Sentinel record

### Objective

The live record evaluates actual application behaviour, not hypothetical prices.

It answers:

> When Swing Sentinel selected and managed comparable trades, did its decisions and execution behave as expected?

### Source data

Use `data/positions.csv` as the initial canonical source, plus stored entry snapshots and post-mortems where available.

For each closed record, retain or derive:

- Symbol, setup type, strategy version, entry date/time, actual entry, actual quantity.
- Planned initial stop, T1/T2, risk per share, rupee risk.
- Outcome, actual exit, net P&L, realised R, hold duration.
- Entry safety-gate snapshot, macro permission, quality score, and debate state.
- Fill type: manual, GTT/limit, market, or unknown.
- Exit type: SL, gap-through-SL, T1/T2, time exit, manual exit, prune, or unknown.

### Comparable live-trade selection

The card's live summary filters to closed trades matching as much of the following as data permits:

1. Same symbol.
2. Same setup family.
3. Same `strategy_version` or explicitly compatible version.
4. Similar macro permission/regime, when snapshots exist.

Display the matching criteria and sample count. If the same-symbol sample is too small, optionally show an additional **universe live record** for the same setup, clearly labelled as not same-stock evidence.

### Live metrics

- Completed comparable trades.
- Win rate (secondary only).
- Net expectancy in R (primary).
- Average and median realised R.
- Median hold sessions.
- T1/T2/SL/time-exit distribution.
- Worst realised R and gap-through-stop count.
- Plan adherence: entry-zone compliance, stop compliance, and exit-rule compliance where available.
- Post-mortem failure-class distribution.

### Data-quality handling

Older trades may lack snapshots. Show them as `partial` and do not use missing fields as successful passes.

Example:

```text
Live Swing Sentinel: 6 comparable trades
4 complete snapshots · 2 partial legacy records
Net expectancy: +0.18R (low confidence)
```

## Result schema and storage

### Strategy version

Every calculated trade plan and evidence record must include a deterministic `strategy_version`.

The version must change when any historical-result-affecting rule changes, including:

- Setup detection conditions.
- Entry-zone formula or maximum wait rule.
- Stop formula.
- Target/trailing/time-exit rule.
- Costs or slippage policy.
- Hard/soft gate definitions included in the simulation.
- Episode de-duplication or cool-down rule.

Suggested version design:

```text
strategy_version = "v1.0-" + SHA256(canonical JSON of backtest rule config)[0:12]
```

This is safer than relying solely on a manually edited version string.

### Historical cache file

Suggested location:

```text
data/historical_evidence/{SYMBOL}_{SETUP}_{STRATEGY_VERSION}.json
```

Suggested schema:

```json
{
  "schema_version": 1,
  "symbol": "ABC",
  "setup_type": "PULLBACK",
  "strategy_version": "v1.0-abc123def456",
  "generated_at": "2026-07-12T16:10:00+05:30",
  "market_data_as_of": "2026-07-11",
  "coverage": {
    "start": "2025-07-12",
    "end": "2026-07-11",
    "sessions": 250,
    "warmup_sessions": 250,
    "sample_quality": "weak",
    "coverage_notes": []
  },
  "rules": {
    "entry_mode": "limit",
    "max_wait_sessions": 5,
    "max_hold_sessions": 20,
    "cooldown_sessions": 5,
    "same_bar_policy": "stop_first",
    "cost_model_version": "india-cash-v1"
  },
  "summary": {
    "signals_observed": 18,
    "independent_episodes": 5,
    "filled": 4,
    "not_filled": 1,
    "wins": 2,
    "losses": 2,
    "net_expectancy_r": 0.24,
    "median_realised_r": 0.11,
    "median_hold_sessions": 8,
    "worst_realised_r": -1.7,
    "gap_through_stop_count": 1,
    "max_drawdown_r": -2.1
  },
  "episodes": [],
  "status": "complete",
  "stale_reason": null
}
```

### Live evidence response

Live evidence can be built on demand from `positions.csv` initially; cache it if the dataset grows.

```json
{
  "schema_version": 1,
  "symbol": "ABC",
  "setup_type": "PULLBACK",
  "strategy_version": "v1.0-abc123def456",
  "generated_at": "2026-07-12T16:10:00+05:30",
  "matching": {
    "same_symbol": true,
    "same_setup": true,
    "strategy_compatible": true,
    "macro_match": "partial"
  },
  "summary": {
    "completed": 4,
    "complete_snapshots": 3,
    "partial_legacy_records": 1,
    "net_expectancy_r": 0.11,
    "win_rate": 0.5,
    "median_hold_sessions": 9,
    "worst_realised_r": -1.4,
    "gap_through_stop_count": 1
  },
  "status": "low_confidence"
}
```

## API design

### Card data endpoint

Extend the existing Analysis-card payload, or add a read-only endpoint:

```text
GET /api/stocks/{symbol}/evidence?setup=PULLBACK
```

Response contains:

- `historical`: complete cached result, queued/stale status, or error state.
- `live`: calculated/cached live record.
- `actions`: whether refresh/run is available and why a run is queued.
- `strategy_version` and data timestamps.

The endpoint should return quickly. It must not synchronously download a year of data during normal card rendering.

### Run endpoint

```text
POST /api/stocks/{symbol}/evidence/recalculate
```

Request:

```json
{
  "setup_type": "PULLBACK",
  "reason": "manual" 
}
```

The endpoint should queue/coalesce work and return `202 Accepted` when a computation is required:

```json
{
  "status": "queued",
  "job_key": "ABC_PULLBACK_v1.0-abc123def456",
  "estimated_source": "historical_daily_ohlcv"
}
```

Avoid parallel duplicate jobs for the same cache key.

## Keep & Refresh integration

### What the existing workflow should do

Keep & Refresh already re-evaluates open/watchlist items. Extend it to:

1. Recompute the current card's market data, entry zone, stop, targets, safety gates, macro permission, quality score, and sizing.
2. Compare the card's current `setup_type` and `strategy_version` against its cached historical evidence.
3. Mark historical evidence stale or queue a refresh only when the rules below require it.
4. Rebuild live evidence when positions/trade outcomes change.
5. Never delay the keep/refresh scan while a historical backtest runs; it is background work.

### Recalculate historical simulation: required triggers

Queue or run a historical simulation when any of these occurs:

| Trigger | Action | Reason |
|---|---|---|
| No cache exists for `symbol + setup_type + strategy_version` | Queue immediately for eligible cards; show `queued`. | First evidence request. |
| Strategy version changes | Mark all matching cache entries stale and queue lazily/on next eligible card. | Rules changed; prior results are not comparable. |
| Setup family changes, e.g. `PULLBACK -> BREAKOUT` | Queue a result for the new setup family. | Different historical behaviour and rules. |
| Market-data end date advances to a new completed trading session | Queue once after market close or next morning. | Add the newly completed bar. |
| Cache is older than one completed trading session | Queue refresh. | Normal daily freshness. |
| User clicks “Recalculate historical evidence” | Queue immediately, with per-symbol rate limit. | Manual investigation. |
| Existing cache has failed/incomplete data and its retry cooldown elapsed | Queue retry. | Recover from temporary source failures. |

### Do not recalculate full historical simulation for these events

Do **not** rerun the full one/two-year backtest merely because today's price, entry, stop, target, quantity, or macro multiplier changed while the setup and strategy version remain the same.

For those normal intraday/current-card changes:

- Recalculate the **live trade plan**, safety/macro states, quality score, and sizing immediately.
- Reuse the cached historical rule-based evidence.
- Display the historical `market_data_as_of` date.

The exception is a material change that changes the setup family or strategy version.

### Practical schedule

| Work | Frequency | Execution path |
|---|---|---|
| Current card validation, plan, gates, sizing | Every Keep & Refresh cycle / user refresh | Existing normal scan path. |
| Live evidence summary | When positions file changes, trade closes, or card opens | Fast local aggregation/cache. |
| Historical evidence cache freshness | Once after NSE market close; additionally when a new eligible setup appears | Background job. |
| Full-universe strategy backtest | On strategy-version change and optionally weekly | Separate background/CI task; never card render. |
| Debate | On demand/top candidates, cached daily | Asynchronous; never block card render. |

### Priority rules

To control load, schedule historical jobs in this order:

1. Cards currently `BUY NOW`.
2. Cards currently `ARM LIMIT`.
3. `WATCH` cards with a strong quality score.
4. Other open/watchlist cards.
5. Manually opened/pruned cards.

Set a daily cap and a concurrency limit. Return cached evidence when the queue is busy.

## Performance and reliability requirements

### Performance rules

- Cache daily OHLCV history by symbol, not once per card computation.
- Fetch Nifty, VIX, and sector history once per run and share it across symbols.
- Store historical evidence by cache key; do not recompute when an accordion expands.
- Use background jobs and coalescing: one active job per `symbol + setup + strategy_version`.
- Render the card immediately with cached/queued status.
- Do not run an LLM debate from Keep & Refresh for every card.
- Limit default historical lookback to 12 months, expanding to 24 only for sample sufficiency.
- Preserve source timestamps and fail visibly when stale or unavailable.

### Reliability rules

- Use atomic write/replace for cache JSON files.
- Record errors in cache status; do not delete the last known good result because a refresh fails.
- Do not use future bars when calculating historical indicators, support, resistance, or macro state.
- Test no-fill, gap-through-stop, same-day stop/target, time exit, and episode de-duplication cases.
- Store raw/derived evidence sufficient to reproduce a result later.

## UI behaviour by state

### PASS

```text
PASS · ARM LIMIT
All hard gates passed. Macro: FULL_RISK.
Historical: +0.24R / 5 episodes (weak sample).
Live: +0.11R / 4 comparable trades (low confidence).
Approved: 120 shares · planned loss ₹2,880.
```

### WATCH

```text
WATCH · Wait for confirmation
Hard gates passed; Nifty AMBER and volume is marginal.
Permitted risk: 0.5% · max 60 shares.
Historical evidence is supportive but weak (8 episodes).
```

### SKIP

```text
SKIP
Blockers: sector RED; results in 2 sessions.
Historical score is not used to override blockers.
No order may be armed.
```

### INSUFFICIENT DATA

```text
INSUFFICIENT DATA
Earnings calendar and daily bar timestamp are unavailable.
Historical evidence: queued for strategy v1.0-abc123def456.
Do not buy until card validation completes.
```

## Acceptance criteria

1. Every Analysis-tab card displays one of `PASS`, `WATCH`, `SKIP`, or `INSUFFICIENT DATA`.
2. A hard safety failure always overrides quality score, debate approval, historical evidence, and sizing.
3. Historical and live summaries are visibly separated and never combined into one metric.
4. Historical simulation records `signals_observed`, `independent_episodes`, `filled`, `not_filled`, gross/net R metrics, and source coverage.
5. Consecutive same-setup signals during an active episode do not increase the independent sample count.
6. A limit-entry simulation can result in `NOT_FILLED`.
7. A gap below the stop is exited at the opening price/slippage model, not the intended stop.
8. Keep & Refresh recalculates current plan/gates/sizing frequently but does not rerun the full historical simulation for ordinary daily level changes.
9. A changed strategy version or setup type invalidates/requires a distinct historical cache result.
10. The Analysis tab renders immediately from cache or shows a queued/stale state without waiting for history downloads or debate calls.
11. The user can expand a result to inspect episode-level outcomes and all assumptions.
12. Unit tests cover the execution edge cases and cache invalidation rules.

## Suggested implementation phases

### Phase 1 — Correct evidence foundation

- Define a canonical backtest-rule configuration and deterministic strategy version.
- Extend the existing backtester with realistic entry, no-fill, stop-gap, time-exit, cost, and episode rules.
- Add historical cache read/write and a service/endpoint.
- Add basic live-record aggregation from `positions.csv`.

### Phase 2 — Analysis-card panel

- Add the compact and expanded Historical & Live Evidence panel.
- Add state badges, timestamps, sample-quality labels, and manual queue/retry action.
- Integrate with the card decision summary and sizing output.

### Phase 3 — Keep & Refresh automation

- Add stale detection and background queue logic.
- Run daily historical refreshes after market close.
- Rebuild live evidence on positions-file changes and closed outcomes.
- Add queue/load limits and observability logs.

### Phase 4 — Validation and calibration

- Compare historical evidence to forward/live results by strategy version.
- Add dashboard reporting for calibration: score band, setup, regime, and evidence sample quality versus realised R.
- Tighten or remove factors only after sufficient independent live observations.

## Non-advisory note

This feature is for research, journaling, and risk discipline. Historical and simulated results do not guarantee future returns. The user remains responsible for trade decisions, execution, tax treatment, and compliance with broker/exchange rules.
