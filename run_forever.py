"""
AutoTrader OMEGA — Clean Rebuild v3.0
Self-contained. Simple. Stable. Runs forever.

Target: 60-75% WR | 2.0-5.0 RRR | Expectancy positive and growing
MT5 Demo paper trading | 4H bars | 12 pairs
"""

import os, sys, json, time, gc, signal, logging, subprocess, smtplib, ssl
import random, copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import urllib.request

import numpy as np
import pandas as pd

# ── Never die on SIGTERM ───────────────────────────────────────────────────────
def _handle_signal(sig, frame):
    logging.warning(f"Signal {sig} received — staying alive")

signal.signal(signal.SIGTERM, _handle_signal)
try:
    signal.signal(signal.SIGHUP, _handle_signal)
except AttributeError:
    pass  # Windows

# ── Logging ────────────────────────────────────────────────────────────────────
ROOT    = Path(__file__).parent
LOGDIR  = ROOT / "logs"
LOGDIR.mkdir(exist_ok=True)
LOG_FILE = LOGDIR / f"engine_{datetime.now().strftime('%Y-%m-%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ── Load .env ──────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

MT5_LOGIN    = int(os.environ.get("MT5_LOGIN", 0) or 0)
MT5_PASSWORD = os.environ.get("MT5_PASSWORD", "")
MT5_SERVER   = os.environ.get("MT5_SERVER", "")
TG_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT      = os.environ.get("TELEGRAM_CHAT_ID", "")
GH_TOKEN     = os.environ.get("GITHUB_TOKEN", "")
GH_USER      = os.environ.get("GITHUB_USER", "Upwosti")
GH_REPO      = os.environ.get("GITHUB_REPO", "autotrader-claude")
EMAIL_FROM   = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASS   = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_TO     = os.environ.get("EMAIL_RECEIVER", "")

# ── Pairs & mappings ───────────────────────────────────────────────────────────
PAIRS = [
    "XAUUSD", "GBPUSD", "EURUSD", "USDJPY", "GBPJPY",
    "AUDUSD", "USDCAD", "BTCUSD", "ETHUSD", "NAS100", "US30", "XAGUSD",
]

MT5_SYMBOL_MAP = {
    "XAUUSD": "XAUUSD", "GBPUSD": "GBPUSD", "EURUSD": "EURUSD",
    "USDJPY": "USDJPY", "GBPJPY": "GBPJPY", "AUDUSD": "AUDUSD",
    "USDCAD": "USDCAD", "BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD",
    "NAS100": "NAS100", "US30": "US30",     "XAGUSD": "XAGUSD",
}

YF_TICKERS = {
    "XAUUSD": "GC=F",      "GBPUSD": "GBPUSD=X", "EURUSD": "EURUSD=X",
    "USDJPY": "USDJPY=X",  "GBPJPY": "GBPJPY=X", "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",  "BTCUSD": "BTC-USD",  "ETHUSD": "ETH-USD",
    "NAS100": "^IXIC",     "US30":   "^DJI",      "XAGUSD": "SI=F",
}

# Spread per pair (price units, realistic)
SPREAD = {
    "XAUUSD": 0.30, "XAGUSD": 0.035, "GBPUSD": 0.00008,
    "EURUSD": 0.00006, "USDJPY": 0.07, "GBPJPY": 0.15,
    "AUDUSD": 0.00009, "USDCAD": 0.00010, "BTCUSD": 15.0,
    "ETHUSD": 0.80, "NAS100": 1.0, "US30": 2.0,
}

# Lot sizes for risk calculation
LOT_SIZE = {
    "XAUUSD": 100, "XAGUSD": 5000, "GBPUSD": 100000,
    "EURUSD": 100000, "USDJPY": 100000, "GBPJPY": 100000,
    "AUDUSD": 100000, "USDCAD": 100000, "BTCUSD": 1,
    "ETHUSD": 1, "NAS100": 1, "US30": 1,
}

# ── Parameter space ────────────────────────────────────────────────────────────
PARAM_RANGES = {
    "ema_fast":       [8, 13, 21, 34],
    "ema_slow":       [34, 50, 89, 144],
    "rsi_period":     [10, 14, 21],
    "rsi_long_max":   [55, 60, 65, 68],
    "rsi_short_min":  [32, 35, 40, 45],
    "atr_period":     [10, 14, 21],
    "sl_atr_mult":    [0.3, 0.4, 0.5, 0.6, 0.8],
    "tp_rrr":         [2.0, 2.5, 3.0, 4.0, 5.0],
    "trail_atr_mult": [1.0, 1.5, 2.0, 2.5],
    "partial1_r":     [1.5, 2.0],
    "partial2_r":     [2.5, 3.0],
    "min_adx":        [20, 25, 30],
    "use_adx":        [True, False],
    "use_ema_stack":  [True, False],
    "min_confluence": [2, 3, 4],
}

