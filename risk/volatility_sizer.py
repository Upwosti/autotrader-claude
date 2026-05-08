"""
VolatilitySizer — ATR-based position sizing with volatility scaling.

Reduces lot size in high-volatility regimes, increases slightly in quiet
markets, and skips trades entirely when volatility is extreme.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd
from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ATR_PERIODS: int = 14

NORMAL_ATR_MULTIPLIER: float = 1.0
HIGH_VOL_THRESHOLD:    float = 1.5   # ATR > 1.5× 20-period mean → high vol
EXTREME_VOL_THRESHOLD: float = 2.5   # ATR > 2.5× mean → extreme (skip)
LOW_VOL_THRESHOLD:     float = 0.7   # ATR < 0.7× mean → quiet (larger size ok)

# Pip sizes per instrument (price per 1 pip)
_PIP_SIZE: dict[str, float] = {
    "XAUUSD": 0.1,
    "XAGUSD": 0.01,
    "XPTUSD": 0.01,
    "GBPUSD": 0.0001, "EURUSD": 0.0001,
    "USDJPY": 0.01,   "USDCHF": 0.0001,
    "AUDUSD": 0.0001, "NZDUSD": 0.0001,
    "USDCAD": 0.0001,
    "EURJPY": 0.01,   "GBPJPY": 0.01,
    "BTCUSD": 1.0,    "ETHUSD": 0.10,
    "BTC-USD": 1.0,   "ETH-USD": 0.10,
    "NAS100": 1.0,    "US30": 1.0,    "GER40": 1.0,
    "NQ=F":   1.0,    "YM=F": 1.0,    "FDAX":  1.0,
    "GC=F":   0.10,   "SI=F": 0.01,
}

# Contract (lot) unit sizes — units per standard lot
_LOT_UNITS: dict[str, float] = {
    "XAUUSD": 100.0,    "GC=F": 100.0,
    "XAGUSD": 5000.0,   "SI=F": 5000.0,
    "XPTUSD": 100.0,
    "GBPUSD": 100_000.0, "EURUSD": 100_000.0,
    "USDJPY": 100_000.0, "USDCHF": 100_000.0,
    "AUDUSD": 100_000.0, "NZDUSD": 100_000.0,
    "USDCAD": 100_000.0,
    "EURJPY": 100_000.0, "GBPJPY": 100_000.0,
    "BTCUSD": 1.0,      "ETHUSD": 1.0,
    "BTC-USD": 1.0,     "ETH-USD": 1.0,
    "NAS100": 1.0,      "US30": 1.0,    "GER40": 1.0,
    "NQ=F":   1.0,      "YM=F": 1.0,    "FDAX":  1.0,
}

_MIN_LOT: float = 0.01
_MAX_LOT: float = 10.0


class VolatilitySizer:
    """
    Adjusts lot size up or down based on the ratio of the current ATR to its
    rolling 20-bar mean.

    Parameters
    ----------
    base_risk_pct : float
        Percentage of account equity to risk per trade (e.g. 1.0 = 1 %).
    account_size : float
        Current account equity in USD.
    """

    def __init__(
        self,
        base_risk_pct: float = 1.0,
        account_size:  float = 10_000.0,
    ) -> None:
        self.base_risk_pct = base_risk_pct
        self.account_size  = account_size

    # ------------------------------------------------------------------
    # Core ATR helpers
    # ------------------------------------------------------------------

    def calculate_atr(
        self,
        df:     pd.DataFrame,
        period: int = ATR_PERIODS,
    ) -> float:
        """
        Compute Wilder's Average True Range for the most-recent bar.

        Expects columns: 'high', 'low', 'close' (case-insensitive).
        Returns 0.0 on any error.
        """
        try:
            df = df.copy()
            df.columns = [c.lower() for c in df.columns]

            if len(df) < period + 1:
                logger.debug(
                    f"calculate_atr: insufficient bars ({len(df)} < {period + 1})"
                )
                return 0.0

            high  = df["high"].values.astype(float)
            low   = df["low"].values.astype(float)
            close = df["close"].values.astype(float)

            prev_close = np.roll(close, 1)
            prev_close[0] = close[0]

            tr = np.maximum(
                high - low,
                np.maximum(
                    np.abs(high - prev_close),
                    np.abs(low  - prev_close),
                ),
            )

            # Wilder smoothing
            atr_vals = np.zeros(len(tr))
            atr_vals[period - 1] = tr[:period].mean()
            for i in range(period, len(tr)):
                atr_vals[i] = (atr_vals[i - 1] * (period - 1) + tr[i]) / period

            return float(atr_vals[-1])

        except Exception as exc:
            logger.warning(f"calculate_atr error: {exc}")
            return 0.0

    def get_atr_ratio(self, df: pd.DataFrame) -> float:
        """
        Return current ATR divided by its 20-bar rolling mean.

        A ratio > 1.0 indicates above-average volatility.
        Returns 1.0 as a neutral fallback on any error.
        """
        try:
            current_atr = self.calculate_atr(df, period=ATR_PERIODS)
            if current_atr <= 0:
                return 1.0

            # Compute ATR on a rolling 20-bar window using the last 40 bars
            df_tail = df.tail(40)
            atrs: list[float] = []
            for end in range(ATR_PERIODS, len(df_tail)):
                window = df_tail.iloc[: end + 1]
                a      = self.calculate_atr(window, period=ATR_PERIODS)
                if a > 0:
                    atrs.append(a)

            if not atrs:
                return 1.0

            mean_atr = float(np.mean(atrs[-20:] if len(atrs) >= 20 else atrs))
            if mean_atr <= 0:
                return 1.0

            return round(current_atr / mean_atr, 4)

        except Exception as exc:
            logger.warning(f"get_atr_ratio error: {exc}")
            return 1.0

    # ------------------------------------------------------------------
    # Sizing multiplier
    # ------------------------------------------------------------------

    def get_size_multiplier(self, df: pd.DataFrame) -> float:
        """
        Map ATR ratio to a position-size multiplier.

        atr_ratio > 2.5 → 0.0   (skip trade)
        atr_ratio > 1.5 → 0.50  (half size, high vol)
        atr_ratio > 1.2 → 0.75  (reduced size, elevated vol)
        atr_ratio < 0.7 → 1.25  (larger size, quiet market)
        else            → 1.00  (normal size)
        """
        ratio = self.get_atr_ratio(df)
        logger.debug(f"ATR ratio: {ratio:.3f}")

        if ratio > EXTREME_VOL_THRESHOLD:
            return 0.0
        if ratio > HIGH_VOL_THRESHOLD:
            return 0.5
        if ratio > 1.2:
            return 0.75
        if ratio < LOW_VOL_THRESHOLD:
            return 1.25
        return 1.0

    # ------------------------------------------------------------------
    # Main sizing method
    # ------------------------------------------------------------------

    def get_lot_size(
        self,
        df:    pd.DataFrame,
        entry: float,
        sl:    float,
        pair:  str,
    ) -> float:
        """
        Calculate a volatility-adjusted lot size.

        Steps:
        1.  Determine size multiplier from ATR regime.
        2.  Skip (return 0.0) if multiplier is zero.
        3.  Compute base lot from risk_pct and SL distance.
        4.  Apply volatility multiplier.
        5.  Clamp to [0.01, 10.0] and round to 2 decimal places.

        Parameters
        ----------
        df    : OHLCV DataFrame for the instrument.
        entry : Proposed entry price.
        sl    : Stop-loss price.
        pair  : Instrument symbol (e.g. "XAUUSD").

        Returns
        -------
        float
            Lot size, or 0.0 if the trade should be skipped.
        """
        multiplier = self.get_size_multiplier(df)

        if multiplier == 0.0:
            logger.info(f"{pair}: skipping trade — extreme volatility (ATR ratio > {EXTREME_VOL_THRESHOLD})")
            return 0.0

        sl_distance = abs(entry - sl)
        if sl_distance <= 0:
            logger.warning(f"{pair}: entry == sl, cannot size position")
            return 0.0

        pip_size  = _PIP_SIZE.get(pair, 0.0001)
        lot_units = _LOT_UNITS.get(pair, 100_000.0)

        risk_usd   = self.account_size * (self.base_risk_pct / 100.0)
        # Value of sl_distance in USD per lot
        sl_usd_per_lot = sl_distance * lot_units

        if sl_usd_per_lot <= 0:
            logger.warning(f"{pair}: sl_usd_per_lot is zero, defaulting to min lot")
            return _MIN_LOT

        base_lot     = risk_usd / sl_usd_per_lot
        adjusted_lot = base_lot * multiplier
        clamped_lot  = max(_MIN_LOT, min(_MAX_LOT, round(adjusted_lot, 2)))

        logger.debug(
            f"{pair}: base_lot={base_lot:.4f} × multiplier={multiplier:.2f} "
            f"= {adjusted_lot:.4f} → clamped={clamped_lot}"
        )
        return clamped_lot

    # ------------------------------------------------------------------
    # Trade skip guard
    # ------------------------------------------------------------------

    def should_skip_trade(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        Returns (skip, reason).

        Skip is True when ATR ratio exceeds EXTREME_VOL_THRESHOLD (2.5×).
        """
        ratio = self.get_atr_ratio(df)
        if ratio > EXTREME_VOL_THRESHOLD:
            reason = (
                f"Extreme volatility — ATR ratio {ratio:.2f} exceeds "
                f"threshold {EXTREME_VOL_THRESHOLD}"
            )
            logger.warning(f"should_skip_trade: {reason}")
            return True, reason
        return False, f"Volatility within limits (ATR ratio {ratio:.2f})"

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"VolatilitySizer(risk={self.base_risk_pct}%, "
            f"account={self.account_size:,.0f})"
        )
