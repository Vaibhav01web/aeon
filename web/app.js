// Colourblind-safe sequential ramp (never red-green), implementation.md §8.9.
const RISK_COLORS = {
  low: [253, 212, 158],
  medium: [252, 141, 89],
  high: [215, 48, 31],
  critical: [127, 0, 0],
  'no mapped capacity': [184, 184, 184],
};

const BLANK_STYLE = {
  version: 8, sources: {},
  layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#eeeee8' } }],
};

const state = { resource: 'water', layer: 'risk', scenario: 'baseline', rows: [], meta: null, scenarios: [] };
const cache = {};
let overlay;

async function loadJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

function rowsFromColumnar(cols) {
  const keys = Object.keys(cols);
  const n = cols[keys[0]].length;
  const rows = new Array(n);
  for (let i = 0; i < n; i++) {
    const row = {};
    for (const k of keys) row[k] = cols[k][i];
    rows[i] = row;
  }
  return rows;
}

async function loadScenario(id) {
  if (!cache[id]) cache[id] = rowsFromColumnar(await loadJSON(`data/scenario_gap/${id}.json`));
  return cache[id];
}

function valueFor(d) {
  switch (state.layer) {
    case 'demand': return d.demand_p50;
    case 'capacity': return d.capacity;
    case 'gap': return Math.max(d.gap_p90, 0);
    case 'population': return d.population;
    default: return d.risk_score;
  }
}

function colorFor(d, maxVal) {
  if (state.layer === 'risk') return RISK_COLORS[d.risk_band] || [200, 200, 200];
  const t = maxVal > 0 ? Math.min(Math.sqrt(valueFor(d) / maxVal), 1) : 0;
  return [253 - 126 * t, 212 - 212 * t, 158 - 158 * t];
}

function fmt(v, resource) {
  if (v === null || v === undefined) return '—';
  const scale = state.meta.unit_scale[resource];
  const unit = state.meta.units[resource];
  return `${(v * scale).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${unit}`;
}

function showZone(d) {
  const card = document.getElementById('zone-card');
  card.hidden = false;
  card.replaceChildren();
  const add = (label, value, tag) => {
    const p = document.createElement('p');
    const b = document.createElement('b');
    b.textContent = `${label}: `;
    p.append(b, document.createTextNode(value));
    if (tag) {
      const s = document.createElement('span');
      s.className = `tag tag-${tag.toLowerCase()}`;
      s.textContent = tag;
      p.append(' ', s);
    }
    card.append(p);
  };
  const r = state.resource;
  add('Zone', d.zone_id);
  add('Population', Math.round(d.population).toLocaleString(), 'DERIVED');
  add('Demand P50', fmt(d.demand_p50, r), 'DERIVED');
  add('P10 – P90', `${fmt(d.demand_p10, r)} – ${fmt(d.demand_p90, r)}`, 'ASSUMED');
  add('Capacity', fmt(d.capacity, r), d.capacity_provenance === 'osm_tagged' ? 'MEASURED' : 'ASSUMED');
  add('Risk', d.risk_score === null ? d.risk_band : `${d.risk_score.toFixed(0)} / 100 (${d.risk_band})`);
  add('Why', d.top_driver);
}

function renderTotals() {
  const s = state.scenarios.find(x => x.scenario_id === state.scenario);
  const el = document.getElementById('totals');
  if (!s) { el.textContent = ''; return; }
  const r = state.resource;
  const bands = s.risk_by_band[r] || {};
  el.replaceChildren();
  const total = document.createElement('p');
  total.append(Object.assign(document.createElement('b'), { textContent: 'City demand: ' }),
               fmt(s.total_demand_p50_by_resource[r], r));
  const b = document.createElement('p');
  b.className = 'bands';
  b.textContent = ['critical', 'high', 'medium', 'low', 'no mapped capacity']
    .map(k => `${k}: ${bands[k] || 0}`).join(' · ');
  el.append(total, b);
}

function renderClimate() {
  const c = state.meta.climate_tco2e_per_year;
  const max = Math.max(...Object.values(c));
  const el = document.getElementById('climate-bars');
  el.replaceChildren();
  for (const [label, v] of Object.entries(c)) {
    const row = document.createElement('div');
    row.className = 'bar-row';
    const name = Object.assign(document.createElement('span'), { className: 'bar-label', textContent: label });
    const bar = Object.assign(document.createElement('span'), { className: 'bar' });
    bar.style.width = `${(100 * v / max).toFixed(1)}%`;
    const val = Object.assign(document.createElement('span'), { className: 'bar-val', textContent: Math.round(v).toLocaleString() });
    row.append(name, bar, val);
    el.append(row);
  }
  const src = Object.assign(document.createElement('p'), { className: 'notice',
    textContent: `Emission factors: ${state.meta.climate_sources.join('; ')}` });
  el.append(src);
}

function render() {
  const rows = state.rows.filter(d => d.resource === state.resource);
  const maxVal = Math.max(...rows.map(d => valueFor(d) || 0));
  const layer = new deck.H3HexagonLayer({
    id: 'zones',
    data: rows,
    getHexagon: d => d.zone_id,
    getFillColor: d => colorFor(d, maxVal),
    getElevation: d => d.demand_p50,
    extruded: false,
    stroked: false,
    pickable: true,
    opacity: 0.72,
    onClick: ({ object }) => object && showZone(object),
    updateTriggers: { getFillColor: [state.layer, state.resource, state.scenario] },
  });
  overlay.setProps({ layers: [layer] });
  renderTotals();
}

async function main() {
  const [meta, scenarios] = await Promise.all([loadJSON('data/meta.json'), loadJSON('data/scenarios.json')]);
  state.meta = meta;
  state.scenarios = scenarios;
  state.rows = await loadScenario('baseline');

  document.getElementById('city-name').textContent = `· ${meta.city.name}`;
  document.getElementById('attribution').textContent = meta.attribution;
  document.getElementById('notice').textContent = meta.notice;

  const sel = document.getElementById('scenario');
  for (const s of scenarios) sel.add(new Option(s.label, s.scenario_id));
  sel.onchange = async e => {
    state.scenario = e.target.value;
    state.rows = await loadScenario(state.scenario);
    render();
  };

  const [lon0, lat0, lon1, lat1] = meta.city.bbox;
  const map = new maplibregl.Map({
    container: 'map',
    style: 'https://tiles.openfreemap.org/styles/liberty',
    bounds: [[lon0, lat0], [lon1, lat1]],
    fitBoundsOptions: { padding: 20 },
  });
  let fellBack = false;
  map.on('error', () => {
    if (!fellBack && !map.isStyleLoaded()) { fellBack = true; map.setStyle(BLANK_STYLE); }
  });
  overlay = new deck.MapboxOverlay({ layers: [] });
  map.addControl(overlay);

  document.getElementById('resource').onchange = e => { state.resource = e.target.value; render(); };
  document.getElementById('layer').onchange = e => { state.layer = e.target.value; render(); };
  renderClimate();
  render();
}

main().catch(err => {
  document.getElementById('panel').append(Object.assign(document.createElement('p'), {
    className: 'error',
    textContent: `Could not load data (${err.message}). Run \`python -m urms.cli prototype\` first.`,
  }));
});