PARAM_PRIORITIES = [
    "tp_rrr", "sl_atr_mult", "trail_atr_mult",
    "partial1_r", "partial2_r", "min_confluence",
    "ema_fast", "ema_slow", "rsi_long_max",
    "min_adx", "use_adx", "use_ema_stack",
    "rsi_period", "atr_period", "rsi_short_min",
]

STATE_FILE     = ROOT / "state.json"
EMAIL_TRACKER  = ROOT / "email_tracker.json"
MAX_OPEN_TRADES = 2
RISK_PER_TRADE  = 0.01   # 1% of account

# ── MT5 connection ─────────────────────────────────────────────────────────────
_mt5           = None
_mt5_connected = False
_mt5_last_try  = 0.0


def connect_mt5() -> bool:
    global _mt5, _mt5_connected, _mt5_last_try
    _mt5_last_try = time.time()
    try:
        import MetaTrader5 as mt5
        _mt5 = mt5
        if not mt5.initialize():
            log.warning(f"MT5 initialize failed: {mt5.last_error()}")
            return False
        if MT5_LOGIN and MT5_PASSWORD and MT5_SERVER:
            ok = mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
            if not ok:
                log.warning(f"MT5 login failed: {mt5.last_error()}")
                mt5.shutdown()
                return False
        info = mt5.account_info()
        if info is None:
            log.warning("MT5 account_info is None — not connected")
            mt5.shutdown()
            return False
        log.info(f"MT5 connected | {info.name} | {info.server} | Balance: ${info.balance:,.2f}")
        _mt5_connected = True
        return True
    except ImportError:
        log.warning("MetaTrader5 library not installed — install with: pip install MetaTrader5")
        return False
    except Exception as e:
        log.warning(f"MT5 connect error: {e}")
        return False


def ensure_mt5() -> bool:
    """Return True if MT5 connected. Retry every 60s."""
    global _mt5_connected
    if _mt5_connected and _mt5 is not None:
        try:
            info = _mt5.account_info()
            if info is not None:
                return True
        except Exception:
            pass
        _mt5_connected = False
    if time.time() - _mt5_last_try > 60:
        _mt5_connected = connect_mt5()
    return _mt5_connected


# ── Data cache ─────────────────────────────────────────────────────────────────
_data_cache: Dict[str, tuple] = {}
CACHE_TTL = 6 * 3600  # 6 hours


def get_data(pair: str) -> Optional[pd.DataFrame]:
    """4H OHLCV. MT5 primary → yfinance fallback. Cache 6h."""
    now = time.time()
    if pair in _data_cache:
        df_c, ts = _data_cache[pair]
        if now - ts < CACHE_TTL:
            return df_c

    df = None

    # ── MT5 path ───────────────────────────────────────────────────────────────
    if ensure_mt5() and _mt5 is not None:
        try:
            symbol = MT5_SYMBOL_MAP.get(pair, pair)
            rates  = _mt5.copy_rates_from_pos(symbol, _mt5.TIMEFRAME_H4, 0, 2500)
            if rates is not None and len(rates) > 200:
                df = pd.DataFrame(rates)
                df["time"] = pd.to_datetime(df["time"], unit="s")
                df.set_index("time", inplace=True)
                df = df[["open", "high", "low", "close", "tick_volume"]].rename(
                    columns={"tick_volume": "volume"})
                log.debug(f"MT5 data {pair}: {len(df)} 4H bars")
        except Exception as e:
            log.debug(f"MT5 data error {pair}: {e}")
            df = None

    # ── yfinance fallback ──────────────────────────────────────────────────────
    if df is None or len(df) < 200:
        try:
            import yfinance as yf
            ticker = YF_TICKERS.get(pair, pair)
            raw = yf.download(ticker, period="2y", interval="1h",
                              progress=False, auto_adjust=True)
            if raw is not None and len(raw) > 200:
                raw.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                               for c in raw.columns]
                df = raw.resample("4h").agg({
                    "open": "first", "high": "max",
                    "low": "min", "close": "last", "volume": "sum"
                }).dropna()
                if len(df) > 200:
                    log.debug(f"yfinance data {pair}: {len(df)} 4H bars")
        except Exception as e:
            log.debug(f"yfinance error {pair}: {e}")

    if df is not None and len(df) > 200:
        _data_cache[pair] = (df.copy(), now)
        return df

    return None


