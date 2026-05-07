"""
OHLCV data loader.
Supports: CSV files, yfinance (fallback), synthetic generation for testing.
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional
from loguru import logger
from config import DATA_DIR


class DataLoader:
    """Loads OHLCV data from disk or generates synthetic data."""

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)

    def load_csv(self, pair: str, timeframe: str) -> Optional[pd.DataFrame]:
        """Load data from CSV file: {DATA_DIR}/{pair}_{timeframe}.csv"""
        path = os.path.join(DATA_DIR, f"{pair}_{timeframe}.csv")
        if not os.path.exists(path):
            logger.warning(f"CSV not found: {path}")
            return None
        df = pd.read_csv(path, parse_dates=["time"], index_col="time")
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]].dropna()
        logger.info(f"Loaded {len(df)} bars for {pair} {timeframe}")
        return df

    def load_yfinance(self, pair: str, timeframe: str,
                      start: str = "2020-01-01", end: Optional[str] = None) -> Optional[pd.DataFrame]:
        """Load data via yfinance (requires internet)."""
        try:
            import yfinance as yf
            ticker_map = {
                "XAUUSD": "GC=F", "BTCUSD": "BTC-USD",
                "GBPUSD": "GBPUSD=X", "EURUSD": "EURUSD=X",
            }
            tf_map = {"H1": "1h", "H4": "1h", "D1": "1d", "W1": "1wk"}
            ticker = ticker_map.get(pair, pair)
            interval = tf_map.get(timeframe, "1h")
            df = yf.download(ticker, start=start, end=end, interval=interval, progress=False)
            df.columns = [c.lower() for c in df.columns]
            df = df[["open", "high", "low", "close", "volume"]].dropna()
            # Resample to H4 if needed
            if timeframe == "H4":
                df = df.resample("4h").agg({
                    "open": "first", "high": "max",
                    "low": "min", "close": "last", "volume": "sum"
                }).dropna()
            logger.info(f"yfinance loaded {len(df)} bars for {pair} {timeframe}")
            return df
        except Exception as e:
            logger.error(f"yfinance error: {e}")
            return None

    def generate_synthetic(
        self,
        pair: str = "XAUUSD",
        n_bars: int = 2000,
        timeframe: str = "H4",
        seed: int = 42,
    ) -> pd.DataFrame:
        """Generate realistic synthetic OHLCV data for backtesting."""
        np.random.seed(seed)
        start_price = {"XAUUSD": 1900.0, "BTCUSD": 30000.0,
                       "GBPUSD": 1.25, "EURUSD": 1.08}.get(pair, 1.0)
        volatility = {"XAUUSD": 0.008, "BTCUSD": 0.025,
                      "GBPUSD": 0.004, "EURUSD": 0.003}.get(pair, 0.005)

        returns = np.random.normal(0, volatility, n_bars)
        # Add occasional trend runs
        for i in range(0, n_bars, 100):
            trend_len = np.random.randint(20, 50)
            trend_dir = np.random.choice([-1, 1])
            end_idx = min(i + trend_len, n_bars)
            returns[i:end_idx] += trend_dir * volatility * 0.5

        closes = start_price * np.exp(np.cumsum(returns))
        opens = np.roll(closes, 1)
        opens[0] = start_price

        wicks = np.random.uniform(0.001, 0.005, n_bars)
        highs = np.maximum(opens, closes) * (1 + wicks * np.random.uniform(0.3, 1.0, n_bars))
        lows = np.minimum(opens, closes) * (1 - wicks * np.random.uniform(0.3, 1.0, n_bars))
        volumes = np.random.randint(100, 10000, n_bars).astype(float)

        tf_hours = {"H1": 1, "H4": 4, "D1": 24, "W1": 168}
        freq_hours = tf_hours.get(timeframe, 4)
        start_dt = datetime(2020, 1, 1)
        times = [start_dt + timedelta(hours=i * freq_hours) for i in range(n_bars)]

        df = pd.DataFrame({
            "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": volumes,
        }, index=pd.DatetimeIndex(times, name="time"))

        logger.info(f"Generated {n_bars} synthetic bars for {pair} {timeframe}")
        return df

    def load(self, pair: str, timeframe: str, synthetic_fallback: bool = True) -> pd.DataFrame:
        """Load from CSV first, then yfinance, then synthetic."""
        df = self.load_csv(pair, timeframe)
        if df is not None and len(df) >= 200:
            return df
        df = self.load_yfinance(pair, timeframe)
        if df is not None and len(df) >= 200:
            return df
        if synthetic_fallback:
            logger.warning(f"Using synthetic data for {pair} {timeframe}")
            return self.generate_synthetic(pair=pair, timeframe=timeframe)
        raise FileNotFoundError(f"No data available for {pair} {timeframe}")
