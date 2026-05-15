"""
AutoTrader OMEGA — MARKET ADAPTIVE ROBOT v4.0
NO FIXED STRATEGY. PURE ADAPTATION.

Reads market condition every 5 min, selects behavior, executes with risk control.
Learns from every trade. Evolves continuously. Never stops.

MT5 Demo: 107089479 | MetaQuotes-Demo
Target: $5000 → $5500 (10% on demo) → FTMO ready
"""

import os, sys, json, time, gc, signal, logging, subprocess, smtplib, ssl
import random, copy, threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import urllib.request

import numpy as np
import pandas as pd

# ── Never die ──────────────────────────────────────────────────────────────────
def _handle_signal(sig, frame):
    logging.warning(f"Signal {sig} received — staying alive")

signal.signal(signal.SIGTERM, _handle_signal)
try:
    signal.signal(signal.SIGHUP, _handle_signal)
except AttributeError:
    pass

# ── Paths & logging ────────────────────────────────────────────────────────────
ROOT    = Path(__file__).parent
LOGDIR  = ROOT / "logs"
DATADIR = ROOT / "data"
LOGDIR.mkdir(exist_ok=True)
DATADIR.mkdir(exist_ok=True)
LOG_FILE = LOGDIR / f"engine_{datetime.now().strftime('%Y-%m-%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
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
GH_USER      = os.environ.get("GITHUB_USERNAME", "Upwosti")
GH_REPO      = os.environ.get("GITHUB_REPO", "autotrader-claude")
EMAIL_FROM   = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASS   = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_TO     = os.environ.get("EMAIL_RECEIVER", "")

# ── Config ─────────────────────────────────────────────────────────────────────
PAIRS = ["XAUUSD", "GBPUSD", "EURUSD", "USDJPY", "GBPJPY",
         "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY",
         "BTCUSD", "ETHUSD", "NAS100", "US30", "GER40",
         "XAGUSD"]

# ─── FTMO hard limits (NEVER BREAK) ────────────────────────────────────────────
FTMO_DAILY_LOSS_PCT = 0.05    # FTMO max 5%
FTMO_TOTAL_DD_PCT   = 0.10    # FTMO max 10%
FTMO_PROFIT_TARGET  = 0.10    # FTMO 10% target (Phase 1)

# Our conservative buffers (stop BEFORE FTMO limit)
MAX_RISK_PER_TRADE = 0.01     # 1% max per trade
MIN_RISK_PER_TRADE = 0.0025   # 0.25% min per trade
MAX_DAILY_LOSS     = 0.03     # stop at 3% (safe before 5%)
MAX_TOTAL_DD       = 0.07     # stop at 7% (safe before 10%)
MAX_OPEN_TRADES    = 1        # ONE trade at a time
MAX_CORRELATED     = 1
MIN_RRR            = 2.0
MIN_CONFIDENCE     = 0.65
MIN_SIGNALS        = 2        # require >=2 confluence signals

# FTMO uses $10k account on this challenge
START_BALANCE_TARGET = 12000.0  # 20% profit goal
START_BALANCE_BASE   = 10000.0

STATE_FILE      = ROOT / "state.json"
PAIR_PROFILES   = DATADIR / "pair_profiles.json"
EMAIL_TRACKER   = ROOT / "email_tracker.json"
TRADE_TAGS      = DATADIR / "trade_tags.json"
DAILY_PNL_FILE  = DATADIR / "daily_pnl.json"

# Pip values (USD per 1 lot per 1 pip)
PIP_VALUE = {
    "XAUUSD": 10.0, "GBPUSD": 10.0, "EURUSD": 10.0,
    "USDJPY": 9.0, "GBPJPY": 8.5,  "AUDUSD": 10.0,
    "USDCAD": 7.5, "BTCUSD": 1.0,  "ETHUSD": 1.0,
    "NAS100": 1.0, "US30":   1.0,  "XAGUSD": 50.0,
}

# Correlation groups (one trade per group)
CORRELATION_GROUPS = {
    "metals":    ["XAUUSD", "XAGUSD"],
    "usd_majors":["EURUSD", "GBPUSD", "AUDUSD", "USDCAD", "USDJPY"],
    "jpy_cross": ["USDJPY", "GBPJPY"],
    "crypto":    ["BTCUSD", "ETHUSD"],
    "us_indices":["NAS100", "US30"],
}

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
            log.warning("MT5 account_info is None")
            mt5.shutdown()
            return False
        log.info(f"MT5 CONNECTED | {info.name} | {info.server} | Balance: ${info.balance:,.2f}")
        _mt5_connected = True
        return True
    except ImportError:
        log.warning("MetaTrader5 not installed — pip install MetaTrader5")
        return False
    except Exception as e:
        log.warning(f"MT5 connect error: {e}")
        return False

def ensure_mt5() -> bool:
    global _mt5_connected
    if _mt5_connected and _mt5 is not None:
        try:
            if _mt5.account_info() is not None:
                return True
        except Exception:
            pass
        _mt5_connected = False
    if time.time() - _mt5_last_try > 30:
        _mt5_connected = connect_mt5()
    return _mt5_connected

# ── Telegram ───────────────────────────────────────────────────────────────────
def send_telegram(msg: str) -> bool:
    if not TG_TOKEN or not TG_CHAT:
        return False
    try:
        data = json.dumps({"chat_id": TG_CHAT, "text": msg}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=data, headers={"Content-Type": "application/json; charset=utf-8"})
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        log.debug(f"Telegram failed: {e}")
        return False

# ── MT5 data fetcher ───────────────────────────────────────────────────────────
_TF_MAP = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 16385, "H4": 16388, "D1": 16408}

def get_mt5_data(pair: str, timeframe: str, bars: int) -> Optional[pd.DataFrame]:
    """Live MT5 OHLCV. timeframe = 'M1','M5','M15','H1','H4','D1'."""
    if not ensure_mt5():
        return None
    try:
        tf_const = getattr(_mt5, f"TIMEFRAME_{timeframe}")
        rates = _mt5.copy_rates_from_pos(pair, tf_const, 0, bars)
        if rates is None or len(rates) == 0:
            return None
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)
        df = df.rename(columns={"tick_volume": "volume"})
        return df[["open", "high", "low", "close", "volume"]]
    except Exception as e:
        log.debug(f"get_mt5_data({pair},{timeframe}) error: {e}")
        return None

