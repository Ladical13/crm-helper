/* ── Constants ─────────────────────────────────────────────────────── */

const TRADES = ['roofing','siding','windows','gutters','other','insurance'];
const TRADE_LABELS = { roofing:'Roofing', siding:'Siding', windows:'Windows', gutters:'Gutters', other:'Other', insurance:'Insurance' };
const TIERS = ['good','better','best'];
const TIER_LABELS = { good:'Good', better:'Better', best:'Best' };
const TEAM = ['aaron','avery','bryan','casey','chris','chris.rollins','clint','cole','dalton',
              'derik','eric','gabriel','jacob','jeremy','jonathan','kyle','logan',
              'luke','richard','ryan','shiloh','ted'];
const TRADE_COLOR_FIELDS = {
  roofing: [{key:'shingle_color',label:'Shingle Color'},{key:'manufacturer',label:'Manufacturer'},{key:'product_line',label:'Product Line'}],
  siding:  [{key:'siding_color',label:'Siding Color'},{key:'trim_color',label:'Trim Color'},{key:'manufacturer',label:'Manufacturer'}],
  windows: [{key:'frame_color',label:'Frame Color'},{key:'glass_package',label:'Glass Package'}],
  gutters: [{key:'gutter_color',label:'Gutter Color'},{key:'material',label:'Material'}],
  other:   [{key:'color',label:'Color / Finish'}],
};
const DEFAULT_CONTRACT = `TERMS AND CONDITIONS — PROJECT ONE ROOFING

SCOPE OF WORK
Contractor agrees to furnish all labor, materials, equipment, and supervision necessary to complete the work described in this estimate. Any work required beyond the described scope will require a written change order approved by both parties prior to commencement.

PAYMENT TERMS
A deposit of 25% is due at contract signing. Final payment is due upon completion of work and homeowner walkthrough. Accepted: check, ACH bank transfer, or major credit card (3% processing fee applies). Balances unpaid after 30 days accrue interest at 1.5% per month.

MATERIALS
All materials remain the property of Project One Roofing until paid in full. Contractor reserves the right to substitute materials of equal or greater quality if specified materials are unavailable, with prior homeowner notification.

WARRANTY
Project One Roofing warrants all workmanship against defects for 5 years from the date of project completion. Manufacturer warranties will be registered in the homeowner's name upon receipt of final payment.

CHANGE ORDERS
Any deviation from the agreed scope must be submitted in writing and signed by both parties before work proceeds. Verbal authorizations are not binding on Contractor.

INSURANCE & LICENSING
Project One Roofing carries general liability insurance ($1,000,000 per occurrence / $2,000,000 aggregate) and maintains workers' compensation coverage for all employees and subcontractors. Certificates of insurance available upon request.

PROPERTY CONDITIONS
Homeowner grants Contractor reasonable access to the property. Contractor will take reasonable precautions to protect landscaping and improvements. Contractor is not responsible for pre-existing damage, hairline drywall cracks from normal roofing vibration, or damage to unmarked underground utilities.

CLEANUP
Contractor will remove all project-related debris and perform a magnetic sweep of driveway and lawn for nails within 1 business day of completion.

PERMITS & INSPECTIONS
Where required, Contractor will obtain all necessary building permits and schedule required inspections. Permit fees are included in the contract price unless noted otherwise.

INSURANCE CLAIMS
If this project involves a homeowner insurance claim, Homeowner authorizes Contractor to communicate directly with the insurance carrier regarding covered scope and pricing.

RIGHT TO CANCEL
Homeowner has the right to cancel this contract without penalty within 3 business days of signing, provided no materials have been delivered or work has commenced. Cancellation must be in writing.

DISPUTE RESOLUTION
Any dispute arising from this agreement shall first be subject to good-faith mediation. If unsuccessful, disputes shall be resolved by binding arbitration in the county where the project is located.`;

const DEFAULT_INSURANCE_CONTRACT = `TERMS AND CONDITIONS — PROJECT ONE ROOFING (INSURANCE CLAIM)

ASSIGNMENT OF BENEFITS / AUTHORIZATION
Homeowner authorizes Project One Roofing to communicate directly with the insurance carrier on the homeowner's behalf to ensure full and fair recovery under the applicable policy. Homeowner may assign insurance benefits directly to Contractor as permitted by applicable law.

SCOPE OF WORK
Contractor agrees to perform all work as approved and covered by the homeowner's insurance carrier. Work will be completed to restore the property to pre-loss condition using materials that meet or exceed insurance replacement standards. Any supplemental work required beyond the initially approved scope will be submitted to the insurance carrier for additional approval before proceeding.

PAYMENT TERMS
Homeowner's out-of-pocket responsibility is limited to the policy deductible, plus the cost of any non-covered upgrades elected by the Homeowner. Final payment is due upon completion of work. Accepted: check, ACH bank transfer, or major credit card (3% processing fee applies). Homeowner shall endorse and deliver all insurance proceeds to Contractor promptly upon receipt.

DEDUCTIBLE
Homeowner acknowledges that the deductible is their financial responsibility and is not covered by the insurance carrier. As required by law, Contractor cannot waive, absorb, rebate, or otherwise pay the deductible on behalf of the Homeowner.

SUPPLEMENTS
Construction costs, materials, and code-required upgrades not initially included in the carrier's estimate may result in supplemental claims. Contractor will file reasonable supplements on Homeowner's behalf. Homeowner authorizes Contractor to negotiate directly with the carrier on supplemental items.

MATERIALS
All materials remain the property of Project One Roofing until paid in full. Contractor reserves the right to substitute materials of equal or greater quality if specified materials are unavailable, with prior notification to Homeowner.

WARRANTY
Project One Roofing warrants all workmanship against defects for 5 years from the date of project completion. Manufacturer warranties will be registered in the homeowner's name upon receipt of final payment.

INSURANCE & LICENSING
Project One Roofing carries general liability insurance ($1,000,000 per occurrence / $2,000,000 aggregate) and maintains workers' compensation coverage for all employees and subcontractors. Certificates of insurance available upon request.

PROPERTY CONDITIONS
Homeowner grants Contractor reasonable access to the property. Contractor will take reasonable precautions to protect landscaping and surrounding improvements. Contractor is not responsible for pre-existing damage, hairline drywall cracks from normal roofing vibration, or damage to unmarked underground utilities.

CLEANUP
Contractor will remove all project-related debris and perform a magnetic sweep of driveway and lawn areas within 1 business day of project completion.

PERMITS & INSPECTIONS
Where required, Contractor will obtain all necessary building permits and schedule required inspections. Permit fees are included in the insurance-approved scope unless otherwise noted.

RIGHT TO CANCEL
Homeowner has the right to cancel this contract without penalty within 3 business days of signing, provided no materials have been delivered and no work has commenced. Cancellation must be submitted in writing.

DISPUTE RESOLUTION
Any dispute arising from this agreement shall first be subject to good-faith mediation. If unsuccessful, disputes shall be resolved by binding arbitration in the county where the project is located.`;

// Common architectural shingle colors across major manufacturers
const DEFAULT_SHINGLE_COLORS = [
  'Charcoal','Weathered Wood','Driftwood','Barkwood','Pewter Gray','Estate Gray',
  'Slate','Shakewood','Hickory','Williamsburg Gray','Hunter Green','Mission Brown',
  'Black Walnut','Aged Copper','Birchwood','Oyster Gray',
];

// Default statements the customer must initial — seeded by estimate type, fully editable
const DEFAULT_INITIALS_RETAIL = [
  'I authorize Project One Roofing to perform the work described in this estimate.',
  'I understand a deposit may be required at signing per the payment terms.',
  'I understand I have the right to cancel within 3 business days of signing.',
];
const DEFAULT_INITIALS_INSURANCE = [
  'I understand my insurance deductible is my responsibility and cannot be waived.',
  'I authorize Project One Roofing to communicate with my insurance carrier on my behalf.',
  'I understand supplemental items may be submitted to my carrier as the scope requires.',
];
function defaultInitials(type) {
  const src = type === 'insurance' ? DEFAULT_INITIALS_INSURANCE : DEFAULT_INITIALS_RETAIL;
  return src.map(text => ({ id:'ini_'+uid(), text }));
}

/* ── Measurements engine ─────────────────────────────────────────────
   Raw measurements live on S.measurements. Line items may carry a
   `measure` key; their quantity then derives automatically. */
const MEASURE_FIELDS = [
  { group:'Roof', fields:[
    {key:'roof_squares',  label:'Roof Area',      unit:'SQ'},
    {key:'waste_pct',     label:'Waste',          unit:'%'},
    {key:'ridge_hip_lf',  label:'Ridge + Hip',    unit:'LF'},
    {key:'valley_lf',     label:'Valley',         unit:'LF'},
    {key:'eave_lf',       label:'Eaves',          unit:'LF'},
    {key:'rake_lf',       label:'Rakes',          unit:'LF'},
    {key:'step_flash_lf', label:'Step Flashing',  unit:'LF'},
    {key:'pipe_boots',    label:'Pipe Boots',     unit:'EA'},
    {key:'skylights',     label:'Skylights',      unit:'EA'},
    {key:'turtle_vents',  label:'Turtle Vents',   unit:'EA'},
    {key:'broan_4in',     label:'4" Broan Vent',  unit:'EA'},
    {key:'broan_8in',     label:'8" Broan Vent',  unit:'EA'},
  ]},
  { group:'Gutters', fields:[
    {key:'gutter_lf',     label:'Gutter',         unit:'LF'},
    {key:'downspout_lf',  label:'Downspouts',     unit:'LF'},
  ]},
  { group:'Siding / Windows', fields:[
    {key:'siding_squares', label:'Siding Area',   unit:'SQ'},
    {key:'windows_count',  label:'Windows',       unit:'EA'},
  ]},
];
const MEASURE_DEFS = {
  squares:              { label:'Roof SQ',            calc:m => mnum(m.roof_squares) },
  squares_waste:        { label:'Roof SQ + Waste',    calc:m => mnum(m.roof_squares) * (1 + mnum(m.waste_pct, 10)/100) },
  ridge_hip:            { label:'Ridge + Hip LF',     calc:m => mnum(m.ridge_hip_lf) },
  valley:               { label:'Valley LF',          calc:m => mnum(m.valley_lf) },
  eave:                 { label:'Eave LF',            calc:m => mnum(m.eave_lf) },
  rake:                 { label:'Rake LF',            calc:m => mnum(m.rake_lf) },
  eave_rake:            { label:'Eave + Rake LF',     calc:m => mnum(m.eave_lf) + mnum(m.rake_lf) },
  eave_valley:          { label:'Eave + Valley LF',   calc:m => mnum(m.eave_lf) + mnum(m.valley_lf) },
  step:                 { label:'Step Flashing LF',   calc:m => mnum(m.step_flash_lf) },
  pipe_boots:           { label:'# Pipe Boots',       calc:m => mnum(m.pipe_boots) },
  skylights:            { label:'# Skylights',        calc:m => mnum(m.skylights) },
  turtle_vents:         { label:'# Turtle Vents',     calc:m => mnum(m.turtle_vents) },
  broan_4in:            { label:'# 4" Broan Vents',   calc:m => mnum(m.broan_4in) },
  broan_8in:            { label:'# 8" Broan Vents',   calc:m => mnum(m.broan_8in) },
  gutter:               { label:'Gutter LF',          calc:m => mnum(m.gutter_lf) },
  downspout:            { label:'Downspout LF',       calc:m => mnum(m.downspout_lf) },
  siding_squares:       { label:'Siding SQ',          calc:m => mnum(m.siding_squares) },
  siding_squares_waste: { label:'Siding SQ + Waste',  calc:m => mnum(m.siding_squares) * (1 + mnum(m.waste_pct, 10)/100) },
  windows:              { label:'# Windows',          calc:m => mnum(m.windows_count) },
};
function mnum(v, dflt) {
  const n = parseFloat(v);
  return isNaN(n) ? (dflt || 0) : n;
}

/* ── Global app settings (loaded from /api/settings at boot) ────────── */
let appSettings = {};
function _globalShingleColors() {
  const list = (appSettings.shingle_colors || []).filter(c => String(c).trim());
  return (list.length ? list : DEFAULT_SHINGLE_COLORS).slice();
}
function _globalWastePct() {
  const w = parseFloat(appSettings.default_waste_pct);
  return isNaN(w) ? 10 : w;
}
function evalFormula(formula, m) {
  // Replace known measurement variable names with their numeric values, then eval.
  let expr = (formula || '').replace(/[a-z][a-z0-9_]*/gi, name => {
    const v = parseFloat((m || {})[name]);
    return isNaN(v) ? '0' : String(v);
  });
  try { return Function('"use strict"; return (' + expr + ')')(); }
  catch { return 0; }
}
function measuredQty(item) {
  const m = S.measurements || {};
  let raw;
  if (item.formula) {
    raw = evalFormula(item.formula, m);
  } else {
    const def = MEASURE_DEFS[item.measure];
    if (!def) return null;
    raw = def.calc(m);
  }
  if (!raw) return 0;
  // Squares keep one decimal (rounded up); linear feet and counts round up whole.
  // The 1e-9 guards against float noise (28 × 1.1 = 30.800000000000004).
  return (item.unit === 'SQ') ? Math.ceil(raw * 10 - 1e-9) / 10 : Math.ceil(raw - 1e-9);
}
function applyMeasurements() {
  // Measurements only auto-fill roofing items (other trades are manual).
  const td = S.trades.roofing;
  (td && td.line_items || []).forEach(item => {
    const q = measuredQty(item);
    if (q !== null) item.quantity = q;
  });
  setDirty();
  renderTotals();
}
function setMeasurement(key, v) {
  if (!S.measurements) S.measurements = {};
  S.measurements[key] = parseFloat(v) || 0;
  // If roofing is enabled but has no items yet, auto-build it now so entering a
  // measurement instantly produces a priced estimate (no separate Load step).
  const rd = S.trades.roofing;
  let built = false;
  if (rd && rd.enabled && templates && (!rd.line_items || rd.line_items.length === 0)) {
    rd.line_items = buildTradeDefaults('roofing');
    built = true;
  }
  applyMeasurements();
  if (built) {
    // New rows were created — re-render so they appear (fired on blur, focus loss ok)
    if (activePage === 'scope') renderScopePage();
    rerender();
    return;
  }
  // Refresh visible qty cells without rebuilding the whole page (keeps focus)
  document.querySelectorAll('.scope-qty-input[data-measured="1"]').forEach(inp => {
    const item = findItem(inp.dataset.trade, inp.dataset.id);
    if (item) inp.value = item.quantity || '';
  });
}
function setItemFormula(trade, id, formula) {
  const item = findItem(trade, id);
  if (!item) return;
  item.formula = formula || undefined;
  item.measure = undefined;
  const q = measuredQty(item);
  if (q !== null) item.quantity = q;
  setDirty();
  renderTotals();
}
function handleMeasureSelect(trade, id, value) {
  if (value === '__formula__') {
    setItemFormula(trade, id, '');
    renderScopePage();
  } else {
    setItemMeasure(trade, id, value);
  }
}
function setItemMeasure(trade, id, key) {
  const item = findItem(trade, id);
  if (!item) return;
  item.formula = undefined;
  item.measure = key || undefined;
  if (key) {
    const q = measuredQty(item);
    if (q !== null) item.quantity = q;
  }
  setDirty();
  renderScopePage();
  renderTotals();
}

const INTRO_TEMPLATES = [
  { id:'general', label:'General', text:
`Dear {name},

Thank you for the opportunity to provide you with this estimate. At Project One Roofing, we take pride in delivering high-quality workmanship backed by premium materials and industry-leading warranties.

Our team of certified professionals has been serving Northern Colorado homeowners for years, and we are committed to making your project as smooth and stress-free as possible — from the first consultation through final cleanup.

Enclosed you will find a detailed scope of work along with Good, Better, and Best package options tailored to your specific needs and budget. We are confident in the value we deliver and welcome any questions you may have.

We look forward to earning your trust and protecting your home.

Warm regards,
Project One Roofing — Northern Colorado` },

  { id:'insurance', label:'Insurance Claim', text:
`Dear {name},

We understand that navigating a homeowner's insurance claim can feel overwhelming — and we are here to make that process as seamless as possible for you.

Project One Roofing has extensive experience working with insurance carriers to ensure homeowners receive the full coverage they are entitled to under their policy. Our team will document all storm-related damage thoroughly and work directly with your adjuster on your behalf.

This estimate reflects the full scope of work needed to restore your property to pre-loss condition using quality materials that meet or exceed insurance replacement standards. We are here every step of the way to advocate for you.

Please feel free to reach out with any questions about the claims process or the work outlined in this estimate.

Sincerely,
Project One Roofing — Northern Colorado` },

  { id:'storm', label:'Storm Damage', text:
`Dear {name},

Recent storm activity in your area has caused significant damage to many homes, and we want to help you restore yours as quickly and correctly as possible.

Our team has carefully inspected your property and documented the damage to provide you with a comprehensive, accurate estimate for restoration. Time is of the essence after a storm event — both to prevent further interior damage and to meet your insurance carrier's claim deadlines.

This estimate outlines the full scope of recommended repairs and replacements. We are committed to transparency and will walk you through every line item. Our team is ready to mobilize quickly and work efficiently to get your home protected.

We appreciate your trust in Project One Roofing.

Respectfully,
Project One Roofing — Northern Colorado` },

  { id:'referral', label:'Referral', text:
`Dear {name},

It was truly a pleasure being referred to you, and we want to honor the trust that comes with a personal recommendation.

At Project One Roofing, we believe that the best advertising is a job done right — which is why so much of our business comes from satisfied homeowners telling their friends, family, and neighbors about their experience with us.

We have put together this estimate with the same care and attention to detail that earned that referral in the first place. Our goal is to exceed your expectations from the first handshake to the final inspection.

Thank you for giving us the opportunity to serve you.

With appreciation,
Project One Roofing — Northern Colorado` },

  { id:'repair', label:'Repair Quote', text:
`Dear {name},

Thank you for reaching out to Project One Roofing for your repair needs. We understand that not every situation requires a full replacement — and we respect your investment in maintaining your existing roof system.

Our repair services are performed by the same skilled craftspeople who handle our full installations, using matching materials and the same quality standards. Every repair comes with our workmanship warranty so you can have confidence in the work.

This estimate details the specific repairs we recommend based on our inspection. If you have any questions about what was found or why certain repairs are recommended, we encourage you to ask — we believe an informed homeowner is a confident homeowner.

Thank you for trusting us with your home.

Best regards,
Project One Roofing — Northern Colorado` },
];

/* ── State ─────────────────────────────────────────────────────────── */

let S = blankEstimate();
let dirty = false;
let activePage = 'cover';
let activeTrade = 'roofing';
let templates    = null;
let priceBook    = null;   // { intros: [...], materials: {...} }
let tierDefaults = { good:[], better:[], best:[] }; // global admin-set defaults for new estimates
let pbActiveTrade = 'roofing';
let pbActiveTab = 'master';  // price book sub-view: master | good | better | best
let pbItems = {};       // working copy of price book materials while modal is open

function blankEstimate() {
  const today = new Date();
  const exp = new Date(today); exp.setDate(exp.getDate() + 30);
  return {
    estimate_id: null, version: 1,
    created_at: null, updated_at: null, status: 'draft',
    customer: { crm_contact_id:null, crm_project_id:null, crm_job_number:'',
                name:'', phone:'', email:'',
                address:{ street:'', city:'', state:'', zip:'' } },
    project_address: '',
    estimate_date: fmtDate(today), valid_until: fmtDate(exp),
    salesperson: '', notes_internal: '', notes_customer: '',
    pricing: { mode:'margin', global_rate:35,
               per_trade_overrides:{ roofing:null,siding:null,windows:null,gutters:null,other:null,insurance:null } },
    selected_tier: 'better',
    tier_descriptions: { good:'', better:'', best:'' },
    tier_features:     { good:[], better:[], best:[] },
    estimate_type: 'retail',
    print_contract: true, contract_text: DEFAULT_CONTRACT,
    contract_initials: defaultInitials('retail'),
    shingle_selection: { enabled: true, options: _globalShingleColors(), chosen: '' },
    measurements: { waste_pct: _globalWastePct() },
    intro_text: '',
    page_visibility: { intro: false, options: true },
    cover_photo_id: null,
    share_token: null, signature: null,
    attachments: [],
    trades: {
      roofing: { enabled:true,  line_items:[], colors:{}, mode:'gbb' },
      siding:  { enabled:false, line_items:[], colors:{}, mode:'gbb' },
      windows: { enabled:false, line_items:[], colors:{}, mode:'gbb' },
      gutters: { enabled:false, line_items:[], colors:{}, mode:'simple' },
      other:     { enabled:false, line_items:[], colors:{}, mode:'gbb' },
      insurance: { enabled:false, sections:[{id:'sec_'+uid(), name:'', items:[]}], scope_notes:'', claim_number:'', carrier:'', colors:{} },
    },
    photos: [],
  };
}

/* ── Utilities ─────────────────────────────────────────────────────── */

function uid() {
  return 'xxxx-4xxx-yxxx'.replace(/[xy]/g, c => {
    const r = Math.random()*16|0;
    return (c==='x'?r:(r&0x3|0x8)).toString(16);
  }) + '-' + Date.now().toString(36);
}
function fmtDate(d) { return d.toISOString().split('T')[0]; }
function fmtCur(n) {
  if (!isFinite(n)) return '$0.00';
  return '$' + Math.abs(n).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(()=>fn(...a), ms); }; }

/* ── Calculations ──────────────────────────────────────────────────── */

