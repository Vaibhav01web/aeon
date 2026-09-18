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

const rasterStyle = (tiles, attribution, maxzoom) => ({
  version: 8,
  sources: { imagery: { type: 'raster', tiles: [tiles], tileSize: 256, maxzoom, attribution } },
  layers: [{ id: 'imagery', type: 'raster', source: 'imagery' }],
});

const BASEMAPS = {
  streets: { style: 'https://tiles.openfreemap.org/styles/liberty', opacity: 0.72 },
  satellite: {
    style: rasterStyle(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      'Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community', 19),
    opacity: 0.55,
  },
  sentinel2: {
    style: rasterStyle(
      'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg',
      'Sentinel-2 cloudless 2024 by EOX IT Services GmbH (contains modified Copernicus Sentinel data 2024), CC BY-NC-SA 4.0', 15),
    opacity: 0.55,
  },
};

const state = { resource: 'water', layer: 'risk', scenario: 'baseline', basemap: 'streets', rows: [], meta: null, scenarios: [] };
let map;
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

const isPhone = () => window.matchMedia('(max-width: 700px)').matches;

function setCollapsed(collapsed) {
  const panel = document.getElementById('panel');
  const btn = document.getElementById('toggle');
  panel.classList.toggle('collapsed', collapsed);
  btn.textContent = collapsed ? 'Show' : 'Hide';
  btn.setAttribute('aria-expanded', String(!collapsed));
}

function showZone(d) {
  const card = document.getElementById('zone-card');
  card.hidden = false;
  document.getElementById('hint').hidden = true;
  setCollapsed(false);
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
  card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
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
    opacity: BASEMAPS[state.basemap].opacity,
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

  document.getElementById('toggle').onclick = () =>
    setCollapsed(!document.getElementById('panel').classList.contains('collapsed'));

  const [lon0, lat0, lon1, lat1] = meta.city.bbox;
  const panelH = document.getElementById('panel').offsetHeight;
  map = new maplibregl.Map({
    container: 'map',
    style: BASEMAPS.streets.style,
    bounds: [[lon0, lat0], [lon1, lat1]],
    fitBoundsOptions: {
      padding: isPhone()
        ? { top: 12, left: 12, right: 12, bottom: panelH + 12 }
        : { top: 20, bottom: 20, right: 20, left: 350 },
    },
    dragRotate: false,
    pitchWithRotate: false,
    touchPitch: false,
    attributionControl: { compact: true },
  });
  map.touchZoomRotate.disableRotation();
  map.keyboard.disableRotation();
  // Only fall back to a blank ground if the very first basemap never loads;
  // later tile errors (e.g. mid basemap switch) must not wipe the chosen style.
  let firstLoad = false;
  map.once('load', () => { firstLoad = true; });
  map.on('error', e => {
    if (!firstLoad && !e.sourceId && !e.tile) { firstLoad = true; map.setStyle(BLANK_STYLE); }
  });

  for (const btn of document.querySelectorAll('[data-basemap]')) {
    btn.onclick = () => {
      const name = btn.dataset.basemap;
      if (name === state.basemap) return;
      state.basemap = name;
      for (const b of document.querySelectorAll('[data-basemap]')) b.setAttribute('aria-pressed', String(b === btn));
      map.setStyle(BASEMAPS[name].style);
      render();
    };
  }
  overlay = new deck.MapLibreOverlay({ layers: [] });
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