# ── Technical indicators ───────────────────────────────────────────────────────
def add_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = df.copy()
    ef = int(params.get("ema_fast", 21))
    es = int(params.get("ema_slow", 89))
    rp = int(params.get("rsi_period", 14))
    ap = int(params.get("atr_period", 14))

    df["ema_fast"] = df["close"].ewm(span=ef, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=es, adjust=False).mean()
    df["ema_200"]  = df["close"].ewm(span=200, adjust=False).mean()

    # RSI
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).ewm(span=rp, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=rp, adjust=False).mean()
    rs    = gain / loss.replace(0, 1e-9)
    df["rsi"] = 100 - 100 / (1 + rs)

    # ATR
    hl  = df["high"] - df["low"]
    hc  = (df["high"] - df["close"].shift()).abs()
    lc  = (df["low"]  - df["close"].shift()).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=ap, adjust=False).mean()

    # ADX
    pdm = df["high"].diff().clip(lower=0)
    ndm = (-df["low"].diff()).clip(lower=0)
    pdm = pdm.where(pdm > ndm, 0.0)
    ndm = ndm.where(ndm > pdm, 0.0)
    atr_s  = tr.ewm(span=ap, adjust=False).mean()
    pdi    = 100 * pdm.ewm(span=ap, adjust=False).mean() / atr_s.replace(0, 1e-9)
    ndi    = 100 * ndm.ewm(span=ap, adjust=False).mean() / atr_s.replace(0, 1e-9)
    dx     = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, 1e-9)
    df["adx"] = dx.ewm(span=ap, adjust=False).mean()

    return df


def generate_signals(df: pd.DataFrame, params: dict) -> List[dict]:
    """Generate long/short entry signals from indicator crossovers."""
    df = add_indicators(df, params)

    rsi_long_max  = float(params.get("rsi_long_max",  65))
    rsi_short_min = float(params.get("rsi_short_min", 35))
    min_adx       = float(params.get("min_adx",       25))
    use_adx       = bool(params.get("use_adx",       True))
    use_ema_stack = bool(params.get("use_ema_stack", True))
    min_conf      = int(params.get("min_confluence",   3))
    atr_arr       = df["atr"].values
    close_arr     = df["close"].values
    n             = len(df)

    signals = []
    for i in range(210, n):
        ef_prev = df["ema_fast"].iloc[i - 1]
        es_prev = df["ema_slow"].iloc[i - 1]
        ef_cur  = df["ema_fast"].iloc[i]
        es_cur  = df["ema_slow"].iloc[i]
        rsi_cur = df["rsi"].iloc[i]
        adx_cur = df["adx"].iloc[i]
        atr_cur = df["atr"].iloc[i]
        cl_cur  = df["close"].iloc[i]
        em200   = df["ema_200"].iloc[i]

        if pd.isna(atr_cur) or atr_cur <= 0:
            continue

        # ATR expanding vs 20-bar avg
        atr_avg = atr_arr[max(0, i - 20):i].mean()
        atr_ok  = atr_cur > atr_avg * 0.8

        # Long signal
        cross_long  = ef_prev < es_prev and ef_cur >= es_cur
        if cross_long:
            conf = sum([
                rsi_cur < rsi_long_max,
                (adx_cur >= min_adx) if use_adx else True,
                (cl_cur > em200) if use_ema_stack else True,
                cl_cur > ef_cur,
                atr_ok,
            ])
            if conf >= min_conf:
                signals.append({"idx": i, "direction": "long",
                                "close": float(cl_cur), "atr": float(atr_cur)})

        # Short signal
        cross_short = ef_prev > es_prev and ef_cur <= es_cur
        if cross_short:
            conf = sum([
                rsi_cur > rsi_short_min,
                (adx_cur >= min_adx) if use_adx else True,
                (cl_cur < em200) if use_ema_stack else True,
                cl_cur < ef_cur,
                atr_ok,
            ])
            if conf >= min_conf:
                signals.append({"idx": i, "direction": "short",
                                "close": float(cl_cur), "atr": float(atr_cur)})

    return signals


