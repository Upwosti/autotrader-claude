# OMEGA-1 Truth Audit — AutoTrader Claude
Generated: 2026-06-02

## Audit Scope
Full system review of backtester, live engine, signal generation, evolution, and reporting.
Evidence cited with file:line references.

---

## VERDICT: NOT READY FOR LIVE TRADING

---

## CRITICAL ISSUES

### 1. Synthetic Data Is the Default — All Backtests Are Against RNG Noise
**File**: `backtester/data_loader.py:112`, `backtester/module_backtester.py:166`

- `load()` defaults to `synthetic_fallback=True`
- yfinance not installed in this environment (`ModuleNotFoundError`)
- No real CSV data files present at `~/AutoTraderClaude/data/`
- **Result**: Every backtest generates price data via `numpy.random.seed(42)`. Win rates of 8-20% are meaningless against Gaussian random walks.

**Evidence**:
```
[WARNING] CSV not found: /root/AutoTraderClaude/data/XAUUSD_4H.csv
[ERROR] yfinance error: No module named 'yfinance'
[INFO] Generated 8000 synthetic bars for XAUUSD 15M
```

### 2. Evolution Optimizes Against Noise, Not Market Structure
**File**: `evolution/module_evolver.py:183-214`

- 200 iterations per module, each testing on fresh synthetic data
- `PARAM_RANGES` includes `crt_range_factor` (just added) — but synthetic data has no real CRT structure
- Parameters that "improve" composite score are fitting the RNG, not market dynamics
- No holdout test after evolution — parameters never validated on unseen data

**Impact**: Evolved parameters will likely underperform on real market data vs default parameters.

### 3. CRT/TBS Direction Bug — Was Setting SL on Wrong Side of Entry
**File**: `strategy/crt_tbs.py:191` (FIXED in this session)

- `detect_tbs()` passed `direction="bullish"/"bearish"` to `_best_entry_model()`
- `_best_entry_model()` checked for `"long"/"short"` — strings never matched
- Fallback SL for "long" used `(crt.high + buf)` instead of `(crt.low - buf)`
- All long trades had SL ABOVE entry → immediate stop-out → 0% win rate

**Status**: FIXED. Long trades now correctly show SL below entry.

### 4. Legacy Backtester Uses Single-Bar Outcome Simulation
**File**: `backtester/engine.py:343-372`

- Trade outcome decided from ONE next bar only
- If neither SL nor TP hit on that bar, trade closes at next close ("open")
- This is mathematically wrong: real swing/position trades resolve over many bars
- The module backtester was fixed (multi-bar up to 60-120 bars) but `engine.py` still uses single-bar

**Impact**: `engine.py` win rates are inflated for lower-timeframe modules; zero resolution for position trades.

### 5. Look-Ahead Bias Risk in engine.py
**File**: `backtester/engine.py:105-165`

- `_precompute_signals()` pre-computes liquidity sweeps, BOS, PD arrays on the **full** dataset
- Then iterates bar-by-bar using `known_pd[:pi]` (growing slice)
- Pre-computation sees ALL bars before iterating — if sweep detection uses future bars to confirm, bias exists
- `pd_arrays.py` not audited; confirming bias would require tracing that file

**Risk**: Medium-high. Walk-forward stability score would be artificially high.

---

## MEDIUM ISSUES

### 6. Backtest Commission/Slippage Modeling Is Too Optimistic
**File**: `config.py:167-168`

- Commission: 0.05% per side (0.1% round-trip)
- Slippage: 1.0 pip fixed
- Real XAUUSD execution on ICT timeframes: 0.3-1.0 pip spread + 0.5-1.5 pip slippage
- Model underestimates friction by ~30-50%, inflating win rate

### 7. Walk-Forward Validation Is Insufficient
**File**: `backtester/engine.py:490-505`

- Only 3 time segments; ~33 trades per segment on typical backtests
- High variance with small samples — stability score is noisy
- No proper rolling walk-forward with train/test splits

