"""
Flask dashboard — full trading system dashboard with dark theme.
URL: http://144.91.69.63:5000
"""

import os
from datetime import datetime
from flask import Flask, jsonify, render_template_string
from database.supabase_client import SupabaseClient

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AutoTrader Claude</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0a0e17;color:#c9d1d9;min-height:100vh}
::-webkit-scrollbar{width:6px;background:#0d1117}
::-webkit-scrollbar-thumb{background:#30363d;border-radius:3px}

/* Header */
.header{background:linear-gradient(90deg,#0d1117 0%,#161b22 100%);
  border-bottom:1px solid #1f6feb44;padding:0 24px;
  display:flex;align-items:center;justify-content:space-between;height:56px;position:sticky;top:0;z-index:100}
.header-left{display:flex;align-items:center;gap:12px}
.logo{width:32px;height:32px;background:linear-gradient(135deg,#1f6feb,#388bfd);
  border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px}
.header h1{font-size:16px;font-weight:600;color:#f0f6fc;letter-spacing:0.3px}
.header-right{display:flex;align-items:center;gap:16px}
.dot{width:8px;height:8px;border-radius:50%;background:#3fb950;box-shadow:0 0 6px #3fb950;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.clock{font-size:12px;color:#8b949e;font-variant-numeric:tabular-nums}
.refresh-btn{background:#21262d;border:1px solid #30363d;color:#c9d1d9;
  padding:5px 12px;border-radius:6px;cursor:pointer;font-size:12px;transition:all .2s}
.refresh-btn:hover{background:#30363d}

/* Layout */
.main{padding:20px 24px;max-width:1400px;margin:0 auto}

/* Status bar */
.status-bar{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.stat-card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px 18px;transition:border-color .2s}
.stat-card:hover{border-color:#1f6feb66}
.stat-card .label{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:0.08em;font-weight:600}
.stat-card .value{font-size:28px;font-weight:700;color:#58a6ff;margin:6px 0 2px;line-height:1}
.stat-card .sub{font-size:11px;color:#6e7681}
.stat-card.green .value{color:#3fb950}
.stat-card.red .value{color:#f85149}
.stat-card.yellow .value{color:#d29922}
.stat-card.purple .value{color:#bc8cff}
.win-bar{background:#21262d;border-radius:4px;height:6px;margin-top:6px;overflow:hidden}
.win-bar-fill{height:100%;border-radius:4px;background:linear-gradient(90deg,#3fb950,#2ea043);transition:width .8s ease}

/* Two-col layout */
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
@media(max-width:900px){.two-col{grid-template-columns:1fr}}

/* Cards */
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;overflow:hidden}
.card-header{padding:12px 16px;border-bottom:1px solid #21262d;display:flex;align-items:center;justify-content:space-between}
.card-header h2{font-size:13px;font-weight:600;color:#f0f6fc;letter-spacing:0.2px}
.card-header .badge{font-size:11px;padding:2px 8px;border-radius:10px;font-weight:600}
.card-body{padding:16px}

/* Active trade card */
.trade-card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:18px;margin-bottom:16px}
.trade-none{color:#484f58;font-size:13px;text-align:center;padding:8px}
.trade-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.trade-item .ti-label{font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:0.08em}
.trade-item .ti-value{font-size:15px;font-weight:600;color:#c9d1d9;margin-top:2px}
.trade-item .ti-value.buy{color:#3fb950}.trade-item .ti-value.sell{color:#f85149}

/* Tables */
table{width:100%;border-collapse:collapse;font-size:12px}
thead th{padding:8px 10px;background:#0d1117;color:#8b949e;font-weight:600;
  font-size:11px;text-transform:uppercase;letter-spacing:0.05em;text-align:left;white-space:nowrap}
tbody td{padding:7px 10px;border-bottom:1px solid #1c2029;color:#c9d1d9;white-space:nowrap}
tbody tr:hover td{background:#1c2029}
tbody tr:last-child td{border-bottom:none}

/* Badges */
.badge-win{background:#1a3528;color:#3fb950;padding:2px 7px;border-radius:8px;font-size:11px;font-weight:600}
.badge-loss{background:#3d1a1a;color:#f85149;padding:2px 7px;border-radius:8px;font-size:11px;font-weight:600}
.badge-kept{background:#1a2940;color:#58a6ff;padding:2px 7px;border-radius:8px;font-size:11px;font-weight:600}
.badge-reverted{background:#2e2210;color:#d29922;padding:2px 7px;border-radius:8px;font-size:11px;font-weight:600}
.badge-ok{background:#1a3528;color:#3fb950;padding:2px 7px;border-radius:8px;font-size:11px;font-weight:600}
.badge-halt{background:#3d1a1a;color:#f85149;padding:2px 7px;border-radius:8px;font-size:11px;font-weight:600}

/* Scanner grid */
.scanner-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-bottom:16px}
.scanner-pair{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 16px}
.scanner-pair .pair-name{font-size:13px;font-weight:600;color:#f0f6fc;margin-bottom:10px;
  display:flex;align-items:center;justify-content:space-between}
.scanner-pair .score-bar{display:flex;gap:4px;margin-bottom:6px}
.cond-dot{width:22px;height:22px;border-radius:50%;border:2px solid #21262d;
  display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:700}
.cond-ok{background:#1a3528;border-color:#3fb950;color:#3fb950}
.cond-no{background:#1c2029;border-color:#30363d;color:#484f58}
.conditions-list{font-size:11px;color:#8b949e;margin-top:6px}
.cond-row{display:flex;align-items:center;gap:6px;margin-bottom:3px}
.cond-row .dot{width:6px;height:6px;border-radius:50%;flex-shrink:0}
.score-text{font-size:11px;font-weight:600}

/* Chart */
.chart-wrap{position:relative;height:200px;padding:8px 0}

/* FTMO bar */
.ftmo-bar{background:#161b22;border:1px solid #30363d;border-radius:10px;
  padding:14px 18px;margin-bottom:16px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.ftmo-item{flex:1;min-width:120px}
.ftmo-item .fl{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:0.05em}
.ftmo-item .fv{font-size:16px;font-weight:700;color:#c9d1d9;margin-top:2px}
.dd-track{background:#21262d;border-radius:4px;height:8px;margin-top:4px;overflow:hidden}
.dd-fill{height:100%;border-radius:4px;transition:width .5s ease}

/* Alert log */
.alert-log{max-height:220px;overflow-y:auto}
.alert-item{padding:7px 0;border-bottom:1px solid #1c2029;display:flex;align-items:flex-start;gap:8px;font-size:12px}
.alert-item:last-child{border-bottom:none}
.alert-icon{font-size:14px;flex-shrink:0;margin-top:1px}
.alert-time{color:#484f58;font-size:11px;white-space:nowrap}
.alert-text{color:#c9d1d9;flex:1}

/* Version history */
.version-chip{display:inline-block;background:#1a2940;color:#58a6ff;
  padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600;margin:2px}

/* Loading */
.loading{color:#484f58;font-size:12px;text-align:center;padding:20px}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <div class="logo">&#129302;</div>
    <h1>AutoTrader Claude</h1>
  </div>
  <div class="header-right">
    <div class="dot"></div>
    <span class="clock" id="clock">--:--:-- UTC</span>
    <button class="refresh-btn" onclick="refresh()">&#8635; Refresh</button>
  </div>
</div>

<div class="main">

  <!-- FTMO Status Bar -->
  <div class="ftmo-bar" id="ftmo-bar">
    <div class="ftmo-item">
      <div class="fl">FTMO Status</div>
      <div class="fv" id="ftmo-status">&#10004; OK</div>
    </div>
    <div class="ftmo-item">
      <div class="fl">Daily DD</div>
      <div class="fv" id="ftmo-daily">0.00%</div>
      <div class="dd-track"><div class="dd-fill" id="dd-daily-bar" style="width:0%;background:#3fb950"></div></div>
    </div>
    <div class="ftmo-item">
      <div class="fl">Total DD Limit (5%)</div>
      <div class="fv" id="ftmo-total">0.00%</div>
      <div class="dd-track"><div class="dd-fill" id="dd-total-bar" style="width:0%;background:#3fb950"></div></div>
    </div>
    <div class="ftmo-item">
      <div class="fl">Trading</div>
      <div class="fv" id="trading-state">Active</div>
    </div>
  </div>

  <!-- Key Stats -->
  <div class="status-bar">
    <div class="stat-card green">
      <div class="label">Win Rate</div>
      <div class="value" id="s-wr">—</div>
      <div class="win-bar"><div class="win-bar-fill" id="wr-bar" style="width:0%"></div></div>
    </div>
    <div class="stat-card">
      <div class="label">Avg RRR</div>
      <div class="value" id="s-rrr">—</div>
      <div class="sub">risk:reward ratio</div>
    </div>
    <div class="stat-card">
      <div class="label">Total Trades</div>
      <div class="value" id="s-trades">—</div>
      <div class="sub" id="s-trades-sub">all time</div>
    </div>
    <div class="stat-card" id="s-ret-card">
      <div class="label">Total Return</div>
      <div class="value" id="s-ret">—</div>
      <div class="sub">backtest</div>
    </div>
    <div class="stat-card red">
      <div class="label">Max Drawdown</div>
      <div class="value" id="s-dd">—</div>
      <div class="sub">peak to trough</div>
    </div>
    <div class="stat-card purple">
      <div class="label">Strategy</div>
      <div class="value" id="s-ver">—</div>
      <div class="sub">current version</div>
    </div>
  </div>

  <!-- Active Trade -->
  <div class="card" style="margin-bottom:16px">
    <div class="card-header">
      <h2>&#128200; Active Trade</h2>
      <span class="badge badge-ok" id="active-badge">No Position</span>
    </div>
    <div class="card-body">
      <div id="active-trade-content">
        <div class="trade-none">No active position</div>
      </div>
    </div>
  </div>

  <!-- Setup Scanner -->
  <div class="card" style="margin-bottom:16px">
    <div class="card-header">
      <h2>&#128270; ICT Setup Scanner</h2>
      <span style="font-size:11px;color:#8b949e">7 conditions per pair</span>
    </div>
    <div class="card-body" style="padding:12px">
      <div class="scanner-grid" id="scanner-grid">
        <div class="loading">Loading scanner data...</div>
      </div>
    </div>
  </div>

  <!-- Charts + Tables row -->
  <div class="two-col">
    <div class="card">
      <div class="card-header"><h2>&#128200; Equity Curve</h2></div>
      <div class="card-body">
        <div class="chart-wrap"><canvas id="equity-chart"></canvas></div>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><h2>&#128202; Win Rate by Pair</h2></div>
      <div class="card-body">
        <div class="chart-wrap"><canvas id="pair-chart"></canvas></div>
      </div>
    </div>
  </div>

  <div class="two-col">
    <div class="card">
      <div class="card-header">
        <h2>&#128203; Recent Trades</h2>
        <span class="badge badge-ok" id="trades-count">0</span>
      </div>
      <div class="card-body" style="padding:0">
        <table>
          <thead><tr><th>Pair</th><th>Dir</th><th>RRR</th><th>P&L</th><th>Session</th><th>Result</th></tr></thead>
          <tbody id="trades-body"></tbody>
        </table>
      </div>
    </div>
    <div class="card">
      <div class="card-header">
        <h2>&#9881;&#65039; Evolution Log</h2>
        <span class="badge badge-kept" id="evo-count">0</span>
      </div>
      <div class="card-body" style="padding:0">
        <table>
          <thead><tr><th>#</th><th>Param</th><th>Change</th><th>WR&nbsp;&#916;</th><th>Decision</th></tr></thead>
          <tbody id="evo-body"></tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="two-col" style="margin-top:16px">
    <div class="card">
      <div class="card-header"><h2>&#128293; Alert Log</h2></div>
      <div class="card-body" style="padding:8px 12px">
        <div class="alert-log" id="alert-log">
          <div class="loading">No alerts yet</div>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><h2>&#127942; Top Strategy Versions</h2></div>
      <div class="card-body" style="padding:0">
        <table>
          <thead><tr><th>Version</th><th>Win Rate</th><th>RRR</th><th>Trades</th><th>DD</th></tr></thead>
          <tbody id="versions-body"></tbody>
        </table>
      </div>
    </div>
  </div>

</div><!-- /main -->

<script>
const PAIR_COLORS = {XAUUSD:'#d4a017',BTCUSD:'#f7931a',GBPUSD:'#4aa3df',EURUSD:'#5cb85c'};
const ICT_CONDITIONS = ['Kill Zone','Liquidity Sweep','BOS/CHoCH','FVG Present','HTF Bias Aligned','DXY Filter','Min RRR'];
let equityChart, pairChart;

function fmt(v,d=2){return v!=null?Number(v).toFixed(d):'—'}
function pct(v){return v!=null?(Number(v)*100).toFixed(1)+'%':'—'}
function sign(v){return v>=0?'+':'';}

async function refresh(){
  try{
    const[dash,status] = await Promise.all([
      fetch('/api/dashboard').then(r=>r.json()),
      fetch('/api/status').then(r=>r.json())
    ]);
    updateStats(dash);
    updateActiveTrade(dash.active_trade);
    updateScanner(dash.scanner);
    updateTrades(dash.recent_trades||[]);
    updateEvolution(dash.recent_evolution||[]);
    updateVersions(dash.top_versions||[]);
    updateAlerts(dash.alerts||[]);
    updateEquityChart(dash.equity_curve||[]);
    updatePairChart(dash.pair_stats||{});
    updateFTMO(status);
  }catch(e){console.error('Refresh error:',e)}
}

function updateStats(d){
  const wr = d.win_rate||0;
  document.getElementById('s-wr').textContent = pct(wr);
  document.getElementById('wr-bar').style.width = (wr*100)+'%';
  document.getElementById('s-rrr').textContent = fmt(d.avg_rrr);
  document.getElementById('s-trades').textContent = (d.total_trades||0).toLocaleString();
  const ret = d.total_return||0;
  const retEl = document.getElementById('s-ret');
  retEl.textContent = sign(ret)+fmt(ret)+'%';
  document.getElementById('s-ret-card').className = 'stat-card '+(ret>=0?'green':'red');
  document.getElementById('s-dd').textContent = fmt(d.max_drawdown)+'%';
  document.getElementById('s-ver').textContent = 'v'+(d.current_version||1);
}

function updateFTMO(s){
  const halted = s.ftmo_halted||false;
  document.getElementById('ftmo-status').innerHTML = halted
    ? '<span class="badge badge-halt">&#9888; HALTED</span>'
    : '<span style="color:#3fb950">&#10004; OK</span>';
  document.getElementById('trading-state').textContent = s.paused?'Paused':(halted?'Halted':'Active');
  const ddd = Math.min((s.daily_dd||0)/2*100,100);
  const tdd = Math.min((s.total_dd||0)/5*100,100);
  document.getElementById('ftmo-daily').textContent = fmt(s.daily_dd||0)+'%';
  document.getElementById('ftmo-total').textContent = fmt(s.total_dd||0)+'%';
  const dc = ddd>80?'#f85149':ddd>50?'#d29922':'#3fb950';
  const tc = tdd>80?'#f85149':tdd>50?'#d29922':'#3fb950';
  document.getElementById('dd-daily-bar').style.cssText = `width:${ddd}%;background:${dc}`;
  document.getElementById('dd-total-bar').style.cssText = `width:${tdd}%;background:${tc}`;
}

function updateActiveTrade(trade){
  const badge = document.getElementById('active-badge');
  const content = document.getElementById('active-trade-content');
  if(!trade){
    badge.className='badge badge-ok'; badge.textContent='No Position';
    content.innerHTML='<div class="trade-none">No active position — monitoring kill zones</div>';
    return;
  }
  const dir = (trade.direction||'').toUpperCase();
  badge.className='badge '+(dir==='BUY'?'badge-win':'badge-loss');
  badge.textContent='LIVE: '+dir+' '+trade.symbol;
  const rrr_current = trade.tp&&trade.sl&&trade.entry
    ? Math.abs(trade.tp-trade.entry)/Math.abs(trade.entry-trade.sl) : 0;
  content.innerHTML=`
  <div class="trade-grid">
    <div class="trade-item"><div class="ti-label">Symbol</div><div class="ti-value">${trade.symbol||'—'}</div></div>
    <div class="trade-item"><div class="ti-label">Direction</div><div class="ti-value ${dir.toLowerCase()}">${dir}</div></div>
    <div class="trade-item"><div class="ti-label">Entry</div><div class="ti-value">${fmt(trade.entry,5)}</div></div>
    <div class="trade-item"><div class="ti-label">Stop Loss</div><div class="ti-value" style="color:#f85149">${fmt(trade.sl,5)}</div></div>
    <div class="trade-item"><div class="ti-label">Take Profit</div><div class="ti-value" style="color:#3fb950">${fmt(trade.tp,5)}</div></div>
    <div class="trade-item"><div class="ti-label">RRR</div><div class="ti-value">${fmt(rrr_current)}</div></div>
    <div class="trade-item"><div class="ti-label">Lots</div><div class="ti-value">${fmt(trade.lot,2)}</div></div>
    <div class="trade-item"><div class="ti-label">Confidence</div><div class="ti-value">${trade.confidence||0}/7</div></div>
    <div class="trade-item"><div class="ti-label">Opened</div><div class="ti-value" style="font-size:12px">${(trade.opened_at||'—').slice(0,16)}</div></div>
  </div>`;
}

function updateScanner(scanner){
  const grid = document.getElementById('scanner-grid');
  const pairs = ['XAUUSD','GBPUSD','EURUSD','BTCUSD'];
  grid.innerHTML = pairs.map(pair=>{
    const data = scanner&&scanner[pair]||{};
    const conds = data.conditions||{};
    const score = Object.values(conds).filter(Boolean).length;
    const total = ICT_CONDITIONS.length;
    const color = score>=5?'#3fb950':score>=3?'#d29922':'#f85149';
    const bias = data.htf_bias||'neutral';
    const biasColor = bias==='bullish'?'#3fb950':bias==='bearish'?'#f85149':'#8b949e';
    const dotsHtml = ICT_CONDITIONS.map((c,i)=>{
      const ok = Object.values(conds)[i]||false;
      return `<div class="cond-dot ${ok?'cond-ok':'cond-no'}" title="${c}">${i+1}</div>`;
    }).join('');
    const condRows = ICT_CONDITIONS.map((c,i)=>{
      const ok = Object.values(conds)[i]||false;
      return `<div class="cond-row"><div class="dot" style="background:${ok?'#3fb950':'#30363d'}"></div><span style="color:${ok?'#c9d1d9':'#484f58'}">${c}</span></div>`;
    }).join('');
    return `<div class="scanner-pair">
      <div class="pair-name">
        <span style="color:${PAIR_COLORS[pair]||'#c9d1d9'}">${pair}</span>
        <span class="score-text" style="color:${color}">${score}/${total}</span>
      </div>
      <div class="score-bar">${dotsHtml}</div>
      <div class="conditions-list">${condRows}</div>
      <div style="margin-top:8px;font-size:11px">
        HTF Bias: <span style="color:${biasColor};font-weight:600">${bias.toUpperCase()}</span>
        &nbsp;|&nbsp; Signal: <span style="color:${color};font-weight:600">${score>=5?'TRADE':'WAIT'}</span>
      </div>
    </div>`;
  }).join('');
}

function updateTrades(trades){
  document.getElementById('trades-count').textContent = trades.length;
  const tbody = document.getElementById('trades-body');
  if(!trades.length){tbody.innerHTML='<tr><td colspan="6" style="text-align:center;color:#484f58;padding:16px">No trades yet</td></tr>';return}
  tbody.innerHTML = trades.map(t=>{
    const pnl = t.pnl_pct||0;
    const spnl = sign(pnl)+fmt(pnl)+'%';
    const badge = t.outcome==='win'?'badge-win':'badge-loss';
    return `<tr>
      <td style="color:${PAIR_COLORS[t.pair]||'#c9d1d9'};font-weight:600">${t.pair}</td>
      <td style="color:${t.direction==='buy'?'#3fb950':'#f85149'}">${(t.direction||'').toUpperCase()}</td>
      <td>${fmt(t.rrr_achieved||t.rrr)}</td>
      <td style="color:${pnl>=0?'#3fb950':'#f85149'}">${spnl}</td>
      <td style="color:#8b949e">${t.session||'—'}</td>
      <td><span class="${badge}">${(t.outcome||'').toUpperCase()}</span></td>
    </tr>`;
  }).join('');
}

function updateEvolution(evos){
  document.getElementById('evo-count').textContent = evos.length+' recent';
  const tbody = document.getElementById('evo-body');
  if(!evos.length){tbody.innerHTML='<tr><td colspan="5" style="text-align:center;color:#484f58;padding:16px">No evolution data</td></tr>';return}
  tbody.innerHTML = evos.map(e=>{
    const badge = e.decision==='kept'?'badge-kept':'badge-reverted';
    const delta = (e.win_rate_after||0)-(e.win_rate_before||0);
    const deltaStr = (delta>=0?'+':'')+pct(delta);
    const dc = delta>0?'#3fb950':delta<0?'#f85149':'#8b949e';
    return `<tr>
      <td style="color:#8b949e">${e.iteration}</td>
      <td style="color:#c9d1d9">${e.param_changed||'—'}</td>
      <td style="color:#8b949e;font-size:11px">${e.old_value||''}&#8594;${e.new_value||''}</td>
      <td style="color:${dc}">${deltaStr}</td>
      <td><span class="${badge}">${(e.decision||'').toUpperCase()}</span></td>
    </tr>`;
  }).join('');
}

function updateVersions(versions){
  const tbody = document.getElementById('versions-body');
  if(!versions.length){tbody.innerHTML='<tr><td colspan="5" style="text-align:center;color:#484f58;padding:16px">No versions</td></tr>';return}
  tbody.innerHTML = versions.map((v,i)=>{
    const medalColor = i===0?'#d4a017':i===1?'#8b949e':i===2?'#cd7f32':'#484f58';
    return `<tr>
      <td><span style="color:${medalColor}">&#9679;</span> <span class="version-chip">v${v.version||v.id}</span></td>
      <td style="color:#3fb950">${pct(v.win_rate)}</td>
      <td>${fmt(v.avg_rrr)}</td>
      <td>${v.total_trades||0}</td>
      <td style="color:#f85149">${fmt(v.max_drawdown)}%</td>
    </tr>`;
  }).join('');
}

function updateAlerts(alerts){
  const log = document.getElementById('alert-log');
  if(!alerts.length){log.innerHTML='<div class="loading">No recent alerts</div>';return}
  log.innerHTML = alerts.map(a=>{
    const icons = {trade_open:'&#128200;',trade_close:'&#128203;',evolution:'&#129516;',
                   ftmo:'&#128721;',milestone:'&#127942;',system:'&#128268;'};
    const icon = icons[a.type]||'&#128276;';
    return `<div class="alert-item">
      <span class="alert-icon">${icon}</span>
      <div style="flex:1">
        <div class="alert-text">${a.message||''}</div>
        <div class="alert-time">${(a.time||'').slice(0,16)} UTC</div>
      </div>
    </div>`;
  }).join('');
}

function updateEquityChart(curve){
  const ctx = document.getElementById('equity-chart').getContext('2d');
  const labels = curve.map((_,i)=>i+1);
  const values = curve.length?curve:[0];
  if(equityChart) equityChart.destroy();
  equityChart = new Chart(ctx,{
    type:'line',
    data:{labels,datasets:[{
      label:'Equity %',data:values,
      borderColor:'#1f6feb',backgroundColor:'rgba(31,111,235,0.1)',
      borderWidth:2,pointRadius:0,tension:0.3,fill:true
    }]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false},tooltip:{mode:'index'}},
      scales:{
        x:{display:false,grid:{display:false}},
        y:{grid:{color:'#1c2029'},ticks:{color:'#8b949e',font:{size:10}}}
      }
    }
  });
}

function updatePairChart(pairStats){
  const ctx = document.getElementById('pair-chart').getContext('2d');
  const labels = Object.keys(pairStats);
  const values = labels.map(p=>(pairStats[p].win_rate||0)*100);
  const colors = labels.map(p=>PAIR_COLORS[p]||'#58a6ff');
  if(pairChart) pairChart.destroy();
  pairChart = new Chart(ctx,{
    type:'bar',
    data:{labels,datasets:[{label:'Win %',data:values,backgroundColor:colors,borderRadius:6}]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false}},
      scales:{
        x:{grid:{display:false},ticks:{color:'#8b949e',font:{size:11}}},
        y:{min:0,max:100,grid:{color:'#1c2029'},ticks:{color:'#8b949e',font:{size:10},callback:v=>v+'%'}}
      }
    }
  });
}

function tick(){
  const now = new Date();
  document.getElementById('clock').textContent =
    now.toISOString().slice(11,19)+' UTC';
}

refresh();
tick();
setInterval(refresh,30000);
setInterval(tick,1000);
</script>
</body>
</html>"""


_alert_log = []   # in-memory alert log (last 50)


def _push_alert(alert_type: str, message: str):
    _alert_log.append({
        "type": alert_type, "message": message,
        "time": datetime.utcnow().isoformat(),
    })
    if len(_alert_log) > 50:
        _alert_log.pop(0)


def create_app(db: SupabaseClient = None) -> Flask:
    app = Flask(__name__)
    if db is None:
        db = SupabaseClient()

    @app.route("/")
    def index():
        return render_template_string(DASHBOARD_HTML)

    @app.route("/api/dashboard")
    def api_dashboard():
        total_trades    = db.get_total_trades()
        current_version = db.get_current_version()

        recent_runs = db.select("backtest_runs", limit=200)
        recent_runs = sorted(recent_runs, key=lambda r: r.get("_inserted_at",""), reverse=True)
        latest = recent_runs[0] if recent_runs else {}

        win_rate     = latest.get("win_rate")
        avg_rrr      = latest.get("avg_rrr")
        max_drawdown = latest.get("max_drawdown_pct")
        total_return = latest.get("total_return_pct")

        # Equity curve from backtest runs
        equity_curve = []
        for r in sorted(recent_runs, key=lambda x: x.get("_inserted_at",""))[-50:]:
            equity_curve.append(round(r.get("total_return_pct", 0), 2))

        # Recent trades
        all_trades  = db.select("trades", limit=1000)
        recent_trades = sorted(all_trades, key=lambda t: t.get("_inserted_at",""), reverse=True)[:15]

        # Per-pair stats
        pair_stats = {}
        for t in all_trades:
            p = t.get("pair","XAUUSD")
            pair_stats.setdefault(p, {"wins":0,"total":0})
            pair_stats[p]["total"] += 1
            if t.get("outcome") == "win":
                pair_stats[p]["wins"] += 1
        for p in pair_stats:
            s = pair_stats[p]
            s["win_rate"] = s["wins"] / max(s["total"], 1)

        # Top versions
        versions = db.select("strategy_versions", limit=100)
        top_versions = sorted(versions, key=lambda v: v.get("win_rate",0), reverse=True)[:5]

        # Evolution log
        recent_evo = db.select("evolution_log", limit=500)
        recent_evo = sorted(recent_evo, key=lambda e: e.get("iteration",0), reverse=True)[:20]

        # Scanner data (placeholder — real scanner called from ICT engine)
        scanner = _build_scanner_data(db, pair_stats)

        return jsonify({
            "current_version": current_version,
            "total_trades":    total_trades,
            "win_rate":        win_rate,
            "avg_rrr":         avg_rrr,
            "max_drawdown":    max_drawdown,
            "total_return":    total_return,
            "equity_curve":    equity_curve,
            "recent_trades":   recent_trades,
            "pair_stats":      pair_stats,
            "top_versions":    top_versions,
            "recent_evolution": recent_evo,
            "scanner":         scanner,
            "alerts":          list(reversed(_alert_log))[:20],
            "active_trade":    None,
        })

    @app.route("/api/status")
    def api_status():
        versions = db.select("strategy_versions", limit=1)
        best_v   = versions[0] if versions else {}
        return jsonify({
            "db_online":     db.online,
            "best_version":  best_v.get("version", 1),
            "best_win_rate": best_v.get("win_rate", 0),
            "timestamp":     datetime.utcnow().isoformat(),
            "ftmo_halted":   False,
            "paused":        False,
            "daily_dd":      0.0,
            "total_dd":      0.0,
        })

    @app.route("/api/alert", methods=["POST"])
    def api_alert():
        from flask import request
        data = request.json or {}
        _push_alert(data.get("type","system"), data.get("message",""))
        return jsonify({"ok": True})

    @app.route("/api/health")
    def health():
        return jsonify({
            "status":    "ok",
            "db_online": db.online,
            "timestamp": datetime.utcnow().isoformat(),
        })

    # ── React frontend API endpoints ─────────────────────────────────────────

    @app.route("/api/state")
    def api_state():
        """State file for React dashboard."""
        import json
        state_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "local_db", "auto_loop_state.json")
        try:
            if os.path.exists(state_path):
                with open(state_path, encoding="utf-8") as f:
                    return jsonify(json.load(f))
        except Exception:
            pass
        return jsonify({})

    @app.route("/api/alerts")
    def api_alerts():
        """Alert log for React dashboard."""
        alerts = [
            {"time": getattr(a, "time", ""), "message": a if isinstance(a, str) else str(a)}
            for a in reversed(list(_alert_log)[-50:])
        ]
        return jsonify(alerts)

    @app.route("/api/skills")
    def api_skills():
        """Skills library for React dashboard."""
        import json
        skills_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "local_db", "skills.json")
        try:
            if os.path.exists(skills_path):
                with open(skills_path, encoding="utf-8") as f:
                    return jsonify(json.load(f))
        except Exception:
            pass
        return jsonify({})

    @app.route("/api/ml")
    def api_ml():
        """ML model metadata for React dashboard."""
        import json
        meta_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "local_db", "ml_models", "meta.json")
        try:
            if os.path.exists(meta_path):
                with open(meta_path, encoding="utf-8") as f:
                    return jsonify(json.load(f))
        except Exception:
            pass
        return jsonify({"trained": False, "models": []})

    @app.route("/react")
    @app.route("/react/")
    def react_dashboard():
        """Serve the React CDN dashboard."""
        from flask import send_from_directory
        frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
        return send_from_directory(frontend_dir, "index.html")

    return app


def _build_scanner_data(db, pair_stats: dict) -> dict:
    """Build scanner condition data from recent evolution/backtest data."""
    pairs = ["XAUUSD", "GBPUSD", "EURUSD", "BTCUSD"]
    scanner = {}
    for pair in pairs:
        ps = pair_stats.get(pair, {})
        wr = ps.get("win_rate", 0)
        total = ps.get("total", 0)
        # Derive conditions from available data
        scanner[pair] = {
            "htf_bias": "bullish" if wr > 0.55 else ("bearish" if wr < 0.45 else "neutral"),
            "conditions": {
                "kill_zone":       total > 0,
                "liquidity_sweep": wr > 0.4,
                "bos_choch":       wr > 0.45,
                "fvg_present":     wr > 0.5,
                "htf_aligned":     wr > 0.55,
                "dxy_filter":      True,
                "min_rrr":         wr > 0.45,
            }
        }
    return scanner


if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=5000, debug=False)