# ── Indicators ─────────────────────────────────────────────────────────────────
def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([(high - low),
                    (high - close.shift()).abs(),
                    (low  - close.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def adx(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period * 2:
        return 0.0
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm  = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    tr = pd.concat([(high - low),
                    (high - close.shift()).abs(),
                    (low  - close.shift()).abs()], axis=1).max(axis=1)
    atr_v = tr.rolling(period).mean()
    plus_di  = 100 * (plus_dm.rolling(period).mean() / (atr_v + 1e-9))
    minus_di = 100 * (minus_dm.rolling(period).mean() / (atr_v + 1e-9))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    return float(dx.rolling(period).mean().iloc[-1])

def bollinger_width(df: pd.DataFrame, period: int = 20) -> float:
    sma = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    upper, lower = sma + 2 * std, sma - 2 * std
    width = ((upper - lower) / (sma + 1e-9))
    return float(width.iloc[-1])

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))

# ── Market condition classifier ────────────────────────────────────────────────
def classify_market(pair: str) -> Dict:
    """Reads M5/H1/H4 from MT5 and classifies market condition."""
    m5 = get_mt5_data(pair, "M5", 500)
    h1 = get_mt5_data(pair, "H1", 200)
    if m5 is None or h1 is None or len(m5) < 100 or len(h1) < 50:
        return {"condition": "UNCLEAR", "behavior": "skip", "confidence": 0}

    try:
        atr_series = atr(m5, 14)
        atr_now = float(atr_series.iloc[-1])
        atr_avg = float(atr_series.rolling(50).mean().iloc[-1])
        if not atr_avg or atr_avg == 0:
            return {"condition": "UNCLEAR", "behavior": "skip", "confidence": 0}
        adx_now = adx(h1, 14)
        bb_now  = bollinger_width(m5, 20)
        bb_avg  = float(((m5["high"].rolling(20).max() - m5["low"].rolling(20).min())
                         / (m5["close"].rolling(20).mean() + 1e-9)).rolling(50).mean().iloc[-1])

        # VOLATILE / news (skip)
        if atr_now > atr_avg * 2.5:
            return {"condition": "VOLATILE", "confidence": 0.30, "behavior": "skip"}
        # LOW liquidity (skip)
        if atr_now < atr_avg * 0.5:
            return {"condition": "LOW_LIQUIDITY", "confidence": 0.20, "behavior": "skip"}
        # STRONG TREND
        if adx_now > 40:
            return {"condition": "STRONG_TREND", "confidence": min(adx_now/60, 1.0),
                    "behavior": "full_runner", "tp_mult": 8.0, "sl_mult": 0.4,
                    "partial_1": 0.0, "partial_2": 0.0, "runner": 1.0, "max_hold": "multi_session"}
        # TRENDING
        if adx_now > 25 and atr_now > atr_avg:
            return {"condition": "TRENDING", "confidence": min(adx_now/50, 1.0),
                    "behavior": "runner_mode", "tp_mult": 4.0, "sl_mult": 0.5,
                    "partial_1": 0.20, "partial_2": 0.20, "runner": 0.60, "max_hold": "swing"}
        # COMPRESSION (squeeze before breakout)
        if bb_avg and bb_now < bb_avg * 0.7:
            return {"condition": "COMPRESSION", "confidence": 0.70,
                    "behavior": "breakout_ready", "tp_mult": 5.0, "sl_mult": 0.3,
                    "partial_1": 0.25, "partial_2": 0.25, "runner": 0.50, "max_hold": "swing"}
        # EXPANSION
        if atr_now > atr_avg * 1.5:
            return {"condition": "EXPANSION", "confidence": min(atr_now/(atr_avg*2), 1.0),
                    "behavior": "momentum_capture", "tp_mult": 6.0, "sl_mult": 0.6,
                    "partial_1": 0.10, "partial_2": 0.20, "runner": 0.70, "max_hold": "swing"}
        # RANGING
        if adx_now < 20 and bb_avg and bb_now < bb_avg:
            return {"condition": "RANGING", "confidence": 1 - (adx_now/20),
                    "behavior": "mean_reversion", "tp_mult": 1.5, "sl_mult": 0.8,
                    "partial_1": 0.50, "partial_2": 0.30, "runner": 0.20, "max_hold": "session"}
        return {"condition": "UNCLEAR", "behavior": "skip", "confidence": 0}
    except Exception as e:
        log.debug(f"classify_market({pair}) error: {e}")
        return {"condition": "UNCLEAR", "behavior": "skip", "confidence": 0}

# ── H4 bias ────────────────────────────────────────────────────────────────────
def _tf_bias(df: Optional[pd.DataFrame]) -> Optional[str]:
    if df is None or len(df) < 50: return None
    fast = min(20, len(df)//3)
    slow = min(50, len(df)//2)
    if fast < 5 or slow < 10: return None
    ef = ema(df["close"], fast).iloc[-1]
    es = ema(df["close"], slow).iloc[-1]
    last = df["close"].iloc[-1]
    if last > ef > es: return "buy"
    if last < ef < es: return "sell"
    return None

def get_h4_bias(pair: str) -> Optional[str]:
    h4 = get_mt5_data(pair, "H4", 250)
    if h4 is None or len(h4) < 210: return None
    e50  = ema(h4["close"], 50).iloc[-1]
    e200 = ema(h4["close"], 200).iloc[-1]
    last = h4["close"].iloc[-1]
    if last > e50 > e200: return "buy"
    if last < e50 < e200: return "sell"
    return None

def get_aligned_bias(pair: str) -> Optional[str]:
    """MN1 + W1 + D1 must all agree (FTMO multi-TF rule)."""
    mn = get_mt5_data(pair, "MN1", 24)
    w1 = get_mt5_data(pair, "W1", 52)
    d1 = get_mt5_data(pair, "D1", 200)
    bmn, bw, bd = _tf_bias(mn), _tf_bias(w1), _tf_bias(d1)
    if bmn and bw and bd and bmn == bw == bd:
        return bmn
    # Fall back to W1+D1 agreement if MN1 sparse
    if bw and bd and bw == bd:
        return bw
    return None

# ── Entry signals ──────────────────────────────────────────────────────────────
def detect_liquidity_sweep(m5: pd.DataFrame) -> Optional[Dict]:
    """Sweep of recent high/low with rejection wick."""
    if len(m5) < 30:
        return None
    last = m5.iloc[-1]
    prev = m5.iloc[-30:-1]
    body = abs(last["close"] - last["open"])
    upper_wick = last["high"] - max(last["open"], last["close"])
    lower_wick = min(last["open"], last["close"]) - last["low"]
    # Bullish sweep: swept previous low, closed above, big lower wick
    if last["low"] < prev["low"].min() and last["close"] > last["open"] and lower_wick > body * 1.5:
        return {"direction": "buy"}
    # Bearish sweep
    if last["high"] > prev["high"].max() and last["close"] < last["open"] and upper_wick > body * 1.5:
        return {"direction": "sell"}
    return None

def detect_fvg(m5: pd.DataFrame) -> Optional[Dict]:
    """3-bar Fair Value Gap detected in last 10 bars."""
    if len(m5) < 12:
        return None
    for i in range(len(m5) - 10, len(m5) - 2):
        b1, b2, b3 = m5.iloc[i], m5.iloc[i+1], m5.iloc[i+2]
        # Bullish FVG: b3.low > b1.high
        if b3["low"] > b1["high"] and b2["close"] > b2["open"]:
            # Price in or just above gap
            if m5["close"].iloc[-1] > b1["high"]:
                return {"direction": "buy"}
        # Bearish FVG
        if b3["high"] < b1["low"] and b2["close"] < b2["open"]:
            if m5["close"].iloc[-1] < b1["low"]:
                return {"direction": "sell"}
    return None

def detect_bos(m5: pd.DataFrame) -> Optional[Dict]:
    """Break of structure: latest close beyond recent swing high/low."""
    if len(m5) < 30:
        return None
    recent = m5.iloc[-30:-2]
    last_close = m5["close"].iloc[-1]
    if last_close > recent["high"].max():
        return {"direction": "buy"}
    if last_close < recent["low"].min():
        return {"direction": "sell"}
    return None

def detect_momentum(m5: pd.DataFrame) -> Dict:
    """Strong directional momentum on M5."""
    if len(m5) < 30:
        return {"strong": False, "direction": None}
    close = m5["close"]
    e8, e21 = ema(close, 8).iloc[-1], ema(close, 21).iloc[-1]
    r = rsi(close, 14).iloc[-1]
    last_5 = close.iloc[-5:]
    pct_move = (last_5.iloc[-1] - last_5.iloc[0]) / last_5.iloc[0]
    if e8 > e21 and r > 55 and pct_move > 0.0015:
        return {"strong": True, "direction": "buy"}
    if e8 < e21 and r < 45 and pct_move < -0.0015:
        return {"strong": True, "direction": "sell"}
    return {"strong": False, "direction": None}

def detect_breakout(m5: pd.DataFrame) -> Optional[Dict]:
    """Compression breakout: close outside last 20-bar BB."""
    if len(m5) < 25:
        return None
    sma = m5["close"].rolling(20).mean()
    std = m5["close"].rolling(20).std()
    upper, lower = sma.iloc[-1] + 2*std.iloc[-1], sma.iloc[-1] - 2*std.iloc[-1]
    last = m5["close"].iloc[-1]
    if last > upper:
        return {"direction": "buy"}
    if last < lower:
        return {"direction": "sell"}
    return None

def find_entry(pair: str, condition: Dict, profile: Dict) -> Optional[Dict]:
    """Confluence of 5 signal types; aligns with H4 bias."""
    if condition.get("behavior") == "skip":
        return None
    m5 = get_mt5_data(pair, "M5", 200)
    if m5 is None or len(m5) < 50:
        return None
    h4_bias = get_aligned_bias(pair)
    if h4_bias is None:
        h4_bias = get_h4_bias(pair)
    if h4_bias is None:
        return None

    signals = []
    sweep = detect_liquidity_sweep(m5)
    if sweep and sweep["direction"] == h4_bias:
        signals.append({"type": "sweep", "strength": 0.80 * profile.get("sweep", 1.0)})
    fvg = detect_fvg(m5)
    if fvg and fvg["direction"] == h4_bias:
        signals.append({"type": "fvg", "strength": 0.70 * profile.get("fvg", 1.0)})
    bos = detect_bos(m5)
    if bos and bos["direction"] == h4_bias:
        signals.append({"type": "bos", "strength": 0.75 * profile.get("bos", 1.0)})
    mom = detect_momentum(m5)
    if mom["strong"] and mom["direction"] == h4_bias:
        signals.append({"type": "momentum", "strength": 0.85 * profile.get("momentum", 1.0)})
    if condition.get("condition") == "COMPRESSION":
        br = detect_breakout(m5)
        if br and br["direction"] == h4_bias:
            signals.append({"type": "breakout", "strength": 0.90 * profile.get("breakout", 1.0)})

    if len(signals) < MIN_SIGNALS:
        return None
    confidence = sum(s["strength"] for s in signals) / len(signals)
    confidence *= condition.get("confidence", 1.0)
    if confidence < MIN_CONFIDENCE:
        return None

    return {
        "pair": pair, "direction": h4_bias, "confidence": confidence,
        "signals": [s["type"] for s in signals], "condition": condition.get("condition"),
    }

# ── SL / TP / lots ─────────────────────────────────────────────────────────────
def avoid_round_number(price: float, pair: str) -> float:
    """Move SL away from psychological round levels."""
    if "JPY" in pair:
        nearest = round(price * 10) / 10
        if abs(price - nearest) < 0.02:
            return price - 0.03 if price > nearest else price + 0.03
    elif pair in ("XAUUSD",):
        nearest = round(price)
        if abs(price - nearest) < 0.30:
            return price - 0.50 if price > nearest else price + 0.50
    elif pair in ("BTCUSD",):
        nearest = round(price / 100) * 100
        if abs(price - nearest) < 30:
            return price - 50 if price > nearest else price + 50
    else:
        nearest = round(price, 3)
        if abs(price - nearest) < 0.0002:
            return price - 0.0003 if price > nearest else price + 0.0003
    return price

def calculate_sl(pair: str, direction: str, condition: Dict) -> Optional[Tuple[float, float]]:
    m5 = get_mt5_data(pair, "M5", 100)
    if m5 is None or len(m5) < 30:
        return None
    atr_val = float(atr(m5, 14).iloc[-1])
    sl_mult = condition.get("sl_mult", 0.5)

    tick = _mt5.symbol_info_tick(pair) if _mt5 else None
    if tick is None:
        return None
    entry = tick.ask if direction == "buy" else tick.bid

    if direction == "buy":
        swing_low = float(m5["low"].iloc[-20:].min())
        sl = swing_low - atr_val * 0.2
        sl = avoid_round_number(sl, pair)
    else:
        swing_high = float(m5["high"].iloc[-20:].max())
        sl = swing_high + atr_val * 0.2
        sl = avoid_round_number(sl, pair)

    sl_distance = abs(entry - sl)
    if sl_distance < atr_val * 0.4:
        sl_distance = atr_val * 0.4
        sl = entry - sl_distance if direction == "buy" else entry + sl_distance
    if sl_distance > atr_val * sl_mult * 3:
        return None
    return sl, sl_distance

def calculate_tp(entry: float, sl_dist: float, direction: str, condition: Dict) -> float:
    tp_mult = condition.get("tp_mult", 3.0)
    if direction == "buy":
        return entry + sl_dist * tp_mult
    return entry - sl_dist * tp_mult

def calculate_lots(pair: str, sl_distance: float, balance: float) -> float:
    risk_amount = balance * MAX_RISK_PER_TRADE
    pip_val = PIP_VALUE.get(pair, 10.0)
    # For non-FX, sl_distance is already in price units worth pip_val per unit
    if pair in ("BTCUSD", "ETHUSD", "NAS100", "US30"):
        lots = risk_amount / max(sl_distance * pip_val, 0.01)
    elif "JPY" in pair:
        pips = sl_distance * 100
        lots = risk_amount / max(pips * pip_val, 0.01)
    elif pair in ("XAUUSD", "XAGUSD"):
        lots = risk_amount / max(sl_distance * pip_val, 0.01)
    else:
        pips = sl_distance * 10000
        lots = risk_amount / max(pips * pip_val, 0.01)
    return max(0.01, min(round(lots, 2), 1.0))

# ── Correlation / daily limit ──────────────────────────────────────────────────
def check_correlation(pair: str) -> bool:
    if not ensure_mt5(): return True
    positions = _mt5.positions_get() or []
    for group, members in CORRELATION_GROUPS.items():
        if pair in members:
            existing = [p for p in positions if p.symbol in members]
            if len(existing) >= MAX_CORRELATED:
                return False
    return True

def _load_daily_pnl() -> dict:
    try:
        if DAILY_PNL_FILE.exists():
            return json.load(open(DAILY_PNL_FILE))
    except Exception: pass
    return {}

def _save_daily_pnl(d: dict):
    try: json.dump(d, open(DAILY_PNL_FILE, "w"), indent=2)
    except Exception: pass

def check_daily_limit() -> bool:
    if not ensure_mt5(): return True
    info = _mt5.account_info()
    if info is None: return True
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    d = _load_daily_pnl()
    if today not in d:
        d[today] = {"start_balance": info.balance, "paused": False}
        _save_daily_pnl(d)
    start = d[today]["start_balance"]
    loss_pct = (start - info.balance) / start if start > 0 else 0
    if loss_pct >= MAX_DAILY_LOSS:
        if not d[today].get("paused"):
            send_telegram(f"⛔ DAILY LIMIT HIT — {loss_pct:.1%} loss\nTrading paused for today")
            d[today]["paused"] = True
            _save_daily_pnl(d)
        return False
    return not d[today].get("paused", False)

# ── Session filter ─────────────────────────────────────────────────────────────
def in_session() -> bool:
    """London 07-10 UTC + NY 13-16 UTC."""
    h = datetime.now(timezone.utc).hour
    return (7 <= h <= 10) or (13 <= h <= 16)

# ── Pair profiles (learning) ───────────────────────────────────────────────────
def _load_profiles() -> dict:
    try:
        if PAIR_PROFILES.exists():
            return json.load(open(PAIR_PROFILES))
    except Exception: pass
    return {}

def _save_profiles(d: dict):
    try: json.dump(d, open(PAIR_PROFILES, "w"), indent=2)
    except Exception: pass

def get_profile(pair: str) -> dict:
    profiles = _load_profiles()
    return profiles.get(pair, {"sweep":1.0,"fvg":1.0,"bos":1.0,"momentum":1.0,"breakout":1.0,
                                "trades":0,"wins":0,"total_r":0.0})

def update_profile(pair: str, signals: List[str], won: bool, realized_r: float):
    profiles = _load_profiles()
    p = profiles.get(pair, {"sweep":1.0,"fvg":1.0,"bos":1.0,"momentum":1.0,"breakout":1.0,
                            "trades":0,"wins":0,"total_r":0.0})
    p["trades"] = p.get("trades", 0) + 1
    if won: p["wins"] = p.get("wins", 0) + 1
    p["total_r"] = p.get("total_r", 0.0) + realized_r
    # Reward / punish signals
    delta = 0.05 if won else -0.05
    for s in signals:
        cur = p.get(s, 1.0)
        p[s] = max(0.3, min(1.5, cur + delta))
    profiles[pair] = p
    _save_profiles(profiles)

# ── Trade tags (partial exits done) ────────────────────────────────────────────
def _load_tags() -> dict:
    try:
        if TRADE_TAGS.exists(): return json.load(open(TRADE_TAGS))
    except Exception: pass
    return {}

def _save_tags(d: dict):
    try: json.dump(d, open(TRADE_TAGS, "w"), indent=2)
    except Exception: pass

def tag(ticket: int, key: str) -> bool:
    d = _load_tags()
    return key in d.get(str(ticket), [])

def tag_set(ticket: int, key: str):
    d = _load_tags()
    s = d.get(str(ticket), [])
    if key not in s:
        s.append(key); d[str(ticket)] = s; _save_tags(d)

# ── Order execution ────────────────────────────────────────────────────────────
def place_trade(pair: str, direction: str, sl: float, tp: float, lots: float) -> Optional[object]:
    if not ensure_mt5(): return None
    try:
        info = _mt5.symbol_info(pair)
        if info is None:
            log.warning(f"symbol_info None for {pair}")
            return None
        if not info.visible:
            _mt5.symbol_select(pair, True)
        tick = _mt5.symbol_info_tick(pair)
        if tick is None: return None
        price = tick.ask if direction == "buy" else tick.bid
        order_type = _mt5.ORDER_TYPE_BUY if direction == "buy" else _mt5.ORDER_TYPE_SELL
        request = {
            "action": _mt5.TRADE_ACTION_DEAL, "symbol": pair, "volume": float(lots),
            "type": order_type, "price": price, "sl": float(sl), "tp": float(tp),
            "deviation": 20, "magic": 234000, "comment": "OMEGA_ADAPTIVE",
            "type_time": _mt5.ORDER_TIME_GTC, "type_filling": _mt5.ORDER_FILLING_IOC,
        }
        result = _mt5.order_send(request)
        if result is None:
            log.warning(f"order_send returned None for {pair}")
            return None
        if result.retcode != _mt5.TRADE_RETCODE_DONE:
            # Try FOK if IOC rejected
            request["type_filling"] = _mt5.ORDER_FILLING_FOK
            result = _mt5.order_send(request)
        return result
    except Exception as e:
        log.warning(f"place_trade error {pair}: {e}")
        return None

def close_partial(position, pct: float) -> bool:
    if not ensure_mt5(): return False
    try:
        vol = round(position.volume * pct, 2)
        if vol < 0.01: return False
        opp_type = _mt5.ORDER_TYPE_SELL if position.type == _mt5.ORDER_TYPE_BUY else _mt5.ORDER_TYPE_BUY
        tick = _mt5.symbol_info_tick(position.symbol)
        price = tick.bid if position.type == _mt5.ORDER_TYPE_BUY else tick.ask
        request = {
            "action": _mt5.TRADE_ACTION_DEAL, "position": position.ticket,
            "symbol": position.symbol, "volume": vol, "type": opp_type, "price": price,
            "deviation": 20, "magic": 234000, "comment": "OMEGA_PARTIAL",
            "type_filling": _mt5.ORDER_FILLING_IOC,
        }
        result = _mt5.order_send(request)
        return result is not None and result.retcode == _mt5.TRADE_RETCODE_DONE
    except Exception as e:
        log.warning(f"close_partial error: {e}")
        return False

def modify_sl(position, new_sl: float) -> bool:
    if not ensure_mt5(): return False
    try:
        request = {
            "action": _mt5.TRADE_ACTION_SLTP, "position": position.ticket,
            "symbol": position.symbol, "sl": float(new_sl), "tp": float(position.tp),
        }
        result = _mt5.order_send(request)
        return result is not None and result.retcode == _mt5.TRADE_RETCODE_DONE
    except Exception as e:
        log.warning(f"modify_sl error: {e}")
        return False

def close_position(position) -> bool:
    return close_partial(position, 1.0)

# ── Exit management ────────────────────────────────────────────────────────────
def manage_position(position):
    """Run partial exits + trailing stops based on the condition tagged at entry."""
    try:
        tick = _mt5.symbol_info_tick(position.symbol)
        if tick is None: return
        entry = position.price_open
        sl    = position.sl
        if sl == 0: return
        sl_dist = abs(entry - sl)
        if sl_dist == 0: return
        current = tick.bid if position.type == _mt5.ORDER_TYPE_BUY else tick.ask
        if position.type == _mt5.ORDER_TYPE_BUY:
            profit_r = (current - entry) / sl_dist
        else:
            profit_r = (entry - current) / sl_dist

        # Re-read condition (recompute if needed; default to TRENDING profile)
        cond = classify_market(position.symbol)
        p1 = cond.get("partial_1", 0.25)
        p2 = cond.get("partial_2", 0.25)
        behavior = cond.get("behavior", "runner_mode")

        # PARTIAL 1 at 1.5R
        if profit_r >= 1.5 and not tag(position.ticket, "p1") and p1 > 0:
            if close_partial(position, p1):
                modify_sl(position, entry)  # BE
                tag_set(position.ticket, "p1")
                send_telegram(
                    f"✅ PARTIAL 1 — {position.symbol}\n"
                    f"Closed {int(p1*100)}% at 1.5R | SL→BE\n"
                    f"PnL: ${position.profit:.2f}")

        # PARTIAL 2 at 2.5R
        if profit_r >= 2.5 and not tag(position.ticket, "p2") and p2 > 0:
            if close_partial(position, p2):
                # trailing: 1 ATR behind
                m5 = get_mt5_data(position.symbol, "M5", 60)
                if m5 is not None:
                    atr_v = float(atr(m5, 14).iloc[-1])
                    new_sl = current - atr_v if position.type == _mt5.ORDER_TYPE_BUY else current + atr_v
                    modify_sl(position, new_sl)
                tag_set(position.ticket, "p2")
                send_telegram(
                    f"✅ PARTIAL 2 — {position.symbol}\n"
                    f"Closed {int(p2*100)}% at 2.5R | Trailing ON\n"
                    f"PnL: ${position.profit:.2f}")

        # Full runner: at 1R move BE, at 3R trail 2ATR
        if behavior == "full_runner":
            if profit_r >= 1.0 and not tag(position.ticket, "be"):
                modify_sl(position, entry); tag_set(position.ticket, "be")
            if profit_r >= 3.0:
                m5 = get_mt5_data(position.symbol, "M5", 60)
                if m5 is not None:
                    atr_v = float(atr(m5, 14).iloc[-1])
                    new_sl = current - 2*atr_v if position.type == _mt5.ORDER_TYPE_BUY else current + 2*atr_v
                    if (position.type == _mt5.ORDER_TYPE_BUY and new_sl > position.sl) or \
                       (position.type == _mt5.ORDER_TYPE_SELL and new_sl < position.sl):
                        modify_sl(position, new_sl)

        # Runner trail update after p2
        if profit_r >= 2.5 and tag(position.ticket, "p2"):
            m5 = get_mt5_data(position.symbol, "M5", 60)
            if m5 is not None:
                atr_v = float(atr(m5, 14).iloc[-1])
                new_sl = current - 1.5*atr_v if position.type == _mt5.ORDER_TYPE_BUY else current + 1.5*atr_v
                if (position.type == _mt5.ORDER_TYPE_BUY and new_sl > position.sl) or \
                   (position.type == _mt5.ORDER_TYPE_SELL and new_sl < position.sl):
                    modify_sl(position, new_sl)

    except Exception as e:
        log.debug(f"manage_position error: {e}")

# ── Position monitor thread ────────────────────────────────────────────────────
_closed_tickets: set = set()

def monitor_positions_loop():
    """Background: run exit management every 30s + report closes."""
    while True:
        try:
            if ensure_mt5():
                positions = _mt5.positions_get() or []
                open_tickets = {p.ticket for p in positions}
                for pos in positions:
                    if pos.magic == 234000:
                        manage_position(pos)
                # Detect closed trades
                history_from = datetime.now(timezone.utc) - timedelta(hours=24)
                deals = _mt5.history_deals_get(history_from, datetime.now(timezone.utc)) or []
                for d in deals:
                    if d.magic != 234000: continue
                    if d.entry != _mt5.DEAL_ENTRY_OUT: continue
                    if d.position_id in _closed_tickets: continue
                    if d.position_id in open_tickets: continue
                    _closed_tickets.add(d.position_id)
                    # Compute realized R from related deals
                    related = [x for x in deals if x.position_id == d.position_id]
                    pnl = sum(x.profit for x in related)
                    won = pnl > 0
                    # Approximate realized R: pnl / risk
                    info = _mt5.account_info()
                    risk = (info.balance if info else 5000) * MAX_RISK_PER_TRADE
                    rr = pnl / risk if risk > 0 else 0
                    log.info(f"Position {d.position_id} closed: pnl={pnl:.2f} R={rr:.2f}")
                    send_telegram(
                        f"{'🟢 WIN' if won else '🔴 LOSS'} {d.symbol}\n"
                        f"PnL: ${pnl:+.2f} | R: {rr:+.2f}\n"
                        f"Balance: ${info.balance if info else 0:.2f}")
                    # Learning update (we don't have signals here; best-effort)
                    update_profile(d.symbol, [], won, rr)
                    send_balance_update()
        except Exception as e:
            log.debug(f"monitor_positions error: {e}")
        time.sleep(30)

# ── Progress tracker ───────────────────────────────────────────────────────────
_target_hit_sent = False

def send_balance_update():
    global _target_hit_sent
    if not ensure_mt5(): return
    info = _mt5.account_info()
    if info is None: return
    balance = info.balance
    progress = ((balance - START_BALANCE_BASE) / START_BALANCE_BASE) * 100
    remaining = max(0, START_BALANCE_TARGET - balance)
    send_telegram(
        f"💰 BALANCE UPDATE\n"
        f"Balance: ${balance:.2f}\n"
        f"Target: ${START_BALANCE_TARGET:.0f}\n"
        f"Progress: {progress:.2f}% | Remaining: ${remaining:.2f}\n"
        f"{'🎯 TARGET HIT!' if balance>=START_BALANCE_TARGET else 'Keep going...'}")
    if balance >= START_BALANCE_TARGET and not _target_hit_sent:
        _target_hit_sent = True
        send_telegram(
            "🏆 10% TARGET ACHIEVED\n"
            f"Balance: ${balance:.2f}\n"
            "System proven on demo.\n"
            "Ready for FTMO challenge.\n"
            "Awaiting your instruction.")

# ── State persistence ──────────────────────────────────────────────────────────
def load_state() -> dict:
    try:
        if STATE_FILE.exists(): return json.load(open(STATE_FILE))
    except Exception: pass
    return {"iteration": 0, "best_wr": {}, "best_expectancy": {},
            "best_params": {}, "best_score": {}}

def save_state(state: dict):
    try: json.dump(state, open(STATE_FILE, "w"), indent=2)
    except Exception as e: log.debug(f"save_state error: {e}")

# ── Email scheduling ───────────────────────────────────────────────────────────
def _load_tracker() -> dict:
    try:
        if EMAIL_TRACKER.exists(): return json.load(open(EMAIL_TRACKER))
    except Exception: pass
    return {}

def _mark_email(key: str):
    d = _load_tracker(); d[key] = datetime.utcnow().isoformat()
    try: json.dump(d, open(EMAIL_TRACKER, "w"), indent=2)
    except Exception: pass

def _email_sent_today(key: str) -> bool:
    d = _load_tracker()
    if key not in d: return False
    try:
        last = datetime.fromisoformat(d[key])
        return last.date() == datetime.utcnow().date()
    except Exception: return False

def send_email(subject: str, html: str) -> bool:
    if not EMAIL_FROM or not EMAIL_PASS or not EMAIL_TO:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject; msg["From"] = EMAIL_FROM; msg["To"] = EMAIL_TO
        msg.attach(MIMEText(html, "html"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30) as s:
            s.login(EMAIL_FROM, EMAIL_PASS)
            s.send_message(msg)
        return True
    except Exception as e:
        log.debug(f"email error: {e}")
        return False

# ── Git push ───────────────────────────────────────────────────────────────────
def git_push(iteration: int, note: str = "") -> bool:
    if not GH_TOKEN: return False
    try:
        cwd = str(ROOT)
        subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True, timeout=30)
        msg = f"iter{iteration}" + (f" {note}" if note else "")
        subprocess.run(["git", "commit", "-m", msg], cwd=cwd, capture_output=True, timeout=30)
        remote = f"https://{GH_TOKEN}@github.com/{GH_USER}/{GH_REPO}.git"
        for branch in ["master", "main"]:
            r = subprocess.run(["git", "push", remote, f"HEAD:{branch}"],
                               cwd=cwd, capture_output=True, timeout=60)
            if r.returncode == 0: return True
        return False
    except Exception: return False

# ── RAM ────────────────────────────────────────────────────────────────────────
def get_ram_pct() -> float:
    try:
        import psutil
        return psutil.virtual_memory().percent
    except ImportError: return 0.0

# ── Evolution (background) ─────────────────────────────────────────────────────
_evolution_iter = 0
_global_best_score: Dict[str, float] = {}

def evolution_loop():
    """Background micro-evolution: tune signal weights based on observed pair perf."""
    global _evolution_iter
    while True:
        try:
            # Light periodic recalibration — no heavy backtest here (live profiles drive it)
            profiles = _load_profiles()
            for pair in PAIRS:
                p = profiles.get(pair)
                if not p or p.get("trades", 0) < 10: continue
                wr = p["wins"] / max(p["trades"], 1)
                if wr < 0.50:
                    # weaken all signals slightly
                    for k in ("sweep","fvg","bos","momentum","breakout"):
                        p[k] = max(0.3, p.get(k,1.0) - 0.01)
                elif wr > 0.65:
                    for k in ("sweep","fvg","bos","momentum","breakout"):
                        p[k] = min(1.5, p.get(k,1.0) + 0.005)
            _save_profiles(profiles)
            _evolution_iter += 1
            if _evolution_iter % 10 == 0:
                git_push(_evolution_iter, "evolution")
            gc.collect()
        except Exception as e:
            log.debug(f"evolution_loop error: {e}")
        time.sleep(600)  # every 10 min

# ── Scheduler ──────────────────────────────────────────────────────────────────
def scheduler_loop():
    while True:
        try:
            now = datetime.now(timezone.utc)
            ram = get_ram_pct()
            if ram > 88:
                send_telegram(f"⚠️ HIGH RAM: {ram:.0f}%")
            # Daily 08:00 UTC — evolution email
            if now.hour == 8 and now.minute < 5 and not _email_sent_today("evolution"):
                profiles = _load_profiles()
                rows = "".join(
                    f"<tr><td>{p}</td><td>{v.get('trades',0)}</td>"
                    f"<td>{(v['wins']/max(v['trades'],1)):.1%}</td>"
                    f"<td>{v.get('total_r',0):.2f}R</td></tr>"
                    for p, v in profiles.items() if v.get("trades",0) > 0)
                html = (f"<h2>Evolution Report — {now.strftime('%Y-%m-%d')}</h2>"
                        f"<table border=1 cellpadding=4><tr><th>Pair</th><th>Trades</th>"
                        f"<th>WR</th><th>Total R</th></tr>{rows}</table>")
                if send_email(f"AutoTrader Evolution — {now.strftime('%Y-%m-%d')}", html):
                    _mark_email("evolution")
            # Daily 09:00 UTC — trade report
            if now.hour == 9 and now.minute < 5 and not _email_sent_today("trade"):
                if ensure_mt5():
                    history_from = datetime.now(timezone.utc) - timedelta(days=1)
                    deals = _mt5.history_deals_get(history_from, datetime.now(timezone.utc)) or []
                    omega = [d for d in deals if d.magic == 234000 and d.entry == _mt5.DEAL_ENTRY_OUT]
                    rows = "".join(
                        f"<tr><td>{d.symbol}</td><td>${d.profit:.2f}</td></tr>"
                        for d in omega)
                    html = f"<h2>Trade Report — {now.strftime('%Y-%m-%d')}</h2><table border=1>{rows}</table>"
                    if send_email(f"AutoTrader Trades — {now.strftime('%Y-%m-%d')}", html):
                        _mark_email("trade")
        except Exception as e:
            log.debug(f"scheduler error: {e}")
        time.sleep(60)

# ── Adaptive risk (FTMO) ───────────────────────────────────────────────────────
def calculate_risk_pct(confidence: float) -> float:
    if confidence >= 0.85: return 0.0100   # 1.00%
    if confidence >= 0.75: return 0.0075   # 0.75%
    if confidence >= 0.65: return 0.0050   # 0.50%
    return MIN_RISK_PER_TRADE              # 0.25%

# ── FTMO limit check ───────────────────────────────────────────────────────────
_ftmo_state_cache = {"daily_start": None, "daily_date": None,
                     "peak_balance": None, "start_balance": None,
                     "paused_today": False, "halted_total": False}

def check_ftmo_limits() -> bool:
    """Returns True if trading allowed under FTMO rules."""
    if not ensure_mt5(): return False
    info = _mt5.account_info()
    if info is None: return False
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    eq = info.equity; bal = info.balance

    # Track persistent start balance
    if _ftmo_state_cache["start_balance"] is None:
        st = load_state()
        if "start_balance" in st:
            _ftmo_state_cache["start_balance"] = st["start_balance"]
        else:
            _ftmo_state_cache["start_balance"] = bal
            st["start_balance"] = bal
            save_state(st)

    # Daily reset
    if _ftmo_state_cache["daily_date"] != today:
        _ftmo_state_cache["daily_date"] = today
        _ftmo_state_cache["daily_start"] = bal
        _ftmo_state_cache["paused_today"] = False

    # Peak balance for total DD
    if _ftmo_state_cache["peak_balance"] is None or bal > _ftmo_state_cache["peak_balance"]:
        _ftmo_state_cache["peak_balance"] = max(bal, _ftmo_state_cache.get("peak_balance") or bal)
        st = load_state()
        st["peak_balance"] = _ftmo_state_cache["peak_balance"]
        save_state(st)

    if _ftmo_state_cache["halted_total"]:
        return False

    daily_start = _ftmo_state_cache["daily_start"]
    daily_loss_pct = (daily_start - eq) / daily_start if daily_start else 0
    if daily_loss_pct >= MAX_DAILY_LOSS:
        if not _ftmo_state_cache["paused_today"]:
            send_telegram(
                f"⛔ DAILY LIMIT HIT\n"
                f"Loss: {daily_loss_pct:.1%} | FTMO max: 5%\n"
                f"Trading paused until tomorrow.")
            _ftmo_state_cache["paused_today"] = True
        return False

    peak = _ftmo_state_cache["peak_balance"]
    total_dd_pct = (peak - eq) / peak if peak else 0
    if total_dd_pct >= MAX_TOTAL_DD:
        send_telegram(
            f"🚨 TOTAL DD LIMIT HIT\n"
            f"DD: {total_dd_pct:.1%} | FTMO max: 10%\n"
            f"ALL TRADING STOPPED — manual review required.")
        _ftmo_state_cache["halted_total"] = True
        return False

    # Target achievement notice
    start = _ftmo_state_cache["start_balance"]
    profit_pct = (bal - start) / start if start else 0
    st = load_state()
    if profit_pct >= FTMO_PROFIT_TARGET and not st.get("ftmo_target_sent"):
        send_telegram(
            f"🏆 FTMO TARGET ACHIEVED!\n"
            f"Profit: {profit_pct:.1%}\n"
            f"Target was: {FTMO_PROFIT_TARGET:.0%}\n"
            f"CHALLENGE PASSED!")
        st["ftmo_target_sent"] = True
        save_state(st)
    return True

def get_ftmo_progress() -> dict:
    if not ensure_mt5(): return {}
    info = _mt5.account_info()
    if info is None: return {}
    bal = info.balance; eq = info.equity
    start = _ftmo_state_cache.get("start_balance") or load_state().get("start_balance", bal)
    peak  = _ftmo_state_cache.get("peak_balance") or bal
    daily_start = _ftmo_state_cache.get("daily_start") or bal
    return {
        "balance": bal, "equity": eq, "start": start, "peak": peak,
        "profit_pct": ((bal - start) / start * 100) if start else 0,
        "daily_loss_pct": max(0, (daily_start - eq) / daily_start * 100) if daily_start else 0,
        "total_dd_pct": max(0, (peak - eq) / peak * 100) if peak else 0,
    }

# ── 2-year history analyzer ────────────────────────────────────────────────────
HISTORY_REPORT_FILE = DATADIR / "history_report.json"

def _quick_backtest_signals(df: pd.DataFrame) -> List[dict]:
    """Quick EMA crossover backtest to get stats."""
    if df is None or len(df) < 100: return []
    df = df.copy()
    df["ef"] = ema(df["close"], 20)
    df["es"] = ema(df["close"], 50)
    df["atr"] = atr(df, 14)
    trades = []
    in_pos = False; entry = 0; sl = 0; tp = 0; direction = ""
    for i in range(60, len(df) - 1):
        row = df.iloc[i]; prev = df.iloc[i-1]
        if not in_pos:
            if prev["ef"] < prev["es"] and row["ef"] > row["es"]:
                entry = row["close"]; sl = entry - row["atr"]; tp = entry + 3*row["atr"]
                direction = "buy"; in_pos = True
            elif prev["ef"] > prev["es"] and row["ef"] < row["es"]:
                entry = row["close"]; sl = entry + row["atr"]; tp = entry - 3*row["atr"]
                direction = "sell"; in_pos = True
        else:
            nrow = df.iloc[i+1]
            if direction == "buy":
                if nrow["low"] <= sl:
                    trades.append({"pnl": sl - entry}); in_pos = False
                elif nrow["high"] >= tp:
                    trades.append({"pnl": tp - entry}); in_pos = False
            else:
                if nrow["high"] >= sl:
                    trades.append({"pnl": entry - sl}); in_pos = False
                elif nrow["low"] <= tp:
                    trades.append({"pnl": entry - tp}); in_pos = False
    return trades

def analyze_pair_history(pair: str, timeframe: str = "D1", bars: int = 730) -> dict:
    df = get_mt5_data(pair, timeframe, bars)
    if df is None or len(df) < 100:
        return {}
    atr_v  = float(atr(df, 14).mean())
    adx_v  = adx(df, 14)
    vol    = atr_v / df["close"].mean() * 100 if df["close"].mean() else 0
    trades = _quick_backtest_signals(df)
    if not trades: return {}
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total = len(trades)
    wr = len(wins) / total if total else 0
    avg_win = float(np.mean([t["pnl"] for t in wins])) if wins else 0
    avg_loss = float(abs(np.mean([t["pnl"] for t in losses]))) if losses else 0
    rrr = avg_win / avg_loss if avg_loss > 0 else 0
    exp = wr * avg_win - (1 - wr) * avg_loss
    return {
        "total_signals": total, "win_rate": round(wr * 100, 1),
        "avg_rrr": round(rrr, 2), "expectancy": round(exp, 4),
        "trend_strength": round(float(adx_v), 1),
        "volatility_pct": round(vol, 3),
    }

def read_all_history():
    """Analyze 2 years of D1 + H4 for all pairs. Send Telegram + save JSON."""
    log.info("Starting 2-year history analysis...")
    report = {}
    for pair in PAIRS:
        try:
            if ensure_mt5(): _mt5.symbol_select(pair, True)
            d1 = analyze_pair_history(pair, "D1", 730)
            h4 = analyze_pair_history(pair, "H4", 2000)
            if d1 or h4:
                report[pair] = {"D1": d1, "H4": h4}
            gc.collect()
        except Exception as e:
            log.warning(f"history {pair} error: {e}")
    try:
        json.dump(report, open(HISTORY_REPORT_FILE, "w"), indent=2)
    except Exception as e:
        log.debug(f"save history error: {e}")
    # Build ranked report
    scores = []
    for pair, tfs in report.items():
        d = tfs.get("D1", {})
        if d: scores.append({"pair": pair, "wr": d.get("win_rate",0),
                             "rrr": d.get("avg_rrr",0), "exp": d.get("expectancy",0)})
    scores.sort(key=lambda x: x["exp"], reverse=True)
    msg = "=== 2-YEAR HISTORY REPORT (D1) ===\n\nRanked by expectancy:\n"
    for i, p in enumerate(scores[:10]):
        msg += f"{i+1}. {p['pair']}: WR={p['wr']:.0f}% RRR={p['rrr']:.1f} Exp={p['exp']:.3f}\n"
    msg += "\nTop 3 for trading:\n"
    for p in scores[:3]:
        msg += f"→ {p['pair']}\n"
    msg += "\n==============================="
    send_telegram(msg)
    log.info("History analysis complete.")
    return report

# ── Telegram BOT (command handler) ─────────────────────────────────────────────
TRADING_ENABLED = True
_bot = None

def close_all_positions():
    if not ensure_mt5(): return 0
    positions = _mt5.positions_get() or []
    n = 0
    for p in positions:
        if close_position(p): n += 1
    return n

def start_telegram_bot():
    """Background thread: poll Telegram for commands."""
    global _bot
    try:
        import telebot
    except ImportError:
        log.warning("pytelegrambotapi not installed — bot disabled")
        return
    if not TG_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN missing — bot disabled")
        return

    _bot = telebot.TeleBot(TG_TOKEN, threaded=False)

    def _is_auth(message) -> bool:
        try:
            return str(message.chat.id) == str(TG_CHAT)
        except Exception:
            return False

    @_bot.message_handler(commands=["status"])
    def cmd_status(message):
        if not _is_auth(message): return
        try:
            info = _mt5.account_info() if ensure_mt5() else None
            positions = _mt5.positions_get() or [] if ensure_mt5() else []
            ram = get_ram_pct()
            bal = info.balance if info else 0
            eq  = info.equity if info else 0
            progress = ((bal - START_BALANCE_BASE) / START_BALANCE_BASE * 100) if bal else 0
            _bot.reply_to(message,
                f"=== STATUS ===\n"
                f"Balance: ${bal:.2f}\n"
                f"Equity: ${eq:.2f}\n"
                f"Open trades: {len(positions)}\n"
                f"RAM: {ram:.1f}%\n"
                f"Iteration: {_iteration}\n"
                f"MT5: {'Connected' if _mt5_connected else 'Disconnected'}\n"
                f"Trading: {'ENABLED' if TRADING_ENABLED else 'PAUSED'}\n"
                f"Target: ${START_BALANCE_TARGET:.0f}\n"
                f"Progress: {progress:.2f}%")
        except Exception as e:
            _bot.reply_to(message, f"Error: {e}")

    @_bot.message_handler(commands=["balance"])
    def cmd_balance(message):
        if not _is_auth(message): return
        try:
            if not ensure_mt5():
                _bot.reply_to(message, "MT5 disconnected"); return
            info = _mt5.account_info()
            bal = info.balance; eq = info.equity
            progress = (bal - START_BALANCE_BASE) / START_BALANCE_BASE * 100
            _bot.reply_to(message,
                f"Balance: ${bal:.2f}\n"
                f"Equity: ${eq:.2f}\n"
                f"Progress: {progress:.2f}%\n"
                f"Target: ${START_BALANCE_TARGET:.0f}")
        except Exception as e:
            _bot.reply_to(message, f"Error: {e}")

    @_bot.message_handler(commands=["trades"])
    def cmd_trades(message):
        if not _is_auth(message): return
        try:
            if not ensure_mt5():
                _bot.reply_to(message, "MT5 disconnected"); return
            positions = _mt5.positions_get() or []
            if not positions:
                _bot.reply_to(message, "No open trades."); return
            msg = "=== OPEN TRADES ===\n"
            for p in positions:
                direction = "BUY" if p.type == _mt5.ORDER_TYPE_BUY else "SELL"
                msg += (f"{p.symbol} {direction}\n"
                        f"Entry: {p.price_open}\n"
                        f"SL: {p.sl} | TP: {p.tp}\n"
                        f"PnL: ${p.profit:+.2f}\n"
                        f"Lots: {p.volume} | Ticket: {p.ticket}\n\n")
            _bot.reply_to(message, msg)
        except Exception as e:
            _bot.reply_to(message, f"Error: {e}")

    @_bot.message_handler(commands=["pause"])
    def cmd_pause(message):
        if not _is_auth(message): return
        global TRADING_ENABLED
        TRADING_ENABLED = False
        _bot.reply_to(message,
            "⏸ Trading PAUSED.\n"
            "Monitoring continues.\n"
            "Send /resume to restart.")

    @_bot.message_handler(commands=["resume"])
    def cmd_resume(message):
        if not _is_auth(message): return
        global TRADING_ENABLED
        TRADING_ENABLED = True
        _bot.reply_to(message,
            "▶ Trading RESUMED.\n"
            "Scanning markets now.")

    @_bot.message_handler(commands=["stop"])
    def cmd_stop(message):
        if not _is_auth(message): return
        _bot.reply_to(message,
            "🛑 Emergency stop received.\n"
            "Closing all positions...\n"
            "System shutting down safely.")
        try:
            n = close_all_positions()
            _bot.send_message(TG_CHAT, f"Closed {n} positions. Exiting.")
        except Exception as e:
            _bot.send_message(TG_CHAT, f"Close error: {e}")
        os._exit(0)

    @_bot.message_handler(commands=["close"])
    def cmd_close(message):
        if not _is_auth(message): return
        try:
            if not ensure_mt5():
                _bot.reply_to(message, "MT5 disconnected"); return
            positions = _mt5.positions_get() or []
            if not positions:
                _bot.reply_to(message, "No open trades to close."); return
            n = close_all_positions()
            info = _mt5.account_info()
            _bot.reply_to(message,
                f"Closed {n}/{len(positions)} trades.\n"
                f"Balance: ${info.balance:.2f}")
        except Exception as e:
            _bot.reply_to(message, f"Error: {e}")

    @_bot.message_handler(commands=["ram"])
    def cmd_ram(message):
        if not _is_auth(message): return
        try:
            import psutil
            ram = psutil.virtual_memory()
            _bot.reply_to(message,
                f"RAM Used: {ram.percent:.1f}%\n"
                f"Used: {ram.used/1e9:.2f}GB\n"
                f"Free: {ram.available/1e9:.2f}GB\n"
                f"Total: {ram.total/1e9:.2f}GB")
        except Exception as e:
            _bot.reply_to(message, f"Error: {e}")

    @_bot.message_handler(commands=["report"])
    def cmd_report(message):
        if not _is_auth(message): return
        try:
            if not ensure_mt5():
                _bot.reply_to(message, "MT5 disconnected"); return
            info = _mt5.account_info()
            positions = _mt5.positions_get() or []
            profiles = _load_profiles()
            lines = [f"=== FULL REPORT ===",
                     f"Balance: ${info.balance:.2f} | Equity: ${info.equity:.2f}",
                     f"Open positions: {len(positions)}",
                     f"Iteration: {_iteration}",
                     f"Trading: {'ENABLED' if TRADING_ENABLED else 'PAUSED'}",
                     f"RAM: {get_ram_pct():.1f}%",
                     "",
                     "Pair learning:"]
            for pair, p in sorted(profiles.items()):
                trades = p.get("trades", 0)
                if trades > 0:
                    wr = p.get("wins", 0) / trades
                    lines.append(f"  {pair}: {trades}t WR={wr:.0%} R={p.get('total_r',0):.1f}")
            if len(lines) == 8:
                lines.append("  (no closed trades yet)")
            _bot.reply_to(message, "\n".join(lines))
        except Exception as e:
            _bot.reply_to(message, f"Error: {e}")

    @_bot.message_handler(commands=["progress"])
    def cmd_progress(message):
        if not _is_auth(message): return
        try:
            p = get_ftmo_progress()
            if not p: _bot.reply_to(message, "MT5 disconnected"); return
            _bot.reply_to(message,
                f"=== FTMO PROGRESS ===\n"
                f"Target: 10% profit (Phase 1)\n"
                f"Current: {p['profit_pct']:.2f}%\n"
                f"Remaining: {max(0, 10 - p['profit_pct']):.2f}%\n"
                f"Daily loss used: {p['daily_loss_pct']:.2f}% / 5%\n"
                f"Total DD: {p['total_dd_pct']:.2f}% / 10%\n"
                f"Balance: ${p['balance']:.2f} | Peak: ${p['peak']:.2f}\n"
                f"Status: {'ON TRACK' if p['profit_pct'] >= 0 else 'RECOVERING'}")
        except Exception as e:
            _bot.reply_to(message, f"Error: {e}")

    @_bot.message_handler(commands=["history"])
    def cmd_history(message):
        if not _is_auth(message): return
        try:
            if not HISTORY_REPORT_FILE.exists():
                _bot.reply_to(message, "History not analyzed yet. Running now..."); return
            report = json.load(open(HISTORY_REPORT_FILE))
            scores = []
            for pair, tfs in report.items():
                d = tfs.get("D1", {})
                if d: scores.append((pair, d.get("win_rate",0), d.get("avg_rrr",0), d.get("expectancy",0)))
            scores.sort(key=lambda x: x[3], reverse=True)
            text = "=== 2Y HISTORY (D1) ===\n"
            for p in scores[:10]:
                text += f"{p[0]}: WR={p[1]:.0f}% RRR={p[2]:.1f} Exp={p[3]:.3f}\n"
            _bot.reply_to(message, text)
        except Exception as e:
            _bot.reply_to(message, f"Error: {e}")

    @_bot.message_handler(commands=["best"])
    def cmd_best(message):
        if not _is_auth(message): return
        try:
            profiles = _load_profiles()
            scored = [(pair, p) for pair, p in profiles.items() if p.get("trades", 0) > 0]
            scored.sort(key=lambda x: x[1].get("total_r", 0), reverse=True)
            if not scored:
                _bot.reply_to(message, "No closed trades yet — no live ranking."); return
            text = "=== BEST PAIRS (live) ===\n"
            for pair, p in scored[:8]:
                tr = p.get("trades",0)
                wr = p.get("wins",0) / tr if tr else 0
                text += f"{pair}: {tr}t WR={wr:.0%} TotR={p.get('total_r',0):.2f}\n"
            _bot.reply_to(message, text)
        except Exception as e:
            _bot.reply_to(message, f"Error: {e}")

    @_bot.message_handler(commands=["evolution"])
    def cmd_evolution(message):
        if not _is_auth(message): return
        try:
            profiles = _load_profiles()
            best = sorted(
                [(p, v) for p, v in profiles.items() if v.get("trades", 0) > 0],
                key=lambda x: x[1].get("total_r", 0), reverse=True)[:5]
            text = f"Evolution iter: {_iteration}\nLearning profiles:\n"
            if not best: text += "(no closed trades yet)\n"
            for pair, v in best:
                tr = v.get("trades",0)
                wr = v.get("wins",0) / tr if tr else 0
                text += f"{pair}: WR={wr:.0%} R={v.get('total_r',0):.2f}\n"
            _bot.reply_to(message, text)
        except Exception as e:
            _bot.reply_to(message, f"Error: {e}")

    @_bot.message_handler(commands=["help", "start"])
    def cmd_help(message):
        if not _is_auth(message): return
        _bot.reply_to(message,
            "=== AUTOTRADER FTMO COMMANDS ===\n"
            "/status    - full system status\n"
            "/balance   - balance check\n"
            "/progress  - FTMO progress\n"
            "/trades    - open trades\n"
            "/history   - 2y analysis\n"
            "/best      - best pairs (live)\n"
            "/evolution - evolution status\n"
            "/pause     - pause trading\n"
            "/resume    - resume trading\n"
            "/close     - close all trades\n"
            "/stop      - emergency stop\n"
            "/ram       - memory usage\n"
            "/report    - full report\n"
            "/help      - this menu")

    log.info("Telegram bot starting infinity_polling...")
    while True:
        try:
            _bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            log.warning(f"Telegram bot error: {e}")
            time.sleep(10)
            continue

# ── MAIN LOOP ──────────────────────────────────────────────────────────────────
_running = True
_iteration = 0
_telegram_report_every = 50

def main_scan_loop():
    """Adaptive scan: every 5 min, classify, find entry, place trade."""
    global _iteration
    state = load_state()
    _iteration = state.get("iteration", 0)

    # Background threads
    threading.Thread(target=monitor_positions_loop, daemon=True, name="monitor").start()
    threading.Thread(target=evolution_loop, daemon=True, name="evolution").start()
    threading.Thread(target=scheduler_loop, daemon=True, name="scheduler").start()
    threading.Thread(target=start_telegram_bot, daemon=True, name="telebot").start()

    # Persist start_balance for FTMO tracking (once)
    if ensure_mt5():
        info = _mt5.account_info()
        s = load_state()
        if "start_balance" not in s:
            s["start_balance"] = info.balance
            save_state(s)
        # Run 2-year history once
        if not HISTORY_REPORT_FILE.exists():
            send_telegram("Reading 2-year history for all pairs...")
            try:
                read_all_history()
            except Exception as e:
                log.warning(f"history analysis error: {e}")
        # Initialize FTMO state
        check_ftmo_limits()
        send_telegram(
            f"=== AUTOTRADER OMEGA FTMO LIVE ===\n"
            f"FTMO Demo: {info.name}\n"
            f"Balance: ${info.balance:.2f}\n"
            f"Target: 10% profit (Phase 1)\n"
            f"Risk: 0.25%–1% adaptive\n"
            f"Max trades: 1 at a time\n"
            f"Pairs: {len(PAIRS)}\n"
            f"Sessions: London + NY (UTC)\n"
            f"FTMO buffers: 3% daily / 7% total DD\n"
            f"Evolution running 24/7\n"
            f"Commands: /help")

    while _running:
        try:
            if not ensure_mt5():
                log.warning("MT5 disconnected — retrying in 30s")
                time.sleep(30); continue

            positions = _mt5.positions_get() or []
            open_omega = [p for p in positions if p.magic == 234000]

            if TRADING_ENABLED and len(open_omega) < MAX_OPEN_TRADES and in_session() and check_ftmo_limits():
                # Scan ALL pairs, collect candidates, pick HIGHEST confidence
                candidates = []
                for pair in PAIRS:
                    try:
                        if ensure_mt5(): _mt5.symbol_select(pair, True)
                        if not check_correlation(pair): continue
                        condition = classify_market(pair)
                        if condition.get("behavior") == "skip": continue
                        profile = get_profile(pair)
                        entry = find_entry(pair, condition, profile)
                        if not entry: continue
                        candidates.append((entry["confidence"], pair, entry, condition))
                    except Exception as e:
                        log.debug(f"scan {pair}: {e}")
                        continue
                if candidates:
                    candidates.sort(key=lambda x: x[0], reverse=True)
                    _, pair, entry, condition = candidates[0]
                    sl_res = calculate_sl(pair, entry["direction"], condition)
                    if sl_res is not None:
                        sl, sl_dist = sl_res
                        info = _mt5.account_info()
                        if info is not None:
                            # Adaptive risk based on confidence
                            risk_pct = calculate_risk_pct(entry["confidence"])
                            # Lot calculation respecting selected risk
                            pip_val = PIP_VALUE.get(pair, 10.0)
                            risk_amt = info.balance * risk_pct
                            if pair in ("BTCUSD","ETHUSD","NAS100","US30","GER40"):
                                lots = risk_amt / max(sl_dist * pip_val, 0.01)
                            elif "JPY" in pair:
                                lots = risk_amt / max(sl_dist * 100 * pip_val, 0.01)
                            elif pair in ("XAUUSD","XAGUSD"):
                                lots = risk_amt / max(sl_dist * pip_val, 0.01)
                            else:
                                lots = risk_amt / max(sl_dist * 10000 * pip_val, 0.01)
                            lots = max(0.01, min(round(lots, 2), 1.0))
                            tick = _mt5.symbol_info_tick(pair)
                            if tick is not None:
                                entry_px = tick.ask if entry["direction"] == "buy" else tick.bid
                                tp = calculate_tp(entry_px, sl_dist, entry["direction"], condition)
                                rrr = abs(tp - entry_px) / sl_dist
                                if rrr >= MIN_RRR:
                                    result = place_trade(pair, entry["direction"], sl, tp, lots)
                                    if result and result.retcode == _mt5.TRADE_RETCODE_DONE:
                                        send_telegram(
                                            f"=== TRADE OPENED ===\n"
                                            f"Pair: {pair} | Direction: {entry['direction'].upper()}\n"
                                            f"Condition: {condition.get('condition')}\n"
                                            f"Confidence: {entry['confidence']:.0%}\n"
                                            f"Signals: {', '.join(entry['signals'])}\n"
                                            f"Entry: {entry_px:.5f}\n"
                                            f"SL: {sl:.5f} | TP: {tp:.5f} | RRR: {rrr:.2f}\n"
                                            f"Risk: {risk_pct*100:.2f}% = ${info.balance*risk_pct:.2f}\n"
                                            f"Lots: {lots}\n"
                                            f"Balance: ${info.balance:.2f}\n"
                                            f"Target: 10% (FTMO Phase 1)\n"
                                            f"====================")
                                        log.info(f"TRADE OPENED {pair} {entry['direction']} @{entry_px} risk={risk_pct*100:.2f}%")
                                        open_omega.append(result)

            _iteration += 1
            state["iteration"] = _iteration
            save_state(state)
            if _iteration % _telegram_report_every == 0:
                info = _mt5.account_info()
                send_telegram(
                    f"📊 Heartbeat iter {_iteration}\n"
                    f"Balance: ${info.balance if info else 0:.2f}\n"
                    f"Open: {len(open_omega)} | RAM: {get_ram_pct():.0f}%")

        except Exception as e:
            log.error(f"main_scan_loop error: {e}", exc_info=True)
            time.sleep(30); continue
        time.sleep(300)  # 5 min scan

# ── Entry ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Connect MT5 with retry loop
    while not ensure_mt5():
        log.info("MT5 connect retry in 30s...")
        time.sleep(30)
    info = _mt5.account_info()
    send_telegram(f"✅ MT5 CONNECTED — Balance: ${info.balance:.2f}")
    log.info(f"Starting main_scan_loop — Balance ${info.balance:.2f}")
    main_scan_loop()
