# OMEGA-1 48-Hour Demo Challenge Report — AutoTrader Claude
Generated: 2026-06-02

## Status: CANNOT RUN — PREREQUISITES MISSING

This report documents the intended 48-hour demo challenge and the blocking issues.

---

## BLOCKING PREREQUISITES

| Requirement | Status | Reason |
|-------------|--------|--------|
| Real market data | MISSING | No CSV files; yfinance not installed |
| MT5 connection | MISSING | MetaTrader5 package not installed |
| Broker demo account | UNKNOWN | Not configured in environment |
| Telegram credentials | MISSING | TOKEN/CHAT_ID not set as env vars |

---

## DEMO CHALLENGE PROTOCOL (for when prerequisites are met)

### Setup
1. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in environment
2. Connect MT5 demo account (broker: any MT5 provider)
3. Download real XAUUSD CSV data for all 5 timeframes (at least 2 years)
4. Place files at `~/AutoTraderClaude/data/XAUUSD_{tf}.csv`

### Run Command
```bash
python main.py live --pair XAUUSD --interval 60 --balance-interval 3600 --risk 0.5
```

### Monitoring Metrics (per trade, all logged to ~/autotrader_logs/)

| Metric | Target | Warning Level |
|--------|--------|--------------|
| Win Rate | ≥ 60% | < 40% |
| Avg RRR | ≥ 2.5 | < 2.0 |
| Profit Factor | ≥ 1.5 | < 1.2 |
| Max Drawdown | ≤ 8% | > 5% |
| Expectancy | > 0 pips | ≤ 0 |

### Trade Log Schema
Each trade logged to `~/autotrader_logs/autotrader_YYYY-MM-DD.log`:
```
Timestamp | Module | Direction | Entry | SL | TP | RRR | Model | PDF Rule | TF Alignment | Risk% | P&L Pips | Outcome
```

### Exit Criteria
- Emergency stop if daily drawdown ≥ 5%
- Challenge ends after 48 hours OR 20+ trades (whichever comes first)
- Final report auto-generated

---

## MOCK RESULTS (Synthetic Data — For Architecture Validation Only)

Run date: 2026-06-02
Data: Synthetic XAUUSD (numpy RNG, seed=42)
Bars: 2000 LTF2 bars per module

| Module | Trades | WR | RRR | PF | Return | DD | PDF Model |
|--------|--------|----|----|-----|--------|-----|-----------|
| Position (MN/W1/D1) | 12 | 8.3% | 2.99 | 0.12 | -7.8% | 10.5% | TBS none |
| Swing (W1/D1/4H) | 5 | 0.0% | 0.00 | 0.00 | -4.9% | 4.9% | TBS none |
| Intraday (D1/4H/1H) | 11 | 9.1% | 2.00 | 0.11 | -7.8% | 9.6% | TBS none |
| Scalp (4H/1H/15M) | 10 | 0.0% | 0.00 | 0.00 | -9.6% | 9.6% | TBS none |

**VERDICT ON SYNTHETIC RESULTS**: ALL BELOW TARGET — EXPECTED.
Synthetic random walk data has no ICT structure. Results are meaningless for strategy validation.

---

## NOTES ON ENTRY REASONS

All current entries use `entry_model="none"` (fallback midpoint entry).

**Root cause**: The 3 entry models (OB/FVG/BOS) are not firing because:
1. TBS bodies_inside threshold (≥2) is too tight for the limited data window
2. The OB model requires a bearish candle body inside the CRT range — rare on random walks
3. The FVG model requires a specific gap pattern — also rare

**Expected behavior on real data**: OB and FVG models should fire 60-80% of the time.
BOS should be the remaining fallback. "none" (midpoint) should be <5% of trades.

---

## PDF COMPLIANCE CHECK (Partial)

| Rule | Status | Evidence |
|------|--------|---------|
| CRT reference candle: H/O/C/L structure | IMPLEMENTED | `crt_tbs.py:124-145` |
| TBS: bodies inside CRT range | IMPLEMENTED | `crt_tbs.py:170-171` |
| TBS: one side purged | IMPLEMENTED | `crt_tbs.py:173-174` |
| Entry Model 1: Order Block | IMPLEMENTED | `crt_tbs.py:219-225` |
| Entry Model 2: FVG | IMPLEMENTED | `crt_tbs.py:236-248` |
| Entry Model 3: BOS | IMPLEMENTED | `crt_tbs.py:251-258` |
| AMD 3-candle (3rd candle rule) | IMPLEMENTED | `crt_tbs.py:381-386` |
| 4 trading modules (Position/Swing/Intraday/Scalp) | IMPLEMENTED | `execution/trading_module.py:54-83` |
| Kill zones (London/NY) | PARTIAL | Config exists; live_controller not filtering by session |
| SMT divergence | PARTIAL | `strategy/ict_engine.py` only |
| Double purge | PARTIAL | `backtester/engine.py` only |

---

## NEXT STEPS

1. Acquire real data → re-run this challenge with real XAUUSD bars
2. After 48 hours: if WR ≥ 50% and PF ≥ 1.2 → continue paper trading
3. After 4 weeks paper: if consistent → propose live with 0.25% risk cap
