"""
AutoTrader OMEGA — ABSOLUTE FINAL REBUILD
ONE file. Six bulletproof threads. Zero ICT logic. MT5-only data.
NumPy vectorized indicators. Adaptive risk 0.25–1%. One trade at a time.

FTMO Demo: 1513410114 | FTMO-Demo
Target: 20% profit | Safe buffers: 3% daily / 7% total DD
"""

import os, sys, json, time, traceback, threading, queue, signal, gc, urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import telebot
import psutil

# ── .env loader (no external dep) ─────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
def _load_env():
    p = os.path.join(SCRIPT_DIR, ".env")
    if not os.path.exists(p): return
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
_load_env()

# ── Globals & paths ───────────────────────────────────────────────────────────
DATADIR  = os.path.join(SCRIPT_DIR, "data");  os.makedirs(DATADIR, exist_ok=True)
LOGDIR   = os.path.join(SCRIPT_DIR, "logs");  os.makedirs(LOGDIR, exist_ok=True)
STATE_F  = os.path.join(SCRIPT_DIR, "engine_state.json")
PROF_F   = os.path.join(DATADIR, "pair_profiles.json")
ANAL_F   = os.path.join(DATADIR, "analysis_2y.json")
ERR_F    = os.path.join(LOGDIR, "errors.log")
HEART_F  = os.path.join(SCRIPT_DIR, "heartbeat.txt")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
MT5_LOGIN = int(os.environ.get("MT5_LOGIN", 0) or 0)
MT5_PWD   = os.environ.get("MT5_PASSWORD", "")
MT5_SERVER= os.environ.get("MT5_SERVER", "")

PAIRS_18 = [
    "XAUUSD", "XAGUSD", "XPTUSD",
    "GBPUSD", "EURUSD", "USDJPY",
    "USDCHF", "AUDUSD", "NZDUSD",
    "USDCAD", "EURJPY", "GBPJPY",
    "EURGBP", "AUDJPY", "NZDJPY",
    "BTCUSD", "ETHUSD", "NAS100",
]

LIMITS = {
    "MAX_RISK_PCT":    1.00,
    "MIN_RISK_PCT":    0.25,
    "MAX_DAILY_DD":    3.00,
    "MAX_TOTAL_DD":    7.00,
    "MAX_OPEN_TRADES": 1,
    "MIN_RRR":         2.0,
}

TRADING = True
mt5_connected = False
daily_start: float | None = None
peak_balance: float | None = None

# ── Logging ───────────────────────────────────────────────────────────────────
def log_error(msg: str):
    try:
        with open(ERR_F, "a", encoding="utf-8") as f:
            f.write(f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass

def _stamp(msg: str):
    print(f"{datetime.utcnow().strftime('%H:%M:%S')} {msg}", flush=True)

# ── Telegram (two sender threads: urgent + normal) ────────────────────────────
bot = telebot.TeleBot(BOT_TOKEN, threaded=True) if BOT_TOKEN else None
urgent_q: queue.Queue = queue.Queue()
normal_q: queue.Queue = queue.Queue()

def _post(msg: str, timeout: int):
    if not BOT_TOKEN or not CHAT_ID: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": str(msg)},
            timeout=timeout,
        )
    except Exception as e:
        log_error(f"tg_post: {e}")

def urgent_sender():
    while True:
        try:
            msg = urgent_q.get(timeout=1)
            _post(msg, 5)
        except queue.Empty:
            continue
        except Exception:
            time.sleep(1)

def normal_sender():
    while True:
        try:
            msg = normal_q.get(timeout=2)
            _post(msg, 10)
            time.sleep(1)
        except queue.Empty:
            continue
        except Exception:
            time.sleep(2)

def tg(msg, urgent: bool = False):
    try:
        (urgent_q if urgent else normal_q).put(str(msg))
    except Exception:
        pass

# Start senders immediately
threading.Thread(target=urgent_sender, daemon=True, name="tg_urgent").start()
threading.Thread(target=normal_sender, daemon=True, name="tg_normal").start()

# ── MT5 connection ────────────────────────────────────────────────────────────
import MetaTrader5 as mt5
_mt5_lock = threading.Lock()

def mt5_connect() -> bool:
    global mt5_connected
    with _mt5_lock:
        try:
            try: mt5.shutdown()
            except Exception: pass
            time.sleep(0.5)
            if not mt5.initialize():
                log_error(f"MT5 init: {mt5.last_error()}")
                return False
            if not mt5.login(MT5_LOGIN, password=MT5_PWD, server=MT5_SERVER):
                log_error(f"MT5 login: {mt5.last_error()}")
                return False
            info = mt5.account_info()
            if info is None:
                log_error("account_info None")
                return False
            mt5_connected = True
            return True
        except Exception as e:
            log_error(f"mt5_connect: {e}")
            return False

def mt5_keeper():
    global mt5_connected
    while True:
        try:
            with _mt5_lock:
                info = mt5.account_info()
            if info is None:
                mt5_connected = False
                tg("MT5 disconnected — reconnecting...", urgent=True)
                if mt5_connect():
                    with _mt5_lock:
                        i = mt5.account_info()
                    if i: tg(f"MT5 reconnected ${i.balance:.2f}", urgent=True)
            time.sleep(30)
        except Exception as e:
            log_error(f"mt5_keeper: {e}")
            time.sleep(30)