function tradeRate(trade) {
  const ov = S.pricing.per_trade_overrides[trade];
  return (ov !== null && ov !== undefined) ? ov : S.pricing.global_rate;
}
function lineTotal(qty, mat, labor, trade) {
  const cost = (parseFloat(mat)||0) + (parseFloat(labor)||0);
  const q = parseFloat(qty) || 0;
  const r = tradeRate(trade);
  return S.pricing.mode === 'margin'
    ? (r >= 100 ? 0 : cost * q / (1 - r/100))
    : cost * q * (1 + r/100);
}
function tradeTotal(trade, tier) {
  if (trade === 'insurance') return 0; // insurance uses insuranceTotal()
  const td = S.trades[trade];
  if (!td || !td.enabled) return 0;
  const effectiveMode = td.mode || (trade === 'gutters' ? 'simple' : 'gbb');
  if (effectiveMode === 'simple') {
    return (td.line_items || []).reduce((sum, item) =>
      sum + (parseFloat(item.quantity)||0) * (parseFloat(item.unit_price)||0), 0);
  }
  return td.line_items.reduce((sum, item) => {
    const t = (item.tiers && item.tiers[tier]) || {};
    if (t.included === false) return sum;  // excluded from this package tier
    return sum + lineTotal(item.quantity, t.material_unit_cost, t.labor_unit_cost, trade);
  }, 0);
}
function grandTotal(tier) {
  return ['roofing','siding','windows','gutters','other'].reduce((s,tr)=>s+tradeTotal(tr,tier),0);
}
function insuranceTotal() {
  const td = S.trades.insurance;
  if (!td || !td.enabled) return 0;
  const sections = td.sections || (td.line_items ? [{items: td.line_items}] : []);
  return sections.reduce((s, sec) =>
    s + (sec.items||[]).reduce((ss, item) =>
      ss + (parseFloat(item.acv)||0) + (parseFloat(item.depreciation)||0), 0), 0);
}

/* ── Dirty tracking ────────────────────────────────────────────────── */

function setDirty() {
  dirty = true;
  const el = document.getElementById('save-indicator');
  el.textContent = '● Unsaved'; el.className = 'save-indicator unsaved';
}
function setClean() {
  dirty = false;
  const el = document.getElementById('save-indicator');
  el.textContent = '✓ Saved'; el.className = 'save-indicator saved';
}

/* ── Full render ───────────────────────────────────────────────────── */

function renderAll() {
  renderSidebar();
  renderCoverPage();
  renderIntroPage();
  renderPhotosPage();
  renderScopePage();
  renderOptionsPage();
  renderPricingPage();
  renderContractPage();
  renderTotals();
  updatePageNav();
  renderPrintPagesBar();
}

function rerender() {
  renderTotals();
  renderCoverPage();
  if (activePage === 'intro')   renderIntroPage();
  if (activePage === 'photos')  renderPhotosPage();
  renderScopePage();
  renderOptionsPage();
  if (activePage === 'pricing') { renderTabBar(); renderTradeContent(); }
  updatePageNav();
  renderPrintPagesBar();
}

/* ── Page navigation ───────────────────────────────────────────────── */

function switchPage(page) {
  activePage = page;
  document.querySelectorAll('.page').forEach(el => el.style.display = 'none');
  const target = document.getElementById('page-' + page);
  if (target) target.style.display = 'flex';
  updatePageNav();
  if (page === 'pricing') { renderTabBar(); renderTradeContent(); }
  if (page === 'intro')   renderIntroPage();
  if (page === 'scope')   renderScopePage();
  if (page === 'options') renderOptionsPage();
}

function updatePageNav() {
  document.querySelectorAll('.page-btn').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.page === activePage));
}

/* ── Sidebar ───────────────────────────────────────────────────────── */

function renderSidebar() {
  const c = S.customer;
  setVal('cust-name',       c.name);
  setVal('cust-phone',      c.phone);
  setVal('cust-email',      c.email);
  setVal('cust-street',     c.address.street);
  setVal('cust-city',       c.address.city);
  setVal('cust-state',      c.address.state);
  setVal('cust-zip',        c.address.zip);
  setVal('project-address', S.project_address);
  setVal('estimate-date',   S.estimate_date);
  setVal('valid-until',     S.valid_until);
  setVal('salesperson',     S.salesperson);
  setVal('est-status',      S.status);
  setVal('global-rate-slider', S.pricing.global_rate);
  setVal('global-rate-input',  S.pricing.global_rate);
  document.getElementById('global-rate-display').textContent = S.pricing.global_rate + '%';
  renderPricingModeUI();
  renderEstimateTypeUI();
  renderTierButtons();
  renderTradeOverrides();
  renderEstNum();
  renderCrmLinkBadge();
}
function setVal(id, v) {
  const el = document.getElementById(id);
  if (el && el.value !== String(v ?? '')) el.value = v ?? '';
}
function renderEstNum() {
  const num = S.estimate_id ? 'EST-' + S.estimate_id.split('-')[0].toUpperCase() : 'New Estimate';
  const sig = S.signature;
  let badge = '';
  if (sig) {
    const dt  = new Date(sig.signed_at);
    const fmt = dt.toLocaleDateString('en-US', { month:'short', day:'numeric', year:'numeric' });
    badge = ` <span class="sig-badge" title="Signed by ${sig.name} on ${fmt}">✓ Signed</span>`;
  }
  document.getElementById('estimate-number').innerHTML = num + badge;
}
function renderPricingModeUI() {
  document.getElementById('mode-margin').classList.toggle('active', S.pricing.mode === 'margin');
  document.getElementById('mode-markup').classList.toggle('active', S.pricing.mode === 'markup');
}
function renderEstimateTypeUI() {
  const t = S.estimate_type || 'retail';
  const rBtn = document.getElementById('type-retail');
  const iBtn = document.getElementById('type-insurance');
  if (rBtn) rBtn.classList.toggle('active', t === 'retail');
  if (iBtn) iBtn.classList.toggle('active', t === 'insurance');
}
// True when the current initials still match an untouched default set
function initialsMatchDefault(type) {
  const cur = (S.contract_initials || []).map(i => i.text).join('\n');
  const def = (type === 'insurance' ? DEFAULT_INITIALS_INSURANCE : DEFAULT_INITIALS_RETAIL).join('\n');
  return cur === def;
}
function setEstimateType(type) {
  S.estimate_type = type;
  if (type === 'insurance') {
    ['roofing','siding','windows','gutters','other'].forEach(t => {
      S.trades[t].enabled = false;
    });
    S.trades.insurance.enabled = true;
    activeTrade = 'insurance';
    // Auto-load insurance contract if still on retail default or blank
    if (!S.contract_text || S.contract_text === DEFAULT_CONTRACT) {
      S.contract_text = DEFAULT_INSURANCE_CONTRACT;
      const ta = document.getElementById('contract-textarea');
      if (ta) ta.value = DEFAULT_INSURANCE_CONTRACT;
    }
    // Swap initials to insurance defaults if still on the retail defaults
    if (!S.contract_initials || !S.contract_initials.length || initialsMatchDefault('retail'))
      S.contract_initials = defaultInitials('insurance');
    switchPage('pricing');
  } else {
    // Auto-restore retail contract if still on insurance default or blank
    if (!S.contract_text || S.contract_text === DEFAULT_INSURANCE_CONTRACT) {
      S.contract_text = DEFAULT_CONTRACT;
      const ta = document.getElementById('contract-textarea');
      if (ta) ta.value = DEFAULT_CONTRACT;
    }
    if (!S.contract_initials || !S.contract_initials.length || initialsMatchDefault('insurance'))
      S.contract_initials = defaultInitials('retail');
    if (activePage === 'options') renderOptionsPage();
    else if (activePage === 'pricing') { renderTabBar(); renderTradeContent(); }
  }
  setDirty();
  renderEstimateTypeUI();
  if (activePage === 'contract') renderContractPage();
}
function renderTierButtons() {
  document.querySelectorAll('.tier-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.tier === S.selected_tier));
  TIERS.forEach(t => {
    const row = document.getElementById('tr-' + t);
    if (row) row.classList.toggle('is-selected', t === S.selected_tier);
  });
}
function renderTradeOverrides() {
  document.getElementById('trade-overrides').innerHTML =
    `<div class="trade-overrides">` +
    TRADES.map(trade => {
      const ov = S.pricing.per_trade_overrides[trade];
      return `<div class="override-row">
        <label>${TRADE_LABELS[trade]}</label>
        <input type="number" min="0" max="100" step="0.5" placeholder="Global"
          value="${esc(ov !== null && ov !== undefined ? ov : '')}"
          onchange="setTradeOverride('${trade}',this.value)">
        <span style="font-size:10px;color:var(--text-light)">%</span>
      </div>`;
    }).join('') + `</div>`;
}
function renderTotals() {
  TIERS.forEach(t => {
    const el = document.getElementById('total-' + t); if (el) el.textContent = fmtCur(grandTotal(t));
  });
  const insEl  = document.getElementById('total-insurance');
  const insRow = document.getElementById('tr-insurance');
  if (insEl)  insEl.textContent = fmtCur(insuranceTotal());
  if (insRow) insRow.style.display = S.trades.insurance?.enabled ? '' : 'none';
  renderInternalMargin();
  renderCostProfitPanel();
}

/* ── Internal cost / profit (rep-only — never shown to the customer) ────
   GBB trades track material + labor cost, so profit is computed from them.
   Simple-mode trades (e.g. gutters) store a sell price with no cost split,
   so they're reported separately rather than counted as pure profit. */
function tierProfit(tier) {
  let material = 0, labor = 0, gbbSell = 0, simpleSell = 0;
  const perTrade = [];
  ['roofing','siding','windows','gutters','other'].forEach(trade => {
    const td = S.trades[trade];
    if (!td || !td.enabled) return;
    const mode = td.mode || (trade === 'gutters' ? 'simple' : 'gbb');
    if (mode === 'simple') {
      let s = 0;
      (td.line_items||[]).forEach(item => {
        const qty = parseFloat(item.quantity)||0; if (qty <= 0) return;
        s += qty * (parseFloat(item.unit_price)||0);
      });
      if (s > 0) { simpleSell += s; perTrade.push({trade, mode, sell:s}); }
      return;
    }
    let m = 0, l = 0;
    (td.line_items||[]).forEach(item => {
      const qty = parseFloat(item.quantity)||0; if (qty <= 0) return;
      const t = (item.tiers||{})[tier] || {};
      if (t.included === false) return;
      m += (parseFloat(t.material_unit_cost)||0) * qty;
      l += (parseFloat(t.labor_unit_cost)||0) * qty;
    });
    const sell = tradeTotal(trade, tier);
    if (m === 0 && l === 0 && sell === 0) return;
    material += m; labor += l; gbbSell += sell;
    perTrade.push({trade, mode, material:m, labor:l, cost:m+l, sell, profit:sell-(m+l)});
  });
  const cost = material + labor;
  const profit = gbbSell - cost;
  return {material, labor, cost, sell:gbbSell, profit,
          margin: gbbSell > 0 ? (profit/gbbSell*100) : 0,
          simpleSell, perTrade};
}

function _pct(n){ return (Math.round(n*10)/10).toFixed(1) + '%'; }

// Compact sidebar summary for the selected package (rep-only).
function renderInternalMargin() {
  const el = document.getElementById('internal-margin');
  if (!el) return;
  const tier = S.selected_tier;
  const p = tierProfit(tier);
  if (p.sell === 0 && p.cost === 0) { el.innerHTML = ''; el.style.display = 'none'; return; }
  el.style.display = '';
  const profClass = p.profit >= 0 ? 'im-pos' : 'im-neg';
  el.innerHTML = `
    <div class="im-head">🔒 Internal · ${TIER_LABELS[tier]}</div>
    <div class="im-row"><span>Material</span><strong>${fmtCur(p.material)}</strong></div>
    <div class="im-row"><span>Labor</span><strong>${fmtCur(p.labor)}</strong></div>
    <div class="im-row im-cost"><span>Total Cost</span><strong>${fmtCur(p.cost)}</strong></div>
    <div class="im-row ${profClass}"><span>Profit</span><strong>${fmtCur(p.profit)}</strong></div>
    <div class="im-row im-margin"><span>Margin</span><strong>${_pct(p.margin)}</strong></div>
    ${p.simpleSell>0?`<div class="im-note">+${fmtCur(p.simpleSell)} simple-priced (cost not tracked)</div>`:''}`;
}

// Full all-tiers breakdown panel on the Pricing page (rep-only).
function renderCostProfitPanel() {
  const el = document.getElementById('cost-profit-panel');
  if (!el) return;
  const data = {good:tierProfit('good'), better:tierProfit('better'), best:tierProfit('best')};
  const anything = TIERS.some(t => data[t].sell !== 0 || data[t].cost !== 0);
  if (!anything) { el.innerHTML = ''; return; }
  const selTier = S.selected_tier;
  const row = (label, fn, cls='') => `<tr class="${cls}"><td>${label}</td>${
    TIERS.map(t=>`<td>${fn(data[t])}</td>`).join('')}</tr>`;
  const moneyRow = (label, key, cls='') => row(label, d=>fmtCur(d[key]), cls);
  const pt = data[selTier].perTrade.filter(x=>x.mode!=='simple');
  const simpleRows = data[selTier].perTrade.filter(x=>x.mode==='simple');

  el.innerHTML = `
    <div class="cost-profit-panel">
      <div class="cpp-head">
        <span class="cpp-badge">🔒 Internal</span>
        <h3>Cost &amp; Profit</h3>
        <span class="cpp-note">Never shown to the customer</span>
      </div>
      <table class="cpp-table">
        <thead><tr><th></th>${TIERS.map(t=>`<th class="${t===selTier?'cpp-sel':''}">${TIER_LABELS[t]}</th>`).join('')}</tr></thead>
        <tbody>
          ${moneyRow('Material','material')}
          ${moneyRow('Labor','labor')}
          ${moneyRow('Total Cost','cost','cpp-cost')}
          ${moneyRow('Sell Price','sell','cpp-sell')}
          ${row('Profit', d=>`<span class="${d.profit>=0?'cpp-pos':'cpp-neg'}">${fmtCur(d.profit)}</span>`,'cpp-profit')}
          ${row('Margin %', d=>_pct(d.margin),'cpp-margin')}
        </tbody>
      </table>
      ${data[selTier].simpleSell>0?`<div class="cpp-simple-note">Simple-priced trades (e.g. Gutters): ${fmtCur(data[selTier].simpleSell)} sell — cost not tracked, excluded from profit above.</div>`:''}
      ${pt.length?`
      <details class="cpp-bytrade">
        <summary>Per-trade breakdown — ${TIER_LABELS[selTier]}</summary>
        <div class="cpp-trade-wrap"><table class="cpp-table cpp-table-trade">
          <thead><tr><th>Trade</th><th>Material</th><th>Labor</th><th>Cost</th><th>Sell</th><th>Profit</th><th>Margin</th></tr></thead>
          <tbody>
            ${pt.map(x=>`<tr>
              <td>${TRADE_LABELS[x.trade]}</td>
              <td>${fmtCur(x.material)}</td>
              <td>${fmtCur(x.labor)}</td>
              <td>${fmtCur(x.cost)}</td>
              <td>${fmtCur(x.sell)}</td>
              <td class="${x.profit>=0?'cpp-pos':'cpp-neg'}">${fmtCur(x.profit)}</td>
              <td>${x.sell>0?_pct((x.profit/x.sell)*100):'—'}</td>
            </tr>`).join('')}
            ${simpleRows.map(x=>`<tr class="cpp-trade-simple">
              <td>${TRADE_LABELS[x.trade]}</td>
              <td colspan="3">simple pricing — cost not tracked</td>
              <td>${fmtCur(x.sell)}</td>
              <td>—</td><td>—</td>
            </tr>`).join('')}
          </tbody>
        </table></div>
      </details>`:''}
    </div>`;
}

/* ── Page 1: Cover ─────────────────────────────────────────────────── */

function renderCoverPage() {
  const coverPhoto = S.photos.find(p => p.id === S.cover_photo_id) || null;
  const c  = S.customer;
  const cs = [c.address.city, c.address.state].filter(Boolean).join(', ');
  const addr = [c.address.street, cs].filter(Boolean).join(', ');
  const estNum = S.estimate_id ? 'EST-' + S.estimate_id.split(  '-')[0].toUpperCase() : '—';

  document.getElementById('page-cover').innerHTML = `
    <div class="cover-page">
      <div class="cover-photo-zone ${!coverPhoto ? 'cover-zone-empty' : ''}" id="cover-zone"
        ${!coverPhoto
          ? `onclick="document.getElementById('cover-photo-input').click()"
             ondragover="event.preventDefault();this.classList.add('drag-over')"
             ondragleave="this.classList.remove('drag-over')"
             ondrop="event.preventDefault();this.classList.remove('drag-over');uploadAsCoverPhoto(event.dataTransfer.files)"`
          : ''}>
        ${coverPhoto
          ? `<img class="cover-photo-img" src="/uploads/${esc(coverPhoto.filename)}" alt="Property photo">`
          : ''}
        <div class="cover-photo-overlay"></div>
        <div class="cover-logo-overlay">
          ${!coverPhoto ? `
            <img src="/static/logo.png" class="cover-logo-img" alt="Project One Roofing">
            <div class="cover-upload-hint">
              <div class="cover-upload-icon">📷</div>
              <strong>Click to add a cover photo</strong>
              <div>or drag &amp; drop an image here</div>
            </div>` : `<img src="/static/logo.png" class="cover-logo-img" alt="Project One Roofing">`}
        </div>
        ${coverPhoto ? `
          <div class="cover-photo-actions">
            <button class="cover-action-btn" onclick="document.getElementById('cover-photo-input').click();event.stopPropagation()">Change</button>
            <button class="cover-action-btn" onclick="clearCoverPhoto();event.stopPropagation()">Remove</button>
          </div>` : ''}
      </div>

      <div class="cover-info-bar">
        <div>
          <div class="cover-customer-name">${esc(c.name || 'Customer Name')}</div>
          <div class="cover-address">${esc(addr || 'Project Address')}</div>
        </div>
        <div>
          <div class="cover-meta-pills">
            <span class="cover-pill">${esc(estNum)}</span>
            <span class="cover-pill">${esc(S.estimate_date || '—')}</span>
            ${S.salesperson ? `<span class="cover-pill">${esc(cap(S.salesperson))}</span>` : ''}
          </div>
          <button class="cover-set-photo" onclick="switchPage('photos')">${S.photos.length ? 'Manage Photos' : 'Photos →'}</button>
        </div>
      </div>

      ${S.photos.length ? `
        <div style="padding:8px 0">
          <div style="font-size:10px;color:var(--text-light);font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">
            ${coverPhoto ? 'Change cover — click a photo below' : 'Or pick from uploaded photos:'}
          </div>
          <div class="cover-photo-strip">
            ${S.photos.map(p => `
              <div class="cover-strip-thumb ${p.id===S.cover_photo_id?'is-cover':''}"
                onclick="setCoverPhoto('${p.id}')">
                <img src="/uploads/${esc(p.filename)}" alt="">
                ${p.id===S.cover_photo_id
                  ? '<div class="cover-strip-label">COVER</div>' : ''}
              </div>`).join('')}
          </div>
        </div>` : ''}
    </div>`;
}

function setCoverPhoto(id) {
  S.cover_photo_id = (S.cover_photo_id === id) ? null : id;
  setDirty(); renderCoverPage(); warmPrintPhotos();
}
function clearCoverPhoto() { S.cover_photo_id = null; setDirty(); renderCoverPage(); }

/* Upload a photo directly from the cover zone and auto-set it as cover */
async function uploadAsCoverPhoto(files) {
  if (!files || !files.length) return;
  const file = files[0];
  if (!file.type.startsWith('image/')) { alert('Please select an image file.'); return; }
  if (!S.estimate_id) await saveEstimate();
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch(`/api/uploads/${S.estimate_id}`, { method: 'POST', body: fd });
    if (!r.ok) { const e = await r.json().catch(()=>({})); throw new Error(e.error || `Upload failed (${r.status})`); }
    const res = await r.json();
    const photoId = uid();
    S.photos.push({ id: photoId, filename: res.filename, original_name: file.name, caption: '', show_in_estimate: true });
    S.cover_photo_id = photoId;
    setDirty(); renderPhotos(); renderCoverPage(); warmPrintPhotos();
  } catch(e) { alert(`Could not upload cover photo: ${e.message}`); }
  const inp = document.getElementById('cover-photo-input');
  if (inp) inp.value = '';
}

/* ── Page 2: Introduction letter ───────────────────────────────────── */

function renderIntroPage() {
  const saved = priceBook?.intros || [];
  document.getElementById('page-intro').innerHTML = `
    <div class="intro-header">
      <div>
        <h2>Introduction Letter</h2>
        <p>A personalized letter printed right after the cover page. Choose a starter or one of your saved templates.</p>
      </div>
    </div>
    <div class="intro-template-bar">
      <span class="intro-tpl-label">Starter:</span>
      ${INTRO_TEMPLATES.map(t =>
        `<button class="intro-tpl-btn" onclick="applyIntroTemplate('${t.id}')">${esc(t.label)}</button>`
      ).join('')}
      <button class="intro-tpl-btn intro-tpl-clear" onclick="clearIntroText()">✕ Clear</button>
    </div>
    ${saved.length ? `
    <div class="intro-template-bar" style="background:#f0fdf4;border-top:1px solid #a7f3d0">
      <span class="intro-tpl-label" style="color:#065f46">Saved:</span>
      ${saved.map(t => `
        <button class="intro-tpl-btn intro-tpl-saved" onclick="loadSavedIntroTemplate('${t.id}')">${esc(t.name)}</button>
        <button class="intro-tpl-del" onclick="deleteIntroTemplate('${t.id}')" title="Delete '${esc(t.name)}'">✕</button>
      `).join('')}
    </div>` : ''}
    <div class="intro-editor-actions">
      <button class="btn-secondary" onclick="saveIntroTemplate()">💾 Save as Template…</button>
    </div>
    <div class="intro-editor-wrap">
      <textarea id="intro-textarea" class="intro-textarea"
        placeholder="Write a personalized introduction here, or pick a starter template above…"
        oninput="S.intro_text=this.value;setDirty()">${esc(S.intro_text||'')}</textarea>
    </div>`;
}

function applyIntroTemplate(id) {
  const tpl = INTRO_TEMPLATES.find(t => t.id === id);
  if (!tpl) return;
  if (S.intro_text && !confirm('Replace the current introduction with this template?')) return;
  const name = S.customer.name || 'Homeowner';
  S.intro_text = tpl.text.replace(/\{name\}/g, name);
  // also auto-enable intro in print when a template is applied
  if (!S.page_visibility) S.page_visibility = {};
  S.page_visibility.intro = true;
  setDirty();
  const ta = document.getElementById('intro-textarea');
  if (ta) ta.value = S.intro_text;
  renderPrintPagesBar();
}

function clearIntroText() {
  if (S.intro_text && !confirm('Clear the introduction text?')) return;
  S.intro_text = '';
  setDirty();
  const ta = document.getElementById('intro-textarea');
  if (ta) ta.value = '';
}

