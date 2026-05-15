"""EmailReporter — monthly HTML email reports."""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict
from loguru import logger

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class EmailReporter:
    """Sends monthly and weekly HTML performance reports via SMTP."""

    def __init__(self):
        self._host = os.environ.get("EMAIL_SMTP_HOST", "")
        self._port = int(os.environ.get("EMAIL_SMTP_PORT", 587))
        self._user = os.environ.get("EMAIL_USER", "")
        self._password = os.environ.get("EMAIL_PASSWORD", "")
        self._recipient = os.environ.get("EMAIL_RECIPIENT", "")

        self.enabled = bool(self._user and self._password and self._recipient)
        if not self.enabled:
            logger.warning(
                "EmailReporter disabled — set EMAIL_USER, EMAIL_PASSWORD, EMAIL_RECIPIENT"
            )
        else:
            logger.info(f"EmailReporter ready -> {self._recipient}")

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def send_monthly_report(self, month: str, stats: dict) -> bool:
        """
        Generate and send a monthly HTML report.

        Parameters
        ----------
        month : str
            Format "2026-01"
        stats : dict
            Keys: starting_balance, ending_balance, return_pct,
                  per_pair {pair: {wr, rrr, trades}},
                  best_strategy, worst_strategy, ml_accuracy,
                  skills_learned, max_dd, sharpe, profit_factor
        """
        if not self.enabled:
            logger.warning("EmailReporter.send_monthly_report: not enabled")
            return False
        subject = f"AutoTrader Monthly Report — {month}"
        html = self._build_html(month, stats)
        return self._send(subject, html)

    def send_weekly_report(self, stats: dict) -> bool:
        """Send a simplified weekly performance summary."""
        if not self.enabled:
            logger.warning("EmailReporter.send_weekly_report: not enabled")
            return False

        week = stats.get("week", "N/A")
        subject = f"AutoTrader Weekly Report — {week}"

        trades = stats.get("trades", 0)
        wr = stats.get("wr", 0.0)
        return_pct = stats.get("return_pct", 0.0)
        best_pair = stats.get("best_pair", "N/A")

        html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<style>
  body {{ font-family: Consolas, monospace; background:#1a1a2e; color:#e0e0e0; padding:20px; }}
  h2   {{ color:#00d4ff; }}
  .card {{ background:#16213e; border:1px solid #0f3460; border-radius:6px;
           padding:12px 20px; margin:8px 0; display:inline-block; min-width:160px; }}
  .label {{ color:#888; font-size:12px; }}
  .value {{ color:#00d4ff; font-size:22px; font-weight:bold; }}
</style>
</head>
<body>
<h2>AutoTrader — Weekly Report ({week})</h2>
<div class="card"><div class="label">Trades</div><div class="value">{trades}</div></div>
<div class="card"><div class="label">Win Rate</div><div class="value">{wr:.1f}%</div></div>
<div class="card"><div class="label">Return</div><div class="value">{return_pct:+.2f}%</div></div>
<div class="card"><div class="label">Best Pair</div><div class="value">{best_pair}</div></div>
<p style="color:#555;font-size:11px;margin-top:30px;">AutoTrader — automated weekly digest</p>
</body></html>"""
        return self._send(subject, html)

    # ------------------------------------------------------------------ #
    #  HTML builder
    # ------------------------------------------------------------------ #

    def _build_html(self, month: str, stats: dict) -> str:
        starting_balance = stats.get("starting_balance", 0.0)
        ending_balance = stats.get("ending_balance", 0.0)
        return_pct = stats.get("return_pct", 0.0)
        max_dd = stats.get("max_dd", 0.0)
        sharpe = stats.get("sharpe", 0.0)
        profit_factor = stats.get("profit_factor", 0.0)
        ml_accuracy = stats.get("ml_accuracy", 0.0)
        skills_learned = stats.get("skills_learned", 0)
        best_strategy = stats.get("best_strategy", "N/A")
        worst_strategy = stats.get("worst_strategy", "N/A")
        per_pair: Dict[str, Dict] = stats.get("per_pair", {})

        # ASCII bar chart for per-pair win rates
        chart_lines = self._ascii_bar_chart(per_pair)

        # Per-pair table rows
        pair_rows = ""
        for pair, m in sorted(per_pair.items(), key=lambda x: -x[1].get("wr", 0)):
            wr = m.get("wr", 0.0)
            rrr = m.get("rrr", 0.0)
            trades = m.get("trades", 0)
            color = "#00c853" if wr >= 55 else ("#ff6d00" if wr >= 45 else "#d50000")
            pair_rows += (
                f"<tr>"
                f"<td>{pair}</td>"
                f"<td style='color:{color};font-weight:bold'>{wr:.1f}%</td>"
                f"<td>{rrr:.2f}x</td>"
                f"<td>{trades}</td>"
                f"</tr>\n"
            )

        return_color = "#00c853" if return_pct >= 0 else "#d50000"

        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>AutoTrader Monthly Report — {month}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: Consolas, 'Courier New', monospace;
    background: #0d1117;
    color: #c9d1d9;
    padding: 30px;
    font-size: 14px;
  }}
  h1 {{ color: #58a6ff; margin-bottom: 6px; font-size: 22px; }}
  h2 {{ color: #8b949e; margin: 24px 0 10px; font-size: 15px;
        text-transform: uppercase; letter-spacing: 1px; }}
  .subtitle {{ color: #6e7681; margin-bottom: 24px; font-size: 13px; }}
  .cards {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 24px; }}
  .card {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 16px 22px;
    min-width: 140px;
  }}
  .card .label {{ color: #6e7681; font-size: 11px; text-transform: uppercase;
                   letter-spacing: 0.5px; margin-bottom: 4px; }}
  .card .value {{ font-size: 24px; font-weight: bold; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  th {{ background: #161b22; color: #8b949e; padding: 8px 12px;
         text-align: left; font-size: 12px; border-bottom: 1px solid #30363d; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #21262d; font-size: 13px; }}
  tr:hover td {{ background: #161b22; }}
  pre {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 14px;
    font-size: 12px;
    color: #8b949e;
    overflow-x: auto;
    line-height: 1.5;
  }}
  .highlight {{ color: #00d4ff; }}
  .footer {{ margin-top: 40px; color: #484f58; font-size: 11px; }}
</style>
</head>
<body>

<h1>AutoTrader Monthly Report</h1>
<p class="subtitle">{month} &nbsp;|&nbsp; Generated automatically</p>

<h2>Summary</h2>
<div class="cards">
  <div class="card">
    <div class="label">Return</div>
    <div class="value" style="color:{return_color}">{return_pct:+.2f}%</div>
  </div>
  <div class="card">
    <div class="label">Max Drawdown</div>
    <div class="value" style="color:#ff6d00">{max_dd:.2f}%</div>
  </div>
  <div class="card">
    <div class="label">Sharpe</div>
    <div class="value">{sharpe:.2f}</div>
  </div>
  <div class="card">
    <div class="label">Profit Factor</div>
    <div class="value">{profit_factor:.2f}</div>
  </div>
  <div class="card">
    <div class="label">ML Accuracy</div>
    <div class="value">{ml_accuracy:.1f}%</div>
  </div>
  <div class="card">
    <div class="label">Skills Learned</div>
    <div class="value">{skills_learned}</div>
  </div>
</div>

<div class="cards">
  <div class="card">
    <div class="label">Starting Balance</div>
    <div class="value">${starting_balance:,.2f}</div>
  </div>
  <div class="card">
    <div class="label">Ending Balance</div>
    <div class="value" style="color:{return_color}">${ending_balance:,.2f}</div>
  </div>
  <div class="card">
    <div class="label">Best Strategy</div>
    <div class="value" style="font-size:15px;color:#00c853">{best_strategy}</div>
  </div>
  <div class="card">
    <div class="label">Worst Strategy</div>
    <div class="value" style="font-size:15px;color:#d50000">{worst_strategy}</div>
  </div>
</div>

<h2>Per-Pair Performance</h2>
<table>
  <thead>
    <tr>
      <th>Pair</th>
      <th>Win Rate</th>
      <th>Avg RRR</th>
      <th>Trades</th>
    </tr>
  </thead>
  <tbody>
{pair_rows}
  </tbody>
</table>

<h2>Win Rate Chart (ASCII)</h2>
<pre>{chart_lines}</pre>

<p class="footer">AutoTrader — automated monthly digest &nbsp;|&nbsp; Do not reply to this message</p>
</body>
</html>"""
        return html

    # ------------------------------------------------------------------ #
    #  ASCII chart helper
    # ------------------------------------------------------------------ #

    def _ascii_bar_chart(self, per_pair: Dict[str, Dict]) -> str:
        if not per_pair:
            return "(no pair data)"
        max_bar = 30
        lines = []
        for pair, m in sorted(per_pair.items(), key=lambda x: -x[1].get("wr", 0)):
            wr = m.get("wr", 0.0)
            bar_len = int(wr / 100 * max_bar)
            bar = "#" * bar_len + "-" * (max_bar - bar_len)
            lines.append(f"{pair:<10} [{bar}] {wr:.1f}%")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    #  SMTP sender
    # ------------------------------------------------------------------ #

    def _send(self, subject: str, html_body: str) -> bool:
        if not self.enabled:
            return False
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self._user
            msg["To"] = self._recipient
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP(self._host, self._port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self._user, self._password)
                server.sendmail(self._user, [self._recipient], msg.as_string())

            logger.info(f"EmailReporter sent: {subject}")
            return True
        except Exception as e:
            logger.error(f"EmailReporter._send failed: {e}")
            return False
