// Colourblind-safe sequential ramp (never red-green), implementation.md §8.9.
const RISK_COLORS = {
  low: [253, 212, 158],
  medium: [252, 141, 89],
  high: [215, 48, 31],
  critical: [127, 0, 0],
  'no mapped capacity': [150, 150, 150, 90],
};
const BANDS = ['critical', 'high', 'medium', 'low', 'no mapped capacity'];
const BAND_SHORT = { 'no mapped capacity': 'no data' };
const NODATA_CSS = 'rgba(150,150,150,0.45)';
const rgb = c => `rgb(${c[0]},${c[1]},${c[2]})`;
const bandCss = b => (b === 'no mapped capacity' ? NODATA_CSS : rgb(RISK_COLORS[b]));

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

const LAYER_LABEL = { demand: 'Demand (P50)', capacity: 'Capacity', gap: 'Gap (P90 − capacity)', population: 'Population' };
const TOP_TITLE = {
  risk: 'Riskiest zones', demand: 'Highest demand', capacity: 'Most capacity',
  gap: 'Largest gaps', population: 'Most populous',
};

const state = {
  resource: 'water', layer: 'risk', scenario: 'baseline', basemap: 'satellite', opacity: BASEMAPS.satellite.opacity,
  selected: null, rows: [], meta: null, scenarios: [],
};
const cache = {};
let map, overlay;
const $ = id => document.getElementById(id);
const el = (tag, props = {}, ...children) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...children);
  return n;
};

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

const currentRows = () => state.rows.filter(d => d.resource === state.resource);

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

function fmt(v, resource = state.resource) {
  if (v === null || v === undefined) return '—';
  const scaled = v * state.meta.unit_scale[resource];
  const digits = Math.abs(scaled) >= 100 ? 0 : Math.abs(scaled) >= 10 ? 1 : 2;
  return `${scaled.toLocaleString(undefined, { maximumFractionDigits: digits })} ${state.meta.units[resource]}`;
}
const fmtPeople = v => Math.round(v).toLocaleString();
function fmtRange(lo, hi, resource = state.resource) {
  const s = state.meta.unit_scale[resource];
  const n = v => (v * s).toLocaleString(undefined, { maximumFractionDigits: v * s >= 10 ? 1 : 2 });
  return `${n(lo)}–${n(hi)} ${state.meta.units[resource]}`;
}
const topPct = d => (d.risk_score >= 50
  ? `top ${Math.max(1, Math.round(100 - d.risk_score))}%`
  : `lowest ${Math.max(1, Math.round(d.risk_score))}%`);
const fmtValue = d => (state.layer === 'population' ? fmtPeople(d.population)
  : state.layer === 'risk' ? topPct(d) : fmt(valueFor(d)));
const shortId = id => `${id.slice(0, 9)}…`;
const isPhone = () => window.matchMedia('(max-width: 700px)').matches;

// ---- dashboard cards -------------------------------------------------------

function renderHero() {
  const r = state.resource;
  const s = state.scenarios.find(x => x.scenario_id === state.scenario);
  const base = state.scenarios.find(x => x.scenario_id === 'baseline');
  $('hero-label').textContent = state.scenario === 'baseline' ? 'City demand' : `City demand · ${s.label}`;
  $('hero-value').textContent = fmt(s.total_demand_p50_by_resource[r]);
  const people = (state.meta.population_target / 1e6).toFixed(2);
  $('hero-sub').textContent = `${state.meta.demand_basis[r]} · ${people} M people · ${currentRows().length.toLocaleString()} urban zones`;
  const delta = $('hero-delta');
  if (state.scenario === 'baseline') { delta.hidden = true; return; }
  const pct = 100 * (s.total_demand_p50_by_resource[r] / base.total_demand_p50_by_resource[r] - 1);
  delta.hidden = false;
  delta.className = `delta ${pct > 0.05 ? 'up' : pct < -0.05 ? 'down' : 'flat'}`;
  delta.textContent = `${pct > 0 ? '+' : ''}${pct.toFixed(1)}% vs baseline`;
}