async function saveIntroTemplate() {
  const text = (S.intro_text || '').trim();
  if (!text) { alert('Write some intro text first, then save it as a template.'); return; }
  const name = prompt('Name this template:', '');
  if (!name?.trim()) return;
  const tpl = { id: 'usr_' + Date.now().toString(36), name: name.trim(), text };
  try {
    const r = await fetch('/api/pricebook/intros', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(tpl)
    });
    const res = await r.json();
    if (!priceBook) priceBook = { intros: [], materials: {} };
    priceBook.intros = res.intros;
    renderIntroPage();
    // keep textarea value
    const ta = document.getElementById('intro-textarea');
    if (ta) ta.value = S.intro_text || '';
  } catch(e) { alert('Could not save template: ' + e.message); }
}

function loadSavedIntroTemplate(id) {
  const tpl = priceBook?.intros?.find(t => t.id === id);
  if (!tpl) return;
  if (S.intro_text?.trim() && !confirm('Replace the current introduction with this template?')) return;
  const name = S.customer.name || 'Homeowner';
  S.intro_text = tpl.text.replace(/\{name\}/g, name);
  if (!S.page_visibility) S.page_visibility = {};
  S.page_visibility.intro = true;
  setDirty();
  const ta = document.getElementById('intro-textarea');
  if (ta) ta.value = S.intro_text;
  renderPrintPagesBar();
}

async function deleteIntroTemplate(id) {
  const tpl = priceBook?.intros?.find(t => t.id === id);
  if (!tpl || !confirm(`Delete template "${tpl.name}"?`)) return;
  try {
    const r = await fetch(`/api/pricebook/intros/${id}`, { method: 'DELETE' });
    const res = await r.json();
    if (priceBook) priceBook.intros = res.intros;
    renderIntroPage();
    const ta = document.getElementById('intro-textarea');
    if (ta) ta.value = S.intro_text || '';
  } catch(e) { alert('Could not delete template: ' + e.message); }
}

/* ── Price Book ─────────────────────────────────────────────────────── */

const PB_TRADES = TRADES.filter(t => t !== 'insurance');

function openPriceBook() {
  // Seed the editor from /api/templates: when a price book is saved it is the
  // authoritative list, and the server backfills rich descriptions, notes, and
  // measure keys from the hardcoded defaults — so the editor shows the exact
  // items (and Auto-Qty links) that get loaded into estimates.
  pbItems = {};
  PB_TRADES.forEach(trade => {
    const fromTemplates = templates?.[trade];
    const stored = priceBook?.materials?.[trade];
    const defaults = (fromTemplates && fromTemplates.length) ? fromTemplates
                   : (stored && stored.length) ? stored
                   : [];
    pbItems[trade] = defaults.map(t => Object.assign({}, t));
  });
  pbActiveTrade = 'roofing';
  pbActiveTab = 'master';
  renderPBModal();
  document.getElementById('pricebook-modal').classList.remove('hidden');
}

function closePriceBook() {
  document.getElementById('pricebook-modal').classList.add('hidden');
}
function maybePBModalClose(e) {
  if (e.target === document.getElementById('pricebook-modal')) closePriceBook();
}

const PB_SUBTABS = [['master','Master Catalog'],['good','Good'],['better','Better'],['best','Best']];

function renderPBModal() {
  document.getElementById('pb-modal-body').innerHTML = `
    <div class="pb-trade-bar">
      ${PB_TRADES.map(t => `
        <button class="pb-trade-btn ${t===pbActiveTrade?'active':''}"
          onclick="pbActiveTrade='${t}';renderPBModal()">
          ${TRADE_LABELS[t]}
        </button>`).join('')}
    </div>
    <div class="pb-content">
      <div class="pb-toolbar">
        <h3>${TRADE_LABELS[pbActiveTrade]} Price Book</h3>
        <button class="btn-secondary" onclick="pbImportFromEstimate()" title="Copy current estimate items into this trade">↑ Import from Estimate</button>
        <button class="btn-secondary" onclick="pbApplyToEstimate()" title="Replace estimate items with price book">↓ Load into Estimate</button>
      </div>
      <div class="pb-subtab-bar">
        ${PB_SUBTABS.map(([k,label]) => `
          <button class="pb-subtab ${k===pbActiveTab?'active':''} ${k!=='master'?'pb-subtab-'+k:''}"
            onclick="pbActiveTab='${k}';renderPBModal()">
            ${label}${k!=='master'?` <span class="pb-subtab-count">${pbTierCount(k)}</span>`:''}
          </button>`).join('')}
      </div>
      ${pbActiveTab==='master' ? pbRenderMaster() : pbRenderTier(pbActiveTab)}
    </div>
    <div class="pb-footer">
      <span class="pb-footer-note">${pbActiveTab==='master'
        ? 'Master catalog — every product for this trade. Tick which packages each belongs to; set the base cost here.'
        : `Products in your <strong>${TIER_LABELS[pbActiveTab]}</strong> package. The cost here overrides the master base price for this tier only.`}</span>
      <button class="btn-secondary" onclick="pbResetTrade()" style="color:var(--danger)">Reset to Defaults</button>
      <button class="btn-primary" onclick="pbSave()">💾 Save Price Book</button>
    </div>`;
}

function pbTierCount(tier) {
  return (pbItems[pbActiveTrade]||[]).filter(it => it['in_'+tier] !== false).length;
}

// Master tab: full product catalog for the trade + which packages each is in.
function pbRenderMaster() {
  const items = pbItems[pbActiveTrade] || [];
  return `
    <div class="pb-table-wrap">
    <table class="pb-table">
      <thead><tr>
        <th class="pb-th-name">Product Name</th>
        <th>Unit</th>
        <th class="pb-th-auto">Auto Qty From</th>
        <th class="pb-th-basecost">Base Cost</th>
        <th class="pb-th-mem">In Packages</th>
        <th class="pb-th-vis">Show</th>
        <th></th>
      </tr></thead>
      <tbody>
        ${items.length ? items.map((item, i) => {
          const base = item.cost !== undefined ? parseFloat(item.cost)||0 : ((parseFloat(item.mat_better)||0)+(parseFloat(item.lab_better)||0));
          return `<tr class="pb-item-row">
            <td><input class="pb-input-name" type="text" value="${esc(item.name||'')}" oninput="pbItems['${pbActiveTrade}'][${i}].name=this.value" placeholder="Product name"></td>
            <td><input class="pb-input-unit" type="text" value="${esc(item.unit||'')}" oninput="pbItems['${pbActiveTrade}'][${i}].unit=this.value" placeholder="Unit"></td>
            <td class="pb-auto-cell">${pbMeasureCell(item, i)}</td>
            <td><input class="pb-cost-input" type="number" min="0" step="0.01" value="${base}" placeholder="0.00"
              oninput="pbItems['${pbActiveTrade}'][${i}].cost=parseFloat(this.value)||0"></td>
            <td class="pb-mem-cell">
              ${['good','better','best'].map(tier=>{
                const on = item['in_'+tier] !== false;
                return `<label class="pb-mem-chip pb-mem-${tier} ${on?'on':''}" title="In ${TIER_LABELS[tier]}">
                  <input type="checkbox" ${on?'checked':''}
                    onchange="pbItems['${pbActiveTrade}'][${i}].in_${tier}=this.checked;renderPBModal()">${TIER_LABELS[tier][0]}</label>`;
              }).join('')}
            </td>
            <td style="text-align:center">
              <input type="checkbox" title="Visible on the customer estimate" ${item.customer_visible!==false?'checked':''}
                onchange="pbItems['${pbActiveTrade}'][${i}].customer_visible=this.checked">
            </td>
            <td style="text-align:center"><button class="pb-del-btn" title="Delete product" onclick="pbDeleteItem(${i})">✕</button></td>
          </tr>`;
        }).join('') : `<tr><td colspan="7" class="pb-empty">No products yet — add your first one below.</td></tr>`}
      </tbody>
    </table>
    </div>
    <div style="margin-top:10px"><button class="btn-secondary" onclick="pbAddItem()">+ Add Product</button></div>`;
}

// Good / Better / Best tab: the products that make up one package.
function pbRenderTier(tier) {
  const items = pbItems[pbActiveTrade] || [];
  const members    = items.map((it,i)=>({it,i})).filter(x => x.it['in_'+tier] !== false);
  const nonMembers = items.map((it,i)=>({it,i})).filter(x => x.it['in_'+tier] === false && (x.it.name||'').trim());
  return `
    <div class="pb-table-wrap">
    <table class="pb-table">
      <thead><tr>
        <th class="pb-th-name">Product</th>
        <th>Unit</th>
        <th>Customer Label <span class="pb-th-hint">(optional)</span></th>
        <th class="pb-th-cost">${TIER_LABELS[tier]} Cost</th>
        <th></th>
      </tr></thead>
      <tbody>
        ${members.length ? members.map(({it,i}) => {
          const base = it.cost !== undefined ? parseFloat(it.cost)||0 : 0;
          const hasOverride = it['cost_'+tier] !== undefined;
          const tc = hasOverride ? parseFloat(it['cost_'+tier])||0 : base;
          const overridden = hasOverride && tc !== base;
          return `<tr class="pb-item-row">
            <td class="pb-tier-prod">${esc(it.name||'(unnamed)')}</td>
            <td class="pb-tier-unit">${esc(it.unit||'')}</td>
            <td><input class="pb-product-input pb-label-input" type="text" value="${esc(it['product_'+tier]||'')}" placeholder="${esc(it.name||'')}"
              oninput="pbItems['${pbActiveTrade}'][${i}].product_${tier}=this.value"></td>
            <td><input class="pb-cost-input ${overridden?'pb-cost-override':''}" type="number" min="0" step="0.01" value="${tc}" placeholder="${base}"
              title="${overridden?'Overrides master base ('+fmtCur(base)+')':'Matches master base price'}"
              oninput="pbItems['${pbActiveTrade}'][${i}].cost_${tier}=parseFloat(this.value)||0"></td>
            <td style="text-align:center"><button class="pb-del-btn" title="Remove from ${TIER_LABELS[tier]}"
              onclick="pbRemoveFromTier(${i},'${tier}')">✕</button></td>
          </tr>`;
        }).join('') : `<tr><td colspan="5" class="pb-empty">No products in the ${TIER_LABELS[tier]} package yet — add some below.</td></tr>`}
      </tbody>
    </table>
    </div>
    <div class="pb-add-tier">
      ${nonMembers.length ? `
        <select class="pb-add-select" onchange="if(this.value!==''){pbAddToTier(parseInt(this.value),'${tier}')}">
          <option value="">+ Add a product to ${TIER_LABELS[tier]}…</option>
          ${nonMembers.map(({it,i})=>`<option value="${i}">${esc(it.name)} — ${fmtCur(it.cost!==undefined?parseFloat(it.cost)||0:0)}</option>`).join('')}
        </select>`
        : `<span class="pb-add-hint">Every product is already in this package. Add new products on the <a onclick="pbActiveTab='master';renderPBModal()">Master Catalog</a> tab.</span>`}
    </div>`;
}

function pbAddToTier(i, tier) {
  const it = pbItems[pbActiveTrade][i]; if (!it) return;
  it['in_'+tier] = true;
  if (it['cost_'+tier] === undefined) it['cost_'+tier] = it.cost !== undefined ? parseFloat(it.cost)||0 : 0;
  renderPBModal();
}
function pbRemoveFromTier(i, tier) {
  const it = pbItems[pbActiveTrade][i]; if (!it) return;
  it['in_'+tier] = false;
  renderPBModal();
}

function pbAddItem() {
  pbItems[pbActiveTrade] = pbItems[pbActiveTrade] || [];
  pbItems[pbActiveTrade].push({ name:'', unit:'EA', cost:0, customer_visible:true });
  renderPBModal();
}

function pbDeleteItem(i) {
  pbItems[pbActiveTrade].splice(i, 1);
  renderPBModal();
}

// Renders the per-item "Auto Qty From" picker in the price book (measurement
// key or a custom formula). What you set here becomes the quantity source the
// moment the item is loaded into an estimate.
function pbMeasureCell(item, i) {
  const isFormula = !!item.formula;
  const cur = isFormula ? '__formula__' : (item.measure || '');
  const formulaInput = isFormula ? `
    <input class="pb-formula-input" type="text" value="${esc(item.formula||'')}"
      placeholder="eave_lf + valley_lf"
      title="Variables: roof_squares, waste_pct, ridge_hip_lf, valley_lf, eave_lf, rake_lf, step_flash_lf, pipe_boots, skylights, turtle_vents, broan_4in, broan_8in"
      oninput="pbItems['${pbActiveTrade}'][${i}].formula=this.value;pbItems['${pbActiveTrade}'][${i}].measure=undefined">` : '';
  return `
    <select class="pb-measure-select" onchange="pbSetMeasure(${i}, this.value)">
      <option value="">Manual</option>
      ${Object.entries(MEASURE_DEFS).map(([k,d])=>`<option value="${k}" ${cur===k?'selected':''}>${d.label}</option>`).join('')}
      <option value="__formula__" ${isFormula?'selected':''}>Custom formula…</option>
    </select>
    ${formulaInput}`;
}
function pbSetMeasure(i, val) {
  const it = pbItems[pbActiveTrade][i];
  if (!it) return;
  if (val === '__formula__') { it.formula = it.formula || ''; it.measure = undefined; }
  else { it.measure = val || undefined; it.formula = undefined; }
  renderPBModal();
}

function pbResetTrade() {
  if (!confirm(`Reset ${TRADE_LABELS[pbActiveTrade]} to factory defaults?`)) return;
  pbItems[pbActiveTrade] = (templates?.[pbActiveTrade] || []).map(t => Object.assign({}, t));
  renderPBModal();
}

function pbImportFromEstimate() {
  const td = S.trades[pbActiveTrade];
  if (!td?.line_items?.length) { alert('No items in the current estimate for this trade.'); return; }
  if (!confirm(`Import ${td.line_items.length} items from the current estimate into the ${TRADE_LABELS[pbActiveTrade]} price book?`)) return;
  const tier = S.selected_tier || 'better';
  pbItems[pbActiveTrade] = td.line_items.map(item => {
    const gt = item.tiers?.good   || {};
    const bt = item.tiers?.better || {};
    const xt = item.tiers?.best   || {};
    return {
      name: item.name, unit: item.unit,
      measure: item.measure || undefined,
      cost: (parseFloat(bt.material_unit_cost)||0) + (parseFloat(bt.labor_unit_cost)||0),
      cost_good:   parseFloat(gt.material_unit_cost)||0,
      cost_better: parseFloat(bt.material_unit_cost)||0,
      cost_best:   parseFloat(xt.material_unit_cost)||0,
      in_good:   gt.included !== false,
      in_better: bt.included !== false,
      in_best:   xt.included !== false,
      customer_visible: item.customer_visible !== false,
      product_good:    gt.description || '',
      product_better:  bt.description || '',
      product_best:    xt.description || '',
      notes_good:   gt.notes || '',
      notes_better: bt.notes || '',
      notes_best:   xt.notes || '',
    };
  });
  renderPBModal();
}

function pbApplyToEstimate() {
  const items = pbItems[pbActiveTrade];
  if (!items?.length) { alert('No items in this trade to load.'); return; }
  if (S.trades[pbActiveTrade].line_items.length &&
      !confirm(`Replace current estimate ${TRADE_LABELS[pbActiveTrade]} items with price book?`)) return;
  S.trades[pbActiveTrade].enabled = true;
  S.trades[pbActiveTrade].line_items = items.map(t => {
    const baseCost = t.cost !== undefined ? parseFloat(t.cost)||0 : (parseFloat(t.mat_better)||0)+(parseFloat(t.lab_better)||0);
    const costGood   = t.cost_good   !== undefined ? parseFloat(t.cost_good)||0   : baseCost;
    const costBetter = t.cost_better !== undefined ? parseFloat(t.cost_better)||0 : baseCost;
    const costBest   = t.cost_best   !== undefined ? parseFloat(t.cost_best)||0   : baseCost;
    const descGood   = t.product_good   || t.desc_good   || '';
    const descBetter = t.product_better || t.desc_better || '';
    const descBest   = t.product_best   || t.desc_best   || '';
    return {
      id: uid(), name: t.name, unit: t.unit, quantity: 0, scope_note: '',
      customer_visible: t.customer_visible !== false,
      measure: t.measure || undefined,
      formula: t.formula || undefined,
      tiers: {
        good:  { material_unit_cost: costGood,   labor_unit_cost: 0, description: descGood,   notes: t.notes_good||'',   included: t.in_good   !== false },
        better:{ material_unit_cost: costBetter, labor_unit_cost: 0, description: descBetter, notes: t.notes_better||'', included: t.in_better !== false },
        best:  { material_unit_cost: costBest,   labor_unit_cost: 0, description: descBest,   notes: t.notes_best||'',   included: t.in_best   !== false },
      }
    };
  });
  // Apply any measurements already entered so quantities fill in immediately
  applyMeasurements();
  setDirty(); rerender();
  alert(`✓ ${TRADE_LABELS[pbActiveTrade]} loaded into estimate.`);
}

async function pbSave() {
  if (!priceBook) priceBook = { intros: [], materials: {} };
  priceBook.materials = pbItems;
  // Also refresh the templates cache so loadDefaults uses new prices
  try {
    await fetch('/api/pricebook', {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(priceBook)
    });
    // Refresh templates from server
    const r = await fetch('/api/templates');
    templates = await r.json();
    alert('✓ Price Book saved!');
  } catch(e) { alert('Could not save: ' + e.message); }
}

/* ── Print pages bar ────────────────────────────────────────────────── */

function renderPrintPagesBar() {
  const pv = S.page_visibility || {};
  const pages = [
    { id:'cover',    label:'Cover',        on: true,                      always: true },
    { id:'intro',    label:'Introduction', on: pv.intro   !== false,      always: false },
    { id:'options',  label:'Options',      on: pv.options !== false,      always: false },
    { id:'contract', label:'Contract',     on: S.print_contract !== false, always: false },
  ];
  const el = document.getElementById('print-pages-bar');
  if (!el) return;
  el.innerHTML =
    `<span class="ppb-label">Print Pages:</span>` +
    pages.map(p => `
      <button class="ppb-btn ${p.on ? 'on' : 'off'} ${p.always ? 'ppb-always' : ''}"
        onclick="togglePagePrint('${p.id}')"
        title="${p.always ? 'Always included' : (p.on ? 'Click to exclude from print' : 'Click to include in print')}">
        <span class="ppb-dot"></span>${esc(p.label)}
      </button>`).join('');
}

function togglePagePrint(page) {
  if (page === 'cover') return;
  if (!S.page_visibility) S.page_visibility = {};
  if (page === 'contract') {
    S.print_contract = !(S.print_contract !== false);
  } else {
    S.page_visibility[page] = !(S.page_visibility[page] !== false);
  }
  setDirty();
  renderPrintPagesBar();
}

/* ── Page 3: Scope / Measurements ──────────────────────────────────── */