# ── Data cache ────────────────────────────────────────────────────────────────
TF_MAP = {
    "MN1": mt5.TIMEFRAME_MN1, "W1": mt5.TIMEFRAME_W1, "D1": mt5.TIMEFRAME_D1,
    "H4":  mt5.TIMEFRAME_H4,  "H1": mt5.TIMEFRAME_H1, "M15": mt5.TIMEFRAME_M15,
    "M5":  mt5.TIMEFRAME_M5,  "M1": mt5.TIMEFRAME_M1,
}
TTL = {"MN1": 86400, "W1": 43200, "D1": 3600, "H4": 900,
       "H1": 300, "M15": 120, "M5": 60, "M1": 30}
_cache: dict = {}
_cache_lock = threading.Lock()

def get_data(pair: str, tf: str, bars: int = 500):
    key = f"{pair}_{tf}"
    now = time.time()
    with _cache_lock:
        if key in _cache:
            df, ts = _cache[key]
            if now - ts < TTL.get(tf, 60):
                return df
    try:
        with _mt5_lock:
            mt5.symbol_select(pair, True)
            rates = mt5.copy_rates_from_pos(pair, TF_MAP[tf], 0, bars)
        if rates is None or len(rates) < 20:
            with _cache_lock:
                return _cache.get(key, (None, 0))[0]
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
        df.set_index('time', inplace=True)
        with _cache_lock:
            _cache[key] = (df, now)
        return df
    except Exception as e:
        log_error(f"get_data {pair} {tf}: {e}")
        with _cache_lock:
            return _cache.get(key, (None, 0))[0]

# ── NumPy indicators ──────────────────────────────────────────────────────────
def calc_atr(high, low, close, period=14):
    n = len(close)
    if n < 2: return np.zeros(n)
    pc = np.roll(close, 1)
    tr = np.maximum.reduce([high - low, np.abs(high - pc), np.abs(low - pc)])
    tr[0] = high[0] - low[0]
    alpha = 1.0 / period
    out = np.zeros(n); out[0] = tr[0]
    for i in range(1, n):
        out[i] = alpha * tr[i] + (1 - alpha) * out[i-1]
    return out

def calc_ema(data, period):
    n = len(data)
    if n == 0: return np.zeros(0)
    alpha = 2.0 / (period + 1)
    out = np.zeros(n); out[0] = data[0]
    for i in range(1, n):
        out[i] = alpha * data[i] + (1 - alpha) * out[i-1]
    return out

def calc_rsi(close, period=14):
    n = len(close)
    if n < period + 2: return np.full(n, 50.0)
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_g = np.zeros(n); avg_l = np.zeros(n)
    avg_g[period] = np.mean(gain[1:period+1])
    avg_l[period] = np.mean(loss[1:period+1])
    for i in range(period + 1, n):
        avg_g[i] = (avg_g[i-1] * (period - 1) + gain[i]) / period
        avg_l[i] = (avg_l[i-1] * (period - 1) + loss[i]) / period
    rs = np.where(avg_l == 0, 100.0, avg_g / np.where(avg_l == 0, 1.0, avg_l))
    return 100.0 - (100.0 / (1.0 + rs))

def calc_adx(high, low, close, period=14):
    atr_v = calc_atr(high, low, close, period)
    up = np.diff(high, prepend=high[0])
    dn = -np.diff(low,  prepend=low[0])
    dm_p = np.where((up > dn) & (up > 0), up, 0.0)
    dm_m = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_safe = np.where(atr_v == 0, 1.0, atr_v)
    di_p = 100.0 * calc_ema(dm_p, period) / atr_safe
    di_m = 100.0 * calc_ema(dm_m, period) / atr_safe
    sumd = np.where(di_p + di_m == 0, 1.0, di_p + di_m)
    dx = 100.0 * np.abs(di_p - di_m) / sumd
    return calc_ema(dx, period)