function renderLegend() {
  const box = $('legend');
  box.replaceChildren();
  const rows = currentRows();

  if (state.layer === 'risk') {
    const counts = Object.fromEntries(BANDS.map(b => [b, 0]));
    for (const d of rows) counts[d.risk_band] = (counts[d.risk_band] || 0) + 1;
    const stack = el('div', { className: 'stack' });
    for (const b of BANDS) {
      if (!counts[b]) continue;
      const seg = el('span', { title: `${b}: ${counts[b]} zones` });
      seg.style.width = `${(100 * counts[b] / rows.length).toFixed(2)}%`;
      seg.style.background = bandCss(b);
      stack.append(seg);
    }
    const keys = el('div', { className: 'keys' });
    for (const b of BANDS) {
      const swatch = el('i');
      swatch.style.background = bandCss(b);
      keys.append(el('div', { className: 'key' }, el('b', { textContent: counts[b] }), swatch, BAND_SHORT[b] || b));
    }
    box.append(el('span', { className: 'eyebrow', textContent: 'Shortage risk by zone' }), stack, keys,
      el('p', { className: 'caption', textContent: 'Ranked against the baseline city: top 10% critical, next 20% high, next 30% medium.' }));
    return;
  }

  const vals = rows.map(valueFor).filter(v => v !== null && v !== undefined);
  const max = Math.max(...vals, 0);
  const label = state.layer === 'population' ? fmtPeople : v => fmt(v);
  box.append(
    el('span', { className: 'eyebrow', textContent: `${LAYER_LABEL[state.layer]} per zone` }),
    el('div', { className: 'ramp' }),
    el('div', { className: 'ramp-labels' }, el('span', { textContent: label(0) }), el('span', { textContent: label(max) })),
  );
}

function renderTop() {
  $('top-title').textContent = TOP_TITLE[state.layer];
  const rows = currentRows().filter(d => (state.layer === 'risk' ? d.risk_score !== null : true));
  const sorted = rows.slice().sort((a, b) =>
    state.layer === 'risk' ? (b.risk_score - a.risk_score) || (b.gap_p90 - a.gap_p90) : valueFor(b) - valueFor(a));
  const list = $('top-list');
  list.replaceChildren();
  for (const d of sorted.slice(0, 6)) {
    const dot = el('span', { className: 'dot' });
    dot.style.background = bandCss(d.risk_band);
    const btn = el('button', { type: 'button' },
      el('span', {}, dot, `Zone ${shortId(d.zone_id)}`),
      el('span', { className: 'val', textContent: fmtValue(d) }));
    if (d.zone_id === state.selected) btn.setAttribute('aria-current', 'true');
    btn.onclick = () => selectZone(d, true);
    list.append(el('li', {}, btn));
  }
  if (!sorted.length) list.append(el('li', { className: 'hint', textContent: 'No mapped zones for this resource.' }));
}

function renderZone() {
  const card = $('zone-card');
  const d = currentRows().find(x => x.zone_id === state.selected);
  if (!d) {
    card.replaceChildren(el('h2', { textContent: 'Selected zone' }),
      el('p', { className: 'hint', textContent: 'Click a hexagon on the map, or a zone in the list.' }));
    return;
  }
  const badge = el('span', { className: 'badge', textContent: d.risk_band });
  badge.style.background = d.risk_band === 'no mapped capacity' ? '#8a8f95' : rgb(RISK_COLORS[d.risk_band]);
  if (d.risk_band === 'low') badge.style.color = '#4a2c00';

  const kv = el('dl', { className: 'kv' });
  const row = (k, v, tag) => {
    const dd = el('dd', {}, v);
    if (tag) dd.append(el('span', { className: `tag tag-${tag.toLowerCase()}`, textContent: tag }));
    kv.append(el('dt', { textContent: k }), dd);
  };
  row('Population', fmtPeople(d.population), 'DERIVED');
  row('Demand', fmt(d.demand_p50), 'DERIVED');
  row('P10–P90', fmtRange(d.demand_p10, d.demand_p90), 'ASSUMED');
  row('Capacity', fmt(d.capacity), d.capacity_provenance === 'osm_tagged' ? 'MEASURED' : 'ASSUMED');
  if (d.risk_score !== null) row('Risk rank', `${topPct(d)} of zones`);

  card.replaceChildren(
    el('h2', {}, el('span', { textContent: `Zone ${shortId(d.zone_id)}` }), badge),
    kv,
    el('p', { className: 'why', textContent: `Main driver: ${d.top_driver}` }),
  );
}

function renderClimate() {
  const c = state.meta.climate_tco2e_per_year;
  const max = Math.max(...Object.values(c));
  const box = $('climate-bars');
  box.replaceChildren();
  for (const [label, v] of Object.entries(c)) {
    const bar = el('span', { className: 'bar' });
    bar.style.width = `${(100 * v / max).toFixed(1)}%`;
    box.append(el('div', { className: 'bar-row' },
      el('span', { textContent: label }), bar,
      el('span', { className: 'bar-val', textContent: Math.round(v).toLocaleString() })));
  }
  box.append(el('p', { className: 'notice', textContent: `Emission factors: ${state.meta.climate_sources.join('; ')}` }));
}

