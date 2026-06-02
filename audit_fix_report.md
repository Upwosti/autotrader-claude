# Codebase Audit & Fix Report
Generated: 2026-06-02

## Status Summary
- Total files audited: 11
- Issues found: 12
- Issues fixed (in this report): 0 (findings only — no automated edits applied)
- Action items remaining: 12

---

## 1. Import Issues

### 1.1 `main.py` — Windows-only log path hardcoded
**File:** `autotrader_claude/main.py`, line 27  
The `loguru` file sink uses a Windows absolute path:
```python
"C:\\AutoTraderClaude\\logs\\autotrader_{time:YYYY-MM-DD}.log"
```
This will raise `FileNotFoundError` on Linux/macOS. The correct value is already defined in `config.py` as `LOG_FILE` and should be used here instead.

### 1.2 `reports/evolution_report.py` — Windows-only `REPORTS_DIR`
**File:** `autotrader_claude/reports/evolution_report.py`, line 12  
`REPORTS_DIR = "C:\\AutoTraderClaude\\reports_output"` — hardcoded Windows path. Will fail on Linux.

### 1.3 `reports/final_report.py` — Windows-only `REPORTS_DIR`
**File:** `autotrader_claude/reports/final_report.py`, line 14  
Same issue as above: `REPORTS_DIR = "C:\\AutoTraderClaude\\reports_output"`.

### 1.4 `reports/mini_report.py` — Windows-only `REPORTS_DIR`
**File:** `autotrader_claude/reports/mini_report.py`, line 13  
Same issue: `REPORTS_DIR = "C:\\AutoTraderClaude\\reports_output"`.

### 1.5 `config.py` — `DATA_DIR` and `LOG_FILE` use `os.path.expanduser` (correct) but report modules do not
`config.py` defines cross-platform paths using `os.path.expanduser("~")`. The three report modules bypass this entirely with Windows absolute strings.

**Fix for all three report modules and `main.py`:**
```python
import os
REPORTS_DIR = os.path.join(os.path.expanduser("~"), "AutoTraderClaude", "reports_output")
```
In `main.py`, replace the hardcoded log path with `config.LOG_FILE`.

### 1.6 `evolution/analyzer.py` — `anthropic` library version mismatch risk
`requirements.txt` pins `anthropic==0.28.0`. The current Anthropic SDK message creation API used in `analyzer.py` is compatible with 0.28, but `config.py` sets `CLAUDE_MODEL = "claude-opus-4-8"` — a model ID that did not exist in 0.28-era releases. This will raise an API error at runtime. Update `requirements.txt` to `anthropic>=0.40.0` and validate the model name against the Anthropic model list.

---

## 2. Duplicate Modules / Schedulers

### 2.1 Dual scheduler packages with no scheduler wiring
`requirements.txt` lists both `schedule==1.2.2` and `APScheduler==3.10.4`. Neither is imported or used anywhere in the audited source files. There is no scheduler module in the codebase. Email and report dispatch happens ad hoc inside `optimizer.py` via `_send_alert()` and `_check_milestones()`, with no time-based scheduling.

If scheduled emails are required (see Section 3), exactly one scheduler should be chosen and wired up. Recommend retaining APScheduler (more robust, supports cron triggers) and removing the `schedule` dependency.

### 2.2 No duplicate engine instances found
A single `Optimizer` instance is created in `main.cmd_evolve`. No duplicate engines or schedulers were found running concurrently.

---

## 3. Email System

**Current state:** Email is sent only as a side-effect of evolution events via `Optimizer._send_alert()`. There is no time-based scheduling. The five allowed email types are not enforced.

**Allowed email schedule (target state):**
1. 08:00 UTC — Daily Evolution summary
2. 09:00 UTC — Daily Report (backtest/performance snapshot)
3. Sunday 00:00 UTC — Weekly Audit
4. Month start 00:00 UTC — Monthly Summary
5. On-demand — Critical Alerts only (overfitting flag, new best strategy, errors)

**Current gaps:**
- Types 1–4 (time-triggered) are entirely absent.
- Type 5 (critical alerts) partially exists but fires for non-critical events too (e.g., `iteration_complete` fires an email on every single baseline run).
- The `_send_alert` method in `optimizer.py` sends both Telegram and email for every alert type indiscriminately. Non-critical events should be Telegram-only; email should be reserved for the five types above.

**Recommended fix:** Create `autotrader_claude/alerts/scheduler.py` using APScheduler with five jobs wired to the five allowed types. Update `_send_alert()` to gate email sends by alert type against the allowed list.

---

## 4. Resource Usage

### 4.1 No `gc.collect()` usage — not a concern, but no cleanup either
No `gc.collect()` calls were found. This is not a problem in itself, but the evolution loop creates a new `BacktestEngine` on every iteration without explicitly releasing the previous one. On large datasets this will accumulate DataFrames in memory. Adding explicit `del engine` after `engine.run()` in `Optimizer.run_iteration()` is recommended.

### 4.2 Large Supabase fetches without pagination
`final_report.py` calls `self.db.select("trades", limit=10000)` — pulling 10,000 rows into memory at once. This risks high RAM usage. Recommend chunked iteration (e.g., 1,000 rows at a time) with streaming aggregation.

### 4.3 `evolution_report.py` fetches up to 1,000 evolution log rows
`self.db.select("evolution_log", limit=1000)` is acceptable for now but should be reviewed as the log grows.

### 4.4 DataFrame copies in backtester not visible from audit
The backtester engine (`backtester/engine.py`) was not in scope for this audit. If it uses `df.copy()` liberally inside the bar-by-bar loop that would be the primary RAM concern.

