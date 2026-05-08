"""
Telegram Bot — sends rich alerts AND handles incoming commands.
Commands: /status /trades /report /evolution /pause /resume /help
"""

import threading
from datetime import datetime
from loguru import logger
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

try:
    from telegram import Bot, ParseMode
    from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
    TG_OK = True
except ImportError:
    TG_OK = False
    logger.warning("python-telegram-bot not installed")


# ── shared state the handlers can read ──────────────────────────────────────
_state = {
    "db":           None,
    "paused":       False,
    "active_trade": None,
    "last_result":  None,
}


class TelegramAlert:
    """Sends alerts and optionally runs a polling listener for /commands."""

    def __init__(self, db=None):
        self.enabled = bool(TG_OK and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
        self.bot     = Bot(token=TELEGRAM_BOT_TOKEN) if self.enabled else None
        self._updater = None
        _state["db"] = db
        if not self.enabled:
            logger.debug("TelegramAlert disabled — TOKEN/CHAT_ID missing")

    # ── Send helpers ─────────────────────────────────────────────────────────

    def send(self, subject: str, body: str) -> bool:
        return self._send_md(f"*{_esc(subject)}*\n\n{_esc(body)}")

    def send_trade_opened(self, trade: dict) -> bool:
        d = trade.get("direction", "").upper()
        sym = trade.get("symbol", "")
        icon = "🟢" if d == "BUY" else "🔴"
        msg = (
            f"{icon} *TRADE OPENED — {sym}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 Direction : `{d}`\n"
            f"💵 Entry     : `{trade.get('entry', 0):.5f}`\n"
            f"🛑 Stop Loss : `{trade.get('sl', 0):.5f}`\n"
            f"🎯 Take Profit: `{trade.get('tp', 0):.5f}`\n"
            f"📊 Lots      : `{trade.get('lot', 0):.2f}`\n"
            f"⭐ Confidence: `{trade.get('confidence', 0)}/7`\n"
            f"⏰ Time      : `{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC`"
        )
        return self._send_md(msg)

    def send_trade_closed(self, trade: dict) -> bool:
        outcome = trade.get("outcome", "unknown").upper()
        icon = "✅" if outcome == "WIN" else "❌"
        pnl = trade.get("pnl_pct", 0)
        pnl_sign = "+" if pnl >= 0 else ""
        msg = (
            f"{icon} *TRADE CLOSED — {trade.get('symbol', '')}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Outcome   : `{outcome}`\n"
            f"💰 P&L       : `{pnl_sign}{pnl:.2f}%`\n"
            f"📐 RRR       : `{trade.get('rrr', 0):.2f}`\n"
            f"⏱ Duration  : `{trade.get('duration', 'N/A')}`"
        )
        return self._send_md(msg)

    def send_performance_report(self, stats: dict) -> bool:
        wr  = stats.get("win_rate", 0)
        rrr = stats.get("avg_rrr", 0)
        dd  = stats.get("max_dd", 0)
        ret = stats.get("total_return", 0)
        n   = stats.get("total_trades", 0)
        ver = stats.get("version", 1)
        bar = _win_bar(wr)
        msg = (
            f"📊 *PERFORMANCE REPORT*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 Version     : `v{ver}`\n"
            f"📈 Win Rate    : `{wr:.1%}`  {bar}\n"
            f"⚖️ Avg RRR     : `{rrr:.2f}`\n"
            f"💹 Total Return: `{ret:+.2f}%`\n"
            f"📉 Max DD      : `{dd:.2f}%`\n"
            f"🔢 Trades      : `{n}`\n"
            f"⏰ Generated   : `{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC`\n\n"
            f"_Reply /trades for recent trade list_"
        )
        return self._send_md(msg)

    def send_evolution_update(self, iteration: int, param: str,
                              old_val, new_val, wr_before: float,
                              wr_after: float, kept: bool) -> bool:
        icon   = "✅ KEPT" if kept else "↩️ REVERTED"
        arrow  = "🔼" if wr_after > wr_before else "🔽"
        msg = (
            f"🧬 *EVOLUTION — Iter {iteration}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔧 Parameter : `{param}`\n"
            f"📦 Change    : `{old_val}` → `{new_val}`\n"
            f"📊 Win Rate  : `{wr_before:.1%}` {arrow} `{wr_after:.1%}`\n"
            f"🏷 Decision  : `{icon}`"
        )
        return self._send_md(msg)

    def send_ftmo_alert(self, reason: str, equity: float, limit_pct: float) -> bool:
        msg = (
            f"🚨 *FTMO LIMIT HIT — TRADING HALTED*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ Reason  : `{_esc(reason)}`\n"
            f"💵 Equity  : `${equity:,.2f}`\n"
            f"🛑 Limit   : `{limit_pct}%`\n"
            f"⏰ Time    : `{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC`\n\n"
            f"_All positions closed\\. Manual review required\\._"
        )
        return self._send_md(msg)

    # ── Bot polling (interactive replies) ───────────────────────────────────

    def start_listening(self, db=None):
        """Start polling for /commands in a background thread."""
        if not self.enabled or not TG_OK:
            return
        if db:
            _state["db"] = db
        updater = Updater(token=TELEGRAM_BOT_TOKEN, use_context=True)
        dp = updater.dispatcher
        dp.add_handler(CommandHandler("start",     _cmd_start))
        dp.add_handler(CommandHandler("help",      _cmd_help))
        dp.add_handler(CommandHandler("status",    _cmd_status))
        dp.add_handler(CommandHandler("trades",    _cmd_trades))
        dp.add_handler(CommandHandler("report",    _cmd_report))
        dp.add_handler(CommandHandler("evolution", _cmd_evolution))
        dp.add_handler(CommandHandler("pause",     _cmd_pause))
        dp.add_handler(CommandHandler("resume",    _cmd_resume))
        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, _cmd_unknown))

        self._updater = updater
        t = threading.Thread(target=updater.start_polling, daemon=True)
        t.start()
        logger.info("Telegram command listener started")

    def stop_listening(self):
        if self._updater:
            self._updater.stop()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _send_md(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=text,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return True
        except Exception as e:
            # fallback: plain text
            try:
                plain = text.replace("*", "").replace("`", "").replace("_", "").replace("\\", "")
                self.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=plain)
                return True
            except Exception as e2:
                logger.error(f"Telegram send failed: {e2}")
                return False