function selectZone(d, fly) {
  state.selected = d.zone_id;
  if (fly) {
    const [lat, lng] = h3.cellToLatLng(d.zone_id);
    map.flyTo({ center: [lng, lat], zoom: Math.max(map.getZoom(), 13), speed: 1.4 });
  }
  document.querySelector('.cards').classList.add('has-selection');
  render();
  if (isPhone()) $('zone-card').scrollIntoView({ block: 'start', behavior: 'smooth' });
}

// ---- map ------------------------------------------------------------------

function render() {
  const rows = currentRows();
  const maxVal = Math.max(...rows.map(d => valueFor(d) || 0));
  const selected = rows.filter(d => d.zone_id === state.selected);
  overlay.setProps({
    layers: [
      new deck.H3HexagonLayer({
        id: 'zones',
        data: rows,
        getHexagon: d => d.zone_id,
        getFillColor: d => colorFor(d, maxVal),
        extruded: false,
        stroked: true,
        getLineColor: [255, 255, 255, 110],
        lineWidthUnits: 'pixels',
        getLineWidth: 0.6,
        pickable: true,
        autoHighlight: true,
        highlightColor: [255, 255, 255, 90],
        opacity: state.opacity,
        onClick: ({ object }) => object && selectZone(object, false),
        updateTriggers: { getFillColor: [state.layer, state.resource, state.scenario] },
      }),
      new deck.PathLayer({
        id: 'selected',
        // h3 returns [lat, lng]; deck needs [lng, lat]. Close the ring.
        data: selected.map(d => {
          const ring = h3.cellToBoundary(d.zone_id).map(([lat, lng]) => [lng, lat]);
          return { path: [...ring, ring[0]] };
        }),
        getPath: d => d.path,
        getColor: [20, 184, 166, 255],
        widthUnits: 'pixels',
        getWidth: 4,
        jointRounded: true,
        parameters: { depthCompare: 'always', depthWriteEnabled: false },
      }),
    ],
  });
  renderHero();
  renderLegend();
  renderTop();
  renderZone();
}

async function main() {
  const [meta, scenarios] = await Promise.all([loadJSON('data/meta.json'), loadJSON('data/scenarios.json')]);
  state.meta = meta;
  state.scenarios = scenarios;
  state.rows = await loadScenario('baseline');

  $('city-name').textContent = `· ${meta.city.name}`;
  $('attribution').textContent = meta.attribution;
  $('notice').textContent = meta.notice;

  for (const s of scenarios) $('scenario').add(new Option(s.label, s.scenario_id));
  $('scenario').onchange = async e => {
    state.scenario = e.target.value;
    state.rows = await loadScenario(state.scenario);
    render();
  };
  $('layer').onchange = e => { state.layer = e.target.value; render(); };
  $('opacity').oninput = e => { state.opacity = Number(e.target.value); render(); };

  for (const tab of document.querySelectorAll('[data-resource]')) {
    tab.onclick = () => {
      state.resource = tab.dataset.resource;
      for (const t of document.querySelectorAll('[data-resource]')) t.setAttribute('aria-selected', String(t === tab));
      render();
    };
  }

  const [lon0, lat0, lon1, lat1] = meta.city.bbox;
  map = new maplibregl.Map({
    container: 'map',
    style: BASEMAPS[state.basemap].style,
    bounds: [[lon0, lat0], [lon1, lat1]],
    fitBoundsOptions: { padding: isPhone() ? { top: 48, left: 6, right: 6, bottom: 6 } : { top: 90, left: 20, right: 20, bottom: 20 } },
    dragRotate: false,
    pitchWithRotate: false,
    touchPitch: false,
    attributionControl: { compact: true },
  });
  map.touchZoomRotate.disableRotation();
  map.keyboard.disableRotation();
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
  // Compact attribution opens itself on load; on phones that covers the map.
  const collapseAttribution = () => {
    if (isPhone()) document.querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show');
  };
  map.on('load', collapseAttribution);
  map.on('styledata', collapseAttribution);

  // Only fall back to a blank ground if the first style itself fails to load;
  // individual tile errors and later basemap switches must not trigger it.
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
      state.opacity = BASEMAPS[name].opacity;
      $('opacity').value = state.opacity;
      render();
    };
  }

  overlay = new deck.MapLibreOverlay({ layers: [] });
  map.addControl(overlay);
  renderClimate();
  render();
}

main().catch(err => {
  $('dash').prepend(el('p', {
    className: 'error',
    textContent: `Could not load data (${err.message}). Run \`python -m urms.cli prototype\` first.`,
  }));
});