# ── 2-year analyzer ───────────────────────────────────────────────────────────
def run_backtest_vectorized(pair: str, df: pd.DataFrame) -> dict:
    close = df['close'].values; high = df['high'].values; low = df['low'].values
    if len(close) < 250:
        return {"win_rate":0,"avg_rrr":0,"expectancy":0,"total_trades":0,"rank_score":0}
    atr_v = calc_atr(high, low, close, 14)
    ema20 = calc_ema(close, 20)
    ema50 = calc_ema(close, 50)
    ema200 = calc_ema(close, 200)
    adx_v = calc_adx(high, low, close, 14)
    rsi_v = calc_rsi(close, 14)
    trades = []
    for i in range(200, len(close) - 50):
        if close[i] > ema20[i] > ema50[i] > ema200[i]: bias = 1
        elif close[i] < ema20[i] < ema50[i] < ema200[i]: bias = -1
        else: continue
        if adx_v[i] < 22: continue
        if bias == 1 and rsi_v[i] > 72: continue
        if bias == -1 and rsi_v[i] < 28: continue
        entry = close[i]; atr_now = atr_v[i]
        if atr_now <= 0: continue
        if bias == 1: sl = np.min(low[i-15:i]) - atr_now * 0.3
        else:         sl = np.max(high[i-15:i]) + atr_now * 0.3
        sl_dist = abs(entry - sl)
        if sl_dist < atr_now * 0.5 or sl_dist > atr_now * 4: continue
        tp = entry + sl_dist * 3 if bias == 1 else entry - sl_dist * 3
        result = None
        for j in range(i + 1, min(i + 51, len(close))):
            if bias == 1:
                if low[j] <= sl: result = {"win": False}; break
                if high[j] >= tp: result = {"win": True}; break
            else:
                if high[j] >= sl: result = {"win": False}; break
                if low[j] <= tp: result = {"win": True}; break
        if result is not None: trades.append(result)
    if not trades:
        return {"win_rate":0,"avg_rrr":0,"expectancy":0,"total_trades":0,"rank_score":0}
    wins = sum(1 for t in trades if t["win"])
    total = len(trades); wr = wins / total
    avg_rrr = 3.0 if wins > 0 else 0
    expectancy = (wr * avg_rrr) - ((1 - wr) * 1.0)
    rank_score = expectancy * np.sqrt(min(total, 100))
    return {"win_rate": round(wr * 100, 1), "avg_rrr": round(avg_rrr, 2),
            "expectancy": round(expectancy, 4), "total_trades": total,
            "rank_score": round(float(rank_score), 3)}

def analyze_2yr_all_pairs():
    if os.path.exists(ANAL_F):
        try:
            with open(ANAL_F) as f: data = json.load(f)
            send_analysis_report(data)
            return data
        except Exception: pass
    tg("Starting 2-year analysis on 18 pairs...", urgent=True)
    results = {}
    for i, pair in enumerate(PAIRS_18):
        try:
            tg(f"Analyzing {i+1}/18: {pair}")
            df = get_data(pair, "H4", 3500)
            if df is None or len(df) < 500:
                log_error(f"analyze {pair}: insufficient data ({0 if df is None else len(df)})")
                continue
            stats = run_backtest_vectorized(pair, df)
            results[pair] = stats
            gc.collect()
        except Exception as e:
            log_error(f"analyze {pair}: {e}")
    with open(ANAL_F, "w") as f: json.dump(results, f, indent=2)
    send_analysis_report(results)
    return results

def send_analysis_report(results: dict):
    if not results:
        tg("Analysis empty — retry needed", urgent=True); return
    ranked = sorted(results.items(), key=lambda x: x[1].get("rank_score", -999), reverse=True)
    # Telegram has 4096-char limit. Split into two messages.
    msg = "=== 2-YEAR ANALYSIS (18 pairs) ===\n\n"
    for i, (pair, s) in enumerate(ranked[:9]):
        emoji = "[G]" if s["expectancy"] > 0.5 else "[Y]" if s["expectancy"] > 0 else "[R]"
        msg += (f"{emoji} #{i+1} {pair}: WR={s['win_rate']:.1f}% "
                f"RRR={s['avg_rrr']:.1f} Exp={s['expectancy']:.3f} "
                f"T={s['total_trades']}\n")
    tg(msg, urgent=True)
    msg2 = ""
    for i, (pair, s) in enumerate(ranked[9:]):
        emoji = "[G]" if s["expectancy"] > 0.5 else "[Y]" if s["expectancy"] > 0 else "[R]"
        msg2 += (f"{emoji} #{i+10} {pair}: WR={s['win_rate']:.1f}% "
                 f"RRR={s['avg_rrr']:.1f} Exp={s['expectancy']:.3f} "
                 f"T={s['total_trades']}\n")
    msg2 += "\nTOP 5 FOR TRADING:\n"
    for pair, _ in ranked[:5]:
        msg2 += f"  -> {pair}\n"
    tg(msg2, urgent=True)

# ── Market condition ─────────────────────────────────────────────────────────
def get_condition(pair: str):
    try:
        d1 = get_data(pair, "D1", 200); h4 = get_data(pair, "H4", 200); h1 = get_data(pair, "H1", 100)
        if any(x is None for x in [d1, h4, h1]): return None
        c_d1 = d1['close'].values
        ema20_d1 = calc_ema(c_d1, 20)[-1]
        ema50_d1 = calc_ema(c_d1, 50)[-1]
        ema200_d1 = calc_ema(c_d1, 200)[-1]
        if c_d1[-1] > ema20_d1 > ema50_d1 > ema200_d1: bias = "buy"
        elif c_d1[-1] < ema20_d1 < ema50_d1 < ema200_d1: bias = "sell"
        else: return None
        c_h4 = h4['close'].values; h_h4 = h4['high'].values; l_h4 = h4['low'].values
        ema20_h4 = calc_ema(c_h4, 20)[-1]
        if bias == "buy" and c_h4[-1] < ema20_h4: return None
        if bias == "sell" and c_h4[-1] > ema20_h4: return None
        adx_h4 = calc_adx(h_h4, l_h4, c_h4, 14)[-1]
        atr_h4 = calc_atr(h_h4, l_h4, c_h4, 14)
        atr_now = atr_h4[-1]; atr_avg = float(np.mean(atr_h4[-20:])) if len(atr_h4) >= 20 else atr_now
        if adx_h4 < 22: return None
        # Accumulation skip
        recent_range = float(np.max(h_h4[-20:]) - np.min(l_h4[-20:]))
        if recent_range < atr_now * 3: return None
        if adx_h4 > 40:
            return {"regime": "STRONG_TREND", "bias": bias, "confidence": 0.90, "sl_mult": 0.4, "tp_mult": 6.0}
        if adx_h4 > 28 and atr_now > atr_avg * 0.9:
            return {"regime": "TRENDING", "bias": bias, "confidence": 0.78, "sl_mult": 0.5, "tp_mult": 4.0}
        if atr_now > atr_avg * 1.4:
            return {"regime": "EXPANSION", "bias": bias, "confidence": 0.75, "sl_mult": 0.6, "tp_mult": 4.5}
        return None
    except Exception as e:
        log_error(f"condition {pair}: {e}"); return None

