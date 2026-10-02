/* A report is attached only after the rep confirms both properties. */
let pendingHailReportId = new URLSearchParams(location.search).get('hail_report') || '';
let pendingHailReport = null;
let hailReturnEstimateId = new URLSearchParams(location.search).get('hail_estimate') || '';

async function findEstimateHailReport() {
  const owner = S;
  if (!(await saveCurrentWork()) || S !== owner) return;
  const address = (owner.customer || {}).address || {};
  const query = [owner.project_address || address.street, address.city, address.state || 'CO', address.zip].filter(Boolean).join(', ');
  location.href = '/canvass/?address=' + encodeURIComponent(query) + '&estimate_id=' + encodeURIComponent(owner.estimate_id);
}

async function reviewHailHandoff() {
  if (hailReturnEstimateId && S.estimate_id !== hailReturnEstimateId) {
    await doLoadEstimate(hailReturnEstimateId);
    if (S.estimate_id !== hailReturnEstimateId) return;
  }
  hailReturnEstimateId = '';
  showClientTab('documents');
  await renderHailReportPanel();
  document.getElementById('hail-handoff-banner')?.remove();
  document.getElementById('hail-report-panel')?.scrollIntoView({block:'start'});
}

function renderHailReportPanel() {
  const box = document.getElementById('hail-report-panel'); if (!box) return;
  if (pendingHailReportId) box.parentElement.prepend(box);
  const address = (S.customer || {}).address || {};
  const query = [address.street, address.city, address.state || 'CO', address.zip].filter(Boolean).join(', ');
  const reports = S.hail_reports || [];
  box.innerHTML = `<div class="panel hail-estimate-panel"><div class="panel-header"><h3>Colorado property hail history</h3></div>
    <p class="pm-hint">Attach a dated radar report to this estimate. It stays with the customer documents and can be included in the proposal.</p>
    <button class="btn" onclick="findEstimateHailReport()">Find / update property hail report</button>
    ${reports.map(r=>`<p>${esc(r.label)}<br><small>${r.since} to ${r.until} · ${r.storms.length} radar windows · prepared ${r.created_at.slice(0,10)}</small></p>`).join('')}
    ${pendingHailReportId ? '<div id="hail-import-preview"><p>Loading the selected report...</p></div>' : ''}
  </div>`;
  if (pendingHailReportId) return loadPendingHailReport();
}

async function loadPendingHailReport() {
  const box = document.getElementById('hail-import-preview'); if (!box) return;
  try {
    const response = await fetch('/canvass/api/hail/reports/' + encodeURIComponent(pendingHailReportId));
    const report = await response.json(); if (!response.ok) throw new Error(report.error || 'Report unavailable');
    pendingHailReport = report;
    const address = (S.customer || {}).address || {};
    box.innerHTML = `<p><strong>Report property:</strong> ${esc(report.label)}</p>
      <p><strong>Open estimate:</strong> ${esc((S.customer||{}).name || 'New estimate')} — ${esc([address.street,address.city,address.state,address.zip].filter(Boolean).join(', ') || 'Address not set')}</p>
      ${S.project_address ? `<p><strong>Project address:</strong> ${esc(S.project_address)}</p>` : ''}
      <p class="pm-hint">Confirm these refer to the same property. The report includes ${report.storms.length} radar windows; ${report.coverage.days_missing} days are missing or unchecked.</p>
      <label class="hail-property-confirm"><input type="checkbox" id="hail-import-confirm"> <span>This is the property for this estimate</span></label>
      <div class="hail-import-actions">
      <button class="btn" onclick="attachPendingHailReport()">Attach to this estimate</button>
      <button class="btn" onclick="newEstimateFromHailReport()">Start a new estimate for this property</button></div>`;
  } catch(e) { box.textContent = e.message; }
}

async function attachPendingHailReport() {
  if (!document.getElementById('hail-import-confirm')?.checked) { alert('Confirm that the report and estimate are for the same property.'); return; }
  const owner = S;
  if (!(await saveEstimate()) || S !== owner) return;
  try {
    const response = await fetch('/api/estimates/' + encodeURIComponent(owner.estimate_id) + '/hail-report',
      {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({report_id:pendingHailReportId, confirmed_property:true})});
    const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Could not attach report');
    if (S !== owner) return;
    S.hail_reports = result.hail_reports;
    S.attachments = (S.attachments || []).filter(a=>a.id !== result.attachment.id).concat([result.attachment]);
    pendingHailReportId = ''; pendingHailReport = null;
    const url = new URL(location.href); url.searchParams.delete('hail_report'); url.searchParams.delete('hail_estimate'); history.replaceState({}, '', url.pathname + url.search + url.hash);
    document.getElementById('hail-handoff-banner')?.remove();
    renderDocumentsPage();
  } catch(e) { alert(e.message); }
}

async function newEstimateFromHailReport() {
  const report = pendingHailReport; if (!report) return;
  if ((await newEstimateAction()) === false) return;
  S.customer.address = {...report.address}; setDirty();
  showClientTab('documents');
  await renderHailReportPanel();
  document.getElementById('hail-report-panel')?.scrollIntoView({block:'start'});
}

if (pendingHailReportId) {
  const banner = document.createElement('div'); banner.id = 'hail-handoff-banner';
  banner.className = 'hail-handoff-banner';
  banner.setAttribute('role', 'region'); banner.setAttribute('aria-label', 'Hail report ready');
  banner.appendChild(document.createTextNode('Colorado hail report ready. '));
  const button = document.createElement('button'); button.textContent = 'Review and attach to estimate';
  // Startup chooses the initial page asynchronously. Do not let a quick tap
  // load a report/estimate that startup would immediately replace with Home.
  button.disabled = !window._estimatorReady;
  document.addEventListener('estimator:ready', () => { button.disabled = false; }, {once:true});
  button.onclick = reviewHailHandoff;
  button.className = 'btn';
  const dismiss = document.createElement('button'); dismiss.className = 'btn hail-handoff-dismiss';
  dismiss.textContent = 'Later'; dismiss.setAttribute('aria-label', 'Dismiss reminder; report stays available in Documents');
  dismiss.onclick = () => banner.remove();
  banner.append(button, dismiss); document.body.appendChild(banner);
}
