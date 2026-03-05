/* ═══════════════════════════════════════════════════════════
   Odds Magician — Frontend
   Polls local backend every 30s; renders EV chart + pull feed
   ═══════════════════════════════════════════════════════════ */

const API = '';                  // same-origin backend
const REFRESH_MS = 30_000;       // UI refresh interval

// Timeframe → number of 60s poll snapshots to request
const TIMEFRAMES = {
  '1H':  60,
  '6H':  360,
  '24H': 1440,
  '7D':  10080,
  'ALL': 100000,
};

let currentPack = document.getElementById('packSelect')?.value || 'pkmn-starter-pack';
let currentTimeframe = '24H';
let evChart = null;
let knownPullIds = new Set();    // track IDs to animate new arrivals
let refreshTimer = null;

// ─── Chart init ──────────────────────────────────────────────────────────────

function initChart() {
  const ctx = document.getElementById('evChart').getContext('2d');

  const gradient = ctx.createLinearGradient(0, 0, 0, 300);
  gradient.addColorStop(0, 'rgba(59,130,246,0.3)');
  gradient.addColorStop(1, 'rgba(59,130,246,0)');

  evChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'EV Ratio',
          data: [],
          borderColor: '#3b82f6',
          borderWidth: 2,
          backgroundColor: gradient,
          fill: true,
          tension: 0.35,
          pointRadius: 2,
          pointHoverRadius: 5,
          pointBackgroundColor: '#3b82f6',
        },
        {
          // Break-even reference line
          label: 'Break-even (1.00)',
          data: [],
          borderColor: 'rgba(255,255,255,0.25)',
          borderWidth: 1.5,
          borderDash: [6, 4],
          pointRadius: 0,
          fill: false,
          tension: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      animation: { duration: 400 },
      scales: {
        x: {
          type: 'category',
          ticks: {
            color: '#4a5568',
            maxTicksLimit: 8,
            maxRotation: 0,
            font: { family: "'SF Mono', monospace", size: 10 },
          },
          grid: { color: '#1e2d45', drawBorder: false },
        },
        y: {
          min: 0.6,
          max: 1.4,
          ticks: {
            color: '#7a8fa8',
            font: { family: "'SF Mono', monospace", size: 10 },
            stepSize: 0.1,
            callback: (v) => v.toFixed(1) + 'x',
          },
          grid: { color: '#1e2d45', drawBorder: false },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#0d1320',
          borderColor: '#1e2d45',
          borderWidth: 1,
          titleColor: '#e2e8f0',
          bodyColor: '#7a8fa8',
          callbacks: {
            label: (ctx) => {
              if (ctx.datasetIndex === 0)
                return ` EV Ratio: ${ctx.parsed.y.toFixed(4)}x`;
              return null;
            },
          },
        },
      },
    },
  });
}

// ─── Data fetching ────────────────────────────────────────────────────────────

async function fetchJSON(path) {
  try {
    const r = await fetch(API + path);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } catch (e) {
    console.warn('fetchJSON failed:', path, e.message);
    return null;
  }
}

// ─── EV Header update ─────────────────────────────────────────────────────────

