"""
Market Adaptation Engine — classifies current market regime and selects
the optimal ICT entry plan(s) for current conditions.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Optional, Tuple
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'autotrader_claude'))
from config import StrategyParams


@dataclass
class MarketRegime:
    regime: str
    volatility: str
    session: str
    trend_strength: float
    atr: float
    adx: float
    recommended_plans: List[str]
    confidence_modifier: float
    description: str


class MarketAdaptationEngine:

    def __init__(self, params: StrategyParams) -> None:
        self.params = params

    def classify_volatility(self, df: pd.DataFrame) -> str:
        if len(df) < 2:
            return "normal"
        closes = df["close"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        period = min(self.params.atr_period, len(df) - 1)
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:] - closes[:-1]),
            ),
        )
        atr = float(np.mean(tr[-period:])) if len(tr) >= period else float(np.mean(tr))
        mid_price = float(np.mean(closes[-period:]))
        if mid_price == 0.0:
            return "normal"
        ratio = atr / mid_price
        if ratio > 0.015:
            return "high"
        if ratio < 0.005:
            return "low"
        return "normal"

    def _compute_atr(self, df: pd.DataFrame) -> float:
        if len(df) < 2:
            return 0.0
        closes = df["close"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        period = min(self.params.atr_period, len(df) - 1)
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:] - closes[:-1]),
            ),
        )
        if len(tr) == 0:
            return 0.0
        return float(np.mean(tr[-period:])) if len(tr) >= period else float(np.mean(tr))

    def _compute_adx_proxy(self, df: pd.DataFrame, period: int = 14) -> float:
        closes = df["close"].values.astype(float)
        n = min(period, len(closes))
        if n < 2:
            return 0.0
        window = closes[-n:]
        mean_close = np.mean(window)
        if mean_close == 0.0:
            return 0.0
        return float(np.std(window) / mean_close)

    def classify_regime(self, df: pd.DataFrame) -> str:
        if len(df) < 5:
            return "ranging"

        closes = df["close"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)

        atr = self._compute_atr(df)
        adx_proxy = self._compute_adx_proxy(df, period=14)

        # Expanding: last 5-bar price move > 2× ATR
        if len(closes) >= 5 and atr > 0:
            last5_range = float(np.max(highs[-5:]) - np.min(lows[-5:]))
            if last5_range > 2.0 * atr:
                return "expanding"

        # ATR compressing: last-5 ATR < previous-5 ATR
        if len(df) >= 12:
            tr_all = np.maximum(
                highs[1:] - lows[1:],
                np.maximum(
                    np.abs(highs[1:] - closes[:-1]),
                    np.abs(lows[1:] - closes[:-1]),
                ),
            )
            if len(tr_all) >= 10:
                atr_last5 = float(np.mean(tr_all[-5:]))
                atr_prev5 = float(np.mean(tr_all[-10:-5]))
                if atr_prev5 > 0 and atr_last5 < atr_prev5 * 0.85:
                    return "compressing"

        # Reversal: large wick relative to body on most recent candle suggests sweep
        if len(df) >= 1:
            last_high = float(highs[-1])
            last_low = float(lows[-1])
            last_open = float(df["open"].values[-1])
            last_close = float(closes[-1])
            candle_range = last_high - last_low
            body = abs(last_close - last_open)
            if candle_range > 0 and body > 0:
                wick_ratio = (candle_range - body) / candle_range
                if wick_ratio >= 0.6 and candle_range > 1.5 * atr:
                    return "reversal"

        if adx_proxy > 0.02:
            return "trending"
        if adx_proxy < 0.008:
            return "ranging"

        return "trending"

    def get_session(self, timestamp) -> str:
        if timestamp is None:
            return "off"
        if isinstance(timestamp, (int, float)):
            hour = int(timestamp) % 24
        elif hasattr(timestamp, "hour"):
            hour = int(timestamp.hour)
        else:
            try:
                dt = pd.Timestamp(timestamp)
                hour = int(dt.hour)
            except Exception:
                return "off"

        # London: 02–10 UTC
        if 2 <= hour < 10:
            return "london"
        # NY AM: 13–17 UTC
        if 13 <= hour < 17:
            return "ny_am"
        # NY PM: 17–21 UTC
        if 17 <= hour < 21:
            return "ny_pm"
        # Asian: 22–02 UTC (wraps midnight)
        if hour >= 22 or hour < 2:
            return "asian"
        # 10–13 and 21–22 are off
        return "off"

    def get_trend_strength(self, df: pd.DataFrame) -> float:
        if len(df) < 20:
            n = max(len(df), 2)
        else:
            n = 20

        closes = df["close"].values.astype(float)
        if len(closes) < n:
            n = len(closes)
        if n < 2:
            return 0.0

        window = closes[-n:]
        weights = np.exp(np.linspace(0, 1, n))
        weights /= weights.sum()
        ema = float(np.dot(weights, window))

        # Slope of EMA: difference between last EMA and EMA half-window ago
        half = max(n // 2, 1)
        window_half = closes[-(n + half): -half] if len(closes) >= n + half else closes[:n]
        if len(window_half) < 2:
            window_half = window
        weights_half = np.exp(np.linspace(0, 1, len(window_half)))
        weights_half /= weights_half.sum()
        ema_half = float(np.dot(weights_half, window_half))

        slope = ema - ema_half
        atr = self._compute_atr(df)
        if atr == 0.0:
            return 0.0

        normalized = abs(slope) / atr
        strength = float(np.clip(normalized / 3.0, 0.0, 1.0))
        return strength

    def recommend_plans(
        self,
        regime: str,
        volatility: str,
        session: str,
        trend_strength: float,
    ) -> Tuple[List[str], float]:
        kill_zone = session in ("london", "ny_am")

        if regime == "trending" and volatility == "high" and kill_zone:
            return ["A", "B", "C", "D"], +0.5

        if regime == "ranging" and volatility == "low":
            return ["B", "D"], -0.5

        if regime == "reversal":
            return ["A", "B"], +0.3

        if regime == "expanding" and kill_zone:
            return ["A", "B", "C"], +0.3

        if regime == "compressing":
            return ["D"], -0.3

        if session == "off":
            return ["D"], -0.5

        return ["A", "B", "C", "D"], 0.0

    def analyze(self, df: pd.DataFrame, timestamp=None) -> MarketRegime:
        required_cols = {"open", "high", "low", "close"}
        if not required_cols.issubset(set(df.columns)):
            raise ValueError(
                f"DataFrame must contain columns: {required_cols}. Got: {list(df.columns)}"
            )

        if len(df) == 0:
            return MarketRegime(
                regime="ranging",
                volatility="normal",
                session="off",
                trend_strength=0.0,
                atr=0.0,
                adx=0.0,
                recommended_plans=["D"],
                confidence_modifier=-0.5,
                description="Insufficient data — defaulting to minimal-risk plan D.",
            )

        volatility = self.classify_volatility(df)
        regime = self.classify_regime(df)
        atr = self._compute_atr(df)
        adx_proxy = self._compute_adx_proxy(df, period=14)
        trend_strength = self.get_trend_strength(df)

        if timestamp is None and isinstance(df.index, pd.DatetimeIndex) and len(df) > 0:
            timestamp = df.index[-1]
        session = self.get_session(timestamp)

        recommended_plans, confidence_modifier = self.recommend_plans(
            regime, volatility, session, trend_strength
        )

        description_parts = [
            f"Regime: {regime}",
            f"Volatility: {volatility}",
            f"Session: {session}",
            f"Trend strength: {trend_strength:.2f}",
            f"ATR: {atr:.5f}",
            f"ADX proxy: {adx_proxy:.4f}",
            f"Plans: {', '.join(recommended_plans)}",
            f"Confidence modifier: {confidence_modifier:+.1f}",
        ]
        description = " | ".join(description_parts)

        return MarketRegime(
            regime=regime,
            volatility=volatility,
            session=session,
            trend_strength=float(trend_strength),
            atr=float(atr),
            adx=float(adx_proxy),
            recommended_plans=recommended_plans,
            confidence_modifier=float(confidence_modifier),
            description=description,
        )

    def get_entry_filter(self, regime: MarketRegime, base_threshold: float) -> float:
        adjusted = base_threshold + regime.confidence_modifier
        return float(np.clip(adjusted, 4.0, 9.0))
