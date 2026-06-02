# ICT Strategy Blueprint — Extracted from 7 PDFs

---

## 1. Liquidity Sweep Rules

- Equal highs/lows are defined as 2 or more price touches within a 0.05% tolerance of each other.
- A sweep is confirmed when price exceeds the equal high/low level AND the wick_pct is >= 0.3 (the wick extends at least 30% of the total candle range beyond the level).
- **Bearish sweep**: price trades above equal highs and the candle closes back below → bias shifts short.
- **Bullish sweep**: price trades below equal lows and the candle closes back above → bias shifts long.
- Lookback window for level formation: 50 bars.
- Minimum touches required to call a level valid: 2.

---

## 2. BOS / MSS Rules

- **BOS (Break of Structure)**: price closes beyond the last confirmed swing high (bullish BOS) or swing low (bearish BOS).
- **MSS (Market Structure Shift)**: the first BOS that occurs after a liquidity sweep → classified as high-probability because it combines sweep + structural break.
- **Displacement**: a candle whose body is greater than 1.5× ATR(20). Displacement must accompany a valid BOS for the signal to qualify as institutional.
- Bullish BOS condition: `close > last_confirmed_swing_high`
- Bearish BOS condition: `close < last_confirmed_swing_low`
- Swing lookback half-window: 5 bars (a swing high requires 5 bars lower on each side; a swing low requires 5 bars higher on each side).

---

## 3. The 12 PD Arrays (Priority Order: Highest → Lowest)

1. **BB (Breaker Block)**: an Order Block that price previously broke through; it now acts as support (former resistance OB) or resistance (former support OB). Highest priority because price has already accepted it once.
2. **MB (Mitigation Block)**: the original OB at the origin of an inefficiency. Price returns after filling the gap, creating a second entry opportunity. Second highest because it marks untested institutional origin.
3. **OB (Order Block)**: the last opposing candle body before a strong impulse move. For a bullish OB, use the last bearish candle body before the up-move. For a bearish OB, use the last bullish candle body before the down-move.
4. **PB (Propulsion Block)**: an OB that aligns with a premium or discount zone (above 50% equilibrium = premium for sells; below 50% = discount for buys). Requires both OB conditions and zone alignment.
5. **RB (Rejection Block)**: a candle with a strong wick where `wick_pct >= 0.5` of the total candle range (high − low). Indicates institutional rejection at that level.
6. **IRB (Immediate Rebalance)**: two consecutive same-direction gaps between candles, within a tolerance of 0.1% of price. Marks rapid institutional repositioning.
7. **IFVG (Inverted FVG)**: a Fair Value Gap that was previously filled by price and subsequently flipped polarity — former supply now acts as demand and vice versa.
8. **VI (Volume Imbalance)**: a gap measured between the close of candle[i] and the close of candle[i+1], as opposed to using the wicks (high/low). Represents a zone where volume transacted but no full price range exploration occurred.
9. **LV (Liquidity Void)**: a large single-candle gap where `high − low > 2 × ATR(20)`. Signals a rapid vacuum of liquidity that price is likely to revisit.
10. **BPR (Balanced Price Range)**: two overlapping Fair Value Gaps in opposite directions within the same price area. Acts as a magnet where both imbalances compete, creating a balanced zone.
11. **FVG (Fair Value Gap)**: a three-candle pattern. Bearish FVG: `candle[i-1].high < candle[i+1].low` (gap between them). Bullish FVG: `candle[i-1].low > candle[i+1].high`. Minimum size: 2 × pip_size. Lower priority than structured blocks because it is purely a gap, not an institutional origin point.
12. **StatMap Levels**: OHLC-derived statistical reference levels calculated from the prior session or prior bar range: 25%, 50%, 75%, and 100% projections of that range. Used as confluence, not standalone entries.

---

## 4. CRT + TBS Rules (Candle Range Theory + Turtle Body Soup)