function updateEVHeader(ev) {
  if (!ev || ev.error) {
    setEl('evRatioVal', '—');
    setEl('evBadge', 'NO DATA', 'metric-badge');
    setEl('evUsdVal', '—');
    return;
  }

  const ratio = ev.ev_ratio;
  const isPos = ratio >= 1.0;

  const ratioEl = document.getElementById('evRatioVal');
  ratioEl.textContent = ratio.toFixed(4) + 'x';
  ratioEl.className = `metric-value ${isPos ? 'positive' : 'negative'}`;

  const badge = document.getElementById('evBadge');
  const noOdds = ev.no_odds_data ? ' · RAW AVG' : '';
  badge.textContent = (isPos ? '▲ POSITIVE EV' : '▼ HOUSE EDGE') + noOdds;
  badge.className = `metric-badge ${isPos ? 'badge-pos' : 'badge-neg'}`;

  const card = document.getElementById('evRatioCard');
  card.classList.remove('pulse-green', 'pulse-red');
  void card.offsetWidth; // force reflow for animation
  card.classList.add(isPos ? 'pulse-green' : 'pulse-red');

  setEl('evUsdVal', `$${ev.ev_usd.toFixed(2)}`);
  setEl('packPriceVal', `pack cost $${ev.pack_price}`);

  const conf = ev.overall_confidence ?? 0;
  const confPct = (conf * 100).toFixed(0);
  setEl('confVal', confPct + '%');
  document.getElementById('confBar').style.width = confPct + '%';

  setEl('obsVal', (ev.total_obs ?? 0).toLocaleString());

  setEl('lastPollVal', ev.created_at ? 'polled ' + timeAgo(ev.created_at) : 'polling…');
}

// ─── Chart update ─────────────────────────────────────────────────────────────

function updateChart(history) {
  if (!evChart || !history || !history.length) return;

  const showDate = currentTimeframe === '7D' || currentTimeframe === 'ALL';
  const labels = history.map((h) => formatChartTime(h.created_at, showDate));
  const values = history.map((h) => h.ev_ratio);
  const breakeven = values.map(() => 1.0);

  evChart.data.labels = labels;
  evChart.data.datasets[0].data = values;
  evChart.data.datasets[1].data = breakeven;

  // Colour line: green above 1.0, red below
  const latest = values[values.length - 1] ?? 0;
  evChart.data.datasets[0].borderColor = latest >= 1.0 ? '#00d68f' : '#ff4d6d';

  const ctx = evChart.ctx;
  const h = evChart.chartArea?.height ?? 300;
  const g = ctx.createLinearGradient(0, 0, 0, h);
  const col = latest >= 1.0 ? '0,214,143' : '255,77,109';
  g.addColorStop(0, `rgba(${col},0.25)`);
  g.addColorStop(1, `rgba(${col},0)`);
  evChart.data.datasets[0].backgroundColor = g;

  evChart.update('none');
}

// ─── Buckets / Calibration ────────────────────────────────────────────────────

const TIER_CLASS = {
  common: 'tier-common',
  uncommon: 'tier-uncommon',
  rare: 'tier-rare',
  chase: 'tier-chase',
  epic: 'tier-epic',
};

const CONF_FILL = {
  none:     [0, 0, 0, 0, 0],
  very_low: [1, 0, 0, 0, 0],
  low:      [1, 1, 0, 0, 0],
  medium:   [1, 1, 1, 0, 0],
  high:     [1, 1, 1, 1, 1],
};

const CONF_COLOUR = {
  none:     { label: 'NO DATA',       cls: 'conf-none' },
  very_low: { label: 'VERY LOW',      cls: 'conf-vlow' },
  low:      { label: 'LOW',           cls: 'conf-low' },
  medium:   { label: 'CALIBRATING',   cls: 'conf-med' },
  high:     { label: 'CALIBRATED',    cls: 'conf-high' },
};

function dotClass(label, i) {
  const filled = CONF_FILL[label] ?? [0, 0, 0, 0, 0];
  if (!filled[i]) return 'conf-dot';
  if (label === 'very_low') return 'conf-dot filled-low';
  if (label === 'low')      return 'conf-dot filled-low';
  if (label === 'medium')   return 'conf-dot filled-mid';
  return 'conf-dot filled-high';
}

