"""
Data manager — fetches and caches OHLCV data from yfinance.
Falls back to MT5 if connected.
"""

import os
import pandas as pd
from datetime import datetime, timedelta
from loguru import logger

YFINANCE_MAP = {
    "XAUUSD": "GC=F",
    "BTCUSD": "BTC-USD",
    "GBPUSD": "GBPUSD=X",
    "EURUSD": "EURUSD=X",
    "DXY":    "DX-Y.NYB",
}

DATA_DIR = r"C:\Users\Administrator\Desktop\AutoTraderClaude\data"
CACHE_HOURS = 4


class DataManager:
    def __init__(self, mt5=None):
        self.mt5 = mt5
        os.makedirs(DATA_DIR, exist_ok=True)

    def get_ohlcv(self, symbol: str, timeframe: str = "H4", bars: int = 500) -> pd.DataFrame:
        cache_path = os.path.join(DATA_DIR, f"{symbol}_{timeframe}.csv")
        if self._cache_fresh(cache_path):
            df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            logger.debug(f"Cache hit: {symbol} {timeframe} ({len(df)} bars)")
            return df

        df = self._fetch_yfinance(symbol, timeframe, bars)
        if df is not None and not df.empty:
            df.to_csv(cache_path)
            return df

        if os.path.exists(cache_path):
            logger.warning(f"Using stale cache for {symbol}")
            return pd.read_csv(cache_path, index_col=0, parse_dates=True)

        logger.error(f"No data for {symbol} {timeframe}")
        return pd.DataFrame()

    def _cache_fresh(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))
        return age < timedelta(hours=CACHE_HOURS)

    def _fetch_yfinance(self, symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
        try:
            import yfinance as yf
            ticker = YFINANCE_MAP.get(symbol, symbol)
            tf_map = {"H1": "1h", "H4": "1h", "D1": "1d"}
            interval = tf_map.get(timeframe, "1h")
            period = "6mo" if timeframe in ("H1", "H4") else "2y"

            raw = yf.download(ticker, period=period, interval=interval,
                              auto_adjust=True, progress=False)
            if raw.empty:
                return None

            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = [col[0].lower() for col in raw.columns]
            else:
                raw.columns = [c.lower() for c in raw.columns]

            raw.index = pd.to_datetime(raw.index, utc=True)
            raw.index = raw.index.tz_localize(None)

            if timeframe == "H4":
                raw = raw.resample("4h").agg({
                    "open": "first", "high": "max",
                    "low": "min", "close": "last", "volume": "sum"
                }).dropna()

            raw = raw[["open", "high", "low", "close", "volume"]].tail(bars)
            logger.info(f"yfinance {symbol} {timeframe}: {len(raw)} bars")
            return raw
        except Exception as e:
            logger.error(f"yfinance error for {symbol}: {e}")
            return None

    def get_multi(self, symbols: list, timeframe: str = "H4") -> dict:
        return {s: self.get_ohlcv(s, timeframe) for s in symbols}
