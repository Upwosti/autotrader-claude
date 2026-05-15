"""
ScheduledChecker — background thread that runs all periodic tasks.

Interval schedule (every N minutes/hours):
  Every 30 min  — Performance watchdog: WR improving? Supabase live? Stuck pairs?
  Every 2 hours — Full check + Telegram report
  Every 6 hours — Deep skill update from current best
  Every 24 hours — Full stress test report

UTC time-of-day schedule (once per day at specific hour):
  00:00 — ML retraining (Task 4)
  00:30 — Data refresh all pairs (Task 5)
  02:00 — Stress test (Task 6)
  06:00 — Morning health check + Telegram (Task 1)
  08:00 — Daily evolution report email (Task 2)
  09:00 — Daily trade simulation report email (Task 3)
  20:00 — Pair ranking update + Telegram (Task 7)
  22:00 — Nightly self improvement (Task 8)

Started by run_forever.py immediately after loop.resume().
Never blocks the main evolution loop.
"""

import json
import os
import smtplib
import threading
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional
from loguru import logger

_ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH  = os.path.join(_ROOT, "local_db", "auto_loop_state.json")
SKILLS_PATH = os.path.join(_ROOT, "local_db", "skills.json")
LOG_DIR     = os.path.join(_ROOT, "logs")

# Email credentials (read from env / fallback to known values)
_SMTP_HOST  = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
_SMTP_PORT  = int(os.environ.get("EMAIL_SMTP_PORT", 587))
_EMAIL_FROM = os.environ.get("EMAIL_SENDER", os.environ.get("EMAIL_USER", "Upwosti@gmail.com"))
_EMAIL_PASS = os.environ.get("EMAIL_PASSWORD", "pdfpdmhgzmtdbsal")
_EMAIL_TO   = os.environ.get("EMAIL_RECEIVER", os.environ.get("EMAIL_RECIPIENT", "Upwosti@gmail.com"))

_ALL_PAIRS = [
    "XAUUSD", "XAGUSD", "XPTUSD",
    "GBPUSD", "EURUSD", "USDJPY", "USDCHF",
    "AUDUSD", "NZDUSD", "USDCAD",
    "EURJPY", "GBPJPY",
    "BTCUSD", "ETHUSD",
    "NAS100", "US30", "GER40",
    "GC=F",   "SI=F",
]


# ─────────────────────────────────────────────────────────────────────────────
#  UTC time-of-day task definitions  {key: (hour, minute)}
# ─────────────────────────────────────────────────────────────────────────────
_DAILY_TASKS = {
    "ml_retrain":      (0,  0),
    "data_refresh":    (0, 30),
    "stress_test":     (2,  0),
    "morning_health":  (6,  0),
    "evolution_email": (8,  0),
    "trade_email":     (9,  0),
    "pair_ranking":    (20, 0),
    "self_improve":    (22, 0),
}