function renderBuckets(cal) {
  const container = document.getElementById('bucketsContainer');
  if (!cal || !cal.buckets) {
    const msg = cal?.no_odds_data
      ? `Courtyard doesn't publish odds for this pack.<br>EV shown is the raw average of <strong>${cal.total_obs ?? 0}</strong> observed pulls.`
      : 'No calibration data yet.';
    container.innerHTML = `<div class="loading-msg">${msg}</div>`;
    return;
  }

  setEl('oddsTimestamp', cal.total_obs + ' obs total');

  container.innerHTML = cal.buckets.map((b) => {
    const cl = b.confidence_label ?? 'none';
    const cc = CONF_COLOUR[cl] ?? CONF_COLOUR.none;
    const dots = [0, 1, 2, 3, 4].map((i) =>
      `<div class="${dotClass(cl, i)}"></div>`
    ).join('');

    const midDelta = b.weighted_avg != null
      ? ((b.weighted_avg - b.midpoint) / b.midpoint * 100).toFixed(1)
      : null;

    const tierCls = TIER_CLASS[b.tier] ?? 'tier-common';

    return `
      <div class="bucket-card">
        <div class="bucket-header">
          <span class="bucket-range">$${b.min_value}–$${b.max_value}</span>
          <span class="bucket-tier ${tierCls}">${b.tier ?? ''}</span>
          <span class="bucket-odds">${b.odds_pct}%</span>
        </div>
        <div class="bucket-stats">
          <div class="stat-row highlight">
            <span>Calibrated avg</span>
            <span class="val">$${b.calibrated_avg}</span>
          </div>
          <div class="stat-row">
            <span>Midpoint</span>
            <span class="val">$${b.midpoint}</span>
          </div>
          <div class="stat-row">
            <span>Bucket EV</span>
            <span class="val">$${b.bucket_ev}</span>
          </div>
          <div class="stat-row">
            <span>Observations</span>
            <span class="val">${b.n_obs}</span>
          </div>
          ${midDelta != null ? `
          <div class="stat-row" style="grid-column:1/-1">
            <span>Actual vs midpoint</span>
            <span class="val" style="color:${midDelta < 0 ? 'var(--red)' : 'var(--green)'}">
              ${midDelta > 0 ? '+' : ''}${midDelta}%
            </span>
          </div>` : ''}
        </div>
        <div class="conf-indicator">
          <div class="conf-dots">${dots}</div>
          <span class="conf-label ${cc.cls}">${cc.label}</span>
        </div>
        <div class="bucket-bar-wrap">
          <div class="bucket-bar" style="width:${b.odds_pct}%"></div>
        </div>
      </div>
    `;
  }).join('');
}

// ─── Pull Feed ────────────────────────────────────────────────────────────────

function fmvClass(fmv) {
  if (fmv >= 200) return 'fmv-chase';
  if (fmv >= 100) return 'fmv-high';
  if (fmv >= 50)  return 'fmv-mid';
  return 'fmv-low';
}

function renderPulls(pulls, totalCount) {
  const feed = document.getElementById('pullFeed');
  setEl('pullCount', `${(totalCount ?? pulls.length).toLocaleString()} pulls logged`);

  if (!pulls || !pulls.length) {
    feed.innerHTML = '<div class="loading-msg">No pulls logged yet.</div>';
    return;
  }

  const html = pulls.map((p) => {
    const isNew = !knownPullIds.has(p.id ?? p.collectible_id);
    if (isNew) knownPullIds.add(p.id ?? p.collectible_id);
    const imgSrc = p.image_url || 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

    return `
      <div class="pull-item${isNew ? ' is-new' : ''}">
        <img class="pull-img" src="${imgSrc}" alt="card"
             onerror="this.style.opacity='0.3'" loading="lazy" />
        <div class="pull-info">
          <div class="pull-title" title="${escHtml(p.title)}">${escHtml(truncate(p.title, 52))}</div>
          <div class="pull-meta">${timeAgo(p.tx_time)}</div>
        </div>
        <div class="pull-fmv ${fmvClass(p.fmv_usd)}">$${p.fmv_usd.toFixed(0)}</div>
      </div>
    `;
  }).join('');

  feed.innerHTML = html;
}

// ─── Full refresh cycle ───────────────────────────────────────────────────────

