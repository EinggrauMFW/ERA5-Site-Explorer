const $ = selector => document.querySelector(selector);
const form = $('#fetch-form');
const latInput = $('#latitude');
const lonInput = $('#longitude');
const statusCard = $('#status-card');
const statusEl = $('#status');
const logEl = $('#log');
const filesEl = $('#files');
const analysisEl = $('#analysis');
const formError = $('#form-error');
const submitButton = $('#submit');
const cancelButton = $('#cancel');
const deleteButton = $('#delete');
let marker;
let gridMarker;
let activeJob = null;
let pollTimer = null;
let charts = [];

const ACTIVE = ['queued', 'running'];
const PRODUCT_SHORT = { 'single-levels': 'A · single levels', 'mars-surface': 'MARS surface', 'wave-spectra': 'B · wave spectra' };

function showError(message) {
  formError.textContent = message || '';
  formError.hidden = !message;
}

function studyBounds() {
  const lat = Number(latInput.value), lng = Number(lonInput.value);
  const buffer = Number($('#buffer').value) || 0;
  // Matches the fetcher, which clips at the date line instead of wrapping.
  return {
    north: Math.min(90, lat + buffer), south: Math.max(-90, lat - buffer),
    west: Math.max(-180, lng - buffer), east: Math.min(180, lng + buffer)
  };
}

function updateStudyArea() {
  const lat = Number(latInput.value), lng = Number(lonInput.value);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
  const bounds = studyBounds();
  $('#selected-coordinate').textContent = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
  $('#selected-area').textContent =
    `ERA5 box · N ${bounds.north.toFixed(3)} · W ${bounds.west.toFixed(3)} · S ${bounds.south.toFixed(3)} · E ${bounds.east.toFixed(3)} (snapped outward to the 0.5° wave grid on request)`;
  const source = map.getSource('study-area');
  if (source) source.setData({
    type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [[
      [bounds.west, bounds.south], [bounds.east, bounds.south],
      [bounds.east, bounds.north], [bounds.west, bounds.north], [bounds.west, bounds.south]
    ]] }
  });
}

function setCoordinates(lng, lat) {
  lonInput.value = Number(lng).toFixed(5);
  latInput.value = Number(lat).toFixed(5);
  if (marker) marker.setLngLat([lng, lat]);
  $('#map-note').textContent = `${Number(lat).toFixed(4)}, ${Number(lng).toFixed(4)}`;
  updateStudyArea();
}

const map = new maplibregl.Map({
  container: 'map',
  style: 'https://tiles.openfreemap.org/styles/liberty',
  center: [107.6191, -6.9175],
  zoom: 7
});
map.addControl(new maplibregl.NavigationControl(), 'bottom-right');
marker = new maplibregl.Marker({ color: '#c9ff4a', draggable: true })
  .setLngLat([107.6191, -6.9175]).addTo(map);
