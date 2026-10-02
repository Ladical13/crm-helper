/* Confirmed Colorado property search and immutable reports. */
let hailCandidate = null, hailConfirmMap = null, hailConfirmMarker = null;
let propertySearchGeneration = 0;

function scrollHailResults() {
  const body = $('hail-address-modal').querySelector('.modal-body');
  const results = $('hail-address-results');
  body.scrollTop += results.getBoundingClientRect().top - body.getBoundingClientRect().top - 12;
}


async function searchHailProperty() {
  const generation = ++propertySearchGeneration;
  const q = $v('hail-address-input');
  $('hail-address-input').blur();
  $('hail-property-mapbar')?.remove();
  hailCandidate = null;
  if (hailConfirmMap) { hailConfirmMap.remove(); hailConfirmMap = null; }
  $('hail-address-results').innerHTML = '';
  $('hail-address-status').textContent = 'Finding Colorado addresses...';
  try {
    const data = await api('/api/hail/properties?q=' + encodeURIComponent(q));
    if (generation !== propertySearchGeneration) return;
    $('hail-address-status').textContent = data.candidates.length ? 'Choose the address, then confirm its map pin.' : 'No Colorado match. Include the street, city and ZIP code.';
    const list = document.createElement('div'); list.className = 'hail-property-matches';
    for (const candidate of data.candidates) {
      const button = document.createElement('button'); button.className = 'btn-secondary';
      button.textContent = candidate.label;
      button.onclick = () => selectHailProperty(candidate);
      list.appendChild(button);
    }
    $('hail-address-results').appendChild(list);
    if (data.candidates.length) scrollHailResults();
  } catch(e) { if (generation === propertySearchGeneration) $('hail-address-status').textContent = e.message; }
}

function selectHailProperty(candidate) {
  hailCandidate = candidate;
  if (hailConfirmMap) { hailConfirmMap.remove(); hailConfirmMap = null; }
  $('hail-address-results').innerHTML = `<div class="hail-coverage">${escHtml(candidate.label)}<br>Confirm the building below. Tap the roof or drag the pin to correct it.</div>
    <div id="hail-confirm-map" aria-label="Confirm property location on the map"></div>
    <button class="btn-primary" id="hail-confirm-property">Confirm property &amp; create report</button>`;
  hailConfirmMap = L.map('hail-confirm-map').setView([candidate.lat, candidate.lng], 18);
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    {maxZoom:20, maxNativeZoom:19, attribution:'Tiles &copy; Esri'}).addTo(hailConfirmMap);
  hailConfirmMarker = L.marker([candidate.lat, candidate.lng], {draggable:true}).addTo(hailConfirmMap);
  hailConfirmMap.on('click', e => hailConfirmMarker.setLatLng(e.latlng));
  $('hail-confirm-property').onclick = createHailPropertyReport;
  scrollHailResults();
  requestAnimationFrame(() => hailConfirmMap?.invalidateSize());
}

async function createHailPropertyReport() {
  if (!hailCandidate || !hailConfirmMarker) return;
  const button = $('hail-confirm-property'); button.disabled = true;
  const selected = hailCandidate, coords = hailConfirmMarker.getLatLng();
  const generation = propertySearchGeneration;
  $('hail-address-status').textContent = 'Preparing the report from the verified radar archive...';
  try {
    const report = await api('/api/hail/reports', 'POST', {token:selected.token, confirmed:true,
      lat:coords.lat, lng:coords.lng, days:Number($v('hail-address-days'))});
    if (generation !== propertySearchGeneration) return;
    if (hailConfirmMap) { hailConfirmMap.remove(); hailConfirmMap = null; }
    renderPropertyHailReport(report);
  } catch(e) { if (generation === propertySearchGeneration) { $('hail-address-status').textContent = e.message; button.disabled = false; } }
}

function showPropertyStorm(report, date) {
  hide('hail-address-modal');
  if (hailResultLayer) map.removeLayer(hailResultLayer);
  hailResultLayer = L.layerGroup().addTo(map);
  L.marker([report.lat, report.lng]).bindPopup(escHtml(report.label)).addTo(hailResultLayer);
  map.setView([report.lat, report.lng], 14);
  $('hail-date').value = date; $('hail-date-end').value = date;
  $('hail-min-size').value = 1;
  activeHailQuery = {start:date, end:date, min:1};
  refreshHailOverlay();
  $('hail-property-mapbar')?.remove();
  const bar = document.createElement('div'); bar.id = 'hail-property-mapbar';
  const label = document.createElement('span'); label.textContent = 'Radar window: ' + prettyDate(date);
  const back = document.createElement('button'); back.className = 'btn-secondary'; back.textContent = 'Back to report';
  back.onclick = () => { show('hail-address-modal'); scrollHailResults(); };
  bar.append(label, back); $('app').appendChild(bar);
}

function renderPropertyHailReport(report) {
  renderMeshHistory({...report, source:'mrms_mesh', resolved:report.label, query:report.label,
    storm_count:report.storms.length, max_note:report.storms.find(s=>s.size===report.max_size)?.note || ''});
  const container = $('hail-address-results');
  const toolbar = document.createElement('div'); toolbar.className = 'hail-property-actions';
  const pdf = document.createElement('a'); pdf.className = 'btn-primary'; pdf.target = '_blank'; pdf.rel = 'noopener';
  pdf.textContent = 'Open / print PDF'; pdf.href = BASE + '/api/hail/reports/' + encodeURIComponent(report.id) + '/pdf';
  const estimate = document.createElement('a'); estimate.className = 'btn-secondary';
  estimate.textContent = 'Use in estimate'; estimate.href = '/estimate/?hail_report=' + encodeURIComponent(report.id);
  const estimateId = new URLSearchParams(location.search).get('estimate_id');
  if (estimateId) estimate.href += '&hail_estimate=' + encodeURIComponent(estimateId);
  toolbar.append(pdf, estimate); container.prepend(toolbar);
  const rows = container.querySelectorAll('.hail-report-row');
  rows.forEach((row, i) => {
    const event = report.storms[i]; if (!event) return;
    const button = document.createElement('button'); button.className = 'btn-secondary'; button.textContent = 'View storm';
    button.onclick = () => showPropertyStorm(report, event.date);
    row.classList.add('hail-property-storm');
    const window = document.createElement('small'); window.className = 'hail-storm-window';
    window.textContent = event.local_window;
    button.setAttribute('aria-label', 'View storm window ' + prettyDate(event.date));
    row.append(button, window);
  });
  const oldMap = $('hail-show-on-map-btn');
  if (oldMap) {
    const button = oldMap.cloneNode(true); oldMap.replaceWith(button);
    button.onclick = () => showPropertyStorm(report, report.map.date);
  }
  const missing = report.missing_archive_dates;
  if (missing.length) {
    const detail = document.createElement('details'); const summary = document.createElement('summary');
    summary.textContent = `${missing.length} dates have no decoded archive file`;
    const text = document.createElement('p'); text.textContent = missing.join(', ');
    detail.append(summary, text); container.appendChild(detail);
  }
  scrollHailResults();
}

$('hail-address-input').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); searchHailProperty(); } });
$('hail-address-radius').closest('.field-group')?.setAttribute('hidden', '');
const hailIncoming = new URLSearchParams(location.search);
if (hailIncoming.get('address')) {
  $('hail-address-input').value = hailIncoming.get('address');
  show('hail-address-modal');
}

$('hail-address-btn').addEventListener('click', () => $('hail-property-mapbar')?.remove());