async function refresh() {
  setPollDot('polling');
  setFooterStatus('Refreshing…');

  const historyLimit = TIMEFRAMES[currentTimeframe] || 1440;
  const [calData, pullsData, historyData] = await Promise.all([
    fetchJSON(`/api/packs/${currentPack}/calibration`),
    fetchJSON(`/api/packs/${currentPack}/pulls?limit=50`),
    fetchJSON(`/api/packs/${currentPack}/ev/history?limit=${historyLimit}`),
  ]);

  // Update EV header from latest history point
  const latestEV = historyData?.length
    ? historyData[historyData.length - 1]
    : null;

  if (latestEV && calData) {
    latestEV.overall_confidence = calData.overall_confidence;
    latestEV.total_obs = calData.total_obs;
    latestEV.pack_price = calData.pack_price;
  }

  updateEVHeader(calData || latestEV);
  updateChart(historyData);
  renderBuckets(calData);

  if (Array.isArray(pullsData)) {
    // totalCount comes from the calibration total_obs (all stored pulls)
    renderPulls(pullsData, calData?.total_obs);
  }

  setPollDot('ok');
  setFooterStatus(`Last refresh: ${new Date().toLocaleTimeString()}`);

  // Keep the EV widget in sync with latest data
  refreshEVWidget();
  refreshDroughtWidget();
}

// ─── Manual poll ─────────────────────────────────────────────────────────────

async function triggerPoll() {
  const btn = document.getElementById('btnPoll');
  btn.disabled = true;
  btn.textContent = '↻ Polling…';
  setPollDot('polling');

  try {
    // Poll runs synchronously on the server — response returns when done
    await fetch('/api/poll', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pack_id: currentPack }),
    });
    await refresh();
  } catch (e) {
    console.error('Poll failed', e);
    setPollDot('error');
  } finally {
    btn.disabled = false;
    btn.textContent = '↻ Poll Now';
  }
}

// ─── All-Packs EV Widget ──────────────────────────────────────────────────────

const PACK_SHORT = {
  'pkmn-basic-pack':   { name: 'Basic',   price: '$10'  },
  'pkmn-starter-pack': { name: 'Starter', price: '$25'  },
  'pkmn-pro-pack':     { name: 'Pro',     price: '$50'  },
  'pkmn-master-pack':  { name: 'Master',  price: '$100' },
};

async function refreshEVWidget() {
  const packs = await fetchJSON('/api/packs');
  const body = document.getElementById('evWidgetBody');
  if (!packs || !body) return;

  const rowsHtml = packs.map((pack) => {
    const meta = PACK_SHORT[pack.id] || { name: pack.id, price: '' };
    const ratio = pack.ev?.ev_ratio;
    const hasData = ratio != null;
    const isPos = hasData && ratio >= 1.0;
    const ratioStr = hasData ? ratio.toFixed(4) + 'x' : '—';
    const arrow = hasData ? (isPos ? ' ▲' : ' ▼') : '';
    const ratioClass = hasData ? (isPos ? 'positive' : 'negative') : 'no-data';
    const activeClass = pack.id === currentPack ? ' ev-row-active' : '';

    return `
      <div class="ev-widget-row${activeClass}" onclick="switchToPack('${pack.id}')">
        <span class="ev-widget-name">${meta.name}</span>
        <span class="ev-widget-price">${meta.price}</span>
        <span class="ev-widget-ratio ${ratioClass}">${ratioStr}${arrow}</span>
      </div>`;
  }).join('');

  const updatedHtml = `<div class="ev-widget-updated">↻ ${new Date().toLocaleTimeString()}</div>`;
  body.innerHTML = rowsHtml + updatedHtml;
}

// ─── Drought Tracker Widget ───────────────────────────────────────────────────

function droughtCount(n) {
  if (n === null || n === undefined) {
    return '<span class="drought-count nodata">—</span>';
  }
  // Colour thresholds: fresh 0-9 | medium 10-29 | dry 30+
  const cls = n >= 30 ? 'dry' : n >= 10 ? 'medium' : 'fresh';
  return `<span class="drought-count ${cls}">${n}</span>`;
}