# ── Command handlers ─────────────────────────────────────────────────────────

def _reply(update, text: str):
    try:
        update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception:
        plain = text.replace("*","").replace("`","").replace("_","").replace("\\","")
        update.message.reply_text(plain)


def _cmd_start(update, ctx):
    _reply(update,
        "🤖 *AutoTrader Claude* is online\\!\n\n"
        "Use /help to see available commands\\."
    )


def _cmd_help(update, ctx):
    _reply(update,
        "📋 *Available Commands*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/status — System status \\+ active trade\n"
        "/trades — Last 5 closed trades\n"
        "/report — Full performance report\n"
        "/evolution — Latest evolution history\n"
        "/pause — Pause new trade entries\n"
        "/resume — Resume trading\n"
        "/help — This message"
    )


def _cmd_status(update, ctx):
    db = _state.get("db")
    trade = _state.get("active_trade")
    paused = _state.get("paused", False)

    status_icon = "⏸️ PAUSED" if paused else "✅ ACTIVE"
    trade_line  = "None"
    if trade:
        trade_line = f"{trade.get('direction','').upper()} {trade.get('symbol','')} @ {trade.get('entry',0):.5f}"

    total = 0
    wr    = 0.0
    if db:
        total = db.get_total_trades()
        trades = db.select("trades", limit=500)
        wins   = sum(1 for t in trades if t.get("outcome") == "win")
        wr     = wins / max(len(trades), 1)

    _reply(update,
        f"📡 *SYSTEM STATUS*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔄 State      : `{_esc(status_icon)}`\n"
        f"💼 Active     : `{_esc(trade_line)}`\n"
        f"📊 Win Rate   : `{wr:.1%}`\n"
        f"🔢 Trades     : `{total}`\n"
        f"⏰ Time \\(UTC\\): `{datetime.utcnow().strftime('%H:%M:%S')}`"
    )


