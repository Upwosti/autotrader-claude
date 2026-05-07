"""
Flask dashboard — live view of backtest results, evolution history, and trade log.
Runs on http://0.0.0.0:5000
"""

import os
from datetime import datetime
from flask import Flask, jsonify, render_template_string

from database.supabase_client import SupabaseClient

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AutoTrader Claude — Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', sans-serif; background: #0d1117; color: #c9d1d9; }
  header { background: #161b22; padding: 16px 24px; border-bottom: 1px solid #30363d; }
  header h1 { font-size: 1.4rem; color: #f0f6fc; }
  header span { font-size: 0.85rem; color: #8b949e; margin-left: 12px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; padding: 24px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; }
  .card h3 { font-size: 0.8rem; color: #8b949e; text-transform: uppercase; letter-spacing: 0.05em; }
  .card .value { font-size: 2rem; font-weight: 700; color: #58a6ff; margin-top: 8px; }
  .card .sub { font-size: 0.8rem; color: #8b949e; margin-top: 4px; }
  .section { margin: 0 24px 24px; }
  .section h2 { font-size: 1rem; color: #f0f6fc; margin-bottom: 12px; border-bottom: 1px solid #30363d; padding-bottom: 8px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th { text-align: left; padding: 8px 12px; background: #21262d; color: #8b949e; }
  td { padding: 8px 12px; border-bottom: 1px solid #21262d; }
  tr:hover td { background: #161b22; }
  .win { color: #3fb950; }
  .loss { color: #f85149; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }
  .badge-green { background: #1f3d2b; color: #3fb950; }
  .badge-red { background: #3d1f1f; color: #f85149; }
  .badge-yellow { background: #3d3020; color: #d29922; }
  .refresh { font-size: 0.75rem; color: #8b949e; padding: 0 24px 12px; }
</style>
</head>
<body>
<header>
  <h1>AutoTrader Claude</h1>
  <span id="clock"></span>
</header>

<div class="grid" id="stats-grid">
  <div class="card"><h3>Current Version</h3><div class="value" id="stat-version">—</div></div>
  <div class="card"><h3>Win Rate</h3><div class="value" id="stat-wr">—</div></div>
  <div class="card"><h3>Avg RRR</h3><div class="value" id="stat-rrr">—</div></div>
  <div class="card"><h3>Total Trades</h3><div class="value" id="stat-trades">—</div></div>
  <div class="card"><h3>Max Drawdown</h3><div class="value" id="stat-dd">—</div></div>
  <div class="card"><h3>Total Return</h3><div class="value" id="stat-ret">—</div></div>
</div>

<p class="refresh">Auto-refreshes every 30 seconds</p>

<div class="section">
  <h2>Recent Backtest Runs</h2>
  <table>
    <thead><tr><th>#</th><th>Version</th><th>Pair</th><th>Win Rate</th><th>Trades</th><th>Return</th><th>Drawdown</th><th>Overfitting</th></tr></thead>
    <tbody id="runs-tbody"></tbody>
  </table>
</div>

<div class="section">
  <h2>Evolution Log</h2>
  <table>
    <thead><tr><th>Iter</th><th>Parameter</th><th>Old</th><th>New</th><th>WR Before</th><th>WR After</th><th>Decision</th></tr></thead>
    <tbody id="evo-tbody"></tbody>
  </table>
</div>

<div class="section">
  <h2>Recent Trades</h2>
  <table>
    <thead><tr><th>Pair</th><th>Direction</th><th>Entry</th><th>Exit</th><th>RRR</th><th>PnL%</th><th>Session</th><th>Outcome</th></tr></thead>
    <tbody id="trades-tbody"></tbody>
  </table>
</div>

<script>
function fmt(v, digits=2) { return v != null ? Number(v).toFixed(digits) : '—'; }
function pct(v) { return v != null ? (Number(v)*100).toFixed(1)+'%' : '—'; }

async function refresh() {
  try {
    const d = await fetch('/api/dashboard').then(r => r.json());

    document.getElementById('stat-version').textContent = 'v' + (d.current_version || '—');
    document.getElementById('stat-wr').textContent = pct(d.win_rate);
    document.getElementById('stat-rrr').textContent = fmt(d.avg_rrr);
    document.getElementById('stat-trades').textContent = (d.total_trades || 0).toLocaleString();
    document.getElementById('stat-dd').textContent = fmt(d.max_drawdown) + '%';
    document.getElementById('stat-ret').textContent = fmt(d.total_return) + '%';

    const runsBody = document.getElementById('runs-tbody');
    runsBody.innerHTML = '';
    (d.recent_runs || []).forEach(r => {
      const ov = r.overfitting_flag
        ? '<span class="badge badge-yellow">⚠ Yes</span>'
        : '<span class="badge badge-green">No</span>';
      runsBody.innerHTML += `<tr>
        <td>${r.id||'—'}</td><td>v${r.strategy_version}</td><td>${r.pair}</td>
        <td>${pct(r.win_rate)}</td><td>${r.total_trades}</td>
        <td class="${r.total_return_pct>=0?'win':'loss'}">${fmt(r.total_return_pct)}%</td>
        <td>${fmt(r.max_drawdown_pct)}%</td><td>${ov}</td>
      </tr>`;
    });

    const evoBody = document.getElementById('evo-tbody');
    evoBody.innerHTML = '';
    (d.recent_evolution || []).forEach(e => {
      const badge = e.decision === 'kept'
        ? '<span class="badge badge-green">✓ Kept</span>'
        : '<span class="badge badge-red">✗ Reverted</span>';
      evoBody.innerHTML += `<tr>
        <td>${e.iteration}</td><td>${e.param_changed}</td>
        <td>${e.old_value}</td><td>${e.new_value}</td>
        <td>${pct(e.win_rate_before)}</td><td>${pct(e.win_rate_after)}</td>
        <td>${badge}</td>
      </tr>`;
    });

    const tBody = document.getElementById('trades-tbody');
    tBody.innerHTML = '';
    (d.recent_trades || []).forEach(t => {
      const cls = t.outcome === 'win' ? 'win' : 'loss';
      const badge = t.outcome === 'win'
        ? '<span class="badge badge-green">Win</span>'
        : '<span class="badge badge-red">Loss</span>';
      tBody.innerHTML += `<tr>
        <td>${t.pair}</td><td>${t.direction}</td>
        <td>${(t.entry_time||'').slice(0,16)}</td>
        <td>${(t.exit_time||'').slice(0,16)}</td>
        <td>${fmt(t.rrr_achieved)}</td>
        <td class="${cls}">${fmt(t.pnl_pct)}%</td>
        <td>${t.session||'—'}</td><td>${badge}</td>
      </tr>`;
    });
  } catch(e) {
    console.error('Dashboard refresh error:', e);
  }
}

function tick() {
  document.getElementById('clock').textContent = new Date().toUTCString();
}

refresh();
tick();
setInterval(refresh, 30000);
setInterval(tick, 1000);
</script>
</body>
</html>
"""


def create_app(db: SupabaseClient = None) -> Flask:
    app = Flask(__name__)
    if db is None:
        db = SupabaseClient()

    @app.route("/")
    def index():
        return render_template_string(DASHBOARD_HTML)

    @app.route("/api/dashboard")
    def api_dashboard():
        current_version = db.get_current_version()
        total_trades = db.get_total_trades()

        recent_runs = db.select("backtest_runs", limit=10)
        recent_runs = sorted(recent_runs, key=lambda r: r.get("_inserted_at", ""), reverse=True)[:10]

        latest = recent_runs[0] if recent_runs else {}
        win_rate = latest.get("win_rate")
        avg_rrr = latest.get("avg_rrr")
        max_drawdown = latest.get("max_drawdown_pct")
        total_return = latest.get("total_return_pct")

        recent_evo = db.select("evolution_log", limit=20)
        recent_evo = sorted(recent_evo, key=lambda e: e.get("iteration", 0), reverse=True)[:20]

        recent_trades = db.select("trades", limit=30)
        recent_trades = sorted(recent_trades, key=lambda t: t.get("_inserted_at", ""), reverse=True)[:30]

        return jsonify({
            "current_version": current_version,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "avg_rrr": avg_rrr,
            "max_drawdown": max_drawdown,
            "total_return": total_return,
            "recent_runs": recent_runs,
            "recent_evolution": recent_evo,
            "recent_trades": recent_trades,
        })

    @app.route("/api/health")
    def health():
        return jsonify({
            "status": "ok",
            "db_online": db.online,
            "timestamp": datetime.utcnow().isoformat(),
        })

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=5000, debug=False)
