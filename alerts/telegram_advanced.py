"""TelegramAdvanced — rich formatted alerts for all system events."""

from datetime import datetime, timezone
from typing import List, Optional
from loguru import logger

from alerts.telegram_bot import TelegramAlert


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _block(alert_type: str, lines: List[str]) -> str:
    body = "\n".join(lines)
    return (
        f"=== {alert_type} ===\n"
        f"{body}\n"
        f"Time: {_utc_now()}\n"
        f"==================="
    )


class TelegramAdvanced:
    """Rich formatted Telegram alerts wrapping TelegramAlert."""

    def __init__(self, db=None):
        self._tg = TelegramAlert()
        self._db = db

    # ------------------------------------------------------------------ #
    #  Trade lifecycle
    # ------------------------------------------------------------------ #

    def trade_entry(
        self,
        pair: str,
        direction: str,
        entry: float,
        sl: float,
        tp: float,
        rrr: float,
        confluence: float,
        session: str,
    ):
        body = _block(
            "TRADE ENTRY",
            [
                f"Pair:        {pair}",
                f"Action:      {direction.upper()} @ {entry:.5f}",
                f"Details:     SL={sl:.5f}  TP={tp:.5f}  RRR={rrr:.2f}x",
                f"             Confluence={confluence:.1f}%  Session={session}",
            ],
        )
        self._tg.send("TRADE ENTRY", body)

    def trade_exit(
        self,
        pair: str,
        direction: str,
        exit_price: float,
        pnl_pct: float,
        rrr_achieved: float,
        hold_bars: int,
    ):
        emoji = "WIN" if pnl_pct >= 0 else "LOSS"
        body = _block(
            "TRADE EXIT",
            [
                f"Pair:        {pair}",
                f"Action:      {direction.upper()} closed @ {exit_price:.5f}",
                f"Details:     PnL={pnl_pct:+.2f}%  RRR={rrr_achieved:.2f}x  Bars={hold_bars}  [{emoji}]",
            ],
        )
        self._tg.send("TRADE EXIT", body)

    # ------------------------------------------------------------------ #
    #  Evolution milestones
    # ------------------------------------------------------------------ #

    def new_best_wr(self, pair: str, old_wr: float, new_wr: float, iteration: int):
        body = _block(
            "NEW BEST WR ACHIEVED",
            [
                f"Pair:        {pair}",
                f"Action:      Win rate improved",
                f"Details:     {old_wr:.1f}% -> {new_wr:.1f}%  (iteration {iteration})",
            ],
        )
        self._tg.send("NEW BEST WR", body)

    def pair_ranking_changed(self, old_rank: List, new_rank: List):
        old_str = ", ".join(str(p) for p in old_rank[:5])
        new_str = ", ".join(str(p) for p in new_rank[:5])
        body = _block(
            "PAIR RANKING UPDATE",
            [
                f"Pair:        All pairs",
                f"Action:      Rankings reordered",
                f"Details:     Before: [{old_str}]",
                f"             After:  [{new_str}]",
            ],
        )
        self._tg.send("PAIR RANKING UPDATE", body)

    def ml_retrained(
        self,
        model_name: str,
        n_trades: int,
        accuracy: float,
        lift: float,
    ):
        body = _block(
            "ML MODEL RETRAINED",
            [
                f"Pair:        {model_name}",
                f"Action:      Model retrained on {n_trades} trades",
                f"Details:     Accuracy={accuracy:.1f}%  Lift={lift:+.1f}%",
            ],
        )
        self._tg.send("ML MODEL RETRAINED", body)

    def strategy_weight_updated(
        self,
        pair: str,
        strategy: str,
        old_weight: float,
        new_weight: float,
    ):
        body = _block(
            "STRATEGY WEIGHT UPDATE",
            [
                f"Pair:        {pair}",
                f"Action:      Weight adjusted for {strategy}",
                f"Details:     {old_weight:.3f} -> {new_weight:.3f}",
            ],
        )
        self._tg.send("STRATEGY WEIGHT UPDATE", body)

    # ------------------------------------------------------------------ #
    #  Risk & protection
    # ------------------------------------------------------------------ #

    def risk_limit_triggered(
        self,
        limit_type: str,
        current_value: float,
        threshold: float,
    ):
        body = _block(
            "RISK LIMIT TRIGGERED",
            [
                f"Pair:        N/A",
                f"Action:      {limit_type} limit breached",
                f"Details:     Current={current_value:.2f}  Threshold={threshold:.2f}",
            ],
        )
        self._tg.send("RISK LIMIT TRIGGERED", body)

    def equity_protection_activated(
        self,
        reason: str,
        pause_hours: float,
        equity_dd_pct: float,
    ):
        body = _block(
            "EQUITY PROTECTION ACTIVATED",
            [
                f"Pair:        All pairs — TRADING PAUSED",
                f"Action:      Equity protection engaged",
                f"Details:     Reason={reason}  DD={equity_dd_pct:.2f}%  Pause={pause_hours:.1f}h",
            ],
        )
        self._tg.send("EQUITY PROTECTION ACTIVATED", body)

    def equity_protection_resumed(self, equity_current: float):
        body = _block(
            "EQUITY PROTECTION RESUMED",
            [
                f"Pair:        All pairs — TRADING RESUMED",
                f"Action:      Equity protection lifted",
                f"Details:     Current equity level: {equity_current:.2f}",
            ],
        )
        self._tg.send("EQUITY PROTECTION RESUMED", body)

    # ------------------------------------------------------------------ #
    #  FTMO simulation
    # ------------------------------------------------------------------ #

    def ftmo_simulation_passed(
        self,
        pass_rate: float,
        profit_pct: float,
        max_dd: float,
    ):
        body = _block(
            "FTMO SIMULATION PASSED",
            [
                f"Pair:        Simulated portfolio",
                f"Action:      FTMO challenge criteria met",
                f"Details:     PassRate={pass_rate:.1f}%  Profit={profit_pct:.2f}%  MaxDD={max_dd:.2f}%",
            ],
        )
        self._tg.send("FTMO SIMULATION PASSED", body)

    # ------------------------------------------------------------------ #
    #  Periodic summaries
    # ------------------------------------------------------------------ #

    def weekly_summary(
        self,
        iteration: int,
        xau_wr: float,
        best_pair: str,
        skills_n: int,
        healer_fixes: int,
    ):
        body = _block(
            "WEEKLY PERFORMANCE SUMMARY",
            [
                f"Pair:        {best_pair} (best this week)",
                f"Action:      Weekly review complete",
                f"Details:     Iteration={iteration}  XAUUSD WR={xau_wr:.1f}%",
                f"             Skills learned={skills_n}  Healer fixes={healer_fixes}",
            ],
        )
        self._tg.send("WEEKLY SUMMARY", body)

    def monthly_summary(
        self,
        month: str,
        return_pct: float,
        sharpe: float,
        profit_factor: float,
        best_pair: str,
    ):
        body = _block(
            "MONTHLY PERFORMANCE SUMMARY",
            [
                f"Pair:        {best_pair} (best this month)",
                f"Action:      Monthly review complete — {month}",
                f"Details:     Return={return_pct:+.2f}%  Sharpe={sharpe:.2f}  PF={profit_factor:.2f}",
            ],
        )
        self._tg.send("MONTHLY SUMMARY", body)

    # ------------------------------------------------------------------ #
    #  System health
    # ------------------------------------------------------------------ #

    def system_health(
        self,
        cpu_pct: float,
        ram_pct: float,
        disk_pct: float,
        postgres_ok: bool,
        redis_ok: bool,
        supabase_ok: bool,
    ):
        def status(flag: bool) -> str:
            return "OK" if flag else "FAIL"

        body = _block(
            "SYSTEM HEALTH",
            [
                f"Pair:        N/A",
                f"Action:      Scheduled health check",
                f"Details:     CPU={cpu_pct:.1f}%  RAM={ram_pct:.1f}%  Disk={disk_pct:.1f}%",
                f"             PostgreSQL={status(postgres_ok)}  Redis={status(redis_ok)}  Supabase={status(supabase_ok)}",
            ],
        )
        self._tg.send("SYSTEM HEALTH", body)

    # ------------------------------------------------------------------ #
    #  Special events
    # ------------------------------------------------------------------ #

    def institutional_upgrade_complete(
        self,
        pairs: int,
        ml_models: int,
        iteration: int,
    ):
        body = (
            "=== INSTITUTIONAL UPGRADE COMPLETE ===\n"
            "*** MILESTONE ACHIEVED ***\n"
            f"Pair:        {pairs} active trading pairs\n"
            f"Action:      Full institutional-grade system online\n"
            f"Details:     ML models={ml_models}  Iteration={iteration}\n"
            f"             All modules verified and operational\n"
            f"Time: {_utc_now()}\n"
            "======================================="
        )
        self._tg.send("INSTITUTIONAL UPGRADE COMPLETE", body)

    # ------------------------------------------------------------------ #
    #  Passthrough
    # ------------------------------------------------------------------ #

    def send_raw(self, subject: str, body: str):
        """Direct passthrough to the underlying TelegramAlert."""
        self._tg.send(subject, body)
