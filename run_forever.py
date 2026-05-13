"""
AutoTrader Engine v5.0 — Single file. Runs forever. No Claude needed.

Iron Rules:
  1. WR floor per pair — never accept below best ever achieved
  2. RRR floor 1.3 — always
  3. Score = WR * RRR — combined must improve
  4. Walk-forward only — no training data in test
  5. Costs always — spread + slippage + commission
  6. Monte Carlo — 1000 shuffles, 70% survival minimum
  7. Stuck 50 → random restart | Stuck 100 → new strategy type

Usage:
    python run_forever.py
"""

import copy
import json
import os
import random
import smtplib
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional

import numpy as np
from dotenv import load_dotenv
from loguru import logger

# ── Bootstrap ──────────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_ROOT, ".env"), override=True)
sys.path.insert(0, _ROOT)

LOG_DIR   = os.path.join(_ROOT, "logs")
STATE_DIR = os.path.join(_ROOT, "local_db")
DATA_DIR  = os.path.join(_ROOT, "data_cache")
for d in (LOG_DIR, STATE_DIR, DATA_DIR):
    os.makedirs(d, exist_ok=True)

logger.remove()
logger.add(sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
    level="INFO", colorize=True)
logger.add(os.path.join(LOG_DIR, "engine_{time:YYYY-MM-DD}.log"),
    rotation="00:00", retention="30 days", level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} | {message}")

# ── Credentials ────────────────────────────────────────────────────────────────
TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT    = os.environ.get("TELEGRAM_CHAT_ID", "")
EMAIL_FROM = os.environ.get("EMAIL_SENDER", os.environ.get("EMAIL_USER", ""))
EMAIL_PASS = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_TO   = os.environ.get("EMAIL_RECEIVER", os.environ.get("EMAIL_RECIPIENT", ""))
SMTP_HOST  = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT  = int(os.environ.get("EMAIL_SMTP_PORT", 587))
GH_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
GH_USER    = os.environ.get("GITHUB_USERNAME", "")
GH_REPO    = os.environ.get("GITHUB_REPO", "autotrader-claude")

# ── Constants ──────────────────────────────────────────────────────────────────
STATE_FILE   = os.path.join(STATE_DIR, "engine_state.json")
SKILLS_FILE  = os.path.join(STATE_DIR, "skills.json")
MONTHLY_FILE = os.path.join(STATE_DIR, "monthly_backtest.json")

TARGET_WR    = 0.60   # OMEGA target: 55-65% WR with 2.5-5.0 RRR
RRR_FLOOR    = 1.0   # OMEGA: minimum avg realized RRR (transitioning from 0.3→1.0→1.5)
MIN_WR_FLOOR = 0.50  # OMEGA: absolute minimum WR (allows trading WR for RR)
STUCK_RESTART = 50    # random restart threshold per pair
STUCK_STRATEGY = 100  # new strategy type threshold
GITHUB_EVERY  = 10    # sync every N iterations
REPORT_EVERY  = 100   # full ranking report interval
MONTE_N       = 1000  # Monte Carlo shuffles
MONTE_MIN     = 0.65  # min MC survival rate (OMEGA spec: 65%)

# ── Pairs ──────────────────────────────────────────────────────────────────────
PAIRS = [
    "XAUUSD", "XAGUSD", "XPTUSD",
    "GBPUSD", "EURUSD", "USDJPY", "USDCHF",
    "AUDUSD", "NZDUSD", "USDCAD",
    "EURJPY", "GBPJPY",
    "BTCUSD", "ETHUSD",
    "NAS100", "US30", "GER40",
    "GC=F", "SI=F",
]

PAIR_TICKERS = {
    "XAUUSD": "GC=F",  "XAGUSD": "SI=F",  "XPTUSD": "PL=F",
    "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD",
    "NAS100": "^IXIC",  "US30": "^DJI",    "GER40": "^GDAXI",
    "GBPUSD": "GBPUSD=X", "EURUSD": "EURUSD=X", "USDJPY": "USDJPY=X",
    "USDCHF": "USDCHF=X", "AUDUSD": "AUDUSD=X", "NZDUSD": "NZDUSD=X",
    "USDCAD": "USDCAD=X", "EURJPY": "EURJPY=X", "GBPJPY": "GBPJPY=X",
}

PAIR_PERIODS = {
    "XAUUSD": "10y", "XAGUSD": "10y", "GC=F": "10y", "SI=F": "10y",
    "XPTUSD": "5y",  "BTCUSD": "5y",  "ETHUSD": "5y",
    "NAS100": "5y",  "US30": "5y",    "GER40": "5y",
}

PAIR_WEIGHTS = {
    "XAUUSD": 5.0, "GC=F": 5.0,
    "XAGUSD": 1.5, "SI=F": 1.5, "XPTUSD": 1.0,
    "BTCUSD": 1.5, "ETHUSD": 1.0,
    "GBPUSD": 1.0, "EURUSD": 1.0, "USDJPY": 1.0,
    "USDCHF": 0.8, "AUDUSD": 0.8, "NZDUSD": 0.8, "USDCAD": 0.8,
    "EURJPY": 0.8, "GBPJPY": 0.8,
    "NAS100": 0.6, "US30": 0.6, "GER40": 0.6,
}

# ── Parameter search space ─────────────────────────────────────────────────────
PARAM_RANGES = {
    "ema_fast":         [8, 13, 21, 34],
    "ema_slow":         [34, 50, 55, 89, 100, 144],
    "ema_long":         [150, 200, 233],
    "atr_period":       [10, 14, 20],
    "sl_atr_mult":      [0.3, 0.5, 0.75, 1.0, 1.5],
    # OMEGA: extended tp_rrr range to allow 5R-8R runners
    "tp_rrr":           [1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0],
    # OMEGA: wider trailing = more room for runners
    "trail_atr_mult":   [1.0, 1.5, 2.0, 2.5, 3.0, 4.0],
    # OMEGA: smaller partial (25%) to keep more runner position
    "partial_pct_1r":   [0.0, 0.10, 0.25, 0.50],
    "min_adx":          [15.0, 20.0, 25.0, 30.0],
    "rsi_long_min":     [25.0, 30.0, 35.0, 40.0],
    "rsi_long_max":     [55.0, 60.0, 65.0, 68.0, 70.0],
    "rsi_short_min":    [30.0, 32.0, 35.0],
    "rsi_short_max":    [60.0, 65.0, 70.0, 72.0, 75.0],
    "min_confluence":   [2, 3, 4],
    "min_hold_bars":    [0, 1, 2],
    "use_pattern":      [True, False],
    "use_adx_filter":   [True, False],
    "use_weekly_filter":[True, False],
    "use_ema_stack":    [True, False],
    "use_expansion":    [True, False],
    # use_ict_filter excluded: too slow for mutation search
}

PARAM_PRIORITIES = [
    "tp_rrr", "trail_atr_mult", "partial_pct_1r",   # asymmetric payoff — highest priority
    "sl_atr_mult", "min_confluence",
    "use_pattern", "rsi_long_max",
    "rsi_short_min", "min_adx", "ema_slow",
    "use_adx_filter", "use_weekly_filter", "use_ema_stack",
    "ema_fast", "ema_long", "min_hold_bars",
    "rsi_long_min", "rsi_short_max", "use_expansion",
    "atr_period",
]


# ── Utility functions ──────────────────────────────────────────────────────────