map.on('click', event => {
  // A click on a grid node selects it for analysis; anywhere else it moves the site marker.
  if (map.getLayer('nodes-circle') && map.queryRenderedFeatures(event.point, { layers: ['nodes-circle'] }).length) return;
  setCoordinates(event.lngLat.lng, event.lngLat.lat);
});
marker.on('dragend', () => { const p = marker.getLngLat(); setCoordinates(p.lng, p.lat); });
function addMapLayers() {
  // Safe to call repeatedly; waits until the (possibly replaced) style is ready.
  if (!map.isStyleLoaded()) return;
  if (!map.getSource('study-area')) {
    map.addSource('study-area', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addLayer({ id:'study-area-fill', type:'fill', source:'study-area', paint:{ 'fill-color':'#c9ff4a', 'fill-opacity':0.18 } });
    map.addLayer({ id:'study-area-line', type:'line', source:'study-area', paint:{ 'line-color':'#10241e', 'line-width':2, 'line-dasharray':[3,2] } });
  }
  if (!map.getSource('nodes')) {
    map.addSource('nodes', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addLayer({ id:'nodes-circle', type:'circle', source:'nodes', paint:{
      'circle-radius': ['case', ['get', 'selected'], 11, 7],
      'circle-color': ['case', ['get', 'valid'],
        ['interpolate', ['linear'], ['get', 'scaled'], 0, '#d6e8dd', 1, '#0f3b42'], '#b9c0bd'],
      'circle-stroke-width': ['case', ['get', 'selected'], 3, 1],
      'circle-stroke-color': ['case', ['get', 'selected'], '#c9ff4a', '#ffffff']
    } });
    map.on('click', 'nodes-circle', event => {
      const f = event.features[0];
      if (f) selectNode(nodeAt(f.properties.lat, f.properties.lon));
    });
    map.on('mouseenter', 'nodes-circle', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'nodes-circle', () => { map.getCanvas().style.cursor = ''; });
  }
  updateStudyArea();
  renderNodesOnMap();
}
map.on('style.load', addMapLayers);
// 'styledata' also fires when the fallback style replaces a basemap that never loaded; the handler is idempotent.
map.on('styledata', addMapLayers);

// If the remote basemap cannot load (offline, blocked), fall back to a plain background so the
// study box and grid nodes still draw.
const FALLBACK_STYLE = { version: 8, sources: {}, layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#dfe7e3' } }] };
setTimeout(() => {
  if (!map.isStyleLoaded()) {
    console.warn('Basemap style did not load; using a plain background.');
    map.setStyle(FALLBACK_STYLE);
  }
}, 8000);

[latInput, lonInput].forEach(input => input.addEventListener('change', () => {
  const lat = Number(latInput.value), lng = Number(lonInput.value);
  if (Number.isFinite(lat) && Number.isFinite(lng)) setCoordinates(lng, lat);
}));
$('#buffer').addEventListener('input', updateStudyArea);

// ERA5 trails real time, so default to the latest date the server accepts.
const latest = new Date($('#end').max + 'T00:00:00Z');
const monthBefore = new Date(latest); monthBefore.setUTCMonth(latest.getUTCMonth() - 1);
$('#end').value = latest.toISOString().slice(0, 10);
$('#start').value = monthBefore.toISOString().slice(0, 10);

// --- product options -------------------------------------------------------------

const config = JSON.parse($('#product-config').textContent);
const productSelect = $('#product');
const PRODUCT_NOTES = {
  'single-levels': 'Option A. Integrated wave parameters from reanalysis-era5-single-levels: fast, 1940 to present. mwp is the energy period Te (Tm-1), so no period is guessed. Tier 3 also downloads the model depth.',
  'mars-surface': 'Raw MARS access (reanalysis-era5-complete, oper stream). Slower: it reads from the ECMWF tape archive.',
  'wave-spectra': 'Option B. Full 2D wave spectra (24 directions × 30 frequencies, 0.5° grid) from the MARS wave stream. Slow and large; every parameter and the finite-depth flux, flux direction and sector flux are computed from E(f, θ). Also downloads the model depth.'
};

function applyProduct() {
  const product = productSelect.value;
  document.querySelectorAll('.options').forEach(box => { box.hidden = box.dataset.product !== product; });
  $('#product-note').textContent = PRODUCT_NOTES[product];
  $('#time-step').value = String(config.defaultSteps[product]);
  const buffer = $('#buffer');
  buffer.max = config.maxBuffer[product];
  if (Number(buffer.value) > config.maxBuffer[product]) buffer.value = config.maxBuffer[product];
  $('#limits').textContent =
    `At most ${config.maxYears[product]} years per request, buffer up to ${config.maxBuffer[product]}°.`;
  updateStudyArea();
}
productSelect.addEventListener('change', applyProduct);

function checkedValues(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map(input => input.value);
}

// --- job status -------------------------------------------------------------------

function renderFiles(job) {
  filesEl.replaceChildren(...job.files.map(file => {
    const link = document.createElement('a');
    link.href = `/api/jobs/${job.id}/files/${encodeURIComponent(file.name)}`;
    link.textContent = `${file.name} · ${(file.size / 1048576).toFixed(1)} MB`;
    return link;
  }));
}

function renderJob(job) {
  statusEl.textContent = job.status;
  $('#job-id').textContent = `JOB ${job.id} · ${PRODUCT_SHORT[job.product] || 'single levels'}`;
  logEl.textContent = job.log.length ? job.log.join('\n') : 'Waiting for the fetcher…';
  logEl.scrollTop = logEl.scrollHeight;
  renderFiles(job);
  const active = ACTIVE.includes(job.status);
  cancelButton.hidden = !active;
  deleteButton.hidden = active;
  submitButton.disabled = active;
}

async function poll(jobId, failures = 0) {
  clearTimeout(pollTimer);
  if (activeJob !== jobId) return;
  let job;
  try {
    const response = await fetch(`/api/jobs/${jobId}`);
    if (response.status === 404) { logEl.textContent = 'This job no longer exists.'; submitButton.disabled = false; return; }
    job = await response.json();
  } catch (error) {
    // Server restarting or briefly unreachable: back off, then give up.
    if (failures >= 5) {
      logEl.textContent = 'Lost contact with the server. Reload the page to resume.';
      submitButton.disabled = false;
      return;
    }
    pollTimer = setTimeout(() => poll(jobId, failures + 1), 2000 * (failures + 1));
    return;
  }
  if (activeJob !== jobId) return;
  renderJob(job);
  if (ACTIVE.includes(job.status)) {
    pollTimer = setTimeout(() => poll(jobId), 1500);
  } else {
    refreshRecent();
    if (job.status === 'complete' && !job.dry_run && job.files.length) loadAnalysis(jobId);
  }
}

function openJob(jobId) {
  activeJob = jobId;
  clearNodes();
  history.replaceState(null, '', `#job=${jobId}`);
  statusCard.hidden = false;
  analysisEl.hidden = true;
  destroyCharts();
  statusCard.scrollIntoView({ behavior:'smooth' });
  poll(jobId);
}

async function refreshRecent() {
  try {
    const jobs = await (await fetch('/api/jobs')).json();
    $('#recent').hidden = !jobs.length;
    fillCrosscheckSelects(jobs);
    $('#recent-list').replaceChildren(...jobs.slice(0, 15).map(job => {
      const link = document.createElement('a');
      link.href = `#job=${job.id}`;
      link.textContent = `${PRODUCT_SHORT[job.product] || 'single levels'} · ${job.start} → ${job.end} · ${job.latitude.toFixed(3)}, ${job.longitude.toFixed(3)} · ${job.status}${job.dry_run ? ' (preview)' : ''}`;
      link.addEventListener('click', event => { event.preventDefault(); openJob(job.id); });
      return link;
    }));
  } catch (error) { /* the recent list is a convenience */ }
}

cancelButton.addEventListener('click', async () => {
  if (activeJob) await fetch(`/api/jobs/${activeJob}/cancel`, { method:'POST' });
});
deleteButton.addEventListener('click', async () => {
  if (!activeJob || !confirm('Delete this job and its downloaded files?')) return;
  await fetch(`/api/jobs/${activeJob}`, { method:'DELETE' });
  activeJob = null;
  history.replaceState(null, '', location.pathname);
  statusCard.hidden = true;
  analysisEl.hidden = true;
  destroyCharts();
  clearNodes();
  refreshRecent();
});

// --- grid nodes -------------------------------------------------------------------

let nodeData = null;        // /nodes payload of the active job
let selectedNode = null;    // {lat, lon} chosen by the user, or null = nearest ocean cell
let highlightNode = null;   // the grid cell the current analysis actually used
let nodeMetric = 'flux';

const NODE_METRICS = {
  flux: { label: 'Mean energy flux J (kW/m)', short: 'J (kW/m)' },
  hm0: { label: 'Mean Hm0 (m)', short: 'Hm0 (m)' },
  te: { label: 'Mean Te (s)', short: 'Te (s)' },
  value: { label: 'Mean value', short: 'Mean' }
};

function nodeAt(lat, lon) {
  return nodeData && nodeData.nodes.find(n => Math.abs(n.lat - lat) < 1e-3 && Math.abs(n.lon - lon) < 1e-3);
}

function availableMetrics() {
  if (!nodeData) return [];
  return Object.keys(NODE_METRICS).filter(key => nodeData.nodes.some(n => n[key] !== undefined && n[key] !== null));
}

function nodeQuery() {
  return selectedNode ? `node_lat=${selectedNode.lat}&node_lon=${selectedNode.lon}` : '';
}

function renderNodesOnMap() {
  const source = map.getSource && map.getSource('nodes');
  if (!source) return;
  if (!nodeData) { source.setData({ type: 'FeatureCollection', features: [] }); return; }
  const values = nodeData.nodes.filter(n => n.valid && n[nodeMetric] != null).map(n => n[nodeMetric]);
  const low = Math.min(...values), high = Math.max(...values);
  source.setData({ type: 'FeatureCollection', features: nodeData.nodes.map(n => ({
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [n.lon, n.lat] },
    properties: {
      lat: n.lat, lon: n.lon, valid: n.valid,
      scaled: n.valid && n[nodeMetric] != null && high > low ? (n[nodeMetric] - low) / (high - low) : 0,
      selected: !!highlightNode && Math.abs(n.lat - highlightNode.lat) < 1e-3 && Math.abs(n.lon - highlightNode.lon) < 1e-3
    }
  })) });
}

function renderNodePanel() {
  const panel = $('#nodes-panel');
  if (!nodeData) { panel.hidden = true; return; }
  panel.hidden = false;
  const metrics = availableMetrics();
  if (!metrics.includes(nodeMetric)) nodeMetric = metrics[0] || 'value';
  const select = $('#node-metric');
  select.replaceChildren(...metrics.map(key => {
    const option = node('option', '', key === 'value' && nodeData.value_label ? nodeData.value_label : NODE_METRICS[key].label);
    option.value = key;
    return option;
  }));
  select.value = nodeMetric;

  const note = `${nodeData.n_ocean} ocean node${nodeData.n_ocean === 1 ? '' : 's'}, ${nodeData.n_land} land or ice node${nodeData.n_land === 1 ? '' : 's'} ` +
    '(grey on the map; no data, so they cannot be analysed). Click a node on the map or a row below to analyse it. ' +
    'Means are over the whole record, flux computed per record first.' + (nodeData.note ? ' ' + nodeData.note : '');
  $('#nodes-note').textContent = note;

  const columns = [['lat', 'Lat'], ['lon', 'Lon']];
  if (nodeData.nodes.some(n => n.depth != null)) columns.push(['depth', 'Depth (m)']);
  metrics.forEach(key => columns.push([key, key === 'value' && nodeData.value_label ? nodeData.value_label : NODE_METRICS[key].short]));
  columns.push(['distance_km', 'From site (km)']);

  const rows = nodeData.nodes.slice().sort((a, b) => {
    if (a.valid !== b.valid) return a.valid ? -1 : 1;
    return (b[nodeMetric] ?? -Infinity) - (a[nodeMetric] ?? -Infinity);
  });
  const table = $('#nodes-table');
  const head = node('tr');
  columns.forEach(([, text]) => head.append(node('th', '', text)));
  const body = rows.slice(0, 250).map(n => {
    const tr = node('tr');
    const selected = highlightNode && Math.abs(n.lat - highlightNode.lat) < 1e-3 && Math.abs(n.lon - highlightNode.lon) < 1e-3;
    const isDefault = nodeData.default && Math.abs(n.lat - nodeData.default.lat) < 1e-3 && Math.abs(n.lon - nodeData.default.lon) < 1e-3;
    tr.className = [n.valid ? '' : 'land', selected ? 'selected' : ''].join(' ').trim();
    columns.forEach(([key]) => {
      const value = n[key];
      const text = key === 'lat' || key === 'lon' ? Number(value).toFixed(2)
        : value == null ? (n.valid ? '' : 'land / ice') : String(key === 'depth' || key === 'distance_km' ? Math.round(value * 10) / 10 : value);
      const td = node('td', '', text);
      if (key === 'lon' && isDefault) td.append(node('small', 'tag', ' nearest ocean cell'));
      tr.append(td);
    });
    if (n.valid) {
      tr.tabIndex = 0;
      tr.addEventListener('click', () => selectNode(n));
      tr.addEventListener('keydown', event => { if (event.key === 'Enter') selectNode(n); });
    }
    return tr;
  });
  table.replaceChildren(head, ...body);
  if (rows.length > 250) $('#nodes-note').textContent += ` Showing the first 250 of ${rows.length} nodes.`;
}

async function loadNodes(jobId) {
  try {
    const response = await fetch(`/api/jobs/${jobId}/nodes`);
    const data = await response.json();
    if (activeJob !== jobId) return;
    if (!response.ok) { nodeData = null; renderNodesOnMap(); renderNodePanel(); return; }
    nodeData = { ...data, jobId };
    renderNodesOnMap();
    renderNodePanel();
    const lats = nodeData.nodes.map(n => n.lat), lons = nodeData.nodes.map(n => n.lon);
    const site = nodeData.requested_coordinate;
    map.fitBounds([[Math.min(...lons, site.longitude), Math.min(...lats, site.latitude)],
                   [Math.max(...lons, site.longitude), Math.max(...lats, site.latitude)]], { padding: 70, maxZoom: 9, duration: 600 });
  } catch (error) { /* the node picker is an aid; the analysis still works */ }
}

function clearNodes() {
  nodeData = null; selectedNode = null; highlightNode = null;
  renderNodesOnMap();
  $('#nodes-panel').hidden = true;
}

function selectNode(chosen) {
  if (!chosen || !chosen.valid || !activeJob) return;
  const isDefault = nodeData.default && Math.abs(chosen.lat - nodeData.default.lat) < 1e-3 && Math.abs(chosen.lon - nodeData.default.lon) < 1e-3;
  selectedNode = isDefault ? null : { lat: chosen.lat, lon: chosen.lon };
  loadAnalysis(activeJob);
}

$('#node-metric').addEventListener('change', event => {
  nodeMetric = event.target.value;
  renderNodesOnMap();
  renderNodePanel();
});
$('#node-reset').addEventListener('click', () => {
  if (!selectedNode || !activeJob) return;
  selectedNode = null;
  loadAnalysis(activeJob);
});

// --- analysis ---------------------------------------------------------------------

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function metric(label, value, detail) {
  const el = node('div', 'metric');
  el.append(node('span', '', label), node('strong', '', value), node('small', '', detail));
  return el;
}

function destroyCharts() {
  charts.forEach(chart => chart.destroy());
  charts = [];
  for (const id of ['#charts', '#charts-advanced', '#sections']) $(id).replaceChildren();
  $('#advanced').hidden = true;
}

const fmt = (value, unit) => value === null || value === undefined ? '—' : `${value} ${unit}`.trim();

function chartTheme() {
  const css = getComputedStyle(document.documentElement);
  const line = css.getPropertyValue('--sea').trim(), grid = css.getPropertyValue('--line').trim();
  return { line, axis: { stroke:'#66716d', grid:{ stroke:grid, width:1 }, ticks:{ stroke:grid, width:1 } } };
}

function addChart(item, stamps, container) {
  if (!item.values.some(value => value !== null)) return;
  const article = node('article', 'chart');
  const summary = node('p', '', item.circular
    ? `Circular mean ${fmt(item.mean, item.unit)} · block-mean direction plotted`
    : `Mean ${fmt(item.mean, item.unit)} · P95 ${fmt(item.p95, item.unit)} · Max ${fmt(item.maximum, item.unit)}`);
  const holder = node('div');
  article.append(node('h3', '', item.label), summary, holder);
  container.append(article);

  const { line, axis } = chartTheme();
  const series = [{}, item.circular
    ? { label:`${item.label} (${item.unit})`, paths:() => null, points:{ show:true, size:4, fill:line, stroke:line } }
    : { label:`${item.label} (${item.unit})`, stroke:line, width:2 }];
  const data = [stamps, item.values];
  if (item.max) {
    series.push({ label:`Block max (${item.unit})`, stroke:'rgba(21,63,69,.35)', width:1 });
    data.push(item.max);
  }
  charts.push(new uPlot({
    width: Math.max(280, holder.clientWidth || article.clientWidth - 36), height: 220,
    series, scales: item.circular ? { y:{ range:[0, 360] } } : {},
    axes: [axis, { ...axis, size: 56 }], legend: { live:true }, cursor: { drag:{ x:true, y:false } }
  }, data, holder));
}

window.addEventListener('resize', () => charts.forEach(chart => {
  chart.setSize({ width: Math.max(280, chart.root.parentElement.clientWidth), height: chart.height });
}));

function renderKv(section) {
  const table = node('table', 'kv');
  for (const entry of section.rows) {
    const tr = node('tr');
    const label = node('th', '', entry.label);
    const value = node('td', '', entry.value);
    if (entry.note) value.append(node('small', '', entry.note));
    tr.append(label, value);
    table.append(tr);
  }
  return table;
}

function renderTable(section) {
  const table = node('table', 'grid-table');
  const head = node('tr');
  section.columns.forEach(column => head.append(node('th', '', column)));
  table.append(head);
  section.rows.forEach(values => {
    const tr = node('tr');
    values.forEach(value => tr.append(node('td', '', value)));
    table.append(tr);
  });
  return table;
}

function renderScatter(section) {
  const wrap = node('div');
  const toggle = node('select', 'scatter-toggle');
  [['hours_pct', 'Share of records (%)'], ['energy_pct', section.weight_label + ' (%)']].forEach(([value, text]) => {
    const option = node('option', '', text); option.value = value; toggle.append(option);
  });
  const holder = node('div', 'scroll');
  const draw = () => {
    const matrix = section[toggle.value];
    const flat = matrix.flat();
    const peak = Math.max(...flat, 1e-9);
    const xEdges = section.x_edges, yEdges = section.y_edges;
    const table = node('table', 'grid-table scatter');
    const head = node('tr');
    head.append(node('th', '', `${section.x_label} ↓ · ${section.y_label} →`));
    for (let j = 0; j < yEdges.length - 1; j++) head.append(node('th', '', `${yEdges[j]}–${yEdges[j + 1]}`));
    table.append(head);
    for (let i = xEdges.length - 2; i >= 0; i--) {
      const tr = node('tr');
      tr.append(node('th', '', `${xEdges[i]}–${xEdges[i + 1]}`));
      for (let j = 0; j < yEdges.length - 1; j++) {
        const value = matrix[i][j];
        const td = node('td', '', value > 0 ? (value >= 10 ? value.toFixed(0) : value.toFixed(1)) : '');
        if (value > 0) td.style.background = `rgba(21,63,69,${(0.08 + 0.7 * value / peak).toFixed(2)})`;
        if (value > 0.6 * peak) td.style.color = '#fff';
        tr.append(td);
      }
      table.append(tr);
    }
    holder.replaceChildren(table);
  };
  toggle.addEventListener('change', draw);
  draw();
  wrap.append(toggle, holder);
  return wrap;
}

function renderRose(section) {
  const size = 300, c = size / 2, radius = 110;
  const peak = Math.max(...section.series.flatMap(s => s.values), 1e-9);
  const n = section.sector_labels.length, width = 360 / n;
  const point = (angle, r) => [c + r * Math.sin(angle * Math.PI / 180), c - r * Math.cos(angle * Math.PI / 180)];
  const wedge = (centre, span, r) => {
    const [x1, y1] = point(centre - span / 2, r), [x2, y2] = point(centre + span / 2, r);
    return `M${c},${c} L${x1.toFixed(1)},${y1.toFixed(1)} A${r.toFixed(1)},${r.toFixed(1)} 0 0 1 ${x2.toFixed(1)},${y2.toFixed(1)} Z`;
  };
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${size} ${size}`);
  svg.setAttribute('class', 'rose');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', section.title);
  [0.5, 1].forEach(f => {
    const ring = document.createElementNS(ns, 'circle');
    ring.setAttribute('cx', c); ring.setAttribute('cy', c); ring.setAttribute('r', radius * f);
    ring.setAttribute('class', 'ring'); svg.append(ring);
  });
  const styles = [['bar-a', 1], ['bar-b', 0.6]];
  section.series.forEach((series, index) => {
    series.values.forEach((value, k) => {
      if (value <= 0) return;
      const path = document.createElementNS(ns, 'path');
      path.setAttribute('d', wedge(section.sector_labels[k], width * styles[index][1], radius * value / peak));
      path.setAttribute('class', styles[index][0]);
      const title = document.createElementNS(ns, 'title');
      title.textContent = `${section.sector_labels[k]}° · ${series.label}: ${value}${section.unit}`;
      path.append(title);
      svg.append(path);
    });
  });
  [['N', 0], ['E', 90], ['S', 180], ['W', 270]].forEach(([text, angle]) => {
    const [x, y] = point(angle, radius + 16);
    const label = document.createElementNS(ns, 'text');
    label.setAttribute('x', x); label.setAttribute('y', y + 4); label.setAttribute('text-anchor', 'middle');
    label.textContent = text; svg.append(label);
  });
  const wrap = node('div', 'rose-wrap');
  const legend = node('div', 'rose-legend');
  section.series.forEach((series, index) => {
    const item = node('span', '', series.label);
    item.prepend(Object.assign(node('i', styles[index][0].replace('bar', 'swatch'))));
    legend.append(item);
  });
  legend.append(node('span', 'muted', `Outer ring = ${peak.toFixed(1)}${section.unit}`));
  wrap.append(svg, legend);
  return wrap;
}

function renderLine(section) {
  const holder = node('div');
  const { line, axis } = chartTheme();
  const series = [{ label: section.x_label },
    { label: section.y[0].label, stroke: line, width: 2, scale: 'pct' }];
  const data = [section.x, section.y[0].values];
  if (section.y[1]) {
    series.push({ label: section.y[1].label, stroke: 'rgba(120,150,60,.9)', width: 1.5, scale: 'flux' });
    data.push(section.y[1].values);
  }
  requestAnimationFrame(() => charts.push(new uPlot({
    width: Math.max(280, holder.clientWidth || 600), height: 240, series,
    scales: { x: { time: false }, pct: { range: [0, 100] }, flux: {} },
    axes: [{ ...axis, label: section.x_label },
           { ...axis, scale: 'pct', size: 50 },
           { ...axis, scale: 'flux', side: 1, size: 56, grid: { show: false } }],
    legend: { live: true }
  }, data, holder)));
  return holder;
}

const SECTION_RENDERERS = { kv: renderKv, table: renderTable, scatter: renderScatter, rose: renderRose, line: renderLine };

function renderSections(sections) {
  const container = $('#sections');
  container.replaceChildren();
  sections.forEach(section => {
    const block = node('article', `section-block kind-${section.kind}`);
    block.append(node('h3', '', section.title));
    block.append(SECTION_RENDERERS[section.kind](section));
    if (section.note) block.append(node('p', 'section-note', section.note));
    container.append(block);
  });
}

async function loadAnalysis(jobId) {
  analysisEl.hidden = false;
  $('#analysis-meta').textContent = 'Reading the downloaded NetCDF files…';
  $('#metrics').replaceChildren();
  destroyCharts();
  if (!nodeData || nodeData.jobId !== jobId) loadNodes(jobId);
  const params = [];
  if (selectedNode) params.push(nodeQuery());
  const heading = $('#sector-heading').value.trim();
  const sectorParams = heading ? `heading=${encodeURIComponent(heading)}&halfwidth=${encodeURIComponent($('#sector-half').value)}` : '';
  $('#csv-link').href = `/api/jobs/${jobId}/timeseries.csv${selectedNode ? '?' + nodeQuery() : ''}`;
  $('#prov-link').href = `/api/jobs/${jobId}/provenance`;
  if (sectorParams) params.push(sectorParams);
  const query = params.length ? `?${params.join('&')}` : '';
  let response, data;
  try {
    response = await fetch(`/api/jobs/${jobId}/analysis${query}`);
    data = await response.json();
  } catch (error) {
    $('#analysis-meta').textContent = 'Analysis unavailable: could not reach the server';
    return;
  }
  if (activeJob !== jobId) return;
  if (!response.ok) {
    $('#analysis-meta').textContent = `Analysis unavailable: ${data.error || 'unknown error'}`;
    return;
  }
  $('#sector-form').hidden = data.route !== 'wave-spectra';
  highlightNode = { lat: data.grid_coordinate.latitude, lon: data.grid_coordinate.longitude };
  renderNodesOnMap();
  renderNodePanel();
  $('#node-reset').hidden = !selectedNode;

  const requested = data.requested_coordinate, grid = data.grid_coordinate;
  if (gridMarker) gridMarker.remove();
  gridMarker = new maplibregl.Marker({ color:'#153f45' })
    .setLngLat([grid.longitude, grid.latitude])
    .setPopup(new maplibregl.Popup({ offset:18 }).setText(
      `ERA5 grid point ${grid.latitude.toFixed(3)}, ${grid.longitude.toFixed(3)}`
    )).addTo(map);
  let meta = `Requested ${requested.latitude.toFixed(5)}, ${requested.longitude.toFixed(5)} · ` +
    `nearest ERA5 grid ${grid.latitude.toFixed(3)}, ${grid.longitude.toFixed(3)} (${data.grid_distance_km} km away` +
    `${data.depth_m ? `, model depth ${Math.round(data.depth_m)} m` : ''}) · ` +
    `${data.points.toLocaleString()} records, ${(data.coverage * 100).toFixed(1)}% valid`;
  if (data.moved_to_ocean) meta += ' · the nearest cell is land, so the closest ocean cell was used';
  if (data.stride > 1) meta += ` · charts show blocks of ${data.stride} records (mean, with block maximum)`;
  $('#analysis-meta').textContent = meta;

  const warnings = $('#analysis-warnings');
  warnings.replaceChildren(...(data.warnings || []).map(text => node('p', '', text)));
  warnings.hidden = !(data.warnings || []).length;

  const s = data.series;
  const cards = data.order.filter(name => !s[name].circular && !s[name].advanced).map(name => {
    const item = s[name];
    const detail = item.p95 === null ? '' : `P95 ${fmt(item.p95, item.unit)} · Max ${fmt(item.maximum, item.unit)}`;
    return metric(`${item.label} · mean`, fmt(item.mean, item.unit), detail);
  });
  $('#metrics').replaceChildren(...cards.slice(0, 10));
  renderSections(data.sections || []);
  $('#analysis-notes').replaceChildren(...(data.notes || []).map(text => node('li', '', text)));

  const stamps = data.times.map(time => Date.parse(`${time}Z`) / 1000);
  data.order.filter(name => !s[name].advanced).forEach(name => addChart(s[name], stamps, $('#charts')));
  const advanced = data.order.filter(name => s[name].advanced);
  $('#advanced').hidden = !advanced.length;
  $('#advanced').addEventListener('toggle', function once() {
    if (!$('#advanced').open) return;
    $('#advanced').removeEventListener('toggle', once);
    advanced.forEach(name => addChart(s[name], stamps, $('#charts-advanced')));
  });
  analysisEl.scrollIntoView({ behavior:'smooth', block:'start' });
}

$('#sector-form').addEventListener('submit', event => {
  event.preventDefault();
  if (activeJob) loadAnalysis(activeJob);
});

// --- cross-check ------------------------------------------------------------------

function fillCrosscheckSelects(jobs) {
  const finished = jobs.filter(job => job.status === 'complete' && !job.dry_run);
  const fill = (select, product) => {
    const previous = select.value;
    select.replaceChildren(...finished.filter(job => (job.product || 'single-levels') === product).map(job => {
      const option = node('option', '', `${job.start} → ${job.end} · ${job.latitude.toFixed(3)}, ${job.longitude.toFixed(3)} · ${job.id}`);
      option.value = job.id;
      return option;
    }));
    if (previous) select.value = previous;
  };
  fill($('#cc-a'), 'single-levels');
  fill($('#cc-b'), 'wave-spectra');
}

function scatterPlot(pair, label, unit) {
  const holder = node('div');
  const order = pair.a.map((_, i) => i).sort((i, j) => pair.a[i] - pair.a[j]);
  const x = order.map(i => pair.a[i]), y = order.map(i => pair.b[i]);
  const { line, axis } = chartTheme();
  requestAnimationFrame(() => charts.push(new uPlot({
    width: Math.max(280, holder.clientWidth || 360), height: 280,
    series: [{ label: `Option A (${unit})` },
             { label: `Option B (${unit})`, paths: () => null, points: { show: true, size: 4, fill: line, stroke: line } },
             { label: '1:1', stroke: 'rgba(120,150,60,.9)', width: 1, points: { show: false } }],
    scales: { x: { time: false } }, axes: [{ ...axis, label: `A: ${label} (${unit})` }, { ...axis, size: 56, label: `B (${unit})` }],
    legend: { live: false }
  }, [x, y, x], holder)));
  return holder;
}

function renderCrosscheck(data) {
  const out = $('#cc-result');
  out.replaceChildren();
  out.append(node('p', 'hint', `Grid cell ${data.node.latitude.toFixed(2)}, ${data.node.longitude.toFixed(2)}. ${data.overlap_records.toLocaleString()} matching records, ${data.start} to ${data.end} UTC. ` +
    'Differences are B − A. Tolerances are configurable defaults, not standards.'));
  if (data.warnings.length) {
    const box = node('div', 'warnings');
    data.warnings.forEach(text => box.append(node('p', '', text)));
    out.append(box);
  }
  const table = node('table', 'grid-table');
  const head = node('tr');
  ['Quantity', 'n', 'Bias', 'RMSE', 'Bias %', 'Corr.', 'Within tol.', 'Median |Δ|', 'P95 |Δ|', 'Expectation'].forEach(h => head.append(node('th', '', h)));
  table.append(head);
  data.rows.forEach(r => {
    const tr = node('tr');
    if (!r.available) {
      tr.append(node('td', '', `${r.label} (${r.unit})`), node('td', 'muted', `not compared: missing ${r.missing.join(', ')}`));
      tr.firstChild.nextSibling.colSpan = 8;
      tr.append(node('td', '', r.expectation));
    } else {
      const tol = r.tolerance_unit === 'relative' ? `${(r.tolerance * 100).toFixed(0)}%` : `${r.tolerance}°`;
      [`${r.label} (${r.unit})`, r.n.toLocaleString(), r.bias, r.rmse, r.bias_pct === null ? '—' : `${r.bias_pct}%`,
       r.correlation === null ? '—' : r.correlation, `${r.within_tolerance_pct}% (±${tol})`, r.abs_diff_p50, r.abs_diff_p95,
       r.expectation].forEach(v => tr.append(node('td', '', String(v))));
    }
    table.append(tr);
  });
  const scroll = node('div', 'scroll'); scroll.append(table); out.append(scroll);

  const ordering = node('p', 'hint', 'Ordering Tm02 ≤ Tm01 ≤ Te: ' + Object.entries(data.ordering)
    .map(([k, v]) => v ? `${k}: ${v.violations} violations in ${v.checked.toLocaleString()}` : `${k}: not checked (variables not downloaded)`).join(' · '));
  out.append(ordering);

  data.rows.filter(r => r.available).forEach(r => {
    const details = node('details', 'cc-detail');
    details.append(node('summary', '', `${r.label}: worst ${r.worst.length} records${r.scatter ? ' and scatter plot' : ''}`));
    const worst = node('table', 'grid-table');
    const wh = node('tr'); ['Time (UTC)', 'A', 'B', 'B − A'].forEach(h => wh.append(node('th', '', h))); worst.append(wh);
    r.worst.forEach(w => { const tr = node('tr'); [w.time, w.a, w.b, w.diff].forEach(v => tr.append(node('td', '', String(v)))); worst.append(tr); });
    details.append(worst);
    if (r.scatter) {
      let drawn = false;
      details.addEventListener('toggle', () => {
        if (details.open && !drawn) { drawn = true; details.append(scatterPlot(r.scatter, r.label, r.unit)); }
      });
    }
    out.append(details);
  });
}

$('#cc-run').addEventListener('click', async () => {
  const error = $('#cc-error');
  error.hidden = true;
  const a = $('#cc-a').value, b = $('#cc-b').value;
  if (!a || !b) { error.textContent = 'Finish one single-levels job (Option A) and one wave-spectra job (Option B) first.'; error.hidden = false; return; }
  $('#cc-result').replaceChildren(node('p', 'hint', 'Comparing…'));
  try {
    const atNode = $('#cc-node').checked && selectedNode ? `&${nodeQuery()}` : '';
    const response = await fetch(`/api/crosscheck?a=${a}&b=${b}${atNode}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Cross-check failed');
    renderCrosscheck(data);
  } catch (failure) {
    $('#cc-result').replaceChildren();
    error.textContent = failure.message; error.hidden = false;
  }
});

// --- submit -----------------------------------------------------------------------

form.addEventListener('submit', async event => {
  event.preventDefault();
  showError('');
  submitButton.disabled = true;
  const product = productSelect.value;
  const payload = {
    product, time_step: $('#time-step').value,
    groups: checkedValues('groups'), params: checkedValues('params'),
    latitude: latInput.value, longitude: lonInput.value,
    start: $('#start').value, end: $('#end').value,
    buffer: $('#buffer').value, dry_run: $('#dry-run').checked
  };
  const missing = product === 'single-levels' && !payload.groups.length ? 'Choose at least one variable group'
    : product === 'mars-surface' && !payload.params.length ? 'Choose at least one parameter' : '';
  if (missing) { showError(missing); submitButton.disabled = false; return; }
  try {
    const response = await fetch('/api/jobs', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to create request');
    openJob(result.id);
  } catch (error) {
    showError(error.message);
    submitButton.disabled = false;
  }
});

applyProduct();
refreshRecent();
const hashJob = /^#job=([0-9a-f]{12})$/.exec(location.hash);
if (hashJob) openJob(hashJob[1]);