# ── Universal entry ───────────────────────────────────────────────────────────
def find_signal(pair: str, cond: dict):
    try:
        h4 = get_data(pair, "H4", 200); h1 = get_data(pair, "H1", 100); m5 = get_data(pair, "M5", 100)
        if any(x is None for x in [h4, h1, m5]): return None
        bias = cond["bias"]; score = 0; sigs = []
        c_h4 = h4['close'].values; c_h1 = h1['close'].values; c_m5 = m5['close'].values
        rsi_h4 = calc_rsi(c_h4)[-1]; rsi_h1 = calc_rsi(c_h1)[-1]
        # 1 RSI zone
        if bias == "buy" and 45 < rsi_h4 < 68 and rsi_h1 > 50:
            score += 25; sigs.append("rsi")
        elif bias == "sell" and 32 < rsi_h4 < 55 and rsi_h1 < 50:
            score += 25; sigs.append("rsi")
        # 2 Pullback to BB-midband
        if len(c_h1) >= 20:
            mid = float(np.mean(c_h1[-20:])); std = float(np.std(c_h1[-20:]))
            if bias == "buy" and (mid - std) < c_h1[-1] < mid:
                score += 20; sigs.append("pullback")
            elif bias == "sell" and mid < c_h1[-1] < (mid + std):
                score += 20; sigs.append("pullback")
        # 3 Volume spike
        if 'tick_volume' in m5.columns:
            vol = m5['tick_volume'].values
            if len(vol) >= 20 and vol[-1] > float(np.mean(vol[-20:])) * 1.3:
                score += 15; sigs.append("volume")
        # 4 Candle confirmation
        opens = m5['open'].values
        if bias == "buy":
            if sum(1 for i in range(-3, 0) if c_m5[i] > opens[i]) >= 2:
                score += 15; sigs.append("candles")
        else:
            if sum(1 for i in range(-3, 0) if c_m5[i] < opens[i]) >= 2:
                score += 15; sigs.append("candles")
        # 5 Volatility expanding
        atr_h1 = calc_atr(h1['high'].values, h1['low'].values, c_h1)
        if len(atr_h1) >= 20 and atr_h1[-1] > float(np.mean(atr_h1[-20:])) * 1.1:
            score += 15; sigs.append("expanding")
        # 6 Live learning bonus
        profiles = load_profiles()
        if profiles.get(pair, {}).get(f"wr_{cond['regime']}", 0) > 0.60:
            score += 20; sigs.append("history")
        if score < 50: return None
        return {"pair": pair, "direction": bias, "confidence": round((score / 100) * cond["confidence"], 3),
                "score": score, "signals": sigs}
    except Exception as e:
        log_error(f"signal {pair}: {e}"); return None

# ── SL calc ───────────────────────────────────────────────────────────────────
def calc_sl(pair: str, direction: str, cond: dict):
    try:
        h1 = get_data(pair, "H1", 100)
        if h1 is None: return None, None
        with _mt5_lock:
            tick = mt5.symbol_info_tick(pair)
            info = mt5.symbol_info(pair)
        if tick is None or info is None: return None, None
        atr_h1 = calc_atr(h1['high'].values, h1['low'].values, h1['close'].values)[-1]
        entry = tick.ask if direction == "buy" else tick.bid
        sl_mult = cond.get("sl_mult", 0.5)
        if direction == "buy":
            sl = float(np.min(h1['low'].values[-15:])) - atr_h1 * sl_mult
        else:
            sl = float(np.max(h1['high'].values[-15:])) + atr_h1 * sl_mult
        sl_dist = abs(entry - sl)
        min_sl = atr_h1 * 0.4; max_sl = atr_h1 * 4.0
        if sl_dist < min_sl:
            sl_dist = min_sl
            sl = entry - sl_dist if direction == "buy" else entry + sl_dist
        if sl_dist > max_sl: return None, None
        stops_pts = info.trade_stops_level * info.point
        if sl_dist < stops_pts: return None, None
        sl = round(sl, info.digits)
        return sl, sl_dist
    except Exception as e:
        log_error(f"calc_sl {pair}: {e}"); return None, None