# ── Backtester ─────────────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame, pair: str, params: dict) -> Optional[dict]:
    """
    70/30 walk-forward backtest with realistic partial exits.
    Exit structure: 25% at partial1_r, 25% at partial2_r, 50% runner with trail.
    Returns dict with wr, avg_win, avg_loss, pf, dd, avg_rrr, trades.
    """
    df_ind = add_indicators(df, params)
    signals = generate_signals(df, params)

    if len(signals) < 10:
        return None

    split   = int(len(df) * 0.70)
    t_sigs  = [s for s in signals if s["idx"] >= split]

    if len(t_sigs) < 8:
        return None

    # Overfit check via train signals
    tr_sigs = [s for s in signals if s["idx"] < split]

    spread_val = SPREAD.get(pair, 0.0001)
    sl_mult    = float(params.get("sl_atr_mult",    0.5))
    tp_rrr     = float(params.get("tp_rrr",         3.0))
    trail_m    = float(params.get("trail_atr_mult",  1.5))
    p1_r       = float(params.get("partial1_r",     1.5))
    p2_r       = float(params.get("partial2_r",     2.5))

    def simulate(sigs: list) -> List[dict]:
        trades = []
        for s in sigs:
            idx       = s["idx"]
            atr       = s["atr"]
            direction = s["direction"]
            entry_raw = s["close"]

            if direction == "long":
                entry = entry_raw + spread_val
                sl_p  = entry - sl_mult * atr
            else:
                entry = entry_raw - spread_val
                sl_p  = entry + sl_mult * atr

            risk = abs(entry - sl_p)
            if risk <= 0 or risk > entry * 0.20:
                continue

            if direction == "long":
                tp_p = entry + tp_rrr * risk
                p1_p = entry + p1_r  * risk
                p2_p = entry + p2_r  * risk
            else:
                tp_p = entry - tp_rrr * risk
                p1_p = entry - p1_r  * risk
                p2_p = entry - p2_r  * risk

            p1_done = p2_done = False
            partial_pnl = 0.0
            trail = sl_p
            pnl_r = None

            for j in range(idx + 1, min(idx + 250, len(df_ind))):
                bar   = df_ind.iloc[j]
                hi    = float(bar["high"])
                lo    = float(bar["low"])
                cl    = float(bar["close"])
                op    = float(bar.get("open", (hi + lo) / 2))
                atr_j = float(bar["atr"]) if not pd.isna(bar["atr"]) else atr

                # Partial 1 — 25% at p1_r
                if not p1_done:
                    hit1 = (direction == "long" and hi >= p1_p) or \
                           (direction == "short" and lo <= p1_p)
                    if hit1:
                        partial_pnl += 0.25 * p1_r
                        p1_done = True
                        trail = entry  # move SL to breakeven

                # Partial 2 — 25% at p2_r
                if p1_done and not p2_done:
                    hit2 = (direction == "long" and hi >= p2_p) or \
                           (direction == "short" and lo <= p2_p)
                    if hit2:
                        partial_pnl += 0.25 * p2_r
                        p2_done = True

                # Update trailing stop
                if p1_done and atr_j > 0:
                    if direction == "long":
                        trail = max(trail, cl - trail_m * atr_j)
                    else:
                        trail = min(trail, cl + trail_m * atr_j)

                cur_sl = trail if p1_done else sl_p

                sl_hit = (direction == "long"  and lo <= cur_sl) or \
                         (direction == "short" and hi >= cur_sl)
                tp_hit = (direction == "long"  and hi >= tp_p) or \
                         (direction == "short" and lo <= tp_p)

                if sl_hit or tp_hit:
                    if tp_hit and not sl_hit:
                        pnl_r = partial_pnl + 0.50 * tp_rrr
                    elif sl_hit and not tp_hit:
                        if p1_done:
                            runner_rrr = abs(cur_sl - entry) / risk if risk > 0 else 0
                            if direction == "short":
                                runner_rrr = abs(entry - cur_sl) / risk if risk > 0 else 0
                            pnl_r = partial_pnl + 0.50 * (runner_rrr if cur_sl != sl_p else -1.0)
                        else:
                            pnl_r = -1.0
                    else:
                        # Both hit same bar — use bar direction
                        bar_bull = cl >= op
                        if (direction == "long" and bar_bull) or (direction == "short" and not bar_bull):
                            pnl_r = partial_pnl + 0.50 * tp_rrr
                        else:
                            pnl_r = partial_pnl - (0.50 if p1_done else 1.0)
                    break

            if pnl_r is None:
                # Timed out — close at last bar
                last = float(df_ind.iloc[min(idx + 249, len(df_ind) - 1)]["close"])
                raw  = (last - entry) / risk if direction == "long" else (entry - last) / risk
                pnl_r = (partial_pnl + 0.50 * raw) if p1_done else raw

            trades.append({"pnl_r": round(pnl_r, 4),
                           "win":  pnl_r > 0})
        return trades

    test_trades  = simulate(t_sigs)
    train_trades = simulate(tr_sigs)

    if len(test_trades) < 8:
        return None

    def stats(trades):
        if not trades:
            return None
        wins   = [t["pnl_r"] for t in trades if t["win"]]
        losses = [t["pnl_r"] for t in trades if not t["win"]]
        n      = len(trades)
        wr     = len(wins) / n
        avg_w  = sum(wins)  / max(len(wins),   1)
        avg_l  = abs(sum(losses) / max(len(losses), 1))
        pf     = sum(wins) / max(abs(sum(losses)), 1e-9) if losses else 99.0
        # Max drawdown
        eq, peak, dd = 0.0, 0.0, 0.0
        for t in trades:
            eq += t["pnl_r"]
            if eq > peak:
                peak = eq
            dd = max(dd, (peak - eq) / max(abs(peak), 1e-9))
        avg_rrr = sum(abs(w) for w in wins) / max(len(wins), 1)
        return {"wr": wr, "avg_w": avg_w, "avg_l": avg_l,
                "pf": pf, "dd": dd, "avg_rrr": avg_rrr, "n": n}

    s   = stats(test_trades)
    tr  = stats(train_trades)
    if s is None:
        return None

    # Overfit guard: if train WR >> test WR, reject
    if tr and tr["wr"] - s["wr"] > 0.20 and tr["wr"] > 0.65:
        return None

    return {
        "wr":       round(s["wr"],     4),
        "avg_win":  round(s["avg_w"],  4),
        "avg_loss": round(s["avg_l"],  4),
        "pf":       round(s["pf"],     3),
        "dd":       round(s["dd"],     4),
        "avg_rrr":  round(s["avg_rrr"],3),
        "trades":   s["n"],
        "train_wr": round(tr["wr"], 4) if tr else s["wr"],
    }


