"""
Phase 5: News + Volatility Filter

Detects:
  - Extreme volatility (ATR spike > 2× normal)
  - Abnormal spread (> 2.5× normal)
  - News spike detection (sudden 3σ bar)
  - Pre-news window (ForexFactory calendar)

Actions:
  - Reduce risk to 50% during elevated volatility
  - Pause new entries during news window
  - Alert Telegram for market-moving events
  - Resume automatically when conditions normalize
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

try:
    import pandas as pd
    import numpy as np
    _PANDAS = True
except ImportError:
    _PANDAS = False

CACHE_FILE   = Path(__file__).parent.parent / "local_db" / "news_vol_state.json"
NEWS_CACHE   = Path(__file__).parent.parent / "local_db" / "news_calendar.json"
NEWS_BLOCK_MINUTES = 10    # block entries within 10 min of high-impact news

ATR_SPIKE_THRESHOLD    = 2.0   # current ATR > 2× average → spike
SPREAD_SPIKE_MULT      = 2.5   # current spread > 2.5× normal → spike
PRICE_MOVE_SIGMA       = 3.0   # bar size > 3σ of recent bars → spike


@dataclass
class VolatilityState:
    pair: str
    is_spike: bool          = False
    atr_ratio: float        = 1.0   # current ATR / normal ATR
    spread_ratio: float     = 1.0
    news_imminent: bool     = False
    news_event: str         = ""
    risk_multiplier: float  = 1.0
    allow_new_entries: bool = True
    reason: str             = ""


class NewsVolatilityFilter:
    """
    Evaluates current market conditions before allowing new entries.

    Usage:
        filt = NewsVolatilityFilter()
        state = filt.evaluate(pair, df, current_spread_pips)
        if state.allow_new_entries:
            proceed()
    """

    def __init__(self):
        self._normal_atr: Dict[str, float] = {}
        self._normal_spread: Dict[str, float] = {}
        self._news_cache: List[dict] = []
        self._last_news_fetch: float = 0.0
        self._load_state()

    def evaluate(
        self,
        pair: str,
        df=None,             # pd.DataFrame OHLCV
        current_spread_pips: float = 0.0,
        utc_now: Optional[datetime] = None,
    ) -> VolatilityState:
        """
        Full evaluation: ATR spike + spread spike + news check.
        """
        if utc_now is None:
            utc_now = datetime.now(timezone.utc)

        state = VolatilityState(pair=pair)
        reasons = []

        # ── ATR spike check ───────────────────────────────────────────────────
        if _PANDAS and df is not None and len(df) >= 20:
            atr_ratio = self._check_atr_spike(pair, df)
            state.atr_ratio = round(atr_ratio, 2)
            if atr_ratio > ATR_SPIKE_THRESHOLD:
                state.is_spike = True
                reasons.append(f"ATR spike {atr_ratio:.1f}×")

        # ── Spread spike check ────────────────────────────────────────────────
        if current_spread_pips > 0:
            spread_ratio = self._check_spread_spike(pair, current_spread_pips)
            state.spread_ratio = round(spread_ratio, 2)
            if spread_ratio > SPREAD_SPIKE_MULT:
                state.is_spike = True
                reasons.append(f"Spread spike {spread_ratio:.1f}×")

        # ── Price move sigma check ────────────────────────────────────────────
        if _PANDAS and df is not None and len(df) >= 10:
            sigma_ratio = self._check_price_sigma(df)
            if sigma_ratio > PRICE_MOVE_SIGMA:
                state.is_spike = True
                reasons.append(f"Bar size {sigma_ratio:.1f}σ")

        # ── News check ────────────────────────────────────────────────────────
        news_min = self._minutes_to_next_news(pair, utc_now)
        if news_min is not None and news_min <= NEWS_BLOCK_MINUTES:
            state.news_imminent = True
            state.news_event    = f"High-impact news in {news_min} min"
            reasons.append(state.news_event)

        # ── Determine action ──────────────────────────────────────────────────
        if state.news_imminent:
            state.allow_new_entries = False
            state.risk_multiplier   = 0.0   # no new trades during news
        elif state.is_spike:
            state.allow_new_entries = False
            state.risk_multiplier   = 0.5   # halve risk during spike
        else:
            state.allow_new_entries = True
            state.risk_multiplier   = 1.0

        state.reason = " | ".join(reasons) if reasons else "ok"
        return state

    def update_normal_atr(self, pair: str, atr_value: float):
        """Feed running normal ATR estimate (call every bar)."""
        existing = self._normal_atr.get(pair, atr_value)
        # EMA of normal ATR
        self._normal_atr[pair] = existing * 0.95 + atr_value * 0.05

    def update_normal_spread(self, pair: str, spread_pips: float):
        """Feed running normal spread estimate."""
        existing = self._normal_spread.get(pair, spread_pips)
        self._normal_spread[pair] = existing * 0.97 + spread_pips * 0.03

    # ── Private helpers ───────────────────────────────────────────────────────

    def _check_atr_spike(self, pair: str, df) -> float:
        import pandas as pd
        import numpy as np
        high = df["high"]
        low  = df["low"]
        cl   = df["close"]
        prev = cl.shift(1)
        tr   = pd.concat([high-low, (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
        atr14    = tr.ewm(span=14, adjust=False).mean()
        current  = float(atr14.iloc[-1])
        normal   = float(atr14.iloc[-20:-1].mean())

        self.update_normal_atr(pair, normal)
        return current / normal if normal > 0 else 1.0

    def _check_spread_spike(self, pair: str, current_pips: float) -> float:
        self.update_normal_spread(pair, current_pips)
        normal = self._normal_spread.get(pair, current_pips)
        return current_pips / normal if normal > 0 else 1.0

    def _check_price_sigma(self, df) -> float:
        import pandas as pd, numpy as np
        bar_sizes = (df["high"] - df["low"]).iloc[-20:]
        if len(bar_sizes) < 5:
            return 0.0
        mean = bar_sizes.mean()
        std  = bar_sizes.std()
        last = float(bar_sizes.iloc[-1])
        return (last - mean) / std if std > 0 else 0.0

    def _minutes_to_next_news(
        self,
        pair: str,
        utc_now: datetime,
    ) -> Optional[int]:
        """
        Returns minutes until next high-impact news event, or None if no event.
        Uses cached ForexFactory-style calendar.
        """
        self._refresh_news_cache()
        if not self._news_cache:
            return None

        # Map pair to currencies
        currencies = _pair_to_currencies(pair)

        for event in self._news_cache:
            impact    = event.get("impact", "").lower()
            currency  = event.get("currency", "")
            event_dt  = event.get("datetime")

            if impact not in ("high", "red"):
                continue
            if currency not in currencies:
                continue
            if not event_dt:
                continue

            try:
                if isinstance(event_dt, str):
                    dt = datetime.fromisoformat(event_dt.replace("Z", "+00:00"))
                else:
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)

                diff_min = (dt - utc_now).total_seconds() / 60
                if 0 <= diff_min <= NEWS_BLOCK_MINUTES:
                    return int(diff_min)
                if -30 <= diff_min < 0:
                    # Within 30 min after news — still elevated volatility
                    return 0
            except Exception:
                continue

        return None

    def _refresh_news_cache(self):
        """Refresh news cache every 60 minutes."""
        now = time.time()
        if now - self._last_news_fetch < 3600:
            return

        # Try to load from cached file first
        if NEWS_CACHE.exists():
            try:
                with open(NEWS_CACHE) as f:
                    self._news_cache = json.load(f)
                self._last_news_fetch = now
                return
            except Exception:
                pass

        # Try ForexFactory API
        try:
            import ssl, urllib.request
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req  = urllib.request.Request(url, headers={"User-Agent": "AutoTrader/5.0"})
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                data = json.loads(resp.read())
            if isinstance(data, list):
                self._news_cache = data
                with open(NEWS_CACHE, "w") as f:
                    json.dump(data, f)
                self._last_news_fetch = now
                logger.debug(f"News calendar refreshed: {len(data)} events")
        except Exception as e:
            logger.debug(f"News calendar fetch failed: {e}")
            self._last_news_fetch = now   # don't retry immediately

    def _load_state(self):
        try:
            if CACHE_FILE.exists():
                with open(CACHE_FILE) as f:
                    d = json.load(f)
                self._normal_atr    = d.get("normal_atr", {})
                self._normal_spread = d.get("normal_spread", {})
        except Exception:
            pass

    def save_state(self):
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump({
                    "normal_atr":    self._normal_atr,
                    "normal_spread": self._normal_spread,
                    "saved":         datetime.utcnow().isoformat(),
                }, f, indent=2)
        except Exception:
            pass


def _pair_to_currencies(pair: str) -> set:
    known = {
        "EURUSD": {"EUR", "USD"}, "GBPUSD": {"GBP", "USD"},
        "USDJPY": {"USD", "JPY"}, "USDCHF": {"USD", "CHF"},
        "AUDUSD": {"AUD", "USD"}, "NZDUSD": {"NZD", "USD"},
        "USDCAD": {"USD", "CAD"}, "EURJPY": {"EUR", "JPY"},
        "GBPJPY": {"GBP", "JPY"}, "XAUUSD": {"XAU", "USD"},
        "GC=F":   {"XAU", "USD"}, "XAGUSD": {"XAG", "USD"},
        "SI=F":   {"XAG", "USD"}, "BTCUSD": {"BTC", "USD"},
        "ETHUSD": {"ETH", "USD"}, "NAS100": {"USD"},
        "US30":   {"USD"}, "GER40": {"EUR"},
    }
    return known.get(pair, {"USD"})
