const form = document.querySelector('#fetch-form');
const latInput = document.querySelector('#latitude');
const lonInput = document.querySelector('#longitude');
const statusCard = document.querySelector('#status-card');
const statusEl = document.querySelector('#status');
const logEl = document.querySelector('#log');
const filesEl = document.querySelector('#files');
const analysisEl = document.querySelector('#analysis');
let marker;
let gridMarker;

function studyBounds() {
  const lat = Number(latInput.value), lng = Number(lonInput.value);
  const buffer = Number(document.querySelector('#buffer').value) || 0;
  return {
    north: Math.min(90, lat + buffer), south: Math.max(-90, lat - buffer),
    west: lng - buffer, east: lng + buffer
  };
}

function updateStudyArea() {
  const lat = Number(latInput.value), lng = Number(lonInput.value);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
  const bounds = studyBounds();
  document.querySelector('#selected-coordinate').textContent = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
  document.querySelector('#selected-area').textContent =
    `ERA5 box · N ${bounds.north.toFixed(3)} · W ${bounds.west.toFixed(3)} · S ${bounds.south.toFixed(3)} · E ${bounds.east.toFixed(3)}`;
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
  document.querySelector('#map-note').textContent = `${Number(lat).toFixed(4)}, ${Number(lng).toFixed(4)}`;
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
map.on('click', event => setCoordinates(event.lngLat.lng, event.lngLat.lat));
marker.on('dragend', () => { const p = marker.getLngLat(); setCoordinates(p.lng, p.lat); });
map.on('load', () => {
  map.addSource('study-area', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id:'study-area-fill', type:'fill', source:'study-area', paint:{ 'fill-color':'#c9ff4a', 'fill-opacity':0.18 } });
  map.addLayer({ id:'study-area-line', type:'line', source:'study-area', paint:{ 'line-color':'#10241e', 'line-width':2, 'line-dasharray':[3,2] } });
  updateStudyArea();
});

[latInput, lonInput].forEach(input => input.addEventListener('change', () => {
  const lat = Number(latInput.value), lng = Number(lonInput.value);
  if (Number.isFinite(lat) && Number.isFinite(lng)) setCoordinates(lng, lat);
}));
document.querySelector('#buffer').addEventListener('input', updateStudyArea);

const today = new Date();
const monthAgo = new Date(today); monthAgo.setMonth(today.getMonth() - 1);
document.querySelector('#end').value = today.toISOString().slice(0, 10);
document.querySelector('#start').value = monthAgo.toISOString().slice(0, 10);

async function poll(jobId) {
  const response = await fetch(`/api/jobs/${jobId}`);
  const job = await response.json();
  statusEl.textContent = job.status;
  logEl.textContent = job.log.length ? job.log.join('\n') : 'Waiting for the fetcher…';
  logEl.scrollTop = logEl.scrollHeight;
  filesEl.innerHTML = job.files.map(file =>
    `<a href="/api/jobs/${jobId}/files/${encodeURIComponent(file.name)}">${file.name} · ${(file.size / 1048576).toFixed(1)} MB</a>`
  ).join('');
  if (job.status === 'queued' || job.status === 'running') setTimeout(() => poll(jobId), 1500);
  else {
    document.querySelector('#submit').disabled = false;
    if (job.status === 'complete' && !job.dry_run && job.files.length) loadAnalysis(jobId);
  }
}

function metric(label, value, detail) {
  return `<div class="metric"><span>${label}</span><strong>${value}</strong><small>${detail}</small></div>`;
}

