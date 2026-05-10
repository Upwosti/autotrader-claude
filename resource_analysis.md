# Resource Analysis — AutoTrader Claude
Generated: 2026-05-10 | Engine: v5.0 | Iter: 92,700+ | XAUUSD WR: 81.1%

## Current RAM Usage

| Module | Estimated RAM | Notes |
|---|---|---|
| Python process (engine) | ~128 MB | Walk-forward + all pair data loaded |
| yfinance data cache | ~30 MB | OHLC for 19 pairs, daily bars |
| ML ensemble models | ~20 MB | Pattern classifier + LSTM loaded at startup |
| JSON state files | ~5 MB | All local_db/*.json in memory |
| **Total** | **~183 MB** | Well within 6 GB target |

**Status: HEALTHY** — RAM usage is ~183 MB vs 6 GB target. No optimization needed currently.

## Current CPU Usage

| Task | Duration | Frequency | Notes |
|---|---|---|---|
| Walk-forward backtest | 2–3s | Per iteration | 5-fold, all costs |
| Data refresh (yfinance) | 5–10s | Once at startup | 19 pairs |
| ML inference | <0.1s | Per signal | LSTM + pattern classifier |
| Telegram send | <0.5s | Scheduled | Non-blocking |
| GitHub sync | 2–5s | Every 10 iters | Git commit + push |

**Status: HEALTHY** — CPU is dominated by WF backtest (2-3s/iter, ~30-40% CPU utilization).

## Identified Memory Leaks

None confirmed. The following warrant monitoring:

1. **evolution_log.json** (currently 440 KB) — grows with every iteration. Rotate at 50 MB.
2. **local_db/trades.json** (currently 293 KB) — grows with simulated trade history.
3. **reports_output/** — JSON backtest reports accumulate per run. Clean weekly (keep last 500).

## Duplicate Schedulers

| Scheduler | Location | Overlap |
|---|---|---|
| Built-in (canonical) | run_forever.py | 30min/2h/08:00/09:00/20:00/22:00/03:00 |
| Background daemon | evolution/scheduled_check.py | 30min/2h/6h/24h + UTC tasks |
| APScheduler | core/scheduler.py | 1h scan/15min FTMO/Sunday review |

**Fix:** run_forever.py scheduler is canonical. `scheduled_check.py` and `core/scheduler.py` tasks overlap and create double-execution risk. Consolidate into run_forever.py.

## Redundant DataFrame Copies

1. `walk_forward.py` — calls `_add_indicators(daily_df)` then slices `full_df.iloc[...]` — slices are views, not copies. OK.
2. `backtester/engine.py` — may copy full OHLC df per strategy run. Review if monthly backtest uses this.
3. `backtester/data_loader.py` — caches full history in memory dict. Acceptable for current data sizes.

## Bloated Calculation Chains

1. **ICT scorer in mutation loop** — FIXED: removed `use_ict_filter` from PARAM_SPACE.
2. **Monthly backtest (912 WF runs)** — FIXED: moved to 03:00 UTC scheduled slot.
3. **yfinance per-pair download** — could be batched. Currently sequential, acceptable.

## Scheduler/Cron Issues Fixed This Session

- `use_ict_filter` removed from mutation candidates — prevented 10+ minute backtests
- Monthly backtest moved from `__main__` auto-start to 03:00 UTC only
- Single watchdog + engine process confirmed (venv launcher chain is normal Windows behavior)

## Storage

| Path | Size | Growth Rate | Action |
|---|---|---|---|
| local_db/evolution_log.json | 440 KB | ~50 KB/hour | Rotate at 50 MB |
| local_db/system_state.json | 3.1 MB | Slow | Monitor |
| local_db/engine_state.json | 5.8 KB | Stable | OK |
| logs/engine_2026-05-*.log | Growing | ~1 MB/day | Keep 30 days |
| reports_output/ | ~74 files | Per run | Clean old runs weekly |
| data/ (CSV cache) | 526 KB | Static | OK |
| venv/ | ~440 MB | Static | OK |

## Performance Gains from This Session

| Fix | Before | After |
|---|---|---|
| ICT filter stall | 10+ min per mutation | 0 (excluded from mutations) |
| Monthly backtest GIL | Blocked main loop | 03:00 UTC only |
| Multiple engine processes | 3× engines competing | 1 watchdog + 1 engine |
| XAUUSD WR | 69.2% (baseline) | 81.1% (iter 92,700) |

## Recommendations

1. ✅ Archive `database/postgresql_client.py`, `database/redis_client.py`, `ml/reinforcement_agent.py`, `evolution/autonomous_loop.py`
2. ✅ Create `analytics/momentum_engine.py` (Phase 2)
3. ✅ Create `execution/smart_exit_engine.py` (Phase 2)
4. ✅ Create `risk/intelligent_sl_engine.py` (Phase 2)
5. ✅ Create `data/autotrader.db` SQLite schema
6. ✅ Generate monthly HTML reports Jan 2022 → May 2026
7. ✅ Create `reporting/notion_sync.py`
8. ⚠️ Consolidate duplicate schedulers into run_forever.py
9. ⚠️ Add evolution_log.json rotation at 50 MB