# ── Risk + execution ──────────────────────────────────────────────────────────
def check_limits() -> bool:
    global daily_start, peak_balance
    try:
        with _mt5_lock: info = mt5.account_info()
        if info is None: return False
        if daily_start is None: daily_start = info.balance
        if peak_balance is None: peak_balance = info.balance
        peak_balance = max(peak_balance, info.balance)
        daily_loss = (daily_start - info.equity) / daily_start * 100
        if daily_loss >= LIMITS["MAX_DAILY_DD"]:
            tg(f"DAILY LIMIT: {daily_loss:.1f}% — STOPPED", urgent=True); return False
        total_dd = (peak_balance - info.equity) / peak_balance * 100
        if total_dd >= LIMITS["MAX_TOTAL_DD"]:
            tg(f"DD LIMIT: {total_dd:.1f}% — STOPPED", urgent=True); return False
        return True
    except Exception as e:
        log_error(f"check_limits: {e}"); return False

def _risk_pct_for(conf: float) -> float:
    if conf >= 0.85: return LIMITS["MAX_RISK_PCT"]      # 1.00
    if conf >= 0.75: return 0.75
    if conf >= 0.65: return 0.50
    return LIMITS["MIN_RISK_PCT"]                        # 0.25

def place_order(pair: str, direction: str, sl: float, confidence: float):
    try:
        with _mt5_lock:
            mt5.symbol_select(pair, True)
            tick = mt5.symbol_info_tick(pair)
            info = mt5.symbol_info(pair)
            acc  = mt5.account_info()
        if any(x is None for x in [tick, info, acc]): return None
        risk_pct = _risk_pct_for(confidence)
        risk_amt = acc.balance * (risk_pct / 100)
        entry = tick.ask if direction == "buy" else tick.bid
        sl_dist = abs(entry - sl)
        if sl_dist <= 0: return None
        ticks_in_sl = sl_dist / info.point if info.point > 0 else 0
        if ticks_in_sl <= 0 or info.trade_tick_value <= 0:
            return None
        lots = risk_amt / (ticks_in_sl * info.trade_tick_value)
        step = info.volume_step or 0.01
        lots = max(info.volume_min, min(round(lots / step) * step, 1.0))
        lots = round(lots, 2)
        if direction == "buy":
            tp = round(entry + sl_dist * 3, info.digits)
            order_type = mt5.ORDER_TYPE_BUY; price = tick.ask
        else:
            tp = round(entry - sl_dist * 3, info.digits)
            order_type = mt5.ORDER_TYPE_SELL; price = tick.bid
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": pair, "volume": float(lots),
            "type": order_type, "price": price, "sl": float(sl), "tp": float(tp),
            "deviation": 30, "magic": 999999, "comment": "OmegaFinal",
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
        }
        with _mt5_lock: result = mt5.order_send(req)
        if result and result.retcode != mt5.TRADE_RETCODE_DONE:
            req["type_filling"] = mt5.ORDER_FILLING_FOK
            with _mt5_lock: result = mt5.order_send(req)
        if result and result.retcode != mt5.TRADE_RETCODE_DONE:
            req["type_filling"] = mt5.ORDER_FILLING_RETURN
            with _mt5_lock: result = mt5.order_send(req)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            tg(f"=== TRADE OPENED ===\n"
               f"Pair: {pair}\n"
               f"Direction: {direction.upper()}\n"
               f"Entry: {price:.{info.digits}f}\n"
               f"SL: {sl:.{info.digits}f}\n"
               f"TP: {tp:.{info.digits}f}\n"
               f"RRR: 3.0\n"
               f"Lots: {lots} | Risk: ${risk_amt:.2f} ({risk_pct}%)\n"
               f"Balance: ${acc.balance:.2f}\n"
               f"Ticket: {result.order}\n"
               f"===================", urgent=True)
            return result
        if result:
            log_error(f"order failed {pair}: {result.retcode} {result.comment}")
        return None
    except Exception as e:
        log_error(f"place_order {pair}: {e}"); return None

# ── Position monitor ──────────────────────────────────────────────────────────
position_state: dict = {}
_pos_lock = threading.Lock()
_closed_seen: set = set()

def _modify_sl(pos, new_sl: float):
    try:
        with _mt5_lock:
            mt5.order_send({
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": pos.symbol, "position": pos.ticket,
                "sl": float(new_sl), "tp": pos.tp,
            })
    except Exception as e:
        log_error(f"modify_sl: {e}")

def manage(pos):
    with _mt5_lock:
        tick = mt5.symbol_info_tick(pos.symbol)
        info = mt5.symbol_info(pos.symbol)
    if tick is None or info is None: return
    sl_dist = abs(pos.price_open - pos.sl)
    if sl_dist == 0: return
    curr = tick.bid if pos.type == 0 else tick.ask
    profit_r = (curr - pos.price_open) / sl_dist if pos.type == 0 else (pos.price_open - curr) / sl_dist
    with _pos_lock:
        state = position_state.get(pos.ticket, {})
    # BE @ 1R
    if profit_r >= 1.0 and not state.get("be"):
        _modify_sl(pos, round(pos.price_open, info.digits))
        state["be"] = True
        with _pos_lock: position_state[pos.ticket] = state
        tg(f"BREAKEVEN: {pos.symbol}")
    # Trail at 2R
    if profit_r >= 2.0:
        h1 = get_data(pos.symbol, "H1", 60)
        if h1 is not None:
            atr_h1 = calc_atr(h1['high'].values, h1['low'].values, h1['close'].values)[-1]
            if pos.type == 0:
                new_sl = round(curr - atr_h1 * 1.5, info.digits)
                if new_sl > pos.sl: _modify_sl(pos, new_sl)
            else:
                new_sl = round(curr + atr_h1 * 1.5, info.digits)
                if new_sl < pos.sl: _modify_sl(pos, new_sl)