# ── Telegram ───────────────────────────────────────────────────────────────────
def send_telegram(msg: str) -> bool:
    if not TG_TOKEN or not TG_CHAT:
        return False
    try:
        body = json.dumps({"chat_id": TG_CHAT, "text": msg[:4096]}).encode()
        req  = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=body, headers={"Content-Type": "application/json"}, method="POST")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        urllib.request.urlopen(req, timeout=10, context=ctx)
        return True
    except Exception as e:
        log.debug(f"Telegram error: {e}")
        return False


# ── Email ──────────────────────────────────────────────────────────────────────
def _email_sent_today(key: str) -> bool:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        tr = json.load(open(EMAIL_TRACKER)) if EMAIL_TRACKER.exists() else {}
        return tr.get(key) == today
    except Exception:
        return False


def _mark_email(key: str):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        tr = json.load(open(EMAIL_TRACKER)) if EMAIL_TRACKER.exists() else {}
        tr[key] = today
        json.dump(tr, open(EMAIL_TRACKER, "w"))
    except Exception:
        pass


def send_email(subject: str, body: str, key: str = "") -> bool:
    if not EMAIL_FROM or not EMAIL_PASS or not EMAIL_TO:
        return False
    k = key or subject[:40]
    if _email_sent_today(k):
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO
        msg.attach(MIMEText(body, "html"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30, context=ctx) as s:
            s.login(EMAIL_FROM, EMAIL_PASS)
            s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        _mark_email(k)
        log.info(f"Email sent: {subject}")
        return True
    except Exception as e:
        log.debug(f"Email failed: {e}")
        return False


# ── Git sync ───────────────────────────────────────────────────────────────────
def git_push(iteration: int, note: str = "") -> bool:
    if not GH_TOKEN:
        return False
    try:
        cwd = str(ROOT)
        subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True, timeout=30)
        msg = f"iter{iteration}" + (f" {note}" if note else "")
        subprocess.run(["git", "commit", "-m", msg], cwd=cwd, capture_output=True, timeout=30)
        remote = f"https://{GH_TOKEN}@github.com/{GH_USER}/{GH_REPO}.git"
        for branch in ["master", "main"]:
            r = subprocess.run(
                ["git", "push", remote, f"HEAD:{branch}"],
                cwd=cwd, capture_output=True, timeout=60)
            if r.returncode == 0:
                log.info(f"Git push: {msg}")
                return True
        log.debug("Git push failed both branches")
        return False
    except Exception as e:
        log.debug(f"Git push error: {e}")
        return False


# ── RAM check ──────────────────────────────────────────────────────────────────
def get_ram_pct() -> float:
    try:
        import psutil
        return psutil.virtual_memory().percent
    except ImportError:
        return 0.0