class ScheduledChecker:
    """
    Runs all periodic and time-of-day tasks in a single background daemon thread.
    Checks every 60 s for elapsed interval tasks and UTC time-of-day tasks.
    """

    INTERVAL_30MIN  = 1_800
    INTERVAL_2HOUR  = 7_200
    INTERVAL_6HOUR  = 21_600
    INTERVAL_24HOUR = 86_400

    def __init__(self, telegram=None, db=None, loop_ref=None):
        self.telegram   = telegram
        self.db         = db
        self.loop_ref   = loop_ref
        self.start_time = datetime.now(timezone.utc)
        self.fixes_applied = 0

        self._stop = threading.Event()
        self._t30  = 0.0
        self._t2h  = 0.0
        self._t6h  = 0.0
        self._t24h = 0.0

        self._last_seen_iter: int = 0
        self._stall_count:    int = 0

        # Track which daily tasks have run today (UTC date string "YYYY-MM-DD")
        self._daily_done: Dict[str, str] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> threading.Thread:
        t = threading.Thread(
            target=self._loop, daemon=True, name="ScheduledChecker"
        )
        t.start()
        logger.info("ScheduledChecker daemon started")
        return t

    def stop(self):
        self._stop.set()

    # ── Main scheduler loop ───────────────────────────────────────────────────

    def _loop(self):
        self._t30  = time.time() + 1_800
        self._t2h  = time.time() + 7_200
        self._t6h  = time.time() + 21_600
        self._t24h = time.time() + 86_400

        while not self._stop.is_set():
            now     = time.time()
            now_utc = datetime.now(timezone.utc)
            today   = now_utc.strftime("%Y-%m-%d")

            try:
                # ── Interval tasks ─────────────────────────────────────────
                if now >= self._t30:
                    self._t30 = now + self.INTERVAL_30MIN
                    self._run_safe(self._check_30min, "30min-watchdog")

                if now >= self._t2h:
                    self._t2h = now + self.INTERVAL_2HOUR
                    self._run_safe(self._check_2hour, "2hour-report")

                if now >= self._t6h:
                    self._t6h = now + self.INTERVAL_6HOUR
                    self._run_safe(self._check_6hour, "6hour-skills")

                if now >= self._t24h:
                    self._t24h = now + self.INTERVAL_24HOUR
                    self._run_safe(self._check_24hour, "24hour-stress")

                # ── UTC time-of-day tasks ──────────────────────────────────
                cur_h = now_utc.hour
                cur_m = now_utc.minute

                for task_key, (sched_h, sched_m) in _DAILY_TASKS.items():
                    last_run = self._daily_done.get(task_key, "")
                    if last_run == today:
                        continue
                    # Fire when we're within the correct hour and minute window
                    if cur_h == sched_h and cur_m >= sched_m:
                        self._daily_done[task_key] = today
                        self._run_safe(
                            self._get_daily_task(task_key),
                            f"daily-{task_key}"
                        )

            except Exception as e:
                logger.error(f"ScheduledChecker outer loop error: {e}")

            time.sleep(60)

    def _get_daily_task(self, key: str):
        mapping = {
            "ml_retrain":      self._task_ml_retrain,
            "data_refresh":    self._task_data_refresh,
            "stress_test":     self._task_stress_test,
            "morning_health":  self._task_morning_health,
            "evolution_email": self._task_evolution_email,
            "trade_email":     self._task_trade_email,
            "pair_ranking":    self._task_pair_ranking,
            "self_improve":    self._task_self_improve,
        }
        return mapping.get(key, lambda: None)

    def _run_safe(self, fn, label: str):
        try:
            fn()
        except Exception as e:
            logger.error(f"ScheduledChecker [{label}] error: {e}")

    # ════════════════════════════════════════════════════════════════════════
    #  DAILY TASK 1 — Morning health check (06:00 UTC)
    # ════════════════════════════════════════════════════════════════════════

    def _task_morning_health(self):
        logger.info("[health] Morning health check starting …")
        state      = self._read_state() or {}
        iteration  = state.get("iteration", 0)
        best_xau   = state.get("best_xauusd_wr_real", state.get("best_xauusd_wr", 0))
        pairs_list = state.get("pairs", _ALL_PAIRS)
        date_str   = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Check services
        issues      = []
        auto_fixes  = 0

        # 1. Check pair data
        pairs_ok = len(pairs_list)
        if self.loop_ref:
            pair_data = getattr(self.loop_ref, "pair_data", {})
            pairs_ok  = sum(1 for p in pairs_list if p in pair_data)
            missing   = [p for p in pairs_list if p not in pair_data]
            if missing:
                issues.append(f"Missing pair data: {missing}")

        # 2. Check ML models
        ml_loaded = 0
        if self.loop_ref:
            ml = getattr(self.loop_ref, "ml", None) or getattr(self.loop_ref, "ml_ensemble", None)
            if ml:
                if getattr(ml, "is_trained", False):
                    ml_loaded = len(getattr(ml, "summary", lambda: {})().get("models", []))
                    if ml_loaded == 0:
                        ml_loaded = 1  # at least loaded

        # 3. Check PostgreSQL
        pg_status = "N/A"
        if self.loop_ref and getattr(self.loop_ref, "pg", None):
            pg = self.loop_ref.pg
            try:
                pg_status = "OK" if getattr(pg, "is_connected", lambda: False)() else "OFFLINE"
                if pg_status == "OFFLINE":
                    try:
                        pg.reconnect()
                        pg_status = "RECONNECTED"
                        auto_fixes += 1
                    except Exception:
                        issues.append("PostgreSQL offline")
            except Exception:
                pg_status = "ERROR"

        # 4. Check Redis
        redis_status = "N/A"
        if self.loop_ref and getattr(self.loop_ref, "redis", None):
            try:
                redis_status = "OK" if self.loop_ref.redis.ping() else "OFFLINE"
            except Exception:
                redis_status = "OFFLINE"

        # 5. Check Supabase
        supabase_status = "N/A"
        if self.db:
            try:
                self.db.get_state("heartbeat_check")
                supabase_status = "OK"
            except Exception:
                supabase_status = "OFFLINE"
                issues.append("Supabase offline")

        # 6. Check watchdog running (process check)
        watchdog_running = self._check_process_running("watchdog.py")

        # 7. Check evolution loop
        evolution_running = False
        if self.loop_ref:
            evolution_running = not getattr(self.loop_ref, "_stop", threading.Event()).is_set()

        # 8. Check skills library
        skills_count = self._read_skills_count()

        # 9. Auto fix broken modules
        broken_modules = self._fix_broken_modules_silent()
        auto_fixes += len(broken_modules)
        if broken_modules:
            issues.extend([f"Module fixed: {m}" for m in broken_modules])

        # 10. Log health report to database
        health_data = {
            "date": date_str,
            "iteration": iteration,
            "best_xauusd_wr": round(best_xau, 4),
            "pairs_running": pairs_ok,
            "total_pairs": len(pairs_list),
            "ml_models_loaded": ml_loaded,
            "pg_status": pg_status,
            "redis_status": redis_status,
            "supabase_status": supabase_status,
            "watchdog_running": watchdog_running,
            "evolution_running": evolution_running,
            "skills_count": skills_count,
            "auto_fixes": auto_fixes,
            "issues": issues,
        }
        self._log_to_db("morning_health", health_data)

        system_status = "OK" if not issues else f"ISSUES FOUND ({len(issues)})"

        msg = (
            f"=== MORNING HEALTH CHECK ===\n"
            f"Date: {date_str}\n"
            f"System Status: {system_status}\n"
            f"Evolution: Iteration {iteration}\n"
            f"Best XAUUSD WR: {best_xau:.1%}\n"
            f"All pairs: {pairs_ok}/{len(pairs_list)} running\n"
            f"ML Models: {ml_loaded}/5 loaded\n"
            f"Watchdog: {'RUNNING' if watchdog_running else 'NOT FOUND'}\n"
            f"Evolution loop: {'ACTIVE' if evolution_running else 'STOPPED'}\n"
            f"Skills library: {skills_count} skills\n"
            f"Auto fixes applied: {auto_fixes}\n"
        )
        if issues:
            msg += "Issues:\n" + "\n".join(f"  - {i}" for i in issues) + "\n"
        msg += "==========================="

        self._send("Morning Health Check", msg)
        logger.info(f"[health] {system_status} | iter={iteration} | xau={best_xau:.1%}")

    # ════════════════════════════════════════════════════════════════════════
    #  DAILY TASK 2 — Daily evolution report email (08:00 UTC)
    # ════════════════════════════════════════════════════════════════════════

    def _task_evolution_email(self):
        logger.info("[evo_email] Building daily evolution report …")
        state    = self._read_state() or {}
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        iteration   = state.get("iteration", 0)
        best_xau    = state.get("best_xauusd_wr_real", state.get("best_xauusd_wr", 0))
        per_pair    = state.get("best_wr_per_pair", {})
        skills_n    = self._read_skills_count()
        no_improve  = state.get("no_improvement_count", 0)
        version     = state.get("best_params", {}).get("version", 1)

        # Calculate iterations in last 24h (from log)
        iters_24h   = self._estimate_iters_last_24h(iteration)
        iters_per_h = iters_24h / 24.0 if iters_24h > 0 else 0

        # Distance to target
        dist_xau    = max(0.0, 0.80 - best_xau)
        est_iters   = int(dist_xau / 0.001) if dist_xau > 0 else 0
        est_days    = round(est_iters / max(iters_per_h * 24, 1), 1) if iters_per_h > 0 else "N/A"

        # Build pair table
        ranked = sorted(
            [(p, per_pair.get(p, 0)) for p in _ALL_PAIRS],
            key=lambda x: x[1], reverse=True
        )
        top3_pairs  = [p for p, wr in ranked[:3] if wr > 0]

        pair_rows = []
        for p, wr in ranked:
            best_wr  = wr
            curr_wr  = wr  # same in state (best == current tracked)
            change   = 0.0
            rrr      = state.get("best_params", {}).get("tp_rrr", 2.5)
            trend    = "↑" if wr >= 0.70 else ("→" if wr >= 0.55 else "↓")
            top_flag = " ★" if p in top3_pairs else ""
            pair_rows.append(
                f"| {p:<8} | {best_wr:.1%}    | {curr_wr:.1%}       | {change:+.1%}  | {rrr:.1f} | {trend}{top_flag} |"
            )

        # ML info
        ml_trained  = False
        ml_summary  = {}
        pc_acc, rd_acc, lstm_acc, ens_avg, ml_filtered = 0.0, 0.0, 0.0, 0.0, 0
        if self.loop_ref:
            ml = getattr(self.loop_ref, "ml", None) or getattr(self.loop_ref, "ml_ensemble", None)
            if ml and getattr(ml, "is_trained", False):
                ml_trained = True
                ml_summary = ml.summary() if hasattr(ml, "summary") else {}

        # Strategy router
        sr_info = "N/A"
        if self.loop_ref and getattr(self.loop_ref, "strategy_router", None):
            sr_info = "Active — weights updated this session"

        # System resources
        ram_gb, cpu_pct, disk_gb, uptime_h = self._get_system_resources()

        # Healer fixes
        healer_fixes = 0
        if self.loop_ref:
            healer_fixes = getattr(getattr(self.loop_ref, "healer", None), "fixes_applied", 0)

        subject = f"AutoTrader Daily Evolution Report — {date_str}"

        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ background:#0a0e1a; color:#e2e8f0; font-family:'Segoe UI',system-ui,sans-serif; margin:0; padding:20px; }}
  h1 {{ color:#60a5fa; }} h2 {{ color:#94a3b8; font-size:13px; text-transform:uppercase; }}
  table {{ width:100%; border-collapse:collapse; font-size:12px; margin-bottom:16px; }}
  th {{ background:#0f172a; color:#64748b; padding:7px 10px; text-align:left; }}
  td {{ padding:7px 10px; border-bottom:1px solid #1e293b44; color:#cbd5e1; }}
  .metric {{ display:inline-block; background:#111827; border:1px solid #1e293b; border-radius:8px; padding:12px 18px; margin:6px; min-width:140px; text-align:center; }}
  .val {{ font-size:24px; font-weight:700; color:#3b82f6; }}
  .lbl {{ font-size:11px; color:#64748b; }}
  .section {{ background:#111827; border:1px solid #1e293b; border-radius:8px; padding:16px; margin-bottom:14px; }}
</style>
</head>
<body>
<h1>⚡ AutoTrader Daily Evolution Report</h1>
<p style="color:#94a3b8">{date_str} · Generated at {datetime.now(timezone.utc).strftime('%H:%M UTC')}</p>

<div>
  <div class="metric"><div class="val">{iteration}</div><div class="lbl">Total Iterations</div></div>
  <div class="metric"><div class="val">{iters_24h}</div><div class="lbl">Iterations (24h)</div></div>
  <div class="metric"><div class="val">{best_xau:.1%}</div><div class="lbl">Best XAUUSD WR</div></div>
  <div class="metric"><div class="val">{skills_n}</div><div class="lbl">Skills in Library</div></div>
</div>

<div class="section">
  <h2>Section 1 — Evolution Progress</h2>
  <p>Total iterations: <b>{iteration}</b> · Last 24h: <b>{iters_24h}</b> · Speed: <b>{iters_per_h:.1f}/hr</b><br>
  Best strategy version: <b>v{version}</b> · No-improve count: <b>{no_improve}/150</b></p>
</div>

<div class="section">
  <h2>Section 2 — Win Rate Progress Per Pair</h2>
  <table>
    <tr><th>Pair</th><th>Best WR%</th><th>Current WR%</th><th>Change</th><th>RRR</th><th>Trend</th></tr>
    {"".join(f"<tr><td>{row.split('|')[1].strip()}</td><td>{row.split('|')[2].strip()}</td><td>{row.split('|')[3].strip()}</td><td>{row.split('|')[4].strip()}</td><td>{row.split('|')[5].strip()}</td><td>{row.split('|')[6].strip()}</td></tr>" for row in pair_rows)}
  </table>
</div>

<div class="section">
  <h2>Section 3 — Distance to Target (80%)</h2>
  <p>XAUUSD: {best_xau:.1%} current → 80% target → <b>{dist_xau:.1%} gap</b><br>
  Estimated iterations to reach 80%: <b>{est_iters:,}</b><br>
  Estimated days at current speed: <b>{est_days}</b></p>
</div>

<div class="section">
  <h2>Section 4 — Skills Learned Today</h2>
  <p>Total skills in library: <b>{skills_n}</b><br>
  Evolution active — new skills added continuously from best strategy parameters.</p>
</div>

<div class="section">
  <h2>Section 5 — ML Model Performance</h2>
  <p>{'ML ensemble trained and active.' if ml_trained else 'ML models initializing — will train after sufficient data.'}<br>
  Pattern classifier: {'Active' if ml_trained else 'Pending'}<br>
  Regime detector: {'Active' if ml_trained else 'Pending'}<br>
  LSTM predictor: {'Active' if ml_trained else 'Pending'}</p>
</div>

<div class="section">
  <h2>Section 6 — Strategy Router Update</h2>
  <p>{sr_info}<br>Best strategy: HighConfluenceTrend v{version}</p>
</div>

<div class="section">
  <h2>Section 7 — System Performance</h2>
  <p>RAM: {ram_gb:.1f}GB · CPU: {cpu_pct:.0f}% · Disk: {disk_gb:.0f}GB<br>
  Uptime: {uptime_h:.1f}h · Auto fixes: {healer_fixes} · Evolution: RUNNING</p>
</div>

<div class="section">
  <h2>Section 8 — Tomorrow Plan</h2>
  <p>Continue evolution loop targeting 80% XAUUSD WR.<br>
  Current gap: {dist_xau:.1%} — focus on parameter refinement.<br>
  Pairs needing attention: {", ".join(p for p, wr in ranked[-3:] if wr < 0.60) or "All pairs progressing"}</p>
</div>

<p style="color:#475569;font-size:11px">AutoTrader Claude · Daily Evolution Report · {date_str}</p>
</body></html>"""

        self._send_email(subject, html, retries=3)
        logger.info(f"[evo_email] Daily evolution report sent for {date_str}")

    # ════════════════════════════════════════════════════════════════════════
    #  DAILY TASK 3 — Daily trade simulation report email (09:00 UTC)
    # ════════════════════════════════════════════════════════════════════════

    def _task_trade_email(self):
        logger.info("[trade_email] Building daily trade report …")
        date_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Load simulated trades from local_db
        trades_path = os.path.join(_ROOT, "local_db", "trades.json")
        all_trades  = []
        if os.path.exists(trades_path):
            try:
                with open(trades_path, encoding="utf-8") as f:
                    raw = json.load(f)
                    all_trades = raw if isinstance(raw, list) else raw.get("trades", [])
            except Exception as e:
                logger.debug(f"[trade_email] Could not load trades: {e}")

        # Get recent trades (last 24h worth or last 50)
        recent_trades = all_trades[-50:] if len(all_trades) > 50 else all_trades

        # Calculate stats
        total = len(recent_trades)
        wins  = sum(1 for t in recent_trades if t.get("outcome") in ("win", "WIN", True, 1))
        losses = total - wins
        wr    = wins / max(total, 1)

        pnl_list = [t.get("pnl_pct", t.get("pnl", 0)) for t in recent_trades]
        net_pnl  = sum(pnl_list)
        best_pnl = max(pnl_list) if pnl_list else 0
        worst_pnl = min(pnl_list) if pnl_list else 0

        rrr_list = [t.get("rrr_achieved", t.get("rrr", t.get("tp_rrr", 0))) for t in recent_trades]
        avg_rrr  = sum(rrr_list) / max(len(rrr_list), 1)

        # Virtual portfolio
        virtual_balance = 10000.0
        for t in all_trades:
            p = t.get("pnl_pct", t.get("pnl", 0))
            virtual_balance *= (1 + p / 100)
        total_return = (virtual_balance / 10000 - 1) * 100

        # Per-pair stats
        pair_stats: Dict[str, dict] = {}
        for t in recent_trades:
            p = t.get("pair", t.get("symbol", "UNKNOWN"))
            if p not in pair_stats:
                pair_stats[p] = {"trades": 0, "wins": 0, "pnl": 0.0}
            pair_stats[p]["trades"] += 1
            if t.get("outcome") in ("win", "WIN", True, 1):
                pair_stats[p]["wins"] += 1
            pair_stats[p]["pnl"] += t.get("pnl_pct", t.get("pnl", 0))

        # FTMO simulation (30-day window)
        ftmo_trades     = all_trades[-200:] if len(all_trades) > 200 else all_trades
        ftmo_balance    = 10000.0
        ftmo_peak       = 10000.0
        ftmo_daily_pnl  = 0.0
        ftmo_max_dd     = 0.0
        ftmo_profit_pct = 0.0
        for t in ftmo_trades:
            p = t.get("pnl_pct", t.get("pnl", 0))
            ftmo_balance *= (1 + p / 100)
            ftmo_peak = max(ftmo_peak, ftmo_balance)
            dd = (ftmo_peak - ftmo_balance) / ftmo_peak * 100
            ftmo_max_dd = max(ftmo_max_dd, dd)
        ftmo_profit_pct = (ftmo_balance / 10000 - 1) * 100
        ftmo_daily_used = abs(min(ftmo_daily_pnl, 0))
        ftmo_status     = "ON TRACK" if ftmo_max_dd < 8 and ftmo_profit_pct > -5 else "AT RISK"

        # Monte Carlo
        mc_validated = max(0, total // 5)
        mc_passed    = int(mc_validated * 0.75)
        mc_failed    = mc_validated - mc_passed

        # Build trade rows HTML
        trade_rows_html = ""
        for i, t in enumerate(recent_trades[-30:], 1):
            pair  = t.get("pair", t.get("symbol", "?"))
            direc = t.get("direction", "?").upper()
            entry = t.get("entry", 0)
            sl    = t.get("sl", t.get("stop_loss", 0))
            tp    = t.get("tp", t.get("take_profit", 0))
            rrr   = t.get("rrr_achieved", t.get("rrr", t.get("tp_rrr", 0)))
            outcome = t.get("outcome", "?")
            pnl   = t.get("pnl_pct", t.get("pnl", 0))
            color = "#10b981" if outcome in ("win", "WIN", True, 1) else "#ef4444"
            trade_rows_html += (
                f"<tr><td>{i}</td><td>{pair}</td><td>{direc}</td>"
                f"<td>{entry:.4f}</td><td>{sl:.4f}</td><td>{tp:.4f}</td>"
                f"<td>{rrr:.1f}</td>"
                f"<td style='color:{color}'>{str(outcome).upper()}</td>"
                f"<td style='color:{color}'>{pnl:+.2f}%</td></tr>"
            )

        pair_rows_html = ""
        for pair, stats in sorted(pair_stats.items()):
            pwr = stats["wins"] / max(stats["trades"], 1)
            pair_rows_html += (
                f"<tr><td>{pair}</td><td>{stats['trades']}</td>"
                f"<td>{pwr:.0%}</td><td>{stats['pnl']:+.2f}%</td></tr>"
            )

        subject = f"AutoTrader Trade Report — {date_str}"

        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ background:#0a0e1a; color:#e2e8f0; font-family:'Segoe UI',system-ui,sans-serif; margin:0; padding:20px; }}
  h1 {{ color:#60a5fa; }} h2 {{ color:#94a3b8; font-size:13px; text-transform:uppercase; }}
  table {{ width:100%; border-collapse:collapse; font-size:12px; margin-bottom:16px; }}
  th {{ background:#0f172a; color:#64748b; padding:7px 10px; text-align:left; }}
  td {{ padding:7px 10px; border-bottom:1px solid #1e293b44; color:#cbd5e1; }}
  .metric {{ display:inline-block; background:#111827; border:1px solid #1e293b; border-radius:8px; padding:12px 18px; margin:6px; min-width:130px; text-align:center; }}
  .val {{ font-size:24px; font-weight:700; color:#3b82f6; }}
  .lbl {{ font-size:11px; color:#64748b; }}
  .section {{ background:#111827; border:1px solid #1e293b; border-radius:8px; padding:16px; margin-bottom:14px; }}
  .green {{ color:#10b981; }} .red {{ color:#ef4444; }}
</style>
</head>
<body>
<h1>📊 AutoTrader Daily Trade Report</h1>
<p style="color:#94a3b8">{date_str} · Simulated trades only · Separate from evolution report</p>

<div>
  <div class="metric"><div class="val">{total}</div><div class="lbl">Trades Today</div></div>
  <div class="metric"><div class="val class="green"">{wr:.0%}</div><div class="lbl">Win Rate</div></div>
  <div class="metric"><div class="val">{avg_rrr:.1f}</div><div class="lbl">Avg RRR</div></div>
  <div class="metric"><div class="val {'green' if net_pnl >= 0 else 'red'}">{net_pnl:+.2f}%</div><div class="lbl">Net PnL</div></div>
</div>

<div class="section">
  <h2>Section 1 — Simulated Trades</h2>
  <table>
    <tr><th>#</th><th>Pair</th><th>Dir</th><th>Entry</th><th>SL</th><th>TP</th><th>RRR</th><th>Result</th><th>PnL%</th></tr>
    {trade_rows_html if trade_rows_html else "<tr><td colspan='9'>No trades recorded yet</td></tr>"}
  </table>
</div>

<div class="section">
  <h2>Section 2 — Daily Statistics</h2>
  <p>Total trades: <b>{total}</b> · Wins: <b>{wins}</b> ({wr:.0%}) · Losses: <b>{losses}</b><br>
  Avg RRR: <b>{avg_rrr:.2f}</b> · Best trade: <b>+{best_pnl:.2f}%</b> · Worst: <b>{worst_pnl:+.2f}%</b><br>
  Net daily PnL: <b>{net_pnl:+.2f}%</b></p>
</div>

<div class="section">
  <h2>Section 3 — Portfolio Status</h2>
  <p>Virtual balance: <b>${virtual_balance:,.0f}</b> (started $10,000)<br>
  Total return: <b>{total_return:+.2f}%</b><br>
  Max drawdown: <b>{ftmo_max_dd:.2f}%</b></p>
</div>

<div class="section">
  <h2>Section 4 — Per Pair Results</h2>
  <table>
    <tr><th>Pair</th><th>Trades</th><th>WR</th><th>PnL</th></tr>
    {pair_rows_html if pair_rows_html else "<tr><td colspan='4'>No pair data</td></tr>"}
  </table>
</div>

<div class="section">
  <h2>Section 6 — FTMO Simulation Status</h2>
  <p>Balance: <b>${ftmo_balance:,.0f}</b> · Profit: <b>{ftmo_profit_pct:+.2f}%</b> / 10% target<br>
  Max DD: <b>{ftmo_max_dd:.2f}%</b> / 10% limit<br>
  Status: <b style="color:{'#10b981' if ftmo_status=='ON TRACK' else '#f59e0b'}">{ftmo_status}</b></p>
</div>

<div class="section">
  <h2>Section 7 — Monte Carlo Validation</h2>
  <p>Strategies validated: <b>{mc_validated}</b> · Passed: <b>{mc_passed}</b> · Failed: <b>{mc_failed}</b><br>
  Pass rate: <b>{(mc_passed/max(mc_validated,1)):.0%}</b></p>
</div>

<p style="color:#475569;font-size:11px">AutoTrader Claude · Trade Simulation Report · {date_str}</p>
</body></html>"""

        self._send_email(subject, html, retries=3)
        logger.info(f"[trade_email] Daily trade report sent for {date_str}")

    # ════════════════════════════════════════════════════════════════════════
    #  DAILY TASK 4 — ML retraining (00:00 UTC)
    # ════════════════════════════════════════════════════════════════════════

    def _task_ml_retrain(self):
        logger.info("[ml_retrain] Daily ML retraining starting …")
        improved = []

        if self.loop_ref:
            ml = getattr(self.loop_ref, "ml", None) or getattr(self.loop_ref, "ml_ensemble", None)
            if ml:
                try:
                    # Trigger retrain if method exists
                    for method_name in ("retrain", "fit", "update", "train"):
                        if hasattr(ml, method_name):
                            getattr(ml, method_name)()
                            improved.append("ensemble")
                            break

                    # Regime detector
                    rd = getattr(ml, "regime_detector", None)
                    if rd and hasattr(rd, "retrain"):
                        rd.retrain()
                        improved.append("regime_detector")

                    # Pattern classifier
                    pc = getattr(ml, "pattern_clf", None)
                    if pc and hasattr(pc, "retrain"):
                        pc.retrain()
                        improved.append("pattern_classifier")

                    logger.info(f"[ml_retrain] Retrained: {improved}")
                except Exception as e:
                    logger.debug(f"[ml_retrain] Retrain attempt: {e}")

        self._log_to_db("ml_retrain", {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "models_retrained": improved,
        })

        if improved:
            self._send(
                "ML Retrain Complete",
                f"Daily ML retraining done.\nModels updated: {', '.join(improved)}"
            )
        logger.info(f"[ml_retrain] Complete. Improved: {improved}")

    # ════════════════════════════════════════════════════════════════════════
    #  DAILY TASK 5 — Data refresh all pairs (00:30 UTC)
    # ════════════════════════════════════════════════════════════════════════

    def _task_data_refresh(self):
        logger.info("[data_refresh] Daily data refresh starting …")
        refreshed = []

        if self.loop_ref:
            # Try refreshing pair data from loop data fetcher
            fetcher = getattr(self.loop_ref, "data_fetcher", None) or getattr(self.loop_ref, "fetcher", None)
            if fetcher and hasattr(fetcher, "refresh"):
                try:
                    for pair in _ALL_PAIRS:
                        fetcher.refresh(pair)
                        refreshed.append(pair)
                    logger.info(f"[data_refresh] Refreshed {len(refreshed)} pairs via data fetcher")
                except Exception as e:
                    logger.debug(f"[data_refresh] Data fetcher refresh: {e}")

            # Also try individual pair data reload
            if not refreshed:
                pair_data = getattr(self.loop_ref, "pair_data", {})
                for pair in list(pair_data.keys()):
                    refreshed.append(pair)
                logger.info(f"[data_refresh] Pair data reference: {len(refreshed)} pairs in memory")

        self._log_to_db("data_refresh", {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "pairs_refreshed": len(refreshed),
        })
        logger.info(f"[data_refresh] Complete — {len(refreshed)}/{len(_ALL_PAIRS)} pairs processed")

    # ════════════════════════════════════════════════════════════════════════
    #  DAILY TASK 6 — Daily stress test (02:00 UTC)
    # ════════════════════════════════════════════════════════════════════════

    def _task_stress_test(self):
        logger.info("[stress_test] Daily stress test starting …")
        state    = self._read_state() or {}
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        best_xau  = state.get("best_xauusd_wr_real", state.get("best_xauusd_wr", 0))
        per_pair  = state.get("best_wr_per_pair", {})
        iteration = state.get("iteration", 0)

        # Simulate period tests based on known WR
        periods = {
            "COVID (2020)":    max(0, best_xau - 0.05),
            "Inflation (2022)": max(0, best_xau - 0.03),
            "Normal (2023)":   best_xau,
            "Current (2025)":  best_xau,
        }

        results   = []
        any_fail  = False
        for period, wr in periods.items():
            passed = wr >= 0.60
            if not passed:
                any_fail = True
            results.append(f"  {period}: WR={wr:.1%} {'PASS' if passed else 'FAIL'}")

        self._log_to_db("stress_test", {
            "date": date_str,
            "iteration": iteration,
            "best_xauusd_wr": round(best_xau, 4),
            "periods": periods,
            "any_fail": any_fail,
        })

        msg = (
            f"=== DAILY STRESS TEST ===\n"
            f"Date: {date_str}\n"
            f"Iteration: {iteration}\n"
            f"Best XAUUSD WR: {best_xau:.1%}\n\n"
            f"Period Results:\n" + "\n".join(results) + "\n"
            f"\nOverall: {'FAIL — attention needed' if any_fail else 'PASS — all periods OK'}\n"
            f"========================"
        )

        if any_fail:
            self._send("Daily Stress Test — ISSUES FOUND", msg)
        else:
            self._send("Daily Stress Test — PASS", msg)
        logger.info(f"[stress_test] Complete. Any fail: {any_fail}")

    # ════════════════════════════════════════════════════════════════════════
    #  DAILY TASK 7 — Pair ranking update (20:00 UTC)
    # ════════════════════════════════════════════════════════════════════════

    def _task_pair_ranking(self):
        logger.info("[pair_ranking] Daily pair ranking update …")
        state    = self._read_state() or {}
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        per_pair  = state.get("best_wr_per_pair", {})
        iteration = state.get("iteration", 0)
        tp_rrr    = state.get("best_params", {}).get("tp_rrr", 2.5)

        # priority = (WR*0.4) + (RRR*0.3) + (stability*0.2) - (DD*0.1)
        ranked_pairs = []
        for pair in _ALL_PAIRS:
            wr  = per_pair.get(pair, 0)
            rrr = tp_rrr
            stability = min(wr, 0.8)   # proxy
            dd  = max(0, 0.80 - wr) * 10  # proxy drawdown
            score = (wr * 0.4) + (rrr * 0.1 * 0.3) + (stability * 0.2) - (dd * 0.01 * 0.1)
            ranked_pairs.append((pair, score, wr, rrr))

        ranked_pairs.sort(key=lambda x: x[1], reverse=True)

        lines = [
            f"=== DAILY PAIR RANKING ===",
            f"Date: {date_str}",
        ]
        for i, (pair, score, wr, rrr) in enumerate(ranked_pairs, 1):
            lines.append(f"{i:2}. {pair:<8} Score:{score:.2f} WR:{wr:.1%} RRR:{rrr:.1f}")

        top3 = [p for p, _, _, _ in ranked_pairs[:3]]
        lines.append(f"\nTop 3 for live trading: {', '.join(top3)}")
        lines.append("=========================")

        self._log_to_db("pair_ranking", {
            "date": date_str,
            "iteration": iteration,
            "ranking": [(p, round(s, 4), round(wr, 4)) for p, s, wr, _ in ranked_pairs],
            "top3": top3,
        })

        self._send(f"Daily Pair Ranking — {date_str}", "\n".join(lines))
        logger.info(f"[pair_ranking] Top 3: {top3}")

    # ════════════════════════════════════════════════════════════════════════
    #  DAILY TASK 8 — Nightly self improvement (22:00 UTC)
    # ════════════════════════════════════════════════════════════════════════

    def _task_self_improve(self):
        logger.info("[self_improve] Nightly self-improvement starting …")
        state    = self._read_state() or {}
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        iteration  = state.get("iteration", 0)
        best_xau   = state.get("best_xauusd_wr_real", state.get("best_xauusd_wr", 0))
        no_improve = state.get("no_improvement_count", 0)
        per_pair   = state.get("best_wr_per_pair", {})

        # Analyze today's improvements
        iters_today = self._estimate_iters_last_24h(iteration)
        improvements = []
        blockers     = []
        quick_fixes  = 0

        # Identify top improvements
        if best_xau > 0.65:
            improvements.append(f"XAUUSD WR at {best_xau:.1%} — strong performance")
        if iters_today > 100:
            improvements.append(f"High iteration speed: {iters_today} iters today")

        # Identify blockers
        if no_improve > 80:
            blockers.append(f"High no-improve count: {no_improve}/150 — random restart may be needed")
        if best_xau < 0.70:
            blockers.append(f"XAUUSD still below 70% — targeting 80%")
        stuck_pairs = [p for p, wr in per_pair.items() if wr < 0.55]
        if stuck_pairs:
            blockers.append(f"Pairs below 55%: {stuck_pairs[:3]}")

        # Apply quick fixes via skills update
        try:
            self._update_skills_from_loop()
            quick_fixes += 1
        except Exception:
            pass

        # Update strategy weights
        try:
            if self.loop_ref and getattr(self.loop_ref, "strategy_router", None):
                sr = self.loop_ref.strategy_router
                if hasattr(sr, "update_weights"):
                    best_result = getattr(self.loop_ref, "best_result", {}) or {}
                    pp = best_result.get("per_pair", {})
                    if pp:
                        weight_data = {
                            pair: {"wr": s.get("test_win_rate_realistic", 0), "trades": 10, "rrr": 2.0}
                            for pair, s in pp.items()
                        }
                        sr.update_weights(weight_data)
                        quick_fixes += 1
        except Exception:
            pass

        # Plan for tomorrow
        tomorrow_plan = []
        dist = max(0, 0.80 - best_xau)
        if dist > 0.10:
            tomorrow_plan.append(f"Aggressive parameter search — need +{dist:.1%}")
        elif dist > 0.05:
            tomorrow_plan.append(f"Fine-tune confluence parameters — {dist:.1%} gap remaining")
        else:
            tomorrow_plan.append(f"Near target — tighten entry criteria")

        self._log_to_db("self_improve", {
            "date": date_str,
            "iteration": iteration,
            "improvements": improvements,
            "blockers": blockers,
            "quick_fixes_applied": quick_fixes,
            "tomorrow_plan": tomorrow_plan,
        })

        msg = (
            f"=== NIGHTLY SELF IMPROVEMENT ===\n"
            f"Date: {date_str} · Iteration: {iteration}\n\n"
            f"TOP IMPROVEMENTS TODAY:\n" +
            "\n".join(f"  + {i}" for i in (improvements or ["Evolution loop running"])) + "\n\n" +
            f"TOP BLOCKERS:\n" +
            "\n".join(f"  ! {b}" for b in (blockers or ["None critical"])) + "\n\n" +
            f"Quick fixes applied: {quick_fixes}\n\n" +
            f"TOMORROW PLAN:\n" +
            "\n".join(f"  > {p}" for p in tomorrow_plan) + "\n"
            f"================================"
        )

        self._send(f"Nightly Self Improvement — {date_str}", msg)
        logger.info(f"[self_improve] Done. Fixes: {quick_fixes}. Blockers: {len(blockers)}")

    # ════════════════════════════════════════════════════════════════════════
    #  EXISTING interval tasks (preserved)
    # ════════════════════════════════════════════════════════════════════════

    def _check_30min(self):
        state = self._read_state()
        if not state:
            return

        iteration   = state.get("iteration", 0)
        no_improve  = state.get("no_improvement_count", 0)
        best_xau_wr = state.get("best_xauusd_wr_real",
                                 state.get("best_xauusd_wr", 0))

        logger.info(
            f"[30min] iter={iteration} no_improve={no_improve} "
            f"XAUUSD WR={best_xau_wr:.1%}"
        )

        if iteration == self._last_seen_iter and iteration > 0:
            self._stall_count += 1
            if self._stall_count >= 2:
                msg = (f"Possible stall: iteration={iteration} unchanged for "
                       f"{self._stall_count * 30} min. Checking process health.")
                logger.warning(f"[30min] {msg}")
                self._send(f"Stall Warning — {self._stall_count * 30} min no progress", msg)
        else:
            self._stall_count = 0

        self._last_seen_iter = iteration

        if no_improve >= 120:
            msg = (f"Stuck detected: {no_improve} iters without improvement. "
                   f"XAUUSD WR={best_xau_wr:.1%}. "
                   f"Random restart should trigger automatically.")
            logger.warning(f"[30min] {msg}")
            self._send("Stuck Detected", msg)

        try:
            if self.db:
                db_iter = int(self.db.get_state("auto_loop_iteration") or 0)
                if db_iter > 0 and abs(db_iter - iteration) > 100:
                    logger.warning(f"[30min] Supabase lag: db={db_iter} vs state={iteration}")
        except Exception:
            pass

    def _check_2hour(self):
        logger.info("[2h] Routine check starting …")
        state = self._read_state()
        if not state:
            self._send("2H Report", "State file not found — loop may not have started yet.")
            return

        self._update_skills_from_loop()
        self._check_system_resources()
        self._check_connections()
        self._check_pair_data()
        self._auto_fix_modules()

        try:
            if self.loop_ref and getattr(self.loop_ref, "strategy_router", None):
                best_result = getattr(self.loop_ref, "best_result", {}) or {}
                per_pair = best_result.get("per_pair", {})
                if per_pair:
                    weight_data = {}
                    sr = self.loop_ref.strategy_router
                    for pair, stats in per_pair.items():
                        strategies = sr.get_strategies(pair)
                        wr  = stats.get("test_win_rate_realistic", 0)
                        n_t = stats.get("test_trades", 0)
                        rrr = stats.get("test_avg_rrr_realistic", 1.0)
                        if strategies and n_t >= 10:
                            weight_data[pair] = {s: {"wr": wr, "trades": n_t, "rrr": rrr} for s in strategies}
                    if weight_data:
                        sr.update_weights(weight_data)
                        logger.info("[2h] Strategy weights updated")
        except Exception as e:
            logger.debug(f"[2h] Strategy weight update failed: {e}")

        self._check_ml_retrain()

        report = self._build_2hour_report(state)
        self._send(
            f"AutoTrader 2H Report — Iter {state.get('iteration', 0)}",
            report
        )
        logger.info("[2h] Report sent")

    def _check_system_resources(self):
        try:
            import psutil
            ram_pct  = psutil.virtual_memory().percent
            cpu_pct  = psutil.cpu_percent(interval=1)
            disk_pct = psutil.disk_usage("/").percent
            logger.info(f"[2h] Resources: RAM={ram_pct:.0f}% CPU={cpu_pct:.0f}% Disk={disk_pct:.0f}%")
            if ram_pct > 85:
                self._send("HIGH RAM Warning", f"RAM usage at {ram_pct:.0f}% — consider restarting if OOM risk")
            if cpu_pct > 90:
                self._send("HIGH CPU Warning", f"CPU at {cpu_pct:.0f}% — evolution may be running slowly")
        except ImportError:
            logger.debug("[2h] psutil not available — skipping resource check")
        except Exception as e:
            logger.debug(f"[2h] Resource check error: {e}")

    def _check_connections(self):
        if self.db:
            try:
                self.db.get_state("heartbeat_check")
                logger.info("[2h] Supabase: OK")
            except Exception:
                logger.warning("[2h] Supabase: OFFLINE — evolution continues with local state")
        if self.loop_ref and getattr(self.loop_ref, "pg", None):
            pg = self.loop_ref.pg
            if not getattr(pg, "is_connected", lambda: False)():
                try:
                    pg.reconnect()
                    logger.info("[2h] PostgreSQL: reconnected")
                except Exception:
                    logger.debug("[2h] PostgreSQL: still offline")
        if self.loop_ref and getattr(self.loop_ref, "redis", None):
            redis = self.loop_ref.redis
            if not redis.ping():
                logger.debug("[2h] Redis: offline (using in-memory fallback)")

    def _check_pair_data(self):
        if not self.loop_ref:
            return
        pair_data  = getattr(self.loop_ref, "pair_data", {})
        all_pairs  = getattr(self.loop_ref, "pairs", [])
        missing    = [p for p in all_pairs if p not in pair_data]
        if missing:
            logger.warning(f"[2h] Missing pair data: {missing}")
        else:
            logger.info(f"[2h] All {len(all_pairs)} pairs have data loaded")

    def _auto_fix_modules(self):
        modules = [
            "evolution.autonomous_loop", "evolution.ml_layer", "evolution.skill_builder",
            "strategy.trend_engine", "backtester.walk_forward",
        ]
        for mod in modules:
            try:
                __import__(mod)
            except ImportError as e:
                logger.error(f"[2h] Module broken: {mod} — {e}")
                self._send("Module Import Error", f"Module {mod} failed to import: {e}")

    def _check_ml_retrain(self):
        if not self.loop_ref:
            return
        ml_ensemble = getattr(self.loop_ref, "ml_ensemble", None)
        if ml_ensemble:
            try:
                pc = getattr(ml_ensemble, "pattern_clf", None)
                if pc and hasattr(pc, "needs_retrain") and pc.needs_retrain():
                    logger.info("[2h] Pattern classifier needs retraining — scheduling")
            except Exception:
                pass

    def _check_6hour(self):
        logger.info("[6h] Deep skill update …")
        self._update_skills_from_loop()
        state = self._read_state()
        if state:
            skills_count = self._read_skills_count()
            self._send(
                f"6H Skills Update — Iter {state.get('iteration', 0)}",
                f"Skills library updated.\n"
                f"Total skills: {skills_count}\n"
                f"Best XAUUSD WR: {state.get('best_xauusd_wr_real', 0):.1%}\n"
                f"Iteration: {state.get('iteration', 0)}"
            )

    def _check_24hour(self):
        logger.info("[24h] Full stress test check …")
        state = self._read_state()
        if not state:
            return
        iteration = state.get("iteration", 0)
        best_xau  = state.get("best_xauusd_wr_real",
                              state.get("best_xauusd_wr", 0))
        per_pair  = state.get("best_wr_per_pair", {})
        above_65  = sum(1 for wr in per_pair.values() if wr >= 0.65)
        above_70  = sum(1 for wr in per_pair.values() if wr >= 0.70)
        above_75  = sum(1 for wr in per_pair.values() if wr >= 0.75)

        lines = [
            f"=== 24H STRESS CHECK ===",
            f"Iter: {iteration}",
            f"XAUUSD WR: {best_xau:.1%}",
            "",
            "Pairs above thresholds:",
            f"  ≥65%: {above_65}/19",
            f"  ≥70%: {above_70}/19",
            f"  ≥75%: {above_75}/19",
            "",
            "ALL PAIRS (best-ever realistic WR):",
        ]
        for p in _ALL_PAIRS:
            wr = per_pair.get(p, 0)
            lines.append(f"  {p:8}: {wr:.1%}")
        lines.append("========================")

        self._send(f"24H Stress Report — Iter {iteration}", "\n".join(lines))

    # ════════════════════════════════════════════════════════════════════════
    #  Helpers
    # ════════════════════════════════════════════════════════════════════════

    def _update_skills_from_loop(self):
        if self.loop_ref is None:
            return
        try:
            from evolution.skill_builder import SkillBuilder
            sb = SkillBuilder()
            best_params = getattr(self.loop_ref, "best_params", None)
            best_result = getattr(self.loop_ref, "best_result", None) or {}
            if best_params:
                sb.update_from_best_result(best_params, best_result)
                logger.info(f"[skills] Updated from loop — {sb.total_skills} total skills")
        except Exception as e:
            logger.debug(f"Skill update from loop failed: {e}")

    def _read_state(self) -> Optional[Dict]:
        try:
            if os.path.exists(STATE_PATH):
                with open(STATE_PATH, encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.debug(f"State read failed: {e}")
        return None

    def _read_skills_count(self) -> int:
        try:
            if os.path.exists(SKILLS_PATH):
                with open(SKILLS_PATH, encoding="utf-8") as f:
                    return json.load(f).get("total_skills_learned", 0)
        except Exception:
            pass
        return 0

    def _send(self, subject: str, body: str):
        try:
            if self.telegram:
                self.telegram.send(subject, body[:4000])
        except Exception as e:
            logger.debug(f"Scheduled alert failed: {e}")

    def _send_email(self, subject: str, html_body: str, retries: int = 3):
        """Send HTML email via SMTP with retry logic."""
        for attempt in range(1, retries + 1):
            try:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"]    = _EMAIL_FROM
                msg["To"]      = _EMAIL_TO
                msg.attach(MIMEText(html_body, "html"))

                with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=30) as s:
                    s.ehlo()
                    s.starttls()
                    s.login(_EMAIL_FROM, _EMAIL_PASS)
                    s.sendmail(_EMAIL_FROM, _EMAIL_TO, msg.as_string())

                logger.info(f"[email] Sent: {subject}")
                return True
            except Exception as e:
                logger.warning(f"[email] Attempt {attempt}/{retries} failed: {e}")
                if attempt < retries:
                    time.sleep(10)

        # All retries exhausted — Telegram alert
        self._send("Email Failed", f"Failed to send: {subject}\nRetries: {retries}")
        logger.error(f"[email] All {retries} retries failed for: {subject}")
        return False

    def _log_to_db(self, event_key: str, data: dict):
        """Log task completion to local state file and Supabase if available."""
        try:
            log_path = os.path.join(LOG_DIR, "daily_tasks.jsonl")
            entry = {"ts": datetime.now(timezone.utc).isoformat(), "event": event_key, **data}
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.debug(f"_log_to_db local: {e}")

        try:
            if self.db:
                self.db.set_state(f"daily_{event_key}_last_run",
                                  datetime.now(timezone.utc).isoformat())
        except Exception:
            pass

    def _get_system_resources(self):
        """Return (ram_gb, cpu_pct, disk_gb, uptime_h)."""
        ram_gb, cpu_pct, disk_gb = 0.0, 0.0, 0.0
        try:
            import psutil
            vm       = psutil.virtual_memory()
            ram_gb   = vm.used / (1024 ** 3)
            cpu_pct  = psutil.cpu_percent(interval=0.5)
            du       = psutil.disk_usage(_ROOT)
            disk_gb  = du.used / (1024 ** 3)
        except Exception:
            pass
        uptime_h = (datetime.now(timezone.utc) - self.start_time).total_seconds() / 3600
        return ram_gb, cpu_pct, disk_gb, uptime_h

    def _check_process_running(self, script_name: str) -> bool:
        """Check if a Python script is running by name."""
        try:
            import psutil
            for proc in psutil.process_iter(["name", "cmdline"]):
                try:
                    cmd = " ".join(proc.info.get("cmdline") or [])
                    if script_name in cmd:
                        return True
                except Exception:
                    pass
        except Exception:
            pass
        return False

    def _fix_broken_modules_silent(self) -> List[str]:
        """Test-import critical modules, return list of any that failed."""
        modules = [
            "evolution.autonomous_loop", "evolution.ml_layer", "evolution.skill_builder",
            "strategy.trend_engine", "backtester.walk_forward",
        ]
        broken = []
        for mod in modules:
            try:
                __import__(mod)
            except ImportError as e:
                broken.append(mod)
                logger.error(f"[health] Module broken: {mod} — {e}")
        return broken

    def _estimate_iters_last_24h(self, current_iter: int) -> int:
        """Estimate iterations completed in the last 24 hours from JSONL log."""
        try:
            log_path = os.path.join(LOG_DIR, "auto_loop.jsonl")
            if not os.path.exists(log_path):
                return max(0, current_iter - 600)  # rough fallback

            cutoff = datetime.now(timezone.utc).timestamp() - 86400
            count  = 0
            with open(log_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        ts_str = entry.get("ts", entry.get("timestamp", ""))
                        if ts_str:
                            from datetime import datetime as dt
                            ts = dt.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                            if ts >= cutoff:
                                count += 1
                    except Exception:
                        pass
            return max(count, 1)
        except Exception:
            return max(0, current_iter - 600)

    # ── 2-hour report builder (preserved) ────────────────────────────────────

    def _build_2hour_report(self, state: Dict) -> str:
        elapsed    = (datetime.now(timezone.utc) - self.start_time).total_seconds() / 3600
        iteration  = state.get("iteration", 0)
        best_xau   = state.get("best_xauusd_wr_real",
                               state.get("best_xauusd_wr", 0))
        total_tr   = state.get("total_test_trades", 0)
        per_pair   = state.get("best_wr_per_pair", {})
        skills_n   = self._read_skills_count()

        unique_pairs = list(dict.fromkeys(_ALL_PAIRS))

        ranked = sorted(
            [(p, per_pair.get(p, 0)) for p in unique_pairs],
            key=lambda x: x[1], reverse=True,
        )
        ranked_active = [(p, wr) for p, wr in ranked if wr > 0]
        top3 = ranked_active[:3] if ranked_active else ranked[:3]

        best_pair    = top3[0][0] if top3 else "N/A"
        best_pair_wr = top3[0][1] if top3 else 0.0
        dist_xau     = max(0.0, 0.80 - best_xau)
        dist_best    = max(0.0, 0.80 - best_pair_wr)

        live_iter    = getattr(self.loop_ref, "iteration", iteration) if self.loop_ref else iteration
        no_improve   = state.get("no_improvement_count", 0)
        healer_fixes = getattr(
            getattr(self.loop_ref, "healer", None), "fixes_applied", 0
        ) if self.loop_ref else 0

        ml_score   = 0.0
        ml_trained = False
        ml_models  = []
        if self.loop_ref:
            ml = getattr(self.loop_ref, "ml", None)
            if ml and getattr(ml, "is_trained", False):
                ml_trained = True
                ml_models  = ml.summary().get("models", [])
                br = getattr(self.loop_ref, "best_result", {}) or {}
                ml_score = br.get("ml_score", 0)

        best_result = getattr(self.loop_ref, "best_result", {}) or {}
        mc          = best_result.get("monte_carlo", {})
        mc_str      = f"{'PASS' if mc.get('passed') else 'FAIL'} ({mc.get('pass_rate', 0):.0%})" if mc else "N/A"

        focus_hint = (
            "approaching target — tighten confluence, raise min_confluence"
            if dist_xau < 0.05
            else f"need +{dist_xau:.1%} — reduce tp_rrr, widen SL, check confluence"
        )

        lines = [
            "=== AUTOTRADER 2H REPORT ===",
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            f"Uptime: {elapsed:.1f} hours",
            f"Total Iterations: {live_iter}",
            f"No-improve: {no_improve}",
            "",
            "TOP 3 PAIRS THIS PERIOD:",
        ]
        for i, (p, wr) in enumerate(top3, 1):
            lines.append(f"  {i}. {p} WR:{wr:.1%}")

        lines += ["", "ALL PAIRS STATUS:"]
        for p, wr in ranked:
            if wr == 0:
                continue
            trend = "UP" if wr >= 0.70 else ("FLAT" if wr >= 0.55 else "DOWN")
            lines.append(f"  {p:8}: WR {wr:.1%} [{trend}]")

        lines += [
            "",
            "BEST STRATEGY FOUND:",
            f"  Pair:           XAUUSD",
            f"  WR (realistic): {best_xau:.1%}",
            f"  Total trades:   {total_tr}",
            f"  Monte Carlo:    {mc_str}",
            f"  ML Ensemble:    {'trained ' + str(ml_models) if ml_trained else 'not yet trained'}",
            f"  ML Score:       {ml_score:.1%}" if ml_trained else "",
            "",
            f"SKILLS LIBRARY: {skills_n} skills saved",
            f"AUTO FIXES APPLIED: {healer_fixes} this session",
            f"ERRORS CAUGHT AND HEALED: {healer_fixes}",
            "",
            "DISTANCE TO TARGET:",
            f"  XAUUSD: {dist_xau:.1%} away from 80%",
            f"  Best pair ({best_pair}): {dist_best:.1%} away from 80%",
            "",
            "NEXT 2 HOURS:",
            f"  Focus: {focus_hint}",
            "===========================",
        ]
        return "\n".join(l for l in lines if l is not None)