def check_closed():
    try:
        from_time = int(time.time()) - 3600
        with _mt5_lock: deals = mt5.history_deals_get(from_time, int(time.time()))
        if not deals: return
        for d in deals:
            if d.entry == 1 and d.ticket not in _closed_seen and d.magic == 999999:
                _closed_seen.add(d.ticket)
                profit = d.profit + d.swap + d.commission
                with _mt5_lock: acc = mt5.account_info()
                start = load_state().get("start_balance", acc.balance if acc else 10000)
                bal = acc.balance if acc else 0
                prog = (bal - start) / start * 100 if start else 0
                win = profit > 0
                tg(f"=== TRADE CLOSED ===\n"
                   f"Pair: {d.symbol}\n"
                   f"Result: {'WIN' if win else 'LOSS'}\n"
                   f"P&L: ${profit:+.2f}\n"
                   f"Balance: ${bal:.2f}\n"
                   f"Progress: {prog:+.2f}% / 20%\n"
                   f"===================", urgent=True)
                learn_from_trade(d, win)
    except Exception as e:
        log_error(f"check_closed: {e}")

def learn_from_trade(deal, won: bool):
    try:
        profiles = load_profiles()
        p = profiles.get(deal.symbol, {})
        p["total"] = p.get("total", 0) + 1
        p["wins"] = p.get("wins", 0) + (1 if won else 0)
        p["wr"] = p["wins"] / p["total"]
        profiles[deal.symbol] = p
        save_profiles(profiles)
    except Exception:
        pass

def monitor():
    while True:
        try:
            with _mt5_lock: positions = mt5.positions_get() or []
            for pos in positions:
                if pos.magic != 999999: continue
                try: manage(pos)
                except Exception: continue
            check_closed()
            time.sleep(10)
        except Exception as e:
            log_error(f"monitor: {e}"); time.sleep(20)

# ── Scanner ───────────────────────────────────────────────────────────────────
def scanner():
    while True:
        try:
            if not mt5_connected or not TRADING:
                time.sleep(30); continue
            if not check_limits(): time.sleep(3600); continue
            with _mt5_lock: positions = mt5.positions_get() or []
            if len(positions) >= LIMITS["MAX_OPEN_TRADES"]: time.sleep(60); continue
            analysis = load_analysis()
            # Pick top-10 by rank_score, else default to PAIRS_18
            if analysis:
                ranked = sorted(analysis.items(), key=lambda x: x[1].get("rank_score", 0), reverse=True)
                candidates = [p for p, _ in ranked[:10]]
            else:
                candidates = PAIRS_18[:10]
            best = None; best_conf = 0
            for pair in candidates:
                try:
                    cond = get_condition(pair)
                    if cond is None: continue
                    sig = find_signal(pair, cond)
                    if sig is None: continue
                    if sig["confidence"] > best_conf:
                        best_conf = sig["confidence"]; best = (pair, cond, sig)
                except Exception: continue
            if best:
                pair, cond, sig = best
                sl, _ = calc_sl(pair, sig["direction"], cond)
                if sl is not None:
                    place_order(pair, sig["direction"], sl, sig["confidence"])
            time.sleep(180)
        except Exception as e:
            log_error(f"scanner: {e}"); time.sleep(60)

# ── Evolution ────────────────────────────────────────────────────────────────
def evolution():
    iteration = load_state().get("iteration", 0)
    while True:
        try:
            for pair in PAIRS_18:
                try:
                    df = get_data(pair, "H4", 1000)
                    if df is None or len(df) < 200: continue
                    stats = run_backtest_vectorized(pair, df)
                    profiles = load_profiles()
                    p = profiles.get(pair, {})
                    if stats["expectancy"] > p.get("expectancy", -999):
                        p["expectancy"] = stats["expectancy"]
                        p["win_rate"] = stats["win_rate"]
                        p["rrr"] = stats["avg_rrr"]
                        p["last_update"] = time.time()
                        profiles[pair] = p
                        save_profiles(profiles)
                    gc.collect()
                except Exception: continue
            iteration += 1
            state = load_state(); state["iteration"] = iteration; save_state(state)
            if iteration % 20 == 0: send_evolution_report(iteration)
            time.sleep(300)
        except Exception as e:
            log_error(f"evolution: {e}"); time.sleep(60)

def send_evolution_report(iteration: int):
    profiles = load_profiles()
    if not profiles: return
    ranked = sorted(profiles.items(), key=lambda x: x[1].get("expectancy", -999), reverse=True)[:8]
    msg = f"=== EVOLUTION #{iteration} ===\n\n"
    for i, (pair, p) in enumerate(ranked):
        msg += f"#{i+1} {pair}: WR={p.get('win_rate',0):.1f}% Exp={p.get('expectancy',0):.3f}\n"
    msg += "\n========================"
    tg(msg)

