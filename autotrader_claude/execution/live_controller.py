"""
Live Market Controller — orchestrates all 4 trading modules in real-time.

Modes:
  paper   — simulates execution with no real money (default, safe)
  mt5     — live execution via MetaTrader5 Python API (requires MT5 installed)

Architecture:
  1. Every `poll_interval` seconds: fetch/update latest bars for all timeframes
  2. Run TradingModuleManager.evaluate_all() on current data
  3. For each valid signal: check if already in a trade for that module
  4. If not: open position (paper/MT5), send Telegram alert
  5. Check open positions: if SL or TP hit, close and report
  6. Send balance update every `balance_interval` seconds

Balance starts at BACKTEST_INITIAL_CAPITAL and updates from paper P&L.
MT5 mode reads real account balance from broker.
"""

import sys
import os
import time
import gc
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from loguru import logger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'execution'))

from config import StrategyParams, BACKTEST_INITIAL_CAPITAL
from backtester.data_loader import DataLoader
from trading_module import TradingModuleManager, ModuleSignal
from alerts.telegram_bot import TelegramAlert

_STATE_FILE = os.path.join(os.path.expanduser("~"), "autotrader_live_state.json")


# ── Open position ──────────────────────────────────────────────────────────────

@dataclass
class OpenPosition:
    module_name: str
    pair: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    rrr: float
    risk_pct: float
    entry_model: str
    opened_at: datetime
    size: float = 1.0           # lots / units (paper only)
    pnl_pips: float = 0.0
    pnl_pct: float = 0.0


# ── Controller ────────────────────────────────────────────────────────────────