### 8. HTF Bias Uses Price Slope, Not Market Structure
**File**: `execution/trading_module.py:133-147`

- `_htf_bias()`: slope of last 20 bars vs ATR
- Does not use BOS/sweep detection on HTF (which is the ICT method)
- Slope can be flat across a rally; can misidentify direction during consolidation

### 9. Live Controller Not Yet Battle-Tested
**File**: `execution/live_controller.py`

- Paper mode implemented, MT5 mode implemented
- Paper mode never run against real price feed
- Position check logic uses bar-level SL/TP only (no real-time tick data)
- State persistence to JSON file — no atomic writes, could lose state on crash

### 10. AMD Pattern Requires Exact i+2 Alignment
**File**: `strategy/crt_tbs.py:63-64, 382`

- `manip_index` must equal `crt_index + 2` exactly
- Real data often has 1-2 bar gaps (weekends, sessions)
- Misses valid AMD patterns where manipulation is at i+3 or i+4

---

## MINOR ISSUES

### 11. Two Sources of Kill Zone Config
- `config.py:35`: `LONDON_KILL_ZONE` dict
- `config.py:75-78`: `StrategyParams.london_start/end` int fields
- No guarantee they stay in sync

### 12. Profit Factor Is Infinite When No Losses
- `module_backtester.py:309`: `float("inf") if gross_profit > 0 else 0.0`
- Telegram report and evolution scoring cap at 5.0 but the raw value is inf
- Can cause JSON serialization errors

### 13. Position Sizing Fixed at 1%
- `module_backtester.py:315`: `risk_pct = 0.01` hardcoded
- Does not respect `TradingModuleConfig.max_risk_pct` (Position module uses 0.5%)
- Position module should risk 0.5% but currently risks 1%

### 14. Equal-Highs Check Can Produce False Positives
- `crt_tbs.py:478-484`: O(n²) comparison with tolerance
- On synthetic data with tight pip range, tolerance=5 pips catches unrelated highs as "equal"
- Blocks valid high-probability TBS setups

---

## POSITIVE FINDINGS

1. **CRT+TBS multi-timeframe architecture is logically correct** — all 5 ICT TF pairs implemented
2. **AMD 3-candle pattern implementation matches ICT description** — exactly the 3rd candle rule
3. **4 trading modules run independently** — correct non-blocking architecture
4. **R-multiple equity curve is realistic** — capped at ±5R per trade
5. **Telegram reporting is complete** — all 7 message types implemented
6. **Evolution composite score balances 4 metrics** — not just win rate
7. **Monte Carlo and walk-forward exist** (even if imperfect)
8. **Paper trading mode avoids real capital risk** ✓

---

## REQUIRED FIXES BEFORE LIVE

| Priority | Fix | Effort |
|----------|-----|--------|
| P0 | Acquire real XAUUSD CSV data for all 5 timeframes (≥2 years) | Data sourcing |
| P0 | Disable synthetic fallback during evolution | 5 min |
| P1 | Walk-forward split: train on 70%, validate on 30% | 2 hours |
| P1 | Fix engine.py to use multi-bar outcome simulation | 1 hour |
| P1 | Validate live_controller signal matches backtester | 4 hours |
| P2 | Increase commission + slippage to realistic levels | 15 min |
| P2 | HTF bias using BOS structure instead of slope | 1 hour |
| P2 | Fix Position module risk% to 0.5% | 5 min |
| P3 | AMD pattern: allow i+2 through i+4 | 30 min |
| P3 | Atomic state file writes | 30 min |

---

## CONCLUSION

The system is a **research prototype** with correct ICT architecture but no validated edge on real market data.

**Path to live readiness:**
1. Connect real data (CSV or broker feed)
2. Run walk-forward backtest on real data WITHOUT evolving
3. If profitable on holdout data → begin paper trading for 4 weeks
4. If paper results match backtest within ±20% → live with max 0.25% risk/trade
5. Scale risk only after 3 consecutive profitable months