# ── Telegram commands ────────────────────────────────────────────────────────
if bot:
    def _auth(m): return str(m.chat.id) == str(CHAT_ID)

    @bot.message_handler(commands=["help", "start"])
    def cmd_help(m):
        if not _auth(m): return
        bot.reply_to(m,
            "=== OMEGA COMMANDS ===\n"
            "/status /balance /trades\n"
            "/progress /analysis /best\n"
            "/evolution /ftmo\n"
            "/pause /resume /close /stop\n"
            "/ram /help")

    @bot.message_handler(commands=["status"])
    def cmd_status(m):
        if not _auth(m): return
        try:
            with _mt5_lock: info = mt5.account_info()
            state = load_state()
            start = state.get("start_balance", info.balance if info else 10000)
            bal = info.balance if info else 0
            prog = (bal - start) / start * 100 if start else 0
            with _mt5_lock: pos = mt5.positions_get() or []
            ram = psutil.virtual_memory().percent
            bot.reply_to(m,
                f"Balance: ${bal:.2f}\n"
                f"Progress: {prog:+.2f}% / 20%\n"
                f"Open trades: {len(pos)}\n"
                f"Trading: {'ON' if TRADING else 'OFF'}\n"
                f"MT5: {'OK' if mt5_connected else 'DOWN'}\n"
                f"RAM: {ram:.0f}%\n"
                f"Iter: {state.get('iteration', 0)}")
        except Exception as e: bot.reply_to(m, f"Error: {e}")

    @bot.message_handler(commands=["balance"])
    def cmd_balance(m):
        if not _auth(m): return
        try:
            with _mt5_lock: info = mt5.account_info()
            state = load_state()
            start = state.get("start_balance", info.balance)
            prog = (info.balance - start) / start * 100
            bot.reply_to(m, f"Balance: ${info.balance:.2f}\nEquity: ${info.equity:.2f}\n"
                            f"P&L: {prog:+.2f}%\nTarget: 20%\nRemaining: {max(0,20-prog):.2f}%")
        except Exception as e: bot.reply_to(m, f"Error: {e}")

    @bot.message_handler(commands=["progress"])
    def cmd_progress(m):
        if not _auth(m): return
        cmd_balance(m)

    @bot.message_handler(commands=["ftmo"])
    def cmd_ftmo(m):
        if not _auth(m): return
        try:
            with _mt5_lock: info = mt5.account_info()
            ds = daily_start or info.balance
            pk = peak_balance or info.balance
            dl = max(0, (ds - info.equity) / ds * 100)
            td = max(0, (pk - info.equity) / pk * 100)
            bot.reply_to(m,
                f"=== FTMO STATUS ===\n"
                f"Daily loss: {dl:.2f}% / 5% FTMO\n"
                f"Total DD:   {td:.2f}% / 10% FTMO\n"
                f"Safe daily remaining: {max(0,3-dl):.2f}%\n"
                f"Safe DD remaining:    {max(0,7-td):.2f}%\n"
                f"Status: {'SAFE' if (dl<3 and td<7) else 'WARNING'}")
        except Exception as e: bot.reply_to(m, f"Error: {e}")

    @bot.message_handler(commands=["trades"])
    def cmd_trades(m):
        if not _auth(m): return
        try:
            with _mt5_lock: pos = mt5.positions_get() or []
            if not pos: bot.reply_to(m, "No open trades."); return
            text = "=== OPEN TRADES ===\n"
            for p in pos:
                text += f"{p.symbol} {'BUY' if p.type==0 else 'SELL'}\nEntry: {p.price_open}\nSL: {p.sl} TP: {p.tp}\nPnL: ${p.profit:.2f}\n\n"
            bot.reply_to(m, text)
        except Exception as e: bot.reply_to(m, f"Error: {e}")

    @bot.message_handler(commands=["analysis"])
    def cmd_analysis(m):
        if not _auth(m): return
        try:
            with open(ANAL_F) as f: data = json.load(f)
            send_analysis_report(data)
            bot.reply_to(m, "Analysis re-sent.")
        except Exception: bot.reply_to(m, "Analysis not ready yet.")

    @bot.message_handler(commands=["best"])
    def cmd_best(m):
        if not _auth(m): return
        profiles = load_profiles()
        if not profiles: bot.reply_to(m, "No data yet."); return
        ranked = sorted(profiles.items(), key=lambda x: x[1].get("expectancy", -999), reverse=True)[:5]
        text = "TOP 5 PAIRS:\n"
        for p, d in ranked:
            text += f"{p}: WR={d.get('win_rate',0):.1f}% Exp={d.get('expectancy',0):.3f}\n"
        bot.reply_to(m, text)

    @bot.message_handler(commands=["evolution"])
    def cmd_evolution(m):
        if not _auth(m): return
        send_evolution_report(load_state().get("iteration", 0))
        bot.reply_to(m, "Evolution report sent.")

    @bot.message_handler(commands=["pause"])
    def cmd_pause(m):
        if not _auth(m): return
        global TRADING; TRADING = False
        bot.reply_to(m, "PAUSED. /resume to restart.")

    @bot.message_handler(commands=["resume"])
    def cmd_resume(m):
        if not _auth(m): return
        global TRADING; TRADING = True
        bot.reply_to(m, "RESUMED.")

    @bot.message_handler(commands=["close"])
    def cmd_close(m):
        if not _auth(m): return
        try:
            with _mt5_lock: pos = mt5.positions_get() or []
            n = 0
            for p in pos:
                with _mt5_lock:
                    tick = mt5.symbol_info_tick(p.symbol)
                    res = mt5.order_send({
                        "action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol,
                        "volume": p.volume,
                        "type": mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY,
                        "position": p.ticket,
                        "price": tick.bid if p.type == 0 else tick.ask,
                        "deviation": 30, "magic": 999999,
                        "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
                    })
                if res and res.retcode == mt5.TRADE_RETCODE_DONE: n += 1
            bot.reply_to(m, f"Closed {n}/{len(pos)}.")
        except Exception as e: bot.reply_to(m, f"Error: {e}")

    @bot.message_handler(commands=["stop"])
    def cmd_stop(m):
        if not _auth(m): return
        bot.reply_to(m, "EMERGENCY STOP.")
        try:
            with _mt5_lock: pos = mt5.positions_get() or []
            for p in pos:
                with _mt5_lock:
                    tick = mt5.symbol_info_tick(p.symbol)
                    mt5.order_send({
                        "action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume,
                        "type": mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY,
                        "position": p.ticket,
                        "price": tick.bid if p.type == 0 else tick.ask,
                        "deviation": 50, "magic": 999999,
                        "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
                    })
        except Exception: pass
        time.sleep(2); os._exit(0)

    @bot.message_handler(commands=["ram"])
    def cmd_ram(m):
        if not _auth(m): return
        ram = psutil.virtual_memory()
        bot.reply_to(m, f"RAM: {ram.percent:.0f}%\nUsed: {ram.used/1e9:.1f}GB\nFree: {ram.available/1e9:.1f}GB")

