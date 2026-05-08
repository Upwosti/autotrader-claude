"""
Email Alert — sends rich HTML emails with performance tables, trade logs, evolution stats.
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from loguru import logger

from config import (
    SMTP_HOST as EMAIL_SMTP_HOST,
    SMTP_PORT as EMAIL_SMTP_PORT,
    EMAIL_SENDER as EMAIL_ADDRESS,
    EMAIL_PASSWORD,
    EMAIL_RECEIVER as EMAIL_RECIPIENT,
)

# ── HTML template ─────────────────────────────────────────────────────────────

_CSS = """
body{margin:0;padding:0;background:#0d1117;font-family:'Segoe UI',Arial,sans-serif;color:#c9d1d9}
.wrap{max-width:680px;margin:0 auto;background:#0d1117}
.header{background:linear-gradient(135deg,#1f6feb 0%,#388bfd 100%);padding:28px 32px;border-radius:8px 8px 0 0}
.header h1{margin:0;font-size:22px;color:#fff;letter-spacing:0.5px}
.header p{margin:6px 0 0;color:#cce0ff;font-size:13px}
.body{padding:24px 32px}
.stat-row{display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap}
.stat{flex:1;min-width:120px;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 16px;text-align:center}
.stat .label{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:0.05em}
.stat .value{font-size:26px;font-weight:700;color:#58a6ff;margin-top:4px}
.stat .sub{font-size:11px;color:#8b949e;margin-top:2px}
.stat.green .value{color:#3fb950}
.stat.red .value{color:#f85149}
.stat.yellow .value{color:#d29922}
h2{font-size:14px;color:#f0f6fc;border-bottom:1px solid #30363d;padding-bottom:8px;margin:24px 0 12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 10px;background:#161b22;color:#8b949e;font-weight:600;font-size:11px;text-transform:uppercase}
td{padding:8px 10px;border-bottom:1px solid #21262d;color:#c9d1d9}
tr:hover td{background:#161b22}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.win{background:#1f3d2b;color:#3fb950}
.loss{background:#3d1f1f;color:#f85149}
.kept{background:#1f2d3d;color:#58a6ff}
.reverted{background:#2d2010;color:#d29922}
.bar-track{background:#21262d;border-radius:4px;height:8px;width:100%;margin-top:4px}
.bar-fill{background:#3fb950;border-radius:4px;height:8px}
.footer{padding:16px 32px;text-align:center;color:#484f58;font-size:11px;border-top:1px solid #21262d}
.alert-box{background:#1f2d3d;border-left:4px solid #1f6feb;border-radius:4px;padding:12px 16px;margin:12px 0;font-size:13px}
.alert-box.warn{background:#2d2010;border-color:#d29922}
.alert-box.danger{background:#3d1f1f;border-color:#f85149}
"""


def _html_wrap(subject: str, content: str) -> str:
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>{_CSS}</style></head>
<body><div class="wrap">
<div class="header">
  <h1>&#129302; AutoTrader Claude</h1>
  <p>{subject} &nbsp;&bull;&nbsp; {ts}</p>
</div>
<div class="body">{content}</div>
<div class="footer">AutoTrader Claude &bull; Evolutionary ICT Strategy System</div>
</div></body></html>"""


class EmailAlert:
    def __init__(self):
        self.enabled = bool(EMAIL_ADDRESS and EMAIL_PASSWORD and EMAIL_RECIPIENT)
        if not self.enabled:
            logger.debug("EmailAlert disabled — credentials not set")

    # ── Public send methods ──────────────────────────────────────────────────

    def send(self, subject: str, body: str) -> bool:
        """Plain/fallback send — converts plain text to simple HTML."""
        html = _html_wrap(subject, f"<pre style='color:#c9d1d9;font-size:13px'>{body}</pre>")
        return self._send(subject, body, html)

    def send_trade_opened(self, trade: dict) -> bool:
        d   = trade.get("direction", "").upper()
        sym = trade.get("symbol", "")
        color = "#3fb950" if d == "BUY" else "#f85149"
        content = f"""
<div class="alert-box">
  <b style="color:{color}">&#9679; {d} {sym} — Trade Opened</b>
</div>
<div class="stat-row">
  <div class="stat"><div class="label">Entry</div><div class="value" style="font-size:18px">{trade.get('entry',0):.5f}</div></div>
  <div class="stat red"><div class="label">Stop Loss</div><div class="value" style="font-size:18px">{trade.get('sl',0):.5f}</div></div>
  <div class="stat green"><div class="label">Take Profit</div><div class="value" style="font-size:18px">{trade.get('tp',0):.5f}</div></div>
  <div class="stat"><div class="label">Lots</div><div class="value" style="font-size:18px">{trade.get('lot',0):.2f}</div></div>
  <div class="stat"><div class="label">Confidence</div><div class="value" style="font-size:18px">{trade.get('confidence',0)}/7</div></div>
</div>"""
        plain = f"TRADE OPENED: {d} {sym} @ {trade.get('entry',0)} SL={trade.get('sl',0)} TP={trade.get('tp',0)}"
        return self._send(f"Trade Opened — {d} {sym}", plain, _html_wrap(f"Trade Opened — {d} {sym}", content))

    def send_trade_closed(self, trade: dict) -> bool:
        outcome = trade.get("outcome", "unknown")
        pnl     = trade.get("pnl_pct", 0)
        sym     = trade.get("symbol", trade.get("pair", ""))
        stat_cls = "green" if outcome == "win" else "red"
        sign     = "+" if pnl >= 0 else ""
        content = f"""
<div class="alert-box {'warn' if outcome != 'win' else ''}">
  <b>{'&#9989;' if outcome=='win' else '&#10060;'} {sym} Trade {'Won' if outcome=='win' else 'Lost'}</b>
</div>
<div class="stat-row">
  <div class="stat {stat_cls}"><div class="label">P&amp;L</div><div class="value">{sign}{pnl:.2f}%</div></div>
  <div class="stat"><div class="label">RRR</div><div class="value">{trade.get('rrr',0):.2f}</div></div>
  <div class="stat"><div class="label">Direction</div><div class="value" style="font-size:16px">{trade.get('direction','').upper()}</div></div>
</div>"""
        plain = f"TRADE CLOSED: {sym} {outcome.upper()} P&L={sign}{pnl:.2f}%"
        return self._send(f"Trade Closed — {outcome.upper()} {sym}", plain,
                          _html_wrap(f"Trade Closed — {outcome.upper()} {sym}", content))

    def send_performance_report(self, stats: dict, trades: list = None) -> bool:
        wr  = stats.get("win_rate", 0)
        rrr = stats.get("avg_rrr", 0)
        dd  = stats.get("max_dd", 0)
        ret = stats.get("total_return", 0)
        n   = stats.get("total_trades", 0)
        ver = stats.get("version", 1)
        bar_w = int(wr * 100)
        sign  = "+" if ret >= 0 else ""
        ret_cls = "green" if ret >= 0 else "red"

        content = f"""
<div class="stat-row">
  <div class="stat green"><div class="label">Win Rate</div><div class="value">{wr:.1%}</div>
    <div class="bar-track"><div class="bar-fill" style="width:{bar_w}%"></div></div></div>
  <div class="stat"><div class="label">Avg RRR</div><div class="value">{rrr:.2f}</div></div>
  <div class="stat {ret_cls}"><div class="label">Total Return</div><div class="value">{sign}{ret:.2f}%</div></div>
  <div class="stat red"><div class="label">Max Drawdown</div><div class="value">{dd:.2f}%</div></div>
  <div class="stat"><div class="label">Trades</div><div class="value">{n}</div></div>
  <div class="stat"><div class="label">Version</div><div class="value" style="font-size:18px">v{ver}</div></div>
</div>"""

        if trades:
            recent = sorted(trades, key=lambda t: t.get("_inserted_at",""), reverse=True)[:10]
            content += "<h2>Recent Trades</h2><table><thead><tr><th>Pair</th><th>Dir</th><th>RRR</th><th>P&L</th><th>Session</th><th>Result</th></tr></thead><tbody>"
            for t in recent:
                pnl    = t.get("pnl_pct", 0)
                s_pnl  = f"+{pnl:.2f}%" if pnl >= 0 else f"{pnl:.2f}%"
                oc     = t.get("outcome","")
                badge  = f'<span class="badge {oc}">{oc.upper()}</span>'
                content += f"<tr><td>{t.get('pair','')}</td><td>{t.get('direction','').upper()}</td><td>{t.get('rrr_achieved',t.get('rrr',0)):.2f}</td><td>{s_pnl}</td><td>{t.get('session','')}</td><td>{badge}</td></tr>"
            content += "</tbody></table>"

        plain = f"Performance: WR={wr:.1%} RRR={rrr:.2f} Return={sign}{ret:.2f}% DD={dd:.2f}% Trades={n}"
        return self._send("Performance Report", plain,
                          _html_wrap("Performance Report", content))

    def send_evolution_report(self, evolutions: list, best_version: dict) -> bool:
        kept     = [e for e in evolutions if e.get("decision") == "kept"]
        reverted = [e for e in evolutions if e.get("decision") == "reverted"]
        accept_r = len(kept) / max(len(evolutions), 1)

        content = f"""
<div class="stat-row">
  <div class="stat"><div class="label">Iterations</div><div class="value">{len(evolutions)}</div></div>
  <div class="stat green"><div class="label">Kept</div><div class="value">{len(kept)}</div></div>
  <div class="stat red"><div class="label">Reverted</div><div class="value">{len(reverted)}</div></div>
  <div class="stat"><div class="label">Accept Rate</div><div class="value">{accept_r:.0%}</div></div>
  <div class="stat green"><div class="label">Best WR</div><div class="value">{best_version.get('win_rate',0):.1%}</div></div>
</div>
<h2>Recent Evolution Steps</h2>
<table><thead><tr><th>Iter</th><th>Parameter</th><th>Old</th><th>New</th><th>WR Before</th><th>WR After</th><th>Decision</th></tr></thead><tbody>"""

        for e in sorted(evolutions, key=lambda x: x.get("iteration",0), reverse=True)[:15]:
            badge_cls = "kept" if e.get("decision") == "kept" else "reverted"
            content += (
                f"<tr><td>{e.get('iteration','')}</td>"
                f"<td>{e.get('param_changed','')}</td>"
                f"<td>{e.get('old_value','')}</td>"
                f"<td>{e.get('new_value','')}</td>"
                f"<td>{e.get('win_rate_before',0):.1%}</td>"
                f"<td>{e.get('win_rate_after',0):.1%}</td>"
                f"<td><span class='badge {badge_cls}'>{e.get('decision','').upper()}</span></td></tr>"
            )
        content += "</tbody></table>"

        plain = f"Evolution: {len(evolutions)} iters, {len(kept)} kept, best WR={best_version.get('win_rate',0):.1%}"
        return self._send("Evolution Report", plain,
                          _html_wrap("Evolution Report", content))

    def send_ftmo_alert(self, reason: str, equity: float, limit_pct: float) -> bool:
        content = f"""
<div class="alert-box danger">
  <b>&#128721; FTMO LIMIT HIT — All Trading Halted</b>
</div>
<div class="stat-row">
  <div class="stat red"><div class="label">Reason</div><div class="value" style="font-size:14px">{reason}</div></div>
  <div class="stat red"><div class="label">Equity</div><div class="value">${equity:,.2f}</div></div>
  <div class="stat"><div class="label">Limit</div><div class="value">{limit_pct}%</div></div>
</div>
<p style="color:#f85149;margin-top:16px">Manual review required before resuming trading.</p>"""
        plain = f"FTMO HALT: {reason} | Equity=${equity:,.2f}"
        return self._send("FTMO LIMIT HIT — Trading Halted", plain,
                          _html_wrap("FTMO Limit Hit", content))

    # ── Core SMTP send ───────────────────────────────────────────────────────

    def _send(self, subject: str, plain: str, html: str) -> bool:
        if not self.enabled:
            return False
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[AutoTrader] {subject}"
        msg["From"]    = EMAIL_ADDRESS
        msg["To"]      = EMAIL_RECIPIENT
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(html,  "html",  "utf-8"))
        try:
            with smtplib.SMTP(EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, timeout=15) as srv:
                srv.ehlo()
                srv.starttls()
                srv.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
                srv.sendmail(EMAIL_ADDRESS, EMAIL_RECIPIENT, msg.as_string())
            logger.debug(f"Email sent: {subject}")
            return True
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            return False