---

## 5. Dead Code

### 5.1 `dashboard/app.py` — standalone `__main__` block duplicates `main.cmd_dashboard`
`app.py` ends with:
```python
if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=5000, debug=False)
```
This is unreachable in normal operation (entry point is `main.py`). It should be removed or noted as a dev-convenience only block.

### 5.2 `_check_milestones` — assigned but discarded report path
In `optimizer.py` lines 146 and 150:
```python
report = MiniReport(self.db).generate(total_trades, result)
report = EvolutionReport(self.db).generate(total_trades)
```
The returned file path is assigned to `report` but never used, logged, or sent. Either include the path in the alert body or use `_` to signal intentional discard.

### 5.3 `versioner.snapshot()` triggered by wrong condition
`optimizer.py` line 132:
```python
if i % EVOLUTION_MIN_TRADES == 0:
    self.versioner.snapshot(current_params, new_result, i)
```
`EVOLUTION_MIN_TRADES` is 100 (minimum trades before comparing, not an iteration count). Snapshotting every 100 *iterations* may be the intent, but the variable name is misleading. Consider introducing a dedicated `SNAPSHOT_EVERY_ITERATIONS` constant.

---

## 6. Configuration Issues

### 6.1 `config.py` — `CLAUDE_MODEL` value appears invalid
```python
CLAUDE_MODEL: str = "claude-opus-4-8"
```
As of 2026-06-02, the correct model identifiers use the format `claude-opus-4-5`, `claude-sonnet-4-5`, etc. `claude-opus-4-8` is not a known release. Verify against the Anthropic model list and update.

### 6.2 `config.py` — `LONDON_KILL_ZONE` starts at 07:00 UTC but entry engine uses 02:00 UTC
`config.py` defines:
```python
LONDON_KILL_ZONE: Dict[str, int] = {"start": 7, "end": 10}
```
The newly created `execution/entry_engine.py` uses 02:00–10:00 UTC for the London kill zone bonus, following the ICT definition (pre-market open through the London cash open). The config value does not match. Decide on one definition and apply it consistently across all modules. ICT convention: London kill zone is 02:00–05:00 UTC (NY midnight to London open); the broader 07:00–10:00 window is the London open session.

### 6.3 `config.py` — `StrategyParams.confidence_threshold` is 7.0 out of 10
The entry engine plans use scores in the 3.5–6.5 range (sweep 2 + bos 2 + kz 1.5 = max 5.5 for Plan A). The existing `ConfidenceScorer` in the ICT engine uses a different 0–10 scale. These two scoring systems are not reconciled. The entry engine's plan score thresholds should either be mapped to the global confidence scale or kept as a separate, documented system.

### 6.4 `main.py` — Default `args.pairs` not set before `cmd_evolve` when command is `None`
```python
if args.command is None:
    logger.info("No command given — defaulting to 'evolve'")
    args.iterations = 100
    args.pairs = "XAUUSD"
```
`args.pairs` is set correctly here, but `args` is a `Namespace` from argparse with no `pairs` attribute when the subcommand is `None` (the `evolve` subparser was never invoked). The `AttributeError` will be raised on line `pairs = args.pairs.split(",")`. Fix: use `getattr(args, "pairs", "XAUUSD")` inside `cmd_evolve`, or always set defaults on the parser level.

---

## 7. Action Items

1. **Fix Windows hardcoded paths** in `main.py` (log file), `reports/evolution_report.py`, `reports/final_report.py`, and `reports/mini_report.py`. Use `config.LOG_FILE` and `os.path.join(os.path.expanduser("~"), "AutoTraderClaude", "reports_output")`.

2. **Validate and correct `CLAUDE_MODEL`** in `config.py`. Update `requirements.txt` to `anthropic>=0.40.0` to match the actual model IDs in use.

3. **Remove duplicate scheduler dependency**: drop `schedule==1.2.2` from `requirements.txt`; keep `APScheduler==3.10.4`.

4. **Implement time-based email scheduler** (`autotrader_claude/alerts/scheduler.py`) with exactly the five allowed email types. Gate `Optimizer._send_alert()` so email is only sent for those five types; all other events use Telegram only.

5. **Fix `args.pairs` AttributeError** in `main.py` when no command is given. Use `getattr(args, "pairs", "XAUUSD")` and `getattr(args, "iterations", 100)` in `cmd_evolve`.

6. **Fix `_check_milestones` report path discard**: log the returned path or remove the assignment.

7. **Rename `EVOLUTION_MIN_TRADES`** usage in the snapshot trigger to a dedicated constant, e.g. `SNAPSHOT_EVERY_ITERATIONS = 100`.

8. **Add `del engine` after `engine.run()`** in `Optimizer.run_iteration()` to allow garbage collection of large DataFrames.

9. **Chunk the 10,000-row trade fetch** in `FinalReport.generate()` into batches of 1,000 rows.

10. **Reconcile kill zone definition**: align `config.py`'s `LONDON_KILL_ZONE` start hour (currently 7) with the ICT convention (2 or 7 — pick one and apply it in both the ICT engine and the entry engine).

11. **Reconcile confidence scoring scales**: document whether entry plan scores (max ~5.5) are separate from the global `ConfidenceScorer` (0–10) and ensure `confidence_threshold` in `StrategyParams` is not applied to entry plan scores without normalisation.

12. **Remove or document `dashboard/app.py` standalone `__main__` block** to avoid confusion with the canonical entry point in `main.py`.
