"""
OHLCV data loader — real data via yfinance with local CSV cache.
Priority: CSV cache → yfinance → synthetic fallback.
Downloads last 6 months of H1 data and resamples to requested timeframe.
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional
from loguru import logger
from config import DATA_DIR


# Ticker mapping for yfinance
TICKER_MAP = {
    "XAUUSD": "GC=F",       # Gold futures
    "BTCUSD": "BTC-USD",
    "GBPUSD": "GBPUSD=X",
    "EURUSD": "EURUSD=X",
}

# Starting prices for synthetic fallback
SYNTHETIC_BASE = {
    "XAUUSD": 2000.0,
    "BTCUSD": 30000.0,
    "GBPUSD": 1.25,
    "EURUSD": 1.08,
}

SYNTHETIC_VOL = {
    "XAUUSD": 0.008,
    "BTCUSD": 0.025,
    "GBPUSD": 0.004,
    "EURUSD": 0.003,
}


class DataLoader:
    """Loads real OHLCV data from yfinance with local CSV cache."""

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)

    # ─── CSV Cache ────────────────────────────────────────────────────────

    def _cache_path(self, pair: str, timeframe: str) -> str:
        return os.path.join(DATA_DIR, f"{pair}_{timeframe}.csv")

    def _cache_is_fresh(self, path: str, max_age_hours: int = 4) -> bool:
        """Return True if cache file exists and is less than max_age_hours old."""
        if not os.path.exists(path):
            return False
        age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))
        return age < timedelta(hours=max_age_hours)

    def load_csv(self, pair: str, timeframe: str) -> Optional[pd.DataFrame]:
        """Load from local CSV cache regardless of freshness."""
        path = self._cache_path(pair, timeframe)
        if not os.path.exists(path):
            return None
        try:
            df = pd.read_csv(path, parse_dates=["time"], index_col="time")
            df.columns = [c.lower() for c in df.columns]
            required = {"open", "high", "low", "close", "volume"}
            if not required.issubset(set(df.columns)):
                logger.warning(f"CSV {path} missing columns")
                return None
            df = df[list(required)].dropna()
            logger.info(f"Loaded {len(df)} bars from cache: {pair} {timeframe}")
            return df if len(df) >= 100 else None
        except Exception as e:
            logger.warning(f"CSV load error ({path}): {e}")
            return None

    def _save_csv(self, df: pd.DataFrame, pair: str, timeframe: str):
        path = self._cache_path(pair, timeframe)
        df.index.name = "time"
        df.to_csv(path)
        logger.debug(f"Cached {len(df)} bars → {path}")

    # ─── yfinance ─────────────────────────────────────────────────────────

    def load_yfinance(self, pair: str, timeframe: str,
                      period: str = "6mo") -> Optional[pd.DataFrame]:
        """
        Download real OHLCV from yfinance.
        Downloads 1h data and resamples to target timeframe.
        Saves to local CSV cache.
        """
        try:
            import yfinance as yf
        except ImportError:
            logger.error("yfinance not installed — run: pip install yfinance")
            return None

        ticker = TICKER_MAP.get(pair)
        if ticker is None:
            logger.warning(f"No ticker mapping for {pair}")
            return None

        # For H4/D1/W1 we download H1 and resample (yfinance limit: 1h = last 730d, 4h not available)
        # For W1 we use daily data
        try:
            if timeframe in ("H1", "H4"):
                raw = yf.download(ticker, period=period, interval="1h",
                                  progress=False, auto_adjust=True)
            elif timeframe == "D1":
                # Use 10y for maximum history (swing trade backtesting)
                raw = yf.download(ticker, period="10y", interval="1d",
                                  progress=False, auto_adjust=True)
            elif timeframe == "W1":
                raw = yf.download(ticker, period="10y", interval="1wk",
                                  progress=False, auto_adjust=True)
            else:
                raw = yf.download(ticker, period=period, interval="1h",
                                  progress=False, auto_adjust=True)

            if raw is None or raw.empty:
                logger.warning(f"yfinance returned empty data for {ticker}")
                return None

            # Flatten multi-level columns (yfinance returns MultiIndex)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = [col[0].lower() for col in raw.columns]
            else:
                raw.columns = [c.lower() for c in raw.columns]

            # Ensure required columns
            col_map = {"adj close": "close"}
            raw.rename(columns=col_map, inplace=True)
            required = ["open", "high", "low", "close", "volume"]
            missing = [c for c in required if c not in raw.columns]
            if missing:
                logger.warning(f"yfinance data missing columns {missing} for {pair}")
                return None

            df = raw[required].copy()
            df = df.dropna(subset=["open", "high", "low", "close"])
            df.index.name = "time"

            # Resample H1 → H4
            if timeframe == "H4":
                df = df.resample("4h").agg({
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }).dropna(subset=["open", "high", "low", "close"])

            # Remove future/empty bars
            df = df[df["close"] > 0]

            if len(df) < 50:
                logger.warning(f"yfinance {pair} {timeframe}: only {len(df)} bars after processing")
                return None

            self._save_csv(df, pair, timeframe)
            logger.info(f"yfinance: {len(df)} bars for {pair} {timeframe} "
                        f"({df.index[0].date()} → {df.index[-1].date()})")
            return df

        except Exception as e:
            logger.error(f"yfinance download error ({pair} {timeframe}): {e}")
            return None

    # ─── Synthetic Fallback ────────────────────────────────────────────────

    def generate_synthetic(
        self,
        pair: str = "XAUUSD",
        bars: int = 2000,
        n_bars: int = None,       # alias
        timeframe: str = "H4",
        seed: int = 42,
    ) -> pd.DataFrame:
        """
        Generate realistic synthetic OHLCV for testing.
        Uses geometric Brownian motion with periodic trend regimes.
        """
        if n_bars is not None:
            bars = n_bars
        np.random.seed(seed)
        start_price = SYNTHETIC_BASE.get(pair, 1.0)
        vol = SYNTHETIC_VOL.get(pair, 0.005)

        # GBM with trend regime changes
        returns = np.random.normal(0, vol, bars)
        trend_start = 0
        while trend_start < bars:
            trend_len = np.random.randint(30, 80)
            trend_dir = np.random.choice([-1, 1])
            end = min(trend_start + trend_len, bars)
            returns[trend_start:end] += trend_dir * vol * 0.4
            trend_start = end

        closes = start_price * np.exp(np.cumsum(returns))
        opens = np.empty_like(closes)
        opens[0] = start_price
        opens[1:] = closes[:-1]

        # Wicks: random between 10–60% of body
        body = np.abs(closes - opens)
        upper_wick = body * np.random.uniform(0.1, 0.6, bars)
        lower_wick = body * np.random.uniform(0.1, 0.6, bars)
        highs = np.maximum(opens, closes) + upper_wick
        lows = np.minimum(opens, closes) - lower_wick

        # Volume — higher on large moves
        vol_base = np.random.randint(500, 5000, bars).astype(float)
        vol_spike = np.where(body > np.percentile(body, 80), vol_base * 2, vol_base)

        tf_hours = {"H1": 1, "H4": 4, "D1": 24, "W1": 168}
        freq = tf_hours.get(timeframe, 4)
        start_dt = datetime(2024, 1, 1)
        idx = pd.DatetimeIndex(
            [start_dt + timedelta(hours=i * freq) for i in range(bars)],
            name="time"
        )

        df = pd.DataFrame({
            "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": vol_spike,
        }, index=idx)

        logger.info(f"Generated {bars} synthetic bars for {pair} {timeframe}")
        return df

    # ─── Main Loader ──────────────────────────────────────────────────────

    def load(
        self,
        pair: str,
        timeframe: str,
        synthetic_fallback: bool = True,
        source: str = "auto",           # "auto" | "yfinance" | "csv" | "synthetic"
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        Load OHLCV data. Priority:
          1. Fresh CSV cache (< 4h old), unless force_refresh
          2. yfinance download + cache
          3. Stale CSV cache
          4. Synthetic (if synthetic_fallback=True)
        """
        if source == "synthetic":
            return self.generate_synthetic(pair=pair, timeframe=timeframe)

        if source == "csv":
            df = self.load_csv(pair, timeframe)
            if df is not None:
                return df
            raise FileNotFoundError(f"CSV not found for {pair} {timeframe}")

        # Auto: try cache first (unless stale or forced)
        if not force_refresh:
            cache_path = self._cache_path(pair, timeframe)
            if self._cache_is_fresh(cache_path, max_age_hours=4):
                df = self.load_csv(pair, timeframe)
                if df is not None and len(df) >= 100:
                    return df

        # Try yfinance
        df = self.load_yfinance(pair, timeframe)
        if df is not None and len(df) >= 100:
            return df

        # Fall back to stale cache
        df = self.load_csv(pair, timeframe)
        if df is not None and len(df) >= 100:
            logger.warning(f"Using stale cache for {pair} {timeframe}")
            return df

        # Last resort: synthetic
        if synthetic_fallback:
            logger.warning(f"All sources failed for {pair} {timeframe} — using synthetic data")
            return self.generate_synthetic(pair=pair, timeframe=timeframe)

        raise FileNotFoundError(f"No data available for {pair} {timeframe}")