def _cmd_trades(update, ctx):
    db = _state.get("db")
    if not db:
        _reply(update, "⚠️ Database not connected\\."); return

    rows = db.select("trades", limit=500)
    rows = sorted(rows, key=lambda r: r.get("_inserted_at", ""), reverse=True)[:5]
    if not rows:
        _reply(update, "📭 No trades recorded yet\\."); return

    lines = ["📋 *LAST 5 TRADES*\n━━━━━━━━━━━━━━━━━━━━"]
    for t in rows:
        icon = "✅" if t.get("outcome") == "win" else "❌"
        pnl  = t.get("pnl_pct", 0)
        sign = "+" if pnl >= 0 else ""
        lines.append(
            f"{icon} `{t.get('pair','?')}` {t.get('direction','').upper()} "
            f"RRR:`{t.get('rrr_achieved', t.get('rrr', 0)):.2f}` "
            f"P&L:`{sign}{pnl:.2f}%`"
        )
    _reply(update, "\n".join(lines))


def _cmd_report(update, ctx):
    db = _state.get("db")
    if not db:
        _reply(update, "⚠️ Database not connected\\."); return

    trades = db.select("trades", limit=2000)
    n    = len(trades)
    wins = sum(1 for t in trades if t.get("outcome") == "win")
    wr   = wins / max(n, 1)
    rrrs = [t.get("rrr_achieved", t.get("rrr", 0)) for t in trades if t.get("rrr_achieved") or t.get("rrr")]
    avg_rrr = sum(rrrs) / max(len(rrrs), 1)

    pair_stats = {}
    for t in trades:
        p = t.get("pair", "?")
        pair_stats.setdefault(p, {"w": 0, "n": 0})
        pair_stats[p]["n"] += 1
        if t.get("outcome") == "win":
            pair_stats[p]["w"] += 1

    pair_lines = []
    for p, s in sorted(pair_stats.items()):
        pw = s["w"] / max(s["n"], 1)
        pair_lines.append(f"  `{p}` — {pw:.0%} \\({s['n']} trades\\)")

    bar = _win_bar(wr)
    _reply(update,
        f"📊 *PERFORMANCE REPORT*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Win Rate  : `{wr:.1%}` {bar}\n"
        f"⚖️ Avg RRR  : `{avg_rrr:.2f}`\n"
        f"🔢 Trades   : `{n}`\n\n"
        f"*By Pair:*\n" + "\n".join(pair_lines)
    )


def _cmd_evolution(update, ctx):
    db = _state.get("db")
    if not db:
        _reply(update, "⚠️ Database not connected\\."); return

    rows = db.select("evolution_log", limit=500)
    rows = sorted(rows, key=lambda r: r.get("iteration", 0), reverse=True)[:5]
    if not rows:
        _reply(update, "📭 No evolution history yet\\."); return

    lines = ["🧬 *LAST 5 EVOLUTION STEPS*\n━━━━━━━━━━━━━━━━━━━━"]
    for e in rows:
        icon = "✅" if e.get("decision") == "kept" else "↩️"
        lines.append(
            f"{icon} `{e.get('param_changed','?')}` "
            f"{_esc(str(e.get('old_value','')))}→{_esc(str(e.get('new_value','')))} "
            f"WR: `{e.get('win_rate_before',0):.1%}`→`{e.get('win_rate_after',0):.1%}`"
        )
    _reply(update, "\n".join(lines))


def _cmd_pause(update, ctx):
    _state["paused"] = True
    _reply(update, "⏸️ *Trading PAUSED*\\. Use /resume to restart\\.")


def _cmd_resume(update, ctx):
    _state["paused"] = False
    _reply(update, "▶️ *Trading RESUMED*\\.")


def _cmd_unknown(update, ctx):
    _reply(update, "❓ Unknown command\\. Use /help to see options\\.")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    special = r"\_*[]()~`>#+-=|{}.!"
    for ch in special:
        text = str(text).replace(ch, f"\\{ch}")
    return text


def _win_bar(wr: float) -> str:
    filled = int(wr * 10)
    return "▓" * filled + "░" * (10 - filled)


def is_paused() -> bool:
    return _state.get("paused", False)


def set_active_trade(trade: dict):
    _state["active_trade"] = trade