- **Reference candle**: the highest-volume candle within the last 20 bars. This candle defines the CRT range.
- **CRT range**: `reference_candle.high − reference_candle.low`.
- **TBS (Turtle Body Soup) validity conditions**:
  - At least 2 candle bodies fit entirely inside the CRT range.
  - One side of the CRT range must have been swept (price exceeded the high or low).
  - No equal highs exist inside the range (equal highs inside would indicate unresolved BSL, invalidating a bullish TBS setup).
- **Bullish TBS**: the low of the CRT range is swept → enter long after sweep confirmation.
- **Bearish TBS**: the high of the CRT range is swept → enter short after sweep confirmation.
- Minimum bodies inside: 2. If fewer than 2 bodies are inside the range, the pattern is not valid.
- Equal highs inside the range invalidate the setup entirely — they signal potential further manipulation before a clean move.

---

## 5. IRL / ERL Cycle

- **ERL (External Range Liquidity)**: liquidity resting at swing extremes — swing highs (BSL, Buy-Side Liquidity) and swing lows (SSL, Sell-Side Liquidity) that are visible on the chart.
- **IRL (Internal Range Liquidity)**: liquidity targets within the current price range — FVGs, OBs, breakers, and other PD arrays that sit between the ERL swing points.
- **The cycle**:
  1. Price sweeps ERL (takes out a swing high or low).
  2. A displacement move is created (BOS/MSS with displacement candle).
  3. Price targets the nearest IRL (FVG, OB, etc.) in the new direction.
  4. IRL is filled or reacted to → price continues or consolidates briefly.
  5. Price then targets the next ERL in the direction of the move.
- **At ERL**: high-probability reversal or continuation after sweep — trade setup zone.
- **At IRL**: expect continuation of the trend or a brief pause/consolidation before the next ERL target.

---

## 6. SMT Divergence

- **Correlated pairs used for SMT analysis**:
  - EURUSD ↔ GBPUSD (both USD-denominated, positively correlated)
  - AUDUSD ↔ NZDUSD (both commodity currencies, positively correlated)
  - XAUUSD ↔ XAGUSD (precious metals, positively correlated)
  - BTCUSD ↔ ETHUSD (crypto majors, positively correlated)
- **Bearish SMT**: Asset1 makes a new high on bar N, but Asset2 fails to make a corresponding new high on the same bar or within the lookback window → short Asset1 (it is the weaker, about to reverse).
- **Bullish SMT**: Asset1 makes a new low on bar N, but Asset2 fails to make a corresponding new low → long Asset1 (the sweep was false; Asset2 is holding, indicating real support).
- Lookback window: 20 bars.
- SMT divergence confirms the direction of a sweep or BOS — it is a confluence factor, not a standalone entry trigger.

---

## 7. Double Purge Theory

- **Classic double purge**: BSL (Buy-Side Liquidity, swing high) is swept on candle N, then SSL (Sell-Side Liquidity, swing low) is swept on a different candle M. The real directional move begins after both purges are complete.
- **One-candle double purge**: a single candle's high sweeps BSL AND the same candle's low sweeps SSL simultaneously. Both sides are cleared in one bar.
- **AMD (Accumulation-Manipulation-Distribution)**:
  - Accumulation: a tight consolidation range forms over multiple bars — price is coiling.
  - Manipulation: price purges one or both sides of the accumulation range (both BSL and SSL are taken).
  - Distribution: the actual directional trend begins, moving opposite to the final direction of manipulation.
- **Trade rule after double purge**: enter in the direction opposite to the last manipulation leg. If manipulation drove price up (sweeping BSL) and then down (sweeping SSL), the distribution move is bullish. If manipulation drove price down then up, distribution is bearish.

---

## 8. Kill Zone Rules

