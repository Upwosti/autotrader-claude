"""
Telegram alert sender — enhanced with rich HTML reports for module performance,
trade alerts, evolution updates, and balance status.
Silently skips if not configured.
"""

import requests
from loguru import logger
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


class TelegramAlert:
    """Sends Telegram messages via the Bot HTTP API."""

    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self):
        self.enabled = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
        if not self.enabled:
            logger.debug("TelegramAlert disabled — TOKEN/CHAT_ID not set")

    def send(self, subject: str, body: str) -> bool:
        """Send a plain-text message (escapes for MarkdownV2)."""
        if not self.enabled:
            return False
        text = f"*{self._esc(subject)}*\n\n{self._esc(body)}"
        return self._post(text, parse_mode="MarkdownV2")

    def send_raw(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a pre-formatted HTML or MarkdownV2 message."""
        if not self.enabled:
            logger.info(f"[Telegram DISABLED] {text[:200]}")
            return False
        return self._post(text, parse_mode=parse_mode)

    def send_module_report(self, results: list, balance: float = 0.0) -> bool:
        """Send rich HTML report of all 4 module backtest results."""
        text = self._format_module_report(results, balance)
        if not self.enabled:
            logger.info("[Telegram DISABLED] Module report:\n" + text)
            return False
        return self._post(text, parse_mode="HTML")

    def send_trade_alert(self, module: str, direction: str, pair: str,
                         entry: float, sl: float, tp: float, rrr: float,
                         balance: float, entry_model: str = "") -> bool:
        """Send trade execution alert."""
        arrow = "🟢 LONG" if direction == "long" else "🔴 SHORT"
        model_tag = f"\nModel: <b>{entry_model.upper()}</b>" if entry_model else ""
        text = (
            f"<b>🚨 TRADE SIGNAL — {pair}</b>\n"
            f"Module: <b>{module.upper()}</b>{model_tag}\n"
            f"Direction: {arrow}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Entry:  <code>{entry:.4f}</code>\n"
            f"SL:     <code>{sl:.4f}</code>\n"
            f"TP:     <code>{tp:.4f}</code>\n"
            f"RRR:    <b>{rrr:.1f}:1</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Balance: <code>${balance:,.2f}</code>"
        )
        if not self.enabled:
            logger.info(f"[Telegram DISABLED] Trade: {module} {direction} {pair} "
                        f"entry={entry:.4f} SL={sl:.4f} TP={tp:.4f}")
            return False
        return self._post(text, parse_mode="HTML")

    def send_trade_closed(self, module: str, pair: str, direction: str,
                          outcome: str, pnl_pips: float, pnl_pct: float,
                          balance: float) -> bool:
        """Send trade close notification."""
        emoji = "✅" if outcome == "win" else "❌"
        text = (
            f"<b>{emoji} TRADE CLOSED — {pair}</b>\n"
            f"Module: {module.upper()} | {direction.upper()}\n"
            f"Result: <b>{outcome.upper()}</b>\n"
            f"P&amp;L: <code>{pnl_pips:+.1f} pips ({pnl_pct:+.2f}%)</code>\n"
            f"Balance: <code>${balance:,.2f}</code>"
        )
        if not self.enabled:
            logger.info(f"[Telegram DISABLED] Trade closed: {outcome} {pnl_pips:+.1f}p")
            return False
        return self._post(text, parse_mode="HTML")

    def send_evolution_update(self, module: str, iteration: int,
                               win_rate: float, avg_rrr: float, pf: float,
                               dd: float, ret: float, trades: int,
                               status: str) -> bool:
        """Send evolution progress update for a module."""
        wr_ok = "✅" if win_rate >= 0.50 else "❌"
        rr_ok = "✅" if avg_rrr >= 2.0 else "❌"
        pf_ok = "✅" if pf >= 1.5 else "❌"
        text = (
            f"<b>🤖 EVOLVING: {module.upper()}</b>\n"
            f"Iteration <b>#{iteration}</b> — {status}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Win Rate:      <b>{win_rate:.1%}</b> {wr_ok} (target ≥50%)\n"
            f"Avg RRR:       <b>{avg_rrr:.2f}</b> {rr_ok} (target ≥2.0)\n"
            f"Profit Factor: <b>{pf:.2f}</b> {pf_ok} (target ≥1.5)\n"
            f"Max Drawdown:  {dd:.1f}%\n"
            f"Total Return:  {ret:+.1f}%\n"
            f"Trades:        {trades}"
        )
        if not self.enabled:
            logger.info(f"[Telegram DISABLED] Evolution: {module} #{iteration} "
                        f"WR={win_rate:.1%} RRR={avg_rrr:.2f} PF={pf:.2f}")
            return False
        return self._post(text, parse_mode="HTML")

    def send_balance(self, balance: float, daily_pnl: float,
                     open_trades: int, total_trades: int) -> bool:
        """Send account balance status."""
        arrow = "📈" if daily_pnl >= 0 else "📉"
        text = (
            f"<b>💰 ACCOUNT STATUS</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Balance:      <code>${balance:,.2f}</code>\n"
            f"Daily P&amp;L: {arrow} <b>{daily_pnl:+.2f}%</b>\n"
            f"Open Trades:  {open_trades}\n"
            f"Total Trades: {total_trades}"
        )
        if not self.enabled:
            logger.info(f"[Telegram DISABLED] Balance: ${balance:,.2f} | PnL: {daily_pnl:+.2f}%")
            return False
        return self._post(text, parse_mode="HTML")

    def send_all_profitable(self, results: list, balance: float) -> bool:
        """Send celebration message when all modules are profitable."""
        text = (
            f"<b>🏆 ALL MODULES NOW PROFITABLE!</b>\n"
            f"Balance: <code>${balance:,.2f}</code>\n"
            f"━━━━━━━━━━━━━━━\n"
        )
        for r in results:
            wr = getattr(r, "win_rate", 0)
            rrr = getattr(r, "avg_rrr", 0)
            name = getattr(r, "module_name", "?").upper()
            text += f"✅ {name}: WR={wr:.1%} | RRR={rrr:.2f}\n"
        text += "\n<i>Bot is now live — monitoring all 4 timeframe modules.</i>"
        if not self.enabled:
            logger.info("[Telegram DISABLED] All modules profitable!")
            return False
        return self._post(text, parse_mode="HTML")

    # ── Formatters ────────────────────────────────────────────────────────

    def _format_module_report(self, results: list, balance: float) -> str:
        profitable = [r for r in results if getattr(r, "is_profitable", False)]
        header = (f"✅ ALL {len(results)} MODULES PROFITABLE"
                  if len(profitable) == len(results)
                  else f"⚠️ {len(profitable)}/{len(results)} MODULES PROFITABLE")
        lines = [
            "<b>📊 MODULE BACKTEST REPORT</b>",
            f"<i>{header}</i>",
            f"Balance: <code>${balance:,.2f}</code>",
            "━━━━━━━━━━━━━━━━━━━━",
        ]
        for r in results:
            wr = getattr(r, "win_rate", 0)
            rrr = getattr(r, "avg_rrr", 0)
            pf = getattr(r, "profit_factor", 0)
            dd = getattr(r, "max_drawdown_pct", 0)
            ret = getattr(r, "total_return_pct", 0)
            trades = getattr(r, "total_trades", 0)
            exp = getattr(r, "expectancy", 0)
            ltf1 = getattr(r, "ltf1", "?")
            ltf2 = getattr(r, "ltf2", "?")
            ok = getattr(r, "is_profitable", False)
            status = "✅ PASS" if ok else "❌ NEEDS WORK"
            name = getattr(r, "module_name", "?").upper()
            lines += [
                f"\n<b>{status} [{name}]</b>  <code>{ltf1}→{ltf2}</code>",
                f"  Win Rate: <b>{wr:.1%}</b>  |  RRR: <b>{rrr:.2f}:1</b>",
                f"  PF: {pf:.2f}  |  DD: {dd:.1f}%  |  Return: {ret:+.1f}%",
                f"  Expectancy: {exp:+.1f} pips  |  Trades: {trades}",
            ]
        return "\n".join(lines)

    # ── HTTP ──────────────────────────────────────────────────────────────

    def _post(self, text: str, parse_mode: str = "HTML") -> bool:
        url = self.BASE_URL.format(token=TELEGRAM_BOT_TOKEN)
        # Telegram limit: 4096 chars
        if len(text) > 4000:
            text = text[:3990] + "\n...(truncated)"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                logger.debug(f"Telegram OK ({len(text)} chars)")
                return True
            logger.warning(f"Telegram {resp.status_code}: {resp.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"Telegram failed: {e}")
            return False

    def _esc(self, text: str) -> str:
        for ch in r"\_*[]()~`>#+-=|{}.!":
            text = text.replace(ch, f"\\{ch}")
        return text
