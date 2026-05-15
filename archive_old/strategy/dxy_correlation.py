"""
DXY Correlation — filters trade direction using DXY trend.
Gold/GBPUSD/EURUSD: inverse DXY. BTCUSD: weak inverse.
"""

from loguru import logger

INVERSE_PAIRS   = {"XAUUSD", "GBPUSD", "EURUSD"}
POSITIVE_PAIRS  = set()
WEAK_PAIRS      = {"BTCUSD"}


def dxy_allows_trade(symbol: str, trade_direction: str, dxy_bias: str) -> bool:
    """
    Returns True if DXY bias does not contradict the trade direction.
    neutral DXY = always allow.
    """
    if dxy_bias == "neutral":
        return True

    if symbol in INVERSE_PAIRS:
        # DXY bullish => expect gold/EUR/GBP down => only allow SELL
        if dxy_bias == "bullish" and trade_direction == "buy":
            logger.debug(f"DXY filter blocked BUY on {symbol} (DXY bullish)")
            return False
        if dxy_bias == "bearish" and trade_direction == "sell":
            logger.debug(f"DXY filter blocked SELL on {symbol} (DXY bearish)")
            return False

    if symbol in POSITIVE_PAIRS:
        if dxy_bias == "bearish" and trade_direction == "buy":
            return False
        if dxy_bias == "bullish" and trade_direction == "sell":
            return False

    # BTCUSD and unknowns: don't filter
    return True