function chartMarkup(item, times) {
  const values = item.values;
  const finite = values.filter(value => value !== null && Number.isFinite(value));
  if (!finite.length) return '';
  const min = Math.min(...finite), max = Math.max(...finite);
  const range = max - min || 1;
  const width = 720, height = 220, top = 12, bottom = 28;
  const plotHeight = height - top - bottom;
  const coordinates = values.map((value, index) => value === null ? null : [
    (index / Math.max(1, values.length - 1)) * width,
    top + (1 - (value - min) / range) * plotHeight
  ]);
  let path = '';
  coordinates.forEach(point => {
    if (!point) return;
    path += `${path ? 'L' : 'M'}${point[0].toFixed(1)},${point[1].toFixed(1)} `;
  });
  const first = times[0].slice(0, 10), last = times[times.length - 1].slice(0, 10);
  return `<article class="chart">
    <h3>${item.label}</h3><p>Mean ${item.mean} ${item.unit} · P95 ${item.p95} ${item.unit} · Max ${item.maximum} ${item.unit}</p>
    <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${item.label} time series">
      <line class="grid" x1="0" y1="${top}" x2="${width}" y2="${top}"/>
      <line class="grid" x1="0" y1="${top + plotHeight}" x2="${width}" y2="${top + plotHeight}"/>
      <path class="line" d="${path}"/>
      <text x="2" y="10">${max.toFixed(2)} ${item.unit}</text>
      <text x="2" y="${height - 4}">${first}</text>
      <text x="${width - 72}" y="${height - 4}">${last}</text>
    </svg>
  </article>`;
}

async function loadAnalysis(jobId) {
  analysisEl.hidden = false;
  document.querySelector('#analysis-meta').textContent = 'Reading the downloaded NetCDF wave stream…';
  document.querySelector('#metrics').innerHTML = '';
  document.querySelector('#charts').innerHTML = '';
  const response = await fetch(`/api/jobs/${jobId}/analysis`);
  const data = await response.json();
  if (!response.ok) {
    document.querySelector('#analysis-meta').textContent = `Analysis unavailable: ${data.error || 'unknown error'}`;
    return;
  }
  const requested = data.requested_coordinate, grid = data.grid_coordinate;
  if (gridMarker) gridMarker.remove();
  gridMarker = new maplibregl.Marker({ color:'#153f45' })
    .setLngLat([grid.longitude, grid.latitude])
    .setPopup(new maplibregl.Popup({ offset:18 }).setHTML(
      `<strong>ERA5 grid point</strong><br>${grid.latitude.toFixed(3)}, ${grid.longitude.toFixed(3)}`
    )).addTo(map);
  document.querySelector('#analysis-meta').textContent =
    `Requested ${requested.latitude.toFixed(5)}, ${requested.longitude.toFixed(5)} · nearest ERA5 wave grid ${grid.latitude.toFixed(3)}, ${grid.longitude.toFixed(3)} · ${data.points.toLocaleString()} hourly records`;
  const swh = data.series.swh, mwp = data.series.mwp, power = data.series.power;
  document.querySelector('#metrics').innerHTML = [
    metric('Mean wave height', `${swh.mean} m`, `P95 ${swh.p95} m`),
    metric('Maximum wave height', `${swh.maximum} m`, `${data.start.slice(0,10)} — ${data.end.slice(0,10)}`),
    metric('Mean wave period', mwp ? `${mwp.mean} s` : '—', mwp ? `P95 ${mwp.p95} s` : 'Not available'),
    metric('Mean power proxy', power ? `${power.mean} kW/m` : '—', 'Deep-water approximation')
  ].join('');
  const order = ['swh', 'mwp', 'pp1d', 'power', 'mwd'];
  document.querySelector('#charts').innerHTML = order
    .filter(name => data.series[name])
    .map(name => chartMarkup(data.series[name], data.times)).join('');
  analysisEl.scrollIntoView({ behavior:'smooth', block:'start' });
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  const submit = document.querySelector('#submit'); submit.disabled = true;
  const payload = {
    latitude: latInput.value, longitude: lonInput.value,
    start: document.querySelector('#start').value, end: document.querySelector('#end').value,
    buffer: document.querySelector('#buffer').value, dry_run: document.querySelector('#dry-run').checked
  };
  const response = await fetch('/api/jobs', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
  const result = await response.json();
  if (!response.ok) { alert(result.error || 'Unable to create request'); submit.disabled = false; return; }
  statusCard.hidden = false;
  analysisEl.hidden = true;
  document.querySelector('#job-id').textContent = `JOB ${result.id}`;
  statusCard.scrollIntoView({ behavior:'smooth' });
  poll(result.id);
});
