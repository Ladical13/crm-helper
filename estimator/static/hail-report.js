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
  renderHailReportPanel();
}

function renderHailReportPanel() {
  const box = document.getElementById('hail-report-panel'); if (!box) return;
  const address = (S.customer || {}).address || {};
  const query = [address.street, address.city, address.state || 'CO', address.zip].filter(Boolean).join(', ');
  const reports = S.hail_reports || [];
  box.innerHTML = `<div class="panel"><div class="panel-header"><h3>Colorado property hail history</h3></div>
    <p class="pm-hint">Attach a dated radar report to this estimate. It stays with the customer documents and can be included in the proposal.</p>
    <button class="btn" onclick="findEstimateHailReport()">Find / update property hail report</button>
    ${reports.map(r=>`<p>${esc(r.label)}<br><small>${r.since} to ${r.until} · ${r.storms.length} radar windows · prepared ${r.created_at.slice(0,10)}</small></p>`).join('')}
    ${pendingHailReportId ? '<div id="hail-import-preview"><p>Loading the selected report...</p></div>' : ''}
  </div>`;
  if (pendingHailReportId) loadPendingHailReport();
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
      <label><input type="checkbox" id="hail-import-confirm"> This is the property for this estimate</label>
      <button class="btn" onclick="attachPendingHailReport()">Attach to this estimate</button>
      <button class="btn" onclick="newEstimateFromHailReport()">Start a new estimate for this property</button>`;
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
}

if (pendingHailReportId) {
  const banner = document.createElement('div'); banner.id = 'hail-handoff-banner';
  banner.style.cssText = 'position:fixed;bottom:16px;left:16px;right:16px;z-index:9999;padding:16px;background:#173e48;color:white;border-radius:12px;box-shadow:0 3px 15px #0004';
  banner.appendChild(document.createTextNode('Colorado hail report ready. '));
  const button = document.createElement('button'); button.textContent = 'Review and attach to estimate';
  button.onclick = reviewHailHandoff;
  banner.appendChild(button); document.body.appendChild(banner);
}