class LiveController:
    """Runs all 4 trading modules in live (paper or MT5) mode."""

    TF_LIST = ["MN", "W1", "D1", "4H", "1H", "15M"]

    def __init__(
        self,
        params: StrategyParams,
        pair: str = "XAUUSD",
        mode: str = "paper",         # 'paper' | 'mt5'
        poll_interval: int = 60,     # seconds between bar checks
        balance_interval: int = 3600,# seconds between balance reports
        max_open_trades: int = 4,    # one per module max
        risk_pct: float = 1.0,
    ):
        self.params = params
        self.pair = pair
        self.mode = mode
        self.poll_interval = poll_interval
        self.balance_interval = balance_interval
        self.max_open_trades = max_open_trades
        self.risk_pct = risk_pct
        self.pip_size = self._get_pip_size(pair)

        self.loader = DataLoader()
        self.module_manager = TradingModuleManager(params)
        self.telegram = TelegramAlert()

        self.balance = BACKTEST_INITIAL_CAPITAL
        self.daily_start_balance = self.balance
        self.open_positions: Dict[str, OpenPosition] = {}  # module_name → position
        self.closed_trades: List[dict] = []
        self.total_trades = 0
        self.last_balance_report = 0.0

        self._load_state()

    def _get_pip_size(self, pair: str) -> float:
        return {"XAUUSD": 0.01, "BTCUSD": 1.0}.get(pair, 0.0001)

    # ── State persistence ──────────────────────────────────────────────────

    # Maximum daily drawdown before emergency stop (10% of daily start)
    MAX_DAILY_DRAWDOWN_PCT: float = 10.0

    def _save_state(self):
        # Serialize open positions for crash recovery
        positions_data = {}
        for name, pos in self.open_positions.items():
            positions_data[name] = {
                "module_name": pos.module_name,
                "pair": pos.pair,
                "direction": pos.direction,
                "entry_price": pos.entry_price,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "rrr": pos.rrr,
                "risk_pct": pos.risk_pct,
                "entry_model": pos.entry_model,
                "opened_at": pos.opened_at.isoformat(),
            }
        state = {
            "balance": self.balance,
            "total_trades": self.total_trades,
            "daily_start_balance": self.daily_start_balance,
            "closed_trades_count": len(self.closed_trades),
            "open_positions": positions_data,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            # Atomic write: write to .tmp then rename to avoid partial reads
            tmp_path = _STATE_FILE + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_path, _STATE_FILE)
        except Exception as e:
            logger.warning(f"State save failed: {e}")

    def _load_state(self):
        if not os.path.exists(_STATE_FILE):
            return
        try:
            with open(_STATE_FILE) as f:
                state = json.load(f)
            self.balance = float(state.get("balance", self.balance))
            self.total_trades = int(state.get("total_trades", 0))
            self.daily_start_balance = float(state.get("daily_start_balance", self.balance))
            # Restore open positions from state
            for name, pdata in state.get("open_positions", {}).items():
                try:
                    self.open_positions[name] = OpenPosition(
                        module_name=pdata["module_name"],
                        pair=pdata["pair"],
                        direction=pdata["direction"],
                        entry_price=float(pdata["entry_price"]),
                        stop_loss=float(pdata["stop_loss"]),
                        take_profit=float(pdata["take_profit"]),
                        rrr=float(pdata.get("rrr", 0)),
                        risk_pct=float(pdata.get("risk_pct", 1.0)),
                        entry_model=pdata.get("entry_model", "none"),
                        opened_at=datetime.fromisoformat(pdata["opened_at"]),
                    )
                except Exception as pe:
                    logger.warning(f"Could not restore position {name}: {pe}")
            logger.info(
                f"Restored state: balance=${self.balance:,.2f} "
                f"trades={self.total_trades} "
                f"open_positions={len(self.open_positions)}"
            )
        except Exception as e:
            logger.warning(f"State load failed: {e}")

    def _in_kill_zone(self, ts: datetime, signal_score: float = 0.0) -> bool:
        """Return True if UTC time is within an active kill zone.

        PDF §8 rules:
          London: 02:00–05:00 UTC  (+1.5 confidence bonus, applied in scorer)
          NY AM:  07:00–10:00 UTC  (+1.5 confidence bonus)
          NY PM:  13:00–16:00 UTC  (continuation only, no bonus)
          Off-session: allowed only if confidence score >= 8.0
          Asian (22:00–02:00): accumulation only — no new trades
        """
        hour = ts.hour
        p = self.params
        london_start = getattr(p, "london_start", 2)
        london_end   = getattr(p, "london_end",   5)
        ny_start     = getattr(p, "ny_start",      7)
        ny_end       = getattr(p, "ny_end",       10)
        ny_pm_start  = getattr(p, "ny_pm_start",  13)
        ny_pm_end    = getattr(p, "ny_pm_end",    16)
        use_london   = getattr(p, "use_london",  True)
        use_ny       = getattr(p, "use_ny",      True)
        use_ny_pm    = getattr(p, "use_ny_pm",   True)

        # Asian session: 22:00–02:00 — never trade
        if hour >= 22 or hour < 2:
            return False

        if use_london and london_start <= hour < london_end:
            return True
        if use_ny and ny_start <= hour < ny_end:
            return True
        if use_ny_pm and ny_pm_start <= hour < ny_pm_end:
            return True

        # Off-session override: allowed if score >= 8.0 (PDF §8)
        from config import OFF_SESSION_MIN_SCORE
        if signal_score >= OFF_SESSION_MIN_SCORE:
            logger.info(f"Off-session entry allowed: score={signal_score:.1f} >= {OFF_SESSION_MIN_SCORE}")
            return True

        return False

    def _check_emergency_stop(self) -> bool:
        """Return True if daily drawdown exceeds limit — stops new entries."""
        dd = self._daily_pnl_pct()
        if dd <= -self.MAX_DAILY_DRAWDOWN_PCT:
            logger.warning(
                f"[EMERGENCY STOP] Daily drawdown {dd:.1f}% "
                f"exceeds limit {self.MAX_DAILY_DRAWDOWN_PCT:.0f}%"
            )
            self.telegram.send_raw(
                f"<b>🚨 EMERGENCY STOP TRIGGERED</b>\n"
                f"Daily drawdown: <code>{dd:.1f}%</code>\n"
                f"Limit: {self.MAX_DAILY_DRAWDOWN_PCT:.0f}%\n"
                f"<i>No new trades until next session reset.</i>",
                parse_mode="HTML",
            )
            return True
        return False

    # ── Data loading ───────────────────────────────────────────────────────

    def _load_data(self) -> Dict[str, object]:
        """Load all 6 timeframes. Uses synthetic data if no real data available."""
        data = {}
        for tf in self.TF_LIST:
            try:
                df = self.loader.load(self.pair, tf, synthetic_fallback=True)
                data[tf] = df
            except Exception as e:
                logger.warning(f"Failed to load {self.pair} {tf}: {e}")
        return data

    # ── MT5 integration (via mt5_bridge — works on Windows or Linux) ────────

    def _get_mt5(self):
        """Lazy-load the MT5 API via bridge (native or mt5linux)."""
        if not hasattr(self, "_mt5_api"):
            try:
                from execution.mt5_bridge import get_mt5
                self._mt5_api = get_mt5()
            except Exception:
                self._mt5_api = None
        return self._mt5_api

    def _mt5_get_balance(self) -> Optional[float]:
        mt5 = self._get_mt5()
        if mt5 is None:
            return None
        try:
            info = mt5.account_info()
            if info:
                return float(info.balance)
        except Exception as e:
            logger.error(f"MT5 balance error: {e}")
        return None

    def _mt5_open_trade(self, signal: ModuleSignal) -> bool:
        mt5 = self._get_mt5()
        if mt5 is None:
            logger.warning("MT5 unavailable — falling back to paper mode for this trade")
            return False
        try:
            direction = mt5.ORDER_TYPE_BUY if signal.direction == "long" else mt5.ORDER_TYPE_SELL
            lot = self._calc_lot_size(signal)
            tick = mt5.symbol_info_tick(self.pair)
            if tick is None:
                logger.error(f"Cannot get tick for {self.pair}")
                return False
            price = tick.ask if signal.direction == "long" else tick.bid
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.pair,
                "volume": lot,
                "type": direction,
                "price": price,
                "sl": signal.stop_loss,
                "tp": signal.take_profit,
                "deviation": 20,
                "magic": 20240101,
                "comment": f"ICT-{signal.module_name}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result is None:
                logger.error(f"MT5 order_send returned None: {mt5.last_error()}")
                return False
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"MT5 order placed: ticket={result.order} lot={lot:.2f} price={price:.5f}")
                return True
            logger.error(f"MT5 order failed: retcode={result.retcode} comment={result.comment}")
            return False
        except Exception as e:
            logger.error(f"MT5 open trade error: {e}")
            return False

    # Pip value per standard lot (1.0 lot) in USD
    _PIP_VALUE_PER_LOT: Dict[str, float] = {
        "XAUUSD": 10.0,    # $10 per pip ($0.01) per standard lot
        "BTCUSD": 1.0,     # $1 per point per 1 BTC
        "GBPUSD": 10.0,    # $10 per pip per standard lot
        "EURUSD": 10.0,    # $10 per pip per standard lot
    }

    def _calc_lot_size(self, signal: ModuleSignal) -> float:
        """Risk-based lot sizing: risk_pct% of balance per trade."""
        risk_amount = self.balance * (self.risk_pct / 100)
        sl_pips = abs(signal.entry_price - signal.stop_loss) / self.pip_size
        if sl_pips <= 0:
            return 0.01
        pip_value = self._PIP_VALUE_PER_LOT.get(self.pair.upper(), 10.0)
        lot = risk_amount / (sl_pips * pip_value)
        return round(max(0.01, min(lot, 10.0)), 2)

    # ── Position management ────────────────────────────────────────────────

    def _open_position(self, signal: ModuleSignal):
        """Open a new position for the given module signal."""
        if signal.module_name in self.open_positions:
            logger.debug(f"Already in trade for {signal.module_name} — skipping")
            return

        if len(self.open_positions) >= self.max_open_trades:
            logger.debug(f"Max open trades ({self.max_open_trades}) reached — skipping")
            return

        success = True
        if self.mode == "mt5":
            success = self._mt5_open_trade(signal)

        if success:
            self.open_positions[signal.module_name] = OpenPosition(
                module_name=signal.module_name,
                pair=self.pair,
                direction=signal.direction,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                rrr=signal.rrr,
                risk_pct=self.risk_pct,
                entry_model=signal.entry_model,
                opened_at=datetime.now(timezone.utc),
            )
            self.total_trades += 1
            logger.info(f"[{self.mode.upper()}] Opened {signal.direction} on {signal.module_name} "
                        f"entry={signal.entry_price:.4f} SL={signal.stop_loss:.4f} TP={signal.take_profit:.4f}")
            self.telegram.send_trade_alert(
                module=signal.module_name,
                direction=signal.direction,
                pair=self.pair,
                entry=signal.entry_price,
                sl=signal.stop_loss,
                tp=signal.take_profit,
                rrr=signal.rrr,
                balance=self.balance,
                entry_model=signal.entry_model,
            )
            self._save_state()

    def _check_positions(self, data: dict):
        """Check if any open positions have hit SL or TP."""
        latest_tf = "15M" if "15M" in data else "1H" if "1H" in data else "4H"
        if latest_tf not in data or len(data[latest_tf]) < 2:
            return

        df = data[latest_tf]
        current_high = float(df["high"].iloc[-1])
        current_low = float(df["low"].iloc[-1])
        current_price = float(df["close"].iloc[-1])

        to_close = []
        for name, pos in self.open_positions.items():
            risk = abs(pos.entry_price - pos.stop_loss)

            # PDF §11: Scale-out — move SL to breakeven after 1:1 is reached
            if not getattr(pos, "breakeven_set", False) and risk > 0:
                one_r_target = (pos.entry_price + risk) if pos.direction == "long" else (pos.entry_price - risk)
                if (pos.direction == "long" and current_high >= one_r_target) or \
                   (pos.direction == "short" and current_low <= one_r_target):
                    pos.stop_loss = pos.entry_price   # move SL to breakeven
                    pos.breakeven_set = True          # type: ignore[attr-defined]
                    logger.info(f"[{name}] SL moved to breakeven @ {pos.entry_price:.4f} (1:1 hit)")

            if pos.direction == "long":
                if current_low <= pos.stop_loss:
                    to_close.append((name, "loss", pos.stop_loss))
                elif current_high >= pos.take_profit:
                    to_close.append((name, "win", pos.take_profit))
                else:
                    pos.pnl_pips = (current_price - pos.entry_price) / self.pip_size
            else:
                if current_high >= pos.stop_loss:
                    to_close.append((name, "loss", pos.stop_loss))
                elif current_low <= pos.take_profit:
                    to_close.append((name, "win", pos.take_profit))
                else:
                    pos.pnl_pips = (pos.entry_price - current_price) / self.pip_size

        for name, outcome, exit_price in to_close:
            self._close_position(name, outcome, exit_price)

    def _close_position(self, module_name: str, outcome: str, exit_price: float):
        pos = self.open_positions.pop(module_name, None)
        if not pos:
            return
        if pos.direction == "long":
            pnl_pips = (exit_price - pos.entry_price) / self.pip_size
        else:
            pnl_pips = (pos.entry_price - exit_price) / self.pip_size

        risk_pips = abs(pos.entry_price - pos.stop_loss) / self.pip_size
        pnl_pct = (pnl_pips / risk_pips) * pos.risk_pct if risk_pips > 0 else 0.0
        self.balance *= (1 + pnl_pct / 100)

        self.closed_trades.append({
            "module": module_name, "outcome": outcome,
            "pnl_pips": pnl_pips, "pnl_pct": pnl_pct,
            "closed_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"[{self.mode.upper()}] Closed {module_name}: {outcome} "
                    f"{pnl_pips:+.1f}p ({pnl_pct:+.2f}%) balance=${self.balance:,.2f}")
        self.telegram.send_trade_closed(
            module=module_name, pair=self.pair,
            direction=pos.direction, outcome=outcome,
            pnl_pips=pnl_pips, pnl_pct=pnl_pct,
            balance=self.balance,
        )
        self._save_state()

    # ── Balance ────────────────────────────────────────────────────────────

    def get_balance(self) -> float:
        if self.mode == "mt5":
            mt5_bal = self._mt5_get_balance()
            if mt5_bal is not None:
                self.balance = mt5_bal
        return self.balance

    def _daily_pnl_pct(self) -> float:
        if self.daily_start_balance <= 0:
            return 0.0
        return (self.balance - self.daily_start_balance) / self.daily_start_balance * 100

    def _send_balance_report(self):
        self.telegram.send_balance(
            balance=self.get_balance(),
            daily_pnl=self._daily_pnl_pct(),
            open_trades=len(self.open_positions),
            total_trades=self.total_trades,
        )

    # ── Main loop ──────────────────────────────────────────────────────────

    def run(self, duration_hours: Optional[float] = None):
        """Start the live trading loop.
        duration_hours=None → run forever until KeyboardInterrupt.
        """
        end_time = None
        if duration_hours:
            end_time = datetime.now(timezone.utc) + timedelta(hours=duration_hours)

        mode_tag = f"[{self.mode.upper()} MODE]"
        logger.info(f"LiveController starting {mode_tag} | pair={self.pair} "
                    f"| balance=${self.balance:,.2f} | poll={self.poll_interval}s")
        self.telegram.send_raw(
            f"<b>🚀 AutoTrader Claude LIVE {mode_tag}</b>\n"
            f"Pair: {self.pair}\n"
            f"Balance: <code>${self.balance:,.2f}</code>\n"
            f"Modules: Position | Swing ✓ | Intraday | Scalp\n"
            f"Poll interval: {self.poll_interval}s",
            parse_mode="HTML"
        )

        last_balance_time = time.time()

        try:
            while True:
                if end_time and datetime.now(timezone.utc) >= end_time:
                    logger.info("Duration reached — stopping LiveController")
                    break

                # 1. Load latest data
                data = self._load_data()
                if not data:
                    logger.warning("No data loaded — retrying in 30s")
                    time.sleep(30)
                    continue

                # 2. Evaluate all 4 modules
                ts = datetime.now(timezone.utc)
                result = self.module_manager.evaluate_all(
                    data, pip_size=self.pip_size, timestamp=ts)

                # 3. Open new positions (skip if emergency stop or outside kill zone)
                emergency_stop = self._check_emergency_stop()
                if not emergency_stop:
                    for sig in result.valid_signals:
                        best_score = getattr(sig, "confidence_score", 0.0)
                        if self._in_kill_zone(ts, signal_score=best_score):
                            self._open_position(sig)
                        else:
                            logger.debug(
                                f"[{sig.module_name}] Outside kill zone at "
                                f"{ts.strftime('%H:%M')} UTC (score={best_score:.1f}) — skipped"
                            )

                # 4. Check open positions
                self._check_positions(data)

                # 5. Balance report every `balance_interval` seconds
                now = time.time()
                if now - last_balance_time >= self.balance_interval:
                    self._send_balance_report()
                    self.daily_start_balance = self.balance  # reset daily tracking
                    last_balance_time = now

                # 6. Free memory
                del data
                gc.collect()

                # Log status
                logger.info(
                    f"[{ts.strftime('%H:%M:%S')}] bal=${self.balance:,.2f} "
                    f"open={len(self.open_positions)} fired={result.total_modules_fired}/4"
                )
                time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            logger.info("LiveController stopped by user")
        finally:
            self._save_state()
            self.telegram.send_raw(
                f"<b>🛑 AutoTrader Claude STOPPED</b>\n"
                f"Final Balance: <code>${self.balance:,.2f}</code>\n"
                f"Total Trades: {self.total_trades}",
                parse_mode="HTML"
            )