- **London Kill Zone**: 02:00–05:00 UTC. Confidence score bonus: +1.5 points. Highest-quality London setups form in the first 90 minutes (02:00–03:30 UTC).
- **NY AM Kill Zone**: 07:00–10:00 UTC. Confidence score bonus: +1.5 points. NY open creates the highest-volatility window of the day.
- **NY PM Kill Zone (optional)**: 13:00–16:00 UTC. No bonus applied — used for continuation only if a morning setup was not triggered.
- **Off-session rule**: trades are only taken outside kill zones if the confidence score is >= 8.0. Off-session trades receive a confidence score penalty of −0.5 (applied via the session modifier in the engine).
- **Best setup condition**: the setup must form AND trigger during the same kill zone session. A setup that forms in London but does not trigger until Asian session is invalid unless score >= 8.0.

---

## 9. Multi-Plan Entry System (ALL 4 Plans Active Simultaneously)

All four plans are evaluated on every bar. The plan with the highest confidence score that meets the threshold is executed. Plans are not mutually exclusive — if two plans both qualify, the one with the higher score takes priority.

### Plan A — Classic ICT
- **Trigger**: liquidity sweep confirmed + aligned BOS in the same direction + price returning to a PD array.
- **Sweep-BOS alignment**: the sweep direction must match the BOS direction (bearish sweep → bearish BOS; bullish sweep → bullish BOS).
- **Entry**: enter at the nearest PD array in the trade direction. Priority: BB > MB > OB > PB.
- **Stop Loss**: beyond the sweep level + 5 pips buffer.
- **Take Profit**: nearest key liquidity target (opposing ERL) OR minimum 2:1 RRR, whichever is farther.

### Plan B — Double Purge
- **Trigger**: both BSL and SSL have been swept (classic or one-candle). Enter in the direction opposite to the final manipulation leg.
- **Confidence bonus**: +1.0 added to the confidence score (highest-confidence plan by design).
- **Stop Loss**: beyond the second purge level + 5 pips buffer.
- **Take Profit**: opposing ERL level OR minimum 2:1 RRR.

### Plan C — BOS / MSS Alone
- **Trigger**: BOS confirmed with displacement candle (body > 1.5× ATR(20)). No liquidity sweep required.
- **Entry**: wait for price to retest the nearest PD array in the BOS direction before entering.
- **Score note**: no sweep means missing the +2.0 sweep score component — implicitly lower confidence. Minimum score threshold still applies.
- **Stop Loss**: beyond last confirmed swing + 5 pips.
- **Take Profit**: next swing high/low as target OR minimum 2:1 RRR.

### Plan D — HTF Bias + PD Array
- **Trigger**: higher timeframe (daily or weekly) shows a clear bullish or bearish BOS. On the lower timeframe, price enters an OB, FVG, or IRB aligned with the HTF bias.
- **No sweep or BOS required on LTF** — HTF bias substitutes.
- **Entry**: when price touches the LTF PD array (OB, FVG, or IRB) that aligns with HTF direction.
- **Stop Loss**: below/above the PD array + 5 pips.
- **Take Profit**: opposing HTF liquidity level (next ERL on HTF) OR minimum 2:1 RRR.

---

## 10. Confidence Score System (0–10)

Each factor adds to or subtracts from the base score of 0.0. Trades are only executed when the final score >= 6.0 (configurable via `confidence_threshold` in `StrategyParams`).

| Factor | Score Change |
|---|---|
| Liquidity sweep confirmed | +2.0 |
| BOS present | +2.0 |
| PD array quality: OB, BB, MB, or PB | +2.0 |
| PD array quality: FVG, IRB, IFVG, LV, or BPR | +1.5 |
| PD array quality: RB or VI | +1.0 |
| Kill zone active (London or NY AM) | +1.5 |
| HTF bias aligned with trade direction | +1.5 |
| Displacement candle present | +1.0 |
| Strong wick (>= 50% of candle range) | +0.5 |
| Double purge confirmed | +1.0 |
| SMT divergence confirms direction | +1.0 |
| CRT / TBS setup present | +0.5 |
| IRL/ERL cycle aligned with trade | +0.5 |
| Key liquidity / DOL (Draw on Liquidity) identified | +0.5 |
| Wide spread (exceeds max spread threshold) | −1.0 |
| News event within 30-minute window | −1.0 |
| DXY conflict (XAUUSD only, when DXY trend opposes trade) | −2.0 |