# ── AutoTrader ─────────────────────────────────────────────────────────────────
class AutoTrader:

    def __init__(self):
        self.iteration      : int              = 0
        self.best_wr        : Dict[str, float] = {}
        self.best_expectancy: Dict[str, float] = {}
        self.best_score     : Dict[str, float] = {}
        self.best_params    : Dict[str, dict]  = {}
        self.no_improve     : Dict[str, int]   = {}
        self.current_params : Dict[str, dict]  = {}
        self.running        : bool             = True
        self._last_trade_check = 0.0
        self._last_hour_log    = 0.0
        self._open_trades: List[dict] = []

        self.load_state()
        connect_mt5()
        log.info("=" * 55)
        log.info("AutoTrader OMEGA v3.0 — Clean Rebuild")
        log.info(f"Pairs: {len(PAIRS)} | State: iter {self.iteration}")
        log.info("=" * 55)

    # ── Main loop ──────────────────────────────────────────────────────────────
    def run_forever(self):
        send_telegram(
            f"🚀 AutoTrader OMEGA v3.0 STARTED\n"
            f"Pairs: {len(PAIRS)} | iter: {self.iteration}\n"
            f"MT5: {'Connected' if _mt5_connected else 'Offline (retry every 60s)'}\n"
            f"Target: 60-75% WR | 2.0-5.0 RRR | Max DD 8%\n"
            f"Use /status /pause /resume /stop /report"
        )
        while self.running:
            try:
                self.evolve()
                self.iteration += 1
                self.save_state()

                if self.iteration % 10 == 0:
                    git_push(self.iteration)

                if self.iteration % 50 == 0:
                    self.send_telegram_report()

                # Hourly MT5 connection log
                if time.time() - self._last_hour_log > 3600:
                    self._log_hourly()

                # Check for live paper trades every 4 hours
                if time.time() - self._last_trade_check > 4 * 3600:
                    self.check_and_trade()
                    self._last_trade_check = time.time()

                # Scheduled emails
                self.send_email_if_scheduled()

                # Resource guard
                self.check_resources()

            except KeyboardInterrupt:
                log.info("Ctrl+C — stopping gracefully")
                self.save_state()
                send_telegram("🛑 AutoTrader stopped by keyboard interrupt.")
                break
            except Exception as e:
                log.error(f"Main loop error: {e}", exc_info=True)
                time.sleep(30)
                continue  # NEVER STOP

    # ── Evolution ─────────────────────────────────────────────────────────────
    def evolve(self):
        for pair in PAIRS:
            try:
                data = get_data(pair)
                if data is None:
                    continue

                params = self.mutate(pair)
                result = run_backtest(data, pair, params)
                if result is None:
                    continue

                if self.is_better(pair, result):
                    self.accept(pair, params, result)
                    wr  = result["wr"]
                    exp = wr * result["avg_win"] - (1 - wr) * result["avg_loss"]
                    log.info(
                        f"✅ KEPT [{pair}] WR={wr:.1%} RRR={result['avg_rrr']:.2f} "
                        f"E={exp:.3f} PF={result['pf']:.2f} DD={result['dd']:.1%} "
                        f"trades={result['trades']}"
                    )
                else:
                    self.no_improve[pair] = self.no_improve.get(pair, 0) + 1
                    ni = self.no_improve[pair]
                    if ni == 50:
                        self._random_restart(pair)
                    elif ni == 100:
                        self._new_strategy(pair)

                gc.collect()

            except Exception as e:
                log.warning(f"[{pair}] evolve error: {e}")
                continue  # skip pair, never stop

    # ── Acceptance ────────────────────────────────────────────────────────────
    def is_better(self, pair: str, result: dict) -> bool:
        wr      = result.get("wr",       0)
        avg_win = result.get("avg_win",  0)
        avg_loss= result.get("avg_loss", 1)
        pf      = result.get("pf",       0)
        dd      = result.get("dd",       1)
        trades  = result.get("trades",   0)

        if trades < 8:
            return False

        # WR realistic band: 55% – 78%
        if wr < 0.55 or wr > 0.78:
            return False

        # Profit Factor
        if pf > 0 and pf < 1.3:
            return False

        # Max drawdown 8%
        if dd > 0.08:
            return False

        # Positive expectancy
        exp = wr * avg_win - (1 - wr) * avg_loss
        if exp <= 0:
            return False

        # Must beat current best expectancy
        best_e = self.best_expectancy.get(pair, -999)
        if exp <= best_e:
            return False

        # RRR floor
        rrr = result.get("avg_rrr", 0)
        if rrr < 1.5:
            return False

        return True

    def accept(self, pair: str, params: dict, result: dict):
        wr      = result["wr"]
        avg_win = result["avg_win"]
        avg_loss= result["avg_loss"]
        exp     = wr * avg_win - (1 - wr) * avg_loss

        self.best_wr[pair]         = max(self.best_wr.get(pair, 0), wr)
        self.best_expectancy[pair] = exp
        self.best_score[pair]      = exp + result["avg_rrr"] * 0.05
        self.best_params[pair]     = copy.deepcopy(params)
        self.current_params[pair]  = copy.deepcopy(params)
        self.no_improve[pair]      = 0

    # ── Mutation ──────────────────────────────────────────────────────────────
    def mutate(self, pair: str) -> dict:
        base = copy.deepcopy(
            self.current_params.get(pair) or self.best_params.get(pair) or
            self.default_params())
        # Mutate 1-3 parameters
        n_muts = random.choice([1, 1, 2, 3])
        for _ in range(n_muts):
            param   = random.choice(PARAM_PRIORITIES[:10])
            choices = PARAM_RANGES.get(param, [])
            if choices:
                base[param] = random.choice(choices)
        base["version"] = base.get("version", 1) + 1
        return base

    def default_params(self) -> dict:
        return {
            "ema_fast": 21, "ema_slow": 89, "rsi_period": 14,
            "rsi_long_max": 65, "rsi_short_min": 35, "atr_period": 14,
            "sl_atr_mult": 0.5, "tp_rrr": 3.0, "trail_atr_mult": 1.5,
            "partial1_r": 1.5, "partial2_r": 2.5,
            "min_adx": 25, "use_adx": True, "use_ema_stack": True,
            "min_confluence": 3, "version": 1,
        }

    def _random_restart(self, pair: str):
        log.info(f"[{pair}] Random restart after 50 no-improve iters")
        base = self.default_params()
        for _ in range(random.randint(4, 8)):
            p = random.choice(PARAM_PRIORITIES[:10])
            c = PARAM_RANGES.get(p, [])
            if c:
                base[p] = random.choice(c)
        self.current_params[pair] = base
        self.no_improve[pair] = 0
        send_telegram(f"🔄 [{pair}] Random restart — exploring new region.")

    def _new_strategy(self, pair: str):
        log.info(f"[{pair}] New strategy type after 100 no-improve iters")
        templates = [
            {"tp_rrr": 5.0, "sl_atr_mult": 0.4, "trail_atr_mult": 2.0,
             "partial1_r": 2.0, "partial2_r": 3.0, "min_confluence": 3},
            {"tp_rrr": 3.0, "sl_atr_mult": 0.6, "trail_atr_mult": 1.5,
             "partial1_r": 1.5, "partial2_r": 2.5, "min_confluence": 4},
            {"tp_rrr": 4.0, "sl_atr_mult": 0.5, "trail_atr_mult": 2.0,
             "use_ema_stack": False, "use_adx": True, "min_confluence": 3},
        ]
        new = self.default_params()
        new.update(random.choice(templates))
        self.current_params[pair] = new
        self.no_improve[pair] = 0
        send_telegram(f"♻️ [{pair}] New strategy type — 100-iter plateau.")

    # ── State persistence ──────────────────────────────────────────────────────
    def save_state(self):
        try:
            json.dump({
                "iteration":       self.iteration,
                "best_wr":         self.best_wr,
                "best_expectancy": self.best_expectancy,
                "best_score":      self.best_score,
                "best_params":     self.best_params,
                "no_improve":      self.no_improve,
                "current_params":  self.current_params,
                "saved":           datetime.now(timezone.utc).isoformat(),
            }, open(STATE_FILE, "w"), indent=2)
        except Exception as e:
            log.error(f"State save failed: {e}")

    def load_state(self):
        try:
            if STATE_FILE.exists():
                s = json.load(open(STATE_FILE))
                self.iteration       = s.get("iteration",       0)
                self.best_wr         = s.get("best_wr",         {})
                self.best_expectancy = s.get("best_expectancy", {})
                self.best_score      = s.get("best_score",      {})
                self.best_params     = s.get("best_params",     {})
                self.no_improve      = s.get("no_improve",      {})
                self.current_params  = s.get("current_params",  {})
                log.info(f"State loaded: iter={self.iteration} | {len(self.best_params)} pairs with params")
        except Exception as e:
            log.warning(f"State load error: {e}")

    # ── Telegram report ────────────────────────────────────────────────────────
    def send_telegram_report(self):
        try:
            ram = get_ram_pct()
            mt5_status = "Connected" if ensure_mt5() else "Offline"
            lines = [f"=== AutoTrader iter {self.iteration:,} ==="]
            top = sorted(self.best_expectancy.items(), key=lambda x: -x[1])[:6]
            for pair, exp in top:
                wr = self.best_wr.get(pair, 0)
                lines.append(f"{pair}: WR={wr:.1%} E={exp:.3f}")
            if not top:
                lines.append("Evolving... No accepted pairs yet.")
            lines.append(f"RAM: {ram:.0f}%")
            lines.append(f"MT5: {mt5_status}")
            lines.append("=" * 24)
            send_telegram("\n".join(lines))
        except Exception as e:
            log.debug(f"Telegram report error: {e}")

    # ── Scheduled email ────────────────────────────────────────────────────────
    def send_email_if_scheduled(self):
        try:
            now_utc = datetime.now(timezone.utc)
            h, m = now_utc.hour, now_utc.minute

            # 08:00 UTC — evolution report
            if h == 8 and m < 5 and not _email_sent_today("daily_evolution"):
                top = sorted(self.best_expectancy.items(), key=lambda x: -x[1])[:8]
                rows = "".join(
                    f"<tr><td>{p}</td><td>{self.best_wr.get(p,0):.1%}</td>"
                    f"<td>{e:.3f}R</td></tr>"
                    for p, e in top)
                html = (f"<h2>Evolution Report — {now_utc.strftime('%Y-%m-%d')}</h2>"
                        f"<p>Iteration: {self.iteration:,}</p>"
                        f"<table><tr><th>Pair</th><th>WR</th><th>Expectancy</th></tr>"
                        f"{rows}</table>")
                send_email("AutoTrader — Daily Evolution Report", html, "daily_evolution")

            # 09:00 UTC — MT5 trade report
            if h == 9 and m < 5 and not _email_sent_today("daily_trade"):
                trades_html = "<p>Paper trades: see Telegram for live trade updates.</p>"
                send_email("AutoTrader — Daily Trade Report", trades_html, "daily_trade")

        except Exception as e:
            log.debug(f"Email schedule error: {e}")

    # ── Hourly log ─────────────────────────────────────────────────────────────
    def _log_hourly(self):
        self._last_hour_log = time.time()
        ram = get_ram_pct()
        mt5 = ensure_mt5()
        top_pair = max(self.best_expectancy, key=self.best_expectancy.get) \
                   if self.best_expectancy else "None"
        top_exp  = self.best_expectancy.get(top_pair, 0)
        log.info(
            f"HOURLY | iter={self.iteration:,} | "
            f"best={top_pair} E={top_exp:.3f} | "
            f"RAM={ram:.0f}% | MT5={'ON' if mt5 else 'OFF'}"
        )

    # ── Resource guard ─────────────────────────────────────────────────────────
    def check_resources(self):
        try:
            ram = get_ram_pct()
            if ram > 92:
                log.warning(f"HIGH RAM {ram:.0f}% — clearing cache")
                send_telegram(f"⚠️ HIGH RAM {ram:.0f}% — clearing cache, pausing briefly")
                _data_cache.clear()
                gc.collect()
                time.sleep(30)
            elif ram > 85:
                log.warning(f"RAM {ram:.0f}% — clearing data cache")
                _data_cache.clear()
                gc.collect()
        except Exception:
            pass

    # ── MT5 paper trading ──────────────────────────────────────────────────────
    def check_and_trade(self):
        """Check current bar signals and place demo trades if conditions met."""
        if not ensure_mt5():
            return
        try:
            # Count current open trades
            positions = _mt5.positions_get()
            n_open = len(positions) if positions else 0
            if n_open >= MAX_OPEN_TRADES:
                return

            # Check each pair with a known good strategy
            for pair in PAIRS:
                if n_open >= MAX_OPEN_TRADES:
                    break
                params = self.best_params.get(pair)
                exp    = self.best_expectancy.get(pair, 0)
                if params is None or exp <= 0:
                    continue

                # Get fresh data
                data = get_data(pair)
                if data is None or len(data) < 220:
                    continue

                # Check latest completed bar for signal
                signals = generate_signals(data, params)
                if not signals:
                    continue

                last_sig = signals[-1]
                # Signal must be very recent (last 2 bars)
                if len(data) - last_sig["idx"] > 2:
                    continue

                direction = last_sig["direction"]
                atr       = last_sig["atr"]
                sl_mult   = float(params.get("sl_atr_mult", 0.5))
                tp_rrr    = float(params.get("tp_rrr", 3.0))

                # Calculate lot size: 1% risk of account
                acct = _mt5.account_info()
                if acct is None:
                    continue
                balance   = acct.balance
                risk_usd  = balance * RISK_PER_TRADE
                sl_dist   = sl_mult * atr
                lot_units = LOT_SIZE.get(pair, 100000)
                lots      = round(risk_usd / (sl_dist * lot_units), 2)
                lots      = max(0.01, min(lots, 0.50))

                placed = self.place_demo_trade(pair, direction, sl_dist, tp_rrr * sl_dist, lots)
                if placed:
                    n_open += 1
                    msg = (f"📈 PAPER TRADE [{pair}]\n"
                           f"Direction: {direction.upper()}\n"
                           f"Lots: {lots} | Risk: ${risk_usd:.0f}\n"
                           f"SL: {sl_dist:.4f} | TP: {tp_rrr * sl_dist:.4f}\n"
                           f"E={exp:.3f} | Account: ${balance:,.0f}")
                    send_telegram(msg)
                    log.info(f"Paper trade placed: {pair} {direction}")

        except Exception as e:
            log.warning(f"check_and_trade error: {e}")

    def place_demo_trade(self, pair: str, direction: str,
                         sl_dist: float, tp_dist: float, lots: float) -> bool:
        """Place order on MT5 demo account."""
        try:
            symbol = MT5_SYMBOL_MAP.get(pair, pair)
            tick   = _mt5.symbol_info_tick(symbol)
            if tick is None:
                log.warning(f"No tick data for {symbol}")
                return False

            if direction == "long":
                price = tick.ask
                sl    = round(price - sl_dist, 5)
                tp    = round(price + tp_dist, 5)
                order_type = _mt5.ORDER_TYPE_BUY
            else:
                price = tick.bid
                sl    = round(price + sl_dist, 5)
                tp    = round(price - tp_dist, 5)
                order_type = _mt5.ORDER_TYPE_SELL

            request = {
                "action":      _mt5.TRADE_ACTION_DEAL,
                "symbol":      symbol,
                "volume":      lots,
                "type":        order_type,
                "price":       price,
                "sl":          sl,
                "tp":          tp,
                "deviation":   20,
                "magic":       234000,
                "comment":     "AutoTrader OMEGA",
                "type_time":   _mt5.ORDER_TIME_GTC,
                "type_filling":_mt5.ORDER_FILLING_IOC,
            }
            result = _mt5.order_send(request)
            if result is None:
                log.warning(f"order_send returned None for {pair}")
                return False
            if result.retcode == _mt5.TRADE_RETCODE_DONE:
                log.info(f"Trade placed: {pair} {direction} {lots} lots @{price:.5f}")
                return True
            else:
                log.warning(f"Trade failed {pair}: {result.retcode} — {result.comment}")
                return False
        except Exception as e:
            log.warning(f"place_demo_trade error {pair}: {e}")
            return False


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    AutoTrader().run_forever()