async function refreshDroughtWidget() {
  const data = await fetchJSON('/api/drought');
  const body = document.getElementById('droughtWidgetBody');
  if (!data || !body) return;

  const headerHtml = `
    <div class="drought-col-headers">
      <span class="drought-col-pack"></span>
      <span class="drought-col-count">RARE</span>
      <span class="drought-col-count">CHASE</span>
    </div>`;

  const rowsHtml = Object.entries(PACK_SHORT).map(([packId, meta]) => {
    const d = data[packId] || {};
    return `
      <div class="drought-row">
        <span class="drought-pack-name">
          ${meta.name}<span class="drought-pack-price">${meta.price}</span>
        </span>
        ${droughtCount(d.rare)}
        ${droughtCount(d.chase)}
      </div>`;
  }).join('');

  body.innerHTML = headerHtml + rowsHtml;
}

function switchToPack(packId) {
  const select = document.getElementById('packSelect');
  if (select) select.value = packId;
  currentPack = packId;
  knownPullIds.clear();
  refresh();
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function setEl(id, text, className) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  if (className !== undefined) el.className = className;
}

function escHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function truncate(str, n) {
  return str && str.length > n ? str.slice(0, n - 1) + '…' : str;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function setPollDot(state) {
  const dot = document.getElementById('pollDot');
  dot.className = `poll-dot dot-${state}`;
}

function setFooterStatus(msg) {
  const el = document.getElementById('footerStatus');
  if (el) el.textContent = msg;
}

function timeAgo(isoStr) {
  if (!isoStr) return '—';
  try {
    const diff = Date.now() - new Date(isoStr).getTime();
    const secs = Math.floor(diff / 1000);
    if (secs < 60)  return `${secs}s ago`;
    const mins = Math.floor(secs / 60);
    if (mins < 60)  return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24)   return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  } catch {
    return '—';
  }
}

function formatChartTime(isoStr, showDate = false) {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr);
    const h = d.getHours().toString().padStart(2, '0');
    const m = d.getMinutes().toString().padStart(2, '0');
    if (showDate) {
      const mo = (d.getMonth() + 1).toString().padStart(2, '0');
      const dy = d.getDate().toString().padStart(2, '0');
      return `${mo}/${dy} ${h}:${m}`;
    }
    return `${h}:${m}`;
  } catch {
    return '';
  }
}

// ─── Bootstrap ───────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  // Sync currentPack with whatever the select shows on load
  currentPack = document.getElementById('packSelect').value;
  initChart();
  refresh();

  // Auto-refresh every 30 s
  refreshTimer = setInterval(refresh, REFRESH_MS);

  // Pack selector
  document.getElementById('packSelect').addEventListener('change', (e) => {
    currentPack = e.target.value;
    knownPullIds.clear();
    refresh();
  });

  // EV widget collapse toggle
  document.getElementById('evWidgetToggle')?.addEventListener('click', () => {
    const body = document.getElementById('evWidgetBody');
    const btn = document.getElementById('evWidgetToggle');
    if (!body || !btn) return;
    const collapsed = body.classList.toggle('collapsed');
    btn.textContent = collapsed ? '+' : '−';
  });

  // Drought widget collapse toggle
  document.getElementById('droughtWidgetToggle')?.addEventListener('click', () => {
    const body = document.getElementById('droughtWidgetBody');
    const btn = document.getElementById('droughtWidgetToggle');
    if (!body || !btn) return;
    const collapsed = body.classList.toggle('collapsed');
    btn.textContent = collapsed ? '+' : '−';
  });

  // Timeframe buttons
  document.querySelectorAll('.tf-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tf-btn').forEach((b) => b.classList.remove('tf-active'));
      btn.classList.add('tf-active');
      currentTimeframe = btn.dataset.tf;
      refresh();
    });
  });
});
