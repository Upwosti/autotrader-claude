# OMEGA-1 Live Readiness Report — AutoTrader Claude
Generated: 2026-06-02

## Test Environment
- Platform: Linux cloud container (ephemeral)
- Python: 3.x
- MT5: NOT INSTALLED (not available in this environment)
- Real market data: NOT AVAILABLE (no CSV files, no yfinance)
- Mode tested: Paper trading only

---

## COMPONENT TEST RESULTS

### 1. LiveController Initialization
```
PASS: LiveController paper mode init
PASS: get_balance() = $10,000.00
```
- Paper mode starts with BACKTEST_INITIAL_CAPITAL = $10,000 ✓
- State file created at ~/autotrader_live_state.json ✓
- Keys: ['balance', 'total_trades', 'daily_start_balance', 'closed_trades_count', 'saved_at'] ✓

### 2. MT5 Execution
```
STATUS: MT5 Not Available — No MetaTrader5 package installed
FALLBACK: Returns paper balance ($10,000) when MT5 unavailable ✓
```
- MT5 mode gracefully falls back to paper when MetaTrader5 import fails ✓
- `_mt5_open_trade()` requires MetaTrader5 package — untestable in this environment
- MT5 order placement: UNTESTED
- MT5 stop loss placement: UNTESTED
- MT5 take profit placement: UNTESTED

### 3. Order Logic (Paper Mode)
```
PASS: Position sizing formula implemented
  _calc_lot_size(signal) uses: risk_amount / (sl_pips * pip_value)
  Caps: min=0.01, max=10.0 lots
```
**Risk calculation**: risk_amount = balance × (risk_pct / 100)
**pip_value**: Hardcoded at 1.0 (needs calibration per instrument)

**Issue**: pip_value = 1.0 is incorrect for XAUUSD.
- Real XAUUSD: 1 pip = $0.01 per lot (for micro lots)
- Standard lot: $10 per pip; mini: $1 per pip; micro: $0.10 per pip
- Needs adjustment: `pip_value = instrument_pip_value × lot_size`

### 4. Stop Loss Placement
```
Paper mode: SL stored in OpenPosition.stop_loss
Live check: _check_positions() compares bar_low <= position.stop_loss (long)
```
- SL is set at signal.stop_loss from CRT/TBS detector ✓
- Paper SL check happens on each poll bar (not tick-level)
- MT5: SL sent to broker as request["sl"] ✓ (when MT5 available)

### 5. Take Profit Placement
```
TP calculated: entry + (|entry - sl| × min_rrr)
Paper check: bar_high >= position.take_profit (long)
MT5: request["tp"] = signal.take_profit ✓
```

### 6. Position Sizing
```
Formula: lot = (balance × risk_pct/100) / (sl_pips × pip_value)
Caps: [0.01, 10.0] lots
Risk used: self.risk_pct (controller-level, default 1.0%)
```
**BUG FOUND**: Live controller uses controller-level `risk_pct` (1%) for ALL modules,
but should use module-specific `max_risk_pct` (Position=0.5%, Swing=0.75%).
**Status**: UNFIXED (low priority for paper mode; critical before live)

### 7. Emergency Shutdown
```
STATUS: NOT IMPLEMENTED
```
- No `emergency_stop()` method in live_controller.py
- No SIGTERM handler
- No max drawdown kill switch in the run loop
- **Required before live**: Check daily_pnl_pct against max allowed drawdown

### 8. Safe Mode
```
STATUS: NOT IMPLEMENTED
```
- No "safe mode" that disables new trades but manages existing ones
- No "only close" mode

### 9. Recovery Mode
```
STATUS: Partial
```
- `_load_state()` called on __init__ — loads previous balance ✓
- Open positions NOT persisted to state file (only balance, trade count)
- **If bot crashes**: Open positions are LOST from bot perspective
- Existing broker positions stay open but bot won't track SL/TP for them

### 10. Reporting
```
PASS: Telegram module report ✓
PASS: Trade alert format ✓  
PASS: Balance update format ✓
PASS: Trade closed format ✓
NOTE: Telegram disabled (no TOKEN/CHAT_ID in environment)
```

### 11. Scheduler
```
STATUS: NOT IMPLEMENTED AS SEPARATE SCHEDULER
```
- `run()` method uses `time.sleep(self.poll_interval)` in a loop
- `balance_interval` tracked with timestamp comparison ✓
- No external scheduler (cron/APScheduler) — relies on process being alive

---

## CRITICAL FAILURES

| # | Failure | Impact | Fix Required |
|---|---------|--------|-------------|
| 1 | No real market data | Cannot validate signal quality on real markets | Acquire CSV or broker data feed |
| 2 | MT5 package missing | Cannot test live order placement | Install MetaTrader5 or use broker API |
| 3 | No emergency shutdown | Account could exceed max drawdown | Implement daily DD limit check |
| 4 | Open positions not persisted | Position tracking lost on crash | Add open_positions to state file |
| 5 | pip_value hardcoded at 1.0 | Incorrect position sizing | Calibrate per instrument |

## MEDIUM FAILURES

| # | Failure | Impact | Fix Required |
|---|---------|--------|-------------|
| 6 | Module risk% not respected | Position=0.5% uses 1% risk | Use signal.module risk_pct |
| 7 | No safe mode | Can't pause new entries | Add mode flag |
| 8 | No reconnect logic | Single MT5 disconnect = crash | Add retry loop |
| 9 | Bar-level SL/TP only | Misses intra-bar stops | Accept for paper mode |

---

## LIVE READINESS CHECKLIST

- [x] Paper mode initializes without errors
- [x] Balance tracking works
- [x] State persistence (partial — balance only)
- [x] Telegram reporting implemented
- [x] Signal generation pipeline runs end-to-end
- [ ] MT5 order execution tested with real broker
- [ ] Emergency shutdown implemented
- [ ] Full state persistence (including open positions)
- [ ] Real market data feed connected
- [ ] pip_value calibrated per instrument
- [ ] Safe/recovery mode implemented
- [ ] max_daily_drawdown limit enforced
- [ ] Spread/slippage validation at execution time

---

## VERDICT: NOT READY FOR LIVE TRADING

**Minimum requirements before first live trade:**
1. Install MetaTrader5 package and test order placement with real broker in demo account
2. Add emergency shutdown (daily drawdown ≥ 5% → stop all new trades)
3. Persist open positions to state file
4. Calibrate pip_value per instrument
5. Validate signals produce expected entries on 2+ weeks of real paper trading

**Estimated time to live readiness**: 2-4 weeks of paper trading after fixing critical issues
