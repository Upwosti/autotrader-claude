"""
Position Sizer — calculates lot size based on risk % and SL distance.
"""

from loguru import logger

PIP_SIZES = {
    "XAUUSD": 0.1,
    "BTCUSD": 1.0,
    "GBPUSD": 0.0001,
    "EURUSD": 0.0001,
}

LOT_VALUE_PER_PIP = {
    "XAUUSD": 1.0,    # $1 per 0.01 lot per pip (simplified)
    "BTCUSD": 1.0,
    "GBPUSD": 1.0,
    "EURUSD": 1.0,
}

MIN_LOT  = 0.01
MAX_LOT  = 10.0
STEP_LOT = 0.01


def calculate_lot_size(account_balance: float, risk_pct: float,
                       entry: float, sl: float, symbol: str) -> float:
    """
    Returns lot size rounded to 2 decimal places.
    risk_pct: e.g. 1.0 for 1%
    """
    if entry <= 0 or sl <= 0 or entry == sl:
        return MIN_LOT

    risk_amount = account_balance * (risk_pct / 100.0)
    pip_size = PIP_SIZES.get(symbol, 0.0001)
    sl_pips  = abs(entry - sl) / pip_size

    if sl_pips <= 0:
        return MIN_LOT

    # Simplified: $10 per pip per lot for most FX/gold
    pip_value_per_lot = 10.0 if symbol in ("GBPUSD", "EURUSD") else (
        100.0 if symbol == "XAUUSD" else 10.0
    )

    lot = risk_amount / (sl_pips * pip_value_per_lot)
    lot = max(MIN_LOT, min(MAX_LOT, round(lot / STEP_LOT) * STEP_LOT))
    logger.debug(f"Position size {symbol}: balance={account_balance} risk={risk_pct}% "
                 f"sl_pips={sl_pips:.1f} => {lot} lots")
    return lot