function renderScopePage() {
  // Insurance mode: measurements/GBB scope doesn't apply — show a notice
  if ((S.estimate_type || 'retail') === 'insurance') {
    document.getElementById('page-scope').innerHTML = `
      <div class="scope-header">
        <div>
          <h2>Scope of Work — Measurements</h2>
          <p>Switched to Insurance mode — measurements are not used for this estimate</p>
        </div>
      </div>
      <div class="ins-mode-notice">
        <div class="ins-mode-icon">🏛</div>
        <div class="ins-mode-title">Insurance Estimate Active</div>
        <div class="ins-mode-body">Insurance estimates use sections with ACV + Depreciation = RCV line items instead of measured quantities. Enter your sections, items, and scope of work in the Pricing tab.</div>
        <button class="btn-primary ins-mode-btn" onclick="activeTrade='insurance';switchPage('pricing')">Go to Insurance Pricing →</button>
        <div class="ins-mode-switch">Wrong mode? <a href="#" onclick="setEstimateType('retail');return false">Switch back to Retail</a></div>
      </div>`;
    return;
  }

  const RETAIL_TRADES = TRADES.filter(t => t !== 'insurance');
  const allItems = [];
  RETAIL_TRADES.forEach(trade => {
    const td = S.trades[trade];
    const items = (td && td.line_items) || [];
    if (items.length) {
      allItems.push({ type:'group', trade });
      items.forEach(item => allItems.push({ type:'item', trade, item }));
    }
  });

  const hasAny = allItems.some(r => r.type === 'item');
  const m = S.measurements || {};

  // Measurements drive the Roofing estimate only — show just the Roof group.
  const measurePanel = `
    <div class="measure-panel">
      <div class="measure-panel-head">
        <h3>📐 Roof Measurements</h3>
        <span class="measure-hint">Enter once — the Roofing estimate auto-builds with quantities &amp; Good / Better / Best pricing</span>
      </div>
      <div class="measure-groups">
        ${MEASURE_FIELDS.filter(g => g.group === 'Roof').map(g => `
          <div class="measure-group">
            <div class="measure-group-title">${g.group}</div>
            <div class="measure-fields">
              ${g.fields.map(f => `
                <div class="measure-field">
                  <label>${f.label}</label>
                  <div class="measure-input-wrap">
                    <input type="number" min="0" step="${f.unit==='SQ'?'0.1':f.unit==='%'?'1':'1'}"
                      value="${m[f.key] !== undefined && m[f.key] !== 0 ? m[f.key] : (f.key==='waste_pct' ? (m.waste_pct ?? 10) : '')}"
                      placeholder="0"
                      onchange="setMeasurement('${f.key}', this.value)">
                    <span class="measure-unit">${f.unit}</span>
                  </div>
                </div>`).join('')}
            </div>
          </div>`).join('')}
      </div>
    </div>`;

  const measureOptions = (item) => {
    const isFormula = !!item.formula;
    const cur = isFormula ? '__formula__' : (item.measure || '');
    const isAuto = !!MEASURE_DEFS[item.measure];
    const formulaInput = isFormula ? `
      <div class="scope-formula-wrap">
        <input class="scope-formula-input" type="text"
          value="${esc(item.formula||'')}"
          placeholder="e.g. eave_lf + valley_lf"
          title="Available variables: roof_squares, waste_pct, ridge_hip_lf, valley_lf, eave_lf, rake_lf, step_flash_lf, pipe_boots, skylights, turtle_vents, broan_4in, broan_8in"
          onchange="setItemFormula('${item._trade}','${item.id}',this.value)">
        <span class="scope-formula-hint">eave_lf + valley_lf</span>
      </div>` : '';
    return `<div class="scope-measure-cell-inner">
      <select class="scope-measure-select ${(isAuto||isFormula)?'is-auto':''}"
        onchange="handleMeasureSelect('${item._trade}','${item.id}',this.value)"
        title="Auto-fill quantity from a measurement">
        <option value="">Manual</option>
        ${Object.entries(MEASURE_DEFS).map(([k, d]) =>
          `<option value="${k}" ${cur===k?'selected':''}>${d.label}</option>`).join('')}
        <option value="__formula__" ${isFormula?'selected':''}>Custom formula…</option>
      </select>
      ${formulaInput}
    </div>`;
  };

  document.getElementById('page-scope').innerHTML = `
    <div class="scope-header">
      <div>
        <h2>Scope of Work — Measurements</h2>
        <p>Enter measurements once. Linked items auto-fill, and Good / Better / Best price automatically.</p>
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        ${RETAIL_TRADES.map(trade => `
          <label class="scope-trade-toggle ${S.trades[trade].enabled?'enabled':''}">
            <input type="checkbox" ${S.trades[trade].enabled?'checked':''}
              onchange="toggleTrade('${trade}',this.checked)">
            ${TRADE_LABELS[trade]}
          </label>`).join('')}
      </div>
    </div>

    ${measurePanel}

    ${hasAny ? `
      <div class="scope-table-wrap">
        <table class="scope-table">
          <thead>
            <tr>
              <th>Item</th>
              <th class="th-auto">Auto Qty From</th>
              <th class="th-qty">Quantity</th>
              <th class="th-unit">Unit</th>
              <th class="th-note">Measurement Notes</th>
              <th style="width:36px"></th>
            </tr>
          </thead>
          <tbody>
            ${allItems.map(row => {
              if (row.type === 'group') {
                return `<tr class="scope-trade-group">
                  <td colspan="6">${TRADE_LABELS[row.trade]}
                    <button class="scope-defaults-btn" style="margin-left:10px"
                      onclick="loadDefaults('${row.trade}')">Load Defaults</button>
                  </td>
                </tr>`;
              }
              const { trade, item } = row;
              item._trade = trade;
              const isAuto = !!MEASURE_DEFS[item.measure] || !!item.formula;
              return `<tr>
                <td class="scope-name-cell">${esc(item.name)}</td>
                <td class="scope-measure-cell">${measureOptions(item)}</td>
                <td style="text-align:center">
                  <input class="scope-qty-input ${isAuto?'qty-auto':''}" type="number" min="0" step="0.5"
                    value="${item.quantity||''}" placeholder="0"
                    data-trade="${trade}" data-id="${item.id}" data-measured="${isAuto?1:0}"
                    ${isAuto?'readonly title="Auto-calculated — switch to Manual to edit"':''}
                    onchange="liSetQty('${trade}','${item.id}',this.value)">
                </td>
                <td class="scope-unit-cell">${esc(item.unit)}</td>
                <td>
                  <input class="scope-note-input" type="text"
                    value="${esc(item.scope_note||'')}"
                    placeholder="e.g. North slope only, replace only…"
                    onchange="liSetScopeNote('${trade}','${item.id}',this.value)">
                </td>
                <td>
                  <button class="li-del" onclick="liDelete('${trade}','${item.id}')" title="Remove">×</button>
                </td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>
    ` : `
      <div class="scope-empty">
        <p>No items yet. Enable a trade above and click <strong>Load Defaults</strong> to get started.</p>
        ${RETAIL_TRADES.filter(t=>S.trades[t].enabled).map(t =>
          `<button class="scope-defaults-btn" style="margin:6px 4px"
            onclick="loadDefaults('${t}')">Load ${TRADE_LABELS[t]} Defaults</button>`
        ).join('')}
      </div>`}`;
}

function liSetScopeNote(trade, id, v) {
  const i = findItem(trade, id); if (!i) return;
  i.scope_note = v; setDirty();
}

/* ── Page 3: Options ────────────────────────────────────────────────── */

function renderOptionsPage() {
  // Insurance mode: show a notice instead of meaningless GBB cards
  if ((S.estimate_type || 'retail') === 'insurance') {
    document.getElementById('page-options').innerHTML = `
      <div class="options-header">
        <h2>Package Options</h2>
        <p>Switched to Insurance mode — Good/Better/Best is not used for this estimate</p>
      </div>
      <div class="ins-mode-notice">
        <div class="ins-mode-icon">🏛</div>
        <div class="ins-mode-title">Insurance Estimate Active</div>
        <div class="ins-mode-body">Your customer will see insurance claim line items — not package tiers. Enter your insurance items, carrier, claim number, and scope of work in the Pricing tab.</div>
        <button class="btn-primary ins-mode-btn" onclick="activeTrade='insurance';switchPage('pricing')">Go to Insurance Pricing →</button>
        <div class="ins-mode-switch">Wrong mode? <a href="#" onclick="setEstimateType('retail');return false">Switch back to Retail</a></div>
      </div>`;
    return;
  }

  document.getElementById('page-options').innerHTML = `
    <div class="options-header options-header-flex">
      <div>
        <h2>Your Options</h2>
        <p>Edit what appears on each package card — these bullet points are what the customer sees when they open the estimate</p>
      </div>
      <button class="btn-save-defaults" onclick="saveTierDefaults()">💾 Save as Global Defaults</button>
    </div>
    <div class="pkg-cards">
      ${TIERS.map(tier => {
        const total    = grandTotal(tier);
        const desc     = (S.tier_descriptions||{})[tier] || '';
        const isSel    = tier === S.selected_tier;
        const features = (S.tier_features||{})[tier] || [];
        return `
          <div class="pkg-card pkg-${tier} ${isSel?'selected':''}">
            <div class="pkg-card-header">
              <span class="pkg-tier-name">${TIER_LABELS[tier]}</span>
              ${isSel?'<span class="pkg-selected-badge">Selected ✓</span>':''}
            </div>
            <div class="pkg-total">${fmtCur(total)}</div>
            <textarea class="pkg-description"
              placeholder="Short tagline for this package…"
              onchange="setPkgDesc('${tier}',this.value)">${esc(desc)}</textarea>
            <div class="pkg-features-wrap">
              <div class="pkg-features-hdr">
                <span>What's Included</span>
                <button class="pkg-autofill-btn" onclick="genPkgFeatures('${tier}')" title="Auto-fill from pricing tab">↻ Auto-fill</button>
              </div>
              <textarea class="pkg-features-ta"
                rows="6"
                placeholder="One item per line — shown as bullet points on the estimate…
e.g. 30-year architectural shingles
5-year workmanship warranty
Full tear-off included"
                oninput="setPkgFeatures('${tier}',this.value)">${esc(features.join('\n'))}</textarea>
            </div>
            <button class="pkg-select-btn ${isSel?'selected':''}" onclick="setTier('${tier}')">
              ${isSel?'✓ Selected Package':`Select ${TIER_LABELS[tier]}`}
            </button>
          </div>`;
      }).join('')}
    </div>`;
}

function setPkgDesc(tier, v) {
  if (!S.tier_descriptions) S.tier_descriptions = {};
  S.tier_descriptions[tier] = v; setDirty();
}
function setPkgFeatures(tier, text) {
  if (!S.tier_features) S.tier_features = { good:[], better:[], best:[] };
  S.tier_features[tier] = text.split('\n').map(s => s.trim()).filter(Boolean);
  setDirty();
}
function genPkgFeatures(tier) {
  const features = [];
  TRADES.forEach(trade => {
    const td = S.trades[trade];
    if (!td || !td.enabled || trade === 'insurance') return;
    const tradeMode = td.mode || (trade === 'gutters' ? 'simple' : 'gbb');
    (td.line_items || []).forEach(item => {
      if (tradeMode === 'simple') {
        if (parseFloat(item.quantity) > 0 || parseFloat(item.unit_price) > 0)
          features.push(item.description ? `${item.name} — ${item.description}` : item.name);
        return;
      }
      const ti = (item.tiers || {})[tier] || {};
      const cost = (parseFloat(ti.material_unit_cost)||0) + (parseFloat(ti.labor_unit_cost)||0);
      if (cost > 0 || parseFloat(item.quantity) > 0)
        features.push(ti.description ? `${item.name} — ${ti.description}` : item.name);
    });
  });
  if (!S.tier_features) S.tier_features = { good:[], better:[], best:[] };
  S.tier_features[tier] = features;
  setDirty();
  renderOptionsPage();
}

// Apply saved global defaults to an estimate's tier_features (only fills empty tiers)
function applyTierDefaults(est) {
  if (!tierDefaults) return;
  if (!est.tier_features) est.tier_features = { good:[], better:[], best:[] };
  ['good','better','best'].forEach(t => {
    if (!est.tier_features[t] || est.tier_features[t].length === 0)
      est.tier_features[t] = [...(tierDefaults[t] || [])];
  });
}

// Save current estimate's tier_features as the global defaults for all new estimates
async function saveTierDefaults() {
  try {
    const data = S.tier_features || { good:[], better:[], best:[] };
    const r = await fetch('/api/tier-defaults', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    if (!r.ok) throw new Error('Server error');
    tierDefaults = JSON.parse(JSON.stringify(data));
    // Brief visual confirmation
    const btn = document.querySelector('.btn-save-defaults');
    if (btn) { btn.textContent = '✓ Defaults Saved!'; setTimeout(()=>{ btn.textContent='💾 Save as Global Defaults'; }, 2200); }
  } catch(e) { alert('Could not save defaults: ' + e.message); }
}

/* ── Page 4: Pricing (trade tabs + GBB) ────────────────────────────── */

function renderPricingPage() {
  renderTabBar();
  renderTradeContent();
}

function renderTabBar() {
  document.getElementById('tab-bar').innerHTML =
    TRADES.map(trade => {
      const en    = S.trades[trade].enabled;
      const isIns = trade === 'insurance';
      return `<button class="tab-btn ${trade===activeTrade?'active':''} ${en?'enabled':''} ${isIns?'tab-ins':''}"
        onclick="switchTrade('${trade}')">${TRADE_LABELS[trade]}${en?' ✓':''}</button>`;
    }).join('');
}

function switchTrade(trade) {
  activeTrade = trade; renderTabBar(); renderTradeContent();
}

function renderTradeContent() {
  const td    = S.trades[activeTrade];
  const trade = activeTrade;
  const isInsurance = trade === 'insurance';
  const isGutters   = trade === 'gutters';
  // Gutters is always simple; insurance has its own model
  const effectiveMode = isInsurance ? 'insurance'
    : isGutters ? 'simple'
    : (td.mode || 'gbb');
  const showModeToggle   = !isInsurance && !isGutters && td.enabled;
  const showLoadDefaults = td.enabled && !isInsurance && trade !== 'other';
  const showColors       = td.enabled && !isInsurance && effectiveMode === 'gbb' && trade !== 'other';

  document.getElementById('trade-content').innerHTML =
    `<div class="trade-header">
      <h2>${TRADE_LABELS[trade]}${isInsurance?' <span class="ins-badge">Insurance Claim</span>':''}</h2>
      <div class="trade-controls">
        <label class="checkbox-label">
          <input type="checkbox" ${td.enabled?'checked':''}
            onchange="toggleTrade('${trade}',this.checked)"> Include in estimate
        </label>
        ${showModeToggle ? `
          <div class="mode-toggle">
            <button class="mode-btn${effectiveMode==='gbb'?' mode-btn-active':''}"
              onclick="setTradeMode('${trade}','gbb')">Good / Better / Best</button>
            <button class="mode-btn${effectiveMode==='simple'?' mode-btn-active':''}"
              onclick="setTradeMode('${trade}','simple')">Simple</button>
          </div>` : ''}
        ${showLoadDefaults ? `
          <button class="btn-secondary" onclick="loadDefaults('${trade}')">Load Defaults</button>` : ''}
        ${td.enabled ? `<button class="btn-danger" onclick="clearTrade('${trade}')">Clear All</button>` : ''}
      </div>
    </div>
    ${showColors ? renderColorSection(trade) : ''}
    ${td.enabled
      ? (isInsurance ? renderInsuranceFreeform()
         : effectiveMode === 'simple' ? renderSimpleFreeform(trade)
         : trade === 'other' ? renderOtherFreeform()
         : renderGBBGrid(trade))
      : `<div class="trade-disabled">${isInsurance
          ? 'Enable to enter insurance claim line items and scope of work.'
          : 'Enable this trade to add line items.'}</div>`}`;
}

function renderOtherFreeform() {
  const trade = 'other';
  const td    = S.trades[trade];
  const tier  = S.selected_tier;
  const items = td.line_items;
  const UNITS = ['EA','LS','SQ','LF','HR','SF','BD'];

  const rows = items.map(item => {
    const t    = item.tiers[tier] || {material_unit_cost:0,labor_unit_cost:0,notes:''};
    const cost = (parseFloat(t.material_unit_cost)||0) + (parseFloat(t.labor_unit_cost)||0);
    const tot  = lineTotal(item.quantity, t.material_unit_cost, t.labor_unit_cost, trade);
    return `<tr>
      <td class="other-name-cell">
        <input class="other-name-input" type="text" value="${esc(item.name)}" placeholder="Item description"
          onchange="liSetName('${trade}','${item.id}',this.value)">
        <input class="other-note-input" type="text" value="${esc(t.notes||'')}" placeholder="Note (optional)"
          onchange="liSetTier('${trade}','${item.id}','${tier}','notes',this.value)">
      </td>
      <td class="other-qty-cell">
        <input class="other-qty-input" type="number" min="0" step="0.5" value="${item.quantity||''}"
          placeholder="1" onchange="liSetQty('${trade}','${item.id}',this.value)">
      </td>
      <td>
        <select class="other-unit-select" onchange="liSetUnit('${trade}','${item.id}',this.value)">
          ${UNITS.map(u=>`<option ${item.unit===u?'selected':''}>${u}</option>`).join('')}
        </select>
      </td>
      <td class="other-price-cell">
        <input class="other-price-input" type="number" min="0" step="0.01"
          value="${cost||''}" placeholder="0.00"
          onchange="otherSetUnitCost('${item.id}',parseFloat(this.value)||0)">
      </td>
      <td class="other-total-cell">${fmtCur(tot)}</td>
      <td><button class="li-del" onclick="liDelete('${trade}','${item.id}')" title="Remove">×</button></td>
    </tr>`;
  }).join('');

  const rate    = tradeRate(trade);
  const subtot  = tradeTotal(trade, tier);
  const modeHint = S.pricing.mode === 'margin'
    ? `${rate}% margin applied`
    : `${rate}% markup applied`;

  return `
    <div class="other-desc">
      Free-form items — enter any custom scope, allowances, or upgrades.
      <span class="other-rate-hint">${modeHint}</span>
    </div>
    ${items.length ? `
      <div class="other-table-wrap">
        <table class="other-table">
          <thead><tr>
            <th>Item / Description</th>
            <th class="other-th-num">Qty</th>
            <th class="other-th-num">Unit</th>
            <th class="other-th-price">Unit Cost</th>
            <th class="other-th-price">Sell Price</th>
            <th style="width:32px"></th>
          </tr></thead>
          <tbody>${rows}</tbody>
          <tfoot><tr>
            <td colspan="4" style="text-align:right;padding-right:12px;font-weight:600">Subtotal</td>
            <td class="other-total-cell" style="font-weight:700;font-size:14px">${fmtCur(subtot)}</td>
            <td></td>
          </tr></tfoot>
        </table>
      </div>` : `<div class="scope-empty"><p>No items yet. Click <strong>+ Add Item</strong> below.</p></div>`}
    <div class="add-row-bar">
      <button class="btn-add" onclick="addLineItem('other')">+ Add Item</button>
    </div>`;
}

function otherSetUnitCost(id, cost) {
  const item = findItem('other', id);
  if (!item) return;
  // sync the same cost across all tiers so it prints on every package
  TIERS.forEach(tier => {
    if (!item.tiers[tier]) item.tiers[tier] = {material_unit_cost:0,labor_unit_cost:0,description:'',notes:''};
    item.tiers[tier].material_unit_cost = cost;
    item.tiers[tier].labor_unit_cost    = 0;
  });
  setDirty(); rerender();
  if (activePage === 'pricing') renderTradeContent();
}

/* ── Simple freeform tab (gutters + toggled trades) ─────────────────── */

function renderSimpleFreeform(trade) {
  const td    = S.trades[trade];
  const items = td.line_items || [];
  const UNITS = ['SQ','LF','EA','HR','LS','SF','BD'];

  const rows = items.map(item => {
    const qty   = parseFloat(item.quantity)  || 0;
    const price = parseFloat(item.unit_price) || 0;
    const total = qty * price;
    return `<tr>
      <td class="ins-name-cell">
        <input class="other-name-input" type="text" value="${esc(item.name||'')}" list="pb-list-${trade}"
          placeholder="Type to search price book…"
          onchange="liSetNameSmart('${trade}','${item.id}',this.value)">
      </td>
      <td class="ins-desc-cell">
        <input class="ins-desc-input" type="text" value="${esc(item.description||'')}" placeholder="Description"
          onchange="simpleSetField('${trade}','${item.id}','description',this.value)">
      </td>
      <td class="other-qty-cell">
        <input class="other-qty-input" type="number" min="0" step="0.5" value="${qty||''}"
          placeholder="0"
          onchange="simpleSetField('${trade}','${item.id}','quantity',parseFloat(this.value)||0);simpleUpdateTotals('${trade}')">
      </td>
      <td>
        <select class="other-unit-select"
          onchange="simpleSetField('${trade}','${item.id}','unit',this.value)">
          ${UNITS.map(u=>`<option ${(item.unit||'SQ')===u?'selected':''}>${u}</option>`).join('')}
        </select>
      </td>
      <td class="other-price-cell">
        <input class="other-price-input" type="number" min="0" step="0.01"
          value="${price||''}" placeholder="0.00"
          onchange="simpleSetField('${trade}','${item.id}','unit_price',parseFloat(this.value)||0);simpleUpdateTotals('${trade}')">
      </td>
      <td class="other-total-cell simple-line-total" data-strade="${trade}" data-sid="${item.id}">${fmtCur(total)}</td>
      <td><button class="li-del" onclick="simpleDeleteItem('${trade}','${item.id}')" title="Remove">×</button></td>
    </tr>`;
  }).join('');

  const grandTot = tradeTotal(trade, S.selected_tier);

  return `
    ${items.length ? `
      <div class="other-table-wrap">
        <table class="other-table ins-table">
          <thead><tr>
            <th class="ins-th-name">Item Name</th>
            <th class="ins-th-desc">Description</th>
            <th class="other-th-num">Qty</th>
            <th class="other-th-num">Unit</th>
            <th class="other-th-price">Price</th>
            <th class="other-th-price">Total</th>
            <th style="width:32px"></th>
          </tr></thead>
          <tbody>${rows}</tbody>
          <tfoot><tr>
            <td colspan="5" style="text-align:right;padding-right:12px;font-weight:600">${TRADE_LABELS[trade]} Subtotal</td>
            <td class="other-total-cell" id="simple-grand-${trade}" style="font-weight:700;font-size:14px">${fmtCur(grandTot)}</td>
            <td></td>
          </tr></tfoot>
        </table>
      </div>` : `<div class="scope-empty"><p>No items yet. Click <strong>+ Add Item</strong> below.</p></div>`}
    ${pbDatalist(trade)}
    <div class="add-row-bar">
      <button class="btn-add" onclick="simpleAddItem('${trade}')">+ Add Item</button>
      <span class="add-row-hint">Tip: type in the item name to pull from your price book, or free-type anything</span>
    </div>`;
}

function setTradeMode(trade, mode) {
  if ((S.trades[trade].mode || 'gbb') === mode) return;
  const td = S.trades[trade];
  const items = td.line_items || [];

  if (mode === 'simple') {
    // GBB → Simple: keep items, quantities & measurement links.
    // Price becomes the current selling price of the selected tier.
    const pricing = S.pricing || {};
    const ov   = (pricing.per_trade_overrides || {})[trade];
    const rate = ov !== null && ov !== undefined ? parseFloat(ov) : parseFloat(pricing.global_rate || 35);
    const tier = S.selected_tier || 'better';
    td.line_items = items.map(it => {
      const t    = (it.tiers || {})[tier] || {};
      const cost = (parseFloat(t.material_unit_cost)||0) + (parseFloat(t.labor_unit_cost)||0);
      const sell = pricing.mode === 'markup' ? cost * (1 + rate/100)
                 : (rate < 100 ? cost / (1 - rate/100) : 0);
      return {
        id: it.id, name: it.name, unit: it.unit,
        quantity: it.quantity || 0,
        measure: it.measure || undefined,
        scope_note: it.scope_note || '',
        description: t.description || it.description || '',
        unit_price: Math.round(sell * 100) / 100,
        customer_visible: it.customer_visible !== false,
      };
    });
  } else {
    // Simple → GBB: keep items, quantities & measurement links.
    // Tier costs start blank — you price each tier.
    td.line_items = items.map(it => ({
      id: it.id, name: it.name, unit: it.unit,
      quantity: it.quantity || 0,
      measure: it.measure || undefined,
      scope_note: it.scope_note || '',
      customer_visible: it.customer_visible !== false,
      tiers: {
        good:  { material_unit_cost:0, labor_unit_cost:0, description:it.description||'', notes:'' },
        better:{ material_unit_cost:0, labor_unit_cost:0, description:it.description||'', notes:'' },
        best:  { material_unit_cost:0, labor_unit_cost:0, description:it.description||'', notes:'' },
      },
    }));
  }
  td.mode = mode;
  setDirty(); renderTabBar(); renderTradeContent(); renderTotals();
}

function simpleSetField(trade, id, field, val) {
  const item = (S.trades[trade].line_items || []).find(it => it.id === id);
  if (!item) return;
  item[field] = val;
  setDirty();
}
function simpleUpdateTotals(trade) {
  (S.trades[trade].line_items || []).forEach(item => {
    const total = (parseFloat(item.quantity)||0) * (parseFloat(item.unit_price)||0);
    const cell  = document.querySelector(`.simple-line-total[data-strade="${trade}"][data-sid="${item.id}"]`);
    if (cell) cell.textContent = fmtCur(total);
  });
  const gt = document.getElementById(`simple-grand-${trade}`);
  if (gt) gt.textContent = fmtCur(tradeTotal(trade, S.selected_tier));
  renderTotals();
}
function simpleAddItem(trade) {
  if (!S.trades[trade].line_items) S.trades[trade].line_items = [];
  S.trades[trade].line_items.push({
    id: uid(), name:'', description:'', unit:'LF', quantity:0, unit_price:0,
    customer_visible: true
  });
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
}
function simpleDeleteItem(trade, id) {
  S.trades[trade].line_items = (S.trades[trade].line_items || []).filter(it => it.id !== id);
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
}

/* ── Insurance freeform tab ─────────────────────────────────────────── */

function _insSection(sec, sections) {
  const items = sec.items || [];
  // RCV (price) = ACV + Depreciation. Section subtotal is the sum of RCV.
  const secTotal = items.reduce((s, it) => s + (parseFloat(it.acv)||0) + (parseFloat(it.depreciation)||0), 0);
  const canDelete = sections.length > 1;

  const rows = items.map(item => {
    const acv = parseFloat(item.acv) || 0;
    const dep = parseFloat(item.depreciation) || 0;
    return `<tr>
      <td class="ins-name-cell">
        <input class="other-name-input" type="text" value="${esc(item.name||'')}" placeholder="Item name"
          onchange="insSetField('${sec.id}','${item.id}','name',this.value)">
      </td>
      <td class="ins-desc-cell">
        <input class="ins-desc-input" type="text" value="${esc(item.description||'')}" placeholder="Description"
          onchange="insSetField('${sec.id}','${item.id}','description',this.value)">
      </td>
      <td class="ins-acv-cell">
        <input class="ins-price-input" type="number" min="0" step="0.01"
          value="${acv||''}" placeholder="0.00"
          onchange="insSetField('${sec.id}','${item.id}','acv',parseFloat(this.value)||0);insUpdateTotals()">
      </td>
      <td class="ins-acv-cell">
        <input class="ins-price-input" type="number" min="0" step="0.01"
          value="${dep||''}" placeholder="0.00"
          onchange="insSetField('${sec.id}','${item.id}','depreciation',parseFloat(this.value)||0);insUpdateTotals()">
      </td>
      <td class="other-total-cell ins-line-total" data-ins-id="${item.id}">${fmtCur(acv+dep)}</td>
      <td><button class="li-del" onclick="insDeleteItem('${sec.id}','${item.id}')" title="Remove">×</button></td>
    </tr>`;
  }).join('');

  return `<div class="ins-section" data-sec-id="${sec.id}">
    <div class="ins-section-header">
      <input class="ins-section-name" type="text" value="${esc(sec.name||'')}"
        placeholder="Section name (e.g. Roof, Gutters, Interior…)"
        onchange="insRenameSection('${sec.id}',this.value)">
      ${canDelete ? `<button class="ins-del-section" onclick="insDeleteSection('${sec.id}')">Delete Section</button>` : ''}
    </div>
    ${items.length ? `
      <div class="other-table-wrap ins-section-table">
        <table class="other-table ins-table">
          <thead><tr>
            <th class="ins-th-name">Item Name</th>
            <th class="ins-th-desc">Description</th>
            <th class="other-th-price">ACV</th>
            <th class="other-th-price">Depreciation</th>
            <th class="other-th-price">RCV</th>
            <th style="width:32px"></th>
          </tr></thead>
          <tbody>${rows}</tbody>
          <tfoot><tr>
            <td colspan="4" style="text-align:right;padding-right:12px;font-weight:600">
              ${sec.name ? esc(sec.name)+' Subtotal' : 'Subtotal'}
            </td>
            <td class="other-total-cell ins-sec-total" data-sec-id="${sec.id}" style="font-weight:700">${fmtCur(secTotal)}</td>
            <td></td>
          </tr></tfoot>
        </table>
      </div>` : `<div class="ins-section-empty">No items yet.</div>`}
    <div class="ins-section-add-row">
      <button class="btn-add" onclick="insAddItem('${sec.id}')">+ Add Item</button>
    </div>
  </div>`;
}

function renderInsuranceFreeform() {
  const td = S.trades.insurance;
  // Migrate old flat line_items to single section on first render
  if (!td.sections) {
    td.sections = [{id:'sec_'+uid(), name:'', items: td.line_items || []}];
    delete td.line_items;
  }
  const sections = td.sections;
  const grandTot = insuranceTotal();
  const hasItems = sections.some(s => (s.items||[]).length > 0);

  return `
    <div class="ins-meta-bar">
      <div class="field-group" style="flex:1;min-width:180px">
        <label>Insurance Carrier</label>
        <input type="text" class="ins-meta-input" value="${esc(td.carrier||'')}"
          placeholder="e.g. State Farm"
          oninput="S.trades.insurance.carrier=this.value;setDirty()">
      </div>
      <div class="field-group" style="flex:1;min-width:180px">
        <label>Claim Number</label>
        <input type="text" class="ins-meta-input" value="${esc(td.claim_number||'')}"
          placeholder="e.g. CLM-2026-12345"
          oninput="S.trades.insurance.claim_number=this.value;setDirty()">
      </div>
    </div>
    <div class="other-desc" style="margin-bottom:14px">
      Add sections to match your Xactimate breakdown (e.g. Roof, Gutters, Interior). Each section has its own subtotal — ACV + Depreciation = RCV.
    </div>
    ${sections.map(sec => _insSection(sec, sections)).join('')}
    <div class="ins-section-actions">
      <button class="btn-secondary" onclick="insAddSection()">+ Add Section</button>
      ${hasItems ? `<div class="ins-grand-bar">
        <span>Insurance Claim Total</span>
        <strong id="ins-grand-total">${fmtCur(grandTot)}</strong>
      </div>` : ''}
    </div>
    <div class="ins-scope-wrap">
      <div class="panel-header" style="margin-top:18px">
        <h3>Scope of Work <span class="note-tag print">shown to customer</span></h3>
      </div>
      <p class="ins-scope-hint">Describe what work will be performed per the claim — this text prints on the estimate and appears on the customer sign page.</p>
      <textarea class="ins-scope-textarea"
        oninput="S.trades.insurance.scope_notes=this.value;setDirty()"
        placeholder="E.g. Complete tear-off and replacement of existing roofing system per insurance claim…"
      >${esc(td.scope_notes||'')}</textarea>
    </div>`;
}

function insSetField(secId, itemId, field, val) {
  const sec = (S.trades.insurance.sections || []).find(s => s.id === secId);
  if (!sec) return;
  const item = (sec.items || []).find(it => it.id === itemId);
  if (!item) return;
  item[field] = val;
  setDirty();
}
function insUpdateTotals() {
  (S.trades.insurance.sections || []).forEach(sec => {
    let secTotal = 0;
    (sec.items || []).forEach(item => {
      const total = (parseFloat(item.acv)||0) + (parseFloat(item.depreciation)||0);
      secTotal += total;
      const cell = document.querySelector(`.ins-line-total[data-ins-id="${item.id}"]`);
      if (cell) cell.textContent = fmtCur(total);
    });
    const secCell = document.querySelector(`.ins-sec-total[data-sec-id="${sec.id}"]`);
    if (secCell) secCell.textContent = fmtCur(secTotal);
  });
  const gt = document.getElementById('ins-grand-total');
  if (gt) gt.textContent = fmtCur(insuranceTotal());
  renderTotals();
}
function insAddItem(secId) {
  const sec = (S.trades.insurance.sections || []).find(s => s.id === secId);
  if (!sec) return;
  if (!sec.items) sec.items = [];
  sec.items.push({id:'li_'+uid(), name:'', description:'', acv:0, depreciation:0});
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
}
function insDeleteItem(secId, itemId) {
  const sec = (S.trades.insurance.sections || []).find(s => s.id === secId);
  if (!sec) return;
  sec.items = (sec.items || []).filter(it => it.id !== itemId);
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
}
function insAddSection() {
  if (!S.trades.insurance.sections) S.trades.insurance.sections = [];
  S.trades.insurance.sections.push({id:'sec_'+uid(), name:'', items:[]});
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
  setTimeout(() => {
    const secs = document.querySelectorAll('.ins-section');
    if (secs.length) secs[secs.length-1].scrollIntoView({behavior:'smooth', block:'center'});
  }, 60);
}
function insDeleteSection(secId) {
  const sections = S.trades.insurance.sections || [];
  const sec = sections.find(s => s.id === secId);
  if (!sec) return;
  if ((sec.items||[]).length > 0 && !confirm(`Delete section "${sec.name||'Untitled'}" and all its items?`)) return;
  S.trades.insurance.sections = sections.filter(s => s.id !== secId);
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
}
function insRenameSection(secId, name) {
  const sec = (S.trades.insurance.sections || []).find(s => s.id === secId);
  if (!sec) return;
  sec.name = name;
  setDirty();
}

function renderColorSection(trade) {
  const fields = TRADE_COLOR_FIELDS[trade] || []; if (!fields.length) return '';
  const colors = S.trades[trade].colors || {};
  return `<div class="color-section">
    <span class="color-section-label">Color / Product</span>
    <div class="color-fields">
      ${fields.map(f => `<div class="color-field">
        <label>${f.label}</label>
        <input type="text" value="${esc(colors[f.key]||'')}" placeholder="${f.label}…"
          onchange="setTradeColor('${trade}','${f.key}',this.value)">
      </div>`).join('')}
    </div>
  </div>`;
}
function setTradeColor(trade, key, v) {
  if (!S.trades[trade].colors) S.trades[trade].colors = {};
  S.trades[trade].colors[key] = v; setDirty();
}

/* Price-book lookup: datalist of known items for a trade + smart fill */
function pbDatalist(trade) {
  const items = (templates && templates[trade]) || [];
  if (!items.length) return '';
  return `<datalist id="pb-list-${trade}">
    ${items.map(t => `<option value="${esc(t.name)}">`).join('')}
  </datalist>`;
}
function pbFind(trade, name) {
  const items = (templates && templates[trade]) || [];
  const n = (name || '').trim().toLowerCase();
  return items.find(t => (t.name || '').trim().toLowerCase() === n) || null;
}
function liSetNameSmart(trade, id, v) {
  const item = findItem(trade, id);
  if (!item) return;
  item.name = v;
  const t = pbFind(trade, v);
  if (t) {
    // Pulled from price book — fill unit, measurement link, costs & descriptions
    item.unit = t.unit || item.unit;
    if (!item.measure && t.measure) item.measure = t.measure;
    const cost = t.cost !== undefined ? parseFloat(t.cost) || 0 : 0;
    if (item.tiers) {
      ['good','better','best'].forEach(tier => {
        if (!item.tiers[tier]) item.tiers[tier] = {material_unit_cost:0, labor_unit_cost:0, description:'', notes:''};
        const tt = item.tiers[tier];
        if (!parseFloat(tt.material_unit_cost) && !parseFloat(tt.labor_unit_cost)) tt.material_unit_cost = cost;
        if (!tt.description) tt.description = t['desc_'+tier] || '';
        if (!tt.notes)       tt.notes       = t['notes_'+tier] || '';
      });
    } else if (item.description === '' && t.desc_better) {
      item.description = t.desc_better;
    }
    const q = measuredQty(item);
    if (q !== null) item.quantity = q;
  }
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
  renderTotals();
}

function renderGBBGrid(trade) {
  const items = S.trades[trade].line_items;
  const tier  = S.selected_tier;
  const grid  = TIERS.map(t => `
    <div class="tier-column col-${t} ${t===tier?'selected-tier':''}">
      <div class="tier-col-header">
        ${TIER_LABELS[t]} <span class="tier-col-total">${fmtCur(tradeTotal(trade,t))}</span>
      </div>
      <div class="tier-items">
        ${items.length ? items.map((item,idx)=>renderLiCard(trade,t,item,idx)).join('')
          : '<div class="empty-items">Load Defaults or + Add Line Item</div>'}
      </div>
    </div>`).join('');
  return `
    <div class="gbb-grid">${grid}</div>
    ${pbDatalist(trade)}
    <div class="add-row-bar">
      <button class="btn-add" onclick="addLineItem('${trade}')">+ Add Line Item</button>
      <span class="add-row-hint">Tip: type in the item name to pull from your price book, or free-type anything</span>
    </div>
    <div class="subtotal-bar">
      ${TIERS.map(t=>`<div class="tier-subtotal ${t===tier?'sel-'+t:''}">
        ${TIER_LABELS[t]}<strong>${fmtCur(tradeTotal(trade,t))}</strong>
      </div>`).join('')}
    </div>`;
}

function renderLiCard(trade, tier, item, idx) {
  const t   = item.tiers[tier] || {material_unit_cost:0,labor_unit_cost:0,description:'',notes:''};
  const tot = lineTotal(item.quantity, t.material_unit_cost, t.labor_unit_cost, trade);
  const isB = tier === 'better';
  const UNITS = ['SQ','LF','EA','HR','LS','SF','BD'];
  const isVisible = item.customer_visible !== false;
  const included  = t.included !== false;
  return `<div class="li-card${!isVisible?' li-card-hidden':''}${!included?' li-card-excluded':''}">
    <div class="li-tier-include-row">
      <label class="li-tier-include${included?'':' off'}" title="Include this item in the ${esc(TIER_LABELS[tier])} package">
        <input type="checkbox" ${included?'checked':''}
          onchange="liSetIncluded('${trade}','${item.id}','${tier}',this.checked)">
        ${included?`In ${esc(TIER_LABELS[tier])}`:`Excluded from ${esc(TIER_LABELS[tier])}`}
      </label>
    </div>
    <div class="li-row li-name-row">
      ${isB
        ? `<input class="li-name-input" type="text" value="${esc(item.name)}" list="pb-list-${trade}"
             onchange="liSetNameSmart('${trade}','${item.id}',this.value)" placeholder="Type to search price book…">`
        : `<span class="li-name-static">${esc(item.name)}</span>
           ${!isVisible?'<span class="li-hidden-badge">Hidden</span>':''}`}
      ${isB?`<button class="li-del" onclick="liDelete('${trade}','${item.id}')" title="Remove">×</button>`:''}
    </div>
    ${isB?`<div class="li-vis-row"><label class="li-vis-toggle${!isVisible?' vis-off':''}">
      <input type="checkbox" ${isVisible?'checked':''}
        onchange="liSetVisible('${trade}','${item.id}',this.checked)">
      ${isVisible?'👁 Customer sees this':'🚫 Hidden from customer'}
    </label></div>`:''}
    <div class="li-row">
      <input class="li-desc-input" type="text" value="${esc(t.description||'')}"
        onchange="liSetTier('${trade}','${item.id}','${tier}','description',this.value)"
        placeholder="${tier==='good'?'e.g. 3-Tab':tier==='better'?'e.g. Architectural':'e.g. Designer'}">
    </div>
    <div class="li-row">
      ${isB
        ? `<input class="li-qty-input" type="number" value="${item.quantity||''}" min="0" step="0.5"
             onchange="liSetQty('${trade}','${item.id}',this.value)" placeholder="Qty" title="Shared across all tiers">
           <select class="li-unit-select" onchange="liSetUnit('${trade}','${item.id}',this.value)">
             ${UNITS.map(u=>`<option ${item.unit===u?'selected':''}>${u}</option>`).join('')}
           </select>`
        : `<span class="li-qty-static">${item.quantity||0} ${item.unit}</span>`}
    </div>
    <div class="li-row">
      <div class="li-cost-wrap"><label>Mat $</label>
        <input class="li-cost-input" type="number" min="0" step="0.01"
          value="${t.material_unit_cost||''}" placeholder="0.00"
          onchange="liSetTier('${trade}','${item.id}','${tier}','material_unit_cost',parseFloat(this.value)||0)">
      </div>
      <div class="li-cost-wrap"><label>Labor $</label>
        <input class="li-cost-input" type="number" min="0" step="0.01"
          value="${t.labor_unit_cost||''}" placeholder="0.00"
          onchange="liSetTier('${trade}','${item.id}','${tier}','labor_unit_cost',parseFloat(this.value)||0)">
      </div>
    </div>
    <div class="li-notes-wrap">
      <button class="li-notes-toggle" onclick="toggleNotes(this)">▸ Description / Marketing Notes</button>
      <textarea class="li-notes-input" style="display:none"
        placeholder="Describe what makes this item great for the customer…"
        onchange="liSetTier('${trade}','${item.id}','${tier}','notes',this.value)">${esc(t.notes||'')}</textarea>
    </div>
    <div class="li-row li-total-row">
      <span class="li-total-value">${included?fmtCur(tot):'<span class="li-excluded-note">Not in this package</span>'}</span>
    </div>
  </div>`;
}

function toggleNotes(btn) {
  const ta = btn.nextElementSibling;
  const open = ta.style.display !== 'none';
  ta.style.display = open ? 'none' : 'block';
  btn.textContent = (open ? '▸' : '▾') + ' Description / Marketing Notes';
}

/* ── Page 5: Contract ───────────────────────────────────────────────── */

function renderContractPage() {
  // Notes
  setTA('notes-internal', S.notes_internal);
  setTA('notes-customer', S.notes_customer);
  // Contract
  document.getElementById('contract-section').innerHTML =
    `<div class="panel-header">
      <h3>Contract Terms</h3>
      <label class="checkbox-label" style="font-size:11px">
        <input type="checkbox" ${S.print_contract!==false?'checked':''}
          onchange="S.print_contract=this.checked;setDirty()"> Print with estimate
      </label>
    </div>
    <textarea id="contract-textarea" rows="14"
      onchange="S.contract_text=this.value;setDirty()">${esc(S.contract_text||(S.estimate_type==='insurance'?DEFAULT_INSURANCE_CONTRACT:DEFAULT_CONTRACT))}</textarea>
    ${renderSigningRequirements()}`;
}

function renderSigningRequirements() {
  const ss = S.shingle_selection || {enabled:true, options:DEFAULT_SHINGLE_COLORS.slice(), chosen:''};
  const initials = S.contract_initials || [];
  const optionsText = (ss.options || []).join(', ');

  const initialRows = initials.map((it, idx) => `
    <div class="sr-initial-row">
      <span class="sr-initial-num">${idx + 1}</span>
      <input type="text" class="sr-initial-input" value="${esc(it.text)}"
        placeholder="Statement the customer must initial…"
        onchange="setInitialText('${it.id}', this.value)">
      <button class="sr-initial-del" onclick="deleteInitial('${it.id}')" title="Remove">×</button>
    </div>`).join('');

  return `
  <div class="signing-req">
    <div class="panel-header" style="margin-top:22px">
      <h3>✍️ Signing Requirements <span class="note-tag print">what the customer does at signing</span></h3>
    </div>

    <div class="sr-block">
      <label class="sr-toggle">
        <input type="checkbox" ${ss.enabled !== false ? 'checked' : ''}
          onchange="setShingleEnabled(this.checked)">
        <span>Ask the customer to confirm a <strong>shingle color</strong> at signing</span>
      </label>
      <div class="sr-shingle-body" style="${ss.enabled !== false ? '' : 'display:none'}">
        <div class="field-group">
          <label>Color already chosen? <span class="sr-hint">leave blank to let the customer pick</span></label>
          <input type="text" list="shingle-color-list" class="sr-chosen-input"
            value="${esc(ss.chosen || '')}" placeholder="e.g. Weathered Wood — or leave blank"
            onchange="setShingleChosen(this.value)">
          <datalist id="shingle-color-list">
            ${(ss.options || []).map(o => `<option value="${esc(o)}">`).join('')}
          </datalist>
        </div>
        <div class="field-group">
          <label>Color options offered to the customer <span class="sr-hint">comma-separated</span></label>
          <textarea class="sr-options-input" rows="2"
            onchange="setShingleOptions(this.value)"
            placeholder="Charcoal, Weathered Wood, Driftwood…">${esc(optionsText)}</textarea>
        </div>
      </div>
    </div>

    <div class="sr-block">
      <div class="sr-block-title">Items the customer must <strong>initial</strong></div>
      <p class="sr-hint" style="margin:0 0 8px">Each line gets its own initial box on the sign page, on top of the full signature.</p>
      <div class="sr-initials">${initialRows || '<div class="sr-empty">No initial items — the customer will just sign &amp; agree.</div>'}</div>
      <button class="btn-add" onclick="addInitial()">+ Add initial item</button>
    </div>
  </div>`;
}

function setShingleEnabled(v) {
  if (!S.shingle_selection) S.shingle_selection = {options:DEFAULT_SHINGLE_COLORS.slice(), chosen:''};
  S.shingle_selection.enabled = v; setDirty();
  renderContractPage();
}
function setShingleChosen(v) {
  if (!S.shingle_selection) S.shingle_selection = {enabled:true, options:DEFAULT_SHINGLE_COLORS.slice()};
  S.shingle_selection.chosen = v.trim();
  // Keep roofing's color field in sync so it shows on prints
  if (S.trades.roofing) { S.trades.roofing.colors = S.trades.roofing.colors || {}; S.trades.roofing.colors.shingle_color = v.trim(); }
  setDirty();
}
function setShingleOptions(v) {
  if (!S.shingle_selection) S.shingle_selection = {enabled:true, chosen:''};
  const opts = v.split(',').map(s => s.trim()).filter(Boolean);
  S.shingle_selection.options = opts.length ? opts : DEFAULT_SHINGLE_COLORS.slice();
  setDirty();
}
function addInitial() {
  if (!Array.isArray(S.contract_initials)) S.contract_initials = [];
  S.contract_initials.push({ id:'ini_'+uid(), text:'' });
  setDirty(); renderContractPage();
}
function setInitialText(id, v) {
  const it = (S.contract_initials || []).find(x => x.id === id);
  if (it) { it.text = v; setDirty(); }
}
function deleteInitial(id) {
  S.contract_initials = (S.contract_initials || []).filter(x => x.id !== id);
  setDirty(); renderContractPage();
}

function renderPhotosPage() {
  const grid = document.getElementById('photo-grid'); if (!grid) return;
  grid.innerHTML = S.photos.map(p => {
    const hasAnns = p.annotations && p.annotations.length > 0;
    return `
    <div class="photo-thumb photo-report-thumb ${p.id===S.cover_photo_id?'is-cover':''}">
      <div class="photo-img-wrap">
        ${hasAnns
          ? `<canvas class="photo-ann-canvas" id="ann-ph-${p.id}" data-src="/uploads/${esc(p.filename)}" data-id="${p.id}"></canvas>`
          : `<img src="/uploads/${esc(p.filename)}" alt="${esc(p.caption)}">`}
      </div>
      <div class="photo-report-controls">
        <input class="photo-caption" type="text" value="${esc(p.caption)}"
          placeholder="Caption / note…" onchange="photoCaption('${p.id}',this.value)">
        <div class="photo-report-btns">
          <button class="btn-annotate ${hasAnns?'has-anns':''}" onclick="openAnnotationModal('${p.id}')" title="Add/edit annotations">
            ✏ ${hasAnns ? 'Edit Annotations' : 'Annotate'}
          </button>
          ${p.id===S.cover_photo_id
            ? '<span class="cover-label-badge">COVER</span>'
            : `<button class="set-cover-btn" onclick="setCoverPhoto('${p.id}')">Cover</button>`}
          <label class="photo-print-toggle" title="Include in printed estimate">
            <input type="checkbox" ${p.show_in_estimate?'checked':''}
              onchange="photoToggle('${p.id}',this.checked)"> Print
          </label>
          <button class="photo-del" onclick="photoDelete('${p.id}')" title="Delete">×</button>
        </div>
      </div>
    </div>`;
  }).join('');

  // Render annotation canvases
  S.photos.forEach(p => {
    if (p.annotations && p.annotations.length > 0) {
      const canvas = document.getElementById('ann-ph-' + p.id);
      if (canvas) drawAnnotatedPhoto(canvas, '/uploads/' + p.filename, p.annotations);
    }
  });

  renderAttachments();
}

/* legacy alias so any other callers still work */
function renderPhotos() { renderPhotosPage(); }

/* Draw photo + annotations onto a canvas element */
function drawAnnotatedPhoto(canvas, src, annotations) {
  const img = new Image();
  img.onload = () => {
    const W = canvas.parentElement.clientWidth || 280;
    const H = Math.round(W * img.naturalHeight / img.naturalWidth);
    canvas.width  = W;
    canvas.height = H;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0, W, H);
    annotations.forEach(ann => drawAnn(ctx, ann, W, H, W / 300));
  };
  img.src = src;
}

/* ── Annotation editor ──────────────────────────────────────────────── */

const annState = {
  photoId: null, img: null, canvas: null, ctx: null,
  tool: 'oval', color: '#ef4444', sw: 3,
  annotations: [], drawing: false,
  sx: 0, sy: 0, preview: null, history: [],
  textPending: null, // {x, y} waiting for text input
};

function openAnnotationModal(photoId) {
  const photo = S.photos.find(p => p.id === photoId);
  if (!photo) return;
  annState.photoId    = photoId;
  annState.annotations = (photo.annotations || []).map(a => Object.assign({}, a));
  annState.history    = [];
  annState.drawing    = false;
  annState.preview    = null;
  annState.textPending = null;

  document.getElementById('annotation-modal').classList.remove('hidden');
  document.getElementById('ann-text-input-wrap').style.display = 'none';

  setAnnTool(annState.tool);
  setAnnColor(annState.color);

  const canvas = document.getElementById('ann-canvas-edit');
  annState.canvas = canvas;
  annState.ctx    = canvas.getContext('2d');

  const img = new Image();
  img.onload = () => {
    annState.img = img;
    const wrap   = document.getElementById('ann-canvas-wrap');
    const maxW   = (wrap.clientWidth  || 800) - 4;
    const maxH   = window.innerHeight * 0.62;
    const scale  = Math.min(maxW / img.naturalWidth, maxH / img.naturalHeight, 1);
    canvas.width  = Math.round(img.naturalWidth  * scale);
    canvas.height = Math.round(img.naturalHeight * scale);
    bindAnnCanvasEvents(canvas); // binds events on fresh clone, updates annState.canvas
    annState.canvas = document.getElementById('ann-canvas-edit'); // re-grab after clone
    annState.ctx    = annState.canvas.getContext('2d');
    redrawAnnotationCanvas();
  };
  img.src = '/uploads/' + photo.filename;
}

function closeAnnotationModal() {
  document.getElementById('annotation-modal').classList.add('hidden');
  document.getElementById('ann-text-input-wrap').style.display = 'none';
  annState.photoId = null;
}
function maybeCloseAnnModal(e) {
  if (e.target === document.getElementById('annotation-modal')) closeAnnotationModal();
}

function setAnnTool(t) {
  annState.tool = t;
  document.querySelectorAll('.ann-tool-btn').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('ann-tool-' + t);
  if (btn) btn.classList.add('active');
  const canvas = annState.canvas;
  if (canvas) canvas.style.cursor = t === 'text' ? 'text' : 'crosshair';
}
function setAnnColor(c) {
  annState.color = c;
  document.querySelectorAll('.ann-color-btn').forEach(b => b.classList.remove('active'));
  const btn = document.querySelector(`.ann-color-btn[data-color="${c}"]`);
  if (btn) btn.classList.add('active');
}
function setAnnSw(w) {
  annState.sw = w;
  document.querySelectorAll('.ann-sw-btn').forEach(b => b.classList.remove('active'));
  event?.target?.classList.add('active');
}

function redrawAnnotationCanvas() {
  const { canvas, ctx, img, annotations, preview } = annState;
  if (!canvas || !ctx || !img) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  annotations.forEach(ann => drawAnn(ctx, ann, canvas.width, canvas.height));
  if (preview) drawAnn(ctx, preview, canvas.width, canvas.height);
}

function drawAnn(ctx, ann, W, H, scale=1) {
  const c  = ann.color || '#ef4444';
  const sw = (ann.sw || 3) * scale;
  ctx.save();
  ctx.strokeStyle = c;
  ctx.fillStyle   = c;
  ctx.lineWidth   = sw;
  ctx.lineCap     = 'round';
  ctx.lineJoin    = 'round';

  if (ann.type === 'oval') {
    const x1 = ann.x1/100*W, y1 = ann.y1/100*H;
    const x2 = ann.x2/100*W, y2 = ann.y2/100*H;
    const cx  = (x1+x2)/2, cy = (y1+y2)/2;
    const rx  = Math.abs(x2-x1)/2 || 1, ry = Math.abs(y2-y1)/2 || 1;
    ctx.beginPath();
    ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI*2);
    ctx.stroke();

  } else if (ann.type === 'arrow') {
    const x1 = ann.x1/100*W, y1 = ann.y1/100*H;
    const x2 = ann.x2/100*W, y2 = ann.y2/100*H;
    const angle = Math.atan2(y2-y1, x2-x1);
    const hl = sw * 6;
    ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x2, y2);
    ctx.lineTo(x2 - hl*Math.cos(angle-0.45), y2 - hl*Math.sin(angle-0.45));
    ctx.lineTo(x2 - hl*Math.cos(angle+0.45), y2 - hl*Math.sin(angle+0.45));
    ctx.closePath(); ctx.fill();

  } else if (ann.type === 'text') {
    const x  = ann.x/100*W, y = ann.y/100*H;
    const fs = Math.max(13, (ann.sw || 3) * 5) * scale;
    ctx.font = `bold ${fs}px 'Segoe UI', sans-serif`;
    ctx.textBaseline = 'top';
    // Readable outline
    ctx.strokeStyle = (c === '#ffffff') ? '#222' : '#fff';
    ctx.lineWidth   = Math.max(1, (ann.sw || 3)) * 2 * scale;
    ctx.strokeText(ann.text || '', x, y);
    ctx.fillStyle   = c;
    ctx.fillText(ann.text || '', x, y);
  }
  ctx.restore();
}

function bindAnnCanvasEvents(canvas) {
  // Replace canvas node to clear all previous listeners
  const fresh = canvas.cloneNode(true);
  canvas.parentNode.replaceChild(fresh, canvas);
  annState.canvas = fresh;
  annState.ctx = fresh.getContext('2d');
  canvas = fresh;

  function pct(e) {
    const r = canvas.getBoundingClientRect();
    const src = e.touches ? e.touches[0] : e;
    return {
      x: Math.max(0, Math.min(100, (src.clientX - r.left) / r.width  * 100)),
      y: Math.max(0, Math.min(100, (src.clientY - r.top)  / r.height * 100)),
    };
  }

  function onStart(e) {
    if (annState.tool === 'text') {
      const {x, y} = pct(e);
      showAnnTextInput(x, y, canvas);
      return;
    }
    e.preventDefault();
    const {x, y} = pct(e);
    annState.sx = x; annState.sy = y;
    annState.drawing = true;
  }
  function onMove(e) {
    if (!annState.drawing) return;
    e.preventDefault();
    const {x, y} = pct(e);
    annState.preview = {
      id: '__preview', type: annState.tool, color: annState.color, sw: annState.sw,
      x1: annState.sx, y1: annState.sy, x2: x, y2: y
    };
    redrawAnnotationCanvas();
  }
  function onEnd(e) {
    if (!annState.drawing) return;
    annState.drawing = false;
    const p = annState.preview;
    if (!p) return;
    const dist = Math.hypot(
      (p.x2-p.x1)/100 * canvas.width,
      (p.y2-p.y1)/100 * canvas.height
    );
    if (dist > 4) {
      annState.history.push(annState.annotations.map(a => Object.assign({},a)));
      annState.annotations.push(Object.assign({}, p, {id: 'ann_' + Date.now().toString(36)}));
    }
    annState.preview = null;
    redrawAnnotationCanvas();
  }

  canvas.addEventListener('mousedown',  onStart);
  canvas.addEventListener('mousemove',  onMove);
  canvas.addEventListener('mouseup',    onEnd);
  canvas.addEventListener('mouseleave', onEnd);
  canvas.addEventListener('touchstart', onStart, {passive:false});
  canvas.addEventListener('touchmove',  onMove,  {passive:false});
  canvas.addEventListener('touchend',   onEnd);
}

function showAnnTextInput(x, y, canvas) {
  const wrap   = document.getElementById('ann-text-input-wrap');
  const input  = document.getElementById('ann-text-input');
  const cRect  = canvas.getBoundingClientRect();
  const wRect  = document.getElementById('ann-canvas-wrap').getBoundingClientRect();
  const px = (cRect.left - wRect.left) + x/100 * canvas.getBoundingClientRect().width;
  const py = (cRect.top  - wRect.top)  + y/100 * canvas.getBoundingClientRect().height;
  wrap.style.left    = px + 'px';
  wrap.style.top     = py + 'px';
  wrap.style.display = 'block';
  input.value = '';
  input.focus();
  annState.textPending = {x, y};

  input.onkeydown = function(ev) {
    if (ev.key === 'Enter' && this.value.trim()) {
      annState.history.push(annState.annotations.map(a => Object.assign({},a)));
      annState.annotations.push({
        id: 'ann_' + Date.now().toString(36),
        type: 'text', color: annState.color, sw: annState.sw,
        x: annState.textPending.x, y: annState.textPending.y,
        text: this.value.trim()
      });
      wrap.style.display = 'none';
      annState.textPending = null;
      redrawAnnotationCanvas();
    } else if (ev.key === 'Escape') {
      wrap.style.display = 'none';
      annState.textPending = null;
    }
  };
}

function undoAnnotation() {
  if (!annState.history.length) return;
  annState.annotations = annState.history.pop();
  annState.preview = null;
  redrawAnnotationCanvas();
}
function clearAnnotations() {
  if (!annState.annotations.length) return;
  annState.history.push(annState.annotations.map(a => Object.assign({},a)));
  annState.annotations = [];
  redrawAnnotationCanvas();
}

function saveAnnotations() {
  const photo = S.photos.find(p => p.id === annState.photoId);
  if (!photo) return;
  photo.annotations = annState.annotations.map(a => Object.assign({},a));
  delete _printPhotoCache[photo.id]; // invalidate stale cached version
  setDirty();
  closeAnnotationModal();
  renderPhotosPage();
  renderCoverPage(); // refresh cover strip
  warmPrintPhotos();
}

/* Get dataURL for a photo (annotated version if available) for print */
function getPhotoDataUrl(photo) {
  const canvas = document.getElementById('ann-ph-' + photo.id);
  if (canvas && canvas.width > 0) return canvas.toDataURL('image/jpeg', 0.9);
  return '/uploads/' + photo.filename;
}

function setTA(id, v) { const el = document.getElementById(id); if (el && el.value !== (v||'')) el.value = v||''; }

/* ── Line item mutations ────────────────────────────────────────────── */

function findItem(trade, id) { return S.trades[trade].line_items.find(i=>i.id===id); }

function liSetName(trade, id, v) { const i=findItem(trade,id); if(!i)return; i.name=v; setDirty(); }
function liSetQty(trade, id, v) {
  const i=findItem(trade,id); if(!i)return;
  i.quantity=parseFloat(v)||0;
  setDirty(); rerender();
}
function liSetUnit(trade, id, v) { const i=findItem(trade,id); if(!i)return; i.unit=v; setDirty(); }
function liSetTier(trade, id, tier, field, v) {
  const i=findItem(trade,id); if(!i)return;
  if(!i.tiers[tier]) i.tiers[tier]={material_unit_cost:0,labor_unit_cost:0,description:'',notes:''};
  i.tiers[tier][field]=v;
  setDirty(); rerender();
  if(activePage==='pricing'){renderTradeContent();}
}
function liDelete(trade, id) {
  S.trades[trade].line_items=S.trades[trade].line_items.filter(i=>i.id!==id);
  setDirty(); rerender();
  if(activePage==='pricing'){renderTradeContent();}
  if(activePage==='scope'){renderScopePage();}
}
function liSetVisible(trade, id, val) {
  const item = findItem(trade, id);
  if (!item) return;
  item.customer_visible = val;
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
}
// Include/exclude a line item from a single package tier. Quantity stays shared;
// only this tier's pricing, totals, customer view and PDF drop the item.
function liSetIncluded(trade, id, tier, on) {
  const item = findItem(trade, id);
  if (!item || !item.tiers || !item.tiers[tier]) return;
  item.tiers[tier].included = on;
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
  renderTotals();
}
function addLineItem(trade) {
  S.trades[trade].line_items.push({
    id:uid(), name:'', unit:'EA', quantity:0, scope_note:'',
    customer_visible: true,
    tiers:{
      good:  {material_unit_cost:0,labor_unit_cost:0,description:'',notes:''},
      better:{material_unit_cost:0,labor_unit_cost:0,description:'',notes:''},
      best:  {material_unit_cost:0,labor_unit_cost:0,description:'',notes:''},
    }
  });
  setDirty(); rerender();
  if(activePage==='pricing'){renderTradeContent();}
}
function toggleTrade(trade, enabled) {
  S.trades[trade].enabled=enabled;
  // Auto-build priced defaults when enabling an empty trade, so measurements
  // flow straight through to a priced estimate with no extra clicks.
  if (enabled && templates && (!S.trades[trade].line_items || S.trades[trade].line_items.length === 0)) {
    S.trades[trade].line_items = buildTradeDefaults(trade);
    applyMeasurements();
  }
  setDirty(); rerender();
  if(activePage==='pricing'){renderTabBar();renderTradeContent();}
  if(activePage==='scope'){renderScopePage();}
}
// Build a trade's line items from the price book / templates (synchronous —
// relies on the global `templates` cache that loads at boot). Pulls per-tier
// products + costs (GBB) or the price (Simple) so the estimate is priced the
// moment items are created.
function buildTradeDefaults(trade) {
  const tpl = (templates && templates[trade]) || [];
  const effectiveMode = S.trades[trade].mode || (trade === 'gutters' ? 'simple' : 'gbb');
  if (effectiveMode === 'simple') {
    return tpl.map(t => {
      const baseCost = t.cost !== undefined ? parseFloat(t.cost)||0 : 0;
      const price = t.cost_better !== undefined ? parseFloat(t.cost_better)||0 : baseCost;
      return {
        id:uid(), name:t.name, unit:t.unit, quantity:0,
        description:(t.product_better || t.desc_better || ''),
        unit_price:price,
        measure: t.measure || undefined,
        formula: t.formula || undefined,
        customer_visible: t.customer_visible !== false,
      };
    });
  }
  return tpl.map(t => {
    const baseCost = t.cost !== undefined ? parseFloat(t.cost)||0 : 0;
    const costGood   = t.cost_good   !== undefined ? parseFloat(t.cost_good)||0   : baseCost;
    const costBetter = t.cost_better !== undefined ? parseFloat(t.cost_better)||0 : baseCost;
    const costBest   = t.cost_best   !== undefined ? parseFloat(t.cost_best)||0   : baseCost;
    const descGood   = t.product_good   ? t.product_good   : (t.desc_good   || '');
    const descBetter = t.product_better ? t.product_better : (t.desc_better || '');
    const descBest   = t.product_best   ? t.product_best   : (t.desc_best   || '');
    return {
      id:uid(), name:t.name, unit:t.unit, quantity:0, scope_note:'',
      measure: t.measure || undefined,
      formula: t.formula || undefined,
      customer_visible: t.customer_visible !== false,
      tiers:{
        good:  {material_unit_cost:costGood,   labor_unit_cost:0, description:descGood,   notes:t.notes_good||'',   included: t.in_good   !== false},
        better:{material_unit_cost:costBetter, labor_unit_cost:0, description:descBetter, notes:t.notes_better||'', included: t.in_better !== false},
        best:  {material_unit_cost:costBest,   labor_unit_cost:0, description:descBest,   notes:t.notes_best||'',   included: t.in_best   !== false},
      }
    };
  });
}
async function loadDefaults(trade) {
  if(S.trades[trade].line_items.length>0){
    if(!confirm(`Replace existing ${TRADE_LABELS[trade]} items with defaults?`))return;
  }
  if(!templates){
    try{const r=await fetch('/api/templates');templates=await r.json();}
    catch{alert('Failed to load templates');return;}
  }
  S.trades[trade].enabled=true;
  S.trades[trade].line_items = buildTradeDefaults(trade);
  // Fill quantities from any measurements already entered
  applyMeasurements();
  setDirty(); rerender();
  if(activePage==='scope'){renderScopePage();}
  if(activePage==='pricing'){renderTabBar();renderTradeContent();}
}
function clearTrade(trade) {
  if(!confirm(`Clear all ${TRADE_LABELS[trade]} line items?`))return;
  S.trades[trade].line_items=[];
  setDirty(); rerender();
  if(activePage==='pricing'){renderTradeContent();}
}

/* ── Sidebar events ────────────────────────────────────────────────── */

function bindSidebarEvents() {
  bind('cust-name',       v=>S.customer.name=v,           'change', ()=>{ rerender(); renderCoverPage(); });
  bind('cust-phone',      v=>S.customer.phone=v);
  bind('cust-email',      v=>S.customer.email=v);
  bind('cust-street',     v=>S.customer.address.street=v, 'change', ()=>renderCoverPage());
  bind('cust-city',       v=>S.customer.address.city=v,   'change', ()=>renderCoverPage());
  bind('cust-state',      v=>S.customer.address.state=v,  'change', ()=>renderCoverPage());
  bind('cust-zip',        v=>S.customer.address.zip=v,    'change', ()=>renderCoverPage());
  bind('project-address', v=>S.project_address=v);
  bind('estimate-date',   v=>S.estimate_date=v,           'change', ()=>renderCoverPage());
  bind('valid-until',     v=>S.valid_until=v);
  bind('salesperson',     v=>S.salesperson=v,             'change', ()=>renderCoverPage());
  bind('est-status',      v=>S.status=v);
  bind('notes-internal',  v=>S.notes_internal=v, 'input');
  bind('notes-customer',  v=>S.notes_customer=v, 'input');
  document.getElementById('global-rate-slider').addEventListener('input', e=>syncRate(e.target.value));
  document.getElementById('global-rate-input').addEventListener('input', e=>syncRate(e.target.value));
  document.getElementById('crm-search').addEventListener('input',
    debounce(e=>crmSearch(e.target.value.trim()),300));
  const dz=document.getElementById('photo-drop-zone');
  const pi=document.getElementById('photo-input');
  dz.addEventListener('click',     ()=>pi.click());
  dz.addEventListener('dragover',  e=>{e.preventDefault();dz.classList.add('drag-over');});
  dz.addEventListener('dragleave', ()=>dz.classList.remove('drag-over'));
  dz.addEventListener('drop', e=>{e.preventDefault();dz.classList.remove('drag-over');uploadPhotos(e.dataTransfer.files);});
  pi.addEventListener('change', ()=>uploadPhotos(pi.files));
  // Cover-photo direct upload
  const ci = document.getElementById('cover-photo-input');
  if (ci) ci.addEventListener('change', () => { uploadAsCoverPhoto(ci.files); });
}

function bind(id, setter, event='change', extra=null) {
  const el=document.getElementById(id); if(!el)return;
  el.addEventListener(event, e=>{setter(e.target.value);setDirty();if(extra)extra();});
}
function syncRate(v) {
  const rate=parseFloat(v)||0; S.pricing.global_rate=rate;
  document.getElementById('global-rate-slider').value=rate;
  document.getElementById('global-rate-input').value=rate;
  document.getElementById('global-rate-display').textContent=rate+'%';
  setDirty(); rerender();
  if(activePage==='pricing')renderTradeContent();
}
function setPricingMode(mode) { S.pricing.mode=mode; setDirty(); renderPricingModeUI(); rerender(); if(activePage==='pricing')renderTradeContent(); }
function setTier(tier) { S.selected_tier=tier; setDirty(); renderTierButtons(); rerender(); if(activePage==='pricing')renderTradeContent(); }
function setTradeOverride(trade,v) { S.pricing.per_trade_overrides[trade]=v===''?null:parseFloat(v); setDirty(); rerender(); if(activePage==='pricing')renderTradeContent(); }

/* ── CRM ───────────────────────────────────────────────────────────── */

async function crmSearch(q) {
  if(!q||q.length<2){closeCrm();return;}
  try{ const r=await fetch(`/api/crm/jobs?q=${encodeURIComponent(q)}`); showCrmResults(await r.json()); }
  catch{ closeCrm(); }
}
function showCrmResults(list) {
  const dd=document.getElementById('crm-dropdown');
  dd.innerHTML=list.length
    ? list.map(p=>`<div class="crm-result" data-id="${esc(p.id)}">
        <strong>${esc(p.client_name||p.name)}</strong>
        <small>${[p.job_number,p.address].filter(Boolean).join(' · ')}</small>
      </div>`).join('')
    : '<div class="crm-no-results">No jobs found</div>';
  dd.querySelectorAll('.crm-result').forEach(el=>
    el.addEventListener('click',()=>selectJob(list.find(p=>p.id===el.dataset.id))));
  dd.classList.remove('hidden');
}
function parseCrmAddress(addr) {
  // CRM job addresses look like "2905 Roanoke Ln, Tyler, TX, 75701"
  const out={street:'',city:'',state:'',zip:''};
  if(!addr)return out;
  const parts=addr.split(',').map(s=>s.trim()).filter(Boolean);
  if(parts.length>=1)out.street=parts[0];
  if(parts.length>=2)out.city=parts[1];
  if(parts.length>=3){
    const m=parts[2].match(/^([A-Za-z]{2})\s*(\d{5}(-\d{4})?)?$/);
    if(m){out.state=m[1].toUpperCase();if(m[2])out.zip=m[2];}
    else out.state=parts[2];
  }
  if(parts.length>=4&&!out.zip){
    const z=parts[3].match(/\d{5}(-\d{4})?/);if(z)out.zip=z[0];
  }
  return out;
}
function selectJob(p) {
  if(!p)return;
  const a=parseCrmAddress(p.address);
  S.customer={crm_contact_id:null,crm_project_id:p.id,crm_job_number:p.job_number||'',
    name:p.client_name||p.name||'',phone:p.client_phone||'',email:p.client_email||'',
    address:a};
  if(!S.project_address)
    S.project_address=[a.street,a.city,a.state,a.zip].filter(Boolean).join(', ');
  // Prefer the job's assigned salesperson when it's a known team member
  if(p.assigned_salesperson){
    const u=p.assigned_salesperson.split('@')[0].toLowerCase();
    if(TEAM.includes(u)){S.salesperson=u;setVal('salesperson',u);}
  }
  document.getElementById('crm-search').value='';
  closeCrm(); setDirty(); renderSidebar(); renderCoverPage(); renderCrmLinkBadge();
}
function renderCrmLinkBadge() {
  const el=document.getElementById('crm-link-badge');
  if(!el)return;
  const c=S.customer||{};
  if(c.crm_project_id){
    el.innerHTML=`🔗 Linked to CRM job <strong>${esc(c.crm_job_number||'')}</strong> — signed contract will upload automatically`;
    el.style.display='block';
  }else{
    el.innerHTML='⚠ Not linked to a CRM job — search above to link so the signed contract uploads to the CRM';
    el.style.display='block';
  }
}
function closeCrm() { document.getElementById('crm-dropdown').classList.add('hidden'); }

/* ── Photos ────────────────────────────────────────────────────────── */

async function uploadPhotos(files) {
  if(!files.length)return;
  if(!S.estimate_id)await saveEstimate();
  for(const file of files){
    const isPdf = /\.pdf$/i.test(file.name) || file.type === 'application/pdf';
    const fd=new FormData(); fd.append('file',file);
    try{
      const r=await fetch(`/api/uploads/${S.estimate_id}`,{method:'POST',body:fd});
      if(!r.ok){const e=await r.json();throw new Error(e.error||'Upload failed');}
      const res=await r.json();
      if(isPdf){
        if(!Array.isArray(S.attachments)) S.attachments=[];
        S.attachments.push({id:uid(),filename:res.filename,original_name:file.name,
          label:file.name.replace(/\.pdf$/i,''),show_in_estimate:true});
        setDirty(); renderPhotos();
      }else{
        S.photos.push({id:uid(),filename:res.filename,original_name:file.name,caption:'',show_in_estimate:true});
        setDirty(); renderPhotos(); renderCoverPage();
      }
    }catch(e){alert(`Could not upload ${file.name}: ${e.message}`);}
  }
  document.getElementById('photo-input').value='';
  warmPrintPhotos();
}
function renderAttachments() {
  const wrap=document.getElementById('attachments-list'); if(!wrap)return;
  const atts=S.attachments||[];
  if(!atts.length){wrap.innerHTML='';return;}
  wrap.innerHTML=`<div class="att-header">📄 PDF Documents <span class="att-hint">shown to the customer as a link on their estimate</span></div>`+
    atts.map(att=>`
    <div class="att-row">
      <span class="att-icon">📄</span>
      <input type="text" class="att-label" value="${esc(att.label||att.original_name||'Document')}"
        onchange="attSetLabel('${att.id}',this.value)" placeholder="Document name shown to customer">
      <label class="att-show" title="Show this document to the customer">
        <input type="checkbox" ${att.show_in_estimate!==false?'checked':''}
          onchange="attToggle('${att.id}',this.checked)"> Show
      </label>
      <a class="att-view" href="/uploads/${esc(att.filename)}" target="_blank" rel="noopener">View</a>
      <button class="att-del" onclick="attDelete('${att.id}')" title="Remove">×</button>
    </div>`).join('');
}
function attSetLabel(id,v){const a=(S.attachments||[]).find(x=>x.id===id);if(a){a.label=v;setDirty();}}
function attToggle(id,v){const a=(S.attachments||[]).find(x=>x.id===id);if(a){a.show_in_estimate=v;setDirty();}}
async function attDelete(id){
  const a=(S.attachments||[]).find(x=>x.id===id); if(!a)return;
  if(!confirm('Remove this PDF?'))return;
  const parts=a.filename.split('/');
  try{await fetch(`/api/uploads/${parts[0]}/${parts[1]}`,{method:'DELETE'});}catch{}
  S.attachments=(S.attachments||[]).filter(x=>x.id!==id);
  setDirty(); renderPhotos();
}
function photoCaption(id,v){ const p=S.photos.find(x=>x.id===id);if(p){p.caption=v;setDirty();} }
function photoToggle(id,v){ const p=S.photos.find(x=>x.id===id);if(p){p.show_in_estimate=v;setDirty();warmPrintPhotos();} }
async function photoDelete(id) {
  const p=S.photos.find(x=>x.id===id); if(!p)return;
  if(!confirm('Delete this photo?'))return;
  if(S.cover_photo_id===id) S.cover_photo_id=null;
  const parts=p.filename.split('/');
  try{await fetch(`/api/uploads/${parts[0]}/${parts[1]}`,{method:'DELETE'});}catch{}
  S.photos=S.photos.filter(x=>x.id!==id);
  setDirty(); renderPhotos(); renderCoverPage();
}

/* ── Dashboard ──────────────────────────────────────────────────────── */

let _dashData = [];
let _dashRep  = null; // null until first open; then '' = all reps

function daysAgoLabel(iso) {
  if (!iso) return '';
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (d <= 0) return 'today';
  if (d === 1) return '1 day ago';
  return `${d} days ago`;
}
function estStatusOf(e) {
  if (e.signed) return 'signed';
  if (e.first_viewed_at) return 'viewed';
  if (e.sent) return 'sent';
  return 'draft';
}

async function openDashboard() {
  try {
    const r = await fetch('/api/estimates');
    _dashData = await r.json();
  } catch { _dashData = []; }
  if (_dashRep === null) _dashRep = _loggedInUser || '';
  renderDashboard();
  document.getElementById('dashboard-modal').classList.remove('hidden');
}
function closeDashboard() { document.getElementById('dashboard-modal').classList.add('hidden'); }
function maybeCloseDashboard(e) { if (e.target.id === 'dashboard-modal') closeDashboard(); }
function dashSetRep(v) { _dashRep = v; renderDashboard(); }

function dashRow(e) {
  const st    = estStatusOf(e);
  const enum_ = e.estimate_id ? 'EST-' + e.estimate_id.split('-')[0].toUpperCase() : '';
  const chips = {
    signed: '<span class="dash-chip dash-chip-signed">✓ Signed</span>',
    viewed: '<span class="dash-chip dash-chip-viewed">👀 Viewed</span>',
    sent:   '<span class="dash-chip dash-chip-sent">📤 Sent</span>',
    draft:  '<span class="dash-chip dash-chip-draft">Draft</span>',
  };
  let activity = '';
  if (st === 'signed')      activity = `Signed ${daysAgoLabel(e.signed_at)}`;
  else if (st === 'viewed') activity = `Viewed ${daysAgoLabel(e.last_viewed_at)}${e.view_count > 1 ? ` (${e.view_count}×)` : ''}`;
  else if (st === 'sent')   activity = `Sent ${daysAgoLabel(e.sent_at)} — not opened yet`;
  else                      activity = `Updated ${daysAgoLabel(e.updated_at)}`;
  const typeLbl = e.estimate_type === 'insurance' ? '🏛 Insurance'
    : (e.selected_tier ? e.selected_tier[0].toUpperCase() + e.selected_tier.slice(1) : 'Retail');
  return `<div class="dash-row" onclick="doLoadEstimate('${esc(e.estimate_id)}');closeDashboard()">
    <div class="dash-row-main">
      <strong>${esc(e.customer_name || '(no customer)')}</strong>
      <small>${esc(enum_)}${e.city ? ' · ' + esc(e.city) : ''} · ${esc(typeLbl)}${e.salesperson ? ' · ' + esc(cap(e.salesperson)) : ''}</small>
    </div>
    <div class="dash-row-side">
      <span class="dash-total">${fmtCur(e.total || 0)}</span>
      ${chips[st]}
      <small class="dash-activity">${esc(activity)}</small>
    </div>
  </div>`;
}

function renderDashboard() {
  const body = document.getElementById('dashboard-body');
  if (!body) return;
  let list = _dashData;
  if (_dashRep) list = list.filter(e => (e.salesperson || '') === _dashRep);

  const viewed  = list.filter(e => estStatusOf(e) === 'viewed')
                      .sort((a, b) => (b.last_viewed_at || '').localeCompare(a.last_viewed_at || ''));
  const sent    = list.filter(e => estStatusOf(e) === 'sent')
                      .sort((a, b) => (a.sent_at || '').localeCompare(b.sent_at || ''));
  const drafts  = list.filter(e => estStatusOf(e) === 'draft')
                      .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
  const signed  = list.filter(e => estStatusOf(e) === 'signed')
                      .sort((a, b) => (b.signed_at || '').localeCompare(a.signed_at || ''));

  const outstanding   = [...viewed, ...sent];
  const outstandingSum = outstanding.reduce((s, e) => s + (e.total || 0), 0);
  const cutoff30   = Date.now() - 30 * 86400000;
  const signed30   = signed.filter(e => e.signed_at && new Date(e.signed_at).getTime() >= cutoff30);
  const signed30Sum = signed30.reduce((s, e) => s + (e.total || 0), 0);

  const repOpts = ['<option value="">All reps</option>']
    .concat(TEAM.map(m => `<option value="${m}" ${m === _dashRep ? 'selected' : ''}>${cap(m)}</option>`))
    .join('');

  const section = (title, arr, cls) => arr.length
    ? `<div class="dash-section"><h4 class="${cls || ''}">${title} <span class="dash-count">${arr.length}</span></h4>
       ${arr.map(dashRow).join('')}</div>`
    : '';

  body.innerHTML = `
    <div class="dash-toolbar">
      <a href="/api/backup" class="dash-backup-link" title="Download a zip of all estimates, photos, and settings">💾 Download full backup</a>
      <select onchange="dashSetRep(this.value)" class="dash-rep-select">${repOpts}</select>
    </div>
    <div class="dash-cards">
      <div class="dash-card">
        <div class="dash-card-num">${outstanding.length}</div>
        <div class="dash-card-lbl">Outstanding</div>
        <div class="dash-card-sub">${fmtCur(outstandingSum)}</div>
      </div>
      <div class="dash-card dash-card-hot">
        <div class="dash-card-num">${viewed.length}</div>
        <div class="dash-card-lbl">Viewed — not signed</div>
        <div class="dash-card-sub">follow up now</div>
      </div>
      <div class="dash-card">
        <div class="dash-card-num">${sent.length}</div>
        <div class="dash-card-lbl">Sent — never opened</div>
        <div class="dash-card-sub">re-send or call</div>
      </div>
      <div class="dash-card dash-card-won">
        <div class="dash-card-num">${signed30.length}</div>
        <div class="dash-card-lbl">Signed (30 days)</div>
        <div class="dash-card-sub">${fmtCur(signed30Sum)}</div>
      </div>
    </div>
    ${section('🔥 Viewed — awaiting signature', viewed, 'dash-h-hot')}
    ${section('📤 Sent — not yet opened', sent)}
    ${section('📝 Drafts', drafts)}
    ${section('✅ Recently signed', signed.slice(0, 15), 'dash-h-won')}
    ${!list.length ? '<div class="dash-empty">No estimates yet for this rep.</div>' : ''}`;
}

/* ── Settings ───────────────────────────────────────────────────────── */

async function openSettings() {
  try {
    const r = await fetch('/api/settings');
    appSettings = await r.json() || {};
  } catch { appSettings = appSettings || {}; }
  document.getElementById('settings-colors').value = _globalShingleColors().join('\n');
  document.getElementById('settings-waste').value  = _globalWastePct();
  document.getElementById('settings-modal').classList.remove('hidden');
}
function closeSettings() { document.getElementById('settings-modal').classList.add('hidden'); }
function maybeCloseSettings(e) { if (e.target.id === 'settings-modal') closeSettings(); }

async function saveSettings() {
  const colors = document.getElementById('settings-colors').value
    .split('\n').map(s => s.trim()).filter(Boolean);
  const waste = parseFloat(document.getElementById('settings-waste').value);
  appSettings = {
    ...appSettings,
    shingle_colors: colors,
    default_waste_pct: isNaN(waste) ? 10 : waste,
  };
  try {
    const r = await fetch('/api/settings', {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(appSettings),
    });
    if (!r.ok) throw new Error('Save failed');
    // Refresh the current estimate's color options if untouched from defaults
    if (S.shingle_selection && !S.signature) {
      S.shingle_selection.options = _globalShingleColors();
      setDirty();
      if (activePage === 'contract') renderContractPage();
    }
    closeSettings();
    alert('✓ Settings saved! New estimates will use these colors.');
  } catch (e) { alert('Could not save settings: ' + e.message); }
}

/* ── Share / E-Signature ────────────────────────────────────────────── */

async function shareEstimate() {
  if (!S.estimate_id) {
    await saveEstimate();
    if (!S.estimate_id) return;
  }
  if (dirty) await saveEstimate();

  try {
    const r = await fetch(`/api/estimates/${S.estimate_id}/share`, { method: 'POST' });
    if (!r.ok) throw new Error('Could not generate share link');
    const data = await r.json();
    S.share_token = data.token;
    if (!S.sent_at) S.sent_at = new Date().toISOString();
    if (!S.status || S.status === 'draft') { S.status = 'sent'; setVal('est-status', 'sent'); }
    showShareModal(data.full_url || (window.location.origin + data.url), data.url);
  } catch(e) {
    alert('Error: ' + e.message);
  }
}

function showShareModal(fullUrl, relUrl) {
  const sig = S.signature;
  const isLocalhost = fullUrl.includes('localhost') || fullUrl.includes('127.0.0.1');

  let sigBlock = '';
  if (sig) {
    const dt  = new Date(sig.signed_at);
    const fmt = dt.toLocaleString('en-US', { dateStyle:'medium', timeStyle:'short' });
    sigBlock = `
      <div class="share-signed-badge">
        <span class="share-signed-icon">✓</span>
        <div>
          <div class="share-signed-name">Signed by <strong>${esc(sig.name)}</strong></div>
          <div class="share-signed-meta">${fmt} · IP ${esc(sig.ip_address||'')}</div>
        </div>
      </div>
      <a class="btn-download-signed" href="/api/estimates/${S.estimate_id}/signed" target="_blank">
        📄 Download Signed Contract
      </a>`;
  }

  const localhostWarning = isLocalhost ? `
    <div class="share-warn">
      ⚠ This link uses your computer's local address — customers outside your network won't be able to open it.
      <div style="margin-top:6px">
        <strong>Quick fix:</strong> Set a public URL below (e.g. from <a href="https://ngrok.com" target="_blank">ngrok.com</a>
        or ask your IT person for the office IP).
      </div>
    </div>` : '';

  document.getElementById('share-modal-body').innerHTML = `
    ${sigBlock}
    ${localhostWarning}
    <div>
      <div class="share-step">1. Copy the customer link</div>
      <div class="share-url-row">
        <input id="share-url-input" class="share-url-input" type="text" value="${esc(fullUrl)}" readonly>
        <button class="share-copy-btn" onclick="copyShareUrl()">Copy</button>
      </div>
      <div class="share-hint">Paste this into a text or email — the customer opens it on any phone or computer.</div>
    </div>
    <div>
      <div class="share-step">2. Send via text or email</div>
      <div class="share-hint">No app needed on their end. They can review, choose a package, and sign electronically.</div>
    </div>
    ${!sig ? `
    <div class="share-status-row">
      <span class="share-status-dot pending"></span>
      <span>Waiting for customer signature</span>
      <button class="btn-secondary" style="margin-left:auto" onclick="checkSignatureStatus()">Check Status</button>
    </div>` : ''}
    <div class="share-puburl-section">
      <div class="share-puburl-label">Public / ngrok URL override <span style="font-weight:400;opacity:.7">(optional — changes all future share links)</span></div>
      <div class="share-url-row">
        <input id="share-puburl-input" class="share-url-input" type="text"
          placeholder="https://abc123.ngrok-free.app"
          value="${esc(window._serverPublicUrl||'')}">
        <button class="share-copy-btn" style="background:#1a3a5c" onclick="savePublicUrl()">Save</button>
      </div>
    </div>
    <div style="text-align:center">
      <a href="${esc(relUrl||fullUrl)}" target="_blank" class="share-preview-link">Preview customer view ↗</a>
    </div>`;

  document.getElementById('share-modal').classList.remove('hidden');
}

async function savePublicUrl() {
  const val = (document.getElementById('share-puburl-input')?.value || '').trim();
  try {
    const r = await fetch('/api/server-info', {
      method: 'PUT',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ public_url: val })
    });
    const d = await r.json();
    window._serverPublicUrl = val;
    const btn = document.querySelector('.share-puburl-section .share-copy-btn');
    if (btn) { btn.textContent = '✓ Saved'; setTimeout(()=>{ btn.textContent='Save'; }, 1800); }
    // Refresh the share URL input to show updated URL
    const inp = document.getElementById('share-url-input');
    if (inp && S.share_token) {
      inp.value = (d.base_url||'').replace(/\/$/,'') + '/sign/' + S.share_token;
      // Remove localhost warning if it was showing
      document.querySelector('.share-warn')?.remove();
    }
  } catch(e) { alert('Could not save: ' + e.message); }
}

function copyShareUrl() {
  const inp = document.getElementById('share-url-input');
  inp.select(); inp.setSelectionRange(0, 9999);
  try {
    navigator.clipboard.writeText(inp.value).catch(() => document.execCommand('copy'));
  } catch {
    document.execCommand('copy');
  }
  const btn = document.querySelector('.share-copy-btn');
  btn.textContent = 'Copied!'; btn.style.background = '#16a34a';
  setTimeout(() => { btn.textContent = 'Copy'; btn.style.background = ''; }, 2000);
}

async function checkSignatureStatus() {
  if (!S.estimate_id) return;
  try {
    const r = await fetch(`/api/estimates/${S.estimate_id}`);
    const est = await r.json();
    if (est.signature) {
      S.signature = est.signature;
      S.status    = est.status;
      setDirty(); renderSidebar();
      showShareModal('/sign/' + S.share_token);
    } else {
      alert('Not signed yet — check back after the customer reviews the estimate.');
    }
  } catch(e) { alert('Error checking status: ' + e.message); }
}

function closeShareModal() { document.getElementById('share-modal').classList.add('hidden'); }
function maybeCloseShareModal(e) { if (e.target === document.getElementById('share-modal')) closeShareModal(); }

/* ── Save / Load ───────────────────────────────────────────────────── */

async function saveEstimate() {
  if(!S.estimate_id){S.estimate_id=uid();S.created_at=new Date().toISOString();}
  try{
    const r=await fetch(`/api/estimates/${S.estimate_id}`,{
      method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(S),
    });
    if(!r.ok)throw new Error('Save failed');
    setClean(); renderEstNum();
  }catch(e){alert('Save failed: '+e.message);}
}

async function newEstimateAction() {
  if(dirty&&!confirm('You have unsaved changes. Start a new estimate anyway?'))return;
  S=blankEstimate();
  applyTierDefaults(S); // pre-fill from global admin defaults
  activeTrade='roofing'; dirty=false;
  document.getElementById('save-indicator').textContent='';
  document.getElementById('save-indicator').className='save-indicator';
  renderAll(); switchPage('cover');
}

async function openEstimate() {
  try{ const r=await fetch('/api/estimates'); showOpenModal(await r.json()); }
  catch{ alert('Failed to load estimate list'); }
}
function showOpenModal(list) {
  const el=document.getElementById('estimates-list');
  el.innerHTML=list.length
    ? list.sort((a,b)=>(b.updated_at||'').localeCompare(a.updated_at||''))
        .map(e=>{
          const signed = e.status === 'accepted';
          return `<div class="eli ${signed?'eli-signed':''}" onclick="doLoadEstimate('${e.estimate_id}')">
            <div class="eli-name">${esc(e.customer_name||'(unnamed)')}${signed?' <span class="eli-signed-tag">✓ Signed</span>':''}</div>
            <div class="eli-meta">${esc(e.estimate_date||'')} · ${esc((e.status||'draft').toUpperCase())} · ${esc(TIER_LABELS[e.selected_tier]||'')}</div>
            ${signed ? `<a class="eli-dl-btn" href="/api/estimates/${e.estimate_id}/signed" target="_blank" onclick="event.stopPropagation()">📄 Download</a>` : ''}
            <button class="eli-delete" onclick="doDeleteEstimate(event,'${e.estimate_id}')">Delete</button>
          </div>`;
        }).join('')
    : '<p class="empty-msg">No saved estimates yet.</p>';
  document.getElementById('open-modal').classList.remove('hidden');
}
async function doLoadEstimate(id) {
  try{
    const r=await fetch(`/api/estimates/${id}`);
    if(!r.ok)throw new Error('Not found');
    S=await r.json();
    if(!S.tier_descriptions) S.tier_descriptions={good:'',better:'',best:''};
    if(S.print_contract===undefined) S.print_contract=true;
    if(!S.contract_text) S.contract_text=(S.estimate_type==='insurance'?DEFAULT_INSURANCE_CONTRACT:DEFAULT_CONTRACT);
    if(!S.cover_photo_id) S.cover_photo_id=null;
    if(S.intro_text===undefined) S.intro_text='';
    if(!S.page_visibility) S.page_visibility={intro:false,options:true};
    if(S.share_token===undefined) S.share_token=null;
    if(S.signature===undefined) S.signature=null;
    if(!S.trades.insurance) {
      S.trades.insurance={enabled:false,sections:[{id:'sec_'+uid(),name:'',items:[]}],scope_notes:'',claim_number:'',carrier:'',colors:{}};
    } else if(S.trades.insurance.line_items && !S.trades.insurance.sections) {
      S.trades.insurance.sections=[{id:'sec_'+uid(),name:'',items:S.trades.insurance.line_items}];
      delete S.trades.insurance.line_items;
    } else if(!S.trades.insurance.sections) {
      S.trades.insurance.sections=[{id:'sec_'+uid(),name:'',items:[]}];
    }
    TRADES.forEach(t=>{if(!S.trades[t])S.trades[t]={enabled:false,line_items:[],colors:{}};if(!S.trades[t].colors)S.trades[t].colors={};});
    if(!S.tier_features) S.tier_features={good:[],better:[],best:[]};
    if(!S.estimate_type) S.estimate_type='retail';
    if(!Array.isArray(S.contract_initials)) S.contract_initials=defaultInitials(S.estimate_type);
    if(!S.shingle_selection||typeof S.shingle_selection!=='object')
      S.shingle_selection={enabled:true,options:DEFAULT_SHINGLE_COLORS.slice(),chosen:''};
    if(!Array.isArray(S.shingle_selection.options)||!S.shingle_selection.options.length)
      S.shingle_selection.options=DEFAULT_SHINGLE_COLORS.slice();
    if(!Array.isArray(S.attachments)) S.attachments=[];
    if(!S.measurements||typeof S.measurements!=='object') S.measurements={waste_pct:_globalWastePct()};
    if(S.measurements.waste_pct===undefined) S.measurements.waste_pct=_globalWastePct();
    // Migrate old separate ridge_lf / hip_lf into combined ridge_hip_lf
    if(S.measurements.ridge_lf !== undefined || S.measurements.hip_lf !== undefined) {
      S.measurements.ridge_hip_lf = (parseFloat(S.measurements.ridge_lf)||0) + (parseFloat(S.measurements.hip_lf)||0);
      delete S.measurements.ridge_lf;
      delete S.measurements.hip_lf;
    }
    S.trades && Object.values(S.trades).forEach(td=>{
      if(td.line_items) td.line_items.forEach(i=>{
        if(i.scope_note===undefined) i.scope_note='';
        if(i.customer_visible===undefined) i.customer_visible=true;
        // Migrate old 'ridge' or 'hip' measure keys to combined ridge_hip
        if(i.measure === 'ridge' || i.measure === 'hip') i.measure = 'ridge_hip';
      });
    });
    activeTrade='roofing'; closeModal(); setClean(); renderAll(); switchPage('cover');
    warmPrintPhotos();
  }catch(e){alert('Could not load estimate: '+e.message);}
}
async function doDeleteEstimate(e,id) {
  e.stopPropagation();
  if(!confirm('Permanently delete this estimate?'))return;
  await fetch(`/api/estimates/${id}`,{method:'DELETE'});
  openEstimate();
}
function closeModal() { document.getElementById('open-modal').classList.add('hidden'); }
function maybeCloseModal(e) { if(e.target===document.getElementById('open-modal'))closeModal(); }

/* ── Print ─────────────────────────────────────────────────────────── */

// Cache of print-ready photo data URLs (full image + annotations baked in),
// keyed by photo id. Built before printing so the print DOM contains only
// instantly-rendered data URLs — no async <img> load races.
const _printPhotoCache = {};

function _loadImage(src) {
  return new Promise((resolve, reject) => {
    const im = new Image();
    im.onload  = () => resolve(im);
    im.onerror = () => reject(new Error('Image failed to load: ' + src));
    im.src = src;
  });
}

/* Build data URLs for the cover photo + every "show in estimate" photo,
   baking in any annotations. Safe to call repeatedly (keeps cache warm). */
async function preparePrintPhotos() {
  const needed = new Set();
  if (S.cover_photo_id) needed.add(S.cover_photo_id);
  (S.photos || []).forEach(p => { if (p.show_in_estimate) needed.add(p.id); });

  for (const p of (S.photos || [])) {
    if (!needed.has(p.id)) continue;
    try {
      const img = await _loadImage('/uploads/' + p.filename);
      const maxW = 1100; // cap resolution to keep PDF size reasonable
      const k = img.naturalWidth > maxW ? maxW / img.naturalWidth : 1;
      const W = Math.max(1, Math.round(img.naturalWidth  * k));
      const H = Math.max(1, Math.round(img.naturalHeight * k));
      const cv = document.createElement('canvas');
      cv.width = W; cv.height = H;
      const ctx = cv.getContext('2d');
      ctx.drawImage(img, 0, 0, W, H);
      (p.annotations || []).forEach(ann => drawAnn(ctx, ann, W, H, W / 300));
      _printPhotoCache[p.id] = cv.toDataURL('image/jpeg', 0.85);
    } catch (e) {
      console.warn('Could not prepare print photo', p.filename, e);
      delete _printPhotoCache[p.id]; // fall back to URL
    }
  }
}

/* Warm the cache opportunistically (fire-and-forget) so direct Ctrl+P works too */
function warmPrintPhotos() { preparePrintPhotos().catch(()=>{}); }

/* Source for a photo in print: baked data URL if ready, else raw upload URL */
function printPhotoSrc(photo) {
  return _printPhotoCache[photo.id] || ('/uploads/' + photo.filename);
}

/* Wait for any remaining <img> in print-content (fallback URLs) to finish */
async function _waitForPrintImages() {
  const imgs = [...document.querySelectorAll('#print-content img')];
  await Promise.all(imgs.map(img =>
    (img.complete && img.naturalWidth)
      ? Promise.resolve()
      : new Promise(res => { img.onload = img.onerror = res; })
  ));
}

async function doPrint() {
  await preparePrintPhotos();   // bake all photos to data URLs first
  buildPrintContent();
  await _waitForPrintImages();  // belt-and-suspenders for any fallback URLs / logo
  window.print();
}
window.addEventListener('beforeprint', () => { buildPrintContent(); });
window.addEventListener('afterprint',  ()=>{document.getElementById('print-content').innerHTML='';});

function buildPrintContent() {
  const tier=S.selected_tier;
  const c=S.customer;
  const cityState=[c.address.city,c.address.state].filter(Boolean).join(', ');
  const addr=[c.address.street,cityState].filter(Boolean).join(', ');
  const addrHtml=[c.address.street,cityState&&(cityState+(c.address.zip?' '+c.address.zip:''))].filter(Boolean).join('<br>');
  const projA=S.project_address||addr;
  const estNum=S.estimate_id?'EST-'+S.estimate_id.split('-')[0].toUpperCase():'DRAFT';
  const coverPhoto=S.photos.find(p=>p.id===S.cover_photo_id)||null;

  // Reusable branded page header (logo + contact) used on every section
  const pHeader = `<div class="p-header">
    <div class="p-header-brand">
      <img src="/static/logo.png" class="p-header-logo" alt="Project One Roofing">
      <div class="p-company-sub">115 E 5th St · Loveland, CO 80537 · 970-776-0945 · projectoneroofingcolorado.com</div>
    </div>
    <div class="p-est-badge"><span class="p-badge-num">${esc(estNum)}</span>${esc(S.estimate_date||'')}</div>
  </div>`;

  // ── Cover page: logo (top) · photo (center) · customer info (bottom) ──
  let html=`<div class="p-cover">
    <div class="p-cover-top">
      <img src="/static/logo.png" class="p-cover-toplogo" alt="Project One Roofing">
      <div class="p-cover-tagline">Your Roofing Estimate</div>
    </div>
    <div class="p-cover-mid">
      ${coverPhoto
        ? `<img class="p-cover-photo" src="${printPhotoSrc(coverPhoto)}" alt="Property">`
        : `<div class="p-cover-noimg">Project One Roofing<span>Prepared Just For You</span></div>`}
    </div>
    <div class="p-cover-bottom">
      <div class="p-cover-cname">${esc(c.name||'—')}</div>
      <div class="p-cover-addr">${addrHtml||esc(projA)||''}</div>
      <div class="p-cover-metarow">
        <div><span>Estimate</span>${esc(estNum)}</div>
        <div><span>Date</span>${esc(S.estimate_date||'—')}</div>
        <div><span>Valid Until</span>${esc(S.valid_until||'—')}</div>
        ${S.salesperson?`<div><span>Sales Rep</span>${esc(cap(S.salesperson))}</div>`:''}
      </div>
      <div class="p-cover-company">115 E 5th St · Loveland, CO 80537 · 970-776-0945 · projectoneroofingcolorado.com</div>
    </div>
  </div>`;

  // ── Intro letter ────────────────────────────────────────────────
  const pv = S.page_visibility || {};
  if (pv.intro !== false && S.intro_text?.trim()) {
    html += `<div class="p-intro">
      <div class="p-intro-letterhead">
        <img src="/static/logo.png" class="p-intro-logo" alt="Project One Roofing">
      </div>
      <div class="p-intro-body">${esc(S.intro_text)}</div>
    </div>`;
  }

  // ── Photo Report (its own page, right after the intro) ───────────
  const printPhotos = S.photos.filter(p => p.show_in_estimate && p.id !== S.cover_photo_id);
  if (printPhotos.length)
    html += `<div class="p-photos-page">
      ${pHeader}
      <h2 class="p-photos-title">Photo Report</h2>
      <div class="p-photo-grid">
        ${printPhotos.map(p=>`<figure class="p-photo-fig">
          <img src="${printPhotoSrc(p)}" alt="${esc(p.caption)}">
          ${p.caption?`<figcaption>${esc(p.caption)}</figcaption>`:''}
        </figure>`).join('')}
      </div>
    </div>`;

  // ── Inner pages ─────────────────────────────────────────────────
  html+=`<div class="p-page">
  ${pHeader}
  <div class="p-parties">
    <div>
      <div class="p-section-label">Prepared For</div>
      <div class="p-customer-name">${esc(c.name||'—')}</div>
      <div class="p-address">${addrHtml||esc(projA)||''}</div>
      ${c.phone?`<div class="p-address" style="margin-top:3pt">${esc(c.phone)}</div>`:''}
      ${c.email?`<div class="p-address">${esc(c.email)}</div>`:''}
    </div>
    <div>
      <div class="p-section-label">Estimate Details</div>
      <table class="p-meta-table">
        <tr><td>Project Address</td><td>${esc(projA||'—')}</td></tr>
        <tr><td>Estimate Date</td><td>${esc(S.estimate_date||'—')}</td></tr>
        <tr><td>Valid Until</td><td>${esc(S.valid_until||'—')}</td></tr>
        <tr><td>Salesperson</td><td>${esc(S.salesperson?cap(S.salesperson):'—')}</td></tr>
      </table>
    </div>
  </div>`;

  // Build auto-detected item lists (used as fallback when no custom tier_features set)
  const tierItems={};
  TIERS.forEach(t=>{tierItems[t]=[];});
  TRADES.forEach(trade=>{
    const td=S.trades[trade]; if(!td||!td.enabled||trade==='insurance')return;
    const tradeMode=td.mode||(trade==='gutters'?'simple':'gbb');
    (td.line_items||[]).forEach(item=>{
      if(tradeMode==='simple'){
        if(parseFloat(item.quantity)>0){
          const lbl=item.description?`${item.name} — ${item.description}`:item.name;
          TIERS.forEach(t=>tierItems[t].push(lbl));
        }
        return;
      }
      TIERS.forEach(t=>{
        const ti=(item.tiers||{})[t]||{};
        if(ti.included===false)return;  // excluded from this package tier
        if((parseFloat(item.quantity)||0)>0)
          tierItems[t].push(ti.description?`${item.name} — ${ti.description}`:item.name);
      });
    });
  });
  // Use custom tier_features when set; fall back to auto-detected items
  const tierDisplayItems={};
  TIERS.forEach(t=>{
    const f=(S.tier_features||{})[t];
    tierDisplayItems[t]=(f&&f.length)?f:tierItems[t];
  });

  const estType = S.estimate_type || 'retail';

  if (estType === 'retail') {
    // ── Package comparison (retail only) ──────────────────────────
    if (pv.options !== false) html+=`<div class="p-pkg-comparison">
      <h2>Your Options</h2>
      <table class="p-pkg-table"><thead><tr>
        ${TIERS.map(t=>`<th class="col-${t} ${t===tier?'selected-col':''}">
          ${TIER_LABELS[t]} ${t===tier?'<br><span class="p-selected-tag">SELECTED</span>':''}
        </th>`).join('')}
      </tr></thead><tbody><tr>
        ${TIERS.map(t=>{
          const tot=grandTotal(t);
          const desc=(S.tier_descriptions||{})[t]||'';
          return `<td>
            <span class="p-pkg-price">${fmtCur(tot)}</span>
            ${desc?`<span class="p-pkg-desc">${esc(desc)}</span>`:''}
            ${tierDisplayItems[t].slice(0,10).map(i=>`<span class="p-pkg-item">· ${esc(i)}</span>`).join('')}
            ${tierDisplayItems[t].length>10?`<span class="p-pkg-item" style="color:#aaa">+ ${tierDisplayItems[t].length-10} more…</span>`:''}
          </td>`;
        }).join('')}
      </tr></tbody></table>
    </div>`;

    // ── GBB trade tables ──────────────────────────────────────────
    html+=`<div class="p-package-banner">Package: ${TIER_LABELS[tier]}</div>`;
    TRADES.filter(t=>t!=='insurance').forEach(trade=>{
      const td=S.trades[trade];
      if(!td.enabled||!td.line_items.length)return;
      // Skip the whole trade if nothing will print (all zero-qty / excluded items)
      const _shown=td.line_items.filter(item =>
        (item.tiers?.[tier]?.included)!==false && (parseFloat(item.quantity)||0)>0);
      if(!_shown.length)return;
      const rate=tradeRate(trade);
      const subtot=tradeTotal(trade,tier);
      const colors=td.colors||{};
      const colorStr=Object.entries(colors).filter(([,v])=>v).map(([k,v])=>`${cap(k.replace(/_/g,' '))}: ${v}`).join(' · ');
      html+=`<div class="p-trade">
        <div class="p-trade-title">${TRADE_LABELS[trade]}
          ${colorStr?`<span style="font-size:9pt;font-weight:400;color:#555;margin-left:10pt">· ${esc(colorStr)}</span>`:''}
        </div>
        <table class="p-table"><thead><tr>
          <th>Description</th><th class="p-right">Qty</th><th>Unit</th>
          <th class="p-right">Unit Price</th><th class="p-right">Total</th>
        </tr></thead><tbody>
          ${(()=>{
            const inTier       = td.line_items.filter(item =>
              (item.tiers?.[tier]?.included) !== false && (parseFloat(item.quantity)||0) > 0);
            const visibleItems = inTier.filter(item => item.customer_visible !== false);
            const hiddenCount  = inTier.length - visibleItems.length;
            return visibleItems.map(item=>{
              const t=item.tiers[tier]||{};
              const cost=(parseFloat(t.material_unit_cost)||0)+(parseFloat(t.labor_unit_cost)||0);
              const sell=S.pricing.mode==='margin'?(rate>=100?0:cost/(1-rate/100)):cost*(1+rate/100);
              const tot=sell*(parseFloat(item.quantity)||0);
              return `<tr>
                <td>${esc(item.name)}
                  ${t.description?`<div class="p-desc-sub">${esc(t.description)}</div>`:''}
                  ${t.notes?`<div class="p-desc-sub" style="font-style:italic;color:#555">${esc(t.notes)}</div>`:''}
                </td>
                <td class="p-right">${item.quantity||0}</td>
                <td>${esc(item.unit)}</td>
                <td class="p-right">${fmtCur(sell)}</td>
                <td class="p-right">${fmtCur(tot)}</td>
              </tr>`;
            }).join('') + (hiddenCount?`<tr><td colspan="5" class="p-hidden-note">Additional materials &amp; supplies included in total</td></tr>`:'');
          })()}
        </tbody><tfoot><tr>
          <td colspan="4">${TRADE_LABELS[trade]} Subtotal</td>
          <td class="p-right">${fmtCur(subtot)}</td>
        </tr></tfoot></table>
      </div>`;
    });
    html+=`<div class="p-grand-total"><span>Total — ${TIER_LABELS[tier]} Package</span><span>${fmtCur(grandTotal(tier))}</span></div>`;

  } else {
    // ── Insurance-only print layout ───────────────────────────────
    const insTd=S.trades.insurance;
    const insCarrier=insTd?.carrier?` — ${esc(insTd.carrier)}`:'';
    const insClaimNum=insTd?.claim_number?` &nbsp;·&nbsp; Claim #: ${esc(insTd.claim_number)}`:'';
    html+=`<div class="p-package-banner">Insurance Estimate${insCarrier}${insClaimNum}</div>`;
    if(insTd?.enabled){
      const sections=insTd.sections||(insTd.line_items?[{name:'',items:insTd.line_items}]:[]);
      const activeSections=sections.filter(s=>(s.items||[]).length>0);
      activeSections.forEach(sec=>{
        const secTotal=(sec.items||[]).reduce((s,it)=>s+(parseFloat(it.acv)||0)+(parseFloat(it.depreciation)||0),0);
        html+=`<div class="p-trade">`;
        if(sec.name) html+=`<div class="p-trade-title">${esc(sec.name)}</div>`;
        html+=`<table class="p-table"><thead><tr>
          <th>Item Name</th><th>Description</th>
          <th class="p-right">ACV</th><th class="p-right">Depreciation</th><th class="p-right">RCV</th>
        </tr></thead><tbody>
          ${(sec.items||[]).map(item=>{
            const acv=parseFloat(item.acv)||0;
            const dep=parseFloat(item.depreciation)||0;
            return `<tr>
              <td>${esc(item.name||'')}</td>
              <td>${esc(item.description||'')}</td>
              <td class="p-right">${fmtCur(acv)}</td>
              <td class="p-right">${fmtCur(dep)}</td>
              <td class="p-right">${fmtCur(acv+dep)}</td>
            </tr>`;
          }).join('')}
        </tbody><tfoot><tr>
          <td colspan="4">${sec.name?esc(sec.name)+' Subtotal':'Subtotal'}</td>
          <td class="p-right">${fmtCur(secTotal)}</td>
        </tr></tfoot></table></div>`;
      });
      if(activeSections.length)
        html+=`<div class="p-grand-total"><span>Insurance Claim Total</span><span>${fmtCur(insuranceTotal())}</span></div>`;
    }
    if(insTd?.scope_notes?.trim())
      html+=`<div class="p-notes" style="margin-top:8pt"><h3>Scope of Work</h3><p>${esc(insTd.scope_notes)}</p></div>`;
  }

  if(S.notes_customer?.trim())
    html+=`<div class="p-notes"><h3>Notes</h3><p>${esc(S.notes_customer)}</p></div>`;

  html+=`<div class="p-signatures">
    <div class="p-sig-block"><div class="p-sig-line"></div><div class="p-sig-label">Homeowner Signature</div>
      <div class="p-sig-date"><div><div class="p-sig-date-line"></div><span>Date</span></div></div></div>
    <div class="p-sig-block"><div class="p-sig-line"></div><div class="p-sig-label">Project One Roofing Representative</div>
      <div class="p-sig-date"><div><div class="p-sig-date-line"></div><span>Date</span></div></div></div>
  </div></div>`; // close p-page

  if(S.print_contract!==false&&S.contract_text?.trim())
    html+=`<div class="p-contract">${pHeader}<h2>Terms &amp; Conditions</h2>
      <div class="p-contract-body">${esc(S.contract_text)}</div></div>`;

  document.getElementById('print-content').innerHTML=html;
}

/* ── Helpers ───────────────────────────────────────────────────────── */

function cap(s) {
  if(!s)return'';
  return s.replace(/_/g,' ').replace(/\./g,' ').split(' ')
    .map(w=>w.charAt(0).toUpperCase()+w.slice(1)).join(' ');
}

/* ── Init ──────────────────────────────────────────────────────────── */

function populateSalespersonDropdown() {
  const sel=document.getElementById('salesperson');
  TEAM.forEach(m=>{const o=document.createElement('option');o.value=m;o.textContent=cap(m);sel.appendChild(o);});
}

setInterval(()=>{if(dirty&&S.estimate_id)saveEstimate();},60000);

document.addEventListener('click', e=>{
  if(!e.target.closest('.crm-search-wrap'))closeCrm();
});

let _loggedInUser = '';

document.addEventListener('DOMContentLoaded', async ()=>{
  populateSalespersonDropdown();
  bindSidebarEvents();
  try {
    const [tRes, pbRes, tdRes, siRes, meRes, setRes] = await Promise.all([
      fetch('/api/templates'), fetch('/api/pricebook'),
      fetch('/api/tier-defaults'), fetch('/api/server-info'),
      fetch('/api/me'), fetch('/api/settings')
    ]);
    templates    = await tRes.json();
    priceBook    = await pbRes.json();
    tierDefaults = await tdRes.json();
    try { appSettings = await setRes.json() || {}; } catch { appSettings = {}; }
    // Apply global settings to the fresh blank estimate
    if (!S.estimate_id) {
      if (S.shingle_selection) S.shingle_selection.options = _globalShingleColors();
      if (S.measurements) S.measurements.waste_pct = _globalWastePct();
    }
    const si = await siRes.json();
    window._serverPublicUrl = si.public_url || '';
    window._serverBaseUrl   = si.base_url   || '';
    const me = await meRes.json();
    if (me.username) {
      _loggedInUser = me.username;
      const badge = document.getElementById('user-badge');
      const nameEl = document.getElementById('user-display-name');
      if (badge) badge.style.display = 'flex';
      if (nameEl) nameEl.textContent = me.display_name;
      // Auto-set salesperson on blank (new) estimate
      if (!S.salesperson) {
        S.salesperson = me.username;
        setVal('salesperson', me.username);
      }
    }
  } catch {}
  // Apply any saved defaults to the initial blank estimate
  applyTierDefaults(S);
  renderAll();
  switchPage('cover');
});