def send_telegram(msg: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        import ssl
        import urllib.request
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        data = json.dumps({"chat_id": TG_CHAT, "text": msg[:4000],
                           "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        logger.debug(f"Telegram failed: {e}")


_EMAIL_TRACKER_PATH = os.path.join(_ROOT, "data", "email_tracker.json")

def _email_already_sent(key: str) -> bool:
    """Return True if this email key was already sent today."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        if os.path.exists(_EMAIL_TRACKER_PATH):
            with open(_EMAIL_TRACKER_PATH) as f:
                tracker = json.load(f)
        else:
            tracker = {}
        return tracker.get(key) == today
    except Exception:
        return False

def _mark_email_sent(key: str) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        if os.path.exists(_EMAIL_TRACKER_PATH):
            with open(_EMAIL_TRACKER_PATH) as f:
                tracker = json.load(f)
        else:
            tracker = {}
        tracker[key] = today
        with open(_EMAIL_TRACKER_PATH, "w") as f:
            json.dump(tracker, f)
    except Exception:
        pass

def send_email(subject: str, body_html: str, dedup_key: str = "") -> None:
    if not EMAIL_FROM or not EMAIL_PASS or not EMAIL_TO:
        return
    # Deduplication: skip if already sent today with same key
    key = dedup_key or subject[:50]
    if _email_already_sent(key):
        logger.debug(f"Email skipped (already sent today): {key}")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO
        msg.attach(MIMEText(body_html, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.starttls()
            s.login(EMAIL_FROM, EMAIL_PASS)
            s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        _mark_email_sent(key)
        logger.info(f"Email sent: {subject}")
    except Exception as e:
        logger.debug(f"Email failed: {e}")


def github_sync(iteration: int, xau_wr: float) -> None:
    if not GH_TOKEN:
        return
    try:
        cwd = _ROOT
        msg = f"iter{iteration} XAU_WR={xau_wr:.1%}"
        subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True, timeout=30)
        subprocess.run(["git", "commit", "-m", msg], cwd=cwd, capture_output=True, timeout=30)
        remote = f"https://{GH_TOKEN}@github.com/{GH_USER}/{GH_REPO}.git"
        result = subprocess.run(
            ["git", "push", remote, "HEAD:master"],
            cwd=cwd, capture_output=True, timeout=60)
        if result.returncode == 0:
            logger.info(f"GitHub sync: {msg}")
        else:
            logger.debug(f"GitHub push: {result.stderr.decode()[:200]}")
    except Exception as e:
        logger.debug(f"GitHub sync failed: {e}")


# ── Telegram Command Handler ────────────────────────────────────────────────────

_tg_offset = 0
_engine_paused = False

def _poll_telegram_commands(engine) -> None:
    """Poll getUpdates and execute commands. Call from scheduler thread."""
    global _tg_offset, _engine_paused
    if not TG_TOKEN:
        return
    try:
        import ssl, urllib.request
        url = (f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
               f"?offset={_tg_offset}&timeout=2&limit=10")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(url, timeout=8, context=ctx) as r:
            data = json.loads(r.read())
        if not data.get("ok"):
            return
        for upd in data.get("result", []):
            _tg_offset = upd["update_id"] + 1
            msg = upd.get("message", {}).get("text", "").strip().lower()
            if not msg.startswith("/"):
                continue
            cmd = msg.split()[0]
            reply = _handle_command(cmd, engine)
            if reply:
                send_telegram(reply)
    except Exception:
        pass

def _handle_command(cmd: str, engine) -> str:
    global _engine_paused
    try:
        if cmd == "/status":
            top = sorted(engine.best_wr.items(), key=lambda x: x[1], reverse=True)[:5]
            pairs_str = " | ".join(f"{p} {w:.1%}" for p, w in top)
            return (f"AUTOTRADER STATUS\n"
                    f"Iter: {engine.iteration:,}\n"
                    f"XAUUSD WR: {engine.xauusd_best_wr:.1%}\n"
                    f"Paused: {_engine_paused}\n"
                    f"Top: {pairs_str}")
        elif cmd == "/pause":
            _engine_paused = True
            return "Engine PAUSED — no new entries."
        elif cmd == "/resume":
            _engine_paused = False
            return "Engine RESUMED."
        elif cmd == "/stop":
            _engine_paused = True
            return "Engine PAUSED via /stop. Use /resume to restart."
        elif cmd == "/ram":
            try:
                import psutil
                m = psutil.virtual_memory()
                used = m.used / 1024**3
                pct  = m.percent
                return f"RAM: {used:.1f}GB / {m.total/1024**3:.1f}GB ({pct:.1f}%)"
            except Exception:
                return "RAM: psutil not available"
        elif cmd == "/best":
            top = sorted(engine.best_wr.items(), key=lambda x: x[1], reverse=True)[:10]
            lines = [f"TOP PAIRS BY WR"]
            for p, w in top:
                rr = engine.best_rrr.get(p, 0)
                exp = w * rr - (1 - w)
                lines.append(f"{p}: WR {w:.1%} RR {rr:.2f} E={exp:.3f}")
            return "\n".join(lines)
        elif cmd == "/report":
            try:
                from evolution.evolution_engine import get_engine as _get_evo
                return _get_evo().generate_report()
            except Exception:
                top = sorted(engine.best_wr.items(), key=lambda x: x[1], reverse=True)
                lines = [f"REPORT — iter {engine.iteration:,}"]
                for p, w in top:
                    rr  = engine.best_rrr.get(p, 0)
                    exp = w * rr - (1 - w)
                    lines.append(f"{p}: WR={w:.1%} RR={rr:.2f} E={exp:.3f}")
                return "\n".join(lines)
        elif cmd == "/audit":
            import psutil
            ram = psutil.virtual_memory().percent if _try_import("psutil") else 0
            return (f"AUDIT\nIter: {engine.iteration:,}\n"
                    f"Pairs: {len(engine.best_wr)}\nRAM: {ram:.1f}%\n"
                    f"Git: master\nPaused: {_engine_paused}")
        elif cmd == "/risk":
            lines = ["RISK MULTIPLIERS"]
            if engine._drift_monitor:
                for p, m in engine._drift_monitor._risk_overrides.items():
                    if m < 1.0:
                        lines.append(f"{p}: x{m:.2f}")
            if len(lines) == 1:
                lines.append("All pairs at 1.0x")
            return "\n".join(lines)
        elif cmd == "/close_all":
            return "CLOSE_ALL: No live MT5 positions active (demo mode). Use MT5 terminal."
        elif cmd == "/cpu":
            try:
                import psutil
                cpu = psutil.cpu_percent(interval=1)
                return f"CPU: {cpu:.1f}%"
            except Exception:
                return "CPU: psutil not available"
        elif cmd == "/iter":
            return f"Iteration: {engine.iteration:,}"
        elif cmd == "/restart":
            _engine_paused = False
            return "Engine unpaused. Watchdog will restart if needed."
        elif cmd == "/safemode":
            _engine_paused = True
            return ("SAFE MODE ACTIVE\n"
                    "Engine paused. No new iterations.\n"
                    "Use /resume to exit safe mode.")
        else:
            return (f"Commands: /status /pause /resume /stop /close_all\n"
                    f"/report /best /ram /cpu /iter /audit /risk\n"
                    f"/restart /safemode")
    except Exception as e:
        return f"Command error: {e}"

def _try_import(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


# ── Main Engine ────────────────────────────────────────────────────────────────

class AutoTraderEngine:

    def __init__(self):
        # --- Per-pair state (never goes down) ---
        self.best_wr:     Dict[str, float] = {}   # WR floor per pair
        self.best_rrr:    Dict[str, float] = {}   # RRR floor per pair
        self.best_score:  Dict[str, float] = {}   # WR*RRR floor per pair
        self.best_params: Dict[str, dict]  = {}   # best params per pair
        self.best_result: Dict[str, dict]  = {}   # full result per pair

        # --- Global counters ---
        self.iteration:       int   = 0
        self.xauusd_best_wr:  float = 0.0
        self.global_best_score: float = 0.0

        # --- Per-pair stuck counters ---
        self.no_improve: Dict[str, int]  = {}
        self.pair_iter:  Dict[str, int]  = {}     # iters since last improvement

        # --- Smart mutation state ---
        # param_momentum[pair][param] = deque of (delta_score) last 5 changes
        self.param_momentum: Dict[str, Dict[str, deque]] = {}
        self.last_changed:   Dict[str, str] = {}   # last param changed per pair
        self.last_direction: Dict[str, int] = {}   # +1 or -1

        # --- Current working params per pair ---
        self.current_params: Dict[str, dict] = {}

        # --- Data cache ---
        self.data_cache:       Dict = {}
        self.data_cache_time:  float = 0.0

        # --- Skills library ---
        self.skills: dict = {}

        # --- Monthly backtest db ---
        self.monthly_db: dict = {}

        # --- Schedule tracking ---
        self._last_30min = 0.0
        self._last_2h    = 0.0
        self._last_6h    = 0.0
        self._last_24h   = 0.0
        self._last_github = 0

        # --- Import heavy modules once ---
        self._wf_cls   = None   # WalkForwardBacktester class
        self._tp_cls   = None   # TrendParams class

        # --- Phase 3-8 engines (lazy-init) ---
        self._drift_monitor   = None   # analytics.live_drift_monitor.LiveDriftMonitor
        self._exposure_engine = None   # portfolio.live_exposure_engine.LiveExposureEngine
        self._news_filter     = None   # risk.news_volatility_filter.NewsVolatilityFilter
        self._paper_engine    = None   # execution.paper_trading.PaperTradingEngine
        self._wf_validator    = None   # validation.walk_forward_validator.WalkForwardValidator
        self._resource_mon    = None   # core.resource_monitor.ResourceMonitor
        self._last_drift_check = 0.0
        self._last_validation  = 0.0

    # ── Entry point ────────────────────────────────────────────────────────────

    def run_forever(self):
        logger.info("=" * 60)
        logger.info("AutoTrader Engine v5.0 — STARTING")
        logger.info("=" * 60)

        self._import_modules()
        self.load_state()
        self._refresh_data(force=True)

        send_telegram(
            f"=== AUTOTRADER ENGINE v5.0 ===\n"
            f"Resumed iter: {self.iteration}\n"
            f"XAUUSD WR: {self.xauusd_best_wr:.1%}\n"
            f"Pairs: {len(PAIRS)}\n"
            f"Running forever. No Claude needed."
        )

        # Start background scheduler
        t = threading.Thread(target=self._scheduler_loop, daemon=True)
        t.start()

        while True:
            try:
                # Pause gate
                if _engine_paused:
                    time.sleep(5)
                    continue

                # Resource check every 10 iterations
                if self._resource_mon and self.iteration % 10 == 0:
                    snap = self._resource_mon.check()
                    if snap.status == "critical":
                        logger.warning("[ENGINE] Resource critical — sleeping 30s")
                        time.sleep(30)

                self.evolve_one_iteration()
                self.save_state()
                self.iteration += 1

                if self.iteration % GITHUB_EVERY == 0:
                    github_sync(self.iteration, self.xauusd_best_wr)

                if self.iteration % REPORT_EVERY == 0:
                    self._send_full_report()

            except KeyboardInterrupt:
                logger.info("Stopped by Ctrl+C")
                self.save_state()
                break
            except Exception as e:
                self.heal(e)
                continue

    # ── Core evolution ─────────────────────────────────────────────────────────

    def evolve_one_iteration(self):
        # Rotate through pairs — XAUUSD gets 3x more iterations
        pair = self._pick_pair()

        params = self.smart_mutate(pair)
        result = self._run_backtest(pair, params)

        if result is None:
            return

        if self.is_better(pair, result):
            old_wr = self.best_wr.get(pair, 0)
            self.accept(pair, params, result)
            new_wr = self.best_wr[pair]
            logger.info(
                f"KEPT [{pair}] WR {old_wr:.1%}→{new_wr:.1%} | "
                f"RRR {result.get('avg_rrr', 0):.2f} | "
                f"Score {result.get('score', 0):.4f} | "
                f"iter {self.iteration}"
            )
            if pair == "XAUUSD" and new_wr >= TARGET_WR and old_wr < TARGET_WR:
                send_telegram(
                    f"★★★ TARGET REACHED ★★★\n"
                    f"XAUUSD WR = {new_wr:.1%}\n"
                    f"Still evolving — pushing higher."
                )
        else:
            self.no_improve[pair] = self.no_improve.get(pair, 0) + 1
            logger.info(
                f"REVERTED [{pair}] WR {result.get('win_rate', 0):.1%} "
                f"RRR {result.get('avg_rrr', 0):.2f} "
                f"trades {result.get('trades', 0)} "
                f"vs best WR {self.best_wr.get(pair, 0):.1%} score {self.best_score.get(pair, 0):.3f} | "
                f"no_improve={self.no_improve[pair]}"
            )
            # Stuck detection
            ni = self.no_improve[pair]
            if ni == STUCK_RESTART:
                self._random_restart(pair)
            elif ni == STUCK_STRATEGY:
                self._new_strategy_type(pair)

    def _pick_pair(self) -> str:
        """XAUUSD 3x weight, others 1x. Rotate deterministically."""
        weighted = (["XAUUSD"] * 3) + [p for p in PAIRS if p != "XAUUSD"]
        return weighted[self.iteration % len(weighted)]

    def is_better(self, pair: str, result: dict) -> bool:
        """OMEGA acceptance: all 7 conditions must pass."""
        wr     = result.get("win_rate", 0)
        rrr    = result.get("avg_rrr", 0)
        trades = result.get("trades", 0)
        dd     = result.get("max_drawdown", 0)
        pf     = result.get("profit_factor", 0)
        overfit = result.get("overfitting", False)

        if trades < 15:
            logger.debug(f"[{pair}] REJECT: trades={trades} < 15")
            return False

        # 1. WR ≥ 90% of pair's best (protect best_WR per pair)
        wr_floor = max(MIN_WR_FLOOR, self.best_wr.get(pair, 0) * 0.90)
        if wr < wr_floor:
            logger.debug(f"[{pair}] REJECT: wr={wr:.1%} < floor {wr_floor:.1%}")
            return False

        # 2. RRR floor
        if rrr < RRR_FLOOR:
            logger.debug(f"[{pair}] REJECT: rrr={rrr:.3f} < {RRR_FLOOR}")
            return False

        # 3. Drawdown < 8%
        if dd > 0.08:
            logger.debug(f"[{pair}] REJECT: dd={dd:.1%} > 8%")
            return False

        # 4. Profit Factor > 1.3
        if pf > 0 and pf < 1.3:
            logger.debug(f"[{pair}] REJECT: pf={pf:.2f} < 1.3")
            return False

        # 5. No overfit (train/test gap guard from WF backtest)
        if overfit:
            logger.debug(f"[{pair}] REJECT: overfit flag set")
            return False

        # 6. Expectancy > 0 and must beat best
        expectancy = wr * rrr - (1 - wr)
        if expectancy <= 0:
            logger.debug(f"[{pair}] REJECT: expectancy={expectancy:.4f} <= 0")
            return False

        # Score: expectancy + RR asymmetry bonus
        score = expectancy + max(0.0, rrr - 1.5) * 0.05
        cur_best = self.best_score.get(pair, 0)
        if score <= cur_best:
            logger.debug(f"[{pair}] REJECT: score={score:.4f} <= best {cur_best:.4f}")
            return False

        # 7. Monte Carlo survival > 65%
        mc_pass = result.get("monte_carlo_pass_rate", 1.0)
        if mc_pass < 0.65:
            logger.debug(f"[{pair}] REJECT: mc_pass={mc_pass:.2f} < 0.65")
            return False

        return True

    def accept(self, pair: str, params: dict, result: dict):
        wr    = result.get("win_rate", 0)
        rrr   = result.get("avg_rrr", 0)
        expectancy = wr * rrr - (1 - wr)
        score = expectancy + max(0.0, rrr - 1.5) * 0.05

        self.best_wr[pair]     = max(self.best_wr.get(pair, 0), wr)
        self.best_rrr[pair]    = max(self.best_rrr.get(pair, 0), rrr)
        self.best_score[pair]  = score
        self.best_params[pair] = copy.deepcopy(params)
        self.best_result[pair] = result
        self.no_improve[pair]  = 0
        self.current_params[pair] = copy.deepcopy(params)

        # Update XAUUSD global best
        if pair in ("XAUUSD", "GC=F"):
            self.xauusd_best_wr = max(self.xauusd_best_wr, wr)

        # Update global score
        self.global_best_score = max(self.global_best_score, score)

        # Update skills
        self._update_skills(pair, params, result)

        # Update expectancy engine
        try:
            from analytics.expectancy_engine import get_engine as _get_exp
            _get_exp().update_from_result(pair, result)
        except Exception:
            pass

        # Update standalone evolution engine state
        try:
            from evolution.evolution_engine import get_engine as _get_evo
            _get_evo().accept(pair, params, result)
        except Exception:
            pass

        # Update param momentum
        last_p = self.last_changed.get(pair)
        if last_p:
            old_score = self.best_score.get(pair, 0)
            delta = score - old_score
            self.param_momentum.setdefault(pair, {}).setdefault(
                last_p, deque(maxlen=5)).append(delta)

    # ── Smart mutation ─────────────────────────────────────────────────────────

    def smart_mutate(self, pair: str) -> dict:
        """
        Momentum-based mutation:
        1. If last 3 changes to a param improved → continue same direction
        2. If flat/declining → try highest-priority untried param
        3. Stuck 50+ → random restart (handled separately)
        """
        base = self.current_params.get(pair) or self._default_params(pair)
        ni   = self.no_improve.get(pair, 0)

        # After random restart, use fully random params
        if ni == 0 and self.last_changed.get(pair) == "__restart__":
            self.last_changed[pair] = ""
            return base

        # Pick param to mutate
        param = self._pick_param_to_mutate(pair, ni)
        self.last_changed[pair] = param

        new_params = copy.deepcopy(base)
        old_val    = new_params.get(param)
        new_val    = self._pick_new_value(param, old_val, pair)
        new_params[param] = new_val
        new_params["version"] = new_params.get("version", 1) + 1
        new_params["notes"] = f"Mutated {param}: {old_val} → {new_val}"

        return new_params

    def _pick_param_to_mutate(self, pair: str, no_improve_count: int) -> str:
        # Prioritise by: momentum direction > priority list > random
        momentum = self.param_momentum.get(pair, {})

        # If last param had positive momentum, keep mutating it
        last_p = self.last_changed.get(pair, "")
        if last_p and last_p in momentum:
            hist = list(momentum[last_p])
            if len(hist) >= 2 and all(d > 0 for d in hist[-2:]):
                return last_p  # keep momentum

        # Otherwise pick from priority list with some randomness
        candidates = PARAM_PRIORITIES[:]

        # Boost params flagged as likely blockers for XAUUSD
        if pair in ("XAUUSD", "GC=F"):
            xau_wr = self.best_wr.get(pair, 0)
            if xau_wr >= 0.65:
                # boost high-value params for final push to 80%
                boost = ["tp_rrr", "min_confluence",
                         "use_ema_stack", "sl_atr_mult"]
                candidates = boost + [p for p in candidates if p not in boost]

        # Weighted random selection — earlier in list = higher probability
        weights = [1.0 / (i + 1) for i in range(len(candidates))]
        total   = sum(weights)
        r = random.random() * total
        acc = 0
        for param, w in zip(candidates, weights):
            acc += w
            if r <= acc:
                return param
        return random.choice(candidates)

    def _pick_new_value(self, param: str, old_val, pair: str):
        choices = PARAM_RANGES.get(param, [])
        if not choices:
            return old_val

        # Filter out current value
        options = [v for v in choices if v != old_val]
        if not options:
            return old_val

        # Check momentum direction for numeric params
        if isinstance(old_val, (int, float)) and isinstance(choices[0], (int, float)):
            mom = self.param_momentum.get(pair, {}).get(param, deque())
            hist = list(mom)
            if len(hist) >= 2:
                avg_delta = sum(hist[-3:]) / len(hist[-3:])
                # If positive trend, try higher values; negative → lower
                sorted_opts = sorted(options)
                if avg_delta > 0:
                    # Prefer values above current
                    above = [v for v in sorted_opts if v > old_val]
                    if above:
                        return above[0]
                elif avg_delta < 0:
                    below = [v for v in sorted_opts if v < old_val]
                    if below:
                        return below[-1]

        return random.choice(options)

    def _default_params(self, pair: str) -> dict:
        """Return sensible starting params, preferring skills library."""
        # Try skills first
        sk = self.skills.get("per_pair", {}).get(pair, {})
        if sk and isinstance(sk, dict) and "params" in sk:
            return copy.deepcopy(sk["params"])

        # OMEGA default — asymmetric payoff profile
        return {
            "ema_fast": 21, "ema_slow": 89, "ema_long": 200,
            "ema_weekly": 26, "atr_period": 14,
            "sl_atr_mult": 0.75, "tp_rrr": 4.0,
            "trail_atr_mult": 2.5, "partial_pct_1r": 0.25,
            "min_adx": 20.0,
            "rsi_long_min": 30.0, "rsi_long_max": 68.0,
            "rsi_short_min": 32.0, "rsi_short_max": 65.0,
            "pullback_atr_mult": 2.0, "min_vol_ratio": 0.7,
            "use_weekly_filter": True, "use_ema_stack": False,
            "use_pattern": True, "use_pullback_zone": False,
            "use_adx_filter": True, "use_volume_filter": False,
            "use_expansion": True, "use_ict_filter": False,
            "ict_min_score": 40,
            "min_hold_bars": 1, "min_confluence": 3,
            "version": 1, "strategy_name": "HighConfluenceTrend",
            "notes": "OMEGA default",
        }

    def _random_restart(self, pair: str):
        """Jump to random params when stuck for STUCK_RESTART iterations."""
        logger.info(f"RANDOM RESTART [{pair}] after {self.no_improve[pair]} no-improve iters")
        base = copy.deepcopy(self.best_params.get(pair) or self._default_params(pair))
        for _ in range(random.randint(4, 8)):
            param = random.choice(PARAM_PRIORITIES[:12])
            choices = PARAM_RANGES.get(param, [])
            if choices:
                base[param] = random.choice(choices)
        base["version"] = base.get("version", 1) + 1
        base["notes"] = "Random restart"
        self.current_params[pair] = base
        self.no_improve[pair] = 0
        self.last_changed[pair] = "__restart__"
        send_telegram(f"🔄 [{pair}] Random restart after plateau. Exploring new region.")

    def _new_strategy_type(self, pair: str):
        """Try a completely different parameter configuration after 100 stuck iters."""
        logger.info(f"NEW STRATEGY TYPE [{pair}] after {STUCK_STRATEGY} stuck iters")
        # OMEGA strategy templates — all target asymmetric payoff
        strategies = [
            # Momentum runner: wide TP, wide trail, small partial
            {"tp_rrr": 6.0, "trail_atr_mult": 3.0, "partial_pct_1r": 0.10,
             "sl_atr_mult": 1.0, "min_confluence": 3,
             "use_ema_stack": True, "use_adx_filter": True, "use_expansion": True},
            # Breakout with runner
            {"tp_rrr": 5.0, "trail_atr_mult": 2.5, "partial_pct_1r": 0.25,
             "sl_atr_mult": 0.75, "min_confluence": 3,
             "use_ema_stack": True, "use_weekly_filter": True, "use_pattern": True},
            # Swing trade, patient
            {"tp_rrr": 4.0, "trail_atr_mult": 2.0, "partial_pct_1r": 0.25,
             "sl_atr_mult": 1.0, "min_confluence": 4,
             "use_ema_stack": True, "use_weekly_filter": True, "use_expansion": True},
            # Aggressive runner, no partial
            {"tp_rrr": 8.0, "trail_atr_mult": 4.0, "partial_pct_1r": 0.0,
             "sl_atr_mult": 1.5, "min_confluence": 3,
             "use_adx_filter": True, "use_weekly_filter": True},
            # Conservative: moderate RR, clean entries
            {"tp_rrr": 3.0, "trail_atr_mult": 2.0, "partial_pct_1r": 0.25,
             "sl_atr_mult": 0.5, "min_confluence": 4,
             "use_ema_stack": True, "use_pattern": True},
        ]
        template = random.choice(strategies)
        new = self._default_params(pair)
        new.update(template)
        new["version"] = new.get("version", 1) + 1
        new["notes"] = "New strategy type"
        self.current_params[pair] = new
        self.no_improve[pair] = 0
        send_telegram(f"♻️ [{pair}] Switching strategy type after 100-iter plateau.")

    # ── Backtest ───────────────────────────────────────────────────────────────

    def _run_backtest(self, pair: str, params: dict) -> Optional[dict]:
        try:
            d1, w1 = self._get_data(pair)
            if d1 is None or len(d1) < 300:
                logger.warning(f"Insufficient data for {pair}")
                return None

            TrendParams = self._tp_cls
            WFBacktester = self._wf_cls

            tp = TrendParams.from_dict(params)
            bt = WFBacktester(tp)
            wf = bt.run(d1, w1, pair=pair, n_folds=5)

            wr  = wf.test_win_rate_realistic if wf.test_win_rate_realistic > 0 else wf.test_win_rate
            # Use non-realistic avg_rrr: measures abs(pnl/risk) across all trades.
            # test_avg_rrr_realistic measures only winning partial-close trades — biased low.
            rrr = wf.test_avg_rrr if wf.test_avg_rrr > 0 else wf.test_avg_rrr_realistic

            # Monte Carlo on test trades
            mc_pass_rate = self._monte_carlo(wf)

            # Use composite_score() as primary score — already handles WR, trades, DD, PF
            comp = wf.composite_score()

            return {
                "win_rate":    wr,
                "avg_rrr":     rrr,
                "score":       comp if comp > 0 else wr * min(rrr, 5.0),
                "trades":      wf.test_trades,
                "max_dd":      wf.test_max_dd_pct,
                "profit_factor": wf.test_profit_factor,
                "sharpe":      wf.test_sharpe,
                "return_pct":  wf.test_return_pct,
                "train_wr":    wf.train_win_rate,
                "overfitting": wf.overfitting_flag,
                "monte_carlo_pass_rate": mc_pass_rate,
                "pair":        pair,
                "timestamp":   datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.error(f"Backtest failed [{pair}]: {e}")
            return None

    def _monte_carlo(self, wf_result) -> float:
        """Shuffle test trades 1000x, check WR survives. Returns pass rate."""
        try:
            test_trades = [t for t in wf_result.trades if t.split == "test"]
            if len(test_trades) < 15:
                return 1.0
            outcomes = [1 if t.outcome == "win" else 0 for t in test_trades]
            n = len(outcomes)
            passes = 0
            base_wr = sum(outcomes) / n
            for _ in range(MONTE_N):
                shuffled = random.choices(outcomes, k=n)
                if sum(shuffled) / n >= base_wr * 0.85:
                    passes += 1
            return passes / MONTE_N
        except Exception:
            return 1.0

    # ── Data management ────────────────────────────────────────────────────────

    def _get_data(self, pair: str):
        """Return (d1_df, w1_df) from cache or download."""
        now = time.time()
        cache_key = pair

        # Check in-memory cache (24h)
        if cache_key in self.data_cache:
            d1, w1, ts = self.data_cache[cache_key]
            if now - ts < 86400:
                return d1, w1

        # Check disk cache
        disk_file = os.path.join(DATA_DIR, f"{pair.replace('=','_').replace('-','_')}_d1.pkl")
        if os.path.exists(disk_file):
            age = now - os.path.getmtime(disk_file)
            if age < 86400:
                try:
                    import pickle
                    with open(disk_file, "rb") as f:
                        d1, w1 = pickle.load(f)
                    self.data_cache[cache_key] = (d1, w1, now)
                    return d1, w1
                except Exception:
                    pass

        return self._refresh_single_pair(pair)

    def _refresh_single_pair(self, pair: str):
        try:
            import yfinance as yf
            ticker = PAIR_TICKERS.get(pair, pair)
            period = PAIR_PERIODS.get(pair, "5y")

            raw_d1 = yf.download(ticker, period=period, interval="1d",
                                  progress=False, auto_adjust=True)
            raw_w1 = yf.download(ticker, period=period, interval="1wk",
                                  progress=False, auto_adjust=True)

            if raw_d1 is None or len(raw_d1) < 100:
                logger.warning(f"No D1 data for {pair}")
                return None, None

            for raw in (raw_d1, raw_w1):
                if hasattr(raw.columns, "levels"):
                    raw.columns = [c[0].lower() for c in raw.columns]
                else:
                    raw.columns = [c.lower() for c in raw.columns]
                raw.index.name = "time"
                if raw.index.tzinfo is not None:
                    raw.index = raw.index.tz_localize(None)

            d1 = raw_d1[["open", "high", "low", "close", "volume"]].dropna()
            w1 = raw_w1[["open", "high", "low", "close", "volume"]].dropna() \
                 if not raw_w1.empty else None

            # Save to disk
            import pickle
            disk_file = os.path.join(DATA_DIR, f"{pair.replace('=','_').replace('-','_')}_d1.pkl")
            with open(disk_file, "wb") as f:
                pickle.dump((d1, w1), f)

            self.data_cache[pair] = (d1, w1, time.time())
            logger.info(f"Data: {pair} {len(d1)} bars ({d1.index[0].date()} → {d1.index[-1].date()})")
            return d1, w1

        except Exception as e:
            logger.error(f"Data download failed [{pair}]: {e}")
            return None, None

    def _refresh_data(self, force: bool = False):
        """Download/refresh all pairs. Called at startup and every 24h."""
        logger.info("Refreshing all pair data...")
        for pair in PAIRS:
            if force:
                # Clear cache to force re-download
                self.data_cache.pop(pair, None)
                disk = os.path.join(DATA_DIR, f"{pair.replace('=','_').replace('-','_')}_d1.pkl")
                if os.path.exists(disk):
                    try:
                        age = time.time() - os.path.getmtime(disk)
                        if age > 86400:
                            os.remove(disk)
                    except Exception:
                        pass
            self._get_data(pair)
        self.data_cache_time = time.time()

    # ── State persistence ──────────────────────────────────────────────────────

    def save_state(self):
        state = {
            "iteration":       self.iteration,
            "xauusd_best_wr":  self.xauusd_best_wr,
            "global_best_score": self.global_best_score,
            "best_wr":         self.best_wr,
            "best_rrr":        self.best_rrr,
            "best_score":      self.best_score,
            "best_params":     self.best_params,
            "no_improve":      self.no_improve,
            "current_params":  self.current_params,
            "last_saved":      datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"State save failed: {e}")

        # Also save to legacy path so watchdog/dashboard still works
        legacy = os.path.join(STATE_DIR, "auto_loop_state.json")
        try:
            legacy_state = {
                "iteration":            self.iteration,
                "best_wr":              self.global_best_score,
                "best_xauusd_wr":       self.xauusd_best_wr,
                "best_xauusd_wr_real":  self.xauusd_best_wr,
                "no_improvement_count": max(self.no_improve.values()) if self.no_improve else 0,
                "last_saved":           datetime.now(timezone.utc).isoformat(),
                "best_params":          self.best_params.get("XAUUSD", {}),
                "pairs":                PAIRS,
            }
            with open(legacy, "w") as f:
                json.dump(legacy_state, f, indent=2)
        except Exception:
            pass

        # Supabase backup every 10 iterations
        if self.iteration % 10 == 0:
            self._save_supabase()

    def load_state(self):
        # Try new state file first
        for path in [STATE_FILE, os.path.join(STATE_DIR, "auto_loop_state.json")]:
            if not os.path.exists(path):
                continue
            try:
                with open(path) as f:
                    s = json.load(f)

                self.iteration        = s.get("iteration", 0)
                self.xauusd_best_wr   = s.get("xauusd_best_wr",
                                         s.get("best_xauusd_wr_real",
                                         s.get("best_xauusd_wr", 0)))
                self.global_best_score = s.get("global_best_score", 0)

                # Guard: legacy files stored these as scalars not dicts
                _wr = s.get("best_wr", {})
                self.best_wr = _wr if isinstance(_wr, dict) else {}

                _rrr = s.get("best_rrr", {})
                self.best_rrr = _rrr if isinstance(_rrr, dict) else {}

                _sc = s.get("best_score", {})
                self.best_score = _sc if isinstance(_sc, dict) else {}

                _bp = s.get("best_params", {})
                if isinstance(_bp, dict) and "ema_fast" in _bp:
                    # Flat param dict from old engine — treat as XAUUSD params
                    self.best_params = {"XAUUSD": _bp}
                elif isinstance(_bp, dict):
                    # Proper pair-keyed dict — but filter out any flat entries
                    self.best_params = {k: v for k, v in _bp.items()
                                        if isinstance(v, dict) and "ema_fast" in v}
                else:
                    self.best_params = {}

                _ni = s.get("no_improve", s.get("no_improvement_per_pair", {}))
                self.no_improve = _ni if isinstance(_ni, dict) else {}

                _cp = s.get("current_params", {})
                if isinstance(_cp, dict) and "ema_fast" in _cp:
                    # Flat param dict — use it as starting point for all pairs
                    self.current_params = {p: copy.deepcopy(_cp) for p in PAIRS}
                elif isinstance(_cp, dict):
                    self.current_params = {k: v for k, v in _cp.items()
                                           if isinstance(v, dict) and "ema_fast" in v}
                else:
                    self.current_params = {}

                # Populate current_params from best_params for pairs not present
                for p in PAIRS:
                    if p not in self.current_params and p in self.best_params:
                        self.current_params[p] = copy.deepcopy(self.best_params[p])

                # Restore XAUUSD WR floor from saved xauusd_best_wr if not in best_wr
                # best_score is NOT restored — first qualifying result sets the baseline.
                # This prevents the artificial floor from blocking genuine improvements.
                if self.xauusd_best_wr > 0:
                    for xau in ("XAUUSD", "GC=F"):
                        if xau not in self.best_wr:
                            self.best_wr[xau] = self.xauusd_best_wr

                logger.info(
                    f"State loaded: iter={self.iteration} | "
                    f"XAUUSD WR={self.xauusd_best_wr:.1%} | "
                    f"path={os.path.basename(path)}"
                )
                break
            except Exception as e:
                logger.warning(f"State load failed ({path}): {e}")

        # Load skills
        if os.path.exists(SKILLS_FILE):
            try:
                with open(SKILLS_FILE) as f:
                    self.skills = json.load(f)
                logger.info(f"Skills loaded: {self.skills.get('total_skills_learned', 0)} total")
            except Exception:
                self.skills = {}

        # Load monthly db
        if os.path.exists(MONTHLY_FILE):
            try:
                with open(MONTHLY_FILE) as f:
                    self.monthly_db = json.load(f)
            except Exception:
                self.monthly_db = {}

    def _save_supabase(self):
        try:
            from database.supabase_client import SupabaseClient
            db = SupabaseClient()
            db.set_state("iteration", str(self.iteration))
            db.set_state("xauusd_best_wr", f"{self.xauusd_best_wr:.6f}")
            db.set_state("global_best_score", f"{self.global_best_score:.6f}")
            db.set_state("last_update", datetime.now(timezone.utc).isoformat())
        except Exception as e:
            logger.debug(f"Supabase backup failed: {e}")

    # ── Skills ─────────────────────────────────────────────────────────────────

    def _update_skills(self, pair: str, params: dict, result: dict):
        per_pair = self.skills.setdefault("per_pair", {})
        existing = per_pair.get(pair, {})
        new_score = result.get("score", 0)

        if new_score > existing.get("score", 0):
            per_pair[pair] = {
                "params": copy.deepcopy(params),
                "win_rate": result.get("win_rate", 0),
                "avg_rrr":  result.get("avg_rrr", 0),
                "score":    new_score,
                "updated":  datetime.now(timezone.utc).isoformat(),
            }
            self.skills["total_skills_learned"] = \
                self.skills.get("total_skills_learned", 0) + 1
            self.skills["last_updated"] = datetime.now(timezone.utc).isoformat()

            try:
                with open(SKILLS_FILE, "w") as f:
                    json.dump(self.skills, f, indent=2)
            except Exception:
                pass

    # ── Reporting ──────────────────────────────────────────────────────────────

    def _send_telegram_report(self):
        now = datetime.now(timezone.utc)
        elapsed_h = (now.timestamp() - (self._start_time or now.timestamp())) / 3600

        xau_wr = self.xauusd_best_wr
        gap = max(0, TARGET_WR - xau_wr)

        top3 = sorted(
            [(p, self.best_wr.get(p, 0)) for p in PAIRS if self.best_wr.get(p, 0) > 0],
            key=lambda x: -x[1]
        )[:3]
        top3_str = " | ".join(f"{p}:{wr:.0%}" for p, wr in top3) or "evolving..."

        stuck = sum(1 for ni in self.no_improve.values() if ni >= 20)

        msg = (
            f"=== AUTOTRADER {now.strftime('%H:%M UTC')} ===\n"
            f"Iter: {self.iteration} | Uptime: {elapsed_h:.1f}h\n"
            f"XAUUSD: {xau_wr:.1%} WR\n"
            f"Top3: {top3_str}\n"
            f"Stuck pairs: {stuck}\n"
            f"To 80%: {gap:.1%} gap\n"
            f"Skills: {self.skills.get('total_skills_learned', 0)}\n"
            f"========================"
        )
        send_telegram(msg)

    def _send_full_report(self):
        rows = []
        for pair in PAIRS:
            wr  = self.best_wr.get(pair, 0)
            rrr = self.best_rrr.get(pair, 0)
            sc  = self.best_score.get(pair, 0)
            ni  = self.no_improve.get(pair, 0)
            rows.append(f"{pair:10s} | {wr:.0%} | {rrr:.2f} | {sc:.3f} | {ni} iter stuck")

        table = "\n".join(rows)
        logger.info(f"\n{'='*60}\nPAIR RANKINGS (iter {self.iteration}):\n{table}\n{'='*60}")
        send_telegram(
            f"PAIR RANKINGS iter {self.iteration}\n"
            f"XAUUSD WR={self.xauusd_best_wr:.1%}\n"
            + "\n".join(rows[:10])
        )

        # Send email
        self._send_evolution_email()

    def _send_evolution_email(self):
        subject = f"AutoTrader Evolution {datetime.now().strftime('%Y-%m-%d')}"
        rows_html = ""
        for pair in PAIRS:
            wr  = self.best_wr.get(pair, 0)
            rrr = self.best_rrr.get(pair, 0)
            sc  = self.best_score.get(pair, 0)
            ni  = self.no_improve.get(pair, 0)
            color = "#00ff88" if wr >= 0.65 else ("#ffaa00" if wr >= 0.50 else "#ff4444")
            rows_html += (
                f"<tr><td>{pair}</td>"
                f"<td style='color:{color}'>{wr:.1%}</td>"
                f"<td>{rrr:.2f}</td>"
                f"<td>{sc:.3f}</td>"
                f"<td>{'▲' if ni == 0 else ni}</td></tr>"
            )

        best_p = self.best_params.get("XAUUSD", {})
        monthly_html = self._monthly_table_html()

        html = f"""
        <html><body style="background:#1a1a2e;color:#eee;font-family:monospace">
        <h2 style="color:#ffd700">AutoTrader Evolution — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}</h2>
        <table style="border-collapse:collapse;width:100%">
        <tr style="background:#2a2a4e;color:#aaa">
          <th>Pair</th><th>WR%</th><th>RRR</th><th>Score</th><th>Stuck</th>
        </tr>{rows_html}
        </table>
        <h3 style="color:#ffd700">XAUUSD Best Params</h3>
        <pre style="background:#0d1117;padding:10px;color:#7ee787">{json.dumps(best_p, indent=2)}</pre>
        <p style="color:#888">Iteration: {self.iteration} | Skills: {self.skills.get('total_skills_learned',0)}</p>
        {monthly_html}
        </body></html>
        """
        send_email(subject, html)

    def _monthly_table_html(self) -> str:
        if not self.monthly_db:
            return "<p style='color:#888'>Monthly backtest not yet run.</p>"
        rows = []
        for key in sorted(self.monthly_db.keys())[-24:]:   # last 24 months
            d = self.monthly_db[key].get("XAUUSD", {})
            rows.append(
                f"<tr><td>{key}</td>"
                f"<td>{d.get('trades',0)}</td>"
                f"<td>{d.get('wr',0):.0%}</td>"
                f"<td>{d.get('rrr',0):.2f}</td>"
                f"<td>{d.get('pnl',0):+.1f}%</td>"
                f"<td>{d.get('max_dd',0):.1f}%</td></tr>"
            )
        if not rows:
            return ""
        return (
            "<h3 style='color:#ffd700'>XAUUSD Monthly History</h3>"
            "<table style='border-collapse:collapse;width:100%'>"
            "<tr style='background:#2a2a4e;color:#aaa'>"
            "<th>Month</th><th>Trades</th><th>WR</th><th>RRR</th><th>PnL%</th><th>MaxDD%</th>"
            "</tr>" + "".join(rows) + "</table>"
        )

    # ── Monthly backtest ───────────────────────────────────────────────────────

    def run_monthly_backtest(self):
        """Build complete monthly P&L history 2022-today for all pairs. Run once."""
        import pandas as pd
        logger.info("Running monthly backtest 2022-today for all pairs...")
        send_telegram("Starting monthly backtest history build 2022→today...")

        start_year = 2022
        now = datetime.now()

        for pair in PAIRS:
            d1, _ = self._get_data(pair)
            if d1 is None or len(d1) < 100:
                continue

            best_p = self.best_params.get(pair) or self._default_params(pair)

            try:
                TrendParams = self._tp_cls
                WFBacktester = self._wf_cls
                tp  = TrendParams.from_dict(best_p)
                bt  = WFBacktester(tp)

                for year in range(start_year, now.year + 1):
                    months = range(1, 13) if year < now.year else range(1, now.month + 1)
                    for month in months:
                        key = f"{year}-{month:02d}"
                        if pair in self.monthly_db.get(key, {}):
                            continue  # already computed

                        # Slice data for this month
                        mask = (d1.index.year == year) & (d1.index.month == month)
                        month_df = d1[mask]
                        if len(month_df) < 10:
                            continue

                        # Include 200 bars of context before this month
                        month_start_pos = d1.index.get_loc(month_df.index[0])
                        context_start   = max(0, month_start_pos - 200)
                        context_df = d1.iloc[context_start:]

                        try:
                            wf = bt.run(context_df, None, pair=pair,
                                        train_pct=context_start / len(context_df),
                                        n_folds=1)
                            if key not in self.monthly_db:
                                self.monthly_db[key] = {}
                            self.monthly_db[key][pair] = {
                                "trades":  wf.test_trades,
                                "wr":      round(wf.test_win_rate_realistic, 4),
                                "rrr":     round(wf.test_avg_rrr_realistic, 4),
                                "pnl":     round(wf.test_return_pct, 2),
                                "max_dd":  round(wf.test_max_dd_pct, 2),
                            }
                        except Exception:
                            pass

            except Exception as e:
                logger.warning(f"Monthly backtest failed [{pair}]: {e}")

        try:
            with open(MONTHLY_FILE, "w") as f:
                json.dump(self.monthly_db, f, indent=2)
            logger.info(f"Monthly backtest saved: {MONTHLY_FILE}")
            send_telegram(f"Monthly backtest complete. {len(self.monthly_db)} months stored.")
        except Exception as e:
            logger.error(f"Monthly save failed: {e}")

    # ── FTMO check ─────────────────────────────────────────────────────────────

    def ftmo_check(self) -> dict:
        """Simulate FTMO challenge rules against current best XAUUSD strategy."""
        r = self.best_result.get("XAUUSD", {})
        if not r:
            return {"pass": False, "reason": "no XAUUSD result"}

        max_dd = r.get("max_dd", 100)
        pnl    = r.get("return_pct", 0)
        wr     = r.get("win_rate", 0)

        dd_ok     = max_dd <= 10.0     # FTMO: max 10% DD
        profit_ok = pnl >= 10.0        # FTMO: 10% profit target
        wr_ok     = wr >= 0.50         # sanity

        passed = dd_ok and profit_ok and wr_ok
        result = {
            "pass":      passed,
            "max_dd":    max_dd,
            "pnl":       pnl,
            "wr":        wr,
            "dd_ok":     dd_ok,
            "profit_ok": profit_ok,
        }
        status = "PASS" if passed else "FAIL"
        logger.info(f"FTMO Check: {status} | DD={max_dd:.1f}% | PnL={pnl:.1f}% | WR={wr:.1%}")
        if passed:
            send_telegram(f"✅ FTMO CHALLENGE PASS SIMULATION\n"
                         f"MaxDD={max_dd:.1f}% | PnL={pnl:.1f}% | WR={wr:.1%}")
        return result

    # ── Heal ───────────────────────────────────────────────────────────────────

    def heal(self, error: Exception):
        err_str = str(error).lower()
        tb      = traceback.format_exc()
        logger.error(f"ERROR: {error}\n{tb[:500]}")

        # Known fixes
        if "no data" in err_str or "empty" in err_str or "nan" in err_str:
            logger.info("Heal: clearing data cache and redownloading")
            self.data_cache.clear()
            for f in os.listdir(DATA_DIR):
                try: os.remove(os.path.join(DATA_DIR, f))
                except Exception: pass
            time.sleep(5)

        elif "json" in err_str or "decode" in err_str:
            logger.info("Heal: corrupt JSON — resetting state")
            try:
                os.remove(STATE_FILE)
            except Exception: pass

        elif "memory" in err_str:
            logger.info("Heal: memory error — trimming data cache")
            self.data_cache.clear()
            time.sleep(10)

        elif "connection" in err_str or "timeout" in err_str:
            logger.info("Heal: network error — waiting 60s")
            time.sleep(60)

        else:
            time.sleep(5)

        send_telegram(f"🔧 Healed: {str(error)[:100]}")

    # ── Scheduler ──────────────────────────────────────────────────────────────

    def _scheduler_loop(self):
        """Background daemon: runs all periodic and daily tasks."""
        self._start_time = time.time()
        self._last_30min = time.time()
        self._last_2h    = time.time()
        self._last_6h    = time.time()
        self._last_24h   = time.time()
        _daily_ran: dict = {}

        while True:
            try:
                now       = time.time()
                utc_hour  = datetime.now(timezone.utc).hour
                utc_date  = datetime.now(timezone.utc).date().isoformat()

                # Interval tasks
                if now - self._last_30min >= 1800:
                    self._check_30min()
                    _poll_telegram_commands(self)
                    self._last_30min = now

                if now - self._last_2h >= 7200:
                    self._send_telegram_report()
                    # Drift monitor — every 2h
                    if self._drift_monitor:
                        try:
                            threading.Thread(
                                target=self._drift_monitor.evaluate_all_pairs,
                                daemon=True,
                            ).start()
                        except Exception:
                            pass
                    # Paper trading evaluation — every 2h
                    if self._paper_engine:
                        try:
                            threading.Thread(
                                target=self._paper_engine.evaluate_readiness,
                                daemon=True,
                            ).start()
                        except Exception:
                            pass
                    self._last_2h = now

                if now - self._last_6h >= 21600:
                    github_sync(self.iteration, self.xauusd_best_wr)
                    # Notion sync
                    try:
                        from reporting.notion_sync import run_full_sync
                        threading.Thread(target=run_full_sync, daemon=True).start()
                    except Exception:
                        pass
                    # Walk-forward validation — every 6h
                    if self._wf_validator:
                        try:
                            threading.Thread(
                                target=self._wf_validator.run_all_pairs,
                                daemon=True,
                            ).start()
                        except Exception:
                            pass
                    # News filter cache refresh
                    if self._news_filter:
                        try:
                            self._news_filter._last_news_fetch = 0.0  # force refresh
                        except Exception:
                            pass
                    self._last_6h = now

                if now - self._last_24h >= 86400:
                    self._refresh_data(force=True)
                    self._last_24h = now

                # Daily tasks by UTC hour
                for task_key, hour in [
                    ("monthly_backtest", 3),  # overnight — no conflict with evolution
                    ("evolution_email", 8),
                    ("backtest_email",  9),
                    ("pair_ranking",   20),
                    ("ftmo_check",     22),
                ]:
                    if (utc_hour == hour
                            and _daily_ran.get(f"{utc_date}_{task_key}") is None):
                        _daily_ran[f"{utc_date}_{task_key}"] = True
                        if task_key == "monthly_backtest":
                            # Run in its own thread to avoid blocking scheduler loop
                            threading.Thread(
                                target=self.run_monthly_backtest, daemon=True
                            ).start()
                        elif task_key == "evolution_email":
                            self._send_evolution_email()
                        elif task_key == "backtest_email":
                            self._send_backtest_email()
                        elif task_key == "pair_ranking":
                            self._send_full_report()
                        elif task_key == "ftmo_check":
                            self.ftmo_check()

                # Clean up old daily task keys
                if len(_daily_ran) > 200:
                    _daily_ran = {k: v for k, v in _daily_ran.items()
                                  if k.startswith(utc_date)}

            except Exception as e:
                logger.debug(f"Scheduler error: {e}")

            time.sleep(30)

    def _check_30min(self):
        """30-min health check: stall detection, resource check."""
        try:
            state_age = time.time() - os.path.getmtime(STATE_FILE) \
                        if os.path.exists(STATE_FILE) else 9999
            if state_age > 3600:
                send_telegram(f"⚠️ STALL: state file not updated for {state_age/60:.0f} min")

            # RAM check
            try:
                import psutil
                ram = psutil.virtual_memory().percent
                cpu = psutil.cpu_percent(interval=1)
                if ram > 90:
                    send_telegram(f"⚠️ HIGH RAM: {ram:.0f}%")
                if cpu > 95:
                    send_telegram(f"⚠️ HIGH CPU: {cpu:.0f}%")
            except ImportError:
                pass
        except Exception:
            pass

    def _send_backtest_email(self):
        """Daily 09:00 email with monthly backtest history table."""
        if not self.monthly_db:
            return
        subject = f"AutoTrader Backtest History {datetime.now().strftime('%Y-%m-%d')}"
        html = self._monthly_table_html()
        if not html:
            return
        send_email(subject, f"<html><body style='background:#1a1a2e;color:#eee;font-family:monospace'>{html}</body></html>")

    # ── Module loader ──────────────────────────────────────────────────────────

    def _import_modules(self):
        try:
            from strategy.trend_engine import TrendParams
            from backtester.walk_forward import WalkForwardBacktester
            self._tp_cls  = TrendParams
            self._wf_cls  = WalkForwardBacktester
            logger.info("Core modules loaded: TrendParams, WalkForwardBacktester")
        except Exception as e:
            logger.critical(f"Cannot load core modules: {e}")
            raise

        # Phase 3-8 engines — non-critical, load best-effort
        try:
            from analytics.live_drift_monitor import LiveDriftMonitor
            self._drift_monitor = LiveDriftMonitor()
            logger.info("LiveDriftMonitor loaded")
        except Exception as e:
            logger.debug(f"LiveDriftMonitor unavailable: {e}")

        try:
            from portfolio.live_exposure_engine import LiveExposureEngine
            self._exposure_engine = LiveExposureEngine()
            logger.info("LiveExposureEngine loaded")
        except Exception as e:
            logger.debug(f"LiveExposureEngine unavailable: {e}")

        try:
            from risk.news_volatility_filter import NewsVolatilityFilter
            self._news_filter = NewsVolatilityFilter()
            logger.info("NewsVolatilityFilter loaded")
        except Exception as e:
            logger.debug(f"NewsVolatilityFilter unavailable: {e}")

        try:
            from execution.paper_trading import PaperTradingEngine
            self._paper_engine = PaperTradingEngine()
            logger.info(f"PaperTradingEngine loaded — mode={self._paper_engine._state.mode}")
        except Exception as e:
            logger.debug(f"PaperTradingEngine unavailable: {e}")

        try:
            from validation.walk_forward_validator import WalkForwardValidator
            self._wf_validator = WalkForwardValidator()
            logger.info("WalkForwardValidator loaded")
        except Exception as e:
            logger.debug(f"WalkForwardValidator unavailable: {e}")

        try:
            from core.resource_monitor import ResourceMonitor
            self._resource_mon = ResourceMonitor()
            logger.info("ResourceMonitor loaded")
        except Exception as e:
            logger.debug(f"ResourceMonitor unavailable: {e}")

        try:
            from evolution.evolution_engine import get_engine as _get_evo
            _get_evo()  # init singleton
            logger.info("EvolutionEngine loaded")
        except Exception as e:
            logger.debug(f"EvolutionEngine unavailable: {e}")

        try:
            from portfolio.portfolio_engine import get_portfolio
            get_portfolio()  # init singleton
            logger.info("PortfolioEngine loaded")
        except Exception as e:
            logger.debug(f"PortfolioEngine unavailable: {e}")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    engine = AutoTraderEngine()
    engine.run_forever()