def bot_loop():
    if not bot: return
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            log_error(f"bot polling: {e}"); time.sleep(10)

# ── Helpers ──────────────────────────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_F) as f: return json.load(f)
    except Exception: return {}

def save_state(s):
    try:
        with open(STATE_F, "w") as f: json.dump(s, f, indent=2)
    except Exception: pass

def load_profiles():
    try:
        with open(PROF_F) as f: return json.load(f)
    except Exception: return {}

def save_profiles(p):
    try:
        with open(PROF_F, "w") as f: json.dump(p, f, indent=2)
    except Exception: pass

def load_analysis():
    try:
        with open(ANAL_F) as f: return json.load(f)
    except Exception: return {}

# ── Heartbeat ────────────────────────────────────────────────────────────────
def heartbeat():
    while True:
        try:
            with open(HEART_F, "w") as f: f.write(str(time.time()))
            time.sleep(30)
        except Exception:
            time.sleep(30)

# ── Bulletproof wrapper ─────────────────────────────────────────────────────
def run_safe(func, name):
    while True:
        try:
            func()
        except SystemExit:
            time.sleep(5)
        except Exception as e:
            tg(f"{name} crashed — restarting in 10s\n{str(e)[:200]}")
            log_error(f"{name}: {traceback.format_exc()}")
            time.sleep(10)

# ── Startup ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda s, f: None)
    try: signal.signal(signal.SIGHUP, lambda s, f: None)
    except AttributeError: pass

    _stamp("OMEGA starting...")
    while not mt5_connect():
        _stamp("MT5 connect retry in 30s...")
        time.sleep(30)

    state = load_state()
    if "start_balance" not in state:
        with _mt5_lock: info = mt5.account_info()
        state["start_balance"] = info.balance
        daily_start = info.balance
        peak_balance = info.balance
        save_state(state)

    # 2yr analysis (background)
    threading.Thread(
        target=lambda: run_safe(analyze_2yr_all_pairs, "ANALYSIS"),
        daemon=True, name="ANALYSIS").start()

    # 6 bulletproof threads
    for func, name in [
        (mt5_keeper, "MT5_KEEPER"),
        (scanner,    "SCANNER"),
        (monitor,    "MONITOR"),
        (evolution,  "EVOLUTION"),
        (bot_loop,   "TELEGRAM"),
        (heartbeat,  "HEARTBEAT"),
    ]:
        threading.Thread(target=lambda f=func, n=name: run_safe(f, n),
                         daemon=True, name=name).start()

    time.sleep(3)
    with _mt5_lock: info = mt5.account_info()
    tg(f"=== OMEGA REBUILD LIVE ===\n"
       f"Balance: ${info.balance:.2f}\n"
       f"Target: 20% profit\n"
       f"18 pairs ranked\n"
       f"1 trade max\n"
       f"0.25-1% adaptive risk\n"
       f"3 min scan\n"
       f"Evolution: every 5 min\n"
       f"All 6 threads bulletproof\n"
       f"========================", urgent=True)
    _stamp("All threads started. Engine alive.")

    # Keep main thread alive
    while True:
        try:
            time.sleep(60)
        except KeyboardInterrupt:
            time.sleep(5)
