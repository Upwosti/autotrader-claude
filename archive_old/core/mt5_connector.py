"""
MT5 connector — wraps MetaTrader5 API with graceful fallback when MT5 not installed.
"""

import os
from loguru import logger
from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False
    logger.warning("MetaTrader5 not installed — running in simulation mode")


class MT5Connector:
    def __init__(self):
        self.connected = False

    def connect(self) -> bool:
        if not MT5_AVAILABLE:
            logger.warning("MT5 not available — skipping connection")
            return False
        if not mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
            logger.error(f"MT5 init failed: {mt5.last_error()}")
            return False
        info = mt5.account_info()
        if info is None:
            logger.error("MT5 account info unavailable")
            return False
        self.connected = True
        logger.info(f"MT5 connected — balance: {info.balance} {info.currency}")
        return True

    def disconnect(self):
        if MT5_AVAILABLE and self.connected:
            mt5.shutdown()
            self.connected = False

    def get_account_info(self) -> dict:
        if not self.connected:
            return {"balance": 10000, "equity": 10000, "currency": "USD", "profit": 0.0}
        info = mt5.account_info()
        return {
            "balance": info.balance,
            "equity": info.equity,
            "currency": info.currency,
            "profit": info.profit,
        }

    def get_symbol_info(self, symbol: str) -> dict:
        if not self.connected:
            defaults = {"XAUUSD": 0.01, "GBPUSD": 0.00001, "EURUSD": 0.00001, "BTCUSD": 0.01}
            return {"point": defaults.get(symbol, 0.00001), "trade_tick_size": defaults.get(symbol, 0.00001)}
        info = mt5.symbol_info(symbol)
        if info is None:
            return {}
        return {"point": info.point, "trade_tick_size": info.trade_tick_size, "digits": info.digits}

    def get_open_positions(self) -> list:
        if not self.connected:
            return []
        positions = mt5.positions_get()
        if positions is None:
            return []
        return [{"ticket": p.ticket, "symbol": p.symbol, "type": p.type,
                 "volume": p.volume, "open_price": p.price_open,
                 "sl": p.sl, "tp": p.tp, "profit": p.profit} for p in positions]

    def place_order(self, symbol: str, order_type: str, volume: float,
                    price: float, sl: float, tp: float, comment: str = "") -> dict:
        if not self.connected:
            logger.info(f"[SIM] Order: {order_type} {volume} {symbol} @ {price} SL={sl} TP={tp}")
            return {"retcode": 10009, "order": 0, "simulated": True}

        action = mt5.ORDER_TYPE_BUY if order_type == "buy" else mt5.ORDER_TYPE_SELL
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": action,
            "price": price,
            "sl": sl,
            "tp": tp,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        return {"retcode": result.retcode, "order": result.order, "comment": result.comment}

    def close_position(self, ticket: int) -> bool:
        if not self.connected:
            logger.info(f"[SIM] Close position {ticket}")
            return True
        position = mt5.positions_get(ticket=ticket)
        if not position:
            return False
        pos = position[0]
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = mt5.symbol_info_tick(pos.symbol).bid if close_type == mt5.ORDER_TYPE_SELL else mt5.symbol_info_tick(pos.symbol).ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "comment": "AutoTrader close",
        }
        result = mt5.order_send(request)
        return result.retcode == mt5.TRADE_RETCODE_DONE
