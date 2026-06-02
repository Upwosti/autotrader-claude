"""
ICT-Structured Synthetic Data Generator.

Generates OHLCV price data with realistic ICT market microstructure:
  - Trend runs (Higher Highs / Lower Lows)
  - Liquidity sweeps (equal highs/lows then reversal)
  - CRT reference candles (large-range consolidation breakers)
  - TBS accumulation (bodies clustering inside CRT range)
  - AMD 3-candle sequences embedded in data
  - Kill zone volatility spikes
  - Fair Value Gaps
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional


def generate_ict_data(
    pair: str = "XAUUSD",
    n_bars: int = 2000,
    timeframe: str = "4H",
    seed: int = 42,
    start_price: Optional[float] = None,
) -> pd.DataFrame:
    """Generate OHLCV data with embedded ICT market structure.

    Structure sequence (repeating cycle):
      Phase 1 (~15%): Accumulation — tight range, equal H/L forming
      Phase 2 (~20%): Manipulation — liquidity sweep beyond range
      Phase 3 (~35%): Distribution — directional move to target
      Phase 4 (~15%): Retracement — partial pullback
      Phase 5 (~15%): Continuation or reversal
    """
    rng = np.random.default_rng(seed)

    # Instrument defaults
    _starts = {"XAUUSD": 2050.0, "BTCUSD": 65000.0, "GBPUSD": 1.27, "EURUSD": 1.09}
    _vols = {"XAUUSD": 0.006, "BTCUSD": 0.020, "GBPUSD": 0.003, "EURUSD": 0.0025}
    price = start_price or _starts.get(pair, 1.0)
    base_vol = _vols.get(pair, 0.004)

    # Global price bounds — hard clamp for all phases
    _start_ref = price
    _g_lo = _start_ref * 0.70   # floor: -30% from start
    _g_hi = _start_ref * 1.40   # ceiling: +40% from start

    # Timeframe bar duration (hours)
    tf_hrs = {
        "M1": 1/60, "5M": 5/60, "15M": 0.25, "30M": 0.5,
        "1H": 1, "H1": 1, "4H": 4, "H4": 4, "D1": 24, "W1": 168, "MN": 720,
    }
    freq = tf_hrs.get(timeframe, 4)

    opens, highs, lows, closes, volumes = [], [], [], [], []

    cycle_len = max(20, n_bars // 8)   # one full market cycle
    i = 0
    direction = 1   # +1 bullish, -1 bearish
    bias_price = price

    while i < n_bars:
        remaining = n_bars - i

        # ── Phase 1: Accumulation (tight range, equal H/L) ─────────────────
        acc_len = min(int(cycle_len * 0.15) + rng.integers(2, 6), remaining)
        acc_range = price * base_vol * 0.5
        acc_high = price + acc_range
        acc_low = price - acc_range

        for _ in range(acc_len):
            o = price + rng.normal(0, acc_range * 0.1)
            c = price + rng.normal(0, acc_range * 0.1)
            h = max(o, c) + rng.uniform(0, acc_range * 0.3)
            l = min(o, c) - rng.uniform(0, acc_range * 0.3)
            # Pinch wicks to equal H/L (liquidity forming)
            h = min(h, acc_high * 1.001)
            l = max(l, acc_low * 0.999)
            opens.append(o); highs.append(h); lows.append(l)
            closes.append(c); volumes.append(rng.integers(200, 800))
        i += acc_len
        if i >= n_bars:
            break

        # ── Phase 2: CRT Reference Candle + Manipulation sweep ─────────────
        # CRT: large candle that sets the reference range
        crt_o = price
        crt_move = price * base_vol * (2.0 + rng.uniform(0, 1.0))
        crt_c = crt_o + crt_move * (-direction)   # closes opposite to trend (CRT)
        crt_h = max(crt_o, crt_c) + rng.uniform(0, crt_move * 0.3)
        crt_l = min(crt_o, crt_c) - rng.uniform(0, crt_move * 0.3)
        crt_range = crt_h - crt_l
        opens.append(crt_o); highs.append(crt_h); lows.append(crt_l)
        closes.append(crt_c); volumes.append(rng.integers(800, 2000))
        price = crt_c
        i += 1
        if i >= n_bars:
            break

        # Accumulation inside CRT range (TBS bodies)
        tbs_len = min(int(cycle_len * 0.08) + rng.integers(2, 5), n_bars - i)
        tbs_center = (crt_h + crt_l) / 2
        for _ in range(tbs_len):
            o = tbs_center + rng.normal(0, crt_range * 0.1)
            c = tbs_center + rng.normal(0, crt_range * 0.1)
            # Bodies inside CRT range
            body_top = max(o, c)
            body_bot = min(o, c)
            if body_top > crt_h:
                shift = body_top - crt_h + 0.01
                o -= shift; c -= shift; body_top -= shift; body_bot -= shift
            if body_bot < crt_l:
                shift = crt_l - body_bot + 0.01
                o += shift; c += shift
            h = body_top + rng.uniform(0, crt_range * 0.15)
            l = body_bot - rng.uniform(0, crt_range * 0.15)
            opens.append(o); highs.append(h); lows.append(l)
            closes.append(c); volumes.append(rng.integers(300, 700))
        price = closes[-1]
        i += tbs_len
        if i >= n_bars:
            break

        # Manipulation: spike BEYOND CRT range (AMD candle 3)
        manip_direction = direction   # spike opposite to upcoming distribution
        if manip_direction == 1:
            # Bullish setup: spike BELOW crt_low (manipulation down → distribution up)
            spike_o = price
            spike_l = crt_l - rng.uniform(crt_range * 0.15, crt_range * 0.5)
            spike_c = crt_l + rng.uniform(0, crt_range * 0.3)  # closes BACK inside
            spike_h = max(spike_o, spike_c) + rng.uniform(0, crt_range * 0.1)
        else:
            # Bearish setup: spike ABOVE crt_high
            spike_o = price
            spike_h = crt_h + rng.uniform(crt_range * 0.15, crt_range * 0.5)
            spike_c = crt_h - rng.uniform(0, crt_range * 0.3)
            spike_l = min(spike_o, spike_c) - rng.uniform(0, crt_range * 0.1)
        opens.append(spike_o); highs.append(spike_h); lows.append(spike_l)
        closes.append(spike_c); volumes.append(rng.integers(1500, 4000))
        price = spike_c
        i += 1
        if i >= n_bars:
            break

        # ── Phase 3: Distribution — directional trending move ───────────────
        dist_len = min(int(cycle_len * 0.35) + rng.integers(3, 10), n_bars - i)
        dist_vol = base_vol * 1.2
        for j in range(dist_len):
            # Trending move with mean reversion to keep price in range
            mean_pull = (_start_ref - price) * 0.03
            drift = direction * price * dist_vol * 0.3 + mean_pull
            o = max(_g_lo, min(_g_hi, price))
            c = o + drift + rng.normal(0, price * dist_vol * 0.15)
            if j % 10 == 3:
                c = o + drift * 1.5
            c = max(_g_lo, min(_g_hi, c))
            h = min(max(o, c) + rng.uniform(0, abs(drift) * 0.3), _g_hi)
            l = max(min(o, c) - rng.uniform(0, abs(drift) * 0.15), _g_lo)
            opens.append(o); highs.append(h); lows.append(l)
            closes.append(c); volumes.append(rng.integers(500, 2000))
            price = c
        i += dist_len
        if i >= n_bars:
            break

        # ── Phase 4: Retracement (partial pullback) ─────────────────────────
        ret_len = min(int(cycle_len * 0.15) + rng.integers(2, 6), n_bars - i)
        for _ in range(ret_len):
            drift = -direction * price * base_vol * 0.4
            o = max(_g_lo, min(_g_hi, price))
            c = o + drift + rng.normal(0, price * base_vol * 0.2)
            c = max(_g_lo, min(_g_hi, c))
            h = min(max(o, c) + rng.uniform(0, abs(drift) * 0.5), _g_hi)
            l = max(min(o, c) - rng.uniform(0, abs(drift) * 0.3), _g_lo)
            opens.append(o); highs.append(h); lows.append(l)
            closes.append(c); volumes.append(rng.integers(200, 600))
            price = c
        i += ret_len
        price = max(_g_lo, min(_g_hi, price))
        bias_price = price

        # Flip bias direction every 2-3 cycles for market structure variety
        if rng.random() < 0.4:
            direction = -direction

    # Trim to exact n_bars
    opens = opens[:n_bars]
    highs = highs[:n_bars]
    lows = lows[:n_bars]
    closes = closes[:n_bars]
    volumes = volumes[:n_bars]

    # Global clamp + OHLC validity: h >= max(o,c), l <= min(o,c), h >= l
    for k in range(len(opens)):
        opens[k]  = max(_g_lo, min(_g_hi, opens[k]))
        closes[k] = max(_g_lo, min(_g_hi, closes[k]))
        h = max(highs[k], opens[k], closes[k])
        l = min(lows[k], opens[k], closes[k])
        highs[k] = min(h, _g_hi)
        lows[k]  = max(l, _g_lo)

    # Build DatetimeIndex
    start_dt = datetime(2022, 1, 1)
    times = [start_dt + timedelta(hours=j * freq) for j in range(len(opens))]

    df = pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": [float(v) for v in volumes],
    }, index=pd.DatetimeIndex(times, name="time"))

    return df