- **Minimum threshold to execute**: 6.0 (default). Configurable.
- **Maximum score**: theoretically uncapped, but practically bounded by available confluence factors.
- **Off-session override**: if score >= 8.0, trades may be taken outside kill zones.

---

## 11. Risk Management Rules

- **Max risk per trade**: 1% of account equity (configurable via `RISK_PER_TRADE_PCT`).
- **Minimum RRR**: 2:1 risk-to-reward ratio. Any setup where the structural target does not allow at least 2:1 is skipped.
- **Max daily drawdown**: 3% of account equity. Trading halts for the remainder of the session once this is reached.
- **Max concurrent trades**: 3. Once 3 positions are open, no new trades are entered regardless of score.
- **Position sizing formula**: `position_size = risk_amount / (stop_distance_pips × pip_value)` where `risk_amount = account_balance × (risk_pct / 100)`.
- **Scale-out rule**: close 50% of position at 1:1 RRR, move stop loss to breakeven on the remainder, let the trailing 50% run to full target.

---

## 12. HTF Bias Determination

- **Weekly bias**: determined by the most recent BOS direction on the weekly chart. Bullish weekly BOS → bullish weekly bias. Bearish weekly BOS → bearish weekly bias.
- **Daily bias**: determined by the most recent BOS direction on the daily chart.
- **Aligned bias**: when daily and weekly BOS directions agree → strong bias. Full position size permitted. HTF score bonus of +1.5 applies.
- **Conflicting bias**: when daily and weekly BOS directions disagree → use the daily bias as the active direction, but reduce position size to 50% of normal. HTF score bonus does not apply in full conflict.
- **Bias update rule**: bias is updated only when a new confirmed BOS forms on the respective timeframe. Between BOS events, the prior bias remains active.

---

## 13. Consolidation Filter

- **Skip condition**: do not enter a trade if `ADR < 0.3 × ATR(20)`. The market is in a tight, low-range environment where ICT setups lack the momentum required.
- **ADR (Average Daily Range)**: calculated as the simple average of the high-minus-low range over the last 5 bars on the active timeframe.
- **ATR period**: 20 bars (standard ICT convention for measuring recent volatility).
- **Application**: the filter is evaluated before scoring begins. A market that fails the consolidation filter receives no score and produces no trade signal for that bar.

---

## 14. Session Transition Rules

- **London close (10:00 UTC)**: if a position is open, reduce position size by 50% if the trade has not yet reached 1:1 RRR. Full exit if the trade is at a loss and London close occurs.
- **NY open (13:30 UTC)**: evaluate for trend continuation in the existing direction or a session reversal. A new setup that contradicts a London position is a signal to exit the London trade and reassess.
- **Asian session (22:00–02:00 UTC)**: treat as accumulation only. Avoid initiating new directional trades during this window unless an extreme ERL sweep occurs with a score >= 8.0. Asian session is used to identify the range that London or NY will likely manipulate.

---

## 15. Trade Invalidation Rules

A trade setup is immediately invalidated and removed from consideration when any of the following occur:

1. **Stop loss hit**: the trade is closed and the setup is over. No re-entry on the same setup.
2. **Session ends without trigger**: if the kill zone closes and price never reached the entry PD array, the setup is cancelled. Do not hold setups across sessions unless HTF bias is exceptionally clear and score >= 8.0.
3. **Opposing BOS forms before entry**: if a BOS in the opposite direction of the planned trade forms before the entry trigger is hit, the setup is invalid. Market structure has changed.
4. **Spread exceeds maximum**: if the broker spread exceeds `MAX_SPREAD_PIPS` for the pair, wait for spread normalization. Do not enter with excessive spread.
5. **News event within 30 minutes**: if a high-impact news event is scheduled within 30 minutes of the intended entry time, hold entry until after the release and reassessment. Apply the −1.0 news penalty to re-scored setups post-news.
