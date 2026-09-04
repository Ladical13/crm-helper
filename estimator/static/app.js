/* ── Mount prefix ───────────────────────────────────────────────────── */
// The estimator runs under /estimate inside the portal (see portal/mounts.py)
// and at the root when served standalone. Derived from the URL rather than
// hardcoded so one bundle works both ways — the test suite and any standalone
// run get '' and behave exactly as before the merge.
const BASE = location.pathname.startsWith('/estimate') ? '/estimate' : '';

/* ── Auth + mount prefix: everything goes through fetch ─────────────── */
// Two jobs in one wrapper. Rewriting /api/ here is what keeps this a 3-line
// change instead of 77 — every API call in this file is a root-absolute
// string, so prefixing them centrally covers all of them. And if the session
// has expired the server returns 401, so bounce to the portal login instead of
// failing to parse JSON. Note /login stays root-absolute: it belongs to the
// portal, not to this app.
(function () {
  const _origFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    if (BASE && typeof args[0] === 'string' && args[0].startsWith('/api/')) {
      args[0] = BASE + args[0];
    }
    const res = await _origFetch(...args);
    if (res.status === 401) { window.location = '/login'; }
    return res;
  };
})();

/* ── Constants ─────────────────────────────────────────────────────── */

const TRADES = ['roofing','siding','windows','gutters','commercial','other','insurance'];
const TRADE_LABELS = { roofing:'Roofing', siding:'Siding', windows:'Windows', gutters:'Gutters', commercial:'Commercial', other:'Other', insurance:'Insurance' };
// Retail trades (everything the G/B/B + simple pricing engine touches).
// `insurance` has a different shape entirely and is always excluded.
const RETAIL_TRADE_KEYS = ['roofing','siding','windows','gutters','commercial','other'];
const TIERS = ['good','better','best'];
const TIER_LABELS = { good:'Good', better:'Better', best:'Best' };
// Letterhead line for every printed page. Mirrors the company block in
// _cv_header / the PDF masthead in app.py — keep the three in sync.
const COMPANY_ADDR_LINE = '115 E 5th St · Loveland, CO 80537 · 970-776-0945 · projectoneroofingcolorado.com';
// Trades built from a flat product catalog + named bundles (the tier dropdown)
// instead of the per-tier template item list. Mirrored server-side by
// BUNDLE_SEEDS in app.py — keep the two lists in sync.
const BUNDLE_TRADES = ['roofing','siding','windows','commercial'];
function isBundleTrade(t) { return BUNDLE_TRADES.includes(t); }
// Trades that sell as one price rather than Good/Better/Best unless the rep
// says otherwise. Mirrored by SIMPLE_MODE_TRADES in app.py — the two must
// agree or the server prices a trade the browser didn't.
//
// Commercial left this list when the coating/overlay/replacement ladder
// shipped: a flat roof is sold three ways like a shingle roof is. Gutters
// stay, because a gutter is one product at one price.
const SIMPLE_MODE_TRADES = ['gutters'];
// Trades whose default FLIPPED from simple to gbb — see effectiveTradeMode.
const MODE_DEFAULT_FLIPPED = ['commercial'];

/* ── Siding profile factors ────────────────────────────────────────────────
   Piece-per-SQ conversions the supplier take-off sheet uses — per manufacturer
   × exposure. Drives the Material Order piece counts on the production packet
   and the customer-facing profile name on the bundle card.

   The primary material's per-SQ COST is baked into the seed catalog and does
   NOT change with profile — the supplier sheet has piece counts but no
   dollars, so shipping fake per-SQ costs for B&B or Shake would undersell
   those systems. Until Luke supplies real supplier pricing per profile a
   per-line price override on the pricing tab is how a rep distinguishes them,
   and a yellow nag on the pricing tab says so out loud whenever a non-default
   profile is picked.

   MUST mirror SIDING_PROFILE_FACTORS + SIDING_BUNDLE_PROFILES in app.py. */
const SIDING_PROFILE_FACTORS = {
  lp: {
    lap_8:       { primary:{ pcs_per_sq:11.11, stick_ft:16, size:'8" Cedar Text Lap' } },
    bb_4x8:      { primary:{ pcs_per_sq:3.0,  size:'4×8 Board' },
                   battens:{ pcs_per_panel:3, stick_ft:16, size:'4/4×2 Batten' } },
    cedar_shake: { primary:{ pcs_per_sq:7.5, stick_ft:8, size:'12" Cedar Shake Panel',
                             source_note:'PLACEHOLDER pcs/SQ — not in supplier sheet, confirm with QXO' } },
  },
  hardie: {
    lap_8_25:        { primary:{ pcs_per_sq:14.25, stick_ft:12, size:'8.25" Cedar Mill Lap' } },
    bb_4x10:         { primary:{ pcs_per_sq:2.5,  size:'4×10 Board' },
                       battens:{ pcs_per_panel:3, stick_ft:12, size:'4/4×2.5 Batten' } },
    shake_straight:  { primary:{ pcs_per_sq:43, stick_ft:4, size:'15.25×4 Straight-Edge Shake' } },
    shake_staggered: { primary:{ pcs_per_sq:50, stick_ft:4, size:'15.25×4 Staggered-Edge Shake' } },
  },
};
const SIDING_BUNDLE_PROFILES = {
  b_lp_standard:      { mfg:'lp',     default:'lap_8',    options:['lap_8','bb_4x8','cedar_shake'] },
  b_lp_expert:        { mfg:'lp',     default:'lap_8',    options:['lap_8','bb_4x8','cedar_shake'] },
  b_hardie_primed:    { mfg:'hardie', default:'lap_8_25', options:['lap_8_25','bb_4x10','shake_straight','shake_staggered'] },
  b_hardie_statement: { mfg:'hardie', default:'lap_8_25', options:['lap_8_25','bb_4x10','shake_straight','shake_staggered'] },
};
// Order the Price Book renders siding groups in. Any unlisted group name
// (including 'Ungrouped') sorts to the end. Physical seed order is
// consistent with this list so freshly-installed books look the same as
// UI-sorted existing ones.
const _SIDING_GROUP_ORDER = ['LP SmartSide', 'James Hardie', 'EDCO Steel',
                             'Shared', 'Labor & Misc', 'Legacy'];
const SIDING_PROFILE_LABELS = {
  lap_8:           '8" Lap',
  bb_4x8:          'Board & Batten (4×8)',
  cedar_shake:     'Cedar Shake',
  lap_8_25:        '8.25" Lap',
  bb_4x10:         'Board & Batten (4×10)',
  shake_straight:  'Shake — Straight Edge',
  shake_staggered: 'Shake — Staggered Edge',
};
// The profile chosen for one tier — bundle's default when none is stored.
function sidingProfile(tier) {
  const td = S.trades && S.trades.siding; if (!td) return '';
  const bundleId = (td.tier_bundles || {})[tier] || '';
  const cfg = SIDING_BUNDLE_PROFILES[bundleId];
  if (!cfg) return '';
  const stored = ((td.tier_profiles || {})[tier] || '').trim();
  if (stored && cfg.options.includes(stored)) return stored;
  return cfg.default;
}
function isNonDefaultSidingProfile(tier) {
  const td = S.trades && S.trades.siding; if (!td) return false;
  const bundleId = (td.tier_bundles || {})[tier] || '';
  const cfg = SIDING_BUNDLE_PROFILES[bundleId];
  const p = sidingProfile(tier);
  return !!cfg && !!p && p !== cfg.default;
}
function setSidingProfile(tier, profile) {
  const td = S.trades && S.trades.siding; if (!td) return;
  const bundleId = (td.tier_bundles || {})[tier] || '';
  const cfg = SIDING_BUNDLE_PROFILES[bundleId];
  if (!cfg) return;
  const clean = cfg.options.includes(profile) ? profile : cfg.default;
  td.tier_profiles = td.tier_profiles || { good:'', better:'', best:'' };
  td.tier_profiles[tier] = clean;
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
}
/* Effective pricing mode for a trade. MUST mirror _trade_mode in app.py.

   An explicit mode always wins, so every estimate blankEstimate() ever wrote
   is unaffected by a default changing underneath it. The sniff is for the ones
   with NO mode key — written before per-trade modes existed, or POSTed by a
   script. Reading those as gbb hands flat unit_price items to the tiered
   pricer, which totals $0 while looking completely normal on screen.

   A tier-shaped item is unmistakable (applyBundleToTier always writes all
   three cells), so no `tiers` key anywhere means this was built and priced as
   a flat, single-price trade. Empty line_items takes today's default. */
function effectiveTradeMode(trade, td) {
  const mode = td && td.mode;
  if (mode) return mode;
  if (MODE_DEFAULT_FLIPPED.includes(trade)) {
    const items = (td && td.line_items) || [];
    if (items.length && !items.some(it => it && it.tiers)) return 'simple';
  }
  return SIMPLE_MODE_TRADES.includes(trade) ? 'simple' : 'gbb';
}
// Per-estimate package toggles: sell just Good/Better, Better/Best, etc.
// Absent key = enabled (backward compatible with every existing estimate).
function tierEnabled(t) { return (S.tiers_enabled || {})[t] !== false; }
function enabledTiers() { return TIERS.filter(tierEnabled); }
function toggleTierEnabled(t) {
  const cur = Object.assign({good:true, better:true, best:true}, S.tiers_enabled || {});
  if (cur[t] && enabledTiers().length <= 1) { alert('At least one package must stay on.'); return; }
  cur[t] = !cur[t];
  S.tiers_enabled = cur;
  if (!tierEnabled(S.selected_tier)) S.selected_tier = enabledTiers()[0];
  gbbTrades().forEach(tr => {
    if (!tierEnabled(tradeTier(tr))) S.trades[tr].selected_tier = enabledTiers()[0];
  });
  setDirty();
  renderTierButtons(); renderTotals();
  if (activePage === 'pricing') renderTradeContent();
  if (activePage === 'options') renderOptionsPage();
}

/* ── Per-trade G/B/B — each product carries its own package content and
   selection (td.tier_features / td.tier_descriptions / td.selected_tier).
   Estimate-level tier_features/descriptions/selected_tier are legacy: kept
   in sync (selected_tier = first GBB trade's pick) so old consumers and
   old signed estimates keep working. ───────────────────────────────── */
function tradeGbbMode(trade) {
  const td = S.trades[trade];
  return !!td && trade !== 'insurance' && td.enabled &&
    (effectiveTradeMode(trade, td)) === 'gbb';
}
function gbbTrades() { return TRADES.filter(tradeGbbMode); }
function tradeTier(trade) {
  const t = (S.trades[trade] || {}).selected_tier;
  return TIERS.includes(t) ? t : (TIERS.includes(S.selected_tier) ? S.selected_tier : 'better');
}
function setTradeTier(trade, tier) {
  if (!S.trades[trade]) return;
  S.trades[trade].selected_tier = tier;
  _syncLegacyTier();
  setDirty();
}
function _syncLegacyTier() {
  const first = gbbTrades()[0];
  if (first) S.selected_tier = tradeTier(first);
}
function tradeTierContent(trade) {
  // Ensure + return this trade's package content. Legacy estimate-level
  // tier_features/tier_descriptions came from a single-trade world where the
  // ONE trade was roofing — migrating them into the current first GBB trade
  // leaks roofing bullets onto siding/windows/other cards.
  const td = S.trades[trade]; if (!td) return { features:{}, descriptions:{} };
  if (!td.tier_features) {
    const legacy = trade === 'roofing' && S.tier_features &&
      TIERS.some(t => (S.tier_features[t] || []).length);
    td.tier_features     = legacy ? JSON.parse(JSON.stringify(S.tier_features))
                                  : { good:[], better:[], best:[] };
    td.tier_descriptions = (legacy && S.tier_descriptions)
      ? JSON.parse(JSON.stringify(S.tier_descriptions))
      : { good:'', better:'', best:'' };
  }
  if (!td.tier_descriptions) td.tier_descriptions = { good:'', better:'', best:'' };
  return { features: td.tier_features, descriptions: td.tier_descriptions };
}
/* ── Do a tier's stored bullets still describe what it actually sells? ─────
   tier_features / tier_descriptions are written by a bundle pick (and, before
   it was retired, by the Options tab). Nothing rewrites them when a rep builds
   the package by hand, so they go stale silently and the customer reads
   "Architectural laminate shingle system — lifetime limited warranty" over a
   rolled-roofing line item. There is no editor left to fix that by hand.

   Stale means one of two things, and both are checked against the estimate's
   own data rather than guessed:
     · the tier is __custom__ — the rep is building a package the price book
       doesn't sell, so the copy belongs to whatever bundle was there before;
     · the tier still names a bundle, but not one of that bundle's products is
       priced in this tier any more — the rep replaced the whole system without
       touching the dropdown.
   A pre-bundle estimate has no tier_bundles at all. Those bullets were curated
   by hand on the Options tab and are the best copy that estimate will ever
   have, so leave them alone — MUST mirror _tier_bullets_are_stale in app.py. */
function tierBulletsAreStale(trade, tier) {
  const td = S.trades[trade] || {};
  const tb = td.tier_bundles;
  if (!tb) return false;                 // pre-bundle estimate — hand-curated
  const bid = (tb[tier] || '').trim();
  /* Custom is the rep saying, on the Product dropdown, that this tier is not
     a package the book sells. Nothing else ever writes __custom__, so it is
     evidence on its own and is judged BEFORE the catalog test below.
     It used to be judged after, and that cost a real estimate: seeding a new
     estimate loads the default shingle bundles, so building a rolled-roofing
     job by hand means deleting those rows — which deletes the last catalog_id
     in the trade, and a trade with no catalog_id was ruled "never built by a
     bundle, bullets are the rep's own". The shingle tagline the bundle wrote
     on the way past then printed over the rolled roofing. Evidence the rep
     destroyed while doing exactly what the tab asks is not evidence. */
  if (bid === '__custom__') return true;
  const items = td.line_items || [];
  // A tier that still NAMES a bundle is judged only on a trade the bundles
  // actually built. Items created by applyBundleToTier carry catalog_id; a
  // hand-shaped trade has none, and without that evidence there is no bundle
  // whose leftover copy this could be — the bullets are the rep's own and stay.
  if (!items.some(it => it.catalog_id)) return false;
  if (!bid) return false;
  const ids = new Set((_tradeBundle(trade, bid) || {}).product_ids || []);
  if (!ids.size) return false;           // bundle deleted from the book — no call to make
  return !items.some(it =>
    ids.has(it.catalog_id) &&
    (parseFloat(it.quantity) || 0) > 0 &&
    ((it.tiers || {})[tier] || {}).included !== false);
}
/* Trades that print as a Good/Better/Best package choice. `other` is a G/B/B
   trade by data shape only: its tab shows one tier at a time and writes cost
   and description to ALL THREE (otherSetUnitCost / otherSetDesc), so offering
   it as a package prints three identical columns — $0.00 / $0.00 on an
   estimate where the extras were never given a quantity. It still prints its
   own line-item table like every other trade. */
function packageTrades() { return gbbTrades().filter(t => t !== 'other'); }

/* ── Estimate sections (structures / roof areas) ──────────────────────
   A trade's items can be grouped under named sections ("Main House",
   "Detached Garage", "South Slope") like insurance-mode sections. The data
   stays a flat line_items array — items just carry a `section` name and the
   trade keeps an ordered `sections` list — so every bit of pricing math
   (client and server) is untouched. Display groups: General (unnamed)
   first, then each section in order. */
function tradeSections(trade) {
  return (S.trades[trade].sections || []).filter(Boolean);
}
function itemSection(item) {
  return (item.section || '').trim();
}
function groupedTradeItems(trade, items) {
  const sections = tradeSections(trade);
  const known = new Set(sections);
  const groups = [{ name: '', items: items.filter(i => !known.has(itemSection(i))) }];
  sections.forEach(name => groups.push({ name, items: items.filter(i => itemSection(i) === name) }));
  return groups;
}
function addTradeSection(trade) {
  const name = (prompt('Section name (e.g. Main House, Detached Garage, South Slope):') || '').trim();
  if (!name) return;
  const td = S.trades[trade];
  td.sections = td.sections || [];
  if (td.sections.includes(name)) { alert('That section already exists.'); return; }
  td.sections.push(name);
  setDirty(); rerender();
  if (activePage === 'pricing') renderTradeContent();
}
function renameTradeSection(trade, idx) {
  const td = S.trades[trade];
  const old = (td.sections || [])[idx];
  if (old === undefined) return;
  // A section that names a building is that building. Renaming it here without
  // moving the structure would leave the roof's measurements joined to a name
  // nothing carries any more, and every item on it would fall back to the
  // estimate's numbers. Hand it to the one function that moves all three.
  const st = structureNamed(old);
  if (st) { renameStructure(st.id); return; }
  const name = (prompt('Rename section:', old) || '').trim();
  if (!name || name === old) return;
  if (td.sections.includes(name)) { alert('That section already exists.'); return; }
  td.sections[idx] = name;
  (td.line_items || []).forEach(i => { if (itemSection(i) === old) i.section = name; });
  setDirty(); rerender();
  if (activePage === 'pricing') renderTradeContent();
}
function deleteTradeSection(trade, idx) {
  const td = S.trades[trade];
  const name = (td.sections || [])[idx];
  if (name === undefined) return;
  // Same reasoning as the rename above -- and removeStructure asks its own
  // question, because dropping a building drops its priced work with it.
  const st = structureNamed(name);
  if (st) { removeStructure(st.id); return; }
  if (!confirm(`Remove the "${name}" section? Its items stay on the estimate under General.`)) return;
  td.sections.splice(idx, 1);
  (td.line_items || []).forEach(i => { if (itemSection(i) === name) delete i.section; });
  setDirty(); rerender();
  if (activePage === 'pricing') renderTradeContent();
}
function liSetSection(trade, id, name) {
  const item = findItem(trade, id);
  if (!item) return;
  if (name) item.section = name; else delete item.section;
  setDirty(); rerender();
  if (activePage === 'pricing') renderTradeContent();
}
function liCanMove(trade, item, dir) {
  const items = S.trades[trade].line_items;
  const i = items.indexOf(item);
  if (i < 0) return false;
  const known = new Set(tradeSections(trade));
  const groupOf = it => known.has(itemSection(it)) ? itemSection(it) : '';
  let j = i + dir;
  while (j >= 0 && j < items.length && groupOf(items[j]) !== groupOf(items[i])) j += dir;
  return j >= 0 && j < items.length;
}
/* Section chips + add button, shown above GBB grids and simple tables. */
function sectionManagerBar(trade) {
  const sections = tradeSections(trade);
  return `<div class="est-sections-bar">
    <span class="est-sections-lbl" title="Group items by structure or roof area — sections show as headers with their own subtotals on the estimate">Sections:</span>
    ${sections.map((name, i) => `<span class="est-section-chip">
      ${esc(name)}
      <button class="est-section-edit" onclick="renameTradeSection('${trade}',${i})" title="Rename section">✏</button>
      <button class="est-section-del" onclick="deleteTradeSection('${trade}',${i})" title="Remove section (items stay)">×</button>
    </span>`).join('')}
    <button class="est-section-add" onclick="addTradeSection('${trade}')">+ Add Section</button>
  </div>`;
}
/* ── Buildings (structures) ───────────────────────────────────
   An apartment complex is seven roofs on one contract, each with its own square
   count. The estimate carried ONE `measurements` dict, so every line item on it
   priced off the same numbers however many buildings the rep typed in.

   A structure is a building: a name, the trade its work lives on, and its own
   measurements in the SAME flat key namespace as S.measurements. That shared
   namespace is the whole trick — every MEASURE_DEFS calc is a pure function of a
   measurements dict, and so is commercialFastening(), so both run unchanged on
   one building's numbers.

   The structure's NAME is its section name. Sections already group items, print
   section headers with their own subtotals on the PDF, and do the same on the
   page the customer signs — so a building is priced, printed and subtotaled by
   machinery that already exists. structures[] adds the measurements behind the
   name, nothing else.

   An estimate with no structures is every estimate written before this and every
   ordinary one-roof job after it: S.measurements stays exactly what it was and
   stays the fallback, so those price identically. */
function estStructures() {
  return Array.isArray(S.structures) ? S.structures.filter(Boolean) : [];
}
function findStructure(id) {
  return estStructures().find(st => st.id === id) || null;
}
function structureNamed(name) {
  const n = String(name || '').trim();
  if (!n) return null;
  return estStructures().find(st => String(st.name || '').trim() === n) || null;
}
function tradeStructures(trade) {
  return estStructures().filter(st => (st.trade || 'commercial') === trade);
}
/* The measurements ONE item's quantity is computed from. An item tagged to a
   building reads that building's numbers; an untagged item — mobilization, a
   dumpster, anything that belongs to the job rather than to a roof — reads the
   estimate's own, which on a single-building estimate is all of them. */
function itemMeasurements(item) {
  const st = structureNamed(itemSection(item));
  return (st && st.measurements) || S.measurements || {};
}
/* The dict a Scope-page field writes into: a building's, or the estimate's. */
function structureMeasurements(id) {
  const st = id ? findStructure(id) : null;
  if (st) return (st.measurements = st.measurements || {});
  return (S.measurements = S.measurements || {});
}
/* Keep the trade's section list carrying every one of its building names.
   Sections may also be plain roof areas the rep added by hand ("South Slope"),
   which have no structure behind them and are left exactly where they are. */
/* One building's subtotal, by exactly the rules tradeTotal() prices a trade
   with -- zero-qty out, tier exclusions honoured -- so the number on the
   building card is the number that reaches the customer's section subtotal. */
function structureTotal(st) {
  if (!st) return 0;
  const trade = st.trade || 'commercial';
  const td = S.trades[trade];
  if (!td || !td.enabled) return 0;
  const mode = effectiveTradeMode(trade, td);
  const tier = tradeTier(trade);
  const name = String(st.name || '').trim();
  return (td.line_items || []).reduce((sum, i) => {
    if (itemSection(i) !== name) return sum;
    if ((parseFloat(i.quantity) || 0) <= 0) return sum;
    if (mode === 'simple') return sum + (parseFloat(i.quantity) || 0) * (parseFloat(i.unit_price) || 0);
    const t = (i.tiers || {})[tier] || {};
    if (t.included === false) return sum;
    return sum + lineTotalEffective(i, tier, trade);
  }, 0);
}

/* Next free "Building N". Counts by name rather than by list length so
   removing Building 2 of three doesn't hand the next one a name already taken. */
function _nextStructureName(trade) {
  const taken = new Set(estStructures().map(st => String(st.name || '').trim()));
  for (let n = 1; n < 500; n++) {
    const name = 'Building ' + n;
    if (!taken.has(name)) return name;
  }
  return 'Building ' + uid();
}
/* The first building on an estimate that already has work on it is the work
   already there. Promote it rather than leaving the rep's typed-in roof
   untagged beside a shiny empty Building 1: its measurements become Building
   1's, and every item on the trade takes its name. S.measurements is COPIED,
   not moved, so any other trade's numbers on this estimate keep working. */
function _promoteTradeToStructures(trade) {
  if (tradeStructures(trade).length) return null;
  const td = S.trades[trade] || {};
  const first = {
    id: 'st_' + uid(), name: 'Building 1', trade,
    measurements: JSON.parse(JSON.stringify(S.measurements || {})),
  };
  S.structures = estStructures().concat([first]);
  (td.line_items || []).forEach(i => { if (!itemSection(i)) i.section = first.name; });
  syncStructureSections(trade);
  return first;
}
function addStructure(trade) {
  trade = trade || 'commercial';
  _promoteTradeToStructures(trade);
  const st = { id: 'st_' + uid(), name: _nextStructureName(trade), trade, measurements: {} };
  S.structures = estStructures().concat([st]);
  syncStructureSections(trade);
  _structureOpen = st.id;
  setDirty(); rerender();
  return st;
}
/* The way buildings 2..7 actually get made. A complex is one roof detail
   repeated with different numbers, so the copy carries the whole priced build-up
   -- every line item, its costs, its description -- and the rep changes the
   squares. Items are deep-cloned with fresh ids: sharing them would make
   editing Building 2 silently edit Building 1. */
function duplicateStructure(id) {
  const src = findStructure(id);
  if (!src) return null;
  const trade = src.trade || 'commercial';
  const copy = {
    id: 'st_' + uid(), name: _nextStructureName(trade), trade,
    measurements: JSON.parse(JSON.stringify(src.measurements || {})),
  };
  S.structures = estStructures().concat([copy]);
  const td = S.trades[trade] || {};
  const srcName = String(src.name || '').trim();
  const clones = (td.line_items || [])
    .filter(i => itemSection(i) === srcName)
    .map(i => Object.assign(JSON.parse(JSON.stringify(i)), { id: uid(), section: copy.name }));
  td.line_items = (td.line_items || []).concat(clones);
  syncStructureSections(trade);
  _structureOpen = copy.id;
  setDirty(); rerender();
  return copy;
}
/* Rename in one move: the structure, the trade's section list and every item
   tagged to it. The name IS the join between a building and its measurements,
   so letting any one of the three drift orphans a roof's numbers. */
function renameStructure(id, name) {
  const st = findStructure(id);
  if (!st) return;
  const next = String(name == null ? (prompt('Rename building:', st.name) || '') : name).trim();
  if (!next || next === st.name) return;
  if (estStructures().some(o => o.id !== id && String(o.name || '').trim() === next)) {
    alert('There is already a building called "' + next + '".');
    return;
  }
  const trade = st.trade || 'commercial';
  const td = S.trades[trade] || {};
  const old = String(st.name || '').trim();
  st.name = next;
  td.sections = (td.sections || []).map(n => (n === old ? next : n));
  (td.line_items || []).forEach(i => { if (itemSection(i) === old) i.section = next; });
  setDirty(); rerender();
}
/* Removing a building removes its work. deleteTradeSection() parks a section's
   items under General, which is right for a roof area but wrong here: 20 items
   from a deleted building would sit in the estimate pricing off whatever
   measurements General resolves to. The count is in the prompt so nobody
   deletes a priced building thinking they are tidying a label. */
function removeStructure(id) {
  const st = findStructure(id);
  if (!st) return;
  const trade = st.trade || 'commercial';
  const td = S.trades[trade] || {};
  const name = String(st.name || '').trim();
  const doomed = (td.line_items || []).filter(i => itemSection(i) === name);
  const what = doomed.length
    ? `Remove ${name} and its ${doomed.length} line item${doomed.length === 1 ? '' : 's'}?`
    : `Remove ${name}?`;
  if (!confirm(what)) return;
  td.line_items = (td.line_items || []).filter(i => itemSection(i) !== name);
  td.sections   = (td.sections || []).filter(n => n !== name);
  S.structures  = estStructures().filter(o => o.id !== id);
  if (_structureOpen === id) _structureOpen = (estStructures()[0] || {}).id || '';
  setDirty(); rerender();
}
/* Which building's card is expanded on the Scope page. View state, not data. */
let _structureOpen = '';
function toggleStructureOpen(id) {
  _structureOpen = (_structureOpen === id) ? '' : id;
  if (activePage === 'scope') renderScopePage();
}

function syncStructureSections(trade) {
  const td = S.trades[trade];
  if (!td) return;
  td.sections = td.sections || [];
  tradeStructures(trade).forEach(st => {
    const n = String(st.name || '').trim();
    if (n && !td.sections.includes(n)) td.sections.push(n);
  });
}

const TEAM = ['avery','bryan','derik','luke','phil'];
const TRADE_COLOR_FIELDS = {
  roofing: [{key:'shingle_color',label:'Shingle Color'},{key:'manufacturer',label:'Manufacturer'},{key:'product_line',label:'Product Line'},
            {key:'drip_edge_color',label:'Drip Edge Color'},{key:'ridge_cap_color',label:'Ridge Cap Color'}],
  siding:  [{key:'siding_color',label:'Siding Color'},{key:'trim_color',label:'Trim Color'},{key:'manufacturer',label:'Manufacturer'}],
  windows: [{key:'frame_color',label:'Frame Color'},{key:'glass_package',label:'Glass Package'}],
  gutters: [{key:'gutter_color',label:'Gutter Color'},{key:'material',label:'Material'}],
  commercial: [{key:'membrane_color',label:'Membrane Color'},{key:'manufacturer',label:'Manufacturer'},{key:'system_type',label:'System'}],
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
Project One Roofing warrants all workmanship against defects for 5 years from the date of project completion on Good and Better packages, and for the lifetime of the homeowner's ownership of the home on the Best package. Manufacturer warranties will be registered in the homeowner's name upon receipt of final payment.

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
Project One Roofing warrants all workmanship against defects for 5 years from the date of project completion on standard scopes, and for the lifetime of the homeowner's ownership of the home when the Best package is selected. Manufacturer warranties will be registered in the homeowner's name upon receipt of final payment.

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
  // Company-wide defaults from ⚙ Settings win; hardcoded texts are fallback
  return globalInitialTexts(type).map(text => ({ id:'ini_'+uid(), text }));
}

/* ── Measurements engine ─────────────────────────────────────────────
   Raw measurements live on S.measurements. Line items may carry a
   `measure` key; their quantity then derives automatically. */
const MEASURE_FIELDS = [
  { group:'Roof', fields:[
    {key:'roof_squares',  label:'Roof Area',      unit:'SQ'},
    {key:'waste_pct',     label:'Waste',          unit:'%'},
    {key:'attic_sqft',    label:'Attic Area',     unit:'SF'},
    {key:'low_slope_squares', label:'Low Slope ≤2/12', unit:'SQ'},
    {key:'steep_squares',     label:'Steep 7/12+',     unit:'SQ'},
    {key:'predominant_pitch', label:'Predominant Pitch', unit:'/12'},
    {key:'ridge_hip_lf',  label:'Ridge + Hip',    unit:'LF'},
    {key:'ridge_lf',      label:'Ridges',         unit:'LF'},
    {key:'valley_lf',     label:'Valley',         unit:'LF'},
    {key:'eave_lf',       label:'Eaves',          unit:'LF'},
    {key:'rake_lf',       label:'Rakes',          unit:'LF'},
    {key:'step_flash_lf', label:'Step Flashing',  unit:'LF'},
    {key:'pipe_boots',    label:'Pipe Boots',     unit:'EA'},
    {key:'turtle_vents',  label:'Turtle Vents',   unit:'EA'},
    {key:'broan_4in',     label:'4" Broan Vent',  unit:'EA'},
    {key:'broan_8in',     label:'8" Broan Vent',  unit:'EA'},
  ]},
  { group:'Gutters', fields:[
    {key:'gutter_lf',     label:'Gutter',         unit:'LF'},
    {key:'downspout_lf',  label:'Downspouts',     unit:'LF'},
  ]},
  { group:'Siding', fields:[
    {key:'siding_squares',            label:'Siding Area',      unit:'SQ'},
    {key:'siding_waste_pct',          label:'Waste',            unit:'%'},
    {key:'siding_openings_count',     label:'Window + Door Openings', unit:'EA'},
    {key:'siding_outside_corners_lf', label:'Outside Corners',  unit:'LF'},
    {key:'siding_inside_corners_lf',  label:'Inside Corners',   unit:'LF'},
    // J-channel and trim boards are separate products at separate prices —
    // EDCO runs J-channel, LP and Hardie run 5/4 trim board — so the bundle
    // can only auto-fill the right one if the rep measured them apart.
    {key:'siding_j_channel_lf',       label:'J-Channel',        unit:'LF'},
    // Trim board split: sloped (rake/gable) runs long-cut so it eats more
    // waste (/0.84) than vertical (/0.85). Supplier's take-off sheet uses
    // this same split. The old siding_trim_lf survives as a fallback in
    // MEASURE_DEFS so in-flight estimates don't lose their trim total.
    {key:'siding_trim_sloped_lf',     label:'Trim — Sloped (rakes/gables)', unit:'LF'},
    {key:'siding_trim_vertical_lf',   label:'Trim — Vertical',              unit:'LF'},
    {key:'siding_trim_width_default', label:'Default Trim Width', unit:'in',
     opts:[[4,'4"'],[6,'6"'],[8,'8"'],[10,'10"'],[12,'12"']]},
    {key:'siding_starter_lf',         label:'Starter Strip',    unit:'LF'},
    {key:'siding_soffit_lf',          label:'Soffit',           unit:'LF'},
    // Soffit width is the SPEC the crew orders from. Widths ≤24" order LF
    // sticks; ≥25" routes to 4×8/9/10 panels (SF-driven) via siding_soffit_sf.
    {key:'siding_soffit_width',       label:'Soffit Width',     unit:'in',
     opts:[[12,'12"'],[16,'16"'],[24,'24"'],[36,'36"'],[48,'48"']]},
    // Full-perimeter vented soffit is standard, so default 100%. When it's
    // less than that (a soffit run that hits a vented section then a solid
    // section) the material order splits the LF into the two SKUs.
    {key:'siding_soffit_vented_pct',  label:'Soffit % Vented',  unit:'%'},
    // Fascia split into eaves vs rakes — supplier's take-off sheet uses this
    // same split. Old siding_fascia_lf survives as fallback in MEASURE_DEFS.
    {key:'siding_fascia_eaves_lf',    label:'Fascia — Eaves',   unit:'LF'},
    {key:'siding_fascia_rakes_lf',    label:'Fascia — Rakes',   unit:'LF'},
    // Frieze boards ride the sloped and level runs immediately under the
    // eaves and above every window/door — a real material line on every
    // LP/Hardie job and not previously collected.
    {key:'siding_frieze_eaves_lf',    label:'Frieze Board — Eaves (Sloped)', unit:'LF'},
    {key:'siding_frieze_level_lf',    label:'Frieze Board — Level',          unit:'LF'},
  ]},
  { group:'Windows', fields:[
    {key:'windows_count', label:'Windows', unit:'EA'},
    {key:'doors_count',   label:'Doors',   unit:'EA'},
  ]},
  // Commercial low-slope. Deliberately its own key namespace (comm_*) so a
  // commercial roof never inherits a steep-slope number left on the estimate.
  { group:'Commercial', fields:[
    {key:'comm_squares',      label:'Roof Area',        unit:'SQ'},
    {key:'comm_waste_pct',    label:'Waste',            unit:'%'},
    {key:'comm_perimeter_lf', label:'Perimeter / Edge', unit:'LF'},
    {key:'comm_parapet_lf',   label:'Parapet / Coping', unit:'LF'},
    {key:'comm_penetrations', label:'Penetrations',     unit:'EA'},
    {key:'comm_drains',       label:'Drains / Scuppers',unit:'EA'},
    {key:'comm_curbs',        label:'HVAC Curbs',       unit:'EA'},
    {key:'comm_skylights',    label:'Skylights/Hatches',unit:'EA'},
    {key:'comm_pitch_pans',   label:'Pitch Pans',       unit:'EA'},
    {key:'comm_walkway_pads', label:'Walkway Pads',     unit:'EA'},
    {key:'comm_sections',     label:'Roof Levels',      unit:'EA'},
    // panelOnly fields are real measurements (they persist, and MEASURE_DEFS
    // and custom formulas can read them) but the fastening panel renders them
    // itself — a bare number box for "Uplift" would hide that it is a menu.
    {key:'comm_length_ft',      label:'Building Length',  unit:'LF', panelOnly:true},
    {key:'comm_width_ft',       label:'Building Width',   unit:'LF', panelOnly:true},
    {key:'comm_height_ft',      label:'Building Height',  unit:'LF', panelOnly:true},
    {key:'comm_uplift',         label:'Uplift Rating',    unit:'psf', panelOnly:true},
    {key:'comm_insul_layers',   label:'Fastened Layers',  unit:'EA', panelOnly:true},
    {key:'comm_zone_field_sf',  label:'Zone: Field',      unit:'SF', panelOnly:true},
    {key:'comm_zone_perim_sf',  label:'Zone: Perimeter',  unit:'SF', panelOnly:true},
    {key:'comm_zone_corner_sf', label:'Zone: Corner',     unit:'SF', panelOnly:true},
  ]},
];
const MEASURE_DEFS = {
  squares:              { label:'Roof SQ',            calc:m => mnum(m.roof_squares) },
  attic_sqft:           { label:'Attic Area SF',      calc:m => mnum(m.attic_sqft) || mnum(m.roof_squares)*100 },
  // Ridge vent quantity is CODE-driven (the exhaust shortfall ÷ NFA per LF),
  // NOT the physical ridge length. bundle_lf:4 on the item then rounds this raw
  // LF up to whole 4-ft ridge-vent sticks. Returns 0 when venting already meets code.
  ridge_vent_code:      { label:'Ridge Vent — code required', calc:m => { const v = atticVentilation(m); return v.needs_ridge ? v.ridge_lf_required : 0; } },
  // Low-slope area is covered by rolled roofing, not shingles — the shingle/
  // underlayment quantity excludes it so the two lines never double-count.
  squares_waste:        { label:'Roof SQ + Waste (excl. low-slope)', calc:m => Math.max(mnum(m.roof_squares) - mnum(m.low_slope_squares), 0) * (1 + mnum(m.waste_pct, 10)/100) },
  low_slope:            { label:'Low Slope SQ (≤2/12)',   calc:m => mnum(m.low_slope_squares) },
  low_slope_waste:      { label:'Low Slope SQ + Waste',   calc:m => mnum(m.low_slope_squares) * (1 + mnum(m.waste_pct, 10)/100) },
  steep:                { label:'Steep SQ (7/12+)',       calc:m => mnum(m.steep_squares) },
  steep_waste:          { label:'Steep SQ + Waste',       calc:m => mnum(m.steep_squares) * (1 + mnum(m.waste_pct, 10)/100) },
  ridge_hip:            { label:'Ridge + Hip LF',     calc:m => mnum(m.ridge_hip_lf) },
  // Ridges alone (excludes hips) — ridge vent orders the full ridge off this.
  ridge_lf:             { label:'Ridge LF',           calc:m => mnum(m.ridge_lf) },
  valley:               { label:'Valley LF',          calc:m => mnum(m.valley_lf) },
  eave:                 { label:'Eave LF',            calc:m => mnum(m.eave_lf) },
  rake:                 { label:'Rake LF',            calc:m => mnum(m.rake_lf) },
  eave_rake:            { label:'Eave + Rake LF',     calc:m => mnum(m.eave_lf) + mnum(m.rake_lf) },
  // iw_second_row (0/1) doubles the eave run — a 2nd course of ice & water
  // barrier at the eaves where code requires it. Valleys are unaffected.
  eave_valley:          { label:'Eave + Valley LF',   calc:m => mnum(m.eave_lf) * (mnum(m.iw_second_row) ? 2 : 1) + mnum(m.valley_lf) },
  step:                 { label:'Step Flashing LF',   calc:m => mnum(m.step_flash_lf) },
  pipe_boots:           { label:'# Pipe Boots',       calc:m => mnum(m.pipe_boots) },
  skylights:            { label:'# Skylights',        calc:m => mnum(m.skylights) },
  turtle_vents:         { label:'# Turtle Vents',     calc:m => mnum(m.turtle_vents) },
  broan_4in:            { label:'# 4" Broan Vents',   calc:m => mnum(m.broan_4in) },
  broan_8in:            { label:'# 8" Broan Vents',   calc:m => mnum(m.broan_8in) },
  gutter:               { label:'Gutter LF',          calc:m => mnum(m.gutter_lf) },
  downspout:            { label:'Downspout LF',       calc:m => mnum(m.downspout_lf) },
  siding_squares:       { label:'Siding SQ',           calc:m => mnum(m.siding_squares) },
  siding_squares_waste: { label:'Siding SQ + Waste',   calc:m => mnum(m.siding_squares) * (1 + mnum(m.waste_pct, 10)/100) },
  siding_sq:            { label:'Siding SQ',           calc:m => mnum(m.siding_squares) },
  siding_sq_waste:      { label:'Siding SQ + Waste',   calc:m => mnum(m.siding_squares) * (1 + mnum(m.siding_waste_pct !== undefined ? m.siding_waste_pct : m.waste_pct, 10)/100) },
  corners_out:          { label:'Outside Corners LF',  calc:m => mnum(m.siding_outside_corners_lf) },
  corners_in:           { label:'Inside Corners LF',   calc:m => mnum(m.siding_inside_corners_lf) },
  j_channel:            { label:'J-Channel LF',        calc:m => mnum(m.siding_j_channel_lf) },
  // Trim reads the sloped + vertical split introduced with the supplier
  // take-off, and falls back to the pre-split single-field total when both
  // new fields are empty (an in-flight estimate saved before the split).
  siding_trim_sloped:   { label:'Trim Sloped LF',      calc:m => mnum(m.siding_trim_sloped_lf) },
  siding_trim_vertical: { label:'Trim Vertical LF',    calc:m => mnum(m.siding_trim_vertical_lf) },
  siding_trim:          { label:'Trim Board LF',       calc:m => {
    const split = mnum(m.siding_trim_sloped_lf) + mnum(m.siding_trim_vertical_lf);
    return split > 0 ? split : mnum(m.siding_trim_lf);
  } },
  siding_starter:       { label:'Starter Strip LF',    calc:m => mnum(m.siding_starter_lf) },
  // Fascia the same shape: eaves + rakes split, fallback to old single field.
  siding_fascia_eaves:  { label:'Fascia Eaves LF',     calc:m => mnum(m.siding_fascia_eaves_lf) },
  siding_fascia_rakes:  { label:'Fascia Rakes LF',     calc:m => mnum(m.siding_fascia_rakes_lf) },
  siding_fascia:        { label:'Fascia LF',           calc:m => {
    const split = mnum(m.siding_fascia_eaves_lf) + mnum(m.siding_fascia_rakes_lf);
    return split > 0 ? split : mnum(m.siding_fascia_lf);
  } },
  siding_frieze_eaves:  { label:'Frieze Eaves LF',     calc:m => mnum(m.siding_frieze_eaves_lf) },
  siding_frieze_level:  { label:'Frieze Level LF',     calc:m => mnum(m.siding_frieze_level_lf) },
  siding_frieze:        { label:'Frieze Total LF',     calc:m => mnum(m.siding_frieze_eaves_lf) + mnum(m.siding_frieze_level_lf) },
  siding_openings:      { label:'# Openings (win + door)', calc:m => mnum(m.siding_openings_count) },
  siding_soffit:        { label:'Soffit LF',           calc:m => mnum(m.siding_soffit_lf) },
  // Vented % defaults to 100 (a fully vented perimeter soffit is the norm).
  // Missing/junk keeps the identity (100), never 0: no vented soffit at all
  // is a genuine choice the rep types explicitly, not the default.
  siding_soffit_vented: { label:'Soffit Vented LF',    calc:m => mnum(m.siding_soffit_lf) * Math.min(Math.max(mnum(m.siding_soffit_vented_pct, 100), 0), 100) / 100 },
  siding_soffit_solid:  { label:'Soffit Solid LF',     calc:m => mnum(m.siding_soffit_lf) * (100 - Math.min(Math.max(mnum(m.siding_soffit_vented_pct, 100), 0), 100)) / 100 },
  // SF/SQ views. Width missing means 12" (identity), NEVER 0 — that would
  // zero the line while the Scope page still showed a run filled in.
  siding_soffit_sf:     { label:'Soffit SF',           calc:m => mnum(m.siding_soffit_lf) * (mnum(m.siding_soffit_width, 12) || 12) / 12 },
  siding_soffit_sq:     { label:'Soffit SQ',           calc:m => mnum(m.siding_soffit_lf) * (mnum(m.siding_soffit_width, 12) || 12) / 12 / 100 },
  // Z-flash for window flashing — one 10' stick per ~4 openings, per the
  // supplier take-off (b_openings / 4 / 10 * 10 → openings / 4). Rounded up
  // on the item via ceil (measuredQty rounds EA up whole).
  siding_zflash:        { label:'Z-Flash EA',          calc:m => mnum(m.siding_openings_count) / 4 },
  windows:              { label:'# Windows',           calc:m => mnum(m.windows_count) },
  doors:                { label:'# Doors',             calc:m => mnum(m.doors_count) },
  comm_sq:              { label:'Commercial SQ',            calc:m => mnum(m.comm_squares) },
  comm_sq_waste:        { label:'Commercial SQ + Waste',    calc:m => mnum(m.comm_squares) * (1 + mnum(m.comm_waste_pct, 10)/100) },
  comm_perimeter:       { label:'Perimeter LF',             calc:m => mnum(m.comm_perimeter_lf) },
  comm_parapet:         { label:'Parapet / Coping LF',      calc:m => mnum(m.comm_parapet_lf) },
  comm_penetrations:    { label:'# Penetrations',           calc:m => mnum(m.comm_penetrations) },
  comm_drains:          { label:'# Drains / Scuppers',      calc:m => mnum(m.comm_drains) },
  // Curbs and skylights/hatches both take the same curb-flashing detail.
  comm_curbs:           { label:'# Curbs + Skylights',      calc:m => mnum(m.comm_curbs) + mnum(m.comm_skylights) },
  comm_pitch_pans:      { label:'# Pitch Pans',             calc:m => mnum(m.comm_pitch_pans) },
  comm_walkway_pads:    { label:'# Walkway Pads',           calc:m => mnum(m.comm_walkway_pads) },
  // Both labor lines ship in every commercial bundle; comm_work_type (0/1)
  // zeroes the one that doesn't apply, and zero-qty items never price.
  comm_labor_reroof:    { label:'Labor SQ — Re-Roof',       calc:m => mnum(m.comm_work_type) ? 0 : mnum(m.comm_squares) },
  comm_labor_new:       { label:'Labor SQ — New Const.',    calc:m => mnum(m.comm_work_type) ? mnum(m.comm_squares) : 0 },
  // Zone-based fastener counts. Both return 0 (and the panel shouts) until the
  // building dimensions and an uplift rating are entered — see commercialFastening.
  comm_fast_insul:      { label:'# Insulation Fasteners',   calc:m => commercialFastening(m, _fastenTable).insul.total },
  comm_fast_seam:       { label:'# Seam Fasteners',         calc:m => commercialFastening(m, _fastenTable).seam.total },
};
function mnum(v, dflt) {
  const n = parseFloat(v);
  return isNaN(n) ? (dflt || 0) : n;
}

/* ── Attic ventilation calculator ───────────────────────────────────────
   Code rule: 1 sq ft of net free area (NFA) per 300 sq ft of attic (the
   balanced 1/300 rule), split 50% exhaust / 50% intake. Turtle/box vents are
   the exhaust the rep already counts; when they fall short the roof needs
   ridge vent + intake. Net-free-area values per vent type are fixed defaults.
   MUST mirror atticVentilation() in app.py. */
const NFA_TURTLE_SQIN    = 50;   // net free area per turtle/box vent
const NFA_RIDGE_SQIN_LF  = 18;   // net free area per LF of ridge vent
const NFA_INTAKE_SQIN_LF = 9;    // net free area per LF of soffit/intake vent
const VENT_RULE_DIVISOR  = 300;  // 1/300 balanced rule
function atticVentilation(m) {
  m = m || {};
  const attic = mnum(m.attic_sqft) || mnum(m.roof_squares) * 100;
  const required_total   = attic > 0 ? (attic / VENT_RULE_DIVISOR) * 144 : 0; // sq in
  const required_exhaust = required_total / 2;
  const required_intake  = required_total / 2;
  const provided_exhaust = mnum(m.turtle_vents) * NFA_TURTLE_SQIN;
  const deficit_exhaust  = Math.max(required_exhaust - provided_exhaust, 0);
  const needs_ridge  = deficit_exhaust > 0;
  const needs_intake = needs_ridge; // balanced rule: add intake whenever adding exhaust
  const ridge_lf_required   = needs_ridge  ? deficit_exhaust / NFA_RIDGE_SQIN_LF : 0; // raw LF
  const ridge_sticks        = Math.ceil(ridge_lf_required / 4);                        // 4-ft sticks
  const intake_lf_suggested = needs_intake ? Math.ceil(required_intake / NFA_INTAKE_SQIN_LF) : 0;
  return { attic_sqft:attic, required_total, required_exhaust, required_intake,
           provided_exhaust, deficit_exhaust, needs_ridge, needs_intake,
           ridge_lf_required, ridge_sticks, intake_lf_suggested };
}

/* ── Commercial fastener calculator ─────────────────────────────────────
   Fastener density on a low-slope roof is set by WHERE on the roof you are
   (ASCE 7 field / perimeter / corner), by how much uplift the roof must
   resist, and by which layer is being fastened. A corner can take 2-3x the
   field density, so one number per square either overbuys the whole roof or
   under-fastens the corners.

   MUST mirror commercial_fastening() in app.py — and unlike atticVentilation,
   this pair IS parity-tested (tests/test_fastening.py runs both over the same
   fixtures). The table is passed in rather than read from module state so the
   function stays pure and both sides can be driven from identical JSON.

   When it cannot know the answer it returns zeros with ok:false. It never
   returns a plausible guess. */
const FASTEN_ZONES = ['field','perimeter','corner'];
function _asceZoneWidth(least, h, rule) {
  // ASCE 7 zone width `a`: 10% of the least horizontal dimension or 40% of the
  // mean roof height, whichever is SMALLER, floored at 4% of the least
  // dimension and an absolute minimum (3 ft in ASCE, 4 ft in some FM
  // approvals), capped at half the least dimension so zones cannot overlap.
  rule = rule || {};
  if (least <= 0 || h <= 0) return 0;
  let a = Math.min(mnum(rule.a_pct_least, 0.10) * least,
                   mnum(rule.a_pct_height, 0.40) * h);
  a = Math.max(a, mnum(rule.a_min_pct_least, 0.04) * least, mnum(rule.a_min_ft, 3));
  return Math.min(a, least / 2);
}
function commercialFastening(m, table) {
  m = m || {};
  const t = table || {};
  const rule    = t.zone_rule || {};
  const boardSf = mnum(t.board_sf, 32) || 32;
  const waste   = mnum(t.waste_pct, 0);
  const ratings = t.ratings || {};
  const warnings = [];
  const zeroLayer = () => {
    const bz = {}; FASTEN_ZONES.forEach(z => { bz[z] = { count: 0 }; });
    return { applies:false, by_zone:bz, raw:0, total:0 };
  };
  const bail = reason => {
    const zs = {}; FASTEN_ZONES.forEach(z => { zs[z] = { sf:0, source:'none' }; });
    return { ok:false, reason, a:0,
             rating:null, rating_requested:mnum(m.comm_uplift), rating_label:'', rating_note:'',
             zones:zs, zone_source:'none',
             area_check:{ bbox_sf:0, measured_sf:0, delta_pct:0, warn:false },
             layers:0, board_sf:boardSf, waste_pct:waste,
             insul:zeroLayer(), seam:zeroLayer(), warnings };
  };

  if (!Object.keys(ratings).length) return bail('no_table');

  // Uplift: exact match, else the smallest published row AT OR ABOVE what was
  // asked for. Never round down — that under-fastens. Keys are JSON strings, so
  // sort numerically ("105" sorts below "60" as text).
  const requested = mnum(m.comm_uplift);
  if (requested <= 0) return bail('no_uplift_rating');
  const keys = Object.keys(ratings).filter(k => /^-?\d+$/.test(String(k)))
    .map(Number).sort((x, y) => x - y);
  if (!keys.length) return bail('no_table');
  let chosen = keys.find(k => k >= requested);
  let ratingNote = '';
  if (chosen === undefined) {
    chosen = keys[keys.length - 1];
    ratingNote = `No published row at or above ${requested} psf — using the highest available (${chosen} psf). Verify against the system approval.`;
    warnings.push(ratingNote);
  }
  const row = ratings[String(chosen)] || {};

  // Zone areas: a manual override wins, else computed from the bounding box.
  const L = mnum(m.comm_length_ft), W = mnum(m.comm_width_ft), H = mnum(m.comm_height_ft);
  const ov = { field: mnum(m.comm_zone_field_sf), perimeter: mnum(m.comm_zone_perim_sf),
               corner: mnum(m.comm_zone_corner_sf) };
  const hasOv = FASTEN_ZONES.some(z => ov[z] > 0);

  let a = 0;
  const comp = { field:0, perimeter:0, corner:0 };
  const bbox = L * W;
  if (L > 0 && W > 0 && H > 0) {
    a = _asceZoneWidth(Math.min(L, W), H, rule);
    const band = bbox - Math.max(L - 2*a, 0) * Math.max(W - 2*a, 0);
    // ASCE 7-10 corners are a square a x a (4a^2 total); ASCE 7-16 redrew them
    // as an L (two 2a x a legs) = 3a^2 each, 12a^2 total. 3x in the zone that
    // matters most, so it is a table setting, not a constant.
    let corner = ((rule.corner_shape || 'L') === 'L' ? 12 : 4) * a * a;
    corner = Math.min(corner, band);
    comp.field     = Math.max(bbox - band, 0);
    comp.perimeter = Math.max(band - corner, 0);
    comp.corner    = corner;
  } else if (!hasOv) {
    return bail('missing_dimensions');
  }

  const zones = {}, sources = [];
  FASTEN_ZONES.forEach(z => {
    if (ov[z] > 0) { zones[z] = { sf: ov[z], source:'override' }; sources.push('override'); }
    else           { zones[z] = { sf: comp[z], source:'computed' }; sources.push('computed'); }
  });
  const zoneSource = sources.every(s => s === sources[0]) ? sources[0] : 'mixed';
  const totalZoneSf = FASTEN_ZONES.reduce((s, z) => s + zones[z].sf, 0);
  if (totalZoneSf <= 0) return bail('missing_dimensions');

  // The bounding box is an independent second area from the measured roof. On
  // an L-shaped building it is bigger AND the real roof has more corners, so
  // surface the gap rather than scaling anything.
  const measuredSf = mnum(m.comm_squares) * 100;
  const ref = bbox > 0 ? bbox : totalZoneSf;
  let deltaPct = 0, areaWarn = false;
  if (measuredSf > 0 && ref > 0) {
    deltaPct = Math.abs(ref - measuredSf) / ref * 100;
    if (deltaPct > 10) {
      areaWarn = true;
      warnings.push(`Bounding box (${Math.round(ref).toLocaleString()} SF) differs from the measured roof area (${Math.round(measuredSf).toLocaleString()} SF) by ${deltaPct.toFixed(0)}% — this roof is not a rectangle. Enter zone areas manually.`);
    }
  }
  if (hasOv && ref > 0 && Math.abs(totalZoneSf - ref) / ref * 100 > 2) {
    warnings.push(`Zone areas sum to ${Math.round(totalZoneSf).toLocaleString()} SF but the roof is ${Math.round(ref).toLocaleString()} SF — check the override values.`);
  }
  if (mnum(m.comm_sections) >= 2) {
    warnings.push('Multiple roof levels — zones are computed for ONE rectangle. Enter zone areas manually for a stepped or multi-level roof.');
  }

  // A cleared field stores an explicit 0, which legitimately means "recover, no
  // new insulation" — so MISSING means 1, but 0 means 0.
  const layers = (m.comm_insul_layers === undefined || m.comm_insul_layers === null ||
                  m.comm_insul_layers === '') ? 1 : mnum(m.comm_insul_layers);

  // Attachment is resolved from the bundle's products and stored as a
  // measurement (see _syncCommAttachment) so this stays pure. Absent = assume
  // insulation applies, and FAIL CLOSED on seam: an adhered system has no seam
  // fasteners, and guessing yes would put thousands of phantom screws on a bid.
  const insulApplies = (m.comm_insul_attach === undefined || m.comm_insul_attach === null ||
                        m.comm_insul_attach === '') || mnum(m.comm_insul_attach) > 0;
  const seamApplies  = mnum(m.comm_seam_attach) > 0;

  const perBoard = row.insul_per_board || {};
  const seamSpec = row.seam || {};
  const insul = { applies: !!(insulApplies && layers > 0), by_zone:{}, raw:0, total:0 };
  const seam  = { applies: !!seamApplies, by_zone:{}, raw:0, total:0 };

  FASTEN_ZONES.forEach(z => {
    const sf = zones[z].sf;
    const pb = mnum(perBoard[z]);
    const boards = boardSf > 0 ? sf / boardSf : 0;
    const cnt = insul.applies ? boards * pb * layers : 0;
    insul.by_zone[z] = { boards, per_board: pb, count: cnt };
    insul.raw += cnt;

    const spec = seamSpec[z] || {};
    const sw = mnum(spec.sheet_width_ft), sp = mnum(spec.spacing_in);
    // Tributary area per fastener. A run of length L at spacing s truly has
    // L/s + 1 fasteners; at roof scale the +1 is swamped by waste_pct.
    const perFast = (sw > 0 && sp > 0) ? sw * (sp / 12) : 0;
    const scnt = (seam.applies && perFast > 0) ? sf / perFast : 0;
    seam.by_zone[z] = { sf_per_fastener: perFast, spacing_in: sp, sheet_width_ft: sw, count: scnt };
    seam.raw += scnt;
  });
  [insul, seam].forEach(layer => {
    layer.total = Math.ceil(layer.raw * (1 + waste / 100) - 1e-9);
  });

  return {
    ok:true, reason:'', a,
    rating: chosen, rating_requested: requested,
    rating_label: row.label || (chosen + ' psf'), rating_note: ratingNote,
    zones, zone_source: zoneSource,
    area_check: { bbox_sf: bbox, measured_sf: measuredSf, delta_pct: deltaPct, warn: areaWarn },
    layers, board_sf: boardSf, waste_pct: waste,
    insul, seam, warnings,
  };
}

/* The fastening table is DATA served by /api/commercial-fastening, not a
   constant mirrored on both sides — that is what keeps app.js and app.py from
   drifting the way the ventilation constants can. Lazy-loaded like
   _jurisdictions; the .finally() re-derives quantities so the momentary
   pre-fetch zero corrects itself within one round trip. */
let _fastenTable = null;
let _fastenTableLoading = false;
function _ensureFastenTable() {
  if (_fastenTable || _fastenTableLoading) return;
  _fastenTableLoading = true;
  fetch('/api/commercial-fastening')
    .then(r => r.json())
    .then(d => { _fastenTable = d || {}; })
    .catch(() => { _fastenTable = { ratings:{}, zone_rule:{}, board_sf:32, waste_pct:0 }; })
    .finally(() => {
      _fastenTableLoading = false;
      if (S && S.trades) { applyMeasurements(); }
      if (activePage === 'scope') renderScopePage();
    });
}
/* Zone geometry is a fact about ONE building -- its length, width, height and
   uplift rating -- so the panel calculates per structure. The measure keys
   comm_fast_insul / comm_fast_seam need no such change: they are already pure
   functions of the dict measuredQty() hands them, which is now the building's. */
function commFastening(structureId) {
  return commercialFastening(structureMeasurements(structureId), _fastenTable);
}

/* ── Fastening panel (Scope page, commercial only) ──────────────────────
   Shows the zone geometry, the density row in force, and the resulting
   counts — broken out per zone, because the corner spacing is what the crew
   actually lays out by. When it cannot calculate it says so in red and shows
   zero; it never fills the gap with a plausible number. */
const _FASTEN_ZONE_LABELS = { field:'Field', perimeter:'Perimeter', corner:'Corner' };
const _FASTEN_BAIL_TEXT = {
  no_uplift_rating: 'Pick an uplift rating below — it decides every fastener count on this roof.',
  missing_dimensions: 'Enter building length, width, and height below (or type the zone areas in directly).',
  no_table: 'No fastening table is configured. A manager can set one in ⚙ Settings.',
};
function setCommUplift(v, sid)          { setMeasurement('comm_uplift', v, sid); if (activePage === 'scope') renderScopePage(); }
function setCommFastenNum(key, v, sid)  { setMeasurement(key, v, sid); if (activePage === 'scope') renderScopePage(); }
function fastenPanelMarkup(sid) {
  if (!S.trades.commercial || !S.trades.commercial.enabled) return '';
  _ensureFastenTable();
  const m = structureMeasurements(sid);
  const sarg = `, '${sid || ''}'`;
  if (_fastenTableLoading || !_fastenTable) {
    return `<div class="measure-panel measure-panel-fasten">
      <div class="measure-panel-head"><h3>🔩 Fastening Schedule</h3>
        <span class="measure-hint">Loading the fastening table…</span></div></div>`;
  }
  const r = commFastening(sid);
  const t = _fastenTable;
  const attach = _commAttachProfile('commercial');
  const num = (k, lbl, unit, ph) => `
    <div class="measure-field">
      <label>${lbl}</label>
      <div class="measure-input-wrap">
        <input type="number" min="0" step="0.1" value="${m[k] || ''}" placeholder="${ph || '0'}"
          onchange="setCommFastenNum('${k}', this.value${sarg})">
        <span class="measure-unit">${unit}</span>
      </div>
    </div>`;

  const ratingKeys = Object.keys(t.ratings || {}).filter(k => /^-?\d+$/.test(k))
    .map(Number).sort((a, b) => a - b);
  const cur = mnum(m.comm_uplift);
  const upliftSel = `
    <div class="measure-field">
      <label>Uplift Rating <span class="fasten-req">required</span></label>
      <select class="fasten-select ${cur ? '' : 'is-empty'}" onchange="setCommUplift(this.value${sarg})">
        <option value="0" ${cur ? '' : 'selected'}>Choose…</option>
        ${ratingKeys.map(k => `<option value="${k}" ${cur === k ? 'selected' : ''}>${esc((t.ratings[String(k)] || {}).label || (k + ' psf'))}</option>`).join('')}
      </select>
    </div>`;

  const bail = !r.ok ? `
    <div class="fasten-warn fasten-warn-hard">
      <strong>Fastener quantities are 0 — not calculated.</strong>
      ${esc(_FASTEN_BAIL_TEXT[r.reason] || 'Missing input.')}
    </div>` : '';

  const zoneRows = !r.ok ? '' : `
    <table class="fasten-table">
      <thead><tr>
        <th>Zone</th><th class="fz-num">Area</th><th class="fz-num">Plates/board</th>
        <th class="fz-num">Insulation</th><th class="fz-num">Seam spacing</th><th class="fz-num">Seam</th>
      </tr></thead>
      <tbody>
        ${FASTEN_ZONES.map(z => {
          const zi = r.zones[z], ins = r.insul.by_zone[z], se = r.seam.by_zone[z];
          return `<tr>
            <td>${_FASTEN_ZONE_LABELS[z]}
              <span class="fz-src fz-src-${zi.source}">${zi.source === 'override' ? 'manual' : 'computed'}</span></td>
            <td class="fz-num">${Math.round(zi.sf).toLocaleString()} SF</td>
            <td class="fz-num">${r.insul.applies ? ins.per_board : '—'}</td>
            <td class="fz-num">${r.insul.applies ? Math.ceil(ins.count).toLocaleString() : '—'}</td>
            <td class="fz-num">${r.seam.applies ? se.spacing_in + '" o.c. / ' + se.sheet_width_ft + "' sheet" : '—'}</td>
            <td class="fz-num">${r.seam.applies ? Math.ceil(se.count).toLocaleString() : '—'}</td>
          </tr>`;
        }).join('')}
      </tbody>
      <tfoot><tr>
        <td colspan="3">Total incl. ${r.waste_pct}% waste</td>
        <td class="fz-num"><strong>${r.insul.applies ? r.insul.total.toLocaleString() : '0'}</strong></td>
        <td></td>
        <td class="fz-num"><strong>${r.seam.applies ? r.seam.total.toLocaleString() : '0'}</strong></td>
      </tr></tfoot>
    </table>`;

  // Say WHY a layer is zero, rather than showing a confident 0.
  const layerNotes = !r.ok ? '' : [
    !r.seam.applies && attach.source === 'adhered'
      ? 'Seam fasteners: not applicable — this is a fully adhered system.' : '',
    !r.seam.applies && attach.source === 'coating'
      ? 'Coating system — no insulation or membrane fasteners on a restoration recover.' : '',
    !r.seam.applies && attach.source === 'unknown'
      ? '⚠️ This system\'s membrane is not tagged mechanical or adhered, so seam fasteners are NOT counted. If it is mechanically attached, tag the membrane product in the Price Book.' : '',
    !r.insul.applies && r.layers === 0
      ? 'Insulation fasteners: 0 — no fastened layers entered (recover over the existing roof).' : '',
    _commBundleId('commercial').indexOf('modbit') > -1
      ? 'Mod-bit base sheet fastening is NOT calculated here — it runs at a different density. Add it by hand.' : '',
  ].filter(Boolean).map(s => `<div class="fasten-note">${s}</div>`).join('');

  const warnings = (r.warnings || []).map(w => `<div class="fasten-warn">${esc(w)}</div>`).join('');

  const zoneStat = !r.ok ? '' : `
    <div class="fasten-stat">
      <span class="fs-label">Zone width <em>a</em></span>
      <span class="fs-val">${r.a.toFixed(1)} ft</span>
      <span class="fs-sub">${esc((t.zone_rule || {}).standard || '')}${(t.zone_rule || {}).corner_shape === 'L' ? ' · L-shaped corners' : ' · square corners'}</span>
    </div>
    <div class="fasten-stat">
      <span class="fs-label">Density row</span>
      <span class="fs-val">${esc(r.rating_label)}</span>
      <span class="fs-sub">${r.rating_requested !== r.rating ? 'rounded up from ' + r.rating_requested + ' psf' : 'exact match'}</span>
    </div>`;

  return `
    <div class="measure-panel measure-panel-fasten">
      <div class="measure-panel-head">
        <h3>🔩 Fastening Schedule</h3>
        <span class="measure-hint">Fastener counts by roof zone — corners take 2–3× the field density</span>
      </div>
      ${bail}
      <div class="measure-groups">
        <div class="measure-group">
          <div class="measure-group-title">Building &amp; Uplift</div>
          <div class="measure-fields">
            ${num('comm_length_ft', 'Length', 'LF')}
            ${num('comm_width_ft', 'Width', 'LF')}
            ${num('comm_height_ft', 'Height', 'LF')}
            ${upliftSel}
            ${num('comm_insul_layers', 'Fastened Layers', 'EA', '1')}
          </div>
        </div>
        <div class="measure-group">
          <div class="measure-group-title">Zone Areas — leave blank to calculate from the dimensions</div>
          <div class="measure-fields">
            ${num('comm_zone_field_sf', 'Field', 'SF', 'auto')}
            ${num('comm_zone_perim_sf', 'Perimeter', 'SF', 'auto')}
            ${num('comm_zone_corner_sf', 'Corner', 'SF', 'auto')}
          </div>
        </div>
      </div>
      <div class="fasten-stats">${zoneStat}</div>
      ${zoneRows}
      ${layerNotes}
      ${warnings}
      <div class="fasten-foot">
        <span class="fasten-disclaimer">${esc(t.source_note || '')}</span>
        ${_meCanViewAll && _meCanViewAll() ? `<button class="fasten-edit-link" onclick="openSettings()">⚙ Edit fastening table</button>` : ''}
      </div>
    </div>`;
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
/* Company-wide contract + initials defaults (⚙ Settings, admin) — every NEW
   estimate seeds from these; the hardcoded texts are only the fallback when
   nothing has been saved yet. */
/* The estimate types that own their own contract + initials. Anything else
   (including a missing type) is retail. Commercial has its own Settings slots
   so a manager can paste real commercial T&Cs, but falls back to the retail
   text until they do — there is no stock commercial contract to ship. */
const CONTRACT_TYPES = ['insurance','commercial'];
function _ctype(t) { return CONTRACT_TYPES.includes(t) ? t : 'retail'; }
function globalContract(type) {
  const t = { insurance: appSettings.contract_insurance,
              commercial: appSettings.contract_commercial }[_ctype(type)]
            ?? appSettings.contract_retail;
  return (t || '').trim() ? t : (type === 'insurance' ? DEFAULT_INSURANCE_CONTRACT : DEFAULT_CONTRACT);
}
function globalInitialTexts(type) {
  const list = { insurance: appSettings.initials_insurance,
                 commercial: appSettings.initials_commercial }[_ctype(type)]
               ?? appSettings.initials_retail;
  const clean = (list || []).map(s => String(s).trim()).filter(Boolean);
  return clean.length ? clean : (type === 'insurance' ? DEFAULT_INITIALS_INSURANCE : DEFAULT_INITIALS_RETAIL);
}
/* True when the text is one of the untouched stock contracts (hardcoded or
   the saved global default) — used to decide whether a type switch may safely
   swap the contract out. EVERY type's default must be listed here: a type
   missing from this list makes its own default look rep-edited, and the switch
   away from it silently keeps the wrong contract. */
function isStockContract(text) {
  if (!(text || '').trim()) return true;
  return [DEFAULT_CONTRACT, DEFAULT_INSURANCE_CONTRACT,
          globalContract('retail'), globalContract('insurance'),
          globalContract('commercial')].includes(text);
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
  const m = itemMeasurements(item);
  let raw;
  if (item.formula) {
    raw = evalFormula(item.formula, m);
  } else {
    const def = MEASURE_DEFS[item.measure];
    if (!def) return null;
    raw = def.calc(m);
  }
  if (!raw) return 0;
  // bundle_lf: convert raw LF to bundles/sticks — ceil(LF / LF-per-unit).
  const bl = item.bundle_lf;
  if (bl && bl > 0) return Math.ceil(raw / bl - 1e-9);
  // Squares keep one decimal (rounded up); linear feet and counts round up whole.
  // The 1e-9 guards against float noise (28 × 1.1 = 30.800000000000004).
  return (item.unit === 'SQ') ? Math.ceil(raw * 10 - 1e-9) / 10 : Math.ceil(raw - 1e-9);
}
function displayUnit(item) {
  return (item.bundle_lf && item.bundle_unit) ? item.bundle_unit : (item.unit || '');
}
function applyMeasurements() {
  const applyTrade = td => (td && td.line_items || []).forEach(item => {
    const q = measuredQty(item);
    if (q !== null) item.quantity = q;
  });
  applyTrade(S.trades.roofing);
  applyTrade(S.trades.siding);
  applyTrade(S.trades.commercial);
  setDirty();
  renderTotals();
}
const SIDING_MEAS_KEYS = new Set([
  'siding_squares','siding_waste_pct','siding_openings_count',
  'siding_outside_corners_lf','siding_inside_corners_lf','siding_j_channel_lf',
  'siding_trim_sloped_lf','siding_trim_vertical_lf','siding_trim_width_default',
  'siding_starter_lf','siding_soffit_lf','siding_soffit_width','siding_soffit_vented_pct',
  'siding_fascia_eaves_lf','siding_fascia_rakes_lf',
  'siding_frieze_eaves_lf','siding_frieze_level_lf',
  // Legacy pre-split fields still trigger auto-build so an in-flight estimate
  // opened after the change keeps working when the rep enters the OLD fields.
  'siding_trim_lf','siding_fascia_lf',
]);
const COMMERCIAL_MEAS_KEYS = new Set([
  'comm_squares','comm_waste_pct','comm_perimeter_lf','comm_parapet_lf',
  'comm_penetrations','comm_drains','comm_curbs','comm_skylights',
  'comm_pitch_pans','comm_walkway_pads','comm_sections','comm_work_type',
  'comm_length_ft','comm_width_ft','comm_height_ft','comm_uplift',
  'comm_insul_layers','comm_zone_field_sf','comm_zone_perim_sf','comm_zone_corner_sf',
  'comm_seam_attach','comm_insul_attach',
]);
const WINDOWS_MEAS_KEYS = new Set(['windows_count','doors_count']);
/* `structureId` addresses ONE building's numbers; omitted (or '') writes the
   estimate's own, which is every measurement on a single-building job. */
function setMeasurement(key, v, structureId) {
  structureMeasurements(structureId)[key] = parseFloat(v) || 0;
  // Auto-build trade defaults on first measurement entry when trade is enabled + empty.
  const rd = S.trades.roofing;
  let built = false;
  if (rd && rd.enabled && (!rd.line_items || rd.line_items.length === 0)) {
    buildBundleDefaults('roofing');   // roofing is bundle-driven, not buildTradeDefaults
    built = true;
  }
  if (SIDING_MEAS_KEYS.has(key)) {
    const sd = S.trades.siding;
    if (sd && sd.enabled && (!sd.line_items || sd.line_items.length === 0)) {
      buildBundleDefaults('siding');   // siding is bundle-driven now, not buildTradeDefaults
      built = true;
    }
  }
  if (COMMERCIAL_MEAS_KEYS.has(key)) {
    const cd = S.trades.commercial;
    if (cd && cd.enabled && (!cd.line_items || cd.line_items.length === 0)) {
      buildBundleDefaults('commercial');
      built = true;
    }
  }
  if (WINDOWS_MEAS_KEYS.has(key)) {
    const wd = S.trades.windows;
    if (wd && wd.enabled && (!wd.line_items || wd.line_items.length === 0)) {
      buildBundleDefaults('windows');
      built = true;
    }
  }
  applyMeasurements();
  if (built) {
    if (activePage === 'scope') renderScopePage();
    rerender();
    return;
  }
  // Refresh visible qty cells without rebuilding the whole page (keeps focus)
  document.querySelectorAll('.scope-qty-input[data-measured="1"]').forEach(inp => {
    const item = findItem(inp.dataset.trade, inp.dataset.id);
    if (item) inp.value = item.quantity || '';
  });
  // Live-refresh the ventilation panel (status/figures/warnings) in place — its
  // only inputs are checkboxes, so replacing it doesn't disturb the measurement
  // field being typed into.
  const vp = document.querySelector('.measure-panel-vent');
  if (vp) { const html = ventPanelMarkup(); if (html) vp.outerHTML = html; else vp.remove(); }
  // Same for the fastening panel — its counts change with almost every
  // commercial measurement, so it has to keep up while the rep is typing.
  // On a complex there is one panel per building: scope the swap to the card
  // being typed into, or building 1's schedule gets replaced by whichever
  // building the rep is actually editing.
  const card = structureId ? document.querySelector(`.bld-card[data-st="${structureId}"]`) : null;
  const fp = (card || document).querySelector('.measure-panel-fasten');
  if (fp) { const html = fastenPanelMarkup(structureId); if (html) fp.outerHTML = html; else fp.remove(); }
  // The collapsed head carries this building's roof area and price. Leaving it
  // stale while the rep types the roof area into the box right below it reads
  // as the number not registering.
  if (card) {
    const facts = card.querySelector('.bld-facts');
    const st = findStructure(structureId);
    if (facts && st) {
      const sq = mnum((st.measurements || {}).comm_squares);
      facts.innerHTML = `<span class="${sq ? '' : 'bld-fact-empty'}">${sq ? sq + ' SQ' : 'no roof area yet'}</span>`
                      + `<span class="bld-total">${fmtCur(structureTotal(st))}</span>`;
    }
  }
}
function setIwSecondRow(on) {
  // Stored as 0/1 in measurements so it survives save/load and is available
  // as a variable in custom formulas (evalFormula reads S.measurements).
  setMeasurement('iw_second_row', on ? 1 : 0);
  // Re-render so the toggle's highlight state follows the checkbox.
  if (activePage === 'scope') renderScopePage();
}

/* ── Commercial: job type + complexity flags ────────────────────────────
   comm_work_type lives in measurements (0 = re-roof, 1 = new construction) for
   the same reason iw_second_row does: MEASURE_DEFS and custom formulas can only
   see measurements. It drives which of the two labor lines carries quantity —
   the other goes to 0 and a zero-qty line never prices and never prints. */
function setCommWorkType(v, sid) {
  setMeasurement('comm_work_type', v ? 1 : 0, sid);
  if (activePage === 'scope') renderScopePage();
}
// Rep-facing only — these never touch price. They ride along to the production
// packet so the crew and the scheduler see what makes the job hard.
const COMM_FLAGS = [
  { key:'penetrations_10plus', label:'10+ penetrations',        auto:m => mnum(m.comm_penetrations) >= 10 },
  { key:'levels_3plus',        label:'3+ roof levels / sections', auto:m => mnum(m.comm_sections) >= 3 },
  { key:'expansion_joints',    label:'Expansion joints' },
  { key:'heavy_hvac',          label:'Heavy rooftop HVAC' },
  // These two rule a LAYOVER out: IBC does not permit a recover over two or
  // more existing coverings, and GAF calls for tear-off once the existing
  // assembly is wet. Either one means the job is a tear-off regardless of
  // what the customer would rather pay for. MUST mirror COMM_FLAG_LABELS.
  { key:'existing_layers_2plus', label:'Existing roof already has 2+ coverings (no recover)' },
  { key:'wet_insulation',        label:'Ponding / suspected wet insulation' },
];
function commState() {
  if (!S.commercial || typeof S.commercial !== 'object') S.commercial = { flags:{}, notes:'' };
  if (!S.commercial.flags || typeof S.commercial.flags !== 'object') S.commercial.flags = {};
  return S.commercial;
}
function setCommFlag(key, on) {
  commState().flags[key] = !!on;
  setDirty();
  if (activePage === 'scope') renderScopePage();
}
function setCommNotes(v) { commState().notes = v; setDirty(); }
function commComplexityMarkup() {
  const c = commState(), m = S.measurements || {};
  return `
    <div class="comm-complexity">
      <div class="comm-complexity-head">
        <strong>🔶 Job complexity</strong>
        <span class="comm-complexity-hint">Internal only — never shown to the customer. Flag what may warrant a labor premium.</span>
      </div>
      <div class="comm-complexity-flags">
        ${COMM_FLAGS.map(f => {
          const on = !!c.flags[f.key];
          // Suggest, never decide: the measurements say it looks complex, but
          // whether that changes the price is the rep's call.
          const suggest = !on && f.auto && f.auto(m);
          return `<label class="comm-flag ${on?'enabled':''} ${suggest?'suggested':''}">
            <input type="checkbox" ${on?'checked':''} onchange="setCommFlag('${f.key}',this.checked)">
            ${f.label}${suggest?' <span class="comm-flag-suggest">← measurements suggest this</span>':''}
          </label>`;
        }).join('')}
      </div>
      <textarea class="comm-complexity-notes" rows="2" placeholder="Access, staging, crane, occupied building, night work…"
        onchange="setCommNotes(this.value)">${esc(c.notes || '')}</textarea>
    </div>`;
}

/* ── Ventilation panel: drive the vent line items ───────────────────────
   The two checkboxes ARE their line items — checkbox state is inferred from
   the presence of a tagged item (item.vent_role), so it persists with the
   estimate for free and can't desync. Product NAMES match the roofing Price
   Book (Ridge Vent / Vent Plug / Intake Vent). If a matching row already
   exists in the estimate (e.g. loaded as a default) we ADOPT it instead of
   adding a duplicate; otherwise we add one from the catalog. Either way we
   FORCE the ventilation behavior (measure/bundle) so the quantity is correct
   regardless of how the saved product is configured:
     ridge  → qty = FULL ridge length (measure ridge_lf), 4-ft sticks. The crew
              runs ridge vent across the whole ridge for a uniform look; only the
              code-required footage (ridge_vent_code / atticVentilation) is cut in
              for ventilation, and that "cut-in" figure rides the work order.
     plugs  → qty = turtle vent count (measure turtle_vents)
     intake → qty = eave LF (measure eave) */
const VENT_SPECS = {
  ridge:  { name:'Ridge Vent',  measure:'ridge_lf', bundle_lf:4, bundle_unit:'sticks' },
  plugs:  { name:'Vent Plug',   measure:'turtle_vents' },
  intake: { name:'Intake Vent', measure:'eave' },
};
function roofHasVentRole(role) {
  return ((S.trades.roofing && S.trades.roofing.line_items) || []).some(i => i.vent_role === role);
}
// Full markup for the ventilation panel — used by renderScopePage and by the
// live in-place refresh in setMeasurement (so the badge/figures update as the
// rep types roof area / turtle vents without re-rendering the whole page).
function ventPanelMarkup() {
  if (!S.trades.roofing || !S.trades.roofing.enabled) return '';
  const m = S.measurements || {};
  const vent = atticVentilation(m);
  const ventRound = n => Math.round(n).toLocaleString();
  const turtleN  = mnum(m.turtle_vents);
  const hasRidge = roofHasVentRole('ridge');
  // Ordering vs cut-in: we run ridge vent the FULL ridge length (for looks) but
  // only cut the deck open for the code-required footage.
  const ridgeLF    = mnum(m.ridge_lf);
  const fullSticks = Math.ceil(ridgeLF / 4);
  const rawCutin   = Math.ceil(vent.ridge_lf_required);
  const fullCut    = ridgeLF > 0 && rawCutin >= ridgeLF;   // deficit needs the whole ridge
  const cutinLF    = ridgeLF > 0 ? Math.min(rawCutin, ridgeLF) : rawCutin;
  // Three states: meets code / short & ridge added / short & no ridge (loud CTA).
  const statusClass = !vent.needs_ridge ? 'ok' : (hasRidge ? 'ok' : 'below');
  const statusHtml = !vent.needs_ridge
    ? `✅ Meets code`
    : (hasRidge
        ? `✅ Ridge Vent added — covers the ${ventRound(vent.deficit_exhaust)} sq in exhaust shortfall`
        : `⚠️ Below code — <strong>add Ridge Vent</strong> to bring this roof to code <span class="vent-status-sub">(short ${ventRound(vent.deficit_exhaust)} sq in of exhaust)</span>`);
  const ridgeHint = ridgeLF > 0
    ? `— orders <strong>${fullSticks} stick(s)</strong> for the full ridge (${ventRound(ridgeLF)} LF) · cuts in <strong>~${ventRound(cutinLF)} LF</strong> for code${fullCut ? ' (full ridge cut)' : ''}; also plugs your ${turtleN} turtle vent(s)`
    : `— enter <strong>Ridges (LF)</strong> above so it can order; code needs ~${ventRound(rawCutin)} LF cut in`;
  return `
    <div class="measure-panel measure-panel-vent">
      <div class="measure-panel-head">
        <h3>🌬️ Attic Ventilation <span class="vent-rule-tag">1/300 Rule</span></h3>
        <span class="measure-hint">Code-required net free area from attic size — flags short venting and adds ridge + intake to balance it</span>
      </div>
      ${vent.required_total > 0 ? `
        <div class="vent-status ${statusClass}">${statusHtml}</div>
        <div class="vent-figures">
          <div><span class="vf-label">Required (total)</span><span class="vf-val">${ventRound(vent.required_total)} sq in</span></div>
          <div><span class="vf-label">Exhaust needed</span><span class="vf-val">${ventRound(vent.required_exhaust)} sq in</span></div>
          <div><span class="vf-label">Intake needed</span><span class="vf-val">${ventRound(vent.required_intake)} sq in</span></div>
          <div><span class="vf-label">Exhaust provided</span><span class="vf-val">${ventRound(vent.provided_exhaust)} sq in <span class="vf-sub">(${turtleN} turtle × ${NFA_TURTLE_SQIN})</span></span></div>
        </div>
        <div class="vent-actions">
          <label class="iw-second-row-toggle ${hasRidge ? 'enabled' : ''}">
            <input type="checkbox" ${hasRidge ? 'checked' : ''}
              onchange="setVentRole('ridge', this.checked)">
            ✔️ Install Ridge Vent <span class="iw-toggle-hint">${ridgeHint}</span>
          </label>
          <label class="iw-second-row-toggle ${roofHasVentRole('intake') ? 'enabled' : ''}">
            <input type="checkbox" ${roofHasVentRole('intake') ? 'checked' : ''}
              onchange="setVentRole('intake', this.checked)">
            ✔️ Install Intake Vent <span class="iw-toggle-hint">— continuous soffit intake along the eaves</span>
          </label>
        </div>
        ${hasRidge ? `
          <button type="button" class="vent-cutin-btn" onclick="openVentCutinEditor()">🖍️ Mark cut-in on roof <span class="vent-cutin-sub">${(S.vent_cutin && S.vent_cutin.image_filename) ? 'edit map' : '~' + ventRound(cutinLF) + ' LF to cut'}</span></button>` : ''}
        ${hasRidge && ridgeLF === 0 ? `
          <div class="vent-warn">⚠️ Ridge Vent added but <strong>Ridges (LF)</strong> is 0 — enter ridge footage above so it orders.</div>` : ''}
        ${hasRidge && fullCut && ridgeLF > 0 ? `
          <div class="vent-warn">⚠️ Code needs ~${ventRound(rawCutin)} LF of exhaust but the ridge is only ${ventRound(ridgeLF)} LF — cutting the full ridge; add box vents to cover the gap.</div>` : ''}
        ${roofHasVentRole('intake') && mnum(m.eave_lf) === 0 ? `
          <div class="vent-warn">⚠️ Intake Vent added but Eave LF is 0 — enter eave footage so it prices.</div>` : ''}
      ` : `
        <div class="measure-hint" style="padding:6px 0">Enter Roof Area above to calculate required ventilation.</div>`}
    </div>`;
}
function _findRoofItemByName(name) {
  const want = String(name || '').trim().toLowerCase();
  return ((S.trades.roofing && S.trades.roofing.line_items) || [])
    .find(i => !i.vent_role && String(i.name || '').trim().toLowerCase() === want);
}
function injectVentItem(role) {
  const rd = S.trades.roofing;
  if (!rd || !rd.enabled || !templates) return;
  // Append to a real estimate — build the roofing defaults first if empty.
  if (!rd.line_items || rd.line_items.length === 0) buildBundleDefaults('roofing');
  const add = (r) => {
    if (roofHasVentRole(r)) return;
    const spec = VENT_SPECS[r];
    // Adopt an existing same-named row (e.g. a Price Book default) so we never
    // add a duplicate; otherwise build a fresh one from the catalog.
    let it = _findRoofItemByName(spec.name);
    if (it) {
      // Snapshot the product's own settings so a later un-check can restore them.
      if (!it._vent_orig) it._vent_orig = { measure:it.measure, formula:it.formula, bundle_lf:it.bundle_lf, bundle_unit:it.bundle_unit };
    } else {
      const tpl = findTemplate('roofing', spec.name);
      if (!tpl) { alert('Add "'+spec.name+'" to the roofing Price Book first.'); return; }
      it = buildItemFromTemplate('roofing', tpl);
      it._vent_injected = true;
      rd.line_items.push(it);
    }
    // Force the ventilation quantity behavior regardless of the saved product.
    it.vent_role = r;
    it.measure = spec.measure;
    it.formula = undefined;
    it.bundle_lf = spec.bundle_lf || undefined;
    it.bundle_unit = spec.bundle_unit || undefined;
  };
  add(role);
  // Installing ridge vent means decking over the existing turtle vents so the
  // ridge draws evenly — auto-add the plugs (qty auto-fills to turtle count).
  if (role === 'ridge') add('plugs');
}
function removeVentRole(role) {
  const rd = S.trades.roofing;
  if (!rd || !rd.line_items) return;
  const roles = role === 'ridge' ? ['ridge', 'plugs'] : [role];
  rd.line_items = rd.line_items.filter(it => {
    if (!roles.includes(it.vent_role)) return true;
    if (it._vent_injected) return false;   // we created it → drop the row
    // Adopted an existing product row → restore its original settings.
    const o = it._vent_orig || {};
    it.measure = o.measure; it.formula = o.formula;
    it.bundle_lf = o.bundle_lf; it.bundle_unit = o.bundle_unit;
    delete it._vent_orig; delete it.vent_role;
    return true;
  });
}
function setVentRole(role, on) {
  if (on) injectVentItem(role); else removeVentRole(role);
  applyMeasurements();      // fills the new items' quantities from their measure
  setDirty();
  if (activePage === 'scope') renderScopePage();
  rerender();               // refresh pricing/options tabs
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
let tierDefaults = {}; // global admin-set G/B/B content, keyed by trade: {trade:{descriptions:{},features:{}}}
let pbActiveTrade = 'roofing';
let pbActiveTab = 'master';  // price book sub-view: master | good | better | best
let pbItems = {};       // working copy of price book materials while modal is open
let pbPresets = {};     // working copy of brand presets (keyed by trade) while modal is open
let pbView = 'catalog'; // price book view: 'catalog' | 'presets'
let pbEditPresetId = null; // preset currently open in the editor
// Bundle trades (roofing, siding) use a two-level model instead of the tier
// tabs: a flat product catalog + named bundles (the G/B/B dropdown options).
// All three working copies are keyed by trade so switching tabs in the modal
// never drops unsaved edits to the other trade.
let pbCatalogs = {};      // {trade: [...]}  working copy of <trade>_catalog
let pbBundleSets = {};    // {trade: [...]}  working copy of <trade>_bundles
let pbBundleDefaults = {};// {trade: {good,better,best}} default bundle id per tier
let pbRoofView = 'products'; // bundle-trade sub-view: 'products' | 'bundles'
let pbEditBundleId = null;    // bundle currently open in the editor
// The active trade's working copies (the modal edits one trade at a time).
function pbCat()     { return pbCatalogs[pbActiveTrade]      || (pbCatalogs[pbActiveTrade] = []); }
function pbBundles() { return pbBundleSets[pbActiveTrade]    || (pbBundleSets[pbActiveTrade] = []); }
function pbDefs()    { return pbBundleDefaults[pbActiveTrade] || (pbBundleDefaults[pbActiveTrade] = {good:'',better:'',best:''}); }

function blankEstimate() {
  const today = new Date();
  const exp = new Date(today); exp.setDate(exp.getDate() + 30);
  return {
    estimate_id: null, version: 1,
    created_at: null, updated_at: null, status: 'draft',
    customer: { crm_contact_id:null, crm_project_id:null, crm_job_number:'',
                crm_lead_id:'',
                name:'', phone:'', email:'',
                address:{ street:'', city:'', state:'', zip:'' } },
    project_address: '',
    // Permit jurisdiction chosen for this job (Scope-page panel). auto_id is the
    // address-matched default; selected_id + confirmed track a rep override
    // (mailing city isn't always the permitting authority — see setJurisdiction).
    // verified caches the /api/jurisdictions/verify boundary answer so it runs
    // once per address, not on every render — see _jxMaybeVerify.
    permit_jurisdiction: { selected_id:null, auto_id:null, confirmed:false, verified:null },
    estimate_date: fmtDate(today), valid_until: fmtDate(exp),
    salesperson: '', notes_internal: '', notes_customer: '',
    pricing: { mode:'margin', global_rate:35,
               tier_rates:{ good:35, better:35, best:35 },
               // Per-trade, per-tier margin overrides. trade_rates[trade] =
               // {good,better,best,simple}; a missing/'' value inherits the
               // tier_rates default. Highest priority in the resolution chain.
               trade_rates:{},
               per_trade_overrides:{ roofing:null,siding:null,windows:null,gutters:null,commercial:null,other:null,insurance:null } },
    selected_tier: 'better',
    tier_descriptions: { good:'', better:'', best:'' },
    tier_features:     { good:[], better:[], best:[] },
    estimate_type: 'retail',
    print_contract: true, contract_text: globalContract('retail'),
    contract_initials: defaultInitials('retail'),
    shingle_selection: { enabled: true, options: _globalShingleColors(), chosen: '' },
    measurements: { waste_pct: _globalWastePct() },
    structures: [],            // buildings on a complex; empty = one roof, measured above
    intro_text: '',
    page_visibility: { intro: false, options: true, products: true, pricing: true, report: true },
    roof_health: {
      condition: '', age_years: '', inspection_date: fmtDate(today),
      material_type: '', pitch: '', summary: '',
      findings: [], recommendations: [], report_photo_ids: [],
    },
    property_condition: null,  // populated on first use of Condition tab
    estimate_label: '',        // rep-facing label to differentiate multiple estimates per customer
    cover_photo_id: null,
    share_token: null, signature: null,
    attachments: [],
    trades: {
      roofing: { enabled:true,  line_items:[], colors:{}, mode:'gbb', selected_tier:'better',
                 tier_bundles:{good:'',better:'',best:''},
                 tier_features:{good:[],better:[],best:[]}, tier_descriptions:{good:'',better:'',best:''} },
      siding:  { enabled:false, line_items:[], colors:{}, mode:'gbb', selected_tier:'better',
                 tier_bundles:{good:'',better:'',best:''},
                 tier_features:{good:[],better:[],best:[]}, tier_descriptions:{good:'',better:'',best:''} },
      windows: { enabled:false, line_items:[], colors:{}, mode:'gbb', selected_tier:'better',
                 tier_features:{good:[],better:[],best:[]}, tier_descriptions:{good:'',better:'',best:''} },
      gutters: { enabled:false, line_items:[], colors:{}, mode:'simple', selected_tier:'better',
                 tier_features:{good:[],better:[],best:[]}, tier_descriptions:{good:'',better:'',best:''} },
      // Commercial low-slope. A bundle trade (pick a system, get the build-up)
      // sold as Good/Better/Best like a shingle roof: coating, overlay, full
      // replacement. The rep can still flip it to Simple per estimate when the
      // building owner only wants one number.
      commercial: { enabled:false, line_items:[], colors:{}, mode:'gbb', selected_tier:'better',
                 simple_bundle:'',
                 tier_bundles:{good:'',better:'',best:''},
                 tier_features:{good:[],better:[],best:[]}, tier_descriptions:{good:'',better:'',best:''} },
      other:     { enabled:false, line_items:[], colors:{}, mode:'gbb', selected_tier:'better',
                   tier_features:{good:[],better:[],best:[]}, tier_descriptions:{good:'',better:'',best:''} },
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
  const sign = n < 0 ? '−' : '';  // keep the sign — a negative profit must look negative
  return sign + '$' + Math.abs(n).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(()=>fn(...a), ms); }; }

/* ── Multi-line description boxes (every Pricing tab) ────────────────
   Every trade's line item carries a Description underneath its name, and every
   one of those boxes is a <textarea>, not an <input> — so Enter inserts a new
   line instead of doing nothing. They grow to fit rather than scrolling, which
   is the only way a rep can see a three-line description they typed.

   Two halves, and both are needed: `autoGrow` on every keystroke, and one
   `autoGrowAll` pass after the tab is re-rendered — a saved description must
   open at full height, not one row with the rest hidden. `rows=` alone only
   counts hard newlines, so it under-sizes anything that wraps.
   Textareas opt in with the `desc-ta` class. */
function autoGrow(el) {
  if (!el) return;
  el.style.height = 'auto';
  // A textarea inside a display:none ancestor measures 0 — the collapsed tier
  // panels in the GBB grid are exactly that at render time. Writing that 0 back
  // would pin the box shut, and expanding the tier would show a 0px-tall
  // description. Leave the height alone and let the `rows` attribute hold it;
  // toggleTierDetails re-renders the tab, so the panel gets measured for real
  // the moment it opens. An empty box that IS laid out still measures > 0
  // (padding + one line), so this only ever skips the unrendered case.
  if (!el.scrollHeight) return;
  el.style.height = el.scrollHeight + 'px';
}
function autoGrowAll(root) {
  (root || document).querySelectorAll('textarea.desc-ta').forEach(autoGrow);
}
// Rows to open a description at before autoGrowAll measures it — keeps a saved
// multi-line description from flashing as a single row on first paint.
function descRows(s) { return Math.max(1, String(s || '').split('\n').length); }

/* ── Calculations ──────────────────────────────────────────────────── */

const DEFAULT_RATE = 35;
// A rate source counts as set only if it parses as a number. 0 counts — a rep
// really can sell at cost — but null/''/junk do not, so the caller falls
// through. MUST mirror _rate_value (app.py).
function _rateValue(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = parseFloat(v);
  return isNaN(n) ? null : n;
}
// Resolve a rate from the first source that is actually set.
// Defaulting to 35 rather than 0 is deliberate: a 0% fallback would silently
// sell at cost. MUST mirror _tier_rate (app.py).
function _resolveRate(sources) {
  for (const v of sources) {
    const n = _rateValue(v);
    if (n !== null) return n;
  }
  return DEFAULT_RATE;
}
// Per-tier margin resolution, most specific first:
//   per-trade-per-tier (trade_rates[trade][tier]) → legacy flat per-trade
//   override → global per-tier rate → global rate → 35 (house default).
// trade_rates lets each trade carry its own Good/Better/Best margin, edited in
// that trade's tab; a blank slot inherits the tier_rates default. MUST mirror
// _tier_rate (app.py).
function tierRate(trade, tier) {
  const p = S.pricing || {};
  return _resolveRate([((p.trade_rates || {})[trade] || {})[tier],
                       (p.per_trade_overrides || {})[trade],
                       (p.tier_rates || {})[tier],
                       p.global_rate]);
}
// Non-tier contexts (simple-mode trades): a trade's own 'simple' margin, then
// the same fallbacks. Simple mode is effectively the Good package when it has
// no dedicated rate. MUST mirror the simple path in app.js recalcSimpleItems.
function tradeRate(trade) {
  const p = S.pricing || {};
  return _resolveRate([((p.trade_rates || {})[trade] || {}).simple,
                       (p.per_trade_overrides || {})[trade],
                       (p.tier_rates || {}).good,
                       p.global_rate]);
}
function lineTotal(qty, mat, labor, trade, tier) {
  const cost = (parseFloat(mat)||0) + (parseFloat(labor)||0);
  const q = parseFloat(qty) || 0;
  const r = tier ? tierRate(trade, tier) : tradeRate(trade);
  // Margin when mode is unset; any other value means markup. Mirrors app.py's
  // pricing.get('mode', 'margin') followed by an exact 'margin' test.
  const mode = (S.pricing || {}).mode ?? 'margin';
  return mode === 'margin'
    ? (r >= 100 ? 0 : cost * q / (1 - r/100))
    : cost * q * (1 + r/100);
}
function lineTotalEffective(item, tier, trade) {
  const t = (item.tiers && item.tiers[tier]) || {};
  if (t.price_override !== undefined && t.price_override !== null && t.price_override !== '') {
    return parseFloat(t.price_override) || 0;
  }
  return lineTotal(item.quantity, t.material_unit_cost, t.labor_unit_cost, trade, tier);
}
function tradeTotal(trade, tier) {
  if (trade === 'insurance') return 0; // insurance uses insuranceTotal()
  const td = S.trades[trade];
  if (!td || !td.enabled) return 0;
  const effectiveMode = effectiveTradeMode(trade, td);
  if (effectiveMode === 'simple') {
    return (td.line_items || []).reduce((sum, item) =>
      sum + (parseFloat(item.quantity)||0) * (parseFloat(item.unit_price)||0), 0);
  }
  return td.line_items.reduce((sum, item) => {
    // Zero-qty items are "not in scope" (the grid parks them in a chip row and
    // the customer page hides them) — they must not price, even with a locked
    // price_override. MUST mirror calc_tier_total in app.py.
    if ((parseFloat(item.quantity) || 0) <= 0) return sum;
    const t = (item.tiers && item.tiers[tier]) || {};
    if (t.included === false) return sum;  // excluded from this package tier
    return sum + lineTotalEffective(item, tier, trade);
  }, 0);
}
function grandTotal(tier) {
  return RETAIL_TRADE_KEYS.reduce((s,tr)=>s+tradeTotal(tr,tier),0);
}
// Mix-and-match total: every trade priced at ITS OWN selected tier.
// MUST mirror calc_selected_total in app.py.
function selectedTotal() {
  return RETAIL_TRADE_KEYS
    .reduce((s,tr)=>s+tradeTotal(tr, tradeTier(tr)),0);
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
  renderProductsPage();
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
  renderProductsPage();
  if (activePage === 'pricing') { renderTabBar(); renderTradeContent(); }
  updatePageNav();
  renderPrintPagesBar();
}

/* ── Page navigation ───────────────────────────────────────────────── */

function switchPage(page) {
  // Documents folded into the customer screen — one place a customer lives,
  // not a page behind a door on another page. Kept as an alias rather than
  // chased through every caller: the header badge, the client hub, deep links
  // and muscle memory all still say 'documents'.
  if (page === 'documents') page = 'client';
  // Save-on-navigate: switching pages is a natural checkpoint, so unsaved work
  // survives a closed tab / dead battery without waiting for the 60s autosave.
  if (dirty && S.estimate_id && page !== activePage) saveEstimate();
  activePage = page;
  document.querySelectorAll('.page').forEach(el => el.style.display = 'none');
  const target = document.getElementById('page-' + page);
  if (target) target.style.display = 'flex';
  // Home page hides sidebar/nav; all other pages restore them
  document.body.classList.toggle('is-home', page === 'home');
  // The customer screen is "client mode": no estimate tab strip or sidebar.
  // It carries the customer's details, notes, estimates and files; the tab
  // strip belongs to the estimate flow only.
  document.body.classList.toggle('is-client', page === 'client');
  updatePageNav();
  const activeBtn = document.querySelector('.page-btn.active');
  if (activeBtn) activeBtn.scrollIntoView({block:'nearest',inline:'center',behavior:'smooth'});
  if (page === 'home')    { renderHomePage(); return; }
  if (page === 'pricing') { renderTabBar(); renderTradeContent(); }
  if (page === 'intro')   renderIntroPage();
  if (page === 'scope')    renderScopePage();
  if (page === 'options')  renderOptionsPage();
  if (page === 'products') renderProductsPage();
  if (page === 'report')   renderConditionPage();
  if (page === 'client')   {
    renderClientPage();
    refreshDocCustData();
    loadCustomerNotes((S.customer || {}).name);
  }
  if (page === 'visualizer') renderVisualizerPage();
}

function pageComplete(page) {
  const hasItems = trade => S.trades[trade].enabled && (S.trades[trade].line_items||[]).length > 0;
  switch (page) {
    case 'cover':    return !!(S.customer && S.customer.name);
    case 'intro':    return !!(S.intro_text && S.intro_text.trim());
    case 'photos':   return (S.photos||[]).length > 0;
    case 'scope':    return RETAIL_TRADE_KEYS.some(hasItems);
    case 'options':  return selectedTotal() > 0 || insuranceTotal() > 0 || isReportOnly();
    case 'products': return RETAIL_TRADE_KEYS.some(t =>
                        S.trades[t].enabled && Object.values(S.trades[t].colors||{}).some(v => String(v||'').trim()));
    case 'pricing':  return selectedTotal() > 0 || insuranceTotal() > 0 || isReportOnly();
    case 'contract': return !!(S.contract_text && S.contract_text.trim());
    // property_condition is what the Condition tab writes; roof_health is the
    // pre-2026 field it migrates FROM. Testing only the old one meant the ✓
    // never lit for a report built on the current tab — invisible until the
    // Report estimate type made this the page a rep starts on.
    case 'report':   return PC_SECTIONS.some(x => {
                       const sec = (S.property_condition?.sections || {})[x.key];
                       return !!(sec && sec.enabled && sec.grade);
                     }) || !!(S.roof_health?.condition);
    case 'documents': return (S.attachments||[]).length > 0;
    case 'client':   return !!(S.customer && S.customer.name);
    case 'visualizer': {
      const vz = S.visualizer || {};
      return Object.values(vz.tier_renders || {}).some(Boolean) ||
        Object.values(vz.elevations || {}).some(ev => Object.values((ev || {}).tier_renders || {}).some(Boolean));
    }
    default: return false;
  }
}
function updatePageNav() {
  document.querySelectorAll('.page-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.page === activePage);
    btn.classList.toggle('pg-done', pageComplete(btn.dataset.page));
  });
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
  renderTierRates();
  renderPricingModeUI();
  renderEstimateTypeUI();
  renderTierButtons();
  renderTradeOverrides();
  renderEstNum();
  renderCrmLinkBadge();
  renderSentLockBanner();
  renderEstStatusBar();
  renderEstLabelBadge();
  // Show "Customer File" button only when a customer name is present
  const cfBtn = document.getElementById('cf-open-btn');
  if (cfBtn) cfBtn.style.display = (S.customer && S.customer.name) ? '' : 'none';
}
function setVal(id, v) {
  const el = document.getElementById(id);
  if (el && el.value !== String(v ?? '')) el.value = v ?? '';
}
/* The EST number and the signed/sent state render into SEPARATE elements on
   purpose. The mobile breakpoint hides the number (monospace noise on a 375px
   header) — but it used to carry the "Signed" badge with it, so a rep on a
   phone had no way to tell a signed estimate from a draft. The badge now
   stands on its own and survives every breakpoint. */
function renderEstNum() {
  const numEl = document.getElementById('estimate-number');
  if (numEl) {
    numEl.textContent = S.estimate_id
      ? 'EST-' + S.estimate_id.split('-')[0].toUpperCase()
      : 'New Estimate';
  }

  const el = document.getElementById('est-status-badge');
  if (!el) return;
  const sig = S.signature;
  if (sig) {
    const dt  = new Date(sig.signed_at);
    const fmt = isNaN(dt) ? '' : dt.toLocaleDateString('en-US',
      { month:'short', day:'numeric', year:'numeric' });
    el.className   = 'est-status-badge est-status-signed';
    el.textContent = '✓ Signed';
    el.title = `Signed by ${sig.name || 'customer'}${fmt ? ' on ' + fmt : ''} — open the signed PDF`;
    el.style.display = '';
  } else if (S.share_token) {
    el.className   = 'est-status-badge est-status-sent';
    el.textContent = '📤 Sent';
    el.title = 'Sent to the customer — not signed yet';
    el.style.display = '';
  } else {
    el.style.display = 'none';
    el.textContent = '';
    el.title = '';
  }
}

// Which estimate a rep is actually looking at is otherwise invisible once
// they leave the Customer hub — the typed label lived only on S.estimate_label
// and was never shown again, so two estimates for the same customer looked
// identical from inside the builder. Sibling to #estimate-number, never
// nested inside it (that element is hidden on mobile — see the note on
// #est-status-badge above the same trap). Clicking jumps to Documents;
// switchPage() already autosaves on navigate, so nothing is lost.
function renderEstLabelBadge() {
  const el = document.getElementById('estimate-label-badge');
  if (!el) return;
  const name = (S.customer && S.customer.name) || '';
  if (!name) { el.style.display = 'none'; el.textContent = ''; return; }
  const label = (S.estimate_label || '').trim() || (EST_TYPE_LABEL[S.estimate_type] || EST_TYPE_LABEL.retail);
  const n = custEstimateCount(name);
  el.innerHTML = esc(label) + (n > 1 ? ` <span class="dash-cf-btn">📁 ${n}</span>` : '');
  el.title = 'This customer’s estimates — click to open Documents';
  el.style.display = '';
}

/* ── Signed contract, front and centre ──────────────────────────────────
   The signed PDF is filed automatically by the server's post-sign pipeline
   (app.py _post_sign_pipeline -> save_signed_contract_attachment), and always
   has been. But Documents has no entry in the tab strip, so unless a rep went
   Customer -> Documents deliberately they never saw it. These give it a door
   from the places the rep actually is after a signature lands. */
function signedContractAttachment() {
  return (S.attachments || []).find(
    a => a.server_generated && a.doc_type === 'signed_contract') || null;
}

function openSignedContract() {
  const att = signedContractAttachment();
  if (!att) return false;
  window.open(`${BASE}/uploads/${att.filename}`, '_blank', 'noopener');
  return true;
}

function estStatusBadgeClick() {
  if (!S.signature) return;          // "Sent" is a status, not a link
  if (openSignedContract()) return;
  // The pipeline builds the PDF in a background thread, so for a few seconds
  // after signing the attachment genuinely is not there yet. Say so rather
  // than appearing to do nothing.
  alert('The signed PDF is still being generated — it lands in Documents within '
      + 'a few seconds of signing. Reopen the estimate if it does not appear.');
}
function renderPricingModeUI() {
  document.getElementById('mode-margin').classList.toggle('active', S.pricing.mode === 'margin');
  document.getElementById('mode-markup').classList.toggle('active', S.pricing.mode === 'markup');
}
/* Adding a type? It reaches the sidebar, the customer screen's create dialog
   and initialsAreStock() from this list — but the contract it gets comes from
   CONTRACT_TYPES, and a type missing THERE silently inherits retail's terms.
   'report' inherits them deliberately: a repair bid sells under the same terms
   as any other retail job. */
const ESTIMATE_TYPES = ['retail','insurance','commercial','report'];
function renderEstimateTypeUI() {
  const t = S.estimate_type || 'retail';
  ESTIMATE_TYPES.forEach(k => {
    const btn = document.getElementById('type-' + k);
    if (btn) btn.classList.toggle('active', t === k);
  });
}
// True when the current initials still match an untouched default set
// (either the hardcoded texts or the saved company-wide defaults)
function initialsMatchDefault(type) {
  const cur  = (S.contract_initials || []).map(i => i.text).join('\n');
  const hard = (type === 'insurance' ? DEFAULT_INITIALS_INSURANCE : DEFAULT_INITIALS_RETAIL).join('\n');
  return cur === hard || cur === globalInitialTexts(type).join('\n');
}
// True when the initials are still SOMEBODY's untouched defaults — i.e. the rep
// hasn't written their own, so a type switch may replace them.
function initialsAreStock() {
  const cur = (S.contract_initials || []).map(i => i.text).join('\n');
  if (!cur.trim()) return true;
  return ESTIMATE_TYPES.some(t => initialsMatchDefault(t));
}
// Commercial bids target 29% gross margin (the rate the commercial-estimator
// process has always used). Seeded once, per trade, and never forced back —
// a rep who changes it on this estimate keeps their number.
function seedCommercialRates() {
  S.pricing.trade_rates = S.pricing.trade_rates || {};
  if (!S.pricing.trade_rates.commercial)
    S.pricing.trade_rates.commercial = { simple:29, good:29, better:29, best:29 };
}
function setEstimateType(type) {
  if (!ESTIMATE_TYPES.includes(type)) type = 'retail';
  S.estimate_type = type;
  const only = tk => TRADES.forEach(t => { if (S.trades[t]) S.trades[t].enabled = (t === tk); });

  if (type === 'report') {
    // Every trade OFF. That is the whole point of the type: a new estimate
    // ships with Roofing enabled and empty, and an inspection written up on
    // top of that used to print three $0 package columns and a "Project Total
    // $0" under a report that had just quoted the repairs. With no trade
    // enabled the estimate is priced by its recommendations (isReportOnly).
    TRADES.forEach(t => { if (S.trades[t]) S.trades[t].enabled = false; });
    activeTrade = 'roofing';       // the Pricing tab still opens on something
    // The report IS the document here — it cannot be the thing the rep hides.
    if (!S.page_visibility) S.page_visibility = {};
    S.page_visibility.report = true;
    // No roof is being sold, so asking the customer to pick a shingle colour
    // before they can sign is asking about work nobody quoted.
    if (S.shingle_selection) S.shingle_selection.enabled = false;
    pcGet();                        // materialize the report the rep is about to fill in
  } else if (type === 'insurance') {
    only('insurance');
    activeTrade = 'insurance';
  } else if (type === 'commercial') {
    only('commercial');
    activeTrade = 'commercial';
    seedCommercialRates();
    // A flat roof has no shingle to pick — leaving this on would ask the
    // customer to choose a shingle color before they could sign.
    if (S.shingle_selection) S.shingle_selection.enabled = false;
    if (!(S.trades.commercial.line_items || []).length) buildBundleDefaults('commercial');
  } else {
    // Back to retail: insurance/commercial/report each turned every other trade
    // off, so restore roofing — otherwise the estimate lands on an empty tab
    // with no enabled trade at all.
    S.trades.insurance.enabled = false;
    S.trades.commercial.enabled = false;
    if (!RETAIL_TRADE_KEYS.some(t => S.trades[t].enabled && t !== 'commercial'))
      S.trades.roofing.enabled = true;
    if (S.shingle_selection && S.trades.roofing.enabled) S.shingle_selection.enabled = true;
    activeTrade = 'roofing';
  }

  // Swap the contract + initials only while they're still stock — a rep who
  // edited either one keeps their version through a type switch.
  if (isStockContract(S.contract_text)) {
    S.contract_text = globalContract(type);
    const ta = document.getElementById('contract-textarea');
    if (ta) ta.value = S.contract_text;
  }
  if (initialsAreStock()) S.contract_initials = defaultInitials(type);

  setDirty();
  renderEstimateTypeUI();
  // Commercial starts at measurements (the bid is driven by the EagleView
  // numbers); insurance starts at pricing (its line items come from the carrier).
  if (type === 'commercial') switchPage('scope');
  else if (type === 'insurance') switchPage('pricing');
  // A report estimate is written on the Condition tab and nowhere else.
  else if (type === 'report') switchPage('report');
  else {
    if (activePage === 'options') renderOptionsPage();
    else if (activePage === 'pricing') { renderTabBar(); renderTradeContent(); }
    else if (activePage === 'scope') renderScopePage();
  }
  renderTotals();
  if (activePage === 'contract') renderContractPage();
}
function renderTierButtons() {
  // A report-only estimate offers no packages at all, so the Good/Better/Best
  // picker and its three $0 rows are asking the rep to choose between nothing.
  const repOnly = isReportOnly();
  const sel = document.querySelector('.tier-selector');
  if (sel) sel.style.display = repOnly ? 'none' : '';
  const title = document.getElementById('section-tier-title');
  if (title) title.textContent = repOnly ? 'Estimate Total' : 'Active Package';
  document.querySelectorAll('.tier-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.tier === S.selected_tier);
    if (TIERS.includes(b.dataset.tier))
      b.style.display = tierEnabled(b.dataset.tier) ? '' : 'none';
  });
  TIERS.forEach(t => {
    const row = document.getElementById('tr-' + t);
    if (row) {
      row.classList.toggle('is-selected', t === S.selected_tier);
      row.style.display = (!repOnly && tierEnabled(t)) ? '' : 'none';
    }
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
  // Report-only estimate: the three tier rows are all $0 and the repairs are
  // the price. Show it here rather than leaving the rep to add it up off the
  // Condition tab. The tier rows' own visibility belongs to renderTierButtons
  // — two writers for one style property is how they end up disagreeing —
  // so call it rather than hiding them here.
  const repEl  = document.getElementById('total-repairs');
  const repRow = document.getElementById('tr-repairs');
  if (repEl)  repEl.textContent = fmtCur(pcRepairTotals().total);
  if (repRow) repRow.style.display = isReportOnly() ? '' : 'none';
  renderTierButtons();
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
  RETAIL_TRADE_KEYS.forEach(trade => {
    const td = S.trades[trade];
    if (!td || !td.enabled) return;
    const mode = effectiveTradeMode(trade, td);
    if (mode === 'simple') {
      let s = 0, c = 0;
      (td.line_items||[]).forEach(item => {
        const qty = parseFloat(item.quantity)||0; if (qty <= 0) return;
        s += qty * (parseFloat(item.unit_price)||0);
        c += qty * (parseFloat(item.unit_cost)||0);
      });
      if (s === 0 && c === 0) return;
      if (c > 0) {
        // Cost is tracked — include in profit calculation
        material += c; gbbSell += s;
        perTrade.push({trade, mode, material:c, labor:0, cost:c, sell:s, profit:s-c});
      } else {
        simpleSell += s;
        perTrade.push({trade, mode, sell:s});
      }
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
  const totalSell = gbbSell + simpleSell;
  const profit = gbbSell - cost;
  const franchise = Math.round(totalSell * FRANCHISE_RATE * 100) / 100;
  const netProfit = profit - franchise;
  return {material, labor, cost, sell:gbbSell, profit, franchise, netProfit,
          margin:    gbbSell   > 0 ? (profit   / gbbSell   * 100) : 0,
          netMargin: totalSell > 0 ? (netProfit / totalSell * 100) : 0,
          simpleSell, perTrade};
}

const FRANCHISE_RATE = 0.08; // 8% of contract price taken off profit
function _pct(n){ return (Math.round(n*10)/10).toFixed(1) + '%'; }

// Compact sidebar summary for the selected package (rep-only).
function renderInternalMargin() {
  const el = document.getElementById('internal-margin');
  if (!el) return;
  const tier = S.selected_tier;
  const p = tierProfit(tier);
  if (p.sell === 0 && p.cost === 0 && p.simpleSell === 0) { el.innerHTML = ''; el.style.display = 'none'; return; }
  el.style.display = '';
  const netClass = p.netProfit >= 0 ? 'im-pos' : 'im-neg';
  el.innerHTML = `
    <div class="im-head">🔒 Internal · ${TIER_LABELS[tier]}</div>
    <div class="im-row"><span>Material</span><strong>${fmtCur(p.material)}</strong></div>
    <div class="im-row"><span>Labor</span><strong>${fmtCur(p.labor)}</strong></div>
    <div class="im-row im-cost"><span>Total Cost</span><strong>${fmtCur(p.cost)}</strong></div>
    <div class="im-row"><span>Gross Profit</span><strong>${fmtCur(p.profit)}</strong></div>
    <div class="im-row im-franchise"><span>Franchise (${Math.round(FRANCHISE_RATE*100)}%)</span><strong>−${fmtCur(p.franchise)}</strong></div>
    <div class="im-row ${netClass} im-net"><span>Net Profit</span><strong>${fmtCur(p.netProfit)}</strong></div>
    <div class="im-row im-margin"><span>Net Margin</span><strong>${_pct(p.netMargin)}</strong></div>
    ${p.simpleSell>0?`<div class="im-note">+${fmtCur(p.simpleSell)} simple-priced included in franchise fee</div>`:''}
    ${(()=>{const sq=parseFloat((S.measurements||{}).roof_squares)||0;const tot=p.sell+p.simpleSell;return sq>0&&tot>0?`<div class="im-note im-persq">${fmtCur(Math.round(tot/sq))} / SQ</div>`:''})()}`;
}

// Full all-tiers breakdown panel on the Pricing page (rep-only).
function renderCostProfitPanel() {
  const el = document.getElementById('cost-profit-panel');
  if (!el) return;
  const data = {good:tierProfit('good'), better:tierProfit('better'), best:tierProfit('best')};
  const anything = TIERS.some(t => data[t].sell !== 0 || data[t].cost !== 0 || data[t].simpleSell !== 0);
  if (!anything) { el.innerHTML = ''; return; }
  const selTier = S.selected_tier;
  const row = (label, fn, cls='') => `<tr class="${cls}"><td>${label}</td>${
    TIERS.map(t=>`<td>${fn(data[t])}</td>`).join('')}</tr>`;
  const moneyRow = (label, key, cls='') => row(label, d=>fmtCur(d[key]), cls);
  // Split by whether cost is actually tracked, not by mode — a Simple-mode
  // trade with unit costs entered IS in the profit math and belongs in the
  // per-trade table, not the "cost not tracked" bucket.
  const pt = data[selTier].perTrade.filter(x => x.cost !== undefined);
  const simpleRows = data[selTier].perTrade.filter(x => x.cost === undefined);

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
          ${row('Gross Profit', d=>`<span class="${d.profit>=0?'cpp-pos':'cpp-neg'}">${fmtCur(d.profit)}</span>`,'cpp-profit')}
          ${row(`Franchise (${Math.round(FRANCHISE_RATE*100)}%)`, d=>`<span class="cpp-neg">−${fmtCur(d.franchise)}</span>`,'cpp-franchise')}
          ${row('Net Profit', d=>`<span class="${d.netProfit>=0?'cpp-pos':'cpp-neg'}">${fmtCur(d.netProfit)}</span>`,'cpp-net')}
          ${row('Net Margin %', d=>_pct(d.netMargin),'cpp-margin')}
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
          ? `<img class="cover-photo-img" src="${BASE}/uploads/${esc(coverPhoto.filename)}" alt="Property photo">`
          : ''}
        <div class="cover-photo-overlay"></div>
        <div class="cover-logo-overlay">
          ${!coverPhoto ? `
            <img src="${BASE}/static/logo.png" class="cover-logo-img" alt="Project One Roofing">
            <div class="cover-upload-hint">
              <div class="cover-upload-icon">📷</div>
              <strong>Click to add a cover photo</strong>
              <div>or drag &amp; drop an image here</div>
            </div>` : `<img src="${BASE}/static/logo.png" class="cover-logo-img" alt="Project One Roofing">`}
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
                <img src="${BASE}/uploads/${esc(p.filename)}" alt="">
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
  fd.append('file', await compressImage(file));
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
    pbItems[trade] = defaults.map(t => {
      const it = Object.assign({}, t);
      // Seed Good's cost from the legacy base cost so it always shows a value.
      // Better/Best stay blank when unset and inherit upward (shown as placeholder).
      const base = it.cost !== undefined ? parseFloat(it.cost)||0
                 : (parseFloat(it.mat_better)||0)+(parseFloat(it.lab_better)||0);
      if (it.cost_good === undefined || it.cost_good === '') it.cost_good = base;
      return it;
    });
  });
  pbPresets = {};
  PB_TRADES.forEach(trade => {
    const stored = priceBook?.presets?.[trade];
    pbPresets[trade] = Array.isArray(stored) ? JSON.parse(JSON.stringify(stored)) : [];
  });
  // Per-trade catalog + bundles working copies (the server seeds these if absent)
  pbCatalogs = {}; pbBundleSets = {}; pbBundleDefaults = {};
  BUNDLE_TRADES.forEach(trade => {
    pbCatalogs[trade]   = JSON.parse(JSON.stringify(priceBook?.[trade + '_catalog'] || []));
    pbBundleSets[trade] = JSON.parse(JSON.stringify(priceBook?.[trade + '_bundles'] || []));
    pbBundleDefaults[trade] = Object.assign({ good:'', better:'', best:'' }, priceBook?.[trade + '_tier_defaults'] || {});
  });
  pbRoofView = 'products';
  pbEditBundleId = null;
  _pbBulletsOpen = {};
  pbActiveTrade = 'roofing';
  pbActiveTab = 'master';
  pbView = 'catalog';
  pbEditPresetId = null;
  renderPBModal();
  document.getElementById('pricebook-modal').classList.remove('hidden');
}

function closePriceBook() {
  document.getElementById('pricebook-modal').classList.add('hidden');
}
function maybePBModalClose(e) {
  if (e.target === document.getElementById('pricebook-modal')) closePriceBook();
}

/* ── Exterior Design Studio catalog (manager+) ───────────────────────
   One row is one installed product/color combination. The optional bundle
   link helps the designer start on the system already quoted, but this visual
   catalog never changes estimate scope or pricing. */
const EXTERIOR_CATEGORIES = [
  ['roof','Roof'], ['siding','Siding'], ['trim','Trim / Fascia'],
  ['soffit','Soffit'], ['door','Doors'], ['gutter','Gutters'],
  ['window','Windows'], ['metal','Metal accents'], ['shutter','Shutters'],
  ['stucco','Stucco'], ['paint','Paint']
];
let exCatalogItems = [];
let exCatalogCategory = 'roof';
let exCatalogSearch = '';
let exCatalogDirty = false;

async function openExteriorCatalog() {
  if (!_meCanViewAll()) return;
  closePriceBook();
  const modal = document.getElementById('exterior-catalog-modal');
  const body = document.getElementById('exterior-catalog-body');
  if (!modal || !body) return;
  modal.classList.remove('hidden');
  body.innerHTML = '<div class="ex-empty">Loading exterior catalog…</div>';
  try {
    const res = await fetch('/api/exterior-catalog');
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not load catalog');
    exCatalogItems = Array.isArray(data.entries) ? data.entries : [];
    exCatalogDirty = false;
    renderExteriorCatalog();
  } catch (error) {
    body.innerHTML = '<div class="ex-empty">' + esc(error.message) + '</div>';
  }
}
function closeExteriorCatalog(force) {
  if (!force && exCatalogDirty && !confirm('Close without saving your exterior catalog changes?')) return false;
  document.getElementById('exterior-catalog-modal')?.classList.add('hidden');
  exCatalogDirty = false;
  return true;
}
function maybeExteriorCatalogClose(e) {
  if (e.target === document.getElementById('exterior-catalog-modal')) closeExteriorCatalog();
}
function exOpenPriceBook() {
  if (closeExteriorCatalog()) openPriceBook();
}
function exSetCategory(category) {
  if (!EXTERIOR_CATEGORIES.some(c => c[0] === category)) return;
  exCatalogCategory = category;
  renderExteriorCatalog();
}
function exSetSearch(value) {
  exCatalogSearch = (value || '').toLowerCase();
  renderExteriorCatalog();
  const input = document.getElementById('ex-catalog-search');
  if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
}
function exBundleOptions(entry) {
  let trade = '';
  if (entry.category === 'roof') trade = 'roofing';
  const surface = entry.category === 'paint' ? entry.applies_to : entry.category;
  if (['siding','trim','soffit','shutter','stucco'].includes(surface)) trade = 'siding';
  if (surface === 'window') trade = 'windows';
  if (surface === 'gutter') trade = 'gutters';
  const bundles = trade ? ((priceBook || {})[trade + '_bundles'] || []) : [];
  let html = '<option value="">No pricing link</option>';
  if (entry.price_book_bundle && !bundles.some(b => b.id === entry.price_book_bundle)) {
    html += '<option value="' + esc(entry.price_book_bundle) + '" selected>Saved link: ' +
      esc(entry.price_book_bundle) + '</option>';
  }
  html += bundles.map(b => '<option value="' + esc(b.id) + '"' +
    (b.id === entry.price_book_bundle ? ' selected' : '') + '>' + esc(b.name || b.id) + '</option>').join('');
  return html;
}
function exSetField(index, field, value, rerender) {
  const entry = exCatalogItems[index];
  if (!entry) return;
  entry[field] = value;
  if (field === 'category' && value !== 'paint') {
    entry.applies_to = value === 'roof' ? 'roof' : value;
  }
  exCatalogDirty = true;
  if (rerender) renderExteriorCatalog();
}
function exSetHex(index, value, sibling) {
  const hex = String(value || '').trim().toLowerCase();
  exSetField(index, 'hex', hex, false);
  if (sibling) sibling.value = hex;
}
function exAddRow() {
  exCatalogItems.push({
    category: exCatalogCategory, brand: '', product: '', style: '',
    color: '', color_code: '', hex: '#777777',
    applies_to: exCatalogCategory === 'paint' ? 'siding' : exCatalogCategory,
    price_book_bundle: '', texture_ref: '', texture_scale: 96,
    placement_image_ref: '', active: true
  });
  exCatalogDirty = true;
  exCatalogSearch = '';
  renderExteriorCatalog();
}
function exDeleteRow(index) {
  const entry = exCatalogItems[index];
  if (!entry) return;
  if (!confirm('Remove ' + (entry.product || 'this product/color') + ' from the Design Studio catalog?')) return;
  exCatalogItems.splice(index, 1);
  exCatalogDirty = true;
  renderExteriorCatalog();
}
function exCatalogStatus(message, kind) {
  const el = document.getElementById('ex-catalog-status');
  if (!el) return;
  el.className = 'ex-status ' + (kind || '');
  el.textContent = message || '';
}
async function exUploadTexture(index, input) {
  const file = input?.files?.[0];
  if (!file || !exCatalogItems[index]) return;
  exCatalogStatus('Uploading texture…');
  try {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch('/api/exterior-catalog/texture', {method:'POST', body:form});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Texture upload failed');
    exCatalogItems[index].texture_ref = data.texture_ref;
    if (!exCatalogItems[index].texture_scale) exCatalogItems[index].texture_scale = 96;
    exCatalogDirty = true;
    renderExteriorCatalog();
    exCatalogStatus('Texture uploaded. Save the catalog to attach it.', 'ok');
  } catch (error) { exCatalogStatus(error.message, 'err'); }
  finally { input.value = ''; }
}
async function exUploadPlacementImage(index, input) {
  const file = input?.files?.[0];
  if (!file || !exCatalogItems[index]) return;
  exCatalogStatus('Uploading product cutout…');
  try {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch('/api/exterior-catalog/placement-image', {method:'POST', body:form});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Product image upload failed');
    exCatalogItems[index].placement_image_ref = data.placement_image_ref;
    exCatalogDirty = true;
    renderExteriorCatalog();
    exCatalogStatus('Product cutout uploaded. Save the catalog to attach it.', 'ok');
  } catch (error) { exCatalogStatus(error.message, 'err'); }
  finally { input.value = ''; }
}
function exClearTexture(index) {
  if (!exCatalogItems[index]) return;
  exCatalogItems[index].texture_ref = '';
  exCatalogDirty = true;
  renderExteriorCatalog();
}
function exClearPlacementImage(index) {
  if (!exCatalogItems[index]) return;
  exCatalogItems[index].placement_image_ref = '';
  exCatalogDirty = true;
  renderExteriorCatalog();
}
function renderExteriorCatalog() {
  const body = document.getElementById('exterior-catalog-body');
  if (!body) return;
  const counts = Object.fromEntries(EXTERIOR_CATEGORIES.map(c => [
    c[0], exCatalogItems.filter(e => e.category === c[0]).length
  ]));
  const search = exCatalogSearch.trim();
  const visible = exCatalogItems.map((entry, index) => ({entry, index})).filter(x => {
    if (x.entry.category !== exCatalogCategory) return false;
    if (!search) return true;
    return [x.entry.brand, x.entry.product, x.entry.style, x.entry.color, x.entry.color_code]
      .join(' ').toLowerCase().includes(search);
  });
  const rows = visible.map(x => {
    const e = x.entry, i = x.index;
    const surfaceLabels = Object.fromEntries(EXTERIOR_CATEGORIES.filter(c => !['roof','paint'].includes(c[0])));
    const applies = e.category === 'paint'
      ? '<select onchange="exSetField(' + i + ',\'applies_to\',this.value,true)">' +
          Object.entries(surfaceLabels).map(([value,label]) => '<option value="' + value + '"' +
            (e.applies_to === value ? ' selected' : '') + '>' + esc(label) + '</option>').join('') + '</select>'
      : '<span>' + esc((EXTERIOR_CATEGORIES.find(c => c[0] === e.category) || [0,e.category])[1]) + '</span>';
    const safeHex = /^#[0-9a-f]{6}$/i.test(e.hex || '') ? e.hex : '#777777';
    const texture = e.texture_ref
      ? '<div class="ex-texture-cell"><img src="' + BASE + '/uploads/' + esc(e.texture_ref) + '" alt="Texture preview">' +
        '<button class="btn small" onclick="exClearTexture(' + i + ')">Remove</button></div>'
      : '<label class="btn small ex-texture-upload">Upload<input type="file" accept="image/png,image/jpeg,image/webp" hidden onchange="exUploadTexture(' + i + ',this)"></label>';
    const canPlace = ['door','window','shutter'].includes(e.category) ||
      (e.category === 'paint' && ['door','window','shutter'].includes(e.applies_to));
    const placement = !canPlace ? '<span class="ex-muted">—</span>'
      : e.placement_image_ref
      ? '<div class="ex-texture-cell"><img src="' + BASE + '/uploads/' + esc(e.placement_image_ref) + '" alt="Product cutout preview">' +
        '<button class="btn small" onclick="exClearPlacementImage(' + i + ')">Remove</button></div>'
      : '<label class="btn small ex-texture-upload">Upload<input type="file" accept="image/png,image/jpeg,image/webp" hidden onchange="exUploadPlacementImage(' + i + ',this)"></label>';
    return '<tr>' +
      '<td><input type="text" value="' + esc(e.brand || '') + '" placeholder="Manufacturer" oninput="exSetField(' + i + ',\'brand\',this.value)"></td>' +
      '<td><input type="text" value="' + esc(e.product || '') + '" placeholder="Product / series" oninput="exSetField(' + i + ',\'product\',this.value)"></td>' +
      '<td><input type="text" value="' + esc(e.style || '') + '" placeholder="Style / profile" oninput="exSetField(' + i + ',\'style\',this.value)"></td>' +
      '<td><input type="text" value="' + esc(e.color || '') + '" placeholder="Color name" oninput="exSetField(' + i + ',\'color\',this.value)"></td>' +
      '<td><input type="text" value="' + esc(e.color_code || '') + '" placeholder="Code" oninput="exSetField(' + i + ',\'color_code\',this.value)"></td>' +
      '<td><div class="ex-color-cell"><input type="color" value="' + safeHex + '" onchange="exSetHex(' + i + ',this.value,this.nextElementSibling)">' +
        '<input type="text" value="' + esc(e.hex || '') + '" placeholder="#1a2b3c" onchange="exSetHex(' + i + ',this.value,this.previousElementSibling)"></div></td>' +
      '<td>' + applies + '</td>' +
      '<td><select class="ex-bundle-select" onchange="exSetField(' + i + ',\'price_book_bundle\',this.value)">' + exBundleOptions(e) + '</select></td>' +
      '<td>' + texture + '<label class="ex-texture-scale">Tile px<input type="number" min="16" max="512" value="' +
        esc(e.texture_scale || 96) + '" onchange="exSetField(' + i + ',\'texture_scale\',this.value)"></label></td>' +
      '<td>' + placement + '</td>' +
      '<td style="text-align:center"><input type="checkbox"' + (e.active !== false ? ' checked' : '') +
        ' onchange="exSetField(' + i + ',\'active\',this.checked)"></td>' +
      '<td><button class="pb-del-btn" onclick="exDeleteRow(' + i + ')" title="Remove">✕</button></td>' +
      '</tr>';
  }).join('');
  body.innerHTML =
    '<div class="ex-toolbar"><div class="ex-toolbar-group">' +
      '<button class="btn-secondary" onclick="exOpenPriceBook()">← Price Book</button>' +
      '<div class="ex-tabs">' + EXTERIOR_CATEGORIES.map(c =>
        '<button class="ex-tab ' + (c[0] === exCatalogCategory ? 'active' : '') +
        '" onclick="exSetCategory(\'' + c[0] + '\')">' + c[1] +
        '<span class="ex-count">' + counts[c[0]] + '</span></button>').join('') + '</div></div>' +
      '<div class="ex-toolbar-group"><input id="ex-catalog-search" class="ex-search" value="' + esc(exCatalogSearch) +
        '" placeholder="Search this category…" oninput="exSetSearch(this.value)">' +
        '<a class="btn-secondary" href="' + BASE + '/api/exterior-catalog/template.csv">↓ CSV Template</a>' +
        '<button class="btn-secondary" onclick="document.getElementById(\'ex-csv-input\').click()">↑ Upload CSV</button>' +
        '<input id="ex-csv-input" type="file" accept=".csv,text/csv" hidden onchange="exImportCsv(this)">' +
        '<button class="btn-secondary" onclick="exExportCsv()">Export Current</button></div></div>' +
    '<p class="ex-help"><strong>One row = one installed product/color.</strong> Add a square material swatch as the optional texture. For doors, windows, and shutters, add a front-facing product cutout so it can be placed directly on the customer\'s photo. Transparent PNGs work best. Pricing links are handoff references only.</p>' +
    '<div class="ex-table-wrap"><table class="ex-table"><thead><tr>' +
      '<th>Brand</th><th>Product / Series</th><th>Style / Profile</th><th>Color</th><th>Color Code</th><th>Preview Hex</th><th>Applies To</th><th>Price Book Link</th><th>Texture</th><th>Product Cutout</th><th>Active</th><th></th>' +
      '</tr></thead><tbody>' + (rows || '<tr><td colspan="12" class="ex-empty">No matching rows. Add one or upload the CSV template.</td></tr>') +
      '</tbody></table></div>' +
    '<div class="ex-footer"><div><button class="btn-secondary" onclick="exAddRow()">+ Add Product / Color</button></div>' +
      '<div class="ex-toolbar-group"><span id="ex-catalog-status" class="ex-status">' +
        (exCatalogDirty ? 'Unsaved changes' : exCatalogItems.length + ' catalog rows') + '</span>' +
        '<button class="btn-primary" id="ex-catalog-save" onclick="exSaveCatalog()">💾 Save Exterior Catalog</button></div></div>';
}

function exParseCsv(text) {
  const rows = [], row = [];
  let field = '', quoted = false;
  const pushField = () => { row.push(field); field = ''; };
  const pushRow = () => {
    pushField();
    if (row.some(v => String(v).trim())) rows.push(row.splice(0));
    else row.splice(0);
  };
  text = String(text || '').replace(/^\uFEFF/, '');
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (ch === '"') quoted = false;
      else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') pushField();
    else if (ch === '\n') pushRow();
    else if (ch !== '\r') field += ch;
  }
  if (field || row.length) pushRow();
  if (quoted) throw new Error('CSV has an unclosed quote');
  if (rows.length < 2) throw new Error('CSV needs a header and at least one product row');
  const headers = rows.shift().map(h => String(h).trim().toLowerCase().replace(/[\s-]+/g, '_'));
  return rows.map(values => {
    const raw = {};
    headers.forEach((h, i) => { raw[h] = String(values[i] || '').trim(); });
    return {
      category: raw.category, brand: raw.brand,
      product: raw.product || raw.series, style: raw.style || raw.profile,
      color: raw.color || raw.color_name, color_code: raw.color_code || raw.code,
      hex: raw.hex || raw.color_hex, applies_to: raw.applies_to,
      price_book_bundle: raw.price_book_bundle || raw.bundle_id,
      texture_ref: raw.texture_ref, texture_scale: raw.texture_scale || 96,
      placement_image_ref: raw.placement_image_ref || raw.product_image_ref,
      active: raw.active
    };
  });
}
async function exImportCsv(input) {
  const file = input && input.files && input.files[0];
  if (!file) return;
  exCatalogStatus('Importing ' + file.name + '…');
  try {
    const rows = exParseCsv(await file.text());
    const res = await fetch('/api/exterior-catalog/import', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({rows: rows})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Import failed');
    exCatalogItems = data.entries || [];
    priceBook.exterior_catalog = exCatalogItems;
    exCatalogDirty = false;
    renderExteriorCatalog();
    exCatalogStatus('Imported ' + data.imported + ' rows; added ' + data.added + '. Catalog saved.', 'ok');
  } catch (error) {
    exCatalogStatus(error.message, 'err');
  } finally {
    input.value = '';
  }
}
async function exSaveCatalog() {
  const button = document.getElementById('ex-catalog-save');
  if (button) { button.disabled = true; button.textContent = 'Saving…'; }
  try {
    const res = await fetch('/api/exterior-catalog', {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({entries: exCatalogItems})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not save catalog');
    exCatalogItems = data.entries || [];
    priceBook.exterior_catalog = exCatalogItems;
    exCatalogDirty = false;
    renderExteriorCatalog();
    exCatalogStatus('Saved ' + data.count + ' catalog rows.', 'ok');
    if (activePage === 'visualizer') renderVisualizerPage();
  } catch (error) {
    exCatalogStatus(error.message, 'err');
    if (button) { button.disabled = false; button.textContent = '💾 Save Exterior Catalog'; }
  }
}
function exCsvCell(value) {
  const text = String(value == null ? '' : value);
  return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
}
function exExportCsv() {
  const headers = ['category','brand','product','style','color','color_code','hex','applies_to','price_book_bundle','texture_ref','texture_scale','placement_image_ref','active'];
  const lines = [headers.join(',')].concat(exCatalogItems.map(e =>
    headers.map(h => exCsvCell(e[h] === undefined ? '' : e[h])).join(',')));
  const url = URL.createObjectURL(new Blob([lines.join('\r\n') + '\r\n'], {type:'text/csv'}));
  const a = document.createElement('a');
  a.href = url; a.download = 'exterior-catalog.csv'; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ── Design Studio operations dashboard (manager+, read only) ─────── */
function _vzOpsBytes(value) {
  let bytes = Math.max(0, Number(value) || 0);
  const units = ['B','KB','MB','GB'];
  let unit = 0;
  while (bytes >= 1024 && unit < units.length - 1) { bytes /= 1024; unit++; }
  return (unit ? bytes.toFixed(bytes >= 10 ? 1 : 2) : Math.round(bytes)) + ' ' + units[unit];
}
function _vzOpsMoney(microusd) {
  const dollars=(Number(microusd)||0)/1000000;
  if(dollars!==0&&Math.abs(dollars)<0.01)return '$'+dollars.toFixed(4).replace(/0+$/,'').replace(/\.$/,'');
  return '$'+dollars.toFixed(2);
}
function _vzOpsTable(title, rows, valueLabel, valueKey, emptyText, countLabel = 'Requests') {
  rows = Array.isArray(rows) ? rows : [];
  if (!rows.length) return '<section class="vz-ops-section"><h3>' + esc(title) + '</h3><p class="vz-ops-empty">' + esc(emptyText || 'No activity in this period.') + '</p></section>';
  return '<section class="vz-ops-section"><h3>' + esc(title) + '</h3><div class="vz-ops-table-wrap"><table class="vz-ops-table"><thead><tr><th>Name</th><th>' + esc(countLabel) + '</th><th>' + esc(valueLabel || 'Estimated cost') + '</th></tr></thead><tbody>' +
    rows.map(row => '<tr><td>' + esc(row.name || row.day || 'Unknown') + '</td><td>' + esc(row.requests ?? row.count ?? 0) + '</td><td>' +
      (valueKey === 'bytes' ? _vzOpsBytes(row.bytes) : _vzOpsMoney(row.cost_microusd)) + '</td></tr>').join('') +
    '</tbody></table></div></section>';
}
async function openVisualizerOperations(days = 30, candidateAgeDays = 30) {
  if (!_meCanViewAll()) return;
  const modal = document.getElementById('visualizer-operations-modal');
  const body = document.getElementById('visualizer-operations-body');
  if (!modal || !body) return;
  modal.classList.remove('hidden');
  body.innerHTML = '<div class="vz-ops-loading">Loading Design Studio operations…</div>';
  try {
    const query = new URLSearchParams({days:String(days), candidate_age_days:String(candidateAgeDays)});
    const response = await fetch('/api/visualizer/operations?' + query);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not load Design Studio operations.');
    renderVisualizerOperations(data, days, candidateAgeDays);
  } catch (error) {
    body.innerHTML = '<div class="vz-ops-error">' + esc(error.message) + '</div>';
  }
}
function closeVisualizerOperations() {
  document.getElementById('visualizer-operations-modal')?.classList.add('hidden');
}
function maybeCloseVisualizerOperations(event) {
  if (event.target?.id === 'visualizer-operations-modal') closeVisualizerOperations();
}
function renderVisualizerOperations(data, days, candidateAgeDays) {
  const body = document.getElementById('visualizer-operations-body');
  if (!body) return;
  const usage = data.usage || {}, storage = data.storage || {};
  const statuses = usage.by_status || {};
  const pendingCount=Number(statuses.submitting||0)+Number(statuses.submitted||0)+Number(statuses.pending||0)+Number(statuses.unknown||0);
  const finishedCount=Number(statuses.completed||0)+Number(statuses.not_found||0);
  const kinds = (storage.by_kind || []).map(row => ({name:String(row.kind || '').replace(/_/g,' '), requests:row.files, bytes:row.bytes}));
  const candidates = storage.cleanup_candidates || {};
  const candidateRows = Array.isArray(candidates.items) ? candidates.items : [];
  const price = usage.price || {};
  body.innerHTML =
    '<div class="vz-ops-toolbar"><label>Usage window<select onchange="openVisualizerOperations(this.value,' + Number(candidateAgeDays || 30) + ')">' +
      [7,30,90,365].map(n => '<option value="' + n + '"' + (Number(days)===n?' selected':'') + '>' + n + ' days</option>').join('') + '</select></label>' +
      '<label>Unused-file age<select onchange="openVisualizerOperations(' + Number(days || 30) + ',this.value)">' +
      [7,30,60,90].map(n => '<option value="' + n + '"' + (Number(candidateAgeDays)===n?' selected':'') + '>' + n + '+ days</option>').join('') + '</select></label>' +
      '<button class="btn-secondary" onclick="openVisualizerOperations(' + Number(days || 30) + ',' + Number(candidateAgeDays || 30) + ')">Refresh</button></div>' +
    '<div class="vz-ops-cards">' +
      '<article><span>fal requests</span><strong>' + esc(usage.submitted_requests || 0) + '</strong><small>' + esc(usage.attempts || 0) + ' tracked attempts</small></article>' +
      '<article><span>Estimated cost</span><strong>' + _vzOpsMoney(usage.estimated_cost_microusd) + '</strong><small>' + esc(usage.unpriced_requests || 0) + ' unpriced</small></article>' +
      '<article><span>Finished</span><strong>' + esc(finishedCount) + '</strong><small>' + esc(statuses.not_found || 0) + ' no surface · ' + esc(pendingCount) + ' pending · ' + esc(statuses.failed || 0) + ' failed</small></article>' +
      '<article><span>Visual files</span><strong>' + esc(storage.total_files || 0) + '</strong><small>' + _vzOpsBytes(storage.total_bytes) + ' total</small></article>' +
      '<article><span>Referenced</span><strong>' + esc(storage.referenced_files || 0) + '</strong><small>' + _vzOpsBytes(storage.referenced_bytes) + '</small></article>' +
      '<article><span>Review candidates</span><strong>' + esc(candidates.total_files || 0) + '</strong><small>' + _vzOpsBytes(candidates.total_bytes) + ' · read only</small></article>' +
    '</div>' +
    '<div class="vz-ops-note"><strong>Estimate only.</strong> The fal invoice is authoritative. This dashboard tracks fal-accepted detection submissions recorded after this feature was deployed' +
      (price.unit_cost_microusd != null ? '; the current estimate rate is ' + _vzOpsMoney(price.unit_cost_microusd) + ' each' : '') +
      '. It never exposes API keys, customer photos, or delete controls.</div>' +
    (Number(usage.billing_unknown_attempts||0)>0?'<div class="vz-ops-warning"><strong>Billing needs review:</strong> '+esc(usage.billing_unknown_attempts)+' attempt(s) may have reached fal but did not produce a confirmed billing state. Compare these with the fal invoice.</div>':'')+
    '<div class="vz-ops-grid">' +
      _vzOpsTable('Usage by day', usage.by_day) +
      _vzOpsTable('Usage by rep', usage.by_rep) +
      _vzOpsTable('Usage by project', usage.by_project) +
      _vzOpsTable('Usage by surface', usage.by_surface) +
      _vzOpsTable('Storage by type', kinds, 'Storage', 'bytes', 'No visual files found.', 'Files') +
    '</div>' +
    '<section class="vz-ops-section vz-ops-candidates"><h3>Old unreferenced files to review</h3>' +
      '<p>These files are not referenced by any saved estimate or live catalog entry and are at least ' + esc(candidates.minimum_age_days || candidateAgeDays) + ' days old. Nothing is deleted here.'+(candidates.truncated?' Showing the first '+esc(candidateRows.length)+' of '+esc(candidates.total_files||candidateRows.length)+'.':'')+'</p>' +
      (candidateRows.length ? '<div class="vz-ops-table-wrap"><table class="vz-ops-table vz-ops-candidate-table"><thead><tr><th>Internal file</th><th>Type</th><th>Size</th><th>Age</th><th>Reason</th><th>Confidence</th></tr></thead><tbody>' +
        candidateRows.map(row => '<tr><td class="vz-ops-ref">' + esc(row.ref || '') + '</td><td>' + esc(String(row.kind || '').replace(/_/g,' ')) + '</td><td>' + _vzOpsBytes(row.bytes) + '</td><td>' + esc(row.age_days || 0) + ' days</td><td>'+esc(row.reason||'Unreferenced')+'</td><td>' + esc(row.confidence || '') + '</td></tr>').join('') + '</tbody></table></div>'
        : '<p class="vz-ops-empty">No old cleanup candidates found.</p>') + '</section>' +
    ((storage.warnings || []).length ? '<div class="vz-ops-warning">' + (storage.warnings || []).map(esc).join('<br>') + '</div>' : '');
}

const PB_SUBTABS = [['master','Master Catalog'],['good','Good'],['better','Better'],['best','Best']];

function renderPBModal() {
  const isRoof = isBundleTrade(pbActiveTrade);
  document.getElementById('pb-modal-body').innerHTML = `
    <div class="pb-trade-bar">
      ${PB_TRADES.map(t => `
        <button class="pb-trade-btn ${t===pbActiveTrade?'active':''}"
          onclick="pbSetTrade('${t}')">
          ${TRADE_LABELS[t]}
        </button>`).join('')}
    </div>
    <div class="pb-content">
      ${isRoof ? pbRenderBundleTrade() : `
      <div class="pb-toolbar">
        <h3>${TRADE_LABELS[pbActiveTrade]} Price Book</h3>
        <div class="pb-view-toggle">
          <button class="pb-view-btn ${pbView==='catalog'?'active':''}" onclick="pbSetView('catalog')">Catalog</button>
          <button class="pb-view-btn pb-view-good   ${pbView==='good'?'active':''}"   onclick="pbSetView('good')">Good</button>
          <button class="pb-view-btn pb-view-better ${pbView==='better'?'active':''}" onclick="pbSetView('better')">Better</button>
          <button class="pb-view-btn pb-view-best   ${pbView==='best'?'active':''}"   onclick="pbSetView('best')">Best</button>
          <button class="pb-view-btn ${pbView==='presets'?'active':''}" onclick="pbSetView('presets')">Presets</button>
        </div>
        ${pbView==='catalog' ? `
          <button class="btn-secondary" onclick="pbImportFromEstimate()" title="Copy current estimate items into this trade">↑ Import from Estimate</button>
          <button class="btn-secondary" onclick="pbApplyToEstimate()" title="Replace estimate items with price book">↓ Load into Estimate</button>` : ''}
      </div>
      ${pbView==='catalog' ? pbRenderMaster()
        : (pbView==='good'||pbView==='better'||pbView==='best') ? pbRenderTier(pbView)
        : pbRenderPresets()}`}
    </div>
    <div class="pb-footer">
      ${isRoof ? `
      <span class="pb-footer-note">${pbRoofView==='products'
        ? 'Products — your full material &amp; labor list, one price each. Bundles pull from this list.'
        : 'Bundles — the Good / Better / Best dropdown options. Each pulls products from your catalog.'}</span>
      <button class="btn-primary" onclick="pbSave()">💾 Save Price Book</button>`
      : `
      <span class="pb-footer-note">${
        pbView==='catalog' ? 'Master catalog — set costs for each tier. Leave Better/Best blank to inherit from Good.'
        : pbView==='good'  ? 'Good package — set which products are included and the cost. Add from the dropdown below.'
        : pbView==='better'? 'Better package — set which products are included and the cost per product.'
        : pbView==='best'  ? 'Best package — premium tier. Set costs independently from Good/Better.'
        : 'Brand presets are one-click bundles reps load in Simple mode.'}</span>
      <button class="btn-secondary" onclick="pbIncludeAllTiers()" title="Mark every item as included in Good, Better, and Best">✓ Include All Tiers</button>
      <button class="btn-secondary" onclick="pbResetTrade()" style="color:var(--danger)">Reset to Defaults</button>
      <button class="btn-primary" onclick="pbSave()">💾 Save Price Book</button>`}
    </div>`;
}

/* ── Bundle-trade Price Book: flat product catalog + named bundles ─────────
   Products = every material/labor line for the trade with one price. Bundles =
   the Good/Better/Best dropdown options, each pulling products from the catalog.
   Working copies (per trade): pbCat() / pbBundles() / pbDefs(). */
function pbSetRoofView(v) { pbRoofView = v; pbEditBundleId = null; renderPBModal(); }
// Switching trades resets the bundle editor state so the Products/Bundles view
// and any open bundle never carry over to a different trade's catalog.
function pbSetTrade(t) { pbActiveTrade = t; pbRoofView = 'products'; pbEditBundleId = null; _pbBulletsOpen = {}; renderPBModal(); }

function pbRenderBundleTrade() {
  return `
    <div class="pb-toolbar">
      <h3>${TRADE_LABELS[pbActiveTrade]} Price Book</h3>
      <div class="pb-view-toggle">
        <button class="pb-view-btn ${pbRoofView==='products'?'active':''}" onclick="pbSetRoofView('products')">Products</button>
        <button class="pb-view-btn ${pbRoofView==='bundles'?'active':''}" onclick="pbSetRoofView('bundles')">Bundles (dropdowns)</button>
      </div>
    </div>
    ${pbRoofView==='products' ? pbRenderRoofCatalog() : pbRenderRoofBundles()}`;
}

function pbRenderRoofCatalog() {
  // Stable-sort by group for display only, so an existing catalog that
  // interleaves LP/Hardie/EDCO rows still renders as clean grouped blocks.
  // The manager's underlying order is untouched — moves via ↑↓ act on the
  // real positions and the render redraws grouped again. Order within a
  // group stays whatever it was in the underlying array. The order of
  // GROUPS themselves is fixed by _SIDING_GROUP_ORDER for siding; other
  // trades fall back to first-seen order.
  const rawItems = pbCat();
  let items = rawItems;
  if (pbActiveTrade === 'siding' && rawItems.some(x => (x.group || '').trim())) {
    const order = _SIDING_GROUP_ORDER;
    // Map: original index → item. Preserved so pbRoofCatSet/Move indices
    // stay pointing at the true position in pbCat() (they always do — we
    // sort only what the map receives). The ↑↓ buttons still use the same
    // pbCat indices, but after a Move the render sorts again on the new
    // positions.
    const rank = it => {
      const g = (it.group || '').trim() || 'Ungrouped';
      const idx = order.indexOf(g);
      return idx === -1 ? order.length : idx;
    };
    // Track original indices so the row's onclick handlers still target
    // pbCat()[origIdx]. Stable sort — ties keep the original ordering.
    items = rawItems.map((it, i) => ({it, i, r: rank(it)}))
                    .sort((a, b) => a.r - b.r || a.i - b.i)
                    .map(x => Object.assign({}, x.it, {__origIdx: x.i}));
  }
  const measOpts = sel => `<option value="">Manual</option>` +
    Object.entries(MEASURE_DEFS).map(([k,d])=>`<option value="${k}" ${sel===k?'selected':''}>${esc(d.label)}</option>`).join('');
  return `
    <div class="pb-roof-intro">Your full ${esc(TRADE_LABELS[pbActiveTrade].toLowerCase())} material &amp; labor list — one price each. Add every product you use; bundles pull from this list. <strong>💬</strong> sets what each one says on the customer's Good/Better/Best card.</div>
    <div class="pb-table-wrap">
    <table class="pb-table">
      <thead><tr>
        <th style="width:42px"></th>
        <th class="pb-th-name">Product</th>
        <th class="pb-th-unit">Unit</th>
        <th class="pb-th-auto">Auto Qty From</th>
        <th class="pb-th-basecost">Price</th>
        <th class="pb-th-vis" title="Show on the customer estimate">Show</th>
        <th></th>
      </tr></thead>
      <tbody>
        ${items.length ? (() => {
          // Insert a group subheader row when a product's `group` differs
          // from the previous product's. Only kicks in when the catalog
          // actually carries a group on any product; a manager who never
          // grouped their catalog still sees the flat list they had before.
          const anyGrouped = items.some(x => (x.group || '').trim());
          return items.map((it, visI) => {
            // Sorted view: visI is the position in `items` (rendered order),
            // origI is the position in pbCat() the onclick handlers must
            // target. When not sorted, __origIdx is absent → same as visI.
            const i = (it.__origIdx !== undefined) ? it.__origIdx : visI;
            const grp = (it.group || '').trim();
            const prevGrp = visI > 0 ? ((items[visI - 1].group || '').trim()) : '__none__';
            const headerRow = anyGrouped && grp !== prevGrp ? `
              <tr class="pb-group-hd">
                <td colspan="7">${esc(grp || 'Ungrouped')}</td>
              </tr>` : '';
            const rawLen = rawItems.length;
            return headerRow + `
            <tr class="pb-item-row">
              <td class="pb-order-cell">
                <button class="pb-order-btn" onclick="pbRoofCatMove(${i},-1)" ${i===0?'disabled':''} title="Move up">↑</button>
                <button class="pb-order-btn" onclick="pbRoofCatMove(${i},1)" ${i===rawLen-1?'disabled':''} title="Move down">↓</button>
              </td>
              <td><input class="pb-input-name" type="text" value="${esc(it.name||'')}" oninput="pbRoofCatSet(${i},'name',this.value)" placeholder="Product name"></td>
              <td><input class="pb-input-unit" type="text" value="${esc(it.unit||'')}" oninput="pbRoofCatSet(${i},'unit',this.value)" placeholder="Unit"></td>
              <td class="pb-auto-cell"><select class="pb-measure-select" onchange="pbRoofCatSet(${i},'measure',this.value)">${measOpts(it.measure||'')}</select></td>
              <td><div class="pb-tier-cost-wrap"><span class="pb-tier-dollar">$</span>
                <input class="pb-tier-cost" type="number" min="0" step="0.01" value="${it.cost!==undefined&&it.cost!==''?it.cost:''}" placeholder="0.00" onchange="pbRoofCatSet(${i},'cost',this.value)"></div></td>
              <td style="text-align:center"><input type="checkbox" ${it.customer_visible!==false?'checked':''} onchange="pbRoofCatSet(${i},'customer_visible',this.checked)"></td>
              <td class="pb-cat-actions">
                <button class="pb-order-btn ${_pbBulletsOpen[it.id]?'on':''}" onclick="pbToggleBullets('${it.id}')"
                  title="What this product says on the Good/Better/Best card">💬</button>
                <button class="pb-del-btn" onclick="pbRoofCatDel(${i})" title="Delete product">✕</button>
              </td>
            </tr>
            ${_pbBulletsOpen[it.id] ? `
            <tr class="pb-bullets-row"><td></td><td colspan="6">
              <label class="pb-variant-field-label">Tagline <small>one line under the package price when this product is the primary material — overrides the bundle's default</small></label>
              <input class="pb-bullets-ta" type="text"
                value="${esc(it.desc||'')}"
                placeholder="Short customer-facing tagline for this product"
                onchange="pbRoofCatSetDesc(${i},this.value)">
              <label class="pb-variant-field-label" style="margin-top:10px">Customer wording <small>one bullet per line</small></label>
              <textarea class="pb-bullets-ta" rows="3"
                placeholder="${esc(it.name||'Product name')}"
                onchange="pbRoofCatSetBullets(${i},this.value)">${esc((it.bullets||[]).join('\n'))}</textarea>
              <label class="pb-bullet-silence">
                <input type="checkbox" ${Array.isArray(it.bullets)&&!it.bullets.length?'checked':''}
                  onchange="pbRoofCatSilence(${i},this.checked)">
                Say nothing on the card
              </label>
              <div class="pb-bundle-copy-hint">${
                (Array.isArray(it.bullets) && !it.bullets.length)
                  ? 'Silent — this product is in the scope and priced, but says nothing on the card.'
                : (Array.isArray(it.bullets) && it.bullets.length)
                  ? ('Shown on every package that includes this product.' + (it.customer_visible === false
                      ? ' Show is off, so the customer reads these lines without seeing the price broken out.' : ''))
                : (it.customer_visible === false)
                  ? 'Not set, and Show is off — this product says nothing on the card. Write the wording above to promise the work without showing its price.'
                  : `Not set — the card falls back to the product name, “${esc(it.name||'')}”.`}</div>
            </td></tr>` : ''}`;
          }).join('');
        })() : `<tr><td colspan="7" class="pb-empty">No products yet — add your first below.</td></tr>`}
      </tbody>
    </table>
    </div>
    <div style="margin-top:10px"><button class="btn-secondary" onclick="pbRoofCatAdd()">+ Add Product</button></div>`;
}
// Which products have their customer-wording editor expanded, by product id.
let _pbBulletsOpen = {};
function pbToggleBullets(pid) { _pbBulletsOpen[pid] = !_pbBulletsOpen[pid]; renderPBModal(); }
/* Empty box DELETES the key rather than saving [] — absence means "fall back to
   the product name", which is what a manager who never touched this wants.
   Deliberate silence is the checkbox below (an explicit []), NOT the Show
   column: Show governs whether the customer sees the priced row, and labor is
   exactly the case where the answer to "show the price?" and "say we do it?"
   differ. See bundleFeatures(). */
function pbRoofCatSetBullets(i, text) {
  const it = pbCat()[i]; if (!it) return;
  const lines = text.split('\n').map(s => s.trim()).filter(Boolean);
  if (lines.length) it.bullets = lines; else delete it.bullets;
  renderPBModal();
}
// Optional per-product tagline. When a bundle is applied to a tier, the first
// product in that bundle with a non-empty `desc` wins over the bundle's own
// description — so swapping the primary material also swaps the customer story.
function pbRoofCatSetDesc(i, text) {
  const it = pbCat()[i]; if (!it) return;
  const t = (text || '').trim();
  if (t) it.desc = t; else delete it.desc;
}
function pbRoofCatSilence(i, on) {
  const it = pbCat()[i]; if (!it) return;
  if (on) it.bullets = []; else delete it.bullets;
  renderPBModal();
}
function pbRoofCatSet(i, field, val) {
  const it = pbCat()[i]; if (!it) return;
  if (field === 'cost') it.cost = val === '' ? 0 : (parseFloat(val)||0);
  else if (field === 'customer_visible') it.customer_visible = val;
  else if (field === 'measure') { if (val) it.measure = val; else delete it.measure; }
  else it[field] = val;
}
function pbRoofCatAdd() {
  pbCat().push({ id: 'p_'+uid(), name:'', unit:'SQ', cost:0 });
  renderPBModal();
}
function pbRoofCatDel(i) {
  const cat = pbCat();
  const it = cat[i]; if (!it) return;
  const used = pbBundles().filter(b => (b.product_ids||[]).includes(it.id));
  if (used.length && !confirm(`"${it.name||'This product'}" is used in ${used.length} bundle(s). Remove it from the catalog and those bundles?`)) return;
  cat.splice(i,1);
  pbBundles().forEach(b => b.product_ids = (b.product_ids||[]).filter(id => id !== it.id));
  renderPBModal();
}
function pbRoofCatMove(i, dir) {
  const cat = pbCat();
  const j = i + dir; if (j < 0 || j >= cat.length) return;
  [cat[i], cat[j]] = [cat[j], cat[i]];
  renderPBModal();
}

function pbRenderRoofBundles() {
  if (pbEditBundleId) {
    const b = pbBundles().find(x => x.id === pbEditBundleId);
    if (b) return pbRenderBundleEditor(b);
    pbEditBundleId = null;
  }
  const bundles = pbBundles(), defs = pbDefs();
  const defSel = tier => `
    <label class="pb-roof-def">
      <span>${TIER_LABELS[tier]} default</span>
      <select onchange="pbRoofSetDefault('${tier}',this.value)">
        <option value="">— none —</option>
        ${bundles.map(b=>`<option value="${b.id}" ${defs[tier]===b.id?'selected':''}>${esc(b.name||'(unnamed)')}</option>`).join('')}
      </select>
    </label>`;
  return `
    <div class="pb-roof-intro">Bundles are the <strong>Good / Better / Best dropdown options</strong>. Each pulls products from your catalog; picking one on an estimate loads its items into that tier.</div>
    <div class="pb-roof-defaults">${TIERS.map(defSel).join('')}</div>
    <div class="pb-presets-list">
      ${bundles.length ? bundles.map(b=>`
        <div class="pb-preset-card">
          <div class="pb-preset-card-main" onclick="pbOpenBundle('${b.id}')">
            <strong>${esc(b.name||'(unnamed bundle)')}</strong>
            <small>${(b.product_ids||[]).length} products</small>
          </div>
          <div class="pb-preset-card-actions">
            <button class="btn-secondary" onclick="pbOpenBundle('${b.id}')">Edit</button>
            <button class="pb-del-btn" onclick="pbDeleteBundle('${b.id}')" title="Delete bundle">✕</button>
          </div>
        </div>`).join('') : '<div class="pb-empty">No bundles yet — create one to give reps a dropdown option.</div>'}
    </div>
    <button class="btn-primary" onclick="pbAddBundle()" style="margin-top:12px">+ New Bundle</button>`;
}
function pbRenderBundleEditor(b) {
  const catalog = pbCat();
  const inBundle = new Set(b.product_ids || []);
  return `
    <div class="pb-preset-edit-head">
      <button class="btn-secondary" onclick="pbCloseBundle()">← All Bundles</button>
      <input class="pb-preset-name-input" type="text" value="${esc(b.name||'')}"
        placeholder="Bundle name (e.g. ${pbActiveTrade==='siding'?'James Hardie - Cedarmill Lap':'IKO Nordic'})" oninput="pbSetBundleField('${b.id}','name',this.value)">
    </div>
    <div class="pb-bundle-copy">
      <label class="pb-variant-field-label">Customer tagline</label>
      <textarea class="pb-bundle-desc" rows="2" placeholder="One line under the price on the Good/Better/Best card…"
        onchange="pbSetBundleField('${b.id}','description',this.value)">${esc(b.description||'')}</textarea>
      <label class="pb-variant-field-label">Closing bullets <small>things no product covers</small></label>
      <textarea class="pb-bundle-feats" rows="2"
        placeholder="One per line, added after the product bullets…
e.g. 5-year Project One workmanship warranty"
        onchange="pbSetBundleExtras('${b.id}',this.value)">${esc((b.extra_features||[]).join('\n'))}</textarea>
      <div id="pb-bundle-feat-box">${pbRenderBundleFeaturePreview(b)}</div>
    </div>
    <div class="pb-bundle-pick-hd">Products in this bundle <small>tap to include — unit price shown</small></div>
    <div class="pb-bundle-picker">
      ${catalog.length ? catalog.map(p=>`
        <label class="pb-bundle-chip ${inBundle.has(p.id)?'on':''}">
          <input type="checkbox" ${inBundle.has(p.id)?'checked':''} onchange="pbBundleToggle('${b.id}','${p.id}',this.checked)">
          <span class="pb-bundle-chip-name">${esc(p.name||'(unnamed)')}</span>
          <span class="pb-bundle-chip-cost">${(p.cost!==undefined&&p.cost!=='')?fmtCur(parseFloat(p.cost)||0):'—'}${p.unit?'/'+esc(p.unit):''}</span>
        </label>`).join('') : '<div class="pb-empty">Add products in the Products tab first.</div>'}
    </div>`;
}
/* The exact card the customer will see, built the same way an estimate builds
   it. It reads off the WORKING copies (pbCat/pbBundles), so ticking a product
   chip updates the preview — which is the whole point: the manager can see
   that dropping soffit drops the soffit bullet. */
function pbRenderBundleFeaturePreview(b) {
  const feats = pbBundleFeatures(b);
  const silent = (b.product_ids || []).filter(pid => {
    const p = pbCat().find(x => x.id === pid);
    return p && (p.customer_visible === false ||
                 (Array.isArray(p.bullets) && !p.bullets.length));
  }).length;
  return `
    <label class="pb-variant-field-label">What's Included <small>built from the products below</small></label>
    ${feats.length ? `<ul class="pb-bundle-feat-preview">${feats.map(f=>`<li>${esc(f)}</li>`).join('')}</ul>`
      : `<div class="pb-empty">No bullets yet — add products, or give them customer wording in the Products tab.</div>`}
    <div class="pb-bundle-copy-hint">Edit this wording on each <strong>product</strong> (Products tab → 💬). Picking this
      bundle on an estimate replaces that package's tagline and bullets with what you see here${
      silent ? `; ${silent} product${silent===1?' is':'s are'} set to say nothing` : ''}.</div>`;
}
// Same rule as bundleFeatures(), against the Price Book's unsaved working copies.
function pbBundleFeatures(b) {
  const cat = pbCat();
  const out = [], seen = new Set();
  const push = s => {
    const t = String(s == null ? '' : s).trim();
    if (t && !seen.has(t)) { seen.add(t); out.push(t); }
  };
  (b.product_ids || []).forEach(pid => {
    const p = cat.find(x => x.id === pid);
    if (!p || p.customer_visible === false) return;
    if (Array.isArray(p.bullets)) p.bullets.forEach(push);
    else push(p.name);
  });
  (b.extra_features || []).forEach(push);
  return out;
}
function pbRoofSetDefault(tier, val) { pbDefs()[tier] = val; }
function pbOpenBundle(id) { pbEditBundleId = id; renderPBModal(); }
function pbCloseBundle() { pbEditBundleId = null; renderPBModal(); }
function pbAddBundle() {
  const id = 'b_'+uid();
  pbBundles().push({ id, name:'', product_ids:[] });
  pbEditBundleId = id; renderPBModal();
}
function pbDeleteBundle(id) {
  if (!confirm('Delete this bundle?')) return;
  pbBundleSets[pbActiveTrade] = pbBundles().filter(b => b.id !== id);
  const defs = pbDefs();
  TIERS.forEach(t => { if (defs[t] === id) defs[t] = ''; });
  if (pbEditBundleId === id) pbEditBundleId = null;
  renderPBModal();
}
function pbSetBundleField(id, field, val) {
  const b = pbBundles().find(x => x.id === id); if (b) b[field] = val;
}
// Bullets are stored as an array (same shape the Options page uses). Always
// ASSIGN — an emptied box saves [] rather than dropping the key, which is what
// tells the server the manager meant it and to stop backfilling the seed copy.
function pbSetBundleExtras(id, text) {
  const b = pbBundles().find(x => x.id === id); if (!b) return;
  b.extra_features = text.split('\n').map(s => s.trim()).filter(Boolean);
  pbRefreshBundlePreview(b);
}
// Repaint just the What's Included preview. A full renderPBModal() would throw
// away the manager's scroll position mid-edit, and toggling 20 product chips is
// exactly when that hurts.
function pbRefreshBundlePreview(b) {
  const box = document.getElementById('pb-bundle-feat-box');
  if (box && b) box.innerHTML = pbRenderBundleFeaturePreview(b);
}
function pbBundleToggle(id, pid, on) {
  const b = pbBundles().find(x => x.id === id); if (!b) return;
  b.product_ids = b.product_ids || [];
  if (on) { if (!b.product_ids.includes(pid)) b.product_ids.push(pid); }
  else b.product_ids = b.product_ids.filter(x => x !== pid);
  const chip = (typeof event !== 'undefined' && event.target) ? event.target.closest('.pb-bundle-chip') : null;
  if (chip) chip.classList.toggle('on', on);
  pbRefreshBundlePreview(b);
}

function pbTierCount(tier) {
  return (pbItems[pbActiveTrade]||[]).filter(it => it['in_'+tier] !== false).length;
}

/* ── Brand presets (one-click bundles for Simple mode) ──────────────── */
function pbSetView(v) { pbView = v; pbEditPresetId = null; renderPBModal(); }
function pbFindPreset(id) { return (pbPresets[pbActiveTrade]||[]).find(p => p.id === id); }
function pbAddPreset() {
  pbPresets[pbActiveTrade] = pbPresets[pbActiveTrade] || [];
  const id = 'pre_' + uid();
  pbPresets[pbActiveTrade].push({ id, name:'', items:[] });
  pbEditPresetId = id;
  renderPBModal();
}
function pbDeletePreset(id) {
  if (!confirm('Delete this brand preset?')) return;
  pbPresets[pbActiveTrade] = (pbPresets[pbActiveTrade]||[]).filter(p => p.id !== id);
  if (pbEditPresetId === id) pbEditPresetId = null;
  renderPBModal();
}
function pbOpenPreset(id) { pbEditPresetId = id; renderPBModal(); }
function pbClosePreset() { pbEditPresetId = null; renderPBModal(); }
function pbSetPresetName(id, v) { const p = pbFindPreset(id); if (p) p.name = v; }
function pbPresetAddItem(id) {
  const p = pbFindPreset(id); if (!p) return;
  p.items = p.items || [];
  p.items.push({ name:'', unit:'EA', cost:0, customer_visible:true });
  renderPBModal();
}
function pbPresetDeleteItem(id, i) { const p = pbFindPreset(id); if (p) { p.items.splice(i,1); renderPBModal(); } }
function pbPresetMove(id, i, dir) {
  const p = pbFindPreset(id); if (!p) return;
  const j = i + dir; if (j < 0 || j >= p.items.length) return;
  [p.items[i], p.items[j]] = [p.items[j], p.items[i]];
  renderPBModal();
}
function pbPresetSet(id, i, field, val) {
  const p = pbFindPreset(id); if (!p || !p.items[i]) return;
  if (val === undefined) delete p.items[i][field];
  else p.items[i][field] = val;
}
// Seed a preset from the trade's default catalog at Good-tier cost.
function pbSeedPresetFromDefaults(id) {
  const p = pbFindPreset(id); if (!p) return;
  if ((p.items||[]).length && !confirm('Replace this preset’s products with the default catalog?')) return;
  const cat = (pbItems[pbActiveTrade]||[]).filter(t => t.is_default !== false && t.in_good !== false);
  p.items = cat.map(t => ({
    name: t.name, unit: t.unit,
    cost: t.cost_good !== undefined ? parseFloat(t.cost_good)||0 : (t.cost !== undefined ? parseFloat(t.cost)||0 : 0),
    measure: t.measure || undefined,
    bundle_lf: t.bundle_lf || undefined,
    bundle_unit: t.bundle_unit || undefined,
    customer_visible: t.customer_visible !== false,
  }));
  renderPBModal();
}

function pbRenderPresets() {
  const list = pbPresets[pbActiveTrade] || [];
  if (pbEditPresetId) {
    const p = list.find(x => x.id === pbEditPresetId);
    if (p) return pbRenderPresetEditor(p);
    pbEditPresetId = null;
  }
  return `
    <div class="pb-presets-intro">Build a brand's full ${TRADE_LABELS[pbActiveTrade]} bundle once — reps load it in one click from the <strong>Brand</strong> dropdown in Simple mode.</div>
    <div class="pb-presets-list">
      ${list.length ? list.map(p => `
        <div class="pb-preset-card">
          <div class="pb-preset-card-main" onclick="pbOpenPreset('${p.id}')">
            <strong>${esc(p.name || '(unnamed preset)')}</strong>
            <small>${(p.items||[]).length} products</small>
          </div>
          <div class="pb-preset-card-actions">
            <button class="btn-secondary" onclick="pbOpenPreset('${p.id}')">Edit</button>
            <button class="pb-del-btn" onclick="pbDeletePreset('${p.id}')" title="Delete preset">✕</button>
          </div>
        </div>`).join('') : '<div class="pb-empty">No brand presets yet — create one to give reps one-click brand bundles.</div>'}
    </div>
    <button class="btn-primary" onclick="pbAddPreset()" style="margin-top:12px">+ New Brand Preset</button>`;
}

function pbRenderPresetEditor(p) {
  const items = p.items || [];
  return `
    <div class="pb-preset-edit-head">
      <button class="btn-secondary" onclick="pbClosePreset()">← All Presets</button>
      <input class="pb-preset-name-input" type="text" value="${esc(p.name||'')}"
        placeholder="Brand / preset name (e.g. GAF Timberline HDZ)"
        oninput="pbSetPresetName('${p.id}',this.value)">
    </div>
    <div class="pb-table-wrap">
    <table class="pb-table">
      <thead><tr>
        <th style="width:42px"></th>
        <th class="pb-th-name">Product Name</th>
        <th class="pb-th-unit">Unit</th>
        <th class="pb-th-auto">Auto Qty From</th>
        <th class="pb-th-lf">LF/unit</th>
        <th class="pb-th-basecost">Cost</th>
        <th class="pb-th-vis">Show</th>
        <th></th>
      </tr></thead>
      <tbody>
        ${items.length ? items.map((it,i)=>pbPresetRow(p.id,i,it,items.length)).join('')
          : '<tr><td colspan="8" class="pb-empty">No products yet. Add one, or seed from the default catalog.</td></tr>'}
      </tbody>
    </table>
    </div>
    <div class="pb-preset-edit-actions">
      <button class="btn-secondary" onclick="pbPresetAddItem('${p.id}')">+ Add Product</button>
      <button class="btn-secondary" onclick="pbSeedPresetFromDefaults('${p.id}')" title="Copy the default catalog (at Good cost) into this preset">⤵ Seed from Default Catalog</button>
    </div>`;
}

function pbPresetRow(pid, i, item, count) {
  const measSel = `
    <select class="pb-measure-select" onchange="pbPresetSet('${pid}',${i},'measure',this.value)">
      <option value="">Manual</option>
      ${Object.entries(MEASURE_DEFS).map(([k,d])=>`<option value="${k}" ${item.measure===k?'selected':''}>${d.label}</option>`).join('')}
    </select>`;
  return `<tr class="pb-item-row">
    <td class="pb-order-cell">
      <button class="pb-order-btn" onclick="pbPresetMove('${pid}',${i},-1)" ${i===0?'disabled':''} title="Move up">↑</button>
      <button class="pb-order-btn" onclick="pbPresetMove('${pid}',${i},1)" ${i===count-1?'disabled':''} title="Move down">↓</button>
    </td>
    <td><input class="pb-input-name" type="text" value="${esc(item.name||'')}" oninput="pbPresetSet('${pid}',${i},'name',this.value)" placeholder="Product name"></td>
    <td><input class="pb-input-unit" type="text" value="${esc(item.unit||'')}" oninput="pbPresetSet('${pid}',${i},'unit',this.value)" placeholder="Unit"></td>
    <td class="pb-auto-cell">${measSel}</td>
    <td><input class="pb-cost-input" type="number" min="0" step="1" value="${item.bundle_lf||''}" placeholder="—"
      oninput="pbPresetSet('${pid}',${i},'bundle_lf',parseFloat(this.value)||undefined)">
      <input class="pb-input-unit" type="text" value="${esc(item.bundle_unit||'')}" placeholder="unit label"
      title="Order unit label (e.g. sticks, bundles)"
      oninput="pbPresetSet('${pid}',${i},'bundle_unit',this.value.trim()||undefined)" style="margin-top:2px"></td>
    <td><input class="pb-cost-input" type="number" min="0" step="0.01" value="${item.cost!==undefined?item.cost:''}" placeholder="0.00"
      oninput="pbPresetSet('${pid}',${i},'cost',parseFloat(this.value)||0)"></td>
    <td style="text-align:center"><input type="checkbox" ${item.customer_visible!==false?'checked':''}
      onchange="pbPresetSet('${pid}',${i},'customer_visible',this.checked)"></td>
    <td style="text-align:center"><button class="pb-del-btn" onclick="pbPresetDeleteItem('${pid}',${i})" title="Delete product">✕</button></td>
  </tr>`;
}

// Load a brand preset into the estimate as Simple-mode line items.
function loadPreset(trade, id) {
  const p = (priceBook?.presets?.[trade]||[]).find(x => x.id === id);
  if (!p) return;
  if ((S.trades[trade].line_items||[]).length &&
      !confirm(`Replace ${TRADE_LABELS[trade]} items with the "${p.name||'preset'}" bundle?`)) {
    renderTradeContent(); return;
  }
  S.trades[trade].enabled = true;
  S.trades[trade].mode = 'simple';
  const r = tradeRate(trade);
  S.trades[trade].line_items = (p.items||[]).map(it => {
    const cost = parseFloat(it.cost)||0;
    const unitPrice = S.pricing.mode === 'markup'
      ? Math.round(cost * (1 + r/100) * 100) / 100
      : (r >= 100 ? 0 : Math.round(cost / (1 - r/100) * 100) / 100);
    return {
      id: uid(), name: it.name, unit: it.unit, quantity: 0, description: '',
      unit_cost: cost, unit_price: unitPrice,
      measure: it.measure || undefined, bundle_lf: it.bundle_lf || undefined, bundle_unit: it.bundle_unit || undefined,
      customer_visible: it.customer_visible !== false,
    };
  });
  applyMeasurements();
  setDirty(); rerender();
  if (activePage === 'pricing') { renderTabBar(); renderTradeContent(); }
  if (activePage === 'scope') renderScopePage();
}

// Consolidated catalog: each product shows its Good/Better/Best inclusion + cost
// inline, so there is one clear place to configure every tier.
function pbRenderMaster() {
  const items = pbItems[pbActiveTrade] || [];
  // Effective (inherited) cost for a tier when its own cell is left blank.
  const effCost = (item) => {
    const base = item.cost_good !== undefined && item.cost_good !== '' ? parseFloat(item.cost_good)||0
               : (item.cost !== undefined ? parseFloat(item.cost)||0 : 0);
    const g = (item.cost_good   !== undefined && item.cost_good   !== '') ? parseFloat(item.cost_good)||0   : base;
    const b = (item.cost_better !== undefined && item.cost_better !== '') ? parseFloat(item.cost_better)||0 : g;
    const x = (item.cost_best   !== undefined && item.cost_best   !== '') ? parseFloat(item.cost_best)||0   : b;
    return { good:g, better:b, best:x };
  };
  const tierCell = (item, i, tier, inheritedPh) => {
    const inc = item['in_'+tier] !== false;
    const raw = item['cost_'+tier];
    const val = (raw !== undefined && raw !== '') ? raw : '';
    const colorCls = inc ? (val !== '' ? 'pb-tier-explicit' : 'pb-tier-inherited') : '';
    return `<td class="pb-tier-cell pb-tier-${tier} ${inc?'':'pb-tier-off'} ${colorCls}">
      <label class="pb-tier-inc" title="${inc?'Included in':'Excluded from'} ${TIER_LABELS[tier]}">
        <input type="checkbox" ${inc?'checked':''} onchange="pbSetTierInc(${i},'${tier}',this.checked)">
        <span>${TIER_LABELS[tier]}</span>
      </label>
      <div class="pb-tier-cost-wrap">
        <span class="pb-tier-dollar">$</span>
        <input class="pb-tier-cost" type="number" min="0" step="0.01"
          value="${val}" placeholder="${inheritedPh}" ${inc?'':'disabled'}
          title="${tier==='good'?'Cost for the Good package':'Cost for '+TIER_LABELS[tier]+' — blank reuses the next-lower tier'}"
          onchange="pbSetTierCost(${i},'${tier}',this.value)">
      </div>
    </td>`;
  };
  return `
    <div class="pb-table-wrap">
    <table class="pb-table pb-table-tiers">
      <thead><tr>
        <th style="width:42px"></th>
        <th class="pb-th-name">Product Name</th>
        <th class="pb-th-unit">Unit</th>
        <th class="pb-th-auto">Auto Qty From</th>
        <th class="pb-th-tier pb-th-good">Good</th>
        <th class="pb-th-tier pb-th-better">Better</th>
        <th class="pb-th-tier pb-th-best">Best</th>
        <th class="pb-th-vis">Show</th>
        <th class="pb-th-dflt" title="Include when loading trade defaults">Default</th>
        <th></th>
      </tr></thead>
      <tbody>
        ${items.length ? items.map((item, i) => {
          const isDefault = item.is_default !== false;
          const eff = effCost(item);
          const base = (item.cost_good !== undefined && item.cost_good !== '') ? parseFloat(item.cost_good)||0
                     : (item.cost !== undefined ? parseFloat(item.cost)||0 : 0);
          // Any row can define a menu of product options per tier that the rep
          // picks from on the estimate (e.g. roofing: architectural shingles vs
          // standing seam vs stone-coated steel; siding: product/exposure).
          // Editor is a collapsible extra row.
          const variantsRow = `<tr class="pb-variants-row"><td colspan="10">
                 <details class="pb-variants" ${pbHasVariants(item)?'open':''}>
                   <summary>Product options ${pbVariantSummary(item)}</summary>
                   <div class="pb-variants-grid">${TIERS.map(tier => pbVariantEditor(item, i, tier)).join('')}</div>
                 </details>
               </td></tr>`;
          return `<tr class="pb-item-row${isDefault ? '' : ' pb-row-nondefault'}">
            <td class="pb-order-cell">
              <button class="pb-order-btn" onclick="pbMoveItem(${i},-1)" ${i===0?'disabled':''} title="Move up">↑</button>
              <button class="pb-order-btn" onclick="pbMoveItem(${i},1)" ${i===items.length-1?'disabled':''} title="Move down">↓</button>
            </td>
            <td><input class="pb-input-name" type="text" value="${esc(item.name||'')}" oninput="pbItems['${pbActiveTrade}'][${i}].name=this.value" placeholder="Product name"></td>
            <td><input class="pb-input-unit" type="text" value="${esc(item.unit||'')}" oninput="pbItems['${pbActiveTrade}'][${i}].unit=this.value" placeholder="Unit"></td>
            <td class="pb-auto-cell">${pbMeasureCell(item, i)}</td>
            ${tierCell(item, i, 'good',   (base||0).toFixed(2))}
            ${tierCell(item, i, 'better', eff.good.toFixed(2))}
            ${tierCell(item, i, 'best',   eff.better.toFixed(2))}
            <td style="text-align:center">
              <input type="checkbox" title="Visible on the customer estimate" ${item.customer_visible!==false?'checked':''}
                onchange="pbItems['${pbActiveTrade}'][${i}].customer_visible=this.checked">
            </td>
            <td style="text-align:center">
              <input type="checkbox" title="Include in Load Defaults" ${isDefault?'checked':''}
                onchange="pbItems['${pbActiveTrade}'][${i}].is_default=this.checked;renderPBModal()">
            </td>
            <td style="text-align:center"><button class="pb-del-btn" title="Delete product" onclick="pbDeleteItem(${i})">✕</button></td>
          </tr>${variantsRow}`;
        }).join('') : `<tr><td colspan="10" class="pb-empty">No products yet — add your first one below.</td></tr>`}
      </tbody>
    </table>
    </div>
    <div style="margin-top:10px"><button class="btn-secondary" onclick="pbAddItem()">+ Add Product</button></div>`;
}

// ── Price-book product variants (all trades) ────────────────────────────
// Per-tier menu of specific products a rep chooses from on the estimate —
// roofing material types, siding products/exposures, etc.
// Stored on the price-book row as variants_<tier> = [{label,cost,notes?}].
function pbHasVariants(item) {
  return TIERS.some(tier => Array.isArray(item['variants_'+tier]) && item['variants_'+tier].length);
}
function pbVariantSummary(item) {
  const n = TIERS.reduce((s,tier)=> s + ((item['variants_'+tier]||[]).length), 0);
  return n ? `<span class="pb-variant-count">${n}</span>` : '';
}
function pbVariantEditor(item, i, tier) {
  const list = Array.isArray(item['variants_'+tier]) ? item['variants_'+tier] : [];
  return `<div class="pb-variant-col pb-tier-${tier}">
    <div class="pb-variant-hd">${TIER_LABELS[tier]}</div>
    ${list.map((v, vi) => `
      <div class="pb-variant-item">
        <label class="pb-variant-field-label">Description</label>
        <input class="pb-variant-label" type="text" value="${esc(v.label||'')}" placeholder="Product / exposure"
          onchange="pbSetVariant(${i},'${tier}',${vi},'label',this.value)">
        <label class="pb-variant-field-label pb-variant-field-label-price">Price</label>
        <div class="pb-variant-price-row">
          <span class="pb-variant-dollar">$</span>
          <input class="pb-variant-cost" type="number" min="0" step="0.01"
            value="${(v.cost!==undefined&&v.cost!=='')?v.cost:''}" placeholder="0.00"
            onchange="pbSetVariant(${i},'${tier}',${vi},'cost',this.value)">
          <button class="pb-variant-del" title="Remove option" onclick="pbRemoveVariant(${i},'${tier}',${vi})">✕</button>
        </div>
        <label class="pb-variant-field-label pb-variant-field-label-price">Tagline (optional)</label>
        <input class="pb-variant-label" type="text" value="${esc(v.description||'')}"
          placeholder="Short customer-facing tagline for this product"
          onchange="pbSetVariant(${i},'${tier}',${vi},'description',this.value)">
        <label class="pb-variant-field-label pb-variant-field-label-price">Customer notes (optional)</label>
        <textarea class="pb-variant-notes" rows="2"
          placeholder="Shown on the customer estimate when this product is selected"
          onchange="pbSetVariant(${i},'${tier}',${vi},'notes',this.value)">${esc(v.notes||'')}</textarea>
      </div>`).join('')}
    <button class="pb-variant-add" onclick="pbAddVariant(${i},'${tier}')">+ Add option</button>
  </div>`;
}
function pbAddVariant(i, tier) {
  const it = pbItems[pbActiveTrade][i];
  if (!Array.isArray(it['variants_'+tier])) it['variants_'+tier] = [];
  it['variants_'+tier].push({ label:'', cost:'' });
  renderPBModal();
}
function pbSetVariant(i, tier, vIdx, field, val) {
  const list = pbItems[pbActiveTrade][i]['variants_'+tier];
  if (!list || !list[vIdx]) return;
  list[vIdx][field] = field==='cost' ? (val===''?'':(parseFloat(val)||0)) : val;
}
function pbRemoveVariant(i, tier, vIdx) {
  const list = pbItems[pbActiveTrade][i]['variants_'+tier];
  if (!list) return;
  list.splice(vIdx, 1);
  if (!list.length) delete pbItems[pbActiveTrade][i]['variants_'+tier];
  renderPBModal();
}

// Good / Better / Best tab: the products that make up one package.
// Shows measure links, correct inherited costs, and brand label per tier.
function pbAutoFillTierCosts(tier) {
  const items = pbItems[pbActiveTrade] || [];
  const multiplier = tier === 'better' ? 1.15 : 1.30;
  items.forEach((it, i) => {
    if (it['in_'+tier] === false) return;
    if (it['cost_'+tier] !== undefined) return; // already set
    const base = it.cost_good !== undefined ? parseFloat(it.cost_good)||0 : (parseFloat(it.cost)||0);
    pbItems[pbActiveTrade][i]['cost_'+tier] = Math.round(base * multiplier * 100) / 100;
  });
  renderPBModal();
}

function pbRenderTier(tier) {
  const items = pbItems[pbActiveTrade] || [];
  const members    = items.map((it,i)=>({it,i})).filter(x => x.it['in_'+tier] !== false);
  const nonMembers = items.map((it,i)=>({it,i})).filter(x => x.it['in_'+tier] === false && (x.it.name||'').trim());

  // Warning: all members still inheriting from previous tier?
  const allInheriting = tier !== 'good' && members.length > 0 && members.every(({it}) =>
    it['cost_'+tier] === undefined || it['cost_'+tier] === ''
  );
  const prevTier = tier === 'better' ? 'Good' : 'Better';
  const warnBanner = allInheriting ? `
    <div class="pb-tier-warn">
      ⚠ All products are using the <strong>${prevTier}</strong> tier cost —
      <strong>${TIER_LABELS[tier]}</strong> will price identically to ${prevTier}.
      Enter costs below, or
      <button class="pb-autofill-btn" onclick="pbAutoFillTierCosts('${tier}')">
        ⚡ Auto-fill at +${tier==='better'?'15':'30'}%
      </button>
    </div>` : '';

  // Correct inheritance: Good → master base, Better → Good cost, Best → Better cost
  const inheritedCost = (it, t) => {
    const base = it.cost !== undefined ? parseFloat(it.cost)||0 : 0;
    const cg   = it.cost_good   !== undefined ? parseFloat(it.cost_good)||0   : base;
    const cb   = it.cost_better !== undefined ? parseFloat(it.cost_better)||0 : cg;
    if (t === 'good')   return {val: cg,   from: cg === base ? 'master' : null};
    if (t === 'better') return {val: cb,   from: it.cost_better !== undefined ? null : 'Good'};
    if (t === 'best')   return {val: (it.cost_best !== undefined ? parseFloat(it.cost_best)||0 : cb), from: it.cost_best !== undefined ? null : 'Better'};
    return {val: base, from: null};
  };

  return `${warnBanner}
    <div class="pb-table-wrap">
    <table class="pb-table">
      <thead><tr>
        <th class="pb-th-name">Product (${TIER_LABELS[tier]} tier)</th>
        <th class="pb-th-unit">Unit</th>
        <th class="pb-th-auto" title="What drives the auto-quantity from measurements">Auto Qty From</th>
        <th>Brand / Label shown to customer</th>
        <th class="pb-th-cost">${TIER_LABELS[tier]} Cost <span class="pb-th-hint">per unit</span></th>
        <th></th>
      </tr></thead>
      <tbody>
        ${members.length ? members.map(({it,i}) => {
          const {val: tc, from} = inheritedCost(it, tier);
          const explicitlySet = it['cost_'+tier] !== undefined;
          const measureLabel = it.measure ? (MEASURE_DEFS[it.measure]?.label || it.measure) : (it.formula ? 'Custom formula' : '—  Manual');
          const bundleHint = it.bundle_lf ? ` (${it.bundle_lf} LF/unit)` : '';
          return `<tr class="pb-item-row">
            <td class="pb-tier-prod"><strong>${esc(it.name||'(unnamed)')}</strong></td>
            <td class="pb-tier-unit">${esc(it.unit||'')}</td>
            <td class="pb-auto-cell" style="font-size:11px;color:var(--text-light)">${esc(measureLabel)}${esc(bundleHint)}</td>
            <td><input class="pb-product-input pb-label-input" type="text" value="${esc(it['product_'+tier]||'')}"
              placeholder="${esc(it.name||'')}"
              title="The brand or product name shown to the customer for this tier"
              oninput="pbItems['${pbActiveTrade}'][${i}].product_${tier}=this.value"></td>
            <td>
              <input class="pb-cost-input ${explicitlySet?'pb-cost-override':''}" type="number" min="0" step="0.01"
                value="${explicitlySet ? (parseFloat(it['cost_'+tier])||0) : ''}"
                placeholder="${from ? 'Inherits from '+from+': '+fmtCur(tc) : fmtCur(tc)}"
                title="${explicitlySet ? 'Explicitly set for '+TIER_LABELS[tier] : 'Inheriting from '+(from||'master')+' — enter a value to override'}"
                oninput="pbItems['${pbActiveTrade}'][${i}].cost_${tier}=this.value===''?undefined:parseFloat(this.value)||0">
              ${!explicitlySet ? `<div style="font-size:9px;color:#94a3b8;margin-top:2px">↑ from ${from||'master'}: ${fmtCur(tc)}</div>` : ''}
            </td>
            <td style="text-align:center"><button class="pb-del-btn" title="Remove from ${TIER_LABELS[tier]}"
              onclick="pbRemoveFromTier(${i},'${tier}')">✕</button></td>
          </tr>`;
        }).join('') : `<tr><td colspan="6" class="pb-empty">No products in the ${TIER_LABELS[tier]} package yet — add some below.</td></tr>`}
      </tbody>
    </table>
    </div>
    <div class="pb-add-tier">
      ${nonMembers.length ? `
        <select class="pb-add-select" onchange="if(this.value!==''){pbAddToTier(parseInt(this.value),'${tier}')}">
          <option value="">+ Add a product to ${TIER_LABELS[tier]}…</option>
          ${nonMembers.map(({it,i})=>`<option value="${i}">${esc(it.name)} — ${esc(it.unit||'')} ${fmtCur(it.cost!==undefined?parseFloat(it.cost)||0:0)}</option>`).join('')}
        </select>`
        : `<span class="pb-add-hint">All catalog products are already in this package.</span>`}
    </div>`;
}

function pbAddToTier(i, tier) {
  const it = pbItems[pbActiveTrade][i]; if (!it) return;
  it['in_'+tier] = true;
  // Don't pre-fill the tier cost — leave it undefined so it inherits from the
  // correct tier (Better inherits Good, Best inherits Better). The placeholder
  // in the tier tab UI shows what it will inherit.
  renderPBModal();
}
function pbRemoveFromTier(i, tier) {
  const it = pbItems[pbActiveTrade][i]; if (!it) return;
  it['in_'+tier] = false;
  renderPBModal();
}
// Toggle whether a product is included in a tier's package.
function pbSetTierInc(i, tier, on) {
  const it = pbItems[pbActiveTrade][i]; if (!it) return;
  it['in_'+tier] = on;
  renderPBModal();  // re-render to enable/disable the cost input
}
// Set a tier's cost. Blank clears it so it inherits the next-lower tier.
// Good's cost is also mirrored to the legacy `cost` field for back-compat.
function pbSetTierCost(i, tier, v) {
  const it = pbItems[pbActiveTrade][i]; if (!it) return;
  if (v === '' || v === null || v === undefined) {
    if (tier === 'good') { it.cost_good = 0; it.cost = 0; }
    else delete it['cost_'+tier];
  } else {
    const n = parseFloat(v) || 0;
    it['cost_'+tier] = n;
    if (tier === 'good') it.cost = n;
  }
  renderPBModal();  // refresh inherited placeholders on lower tiers
}

function pbAddItem() {
  pbItems[pbActiveTrade] = pbItems[pbActiveTrade] || [];
  pbItems[pbActiveTrade].push({ name:'', unit:'EA', cost:0, cost_good:0, customer_visible:true });
  renderPBModal();
}

function pbDeleteItem(i) {
  pbItems[pbActiveTrade].splice(i, 1);
  renderPBModal();
}
function pbMoveItem(i, dir) {
  const items = pbItems[pbActiveTrade];
  const j = i + dir;
  if (j < 0 || j >= items.length) return;
  [items[i], items[j]] = [items[j], items[i]];
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
      title="Variables: roof_squares, waste_pct, attic_sqft, low_slope_squares, steep_squares, ridge_hip_lf, valley_lf, eave_lf, rake_lf, step_flash_lf, pipe_boots, skylights, turtle_vents, broan_4in, broan_8in, iw_second_row (0/1)"
      oninput="pbItems['${pbActiveTrade}'][${i}].formula=this.value;pbItems['${pbActiveTrade}'][${i}].measure=''">` : '';
  const bundleInput = `
    <div class="pb-bundle-wrap">
      <label class="pb-bundle-label">Coverage per unit (LF)</label>
      <input class="pb-bundle-input" type="number" min="1" step="1" placeholder="—"
        value="${item.bundle_lf || ''}"
        title="LF covered by 1 bundle or stick (e.g. 105 for IKO starter, 30 for ridge cap, 9 for drip edge)"
        oninput="pbItems['${pbActiveTrade}'][${i}].bundle_lf = parseFloat(this.value)||0">
      <label class="pb-bundle-label" style="margin-top:4px">Order unit label</label>
      <input class="pb-bundle-input" type="text" placeholder="sticks / bundles"
        value="${esc(item.bundle_unit || '')}"
        title="Label shown for order quantity (e.g. 'sticks', 'bundles'). Leave blank to use the unit field."
        oninput="pbItems['${pbActiveTrade}'][${i}].bundle_unit = this.value.trim() || undefined">
    </div>`;
  return `
    <select class="pb-measure-select" onchange="pbSetMeasure(${i}, this.value)">
      <option value="">Manual</option>
      ${Object.entries(MEASURE_DEFS).map(([k,d])=>`<option value="${k}" ${cur===k?'selected':''}>${d.label}</option>`).join('')}
      <option value="__formula__" ${isFormula?'selected':''}>Custom formula…</option>
    </select>
    ${formulaInput}
    ${bundleInput}`;
}
function pbSetMeasure(i, val) {
  const it = pbItems[pbActiveTrade][i];
  if (!it) return;
  // Manual is stored as an explicit '' (not undefined): an absent key means
  // "never set" and lets the server backfill a measure from the templates —
  // an explicit '' pins the item to Manual. See get_templates in app.py.
  if (val === '__formula__') { it.formula = it.formula || ''; it.measure = ''; }
  else { it.measure = val; it.formula = undefined; }
  renderPBModal();
}

function pbIncludeAllTiers() {
  const items = pbItems[pbActiveTrade] || [];
  items.forEach(it => { it.in_good = true; it.in_better = true; it.in_best = true; });
  renderPBModal();
  alert(`✓ All ${items.length} items in ${TRADE_LABELS[pbActiveTrade]} are now included in Good, Better, and Best.\n\nClick 💾 Save Price Book to keep this change.`);
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
      measure: item.measure || '',  // '' = explicit Manual, blocks server backfill
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
    // Per-tier cost inherits UPWARD when unset: good→base, better→good, best→better.
    const costGood   = t.cost_good   !== undefined ? parseFloat(t.cost_good)||0   : baseCost;
    const costBetter = t.cost_better !== undefined ? parseFloat(t.cost_better)||0 : costGood;
    const costBest   = t.cost_best   !== undefined ? parseFloat(t.cost_best)||0   : costBetter;
    const descGood   = t.product_good   || t.desc_good   || '';
    const descBetter = t.product_better || t.desc_better || '';
    const descBest   = t.product_best   || t.desc_best   || '';
    const tiers = {
      good:  { material_unit_cost: costGood,   labor_unit_cost: 0, description: descGood,   notes: t.notes_good||'',   included: t.in_good   !== false },
      better:{ material_unit_cost: costBetter, labor_unit_cost: 0, description: descBetter, notes: t.notes_better||'', included: t.in_better !== false },
      best:  { material_unit_cost: costBest,   labor_unit_cost: 0, description: descBest,   notes: t.notes_best||'',   included: t.in_best   !== false },
    };
    applyTierVariants(pbActiveTrade, t, tiers);
    return {
      id: uid(), name: t.name, unit: t.unit, quantity: 0, scope_note: '',
      customer_visible: t.customer_visible !== false,
      measure: t.measure || undefined,
      formula: t.formula || undefined,
      bundle_lf: t.bundle_lf || undefined, bundle_unit: t.bundle_unit || undefined,
      tiers
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
  priceBook.presets = pbPresets;
  // Bundle trades' two-level model: flat product catalog + named bundles.
  BUNDLE_TRADES.forEach(trade => {
    priceBook[trade + '_catalog']       = pbCatalogs[trade] || [];
    priceBook[trade + '_bundles']       = pbBundleSets[trade] || [];
    priceBook[trade + '_tier_defaults'] = pbBundleDefaults[trade] || { good:'', better:'', best:'' };
  });
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
    { id:'cover',    label:'Cover',        on: true,                           always: true },
    { id:'intro',    label:'Introduction', on: pv.intro   !== false,           always: false },
    { id:'products', label:'Products',     on: pv.products !== false,          always: false },
    { id:'pricing',    label:'Pricing',      on: pv.pricing    !== false,        always: false },
    { id:'linePrices', label:'Line Prices', on: pv.linePrices === true,         always: false },
    { id:'options',    label:'Options',     on: pv.options    !== false,        always: false },
    // Off = the old behavior, detail tables for the selected package only.
    { id:'allPackages', label:'All Packages', on: pv.allPackages !== false,     always: false },
    { id:'contract', label:'Contract',     on: S.print_contract !== false,     always: false },
    { id:'report',   label:'Roof Health',  on: pv.report  !== false,           always: false },
  ];
  // Trust blocks (content set in ⚙ Settings) now appear on BOTH the online
  // signing link and the printed credibility page, so these chips gate the two
  // together. They stay out of the signed PDF and the document hash — that
  // exclusion is deliberate, see _cv_trust_blocks in app.py.
  const trust = [
    { id:'trust_about',          label:'About Us' },
    { id:'trust_warranty',       label:'Warranty' },
    { id:'trust_certifications', label:'Certs'    },
    { id:'trust_reviews',        label:'Reviews'  },
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
      </button>`).join('') +
    `<span class="ppb-label" title="Company sections shown on the printed credibility page and the customer's online signing page — edit the content in ⚙ Settings">Why Us:</span>` +
    trust.map(p => `
      <button class="ppb-btn ${pv[p.id] !== false ? 'on' : 'off'}"
        onclick="togglePagePrint('${p.id}')"
        title="${pv[p.id] !== false ? 'Shown in print and on the signing page — click to hide for this estimate' : 'Hidden from print and the signing page — click to show'}">
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

/* ── Permit jurisdiction & code (Scope-page panel) ─────────────────────
   Statewide-Colorado reference served by /api/jurisdictions (all 64 counties +
   273 municipalities + a shared colorado_baseline). We match the customer
   address to candidate jurisdictions, but a mailing city is NOT always the
   permitting authority — a "Fort Collins" parcel may sit in unincorporated
   Larimer County — so the panel offers the matched city AND its county (and,
   as a fallback, every CO jurisdiction) and the rep confirms which governs.
   Display/reference only; nothing here touches pricing. */
let _jurisdictions = null;          // {version, colorado_baseline, jurisdictions:[]}
let _jurisdictionsLoading = false;
function _ensureJurisdictions() {
  if (_jurisdictions || _jurisdictionsLoading) return;
  _jurisdictionsLoading = true;
  fetch('/api/jurisdictions')
    .then(r => r.json())
    .then(d => { _jurisdictions = d || {}; })
    .catch(() => { _jurisdictions = { colorado_baseline:{code_points:[],verify_note:''}, jurisdictions:[] }; })
    .finally(() => { _jurisdictionsLoading = false; rerenderJurisdictionPanels(); });
}
const _normCity = s => String(s || '').trim().toLowerCase();
function _jxById(id) {
  return ((_jurisdictions && _jurisdictions.jurisdictions) || []).find(j => j.id === id) || null;
}
// Candidate jurisdictions for the current address. Returns {outOfState, list}.
function jurisdictionCandidates() {
  const data = _jurisdictions;
  const all = (data && data.jurisdictions) || [];
  const addr = (S.customer && S.customer.address) || {};
  const state = String(addr.state || '').trim().toUpperCase();
  const city = _normCity(addr.city);
  const zip = String(addr.zip || '').trim().slice(0, 5);
  if (state && state !== 'CO' && state !== 'COLORADO') return { outOfState:true, state, list:[] };
  const list = [], seen = new Set();
  const add = j => { if (j && !seen.has(j.id)) { seen.add(j.id); list.push(j); } };
  if (zip) all.forEach(j => { if (((j.match && j.match.zips) || []).includes(zip)) add(j); });
  if (city) all.forEach(j => { if (((j.match && j.match.cities) || []).some(c => _normCity(c) === city)) add(j); });
  // Append the county catch-all(s) for every matched municipality.
  const wantCounties = new Set();
  list.filter(j => j.kind === 'city').forEach(j => (j.counties || [j.county]).forEach(c => c && wantCounties.add(c)));
  all.forEach(j => { if (j.kind === 'county' && wantCounties.has(j.county)) add(j); });
  return { outOfState:false, state, list, hasAddr: !!(city || zip) };
}
// baseline code points + any entry-specific extras
function _jxCodePoints(j) {
  const base = ((_jurisdictions && _jurisdictions.colorado_baseline && _jurisdictions.colorado_baseline.code_points) || []);
  return base.concat((j && j.code_points) || []);
}
// The "Roofing code requirements" list on the Scope panel. When this
// jurisdiction has a manager-approved verified_profile the list becomes
// SPECIFIC to the jurisdiction (adopted IRC year first, then each local
// amendment) and drops the generic Colorado baseline — those bullets are
// only useful when we have nothing more precise to show. Any curated
// per-jurisdiction code_points are always appended.
function _jxDisplayCodePoints(j) {
  const vp = j && j.verified_profile;
  const approved = vp && (vp.reviewed_at || '').trim();
  const jurPts = ((j && j.code_points) || []).map(String);
  if (!approved) return _jxCodePoints(j);
  const out = [];
  const ac = (vp.adopted_code || '').trim();
  if (ac) out.push(`Enforces ${ac}.`);
  (vp.amendments || []).forEach(a => {
    const tp = (a && a.topic || '').trim();
    const tx = (a && a.text  || '').trim();
    if (!tx) return;
    out.push(tp ? `${tp}: ${tx}` : tx);
  });
  // Preserve curated per-jurisdiction bullets (e.g. "Roofing affidavit required")
  // — they carry local nuance the manager entered by hand.
  jurPts.forEach(p => { if (p && !out.some(x => x === p)) out.push(p); });
  return out;
}
function _pjState() {
  if (!S.permit_jurisdiction) S.permit_jurisdiction = { selected_id:null, auto_id:null, confirmed:false, verified:null };
  return S.permit_jurisdiction;
}
function setJurisdiction(id) {
  _pjState().selected_id = id || null;
  _pjState().confirmed = true;
  setDirty();
  rerenderJurisdictionPanels();
}
// The panel lives on two pages now (Scope for retail, the Insurance tab for
// claims), so anything that changes it has to refresh whichever is showing.
function rerenderJurisdictionPanels() {
  if (activePage === 'scope') renderScopePage();
  else if (activePage === 'pricing' && activeTrade === 'insurance') renderTradeContent();
}

/* ── Boundary verification (is this parcel inside city limits?) ─────────
   /api/jurisdictions/verify asks the Census geocoder which Incorporated
   Place polygon contains the address. No place returned = unincorporated =
   the county is the AHJ. Runs ONCE per address and caches the answer on the
   estimate; the ↻ button re-runs it. TIGER lags recent annexations and being
   inside city limits doesn't always mean the city issues the permit, so this
   auto-selects but never locks — the rep can still override. */
let _jxVerifyBusy = false;
function _jxAddrKey(a) {
  a = a || {};
  return [a.street, a.city, a.state, a.zip].map(s => _normCity(s)).join('|');
}
function _jxAddrReady(a) {
  return !!(a && String(a.street || '').trim() && (String(a.city || '').trim() || String(a.zip || '').trim()));
}
// Verified result → the jurisdictions.json entry that actually governs.
function _jxFromVerified(v) {
  if (!v || !v.ok) return null;
  const all = (_jurisdictions && _jurisdictions.jurisdictions) || [];
  if (v.incorporated) {
    // match.cities holds the bare name ('Fort Collins'); j.name is the formal
    // one ('City of Fort Collins'), so the alias list is the reliable key.
    const want = _normCity(v.place_clean);
    const hit = all.find(j => j.kind === 'city' &&
                  ((j.match && j.match.cities) || []).some(c => _normCity(c) === want)) ||
                all.find(j => j.kind === 'city' && _normCity(j.name) === want);
    if (hit) return hit;
  }
  const cty = _normCity(v.county_clean);
  return all.find(j => j.kind === 'county' && _normCity(j.county) === cty) || null;
}
function _jxMaybeVerify(force) {
  const pj = _pjState();
  const addr = (S.customer && S.customer.address) || {};
  const state = String(addr.state || '').trim().toUpperCase();
  if (state && state !== 'CO' && state !== 'COLORADO') return;
  if (!_jxAddrReady(addr) || _jxVerifyBusy) return;
  const key = _jxAddrKey(addr);
  if (!force && pj.verified && pj.verified.addr_key === key) return;   // cached
  _jxVerifyBusy = true;
  const qs = new URLSearchParams({ street:addr.street||'', city:addr.city||'',
                                   state:addr.state||'', zip:addr.zip||'' });
  fetch('/api/jurisdictions/verify?' + qs)
    .then(r => r.json())
    .then(v => {
      v.addr_key = key;
      pj.verified = v;
      // Adopt the verified authority unless the rep already picked one.
      const hit = _jxFromVerified(v);
      if (hit && !pj.selected_id) { pj.auto_id = hit.id; }
      setDirty();
    })
    .catch(() => { pj.verified = { ok:false, addr_key:key, error:'Lookup failed — pick the authority manually.' }; })
    .finally(() => { _jxVerifyBusy = false; rerenderJurisdictionPanels(); });
}
function jxReverify() {
  _pjState().verified = null;
  rerenderJurisdictionPanels();
  _jxMaybeVerify(true);
}

/* ── Verified per-jurisdiction code profile (adopted IRC + amendments) ──
   Kicks the tiered verifier on the server (curated URL → publisher heuristics
   → city page → Perplexity fallback), previews the parsed result in a drawer,
   and — once a manager approves — persists it into jurisdictions.json so the
   customer sign page shows the specific code that applies to their address.
   The button is manager-only (_meCanViewAll); reps see a "not verified yet"
   note instead. */
const _jxPendingProfile = {};   // jurisdiction_id → last verify result (unapproved)
const _jxProfileBusy    = {};   // jurisdiction_id → true while a POST is in flight
function jxVerifyProfile(id) {
  if (!id || _jxProfileBusy[id]) return;
  _jxProfileBusy[id] = true;
  rerenderJurisdictionPanels();
  fetch(`/api/jurisdictions/${encodeURIComponent(id)}/verify`, { method: 'POST' })
    .then(r => r.json())
    .then(res => {
      _jxPendingProfile[id] = res || { ok:false, error:'Empty response.' };
    })
    .catch(e => { _jxPendingProfile[id] = { ok:false, error:String(e) }; })
    .finally(() => { _jxProfileBusy[id] = false; rerenderJurisdictionPanels(); });
}
function jxApproveProfile(id) {
  const pending = _jxPendingProfile[id];
  if (!pending || !pending.ok || !pending.profile) return;
  _jxProfileBusy[id] = true;
  rerenderJurisdictionPanels();
  fetch(`/api/jurisdictions/${encodeURIComponent(id)}/approve`, {
    method: 'POST',
    headers: { 'Content-Type':'application/json' },
    body: JSON.stringify({
      profile:   pending.profile,
      source:    pending.source || '',
      citations: pending.citations || [],
    }),
  }).then(r => r.json())
    .then(res => {
      if (res && res.ok && res.jurisdiction) {
        // Splice the freshly-approved copy into _jurisdictions so the sign
        // page picks it up on the next render without reloading the page.
        const all = (_jurisdictions && _jurisdictions.jurisdictions) || [];
        const i = all.findIndex(j => j.id === id);
        if (i >= 0) all[i] = res.jurisdiction;
        delete _jxPendingProfile[id];
      } else {
        alert('Could not approve: ' + ((res && res.error) || 'unknown error'));
      }
    })
    .catch(e => alert('Approve failed: ' + e))
    .finally(() => { _jxProfileBusy[id] = false; rerenderJurisdictionPanels(); });
}
function jxRejectProfile(id) {
  // Clear the preview locally and, if a saved profile exists, wipe it on the
  // server so the next verify runs fresh.
  delete _jxPendingProfile[id];
  const j = _jxById(id);
  if (j && j.verified_profile) {
    fetch(`/api/jurisdictions/${encodeURIComponent(id)}/reject`, { method:'POST' })
      .then(() => { delete j.verified_profile; })
      .finally(() => rerenderJurisdictionPanels());
    return;
  }
  rerenderJurisdictionPanels();
}
// Rep-only surface — everything renders inside the jx-card so it lives right
// under the roofing code requirements list.
function _jxVerifiedProfileMarkup(sel) {
  if (!sel) return '';
  const id = sel.id;
  const canManage = (typeof _meCanViewAll === 'function') && _meCanViewAll();
  const pending = _jxPendingProfile[id];
  const busy = _jxProfileBusy[id];
  const vp = sel.verified_profile;

  // 1) A manager-approved profile is already saved. The requirements list
  //    above already carries the specifics (see _jxDisplayCodePoints), so
  //    this drawer stays compact — just the verified chip, the permit
  //    portal, and manager controls to re-verify or clear.
  if (vp && (vp.reviewed_at || '').trim()) {
    const via = esc(vp.verified_via || '');
    const at  = esc((vp.verified_at || '').slice(0, 10));
    const acu = vp.adopted_code_source_url || '';
    const acLink = (acu && acu !== 'unknown')
      ? ` &middot; <a href="${esc(acu)}" target="_blank" rel="noopener">code source</a>` : '';
    const sources = (vp.sources || [])
      .filter(u => u && u !== acu)
      .slice(0, 4)
      .map(u => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u.replace(/^https?:\/\//,''))}</a>`)
      .join(' · ');
    const rp = vp.reroof_permit || {};
    const rpBits = [];
    if (rp.submittal_method && rp.submittal_method !== 'unknown') rpBits.push('Submittal: ' + esc(rp.submittal_method));
    if (rp.portal_url && rp.portal_url !== 'unknown') rpBits.push(`<a href="${esc(rp.portal_url)}" target="_blank" rel="noopener">Permit portal</a>`);
    const rpLine = rpBits.length ? `<div class="jx-vp-permit">${rpBits.join(' &middot; ')}</div>` : '';
    const actions = canManage ? `<div class="jx-vp-actions">
        <button class="jx-vp-btn" onclick="jxVerifyProfile('${id}')" ${busy ? 'disabled' : ''}>${busy ? 'Verifying…' : '↻ Re-verify'}</button>
        <button class="jx-vp-btn jx-vp-btn-warn" onclick="jxRejectProfile('${id}')">Clear</button>
      </div>` : '';
    return `
      <div class="jx-vp jx-vp-ok">
        <div class="jx-vp-head">✅ Code data verified ${at}${via ? ` · source: ${via}` : ''}${acLink}</div>
        ${sources ? `<div class="jx-vp-sub">Additional sources: ${sources}</div>` : ''}
        ${rpLine}
        ${actions}
      </div>`;
  }

  // 2) A pending verify result — manager reviews sources, clicks Approve/Reject.
  if (pending) {
    if (!pending.ok) {
      const err = esc(pending.error || 'Verify failed.');
      const kind = pending.kind || '';
      const cls = kind === 'SpendCapReached' ? 'jx-vp-cap' : 'jx-vp-fail';
      return `
        <div class="jx-vp ${cls}">
          <div class="jx-vp-head">⚠️ ${err}</div>
          ${canManage ? `<div class="jx-vp-actions">
            <button class="jx-vp-btn" onclick="jxVerifyProfile('${id}')" ${busy ? 'disabled' : ''}>${busy ? 'Verifying…' : 'Try again'}</button>
            <button class="jx-vp-btn" onclick="jxRejectProfile('${id}')">Dismiss</button>
          </div>` : ''}
        </div>`;
    }
    const p = pending.profile || {};
    const ac = esc(p.adopted_code || '');
    const source = esc(pending.source || 'unknown');
    const acu = p.adopted_code_source_url || '';
    const acLink = acu && acu !== 'unknown' ? ` &middot; <a href="${esc(acu)}" target="_blank" rel="noopener">source</a>` : '';
    const amends = (p.amendments || []).slice(0, 8).map(a => {
      const tp = esc((a.topic || '').trim());
      const tx = esc((a.text || '').trim());
      const su = a.source_url || '';
      const link = su ? ` <a href="${esc(su)}" target="_blank" rel="noopener" style="font-size:11.5px">source</a>` : '';
      return `<li>${tp ? `<b>${tp}:</b> ` : ''}${tx}${link}</li>`;
    }).join('');
    const cites = (pending.citations || []).map(c => `<li><a href="${esc(c)}" target="_blank" rel="noopener">${esc(c)}</a></li>`).join('');
    return `
      <div class="jx-vp jx-vp-pending">
        <div class="jx-vp-head">🔎 Preview — source: <b>${source}</b> (not yet visible to customers)</div>
        ${ac ? `<div class="jx-vp-adopted"><b>Enforces</b> ${ac}${acLink}</div>` : '<div class="jx-vp-adopted">Adopted code not detected.</div>'}
        ${amends ? `<div class="jx-vp-title">Local amendments</div><ul class="jx-vp-list">${amends}</ul>` : ''}
        ${cites ? `<div class="jx-vp-title">Citations</div><ul class="jx-vp-list">${cites}</ul>` : ''}
        ${canManage ? `<div class="jx-vp-actions">
          <button class="jx-vp-btn jx-vp-btn-ok" onclick="jxApproveProfile('${id}')" ${busy || !ac ? 'disabled' : ''}>${busy ? 'Saving…' : 'Approve for customer view'}</button>
          <button class="jx-vp-btn" onclick="jxRejectProfile('${id}')">Reject</button>
          <button class="jx-vp-btn" onclick="jxVerifyProfile('${id}')" ${busy ? 'disabled' : ''}>Retry</button>
        </div>` : '<div class="jx-vp-hint">Ask a manager to approve.</div>'}
      </div>`;
  }

  // 3) No profile, no pending — offer to verify (managers) or note the gap.
  if (canManage) {
    return `
      <div class="jx-vp jx-vp-idle">
        <div class="jx-vp-head">Code data not yet verified for this jurisdiction.</div>
        <div class="jx-vp-sub">Pulls the adopted IRC year + local amendments from the city's building department or its published code first, and falls back to Perplexity only if those don't answer.</div>
        <div class="jx-vp-actions">
          <button class="jx-vp-btn jx-vp-btn-ok" onclick="jxVerifyProfile('${id}')" ${busy ? 'disabled' : ''}>${busy ? '🔎 Verifying…' : '🔎 Verify code for this jurisdiction'}</button>
        </div>
      </div>`;
  }
  return `<div class="jx-vp jx-vp-idle">
      <div class="jx-vp-head">Code data not yet verified — the customer will see the shared Colorado baseline until a manager verifies this jurisdiction.</div>
    </div>`;
}
// Verification banner: the answer, its caveats, and a re-run.
function _jxVerifyMarkup() {
  const pj = _pjState();
  const addr = (S.customer && S.customer.address) || {};
  if (!_jxAddrReady(addr))
    return `<div class="jx-verify-bar jx-vb-idle">🔎 Add the street address to auto-verify whether this parcel is inside city limits.</div>`;
  if (_jxVerifyBusy && !pj.verified)
    return `<div class="jx-verify-bar jx-vb-busy">🔎 Checking the jurisdiction boundary…</div>`;
  const v = pj.verified;
  if (!v) return `<div class="jx-verify-bar jx-vb-busy">🔎 Checking the jurisdiction boundary…</div>`;
  const btn = `<button class="jx-reverify" onclick="jxReverify()" title="Run the boundary check again">↻ Re-verify</button>`;
  if (!v.ok)
    return `<div class="jx-verify-bar jx-vb-fail">⚠️ ${esc(v.error || 'Boundary check unavailable.')}${btn}</div>`;
  const where = v.incorporated
    ? `Verified <b>inside ${esc(v.place_clean)} city limits</b> — the city is the AHJ.`
    : `Verified <b>unincorporated</b> — no city limits contain this parcel, so <b>${esc(v.county_clean)} County</b> is the AHJ.`;
  return `
    <div class="jx-verify-bar jx-vb-ok">
      <div class="jx-vb-line">✅ ${where}${btn}</div>
      <div class="jx-vb-sub">${esc(v.matched_address || `${addr.street}, ${addr.city} ${addr.state} ${addr.zip}`)}
        · Census TIGER boundaries · ${esc(v.source || '')}</div>
      <div class="jx-vb-caveat">Boundary data lags recent annexations, and some towns contract inspections to the county — confirm with the office before you file.</div>
    </div>`;
}
// Jump from the Scope panel straight into the Documents permit generator.
function openPermitDoc() {
  _docGenerator = null;            // ensure docToggleGenerator opens (doesn't toggle off)
  switchPage('documents');
  docToggleGenerator('permit');
}
function permitJurisdictionMarkup() {
  _ensureJurisdictions();
  if (_jurisdictions) _jxMaybeVerify(false);
  if (!_jurisdictions) return `
    <div class="measure-panel jx-panel">
      <div class="measure-panel-head"><h3>🏛 Permit Jurisdiction &amp; Code</h3></div>
      <p class="jx-loading">Looking up the permitting jurisdiction…</p>
    </div>`;

  const baseline = _jurisdictions.colorado_baseline || { code_points:[], verify_note:'' };
  const cand = jurisdictionCandidates();
  const head = `<div class="measure-panel-head"><h3>🏛 Permit Jurisdiction &amp; Code</h3>
      <span class="measure-hint">Where you'd pull the permit for this address — and the roofing code that applies</span></div>`;

  if (cand.outOfState) return `
    <div class="measure-panel jx-panel">${head}
      <p class="jx-note">Colorado jurisdictions only — this address is in
        <b>${esc(cand.state)}</b>, so no permit/code data is loaded here.</p>
    </div>`;

  const all = (_jurisdictions.jurisdictions || []).slice()
    .sort((a, b) => a.name.localeCompare(b.name));
  const pj = _pjState();

  // Default authority, best source first: the verified boundary answer, then
  // the name-matched city, then any candidate. Stored as auto_id (no dirty
  // churn); selected_id wins once the rep overrides.
  const verifiedPick = _jxFromVerified(pj.verified);
  const autoPick = verifiedPick || cand.list.find(j => j.kind === 'city') || cand.list[0] || null;
  pj.auto_id = autoPick ? autoPick.id : null;
  const effId = (pj.selected_id && _jxById(pj.selected_id)) ? pj.selected_id : pj.auto_id;
  const sel = effId ? _jxById(effId) : null;
  const isVerifiedPick = !!(verifiedPick && sel && verifiedPick.id === sel.id);

  // The verified authority always heads the shortlist, even when the mailing
  // city never matched an entry (the unincorporated case).
  const likely = verifiedPick && !cand.list.some(j => j.id === verifiedPick.id)
    ? [verifiedPick].concat(cand.list) : cand.list;
  const opt = j => `<option value="${j.id}" ${j.id === effId ? 'selected' : ''}>${esc(j.name)}${
    verifiedPick && j.id === verifiedPick.id ? ' — verified' : ''}</option>`;
  const candIds = new Set(likely.map(j => j.id));
  const selector = `
    <select class="jx-select" onchange="setJurisdiction(this.value)">
      <option value="" ${!effId ? 'selected' : ''}>— select the governing jurisdiction —</option>
      ${likely.length ? `<optgroup label="Likely for this address">${likely.map(opt).join('')}</optgroup>` : ''}
      <optgroup label="All Colorado jurisdictions">${all.filter(j => !candIds.has(j.id)).map(opt).join('')}</optgroup>
    </select>`;

  // The old city-vs-county guess-work nudge, still shown when the boundary
  // check couldn't answer. Once verified, the banner says it outright instead.
  const countyNudge = (!verifiedPick && sel && cand.list.some(j => j.kind === 'county')) ? `
    <p class="jx-nudge">⚠️ The mailing city isn't always the permitting authority — confirm whether this
      parcel is <b>inside city limits</b> or in <b>unincorporated ${esc(sel.counties ? sel.counties[0] : sel.county)}${(sel.county||'').endsWith('County') ? '' : ' County'}</b>, and pick the one that governs.</p>` : '';
  // Rep chose something the boundary check disagrees with — say so, don't override.
  const overrideNote = (verifiedPick && sel && verifiedPick.id !== sel.id) ? `
    <p class="jx-nudge">ℹ️ You've overridden the verified authority — the boundary check put this parcel under
      <b>${esc(verifiedPick.name)}</b>.</p>` : '';

  let card = '';
  if (sel) {
    // Verified profile (when approved) replaces the generic baseline in this
    // list with the jurisdiction's actual adopted-code line and amendments —
    // see _jxDisplayCodePoints for the merge rule.
    const points = _jxDisplayCodePoints(sel);
    const pull = sel.pull || (sel.kind === 'county'
      ? 'Confirm the reroof permit submittal method (portal / in person) with the county building department.'
      : 'Confirm the reroof permit submittal method (portal / in person) with the city building department.');
    const contact = [
      sel.phone ? `<span class="jx-contact">📞 ${esc(sel.phone)}</span>` : '',
      sel.url ? `<a class="jx-contact" href="${esc(sel.url)}" target="_blank" rel="noopener">🔗 ${esc(sel.url.replace(/^https?:\/\//,''))}</a>` : '',
    ].filter(Boolean).join('');
    card = `
      <div class="jx-card">
        <div class="jx-office">${esc(sel.office || sel.name)}
          <span class="jx-kind">${sel.kind === 'county' ? 'County AHJ' : 'Municipal AHJ'}</span>
          ${isVerifiedPick ? '<span class="jx-kind jx-kind-ok">✅ boundary-verified</span>' : ''}</div>
        <div class="jx-pull"><b>Pull the permit:</b> ${esc(pull)}</div>
        ${contact ? `<div class="jx-contacts">${contact}</div>` : ''}
        ${sel.permit_template === 'loveland' ? `
          <button class="jx-permit-btn" onclick="openPermitDoc()">📄 Open reroof permit form →</button>` : ''}
        <div class="jx-code-title">Roofing code requirements</div>
        <ul class="jx-code">${points.map(p => `<li>${esc(p)}</li>`).join('')}</ul>
        ${baseline.verify_note ? `<div class="jx-verify">⚠️ ${esc(baseline.verify_note)}</div>` : ''}
        ${_jxVerifiedProfileMarkup(sel)}
      </div>`;
  } else if (cand.hasAddr) {
    card = `<p class="jx-note">No exact match for this city — it may be an unincorporated area.
      Choose the governing city or county above (the code baseline still applies).</p>`;
  } else {
    card = `<p class="jx-note">Enter the property <b>city</b> (and ZIP) in the sidebar to look up the permit jurisdiction.</p>`;
  }

  return `<div class="measure-panel jx-panel">${head}${_jxVerifyMarkup()}${selector}${countyNudge}${overrideNote}${card}</div>`;
}

/* ── Insurance scope-gap check ──────────────────────────────────────────
   Diffs the carrier's line items against the jurisdiction's machine-checkable
   code_items (the CO baseline plus anything a manager added to this city or
   county). Anything with no matching line is a supplement candidate, carrying
   its code basis so the rep has the argument in hand. Display-only — it never
   edits the estimate or touches money, so it deliberately lives only here in
   app.js rather than being mirrored into app.py. */
function _jxCodeItems(j) {
  const base = ((_jurisdictions && _jurisdictions.colorado_baseline && _jurisdictions.colorado_baseline.code_items) || []);
  const extra = (j && j.code_items) || [];
  // Merged on LABEL, not key: the Settings editor re-slugs keys from the label
  // on every save, so the label is the only stable identity. A jurisdiction
  // item reusing a baseline label replaces it; anything else is appended.
  const byLabel = new Map(base.map(it => [_normCity(it.label), it]));
  extra.forEach(it => byLabel.set(_normCity(it.label), it));
  return Array.from(byLabel.values());
}
// Every carrier line description on the estimate, lowercased for matching.
function _insScopeText() {
  const out = [];
  ((S.trades.insurance && S.trades.insurance.sections) || []).forEach(sec => {
    if (sec.name) out.push(String(sec.name));
    (sec.items || []).forEach(it => {
      if (it.name) out.push(String(it.name));
      if (it.description) out.push(String(it.description));
    });
  });
  if (S.trades.insurance && S.trades.insurance.scope_notes) out.push(String(S.trades.insurance.scope_notes));
  return out.join(' \n ').toLowerCase();
}
function insCodeGaps() {
  const pj = _pjState();
  const j = _jxById(pj.selected_id) || _jxById(pj.auto_id);
  const items = _jxCodeItems(j);
  const hay = _insScopeText();
  const found = [], missing = [];
  items.forEach(it => {
    const hit = (it.match || []).some(m => hay.includes(String(m).toLowerCase()));
    (hit ? found : missing).push(it);
  });
  return { jurisdiction: j, found, missing,
           code:        missing.filter(i => i.class === 'code'),
           common:      missing.filter(i => i.class === 'common'),
           conditional: missing.filter(i => i.class === 'conditional') };
}
function insToggleGapDetail(key) {
  const el = document.getElementById('gap-note-' + key);
  if (el) el.classList.toggle('hidden');
}
function insCodeGapMarkup() {
  _ensureJurisdictions();
  if (!_jurisdictions) return '';
  const hasItems = ((S.trades.insurance && S.trades.insurance.sections) || [])
    .some(s => (s.items || []).length);
  if (!hasItems) return `
    <div class="gap-panel gap-empty">
      <div class="gap-head"><h3>🔍 Scope Gap Check</h3>
        <span class="measure-hint">Compares the carrier's line items against what code requires here</span></div>
      <p class="jx-note">Load the carrier estimate (or add line items) and this will list every
        code-required item the carrier didn't pay for.</p>
    </div>`;

  const g = insCodeGaps();
  const jName = g.jurisdiction ? g.jurisdiction.name : 'the Colorado baseline';
  // Only the code-required bucket is shown for now. The 'common' and
  // 'conditional' items stay in jurisdictions.json (and stay bucketed by
  // insCodeGaps) so switching them back on is a display change only — but the
  // counts below must ignore them, or the header lies about the checklist.
  const codeFound = g.found.filter(i => i.class === 'code');
  const codeTotal = codeFound.length + g.code.length;
  const row = it => `
    <li class="gap-item gap-${it.class}">
      <button class="gap-item-btn" onclick="insToggleGapDetail('${esc(it.key)}')">
        <span class="gap-label">${esc(it.label)}</span>
        <span class="gap-basis">${esc(it.basis || '')}</span>
        ${it.note ? '<span class="gap-caret">▾</span>' : ''}
      </button>
      ${it.note ? `<div class="gap-note hidden" id="gap-note-${esc(it.key)}">${esc(it.note)}</div>` : ''}
    </li>`;
  const block = (title, hint, list, cls) => list.length ? `
    <div class="gap-block ${cls}">
      <div class="gap-block-hd">${title} <span class="gap-count">${list.length}</span></div>
      <div class="gap-block-hint">${hint}</div>
      <ul class="gap-list">${list.map(row).join('')}</ul>
    </div>` : '';

  return `
    <div class="gap-panel">
      <div class="gap-head"><h3>🔍 Scope Gap Check <span class="note-tag">internal only</span></h3>
        <span class="measure-hint">Carrier scope vs. what <b>${esc(jName)}</b> requires —
          ${codeFound.length} of ${codeTotal} code-required items found in the estimate</span></div>
      ${!g.code.length ? `<div class="gap-clean">✅ Every code-required item was found in the carrier scope.
        Still spot-check the quantities — a paid line can still be short.</div>` : ''}
      ${block('🔴 Code-required — not in the carrier scope',
              'Strongest supplement position: the AHJ will not pass the roof without these.', g.code, 'gap-b-code')}
      <div class="gap-foot">Tap any item for the supplement note. Matching is keyword-based on the carrier's
        line descriptions — a paid-but-short line still reads as found, so check quantities too.</div>
    </div>`;
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
      </div>
      ${permitJurisdictionMarkup()}`;
    return;
  }

  const isCommercial = (S.estimate_type || 'retail') === 'commercial';
  // The trade toggles offer the trades that belong to THIS kind of estimate —
  // a house doesn't get a Commercial checkbox and a warehouse doesn't get Siding.
  const RETAIL_TRADES = RETAIL_TRADE_KEYS.filter(t =>
    isCommercial ? t === 'commercial' || t === 'other' : t !== 'commercial');
  const m = S.measurements || {};

  // `sid` is the building whose numbers these boxes read and write. Omitted,
  // they are the estimate's own -- which is every measurement on a one-roof job
  // and on every trade that does not do buildings.
  const renderMeasureGroup = (groups, sid) => { const m = structureMeasurements(sid);
    const sarg = `, '${sid || ''}'`;
    return groups.map(g => `
    <div class="measure-group">
      <div class="measure-group-title">${g.group}</div>
      <div class="measure-fields">
        ${g.fields.filter(f => !f.panelOnly).map(f => {
          const dflt = f.key === 'waste_pct' ? (m.waste_pct ?? 10) : f.key === 'siding_waste_pct' ? (m.siding_waste_pct ?? 10) : f.key === 'attic_sqft' ? (mnum(m.roof_squares)*100 || '') : '';
          const val  = m[f.key] !== undefined && m[f.key] !== 0 ? m[f.key] : dflt;
          // A field with opts is a fixed menu (soffit width), not a free number.
          if (f.opts) {
            const cur = mnum(m[f.key], f.opts[0][0]) || f.opts[0][0];
            return `<div class="measure-field">
              <label>${f.label}</label>
              <div class="measure-input-wrap">
                <select class="measure-select" onchange="setMeasurement('${f.key}', this.value${sarg})">
                  ${f.opts.map(([v,lbl]) => `<option value="${v}" ${cur===v?'selected':''}>${lbl}</option>`).join('')}
                </select>
              </div>
            </div>`;
          }
          return `<div class="measure-field">
            <label>${f.label}</label>
            <div class="measure-input-wrap">
              <input type="number" min="0" step="${f.unit==='SQ'?'0.1':f.unit==='%'?'1':'1'}"
                value="${val}" placeholder="0"
                onchange="setMeasurement('${f.key}', this.value${sarg})">
              <span class="measure-unit">${f.unit}</span>
            </div>
          </div>`;
        }).join('')}
      </div>
    </div>`).join(''); };

  const measurePanel = `
    <div class="measure-panel">
      <div class="measure-panel-head">
        <h3>📐 Roof Measurements</h3>
        <span class="measure-hint">Enter once — the Roofing estimate auto-builds with quantities &amp; Good / Better / Best pricing</span>
        <button class="btn-roofr-import" onclick="document.getElementById('roofr-pdf-input').click()" title="Import measurements from a RoofR report PDF">📥 Import RoofR PDF</button>
      </div>
      <div class="measure-groups">
        ${renderMeasureGroup(MEASURE_FIELDS.filter(g => g.group === 'Roof'))}
      </div>
      <label class="iw-second-row-toggle ${mnum(m.iw_second_row) ? 'enabled' : ''}"
        title="Code sometimes requires ice &amp; water barrier to extend 24&quot; past the interior wall line — a second course at the eaves. Doubles the eave footage in the Eave + Valley auto-quantity (Ice &amp; Water Shield).">
        <input type="checkbox" ${mnum(m.iw_second_row) ? 'checked' : ''}
          onchange="setIwSecondRow(this.checked)">
        ❄️ 2nd row of Ice &amp; Water at eaves <span class="iw-toggle-hint">(code) — doubles eave LF on the Ice &amp; Water line</span>
      </label>
    </div>`;

  const ventPanel = ventPanelMarkup();

  // Commercial low-slope. A flat roof has no attic to ventilate and no ridge or
  // valley to measure, so in commercial mode the steep-slope roof panel and the
  // ventilation calculator are hidden rather than shown full of blanks.
  /* The measurement body one roof gets: the Commercial field group, the job-type
     switch and the fastening schedule. Rendered once for a single-roof estimate
     and once per building on a complex -- same markup either way, so the two
     layouts cannot drift into asking for different numbers. `sid` is '' for the
     estimate's own measurements. */
  const commMeasureBody = (sid) => {
    const cm = structureMeasurements(sid);
    const sarg = `, '${sid || ''}'`;
    const rname = 'comm-work-type-' + (sid || 'est');   // radios must not pair across buildings
    return `
      <div class="measure-groups">
        ${renderMeasureGroup(MEASURE_FIELDS.filter(g => g.group === 'Commercial'), sid)}
      </div>
      <div class="comm-worktype">
        <span class="comm-worktype-label">Job type</span>
        <label class="comm-worktype-opt ${!mnum(cm.comm_work_type) ? 'enabled' : ''}">
          <input type="radio" name="${rname}" ${!mnum(cm.comm_work_type) ? 'checked' : ''}
            onchange="setCommWorkType(0${sarg})">
          Re-Roof <span class="comm-worktype-rate">tear-off &amp; disposal included</span>
        </label>
        <label class="comm-worktype-opt ${mnum(cm.comm_work_type) ? 'enabled' : ''}">
          <input type="radio" name="${rname}" ${mnum(cm.comm_work_type) ? 'checked' : ''}
            onchange="setCommWorkType(1${sarg})">
          New Construction <span class="comm-worktype-rate">install only</span>
        </label>
        <span class="comm-worktype-hint">Switches which labor line prices — the other drops to zero.</span>
      </div>
      ${fastenPanelMarkup(sid)}`;
  };

  /* One collapsible card per building, one open at a time. The head carries the
     two numbers a rep scans a complex for -- roof area and what that building
     costs -- so seven buildings can be checked without opening seven cards. */
  const buildingCard = (st) => {
    const open = _structureOpen === st.id;
    const sm   = st.measurements || {};
    const sq   = mnum(sm.comm_squares);
    const tot  = structureTotal(st);
    return `
      <div class="bld-card ${open ? 'is-open' : ''}" data-st="${st.id}">
        <div class="bld-head" onclick="toggleStructureOpen('${st.id}')">
          <span class="bld-caret">${open ? '▾' : '▸'}</span>
          <span class="bld-name">${esc(st.name || '(unnamed)')}</span>
          <span class="bld-facts">
            <span class="${sq ? '' : 'bld-fact-empty'}">${sq ? sq + ' SQ' : 'no roof area yet'}</span>
            <span class="bld-total">${fmtCur(tot)}</span>
          </span>
          <span class="bld-actions">
            <button class="bld-btn" title="Copy this building — same build-up, new measurements"
              onclick="event.stopPropagation();duplicateStructure('${st.id}')">⧉ Duplicate</button>
            <button class="bld-btn" title="Rename"
              onclick="event.stopPropagation();renameStructure('${st.id}')">✏</button>
            <button class="bld-btn bld-btn-del" title="Remove this building and its line items"
              onclick="event.stopPropagation();removeStructure('${st.id}')">×</button>
          </span>
        </div>
        ${open ? `<div class="bld-body">${commMeasureBody(st.id)}</div>` : ''}
      </div>`;
  };

  const commStructures = tradeStructures('commercial');
  const commercialMeasurePanel = S.trades.commercial?.enabled ? `
    <div class="measure-panel measure-panel-commercial">
      <div class="measure-panel-head">
        <h3>🏢 Commercial Roof Measurements</h3>
        <span class="measure-hint">${commStructures.length
          ? 'One card per building — each carries its own measurements and prices on its own'
          : 'From the EagleView / Hover report — the system build-up auto-fills from these'}</span>
        <button class="btn-add-building" title="A complex is one roof repeated — add a building, then duplicate it"
          onclick="addStructure('commercial')">+ Add Building</button>
      </div>
      ${commStructures.length
        ? `<div class="bld-list">${commStructures.map(buildingCard).join('')}</div>`
        : commMeasureBody('')}
      ${commComplexityMarkup()}
    </div>` : '';

  const sidingMeasurePanel = S.trades.siding.enabled ? `
    <div class="measure-panel measure-panel-siding">
      <div class="measure-panel-head">
        <h3>🏠 Siding Measurements</h3>
        <span class="measure-hint">Enter once — linked siding items auto-fill quantities</span>
      </div>
      <div class="measure-groups">
        ${renderMeasureGroup(MEASURE_FIELDS.filter(g => g.group === 'Siding' || g.group === 'Windows'))}
      </div>
    </div>` : '';

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

    ${isCommercial ? '' : measurePanel}

    ${isCommercial ? '' : ventPanel}

    ${commercialMeasurePanel}

    ${permitJurisdictionMarkup()}

    ${isCommercial ? '' : sidingMeasurePanel}`;
}

/* ── Page 3: Options ────────────────────────────────────────────────── */

function renderOptionsPage() {
  // Options tab retired — bullets/taglines now come from the picked bundle
  // and can no longer be edited per-estimate. The renderer stays callable so
  // rerender()/renderAll() don't have to know, but it no-ops without the div.
  if (!document.getElementById('page-options')) return;
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

  const trades = gbbTrades();
  if (!trades.length) {
    document.getElementById('page-options').innerHTML = `
      <div class="options-header">
        <h2>Your Options</h2>
        <p>No products are in Good / Better / Best mode — switch a product to G/B/B on the Pricing tab to offer packages</p>
      </div>`;
    return;
  }

  document.getElementById('page-options').innerHTML = `
    <div class="options-header options-header-flex">
      <div>
        <h2>Your Options</h2>
        <p>Edit what appears on each package card — these bullet points are what the customer sees when they open the estimate. Each product gets its own Good / Better / Best choice.</p>
      </div>
    </div>
    ${trades.map(trade => renderTradeOptionCards(trade)).join('')}`;
}

function renderTradeOptionCards(trade) {
  const content = tradeTierContent(trade);
  const selTier = tradeTier(trade);
  return `
    <div class="options-trade-section">
      <div class="options-trade-hdr">
        <h3>${TRADE_LABELS[trade]}</h3>
        ${_meCanViewAll() ? `<button class="btn-save-defaults" id="save-defaults-${trade}"
          onclick="saveTierDefaults('${trade}')">💾 Save as ${TRADE_LABELS[trade]} Defaults</button>` : ''}
      </div>
      <div class="pkg-cards">
      ${enabledTiers().map(tier => {
        const total    = tradeTotal(trade, tier);
        const desc     = content.descriptions[tier] || '';
        const isSel    = tier === selTier;
        const features = content.features[tier] || [];
        const sysName  = tierPackageName(trade, tier);
        return `
          <div class="pkg-card pkg-${tier} ${isSel?'selected':''}">
            <div class="pkg-card-header">
              <span class="pkg-tier-name">${TIER_LABELS[tier]}</span>
              ${isSel?'<span class="pkg-selected-badge">Selected ✓</span>':''}
            </div>
            ${sysName?`<div class="pkg-system-name" title="Custom package name — set on the Pricing tab">${esc(sysName)}</div>`:''}
            <div class="pkg-total">${fmtCur(total)}</div>
            <textarea class="pkg-description"
              placeholder="Short tagline for this package…"
              onchange="setPkgDesc('${trade}','${tier}',this.value)">${esc(desc)}</textarea>
            <div class="pkg-features-wrap">
              <div class="pkg-features-hdr">
                <span>What's Included</span>
                <button class="pkg-autofill-btn" onclick="genPkgFeatures('${trade}','${tier}')" title="Auto-fill from pricing tab">↻ Auto-fill</button>
              </div>
              <textarea class="pkg-features-ta"
                rows="6"
                placeholder="One item per line — shown as bullet points on the estimate…
e.g. 30-year architectural shingles
5-year workmanship warranty
Full tear-off included"
                oninput="setPkgFeatures('${trade}','${tier}',this.value)">${esc(features.join('\n'))}</textarea>
            </div>
            <button class="pkg-select-btn ${isSel?'selected':''}" onclick="setTradeTierAction('${trade}','${tier}')">
              ${isSel?'✓ Selected Package':`Select ${TIER_LABELS[tier]}`}
            </button>
          </div>`;
      }).join('')}
      </div>
    </div>`;
}

function setTradeTierAction(trade, tier) {
  setTradeTier(trade, tier);
  renderTierButtons(); rerender();
  if (activePage === 'options') renderOptionsPage();
  if (activePage === 'pricing') renderTradeContent();
}
function setPkgDesc(trade, tier, v) {
  tradeTierContent(trade).descriptions[tier] = v; setDirty();
}
function setPkgFeatures(trade, tier, text) {
  tradeTierContent(trade).features[tier] =
    text.split('\n').map(s => s.trim()).filter(Boolean);
  setDirty();
}
function genPkgFeatures(trade, tier) {
  const features = [];
  const td = S.trades[trade];
  if (td && td.enabled) {
    const tradeMode = effectiveTradeMode(trade, td);
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
  }
  tradeTierContent(trade).features[tier] = features;
  setDirty();
  renderOptionsPage();
}

// Seed a trade's package content from the global per-trade defaults.
// Only fills EMPTY tiers — never clobbers rep edits.
function seedTradeFromDefaults(trade) {
  const dflt = (tierDefaults || {})[trade];
  if (!dflt) return;
  const content = tradeTierContent(trade);
  TIERS.forEach(t => {
    if (!(content.features[t] || []).length)
      content.features[t] = [...((dflt.features || {})[t] || [])];
    if (!(content.descriptions[t] || '').trim())
      content.descriptions[t] = (dflt.descriptions || {})[t] || '';
  });
}

// Apply saved global defaults to an estimate (every GBB trade, empty tiers only)
function applyTierDefaults(est) {
  if (!tierDefaults) return;
  const saved = S; S = est;          // tradeTierContent/gbbTrades read S
  try { gbbTrades().forEach(seedTradeFromDefaults); }
  finally { S = saved; }
}

// Save one trade's current package content as the global defaults for new estimates
async function saveTierDefaults(trade) {
  try {
    const content = tradeTierContent(trade);
    const merged  = JSON.parse(JSON.stringify(tierDefaults || {}));
    merged[trade] = { descriptions: { ...content.descriptions },
                      features:     JSON.parse(JSON.stringify(content.features)) };
    const r = await fetch('/api/tier-defaults', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(merged)
    });
    if (!r.ok) throw new Error('Server error');
    tierDefaults = merged;
    // Brief visual confirmation
    const btn = document.getElementById('save-defaults-' + trade);
    if (btn) { btn.textContent = '✓ Defaults Saved!'; setTimeout(()=>{ btn.textContent=`💾 Save as ${TRADE_LABELS[trade]} Defaults`; }, 2200); }
  } catch(e) { alert('Could not save defaults: ' + e.message); }
}

/* ── Page 4: Pricing (trade tabs + GBB) ────────────────────────────── */

function renderPricingPage() {
  renderTabBar();
  renderTradeContent();
}
// NOTE: the old global margin strip (one slider that overwrote good/better/
// AND best's rates in one move) is gone — per-tier margins are edited right
// next to each package's price in the GBB grid, and in the sidebar.

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
  // Insurance has its own model; everything else asks the ONE resolver the
  // server mirrors. Spelling the default out here instead (`td.mode || 'gbb'`)
  // rendered a G/B/B grid for trades app.py was pricing as simple.
  const effectiveMode = isInsurance ? 'insurance' : effectiveTradeMode(trade, td);
  const showModeToggle   = !isInsurance && !isGutters && td.enabled;
  const showLoadDefaults = td.enabled && !isInsurance && trade !== 'other';

  const host = document.getElementById('trade-content');
  host.innerHTML =
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
    ${td.enabled && effectiveMode === 'simple' && !isInsurance
        ? (isBundleTrade(trade) ? renderSimpleBundleBar(trade) : renderBrandPresetBar(trade))
        : ''}
    ${td.enabled
      ? (isInsurance ? renderInsuranceFreeform()
         : effectiveMode === 'simple' ? renderSimpleFreeform(trade)
         : trade === 'other' ? renderOtherFreeform()
         : renderGBBGrid(trade))
      : (isInsurance
          ? `<div class="trade-disabled ins-tab-empty">
               <div class="ins-tab-empty-icon">🏛</div>
               <p class="ins-tab-empty-title">Insurance Claim Estimate</p>
               <p class="ins-tab-empty-body">Import the carrier's estimate PDF to load the line items automatically, or enable this trade to enter them by hand.</p>
               <button class="btn-primary ins-tab-import-btn" onclick="document.getElementById('xact-pdf-input').click()">📥 Import Carrier Estimate PDF</button>
               <p class="ins-tab-empty-hint">Importing turns on Insurance mode for you.</p>
             </div>`
          : `<div class="trade-disabled">Enable this trade to add line items.</div>`)}`;
  // Every tab's description boxes are sized after the markup lands — see autoGrow.
  autoGrowAll(host);
}

/* ── The $0 guard ───────────────────────────────────────────────────────
   Commercial ships with placeholder $0 material costs BY DESIGN — they come
   off the supplier quote per job — and the coating and layover packages have
   no labor rate yet either. This banner is the only thing standing between a
   placeholder price book and a bid that looks completely legitimate.

   Count the lines that are still unpriced but WILL be on the bid: a line at
   qty 0 isn't in scope, and asking "is every line $0" would never fire, since
   labor usually carries a rate. `tier` null means simple mode (flat unit_cost);
   a tier means G/B/B, where cost lives in the tier cell and the line has to be
   included in THAT tier to matter. */
function unpricedBundleLines(trade, tier) {
  const items = (S.trades[trade] || {}).line_items || [];
  return items.filter(i => {
    if (!i.catalog_id || (parseFloat(i.quantity) || 0) <= 0) return false;
    if (!tier) return (parseFloat(i.unit_cost) || 0) === 0;
    const cell = (i.tiers || {})[tier];
    if (!cell || cell.included === false) return false;
    return (parseFloat(cell.material_unit_cost) || 0) === 0
        && (parseFloat(cell.labor_unit_cost) || 0) === 0;
  });
}
function unpricedWarnHtml(trade, unpriced, label, cls, maxNames) {
  if (!unpriced.length) return '';
  const names = unpriced.slice(0, maxNames).map(i => i.name).join(', ');
  return `
    <div class="${cls}">⚠️ ${label ? esc(label) + ' — ' : ''}${unpriced.length}
      line${unpriced.length === 1 ? '' : 's'} still cost $0${label ? '' : ' on this bid'} —
      ${esc(names)}${unpriced.length > maxNames ? `, +${unpriced.length - maxNames} more` : ''}.
      Set real costs in ⚙ Price Book → ${esc(TRADE_LABELS[trade] || trade)} → Products, or type them
      into the Cost column below, before sending this bid.</div>`;
}

/* Single-price system picker for a bundle trade (commercial). The G/B/B grid
   has a hero <select> per tier; in simple mode there is only one system, so one
   picker sits above the table. */
function renderSimpleBundleBar(trade) {
  const td = S.trades[trade];
  const bundles = _tradeBundles(trade);
  if (!bundles.length) return '';
  const cur = td.simple_bundle || '';
  const warn = unpricedWarnHtml(trade, unpricedBundleLines(trade, null), '',
                                'simple-bundle-warn', 3);
  return `
    <div class="brand-preset-bar simple-bundle-bar">
      <span class="brand-preset-label">🏢 System:</span>
      <select class="brand-preset-select" onchange="applyBundleToSimple('${trade}',this.value)">
        <option value="">Choose a system…</option>
        ${bundles.map(b => `<option value="${esc(b.id)}" ${cur===b.id?'selected':''}>${esc(b.name||'(unnamed)')}</option>`).join('')}
      </select>
      <span class="brand-preset-hint">Loads the full build-up — swapping replaces it</span>
      ${warn}
    </div>`;
}

function renderBrandPresetBar(trade) {
  const presets = (priceBook?.presets?.[trade]) || [];
  if (!presets.length) return '';
  return `
    <div class="brand-preset-bar">
      <span class="brand-preset-label">⚡ Brand bundle:</span>
      <select class="brand-preset-select" onchange="if(this.value){loadPreset('${trade}',this.value);this.value='';}">
        <option value="">Load a brand preset…</option>
        ${presets.map(p => `<option value="${esc(p.id)}">${esc(p.name||'(unnamed)')}</option>`).join('')}
      </select>
      <span class="brand-preset-hint">One-click pre-built bundle</span>
    </div>`;
}

function renderOtherFreeform() {
  const trade = 'other';
  const td    = S.trades[trade];
  const tier  = tradeTier(trade);
  const items = td.line_items;
  const UNITS = ['EA','LS','SQ','LF','HR','SF','BD'];

  if (items.some(otherFoldNoteIntoDesc)) setDirty();

  const rows = items.map(item => {
    const t    = (item.tiers && item.tiers[tier]) || {material_unit_cost:0,labor_unit_cost:0,notes:''};
    const qty  = parseFloat(item.quantity) || 0;
    const cost = (parseFloat(t.material_unit_cost)||0) + (parseFloat(t.labor_unit_cost)||0);
    const calcTot  = lineTotal(item.quantity, t.material_unit_cost, t.labor_unit_cost, trade, tier);
    const override = (t.price_override !== undefined && t.price_override !== null && t.price_override !== '')
      ? parseFloat(t.price_override) : null;
    const tot = override !== null ? override : calcTot;
    // price_override stores the LINE TOTAL — that is what tradeTotal, the PDF
    // and the server all read, and changing it would ripple through pricing
    // parity. The BOX is per-unit, like the Unit Cost beside it and like the
    // Simple tab's Sell Price; the unit figure is derived here and multiplied
    // back out in otherSetPrice. A zero-qty row has no meaningful unit price,
    // and its Total shows $0.00 rather than the stored figure: tradeTotal drops
    // zero-qty lines, so showing the locked number would contradict the
    // subtotal directly below it.
    const unitSell = qty > 0 ? tot / qty : 0;
    return `<tr>
      <td class="other-name-cell">
        <input class="other-name-input" type="text" value="${esc(item.name)}" placeholder="Item name"
          onchange="liSetName('${trade}','${item.id}',this.value)">
        <textarea class="simple-item-desc desc-ta" rows="${descRows(t.description)}"
          placeholder="Description (optional — prints on PDF, Enter for new line)"
          oninput="autoGrow(this);otherSetDesc('${item.id}',this.value)"
          >${esc(t.description||'')}</textarea>
      </td>
      <td class="other-qty-cell">
        <input class="other-qty-input${qty>0?'':' other-qty-zero'}" type="number" inputmode="decimal"
          min="0" step="0.5" value="${item.quantity||''}" placeholder="1"
          title="${qty>0?'':'Quantity 0 — this line will not price and will not print. New items start at 1.'}"
          onchange="otherSetQty('${item.id}',this.value)">
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
      <td class="other-price-cell">
        <div class="simple-price-wrap${override !== null ? ' has-override' : ''}">
          <input class="other-price-input" type="number" min="0" step="0.01"
            value="${unitSell.toFixed(2)}"
            title="${override !== null ? 'Locked sell price PER ' + esc(item.unit||'EA') + ' — margin changes won’t touch it. ↺ resets to cost + margin.'
                     : 'Price per ' + esc(item.unit||'EA') + ', auto-calculated from cost + margin. Edit to lock a specific price.'}"
            onchange="otherSetPrice('${item.id}',this.value)">
          ${override !== null ? `<button class="li-override-reset" title="Reset to margin-calculated price"
            onclick="otherClearPrice('${item.id}')">↺</button>` : ''}
        </div>
      </td>
      <td class="other-total-cell">${fmtCur(qty > 0 ? tot : 0)}</td>
      <td><button class="li-del" onclick="liDelete('${trade}','${item.id}')" title="Remove">×</button></td>
    </tr>`;
  }).join('');

  // Show the rate the rows are actually priced with (the selected tier's) —
  // tradeRate() is the Good/simple rate and could disagree with the row math.
  const rate    = tierRate(trade, tier);
  const subtot  = tradeTotal(trade, tier);
  const modeHint = S.pricing.mode === 'margin'
    ? `${rate}% margin applied (${TIER_LABELS[tier]} package)`
    : `${rate}% markup applied (${TIER_LABELS[tier]} package)`;

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
            <th class="other-th-price">Total</th>
            <th style="width:32px"></th>
          </tr></thead>
          <tbody>${rows}</tbody>
          <tfoot><tr>
            <td colspan="5" style="text-align:right;padding-right:12px;font-weight:600">Subtotal</td>
            <td class="other-total-cell" style="font-weight:700;font-size:14px">${fmtCur(subtot)}</td>
            <td></td>
          </tr></tfoot>
        </table>
      </div>` : `<div class="scope-empty"><p>No items yet. Click <strong>+ Add Item</strong> below.</p></div>`}
    <div class="add-row-bar">
      <button class="btn-add" onclick="addLineItem('other')">+ Add Item</button>
    </div>`;
}

/* The Other tab used to carry a one-line per-tier "Note". It only ever reached
   the printed estimate — never the customer view — and it was the one trade
   with no Description box at all. Both are now the same field: fold a legacy
   note into this tier's description the first time the row renders, so the text
   the rep already typed survives and lands where every other trade's does.
   Other-tab items are hand-entered (no price-book fill, no Load Defaults), so
   in practice the description is empty and the note holds everything. If both
   somehow carry text the note is appended on its own line rather than dropped —
   never lose something a rep typed. Scoped to `other` on purpose: GBB trades
   fill t.notes from the price book, and those are not this field.
   Returns true if anything moved, so the caller can mark the estimate dirty. */
function otherFoldNoteIntoDesc(item) {
  let moved = false;
  TIERS.forEach(tier => {
    const t = item.tiers && item.tiers[tier];
    if (!t) return;
    const note = (t.notes || '').trim();
    if (!note) { delete t.notes; return; }
    const desc = (t.description || '').trim();
    if (!desc)             t.description = note;
    else if (desc !== note) t.description = desc + '\n' + note;
    delete t.notes;
    moved = true;
  });
  return moved;
}

// Same shape as otherSetUnitCost: the Other tab shows one tier at a time but
// writes to all three, so the description prints on every package.
function otherSetDesc(id, v) {
  const item = findItem('other', id);
  if (!item) return;
  TIERS.forEach(tier => {
    if (!item.tiers[tier]) item.tiers[tier] = {material_unit_cost:0,labor_unit_cost:0,description:'',notes:''};
    item.tiers[tier].description = v;
  });
  setDirty();
}

/* Sell price on the Other tab, locked across all three tiers — same rule the
   cost and description boxes follow. Per-tier would be a trap the tab cannot
   show: it renders ONE tier at a time with no package UI, so a $500 allowance
   typed while Better was selected priced at $500 on Better and $0 on Good and
   Best, and the rep had no way to see it.

   `value` is a PER-UNIT price (the box sits beside Unit Cost and matches the
   Simple tab); price_override stores the line total, so multiply back out. */
/* Other-tab rows saved before addLineItem started them at qty 1 sit at
   quantity 0, and a zero-qty line prices at nothing however good the cost and
   sell price beside it look: tradeTotal and calc_tier_total both drop it, so a
   $9,548 deck shows 0.00 in the Sell Price column, adds nothing to the subtotal
   and never prints. Unlike the G/B/B trades this tab has no way to say "not in
   scope" — no chip row, no include toggle, the quantity box is the only
   control — so a zero here is never a deliberate exclusion; it is a row nobody
   ever gave a quantity. Heal the ones that carry money, which are exactly the
   rows a rep meant to charge for, and leave the empty scaffolding rows alone.
   A signed estimate is skipped: its total is a number the customer agreed to,
   and that moves through a change order, never through a migration that runs
   quietly when the estimate is opened.
   Returns how many rows changed, so the caller can mark the doc dirty. */
function healOtherZeroQty(est) {
  if (!est || est.signature) return 0;
  const items = ((est.trades || {}).other || {}).line_items || [];
  let healed = 0;
  items.forEach(i => {
    if ((parseFloat(i.quantity) || 0) > 0) return;
    const carriesMoney = TIERS.some(t => {
      const c = (i.tiers || {})[t] || {};
      return (parseFloat(c.material_unit_cost) || 0) > 0
          || (parseFloat(c.labor_unit_cost)    || 0) > 0
          || (parseFloat(c.price_override)     || 0) > 0;
    });
    if (carriesMoney) { i.quantity = 1; healed++; }
  });
  return healed;
}

/* Quantity is the one number this tab cannot infer — there is no measurement
   behind a haul-away, an allowance or a deck rebuild — so a rep types the
   money, sees the "1" the box has shown as a placeholder all along, and ships
   a line that prices at $0. Entering a cost or a sell price IS the intent to
   charge, so give the row that 1 for real. Only ever fills a blank or zero,
   and only when a real number was typed: a quantity the rep set is theirs, and
   clearing a price must not conjure one. */
function otherEnsureQty(item, entered) {
  if ((parseFloat(entered) || 0) > 0 && (parseFloat(item.quantity) || 0) <= 0)
    item.quantity = 1;
}

function otherSetPrice(id, value) {
  const item = findItem('other', id);
  if (!item) return;
  otherEnsureQty(item, value);
  const qty = parseFloat(item.quantity) || 0;
  const unit = parseFloat(value);
  const total = (!value || isNaN(unit)) ? value
              : Math.round(unit * (qty > 0 ? qty : 1) * 100) / 100;
  TIERS.forEach(tier => otherApplyPrice(item, tier, total));
  setDirty(); rerender();
  if (activePage === 'pricing') renderTradeContent();
}
/* Quantity on the Other tab. liSetQty DELETES every locked price, which is
   right for a G/B/B trade — a locked line total was locked against a quantity,
   so 10 SQ and 40 SQ must not both cost $4,000, and the item's cost still
   drives a sensible number afterwards. On this tab the sell price is the only
   price a rep enters, so deleting it left $0, a $0 subtotal, and a row that
   never printed. Rescale instead: the per-unit price the rep typed survives,
   which is the whole point of the box being per-unit.

   It also re-renders the tab. liSetQty calls rerender() alone, so the Other
   table kept showing the old money after the data had already changed. */
function otherSetQty(id, v) {
  const item = findItem('other', id);
  if (!item) return;
  const oldQty = parseFloat(item.quantity) || 0;
  const newQty = parseFloat(v) || 0;
  TIERS.forEach(tier => {
    const cell = item.tiers && item.tiers[tier];
    if (!cell) return;
    const po = cell.price_override;
    if (po === undefined || po === null || po === '') return;
    // A row sitting at qty 0 has a stored total that never priced anything;
    // read it as the unit price the rep meant rather than scaling from zero.
    const unit = oldQty > 0 ? (parseFloat(po) || 0) / oldQty : (parseFloat(po) || 0);
    cell.price_override = Math.round(unit * newQty * 100) / 100;
  });
  item.quantity = newQty;
  setDirty(); rerender();
  if (activePage === 'pricing') renderTradeContent();
}
function otherClearPrice(id) {
  const item = findItem('other', id);
  if (!item) return;
  TIERS.forEach(tier => { if (item.tiers[tier]) delete item.tiers[tier].price_override; });
  setDirty(); rerender();
  if (activePage === 'pricing') renderTradeContent();
}
// One tier's half of otherSetPrice — mirrors liSetPriceOverride, including the
// "same as the calculated price" case, which stores nothing so the line keeps
// tracking margin instead of freezing at today's number.
function otherApplyPrice(item, tier, value) {
  if (!item.tiers[tier]) item.tiers[tier] = {material_unit_cost:0,labor_unit_cost:0,description:'',notes:''};
  const cell = item.tiers[tier];
  const v = parseFloat(value);
  if (!value || isNaN(v)) { delete cell.price_override; return; }
  const calc = lineTotal(item.quantity, cell.material_unit_cost || 0,
                         cell.labor_unit_cost || 0, 'other', tier);
  if (Math.abs(v - calc) < 0.01) delete cell.price_override;
  else cell.price_override = v;
}

function otherSetUnitCost(id, cost) {
  const item = findItem('other', id);
  if (!item) return;
  otherEnsureQty(item, cost);
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

  const sections = tradeSections(trade);
  const rowFor = item => {
    const qty   = parseFloat(item.quantity)   || 0;
    const cost  = parseFloat(item.unit_cost)  || 0;
    const price = parseFloat(item.unit_price) || 0;
    const total = qty * price;
    const descLines = descRows(item.description);
    const sectionSel = sections.length ? `
      <select class="li-section-select" title="Which section this item belongs to"
        onchange="liSetSection('${trade}','${item.id}',this.value)">
        <option value="">General</option>
        ${sections.map(s=>`<option value="${esc(s)}" ${itemSection(item)===s?'selected':''}>${esc(s)}</option>`).join('')}
      </select>` : '';
    return `<tr>
      <td class="li-move-cell">
        <button class="li-move-btn" onclick="liMove('${trade}','${item.id}',-1)" ${liCanMove(trade,item,-1)?'':'disabled'} title="Move up">↑</button>
        <button class="li-move-btn" onclick="liMove('${trade}','${item.id}',1)" ${liCanMove(trade,item,1)?'':'disabled'} title="Move down">↓</button>
      </td>
      <td class="ins-name-cell">
        <input class="other-name-input" type="text" value="${esc(item.name||'')}" list="pb-list-${trade}"
          placeholder="Type to search price book…"
          onchange="liSetNameSmart('${trade}','${item.id}',this.value)">
        <textarea class="simple-item-desc desc-ta" rows="${descLines}"
          placeholder="Description (optional — prints on PDF, Enter for new line)"
          oninput="autoGrow(this);simpleSetField('${trade}','${item.id}','description',this.value)"
          >${esc(item.description||'')}</textarea>
        ${sectionSel}
      </td>
      <td class="other-qty-cell">
        <input class="other-qty-input" type="number" inputmode="decimal" min="0" step="0.5" value="${qty||''}"
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
        <input class="other-price-input simple-cost-input" type="number" min="0" step="0.01"
          value="${cost||''}" placeholder="0.00"
          title="Your cost per unit — sell price is calculated from this + margin"
          onchange="simpleSetCost('${trade}','${item.id}',parseFloat(this.value)||0)">
      </td>
      <td class="other-price-cell">
        <div class="simple-price-wrap${item.price_locked ? ' has-override' : ''}">
          <input class="other-price-input" type="number" min="0" step="0.01"
            value="${price||''}" placeholder="0.00"
            title="${item.price_locked ? 'Locked sell price — margin changes won’t touch it. ↺ resets to cost + margin.'
                     : cost ? 'Auto-calculated from cost + margin. Edit to lock a specific price.' : 'Enter sell price directly'}"
            onchange="simpleSetPrice('${trade}','${item.id}',this.value)">
          ${item.price_locked ? `<button class="li-override-reset" title="Unlock — go back to cost + margin pricing"
            onclick="simpleUnlockPrice('${trade}','${item.id}')">↺</button>` : ''}
        </div>
      </td>
      <td class="other-total-cell simple-line-total" data-strade="${trade}" data-sid="${item.id}">${fmtCur(total)}</td>
      <td><button class="li-del" onclick="simpleDeleteItem('${trade}','${item.id}')" title="Remove">×</button></td>
    </tr>`;
  };

  const rows = groupedTradeItems(trade, items).map(g => {
    if (!g.items.length) return g.name ? `<tr class="est-section-row"><td colspan="8">${esc(g.name)} <span class="est-section-empty">empty — assign items below</span></td></tr>` : '';
    const hd = g.name ? `<tr class="est-section-row"><td colspan="8">${esc(g.name)}</td></tr>`
             : (sections.length ? `<tr class="est-section-row est-section-general"><td colspan="8">General</td></tr>` : '');
    return hd + g.items.map(rowFor).join('');
  }).join('');

  const grandTot = tradeTotal(trade, S.selected_tier);

  // Per-trade margin for this simple trade: bakes every item's Sell Price from
  // its Unit Cost. Blank inherits the sidebar default (shown as placeholder).
  // Items where the rep typed a sell price directly stay locked (untouched).
  const rateLbl      = S.pricing.mode === 'markup' ? 'Markup' : 'Margin';
  const simpleRate   = ((S.pricing.trade_rates || {})[trade] || {}).simple;
  const hasSimpleR   = simpleRate !== null && simpleRate !== undefined && simpleRate !== '';
  const simpleDflt   = _resolveRate([(S.pricing.per_trade_overrides || {})[trade],
                                     (S.pricing.tier_rates || {}).good,
                                     S.pricing.global_rate]);
  const marginBar = `
    <div class="simple-margin-bar">
      <span class="simple-margin-lbl">${TRADE_LABELS[trade]} ${rateLbl}</span>
      <span class="simple-margin-input-wrap${hasSimpleR ? ' has-custom' : ''}">
        <input type="number" min="0" max="99" step="0.5"
          value="${hasSimpleR ? simpleRate : ''}" placeholder="${simpleDflt}"
          title="${rateLbl} applied to every item's Unit Cost in this trade. Blank uses the ${simpleDflt}% sidebar default. Prices you typed by hand stay locked."
          onchange="setTradeTierRate('${trade}','simple', this.value)">
        <span class="simple-margin-pct">%</span>
      </span>
      <span class="simple-margin-hint">${hasSimpleR ? 'custom for this trade' : `default ${simpleDflt}%`}</span>
    </div>`;

  return `
    ${marginBar}
    ${items.length ? `
      ${sectionManagerBar(trade)}
      <div class="other-table-wrap">
        <table class="other-table ins-table">
          <thead><tr>
            <th class="th-move"></th>
            <th class="ins-th-name">Item Name</th>
            <th class="other-th-num">Qty</th>
            <th class="other-th-num">Unit</th>
            <th class="other-th-price">Unit Cost</th>
            <th class="other-th-price">Sell Price</th>
            <th class="other-th-price">Total</th>
            <th style="width:32px"></th>
          </tr></thead>
          <tbody>${rows}</tbody>
          <tfoot><tr>
            <td colspan="6" style="text-align:right;padding-right:12px;font-weight:600">${TRADE_LABELS[trade]} Subtotal</td>
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

/* Fields that identify an item rather than price it. Both mode converters
   rebuild items from an explicit whitelist, so anything not carried here is
   silently lost on a Simple<->G/B/B round trip. Each one has teeth:
     catalog_id  - the bundle link. Without it a system swap can't tell which
                   rows it owns, so it STACKS the new system on the old one.
     bundle_lf/_unit - ridge-vent stick rounding.
     formula     - a custom auto-quantity expression.
     vent_role   - the ventilation checkboxes infer their state from it. */
function _carryItemIdentity(src, dst) {
  ['catalog_id', 'bundle_lf', 'bundle_unit', 'formula', 'vent_role'].forEach(k => {
    if (src[k] !== undefined) dst[k] = src[k];
  });
  return dst;
}
function setTradeMode(trade, mode) {
  if (effectiveTradeMode(trade, S.trades[trade]) === mode) return;
  const td = S.trades[trade];
  const items = td.line_items || [];

  if (mode === 'simple') {
    // GBB → Simple: keep items + quantities; price from the selected tier.
    // Stash the full tier data so toggling back to GBB restores it exactly.
    const pricing = S.pricing || {};
    const tier = tradeTier(trade);
    const rate = tierRate(trade, tier);
    td.line_items = items.map(it => {
      const t    = (it.tiers || {})[tier] || {};
      const cost = (parseFloat(t.material_unit_cost)||0) + (parseFloat(t.labor_unit_cost)||0);
      const qty  = parseFloat(it.quantity) || 0;
      // A locked line total in GBB carries over as a locked unit price
      const po   = (t.price_override !== undefined && t.price_override !== null && t.price_override !== '')
        ? parseFloat(t.price_override) : null;
      const sell = (po !== null && qty > 0) ? po / qty
        : pricing.mode === 'markup' ? cost * (1 + rate/100)
        : (rate < 100 ? cost / (1 - rate/100) : 0);
      return _carryItemIdentity(it, {
        id: it.id, name: it.name, unit: it.unit,
        quantity: qty,
        measure: it.measure || undefined,
        section: it.section || undefined,
        scope_note: it.scope_note || '',
        description: t.description || it.description || '',
        unit_cost: Math.round(cost * 100) / 100,
        unit_price: Math.round(sell * 100) / 100,
        ...(po !== null && qty > 0 ? { price_locked: true } : {}),
        customer_visible: it.customer_visible !== false,
        // Preserve the per-tier pricing so a switch back to GBB restores it.
        _gbb_tiers: it.tiers || undefined,
      });
    });
  } else {
    // Simple → GBB: keep items + quantities. Restore the stashed per-tier pricing
    // if we came from GBB and the cost wasn't edited; otherwise seed every tier
    // from the single Simple cost so the pricing carries over (never blank/$0).
    td.line_items = items.map(it => {
      const base = _carryItemIdentity(it, {
        id: it.id, name: it.name, unit: it.unit,
        quantity: it.quantity || 0,
        measure: it.measure || undefined,
        section: it.section || undefined,
        scope_note: it.scope_note || '',
        customer_visible: it.customer_visible !== false,
      });
      if (it._gbb_tiers) { base.tiers = it._gbb_tiers; return base; }
      const c = parseFloat(it.unit_cost) || 0;
      base.tiers = {
        good:  { material_unit_cost:c, labor_unit_cost:0, description:it.description||'', notes:'' },
        better:{ material_unit_cost:c, labor_unit_cost:0, description:it.description||'', notes:'' },
        best:  { material_unit_cost:c, labor_unit_cost:0, description:it.description||'', notes:'' },
      };
      return base;
    });
  }
  td.mode = mode;
  // A product newly offered as G/B/B picks up its global package content
  if (mode === 'gbb' && td.enabled) seedTradeFromDefaults(trade);
  _syncLegacyTier();
  setDirty(); renderTabBar(); renderTradeContent(); renderTotals();
}

function simpleSetField(trade, id, field, val) {
  const item = (S.trades[trade].line_items || []).find(it => it.id === id);
  if (!item) return;
  item[field] = val;
  setDirty();
}
function simpleApplyMargin(trade, item) {
  if (item.price_locked) return;  // rep typed a sell price — never clobber it
  const cost = parseFloat(item.unit_cost) || 0;
  if (!cost) return;
  const r = tradeRate(trade);
  item.unit_price = S.pricing.mode === 'markup'
    ? Math.round(cost * (1 + r / 100) * 100) / 100
    : (r >= 100 ? 0 : Math.round(cost / (1 - r / 100) * 100) / 100);
}
// Rep typed the sell price directly — store it and LOCK it so margin/mode
// changes (recalcSimpleItems) don't silently overwrite the number they chose.
function simpleSetPrice(trade, id, v) {
  const item = (S.trades[trade].line_items || []).find(it => it.id === id);
  if (!item) return;
  item.unit_price = parseFloat(v) || 0;
  item.price_locked = true;
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
  renderTotals();
}
function simpleUnlockPrice(trade, id) {
  const item = (S.trades[trade].line_items || []).find(it => it.id === id);
  if (!item) return;
  delete item.price_locked;
  simpleApplyMargin(trade, item);
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
  renderTotals();
}
function simpleSetCost(trade, id, cost) {
  const item = (S.trades[trade].line_items || []).find(it => it.id === id);
  if (!item) return;
  item.unit_cost = cost;
  delete item._gbb_tiers;    // cost changed in Simple — don't restore stale GBB tiers
  delete item.price_locked;  // re-entering cost means "price me from margin again"
  simpleApplyMargin(trade, item);
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
  else renderTotals();
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
    id: uid(), name:'', description:'', unit:'SQ', quantity:0, unit_cost:0, unit_price:0,
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
        <textarea class="ins-desc-input desc-ta" rows="${descRows(item.description)}"
          placeholder="Description (Enter for new line)"
          oninput="autoGrow(this)"
          onchange="insSetField('${sec.id}','${item.id}','description',this.value)"
          >${esc(item.description||'')}</textarea>
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
      <div class="field-group" style="align-self:flex-end">
        <button class="btn-secondary" onclick="document.getElementById('xact-pdf-input').click()">📥 Import Carrier PDF</button>
      </div>
    </div>
    ${_insClaimCard()}
    <div class="other-desc" style="margin-bottom:14px">
      Add sections to match the carrier's breakdown (e.g. Roof, Gutters, Interior) — or use <strong>Import Carrier PDF</strong> to load them straight from the carrier's estimate. Xactimate and Symbility exports are both read automatically. Each section has its own subtotal — ACV + Depreciation = RCV.
    </div>
    ${sections.map(sec => _insSection(sec, sections)).join('')}
    <div class="ins-section-actions">
      <button class="btn-secondary" onclick="insAddSection()">+ Add Section</button>
      ${hasItems ? `<div class="ins-grand-bar">
        <span>Insurance Claim Total</span>
        <strong id="ins-grand-total">${fmtCur(grandTot)}</strong>
      </div>` : ''}
    </div>
    <div id="ins-gap-wrap">${insCodeGapMarkup()}</div>
    ${permitJurisdictionMarkup()}
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

// Read-only claim reference card (populated by the carrier PDF import).
// Internal only — none of this renders on the customer sign page or PDF.
function _insClaimCard() {
  const c = S.insurance_claim;
  if (!c) return '';
  const row = (label, val, money) => val === undefined || val === '' ? '' :
    `<span class="xact-meta-label">${label}</span><span class="xact-meta-value">${money ? fmtCur(val) : esc(String(val))}</span>`;

  // Roof figures lifted off the carrier PDF. Shown here because in Insurance
  // mode the Measurements page is a notice, so this card is the only place
  // they are visible without switching the estimate back to Retail.
  const m = S.measurements || {};
  const carrierRoof = !c.measurements_from_carrier ? '' : [
    mnum(m.roof_squares) ? `${mnum(m.roof_squares)} SQ` : '',
    mnum(m.eave_lf)      ? `${mnum(m.eave_lf)} LF eaves` : '',
    mnum(m.ridge_lf)     ? `${mnum(m.ridge_lf)} LF ridge` : '',
  ].filter(Boolean).join(' · ');
  return `
    <div class="ins-claim-card">
      <div class="ins-claim-card-hd">
        <span>📎 Claim Details <span class="note-tag">internal — imported from carrier PDF</span></span>
        <button class="li-del" title="Remove claim details"
          onclick="delete S.insurance_claim;setDirty();renderTradeContent()">×</button>
      </div>
      <div class="xact-meta-card ins-claim-grid">
        ${row('Policy #', c.policy_number)}
        ${row('Type of Loss', c.type_of_loss)}
        ${row('Date of Loss', c.date_of_loss)}
        ${row('Adjuster', c.adjuster)}
        ${row(c.source === 'symbility_import' ? 'Pricing Database' : 'Price List', c.price_list)}
        ${row('Deductible', c.deductible, true)}
        ${row('Net Claim (ACV)', c.net_claim, true)}
        ${row('Recoverable Depreciation', c.recoverable_depreciation, true)}
        ${row('Paid When Incurred', c.paid_when_incurred, true)}
        ${row('Net Claim if Dep. Recovered', c.net_claim_if_recovered, true)}
        ${row('Roof (from carrier PDF)', carrierRoof)}
      </div>
    </div>`;
}

function insSetField(secId, itemId, field, val) {
  const sec = (S.trades.insurance.sections || []).find(s => s.id === secId);
  if (!sec) return;
  const item = (sec.items || []).find(it => it.id === itemId);
  if (!item) return;
  item[field] = val;
  setDirty();
  // Descriptions are what the gap check matches on — re-run it in place
  // (onchange, so this fires on blur, not per keystroke).
  if (field === 'name' || field === 'description') refreshInsGaps();
}
function refreshInsGaps() {
  const w = document.getElementById('ins-gap-wrap');
  if (w) w.innerHTML = insCodeGapMarkup();
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

/* ── Page: Product Selection ─────────────────────────────────────────
   One place to pick/write in brand, model, and color for every enabled
   trade — shingle brand & color, drip edge & ridge cap color, gutter
   color, siding/trim color, window frame color, etc. Shown to the
   customer online and printed on the estimate so everyone agrees on
   exactly what's being installed, not just the price. */
function renderProductsPage() {
  const RETAIL_TRADES = TRADES.filter(t => t !== 'insurance');
  const active = RETAIL_TRADES.filter(t => S.trades[t].enabled && (TRADE_COLOR_FIELDS[t]||[]).length);

  const header = `<div class="products-header">
    <h2>Product Selection</h2>
    <p>Brand, model, and color choices for this job — shown to the customer and printed on the estimate.</p>
  </div>`;

  if (!active.length) {
    document.getElementById('page-products').innerHTML = header + `
      <div class="scope-empty">
        <p>No trades enabled yet. Enable a trade on the <strong>Scope</strong> page, then come back here to specify products &amp; colors.</p>
      </div>`;
    return;
  }

  document.getElementById('page-products').innerHTML = header + `
    <div class="products-grid">
      ${active.map(trade => {
        const colors = S.trades[trade].colors || {};
        return `<div class="product-card">
          <div class="product-card-hd">${TRADE_LABELS[trade]}</div>
          <div class="product-card-fields">
            ${TRADE_COLOR_FIELDS[trade].map(f => renderProductColorField(trade, f, colors[f.key] || '')).join('')}
          </div>
        </div>`;
      }).join('')}
    </div>`;
}

// A rep-side color field. For shingle_color / siding_color we render a
// dropdown restricted to the picked bundle's palette (IKO Nordic → IKO
// colors only, CertainTeed → CertainTeed only, LP → LP only, Hardie →
// Hardie only), with a "Custom…" escape for special-order colors that
// aren't on the manufacturer's list. Every other color field stays free
// text, since only roofing/siding bundles carry a palette today.
function renderProductColorField(trade, f, cur) {
  const bundleKey = (trade === 'roofing' && f.key === 'shingle_color') ? 'roofing'
                  : (trade === 'siding'  && f.key === 'siding_color')  ? 'siding'
                  : '';
  if (!bundleKey) {
    return `<div class="field-group">
      <label>${esc(f.label)}</label>
      <input type="text" value="${esc(cur)}" placeholder="${esc(f.label)}…"
        onchange="setTradeColor('${trade}','${f.key}',this.value)">
    </div>`;
  }
  const tier   = tradeTier(bundleKey);
  const colors = _bundleColorsForTradeTier(bundleKey, tier);
  const names  = colors.map(c => c.name);
  const inList = cur && names.indexOf(cur) !== -1;
  const isCustom = cur && !inList;
  const selVal = isCustom ? '__custom__' : (cur || '');
  const opts = names.map(n => `<option value="${esc(n)}"${n === selVal ? ' selected' : ''}>${esc(n)}</option>`).join('');
  const hint = colors.length
    ? `<span class="sr-hint">from your ${esc(bundleKey === 'roofing' ? 'shingle' : 'siding')} package</span>`
    : `<span class="sr-hint">no palette on the picked bundle — enter a color</span>`;
  const customInput = isCustom || !colors.length
    ? `<input type="text" value="${esc(cur)}" placeholder="Type a custom color…"
        style="margin-top:6px" onchange="setTradeColor('${trade}','${f.key}',this.value)">`
    : '';
  return `<div class="field-group">
    <label>${esc(f.label)} ${hint}</label>
    <select onchange="onProductColorSelect('${trade}','${f.key}',this)">
      <option value="">Select a color…</option>
      ${opts}
      <option value="__custom__"${isCustom ? ' selected' : ''}>Custom…</option>
    </select>
    ${customInput}
  </div>`;
}
function onProductColorSelect(trade, key, sel) {
  const v = sel.value;
  if (v === '__custom__') {
    setTradeColor(trade, key, '');
    if (activePage === 'products') renderProductsPage();
    return;
  }
  setTradeColor(trade, key, v);
  if (activePage === 'products') renderProductsPage();
}
function setTradeColor(trade, key, v) {
  if (!S.trades[trade].colors) S.trades[trade].colors = {};
  S.trades[trade].colors[key] = v;
  // Shingle color has its own signing-requirement flow (locked-by-rep vs.
  // customer-chooses-at-signing, under Contract > Signing Requirements).
  // Keep them in sync so setting it here also locks it for the customer —
  // otherwise the sign page would still prompt for a color already specified.
  if (trade === 'roofing' && key === 'shingle_color') {
    if (!S.shingle_selection) S.shingle_selection = { enabled: true, options: _globalShingleColors() };
    S.shingle_selection.chosen = (v || '').trim();
  }
  setDirty();
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
    // Pulled from price book — fill unit, measurement link, costs & descriptions.
    // Field copy mirrors buildTradeDefaults so a single searched-in item prices
    // identically to Load Defaults. Existing values are never clobbered.
    item.unit = t.unit || item.unit;
    if (!item.measure && !item.formula && t.measure) item.measure = t.measure;
    if (!item.measure && !item.formula && t.formula) item.formula = t.formula;
    if (item.bundle_lf === undefined && t.bundle_lf) {
      item.bundle_lf   = t.bundle_lf;
      item.bundle_unit = t.bundle_unit || undefined;
    }
    // Per-tier cost inherits UPWARD when unset: good→base, better→good, best→better.
    const baseCost   = t.cost !== undefined ? parseFloat(t.cost)||0 : 0;
    const costGood   = t.cost_good   !== undefined ? parseFloat(t.cost_good)||0   : baseCost;
    const costBetter = t.cost_better !== undefined ? parseFloat(t.cost_better)||0 : costGood;
    const costBest   = t.cost_best   !== undefined ? parseFloat(t.cost_best)||0   : costBetter;
    const tierCost   = { good: costGood, better: costBetter, best: costBest };
    if (item.tiers) {
      ['good','better','best'].forEach(tier => {
        if (!item.tiers[tier]) item.tiers[tier] = {material_unit_cost:0, labor_unit_cost:0, description:'', notes:''};
        const tt = item.tiers[tier];
        if (!parseFloat(tt.material_unit_cost) && !parseFloat(tt.labor_unit_cost)) tt.material_unit_cost = tierCost[tier];
        if (!tt.description) tt.description = t['product_'+tier] || t['desc_'+tier] || '';
        if (!tt.notes)       tt.notes       = t['notes_'+tier] || '';
        // Attach the product-variant menu if this row defines one and the
        // item doesn't already carry variants. Match the existing label to a
        // variant so the picker reflects it; otherwise it reads "Custom".
        if (!Array.isArray(tt.variants)) {
          const menu = t['variants_'+tier];
          if (Array.isArray(menu) && menu.length) {
            tt.variants = menu.map(v => ({ label: v.label || '', cost: parseFloat(v.cost)||0, notes: v.notes || '', description: v.description || '', features: Array.isArray(v.features) ? v.features.slice() : [] }));
            const hit = tt.variants.findIndex(v => v.label && v.label === tt.description);
            tt.selected_variant = hit >= 0 ? hit : -1;
          }
        }
      });
    } else {
      // Simple mode: pull the Good-tier cost and compute the sell price from
      // the trade's margin/markup (was: description only, leaving the row $0).
      if (!parseFloat(item.unit_cost) && !parseFloat(item.unit_price)) {
        const r = tradeRate(trade);
        item.unit_cost  = costGood;
        item.unit_price = S.pricing.mode === 'markup'
          ? Math.round(costGood * (1 + r / 100) * 100) / 100
          : (r >= 100 ? 0 : Math.round(costGood / (1 - r / 100) * 100) / 100);
      }
      if (!item.description) item.description = t.product_good || t.desc_good || t.desc_better || '';
    }
    const q = measuredQty(item);
    if (q !== null) item.quantity = q;
  }
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
  renderTotals();
}

// Roofing tier detail collapse state, keyed `${trade}:${tier}` (default collapsed).
let _tierDetailsOpen = {};
function toggleTierDetails(trade, tier) {
  const k = trade + ':' + tier;
  _tierDetailsOpen[k] = !_tierDetailsOpen[k];
  if (activePage === 'pricing') renderTradeContent();
}

/* ── Bundles → tier loader (roofing + siding) ─────────────────────────────
   Bundle trades use a flat catalog + named bundles (Price Book). Picking a
   bundle for a tier loads that bundle's catalog products as the tier's included
   items. All tiers' items live in one line_items list (union); per-tier
   `included` decides what each package shows + prices — so pricing/customer/PDF
   are reused unchanged. Independent tiers: Good can be one system, Best another. */
function _tradeCatalog(trade) { return (priceBook && priceBook[trade + '_catalog']) || []; }
function _tradeBundles(trade) { return (priceBook && priceBook[trade + '_bundles']) || []; }
function _tradeBundle(trade, id) { return _tradeBundles(trade).find(b => b.id === id) || null; }

/* ── A package card describes the products actually in the bundle ─────────
   The What's Included bullets are BUILT from the bundle's product_ids, not
   stored as a blob on the bundle. The blob was one list per bundle, so every
   siding bundle promised "Soffit and fascia included" whether or not soffit
   was in it, and a manager-built bundle got whatever copy it was typed with —
   the card and the line items could drift apart forever.

   Each catalog product carries its own `bullets` (the material products carry
   the sales bullets, accessories carry one line each). A product with no
   `bullets` key falls back to its name — the honest answer for a manager's
   own product, and it still tracks the bundle. Rules:
     * `bullets: [...]`            -> exactly those lines, EVEN IF the product is
       hidden from the customer. Hiding a row hides the PRICE, not the promise:
       the customer must not see "Install Labor — $9,400" broken out, but
       "Installed by Project One crews to manufacturer spec" is one of the
       strongest lines on the card;
     * `bullets: []` (explicit)    -> contributes nothing, so a manager can
       silence a line without hiding its price;
     * key ABSENT                  -> `[name]`, unless the product is hidden
       (`customer_visible: false`), in which case it says nothing — a product
       nobody wrote copy for and nobody shows the price of has no business
       naming itself on the card. Absence vs empty is the test, same contract
       as manual measures and bundle copy backfill.
   `bundle.extra_features` closes the list with the bullets no product owns —
   the workmanship warranty. Order follows product_ids so the material leads. */
function bundleFeatures(trade, bundle) {
  if (!bundle) return [];
  const catalog = _tradeCatalog(trade);
  const out = [];
  const seen = new Set();
  const push = s => {
    const t = String(s == null ? '' : s).trim();
    if (t && !seen.has(t)) { seen.add(t); out.push(t); }
  };
  (bundle.product_ids || []).forEach(pid => {
    const p = catalog.find(x => x.id === pid);
    if (!p) return;
    if (Array.isArray(p.bullets)) p.bullets.forEach(push);
    else if (p.customer_visible !== false) push(p.name);
  });
  (bundle.extra_features || []).forEach(push);
  return out;
}

/* First product in the bundle that carries its own tagline wins. Falls back to
   the bundle's default `description`. The two knobs stay independent: a manager
   can set every material's tagline once and reuse them across bundles without
   editing bundles at all, while leftover bundles keep their old default. */
function bundleDescription(trade, bundle) {
  if (!bundle) return '';
  const catalog = _tradeCatalog(trade);
  for (const pid of (bundle.product_ids || [])) {
    const p = catalog.find(x => x.id === pid);
    const d = p && typeof p.desc === 'string' ? p.desc.trim() : '';
    if (d) return d;
  }
  return (typeof bundle.description === 'string') ? bundle.description : '';
}

function applyBundleToTier(trade, tier, bundleId, autoOpen) {
  if (!isBundleTrade(trade)) return;
  const td = S.trades[trade];
  td.tier_bundles = td.tier_bundles || { good:'', better:'', best:'' };
  td.line_items = td.line_items || [];

  const bundle = _tradeBundle(trade, bundleId);
  if (bundleId === '__custom__' || !bundle) {
    // Custom is a BLANK SLATE for this tier — the rep is building a package the
    // price book doesn't sell, and starting from the last bundle's items means
    // deleting a dozen rows first. Everything here is excluded from THIS tier
    // only: nothing is deleted, the other tiers keep pricing their own systems,
    // and re-picking a bundle brings it all straight back.
    //
    // Switching to Custom when the tier is ALREADY custom is a no-op. That is
    // the re-render / reopened-estimate case, and it must never wipe the
    // package the rep just typed in by hand.
    if (td.tier_bundles[tier] !== '__custom__') {
      td.line_items.forEach(item => {
        if (item.tiers && item.tiers[tier]) item.tiers[tier].included = false;
      });
      td.tier_bundles[tier] = '__custom__';
    }
    if (autoOpen) _tierDetailsOpen[trade + ':' + tier] = true;
    setDirty();
    if (activePage === 'pricing') renderTradeContent();
    renderTotals();
    return;
  }
  // Leaving Custom for a real bundle drops the hand-typed package name — the
  // bundle names itself, and a stale "Luke's cedar package" over a Hardie card
  // is the same stale-copy bug the bundle description swap fixes.
  if (td.tier_bundle_names) td.tier_bundle_names[tier] = '';
  const catalog = _tradeCatalog(trade);
  const wantIds = new Set(bundle.product_ids || []);
  const norm = s => String(s || '').trim().toLowerCase();

  (bundle.product_ids || []).forEach(pid => {
    const p = catalog.find(x => x.id === pid);
    if (!p) return;
    let item = td.line_items.find(li => li.catalog_id === pid);
    // Adopt a legacy same-named item (built before this trade moved to bundles,
    // so it has no catalog_id) instead of adding a duplicate beside it.
    if (!item) {
      const legacy = td.line_items.find(li => !li.catalog_id && !li.vent_role && norm(li.name) === norm(p.name));
      if (legacy) { legacy.catalog_id = pid; item = legacy; }
    }
    if (!item) {
      item = {
        id: uid(), catalog_id: pid, name: p.name, unit: p.unit || 'EA',
        quantity: 0, scope_note: '',
        measure: p.measure || undefined,
        bundle_lf: p.bundle_lf || undefined, bundle_unit: p.bundle_unit || undefined,
        customer_visible: p.customer_visible !== false,
        tiers: {
          good:   { material_unit_cost:0, labor_unit_cost:0, description:'', notes:'', included:false },
          better: { material_unit_cost:0, labor_unit_cost:0, description:'', notes:'', included:false },
          best:   { material_unit_cost:0, labor_unit_cost:0, description:'', notes:'', included:false },
        },
      };
      td.line_items.push(item);
    } else if (item.measure === undefined && !item.formula && p.measure) {
      // Adopted item that never had an Auto-Qty link: inherit the catalog's.
      // An explicit '' (Manual) is left alone — see the manual-measure contract.
      item.measure = p.measure;
    }
    const cell = item.tiers[tier] || (item.tiers[tier] = {material_unit_cost:0,labor_unit_cost:0,description:'',notes:'',included:false});
    cell.included = true;
    cell.material_unit_cost = parseFloat(p.cost) || 0;
    if (!cell.description) cell.description = p.name || '';
    delete cell.price_override;
    // The bundle IS the product choice now — drop any per-tier variant menu an
    // adopted (pre-bundle) item carried, so the row can't show a stale product
    // dropdown next to a cost the bundle set.
    delete cell.variants; delete cell.selected_variant;
  });
  // Exclude catalog-backed items NOT in this bundle from THIS tier (they stay
  // for other tiers that include them). Hand-added items are left untouched.
  td.line_items.forEach(item => {
    if (!item.catalog_id) return;
    if (!wantIds.has(item.catalog_id) && item.tiers && item.tiers[tier]) {
      item.tiers[tier].included = false;
    }
  });
  td.tier_bundles[tier] = bundleId;
  // Reset the per-tier profile when the picked bundle doesn't offer the
  // stored one. Switching LP → Hardie must not leave `lap_8` on a bundle
  // whose valid options are `lap_8_25` / `bb_4x10` / `shake_*`.
  if (trade === 'siding') {
    const cfg = SIDING_BUNDLE_PROFILES[bundleId];
    td.tier_profiles = td.tier_profiles || { good:'', better:'', best:'' };
    if (!cfg) {
      td.tier_profiles[tier] = '';
    } else if (!cfg.options.includes(td.tier_profiles[tier])) {
      td.tier_profiles[tier] = cfg.default;
    }
  }

  // The bundle owns this tier's customer story, so picking one REPLACES the
  // Options-page tagline and bullets — including a rep's hand-edits. That's the
  // point: swapping Best from a laminate shingle to standing seam must not leave
  // shingle copy on the card. Copy the bundle doesn't define is left alone,
  // since there'd be nothing to replace it with.
  //
  // Tagline preference: first product in the bundle with its own `desc` wins,
  // so swapping the primary material inside a bundle carries its tagline with
  // it. Falls back to bundle.description when no product overrides it.
  const feats = bundleFeatures(trade, bundle);
  const desc  = bundleDescription(trade, bundle);
  if (desc || feats.length) {
    const content = tradeTierContent(trade);
    if (desc) content.descriptions[tier] = desc;
    if (feats.length) content.features[tier] = feats;
  }

  // When the rep explicitly picks a bundle, open this tier's details so the
  // freshly-loaded items are visible immediately (otherwise they sit behind the
  // collapsed "Show details" toggle). Bulk seeding leaves tiers collapsed.
  if (autoOpen) _tierDetailsOpen[trade + ':' + tier] = true;

  if (trade === 'commercial') _syncCommAttachment();   // after the rebuild
  applyMeasurements();
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
  renderTotals();
}

/* The customer-facing name of a tier's package. Only a CUSTOM package carries
   one — a bundle tier is already named by its bundle, and duplicating that name
   into the estimate would let the two drift apart. Mirrored server-side by
   _tier_package_name() in app.py, which reads the same field. */
function tierPackageName(trade, tier) {
  const td = S.trades[trade] || {};
  return String((td.tier_bundle_names || {})[tier] || '').trim();
}
function setTierBundleName(trade, tier, v) {
  const td = S.trades[trade]; if (!td) return;
  td.tier_bundle_names = td.tier_bundle_names || { good:'', better:'', best:'' };
  td.tier_bundle_names[tier] = String(v || '').trim();
  setDirty();
  if (activePage === 'options') renderOptionsPage();
}

// Seed all three tiers of a bundle trade from its default bundles.
function seedTradeBundles(trade, force) {
  const td = S.trades[trade]; if (!td || !isBundleTrade(trade)) return;
  const defaults = (priceBook && priceBook[trade + '_tier_defaults']) || {};
  td.tier_bundles = td.tier_bundles || { good:'', better:'', best:'' };
  TIERS.forEach(tier => {
    if (force || !td.tier_bundles[tier]) {
      const bid = defaults[tier];
      if (bid && _tradeBundle(trade, bid)) applyBundleToTier(trade, tier, bid);
    }
  });
}
// Build (or rebuild) a bundle trade's items from its default bundles — the ONE
// way those items are created now. Replaces buildTradeDefaults(trade) everywhere
// roofing/siding was auto-built: mixing the two produced the legacy material
// line + duplicated accessories alongside the bundle items. Clears first so a
// rebuild never stacks on top of existing items.
function buildBundleDefaults(trade) {
  const td = S.trades[trade]; if (!td || !isBundleTrade(trade)) return;
  // A bundle trade selling as ONE price (commercial by default) needs flat
  // simple-mode items, not per-tier ones — the simple pricer reads unit_price
  // and would total a tier-shaped item at $0.
  if (effectiveTradeMode(trade, td) === 'simple') {
    td.line_items = [];
    const bid = td.simple_bundle || defaultSimpleBundle(trade);
    if (bid && _tradeBundle(trade, bid)) applyBundleToSimple(trade, bid);
    return;
  }
  td.line_items = [];
  td.tier_bundles = { good:'', better:'', best:'' };
  seedTradeBundles(trade, true);
  if (trade === 'commercial') _syncCommAttachment();   // after the rebuild
}
// The system a single-price bundle trade starts on. Falls back to the Better
// tier default so a price book that predates <trade>_simple_default still works.
function defaultSimpleBundle(trade) {
  return (priceBook && priceBook[trade + '_simple_default'])
      || (((priceBook && priceBook[trade + '_tier_defaults']) || {}).better)
      || '';
}
/* Build the flat, single-price line items for a bundle. Deliberately does NOT
   apply margin — the caller runs simpleApplyMargin — so this stays a plain
   data transform that bundle_runner.js can exercise without the pricing chain.
   Quantities, scope notes, and Manual-measure choices survive a system swap:
   re-picking TPO → EPDM must not wipe the squares the rep already entered. */
/* `sectionName` builds the system for ONE building: the carry-over map is
   scoped to that building's rows and the output is tagged to it. Without it
   every building's rows collide in `prev` on the shared catalog_id, and all
   seven inherit the quantity and locked price of whichever happened to sit
   last in line_items. Omitted, this behaves exactly as it did. */
function buildSimpleItemsFromBundle(trade, bundleId, sectionName) {
  const bundle = _tradeBundle(trade, bundleId);
  if (!bundle) return null;
  const catalog = _tradeCatalog(trade);
  const td = S.trades[trade] || {};
  const prev = new Map();
  (td.line_items || []).forEach(li => {
    if (!li.catalog_id) return;
    if (sectionName !== undefined && itemSection(li) !== sectionName) return;
    prev.set(li.catalog_id, li);
  });

  const items = [];
  (bundle.product_ids || []).forEach(pid => {
    const p = catalog.find(x => x.id === pid);
    if (!p) return;
    const old = prev.get(pid);
    items.push({
      id: old ? old.id : uid(),
      catalog_id: pid,
      name: p.name,
      unit: p.unit || 'EA',
      quantity: old ? old.quantity : 0,
      scope_note: old ? (old.scope_note || '') : '',
      description: (old && old.description) || p.name || '',
      // An explicit '' (Manual) on the existing item is preserved — same
      // manual-measure contract the GBB path honors.
      measure: (old && old.measure !== undefined) ? old.measure : (p.measure || undefined),
      bundle_lf: p.bundle_lf || undefined,
      bundle_unit: p.bundle_unit || undefined,
      customer_visible: p.customer_visible !== false,
      unit_cost: parseFloat(p.cost) || 0,
      unit_price: (old && old.price_locked) ? old.unit_price : 0,
      price_locked: (old && old.price_locked) || undefined,
      section: sectionName || undefined,
    });
  });
  // Hand-added rows (no catalog_id) belong to the rep, not the bundle — they
  // ride through a system swap untouched.
  (td.line_items || []).forEach(li => { if (!li.catalog_id) items.push(li); });
  return items;
}
/* Picking a system rebuilds the trade from the bundle. On a complex that has
   to happen once PER BUILDING: the old `td.line_items = items` collapsed seven
   roofs into one set of rows and took six buildings off the estimate. Each
   building keeps its own quantities and locked prices through the carry-over in
   buildSimpleItemsFromBundle, and rows filed outside a building are left where
   the rep put them. */
function applyBundleToSimple(trade, bundleId) {
  const td = S.trades[trade]; if (!td || !isBundleTrade(trade)) return;
  const structures = tradeStructures(trade);
  if (!structures.length) {
    const items = buildSimpleItemsFromBundle(trade, bundleId);
    if (!items) return;
    td.line_items = items;
    items.forEach(it => simpleApplyMargin(trade, it));
  } else {
    if (!_tradeBundle(trade, bundleId)) return;
    if (structures.length > 1 &&
        !confirm(`Rebuild all ${structures.length} buildings with this system? `
               + 'Their measurements and quantities are kept.')) return;
    const names = new Set(structures.map(st => String(st.name || '').trim()));
    const keep  = (td.line_items || []).filter(i => !names.has(itemSection(i)));
    // Built against the ORIGINAL line_items -- assigning inside the loop would
    // let building 2 carry over from building 1's freshly-written rows.
    const rebuilt = [];
    structures.forEach(st => {
      const items = buildSimpleItemsFromBundle(trade, bundleId, String(st.name || '').trim());
      if (!items) return;
      items.forEach(it => simpleApplyMargin(trade, it));
      rebuilt.push(...items);
    });
    td.line_items = keep.concat(rebuilt);
  }
  td.simple_bundle = bundleId;
  _syncCommAttachment();          // AFTER the rebuild — the new system decides
  applyMeasurements();
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
  renderTotals();
}

/* ── Which layers does this system actually fasten? ─────────────────────
   The PRODUCT carries the fact, because the product IS the fact ("TPO
   Membrane 60-mil (Mechanically Attached)"). Reading it off the products
   rather than the bundle means a manager can rename a bundle, or build their
   own out of catalog products, and the answer still holds.

   The result is stored as measurements (comm_seam_attach / comm_insul_attach)
   for the same reason comm_work_type and iw_second_row are: MEASURE_DEFS and
   custom formulas can only see measurements, it persists with the estimate,
   and app.py then gets the same answer for the packet without loading the
   price book. It also keeps commercialFastening(m, table) pure. */
/* The bundle this estimate is actually selling. Mode-aware on purpose:
   setTradeMode does NOT clear simple_bundle, so a gbb→simple→gbb round trip
   leaves a stale id behind, and an unconditional `td.simple_bundle ||` then
   resolves the abandoned system instead of the tier the rep is looking at. */
function _commBundleId(trade) {
  const td = S.trades[trade] || {};
  return effectiveTradeMode(trade, td) === 'simple'
    ? (td.simple_bundle || '')
    : ((td.tier_bundles || {})[tradeTier(trade)] || '');
}
/* The attach profile for ONE bundle id. Split out from _commAttachProfile so
   the G/B/B grid can ask about a tier the rep has not selected — that is how
   it warns when two offered packages disagree on how they fasten. */
function _commAttachProfileForBundle(trade, bundleId) {
  const ov = ((_fastenTable && _fastenTable.bundle_overrides) || {})[bundleId];
  if (ov && (ov.insulation !== undefined || ov.seam !== undefined)) {
    return { insulation: ov.insulation !== false, seam: !!ov.seam, source: 'override' };
  }
  const bundle = _tradeBundle(trade, bundleId);
  const catalog = _tradeCatalog(trade);
  const ids = (bundle && bundle.product_ids) || [];
  const kinds = ids.map(pid => (catalog.find(p => p.id === pid) || {}).attach).filter(Boolean);
  if (kinds.includes('coating'))    return { insulation:false, seam:false, source:'coating' };
  if (kinds.includes('mechanical')) return { insulation:true,  seam:true,  source:'mechanical' };
  if (kinds.includes('adhered'))    return { insulation:true,  seam:false, source:'adhered' };
  // Unknown system (a manager-built bundle with no tagged membrane): fail
  // CLOSED on seam. An adhered roof has no seam fasteners, and guessing yes
  // would put thousands of phantom screws on a bid. The panel says so out loud.
  return { insulation:true, seam:false, source:'unknown' };
}
function _commAttachProfile(trade) {
  return _commAttachProfileForBundle(trade, _commBundleId(trade));
}
function _syncCommAttachment() {
  const td = S.trades.commercial;
  if (!td) return;
  const p = _commAttachProfile('commercial');
  if (!S.measurements) S.measurements = {};
  S.measurements.comm_seam_attach  = p.seam ? 1 : 0;
  S.measurements.comm_insul_attach = p.insulation ? 1 : 0;
  return p;
}

/* Warn when the offered commercial packages disagree on how they fasten.

   Fastener QUANTITY is a shared item field across all three tiers, but
   comm_seam_attach / comm_insul_attach are estimate-level and follow the
   SELECTED tier. So offering a mechanically fastened package beside an adhered
   one means the screw counts on both move when the rep changes the selection —
   the Better price shifts because they clicked Best.

   The shipped ladder (coating / TPO layover MF / TPO tear-off MF) does not
   trip this: the coating fastens nothing at all and the other two agree. A rep
   who swaps a tier to a fully adhered system does, and needs telling. Making
   the counts genuinely per-tier is a larger change across three mirrored
   surfaces; until then this is the honest warning. */
function commFastenMismatchNag(trade, shown) {
  if (trade !== 'commercial') return '';
  const td = S.trades[trade] || {};
  const seen = shown
    .map(t => ({ tier: t, id: (td.tier_bundles || {})[t] }))
    .filter(x => x.id && x.id !== '__custom__')
    .map(x => ({ tier: x.tier, p: _commAttachProfileForBundle(trade, x.id) }))
    // A coating fastens nothing on either layer, so it cannot disagree with
    // anything — it is a different kind of job, not a different attachment.
    .filter(x => x.p.source !== 'coating');
  if (seen.length < 2) return '';
  if (new Set(seen.map(x => x.p.seam)).size < 2) return '';
  const say = kind => seen.filter(x => x.p.seam === kind)
    .map(x => TIER_LABELS[x.tier] || x.tier).join(' and ');
  return `
    <div class="tier-unpriced-warn comm-fasten-nag">⚠️ ${esc(say(true))} ${
      say(true).includes(' and ') ? 'are' : 'is'} mechanically fastened and ${esc(say(false))} ${
      say(false).includes(' and ') ? 'are' : 'is'} fully adhered. Seam-fastener counts
      follow whichever package is SELECTED, so check the fastener lines on each column
      before sending.</div>`;
}

function renderGBBGrid(trade) {
  const items = S.trades[trade].line_items;
  const tier  = tradeTier(trade);   // highlight THIS product's chosen package
  const qtyOf = item => parseFloat(item.quantity) || 0;
  const rateLbl = S.pricing.mode === 'markup' ? 'Markup' : 'Margin';
  const shown = enabledTiers();
  const isRoof = isBundleTrade(trade);
  const grid  = shown.map(t => {
    const inc      = item => item.tiers?.[t]?.included !== false;
    // A bundle tier shows EVERY item the bundle put in it, priced or not. The
    // details panel is the one place the rep edits the system, and an accessory
    // that stays hidden until a measurement exists can't be costed, renamed or
    // dropped — the rep can't even see what the bundle actually loaded. Other
    // trades keep the measured view: 0-qty rows collapse into the chips below.
    const visible  = isRoof ? items.filter(inc)
                            : items.filter(item => inc(item) && (qtyOf(item) > 0 || item._showZero));
    const excluded = items.filter(item => !inc(item));
    const zeroQty  = isRoof ? [] : items.filter(item => inc(item) && qtyOf(item) === 0 && !item._showZero);
    // Nothing is measured yet, so every row reads 0 — say why rather than let
    // the rep think the bundle loaded empty.
    const needsMeasure = isRoof && visible.length > 0 && !visible.some(item => qtyOf(item) > 0);
    // Per-trade, per-tier margin: the value shown here overrides the sidebar
    // default for THIS trade only. Blank = inherit the default (shown as the
    // input placeholder). `dflt` is the effective rate with the trade override
    // removed, so the placeholder tells the truth for legacy per_trade_overrides.
    const tradeRateVal = ((S.pricing.trade_rates || {})[trade] || {})[t];
    const hasTradeRate = tradeRateVal !== null && tradeRateVal !== undefined && tradeRateVal !== '';
    const dflt = _resolveRate([(S.pricing.per_trade_overrides || {})[trade],
                               (S.pricing.tier_rates || {})[t],
                               S.pricing.global_rate]);

    // Bundle trades: a hero bundle dropdown drives each tier; the item breakdown
    // collapses under a "Show details" toggle (default collapsed) so the tab
    // stays clean. Other trades keep the full per-item layout + excluded chips.
    const bundles = _tradeBundles(trade);
    const selBundle = ((S.trades[trade].tier_bundles) || {})[t] || '';
    const isCustomBundle = selBundle === '__custom__' || (selBundle && !bundles.some(b => b.id === selBundle));
    const detailsOpen = !!_tierDetailsOpen[trade + ':' + t];

    const emptyMsg = isCustomBundle
      ? 'Custom package — blank slate. Add your line items below.'
      : (isRoof ? 'Pick a Product above to load a system, or add an item'
                : 'Load Defaults or add an item below');
    const itemsInner = visible.length ? groupedTradeItems(trade, visible).map(g => {
        if (!g.items.length) return g.name ? `<div class="tier-section-hd">${esc(g.name)}<span class="tier-section-empty">empty</span></div>` : '';
        const hd = g.name ? `<div class="tier-section-hd">${esc(g.name)}</div>`
                 : (tradeSections(trade).length ? `<div class="tier-section-hd tier-section-general">General</div>` : '');
        return hd + g.items.map(item => renderLiRow(trade,t,item)).join('');
      }).join('')
      : `<div class="empty-items">${emptyMsg}</div>`;

    // A custom package has no bundle to take its name from, so the rep names it.
    // That name is what the customer sees on the package card in place of the
    // bundle's; blank leaves the card as plain Good/Better/Best.
    const customName = ((S.trades[trade].tier_bundle_names) || {})[t] || '';
    // Siding-only profile picker (lap / B&B / shake) — takeoff-only property.
    // Drives the Material Order piece counts, does NOT change the sell price;
    // a yellow nag says so when a non-default profile is picked so the rep
    // knows to per-line override if B&B/Shake costs are different at QXO.
    let sidingProfileChip = '';
    let sidingProfileNag  = '';
    if (trade === 'siding' && SIDING_BUNDLE_PROFILES[selBundle]) {
      const cfg = SIDING_BUNDLE_PROFILES[selBundle];
      const cur = sidingProfile(t);
      sidingProfileChip = `
        <label class="tier-hero-lbl tier-profile-lbl" title="Exposure / board style — drives the Material Order piece counts.">Profile</label>
        <select class="tier-hero-select tier-profile-select"
          onchange="setSidingProfile('${t}', this.value)"
          title="Exposure / board style — drives the material-order piece counts on the production packet.">
          ${cfg.options.map(o => `<option value="${o}" ${o===cur?'selected':''}>${esc(SIDING_PROFILE_LABELS[o] || o)}</option>`).join('')}
        </select>`;
      if (cur && cur !== cfg.default) {
        sidingProfileNag = `<div class="tier-profile-nag" title="Board & Batten and Shake usually cost more per SQ than lap. The material-order piece counts on the packet ARE profile-aware, but the sell price uses the bundle's baseline $/SQ — override the material line's cost on this tier if QXO's price differs.">⚠ ${esc(SIDING_PROFILE_LABELS[cur] || cur)} typically costs more per SQ than lap — set a per-line cost override if QXO's price differs.</div>`;
      }
    }
    const heroSel = isRoof ? `
      <div class="tier-hero">
        <label class="tier-hero-lbl">Product</label>
        <select class="tier-hero-select" onchange="applyBundleToTier('${trade}','${t}',this.value,true)">
          ${selBundle ? '' : `<option value="" disabled selected>— pick a product —</option>`}
          ${bundles.map(b=>`<option value="${b.id}" ${b.id===selBundle?'selected':''}>${esc(b.name||'(unnamed)')}</option>`).join('')}
          <option value="__custom__" ${isCustomBundle?'selected':''}>Custom…</option>
        </select>
        ${sidingProfileChip}
        ${isCustomBundle ? `
        <input class="tier-hero-name" type="text" value="${esc(customName)}"
          placeholder="Name this package…"
          title="What this custom package is called — shown to the customer on the package card"
          onchange="setTierBundleName('${trade}','${t}',this.value)">` : ''}
      </div>
      ${sidingProfileNag}
      ${unpricedWarnHtml(trade, unpricedBundleLines(trade, t), TIER_LABELS[t] || t,
                         'tier-unpriced-warn', 2)}` : '';

    const measureNudge = needsMeasure
      ? `<div class="tier-measure-nudge">Bundle loaded — enter ${trade === 'siding' ? 'siding' : 'roof'} measurements on the Scope page to set quantities and price.</div>`
      : '';
    const bodyBlock = isRoof ? `
      ${measureNudge}
      <button class="tier-details-toggle" onclick="toggleTierDetails('${trade}','${t}')">
        ${detailsOpen ? '▾ Hide details' : `▸ Show ${visible.length} item${visible.length===1?'':'s'}`}
      </button>
      <div class="tier-items tier-items-collapsible" ${detailsOpen?'':'style="display:none"'}>
        ${itemsInner}
        <button class="li-tier-add" onclick="addLineItem('${trade}','${t}')">+ Add Item</button>
      </div>`
      : `
      <div class="tier-items">${itemsInner}</div>
      ${(excluded.length || zeroQty.length) ? `
        <div class="tier-excluded">
          ${excluded.length ? `
            <div class="tier-excluded-header">
              <span class="tier-excluded-label">Not in ${TIER_LABELS[t]}</span>
              <button class="tier-include-all-btn" onclick="includeAllInTier('${trade}','${t}')" title="Re-include all excluded items in this tier">Include All ↑</button>
            </div>
            ${excluded.map(item => `
              <button class="tier-excluded-chip" title="Add back to ${TIER_LABELS[t]}"
                onclick="liSetIncluded('${trade}','${item.id}','${t}',true)">+ ${esc(item.name||'(item)')}</button>`).join('')}` : ''}
          ${zeroQty.length ? `<div class="tier-excluded-label">0 qty — not in scope</div>
            ${zeroQty.map(item => `
              <button class="tier-excluded-chip tier-zero-chip" title="Show to enter a quantity"
                onclick="liRevealZero('${trade}','${item.id}')">+ ${esc(item.name||'(item)')}</button>`).join('')}` : ''}
        </div>` : ''}
      <button class="li-tier-add" onclick="addLineItem('${trade}')">+ Add Item</button>`;

    return `
    <div class="tier-column col-${t} ${t===tier?'selected-tier':''}">
      <div class="tier-col-header">
        ${TIER_LABELS[t]} <span class="tier-col-total">${fmtCur(tradeTotal(trade,t))}</span>
      </div>
      <div class="tier-col-rate" title="${rateLbl} for ${TRADE_LABELS[trade]} · ${TIER_LABELS[t]}. Blank uses the ${dflt}% default set in the sidebar.">
        <span class="tier-col-rate-lbl">${rateLbl}</span>
        <input type="number" min="0" max="99" step="0.5"
          value="${hasTradeRate ? tradeRateVal : ''}" placeholder="${dflt}"
          onchange="setTradeTierRate('${trade}','${t}', this.value)">
        <span class="tier-col-rate-pct">%</span>
        ${hasTradeRate ? `<span class="tier-col-rate-ovr" title="Custom ${rateLbl.toLowerCase()} for this trade — clear to fall back to the ${dflt}% default">custom</span>` : ''}
      </div>
      ${heroSel}
      ${bodyBlock}
    </div>`;
  }).join('');
  return `
    <div class="gbb-pkg-toggles">
      <span class="gbb-pkg-toggles-lbl">Packages offered:</span>
      ${TIERS.map(t=>`<label class="scope-trade-toggle ${tierEnabled(t)?'enabled':''}"
        title="${tierEnabled(t)?`Offering ${TIER_LABELS[t]} — click to sell without it`:`${TIER_LABELS[t]} is off — click to offer it`}">
        <input type="checkbox" ${tierEnabled(t)?'checked':''} onchange="toggleTierEnabled('${t}')">
        ${TIER_LABELS[t]}
      </label>`).join('')}
    </div>
    ${sectionManagerBar(trade)}
    ${commFastenMismatchNag(trade, shown)}
    <div class="gbb-grid" style="grid-template-columns:repeat(${shown.length},1fr)">${grid}</div>
    <div class="gbb-swipe-hint">← swipe to see all packages →</div>
    ${pbDatalist(trade)}
    <div class="subtotal-bar">
      ${shown.map(t=>`<div class="tier-subtotal ${t===tier?'sel-'+t:''}">
        ${TIER_LABELS[t]}<strong>${fmtCur(tradeTotal(trade,t))}</strong>
      </div>`).join('')}
    </div>`;
}

// Compact per-tier line item row — all three tiers are fully editable.
// The master name is normally typed in the Better column (Good/Best show it as
// a static label) so the rep isn't given three boxes for one value; qty + unit
// are SHARED fields editable from ANY column — changing one updates the item
// everywhere. Per-tier fields (description, cost, include/exclude) are editable
// in every column.
//
// Better can't always carry the name, though. A row added from inside a Custom
// Good or Best belongs to that tier ALONE (see addLineItem), so it never
// renders in the Better column at all — and a row the rep just added has no
// name yet, so the static label is blank. Either way the rep was left looking
// at an empty label with nowhere to type, able to write a description for a
// line item that has no name. Whichever column actually owns the row gets the
// name input, and a brand-new unnamed row offers it wherever it shows up.
function renderLiRow(trade, tier, item) {
  const t        = (item.tiers && item.tiers[tier]) || {material_unit_cost:0,labor_unit_cost:0,description:'',notes:'',included:true};
  const included = t.included !== false;
  const isB      = tier === 'better';
  // Does Better carry this row? If not, this column is the only place the
  // master name, ordering and the customer-visible toggle can be reached.
  const ownsMaster = isB || ((item.tiers || {}).better || {}).included === false;
  const nameHere   = ownsMaster || !String(item.name || '').trim();
  const UNITS    = ['SQ','LF','EA','HR','LS','SF','BD'];
  const mat  = parseFloat(t.material_unit_cost) || 0;
  const lab  = parseFloat(t.labor_unit_cost)    || 0;
  const calcTot = lineTotal(item.quantity, mat, lab, trade, tier);
  const override = (t.price_override !== undefined && t.price_override !== null && t.price_override !== '')
    ? parseFloat(t.price_override) : null;
  const tot  = override !== null ? override : calcTot;
  const isVisible = item.customer_visible !== false;

  const sections = tradeSections(trade);
  // Same reasoning as the name: a tier-only row has no Better column to be
  // filed from, and on a multi-building estimate the section IS the building
  // whose measurements price it — unreachable would mean unpriceable.
  const sectionSel = (ownsMaster && sections.length) ? `
    <select class="li-section-select" title="Which section this item belongs to"
      onchange="liSetSection('${trade}','${item.id}',this.value)">
      <option value="">General</option>
      ${sections.map(s=>`<option value="${esc(s)}" ${itemSection(item)===s?'selected':''}>${esc(s)}</option>`).join('')}
    </select>` : '';

  // Product picker: when this tier carries a variant menu (any trade — e.g.
  // roofing material types, siding product/exposure), let the rep choose which
  // specific product is quoted. Selecting a variant fills this tier's cost +
  // description; a manual edit of either flips it to "Custom".
  const variants = Array.isArray(t.variants) ? t.variants : [];
  const selVar   = (t.selected_variant === undefined || t.selected_variant === null) ? -1 : t.selected_variant;
  const variantSel = variants.length ? `
    <div class="li-row-variant-row">
      <select class="li-row-variant-select" title="Product / exposure quoted for the ${TIER_LABELS[tier]} package"
        onchange="liSetVariant('${trade}','${item.id}','${tier}',this.value)">
        ${variants.map((v,vi)=>`<option value="${vi}" ${vi===selVar?'selected':''}>${esc(v.label||('Option '+(vi+1)))}${(v.cost!==undefined&&v.cost!=='')?` — ${fmtCur(parseFloat(v.cost)||0)}`:''}</option>`).join('')}
        <option value="-1" ${selVar===-1?'selected':''}>Custom…</option>
      </select>
    </div>` : '';

  return `<div class="li-row-card${!included?' li-row-excluded':''}${!isVisible?' li-row-hidden':''}">
    <div class="li-row-top">
      ${nameHere
        ? `<input class="li-row-name-input" type="text" value="${esc(item.name)}" list="pb-list-${trade}"
             placeholder="Type to search price book…"
             onchange="liSetNameSmart('${trade}','${item.id}',this.value)">`
        : `<span class="li-row-name-static">${esc(item.name)}</span>`}
      <div class="li-row-actions">
        ${ownsMaster ? `<button class="li-move-btn" onclick="liMove('${trade}','${item.id}',-1)" ${liCanMove(trade,item,-1)?'':'disabled'} title="Move up">↑</button>
        <button class="li-move-btn" onclick="liMove('${trade}','${item.id}',1)" ${liCanMove(trade,item,1)?'':'disabled'} title="Move down">↓</button>
        <label class="li-vis-toggle${!isVisible?' vis-off':''}" title="${isVisible?'Shown on customer estimate':'Hidden from customer'}">
          <input type="checkbox" ${isVisible?'checked':''} onchange="liSetVisible('${trade}','${item.id}',this.checked)">
          ${isVisible?'👁':'🚫'}
        </label>` : ''}
        <button class="li-row-del" onclick="liDelete('${trade}','${item.id}')" title="Remove item">×</button>
      </div>
    </div>
    ${sectionSel}
    ${variantSel}
    <div class="li-row-desc-row">
      <textarea class="li-row-desc-input desc-ta" rows="${descRows(t.description)}"
        placeholder="${tier==='good'?'e.g. 3-Tab':tier==='better'?'e.g. Architectural':'e.g. Designer'}"
        title="Description shown to the customer on this tier — Enter starts a new line"
        oninput="autoGrow(this)"
        onchange="liSetTier('${trade}','${item.id}','${tier}','description',this.value)"
        >${esc(t.description||'')}</textarea>
    </div>
    <div class="li-row-numbers">
      <input class="li-row-qty-input" type="number" inputmode="decimal" min="0" step="0.5"
        value="${item.quantity||''}" placeholder="Qty" title="Qty — shared across all tiers; edit from any column"
        onchange="liSetQty('${trade}','${item.id}',this.value)">
      <select class="li-row-unit-select" title="Unit — shared across all tiers"
        onchange="liSetUnit('${trade}','${item.id}',this.value)">
        ${UNITS.map(u=>`<option ${item.unit===u?'selected':''}>${u}</option>`).join('')}
      </select>
      <span class="li-row-sep">×</span>
      <div class="li-row-cost-wrap">
        <input class="li-row-cost-input" type="number" min="0" step="0.01"
          value="${mat||''}" placeholder="Cost"
          title="Material cost per ${displayUnit(item)} for ${TIER_LABELS[tier]}${lab ? ` — plus ${fmtCur(lab)}/${displayUnit(item)} labor below` : ''}"
          onchange="liSetTier('${trade}','${item.id}','${tier}','material_unit_cost',parseFloat(this.value)||0)">
      </div>
      ${included
        ? `<div class="li-row-total-wrap${override !== null ? ' has-override' : ''}">
             <input class="li-row-total-input" type="number" step="0.01" min="0"
               value="${tot.toFixed(2)}"
               title="Sell price — edit to lock a specific amount (overrides margin)"
               onchange="liSetPriceOverride('${trade}','${item.id}','${tier}',this.value)"
               onfocus="this.select()">
             ${override !== null ? `<button class="li-override-reset" onclick="liClearPriceOverride('${trade}','${item.id}','${tier}')" title="Reset to margin-calculated price">↺</button>` : ''}
           </div>`
        : '<span class="li-row-total">—</span>'}
    </div>
    ${lab ? `<div class="li-row-labor-note" title="This item carries a labor cost per unit — it's part of the sell price above">
      🔧 + ${fmtCur(lab)}/${displayUnit(item)} labor cost in this price
    </div>` : ''}
    <label class="li-row-include${included?'':' off'}">
      <input type="checkbox" ${included?'checked':''}
        onchange="liSetIncluded('${trade}','${item.id}','${tier}',this.checked)">
      ${included?`In ${TIER_LABELS[tier]}`:`Excluded from ${TIER_LABELS[tier]}`}
    </label>
  </div>`;
}

function toggleNotes(btn) {
  const ta = btn.nextElementSibling;
  const open = ta.style.display !== 'none';
  ta.style.display = open ? 'none' : 'block';
  btn.textContent = (open ? '▸' : '▾') + ' Description / Marketing Notes';
}

/* ── Page 8: Roof Health Report ──────────────────────────────────────── */

const RH_CONDITIONS = [
  { value:'excellent', label:'Excellent', color:'#16a34a', bg:'#dcfce7' },
  { value:'good',      label:'Good',      color:'#2563eb', bg:'#dbeafe' },
  { value:'fair',      label:'Fair',      color:'#d97706', bg:'#fef3c7' },
  { value:'poor',      label:'Poor',      color:'#dc2626', bg:'#fee2e2' },
  { value:'critical',  label:'Critical',  color:'#7c3aed', bg:'#ede9fe' },
];
const RH_SEVERITIES  = [{v:'low',l:'Low',c:'#2563eb'},{v:'medium',l:'Medium',c:'#d97706'},{v:'high',l:'High',c:'#dc2626'}];
const RH_PRIORITIES  = [{v:'immediate',l:'Immediate'},{v:'soon',l:'1–2 Years'},{v:'monitor',l:'Monitor'}];
const RH_MATERIALS   = ['Asphalt Shingles','Metal','Tile','Wood/Cedar','Flat/TPO','Flat/EPDM','Other'];

// ── Property Condition Report constants ─────────────────────────────────────
const PC_GRADES = [
  {g:'A', label:'Excellent', color:'#16a34a', bg:'#dcfce7'},
  {g:'B', label:'Good',      color:'#2563eb', bg:'#dbeafe'},
  {g:'C', label:'Fair',      color:'#d97706', bg:'#fef3c7'},
  {g:'D', label:'Poor',      color:'#dc2626', bg:'#fee2e2'},
  {g:'F', label:'Critical',  color:'#7c3aed', bg:'#ede9fe'},
];
const PC_SECTIONS = [
  {key:'roof',    label:'Roofing',  icon:'🏠'},
  {key:'siding',  label:'Siding',   icon:'🏗'},
  {key:'windows', label:'Windows',  icon:'🪟'},
  {key:'gutters', label:'Gutters',  icon:'🌧'},
  {key:'other',   label:'Exterior / Other', icon:'📋'},
];
let _pcActiveSection = 'roof';

function pcBlankSection(key) {
  const s = {enabled: key==='roof', grade:'', summary:'', findings:[], recommendations:[]};
  if(key==='roof') { s.material_type=''; s.age_years=''; s.pitch=''; }
  return s;
}
function pcGet() {
  if(!S.property_condition) {
    S.property_condition = {
      property_name:'', inspection_date: S.roof_health?.inspection_date || fmtDate(new Date()),
      executive_notes:'', report_photo_ids: S.roof_health?.report_photo_ids || [],
      audience:'homeowner',   // 'homeowner' (default) | 'hoa' — flips report wording
      sections: {}
    };
    PC_SECTIONS.forEach(s => S.property_condition.sections[s.key] = pcBlankSection(s.key));
    // Migrate existing roof_health data into the roof section
    const rh = S.roof_health;
    if(rh && (rh.condition||rh.findings?.length||rh.recommendations?.length)) {
      const cond = RH_CONDITIONS.find(c=>c.value===rh.condition);
      const gradeMap = {excellent:'A',good:'B',fair:'C',poor:'D',critical:'F'};
      const rs = S.property_condition.sections.roof;
      rs.grade = gradeMap[rh.condition]||'';
      rs.summary = rh.summary||'';
      rs.material_type = rh.material_type||'';
      rs.age_years = rh.age_years||'';
      rs.pitch = rh.pitch||'';
      rs.findings = (rh.findings||[]).map(f=>({...f}));
      rs.recommendations = (rh.recommendations||[]).map(r=>({...r, cost_range: r.cost_range||''}));
      rs.enabled = true;
    }
  }
  return S.property_condition;
}
function pcGetSec(key) { const pc=pcGet(); if(!pc.sections[key])pc.sections[key]=pcBlankSection(key); return pc.sections[key]; }
function pcSet(field,val)      { pcGet()[field]=val; setDirty(); }
function pcSecSet(key,field,val){ pcGetSec(key)[field]=val; setDirty(); }
function pcAddFinding(key) { pcGetSec(key).findings.push({id:uid(),area:'',severity:'medium',description:''}); setDirty(); renderConditionPage(); }
function pcDelFinding(key,id)  { const s=pcGetSec(key); s.findings=s.findings.filter(f=>f.id!==id); setDirty(); renderConditionPage(); }
function pcSetFinding(key,id,field,val){ const f=pcGetSec(key).findings.find(x=>x.id===id); if(f){f[field]=val;setDirty();} }
function pcAddRec(key)     { pcGetSec(key).recommendations.push({id:uid(),priority:'monitor',description:'',cost_range:''}); setDirty(); renderConditionPage(); }
function pcDelRec(key,id)      { const s=pcGetSec(key); s.recommendations=s.recommendations.filter(r=>r.id!==id); setDirty(); renderConditionPage(); }
function pcSetRec(key,id,field,val){ const r=pcGetSec(key).recommendations.find(x=>x.id===id); if(r){r[field]=val;setDirty();} }
/* True when a recommendation's cost still holds a RANGE rather than one price.
   The field is a single price now, but estimates saved before that change hold
   strings like "$8,000 – $12,000" and are deliberately not rewritten — totals
   sum the low end, so those keep the honest "+". Mirrored in app.py as
   _pc_is_range(); no parity test binds the two, so change both together. */
function pcIsRange(s) {
  s = (s||'').trim();
  if((s.match(/[\d,]+(\.\d+)?/g)||[]).length > 1) return true;
  return /(?:–|—|-|\bto\b)\s*$/.test(s);   // "$500 to", "$500–" reads open-ended
}

/* ── Report-only estimates ────────────────────────────────────────────
   A rep who inspects a roof and writes up the condition report has produced a
   bid: the recommendations carry prices and they add up. Nothing else on the
   estimate does — a new estimate ships with Roofing ENABLED and empty, so the
   customer used to get a Good/Better/Best comparison of three $0 columns and a
   "Project Total $0" printed underneath a report that had just quoted $6,400 of
   repairs.

   So when no trade carries scope, the recommendations ARE the price: the G/B/B
   comparison is suppressed and the report's own recommendation tables stand as
   the scope. Mirrored in app.py as _pc_repair_lines / _pc_repair_totals /
   _is_report_only, which is what prices the estimate everywhere the server owns
   the number (the list, the funnel, the Den push, the sign page). Held to the
   same numbers by tests/report_only_runner.js. ─────────────────────── */

/* Every recommendation on the enabled, graded sections, in report order — the
   one parse behind both the report's cost summary and the estimate's price, so
   the two can never disagree. */
function pcRepairLines() {
  const pc = S.property_condition || (S.roof_health?.condition ? pcGet() : null);
  if (!pc) return [];
  const out = [];
  PC_SECTIONS.forEach(s => {
    const sec = (pc.sections || {})[s.key];
    if (!sec || !sec.enabled || !sec.grade) return;
    (sec.recommendations || []).forEach(r => {
      // Parse only the FIRST number: a single "$1,500" is the price, a legacy
      // "$500–$1,500" is its low end. Same parse the condition report totals
      // with, and the reason a legacy range keeps the '+'.
      const m = (r.cost_range || '').match(/[\d,]+(\.\d+)?/);
      out.push({
        section: s.label, icon: s.icon,
        priority: r.priority || 'monitor',
        description: (r.description || '').trim(),
        cost_range: r.cost_range || '',
        amount: m ? (parseFloat(m[0].replace(/,/g, '')) || 0) : 0,
        isRange: pcIsRange(r.cost_range),
      });
    });
  });
  return out;
}

function pcRepairTotals() {
  let immediate = 0, soon = 0, monitor = 0, anyRange = false;
  pcRepairLines().forEach(ln => {
    if (ln.amount && ln.isRange) anyRange = true;
    if (ln.priority === 'immediate')  immediate += ln.amount;
    else if (ln.priority === 'soon')  soon      += ln.amount;
    else                              monitor   += ln.amount;
  });
  return { immediate, soon, monitor, total: immediate + soon + monitor, anyRange };
}

/* Enabled is NOT the test — Roofing is enabled on every new estimate. Line
   items are. MUST mirror _has_priced_scope in app.py. */
function hasPricedScope() {
  const ins = S.trades?.insurance;
  if (ins?.enabled && ((ins.sections || []).length || (ins.line_items || []).length)) return true;
  return RETAIL_TRADE_KEYS.some(t => {
    const td = S.trades[t];
    return !!td && td.enabled && (td.line_items || []).length > 0;
  });
}

/* Two ways in, and the ORDER of the tests is the whole design:

     * The rep picked the Report estimate type, which turns every trade off so
       the empty-Roofing trap cannot happen at all. That is a starting posture,
       NOT a lock — hasPricedScope() is tested first, so the moment a rep prices
       a trade on a report estimate (inspect → report → "yes, replace it", which
       is the whole point of handing a realtor one of these) it goes back to
       being an ordinary estimate priced by its line items.
     * Or the SHAPE says so on any other type: no trade carries line items and
       the report is priced. A year of estimates predate the type.

   Both need a report the customer can actually see, and on the inferred path
   the recommendations must total above zero — an estimate that is merely empty
   keeps totalling $0 rather than inventing a price.
   MUST mirror _is_report_only in app.py. */
function isReportOnly() {
  if ((S.estimate_type || 'retail') === 'insurance') return false;
  if ((S.page_visibility || {}).report === false) return false;
  if (hasPricedScope()) return false;
  if ((S.estimate_type || 'retail') === 'report') return true;
  return pcRepairTotals().total > 0;
}

function renderConditionPage() {
  const el = document.getElementById('roof-health-content');
  if(!el) return;
  const pc = pcGet();
  const sec = pcGetSec(_pcActiveSection);
  const secMeta = PC_SECTIONS.find(s=>s.key===_pcActiveSection);

  // Section tabs
  const sectionTabs = PC_SECTIONS.map(s=>{
    const enabled = pcGetSec(s.key).enabled;
    const grade   = pcGetSec(s.key).grade;
    const ginfo   = PC_GRADES.find(g=>g.g===grade);
    return `<button class="pc-sec-tab ${s.key===_pcActiveSection?'active':''}"
      onclick="_pcActiveSection='${s.key}';renderConditionPage()">
      ${s.icon} ${s.label}
      ${enabled&&grade ? `<span class="pc-sec-grade" style="background:${ginfo?.bg};color:${ginfo?.color}">${grade}</span>` : ''}
    </button>`;
  }).join('');

  // Grade buttons
  const gradeHtml = PC_GRADES.map(g=>`
    <button class="pc-grade-btn ${sec.grade===g.g?'active':''}"
      style="--gc:${g.color};--gb:${g.bg}"
      onclick="pcSecSet('${_pcActiveSection}','grade','${g.g}');renderConditionPage()">
      <span class="pc-grade-letter">${g.g}</span>
      <span class="pc-grade-label">${g.label}</span>
    </button>`).join('');

  // Findings table
  const findingsHtml = `
    <div class="rh-section">
      <div class="rh-section-hd">
        <h3>Findings</h3>
        <button class="btn-add" onclick="pcAddFinding('${_pcActiveSection}')">+ Add Finding</button>
      </div>
      ${sec.findings.length ? `<table class="rh-table"><thead><tr>
          <th>Area / Location</th><th>Severity</th><th>Description</th><th style="width:36px"></th>
        </tr></thead><tbody>
        ${sec.findings.map(f=>`<tr>
          <td><input type="text" value="${esc(f.area||'')}" placeholder="e.g. North wall, gutters"
            onchange="pcSetFinding('${_pcActiveSection}','${f.id}','area',this.value)"></td>
          <td><select onchange="pcSetFinding('${_pcActiveSection}','${f.id}','severity',this.value)">
            ${RH_SEVERITIES.map(s=>`<option value="${s.v}" ${f.severity===s.v?'selected':''}>${s.l}</option>`).join('')}
          </select></td>
          <td><input type="text" value="${esc(f.description||'')}" placeholder="Describe the issue"
            onchange="pcSetFinding('${_pcActiveSection}','${f.id}','description',this.value)"></td>
          <td><button class="li-del" onclick="pcDelFinding('${_pcActiveSection}','${f.id}')">×</button></td>
        </tr>`).join('')}
        </tbody></table>` : '<div class="rh-empty">No findings — click + Add Finding.</div>'}
    </div>`;

  // Recommendations table
  const recsHtml = `
    <div class="rh-section">
      <div class="rh-section-hd">
        <h3>Repair Options &amp; Recommendations</h3>
        <button class="btn-add" onclick="pcAddRec('${_pcActiveSection}')">+ Add Recommendation</button>
      </div>
      ${sec.recommendations.length ? `<table class="rh-table"><thead><tr>
          <th>Priority</th><th>Recommendation</th><th>Est. Cost</th><th style="width:36px"></th>
        </tr></thead><tbody>
        ${sec.recommendations.map(r=>`<tr>
          <td><select onchange="pcSetRec('${_pcActiveSection}','${r.id}','priority',this.value)">
            ${RH_PRIORITIES.map(p=>`<option value="${p.v}" ${r.priority===p.v?'selected':''}>${p.l}</option>`).join('')}
          </select></td>
          <td><input type="text" value="${esc(r.description||'')}" placeholder="Describe the action"
            onchange="pcSetRec('${_pcActiveSection}','${r.id}','description',this.value)"></td>
          <td><input type="text" value="${esc(r.cost_range||'')}" placeholder="e.g. $1,500"
            title="One price, not a range — the report totals these exactly so it can be handed over as a bid."
            onchange="pcSetRec('${_pcActiveSection}','${r.id}','cost_range',this.value)"></td>
          <td><button class="li-del" onclick="pcDelRec('${_pcActiveSection}','${r.id}')">×</button></td>
        </tr>`).join('')}
        </tbody></table>` : '<div class="rh-empty">No recommendations — click + Add Recommendation.</div>'}
    </div>`;

  // Roof-specific meta
  const roofMeta = _pcActiveSection==='roof' ? `
    <div class="rh-meta-grid" style="margin-top:0;margin-bottom:14px">
      <div class="field-group"><label>Roof Material</label>
        <select onchange="pcSecSet('roof','material_type',this.value)">
          <option value="">Select…</option>
          ${RH_MATERIALS.map(m=>`<option ${sec.material_type===m?'selected':''}>${esc(m)}</option>`).join('')}
        </select></div>
      <div class="field-group"><label>Est. Age (years)</label>
        <input type="number" min="0" max="100" value="${sec.age_years||''}" placeholder="e.g. 12"
          onchange="pcSecSet('roof','age_years',this.value)"></div>
      <div class="field-group"><label>Pitch</label>
        <input type="text" value="${esc(sec.pitch||'')}" placeholder="e.g. 6/12"
          onchange="pcSecSet('roof','pitch',this.value)"></div>
    </div>` : '';

  // Audience-aware editor wording (report photos live on the Photos page —
  // the printed report no longer embeds its own photo grid)
  const isHoa = pc.audience === 'hoa';

  el.innerHTML = `
    <div class="pc-header">
      <div class="pc-audience-row">
        <span class="pc-audience-lbl">Report for:</span>
        <button class="pc-audience-btn ${!isHoa?'active':''}"
          onclick="pcSet('audience','homeowner');renderConditionPage()">🏠 Homeowner</button>
        <button class="pc-audience-btn ${isHoa?'active':''}"
          onclick="pcSet('audience','hoa');renderConditionPage()">🏢 HOA / Commercial</button>
      </div>
      <div class="rh-meta-grid">
        <div class="field-group"><label>${isHoa?'Property Name / HOA':'Homeowner / Property'}</label>
          <input type="text" value="${esc(pc.property_name||'')}"
            placeholder="${isHoa?'e.g. Ridgeline HOA — Building C':'leave blank to use the customer name'}"
            onchange="pcSet('property_name',this.value)"></div>
        <div class="field-group"><label>Inspection Date</label>
          <input type="date" value="${pc.inspection_date||''}" onchange="pcSet('inspection_date',this.value)"></div>
      </div>
    </div>
    <div class="pc-sec-tabs">${sectionTabs}</div>
    <div class="pc-sec-body">
      <div class="pc-sec-enable">
        <label class="checkbox-label">
          <input type="checkbox" ${sec.enabled?'checked':''}
            onchange="pcSecSet('${_pcActiveSection}','enabled',this.checked);renderConditionPage()">
          Include ${secMeta?.label||''} in this report
        </label>
      </div>
      ${sec.enabled ? `
        <div class="field-group"><label>${secMeta?.label||''} Overall Grade</label>
          <div class="pc-grade-row">${gradeHtml}</div></div>
        ${roofMeta}
        <div class="field-group"><label>Summary / Notes</label>
          <textarea rows="3" placeholder="Overall assessment of this area…"
            onchange="pcSecSet('${_pcActiveSection}','summary',this.value)">${esc(sec.summary||'')}</textarea>
        </div>
        ${findingsHtml}
        ${recsHtml}
      ` : `<div class="rh-empty" style="margin:20px 0">${secMeta?.label||''} not included. Check the box above to add it to the report.</div>`}
    </div>
    <div class="rh-section">
      <div class="field-group"><label>${isHoa?'Executive Summary':'Overall Assessment'} <span class="note-tag print">shown on report cover</span></label>
        <textarea rows="3" placeholder="${isHoa?'Overall property assessment for the board or property manager…':'Overall assessment of the home, written for the homeowner…'}"
          onchange="pcSet('executive_notes',this.value)">${esc(pc.executive_notes||'')}</textarea>
      </div>
    </div>
    <p class="pc-photos-hint">📷 Report photos now come from the <strong>Photos</strong> page — everything marked "Print" appears in the Photo Report, right before this condition report.</p>`;
}

function rhGet()   { if (!S.roof_health) S.roof_health={condition:'',age_years:'',inspection_date:'',material_type:'',pitch:'',summary:'',findings:[],recommendations:[],report_photo_ids:[]}; return S.roof_health; }
function rhSet(field, val) { rhGet()[field]=val; setDirty(); }
function rhAddFinding() { rhGet().findings.push({id:uid(),area:'',severity:'medium',description:'',photo_ids:[]}); setDirty(); renderRoofHealthPage(); }
function rhDelFinding(id){ const rh=rhGet(); rh.findings=rh.findings.filter(f=>f.id!==id); setDirty(); renderRoofHealthPage(); }
function rhSetFinding(id,field,val){ const f=rhGet().findings.find(x=>x.id===id); if(f){f[field]=val;setDirty();} }
function rhAddRec()    { rhGet().recommendations.push({id:uid(),priority:'monitor',description:'',cost_range:''}); setDirty(); renderRoofHealthPage(); }
function rhDelRec(id)  { const rh=rhGet(); rh.recommendations=rh.recommendations.filter(r=>r.id!==id); setDirty(); renderRoofHealthPage(); }
function rhSetRec(id,field,val){ const r=rhGet().recommendations.find(x=>x.id===id); if(r){r[field]=val;setDirty();} }
function rhTogglePhoto(photoId){ const rh=rhGet(); const idx=rh.report_photo_ids.indexOf(photoId); if(idx>=0)rh.report_photo_ids.splice(idx,1); else rh.report_photo_ids.push(photoId); setDirty(); renderRoofHealthPage(); }

function renderRoofHealthPage() {
  const el = document.getElementById('roof-health-content');
  if (!el) return;
  const rh = rhGet();
  const cond = RH_CONDITIONS.find(c=>c.value===rh.condition) || {};

  // ── Header / meta fields ──────────────────────────────────────────
  const metaFields = `
    <div class="rh-meta-grid">
      <div class="field-group">
        <label>Overall Condition</label>
        <div class="rh-condition-btns">
          ${RH_CONDITIONS.map(c=>`
            <button class="rh-cond-btn ${rh.condition===c.value?'active':''}"
              style="--rh-c:${c.color};--rh-bg:${c.bg}"
              onclick="rhSet('condition','${c.value}');renderRoofHealthPage()">${c.label}</button>`).join('')}
        </div>
      </div>
      <div class="field-group">
        <label>Roof Material</label>
        <select onchange="rhSet('material_type',this.value)">
          <option value="">Select…</option>
          ${RH_MATERIALS.map(m=>`<option ${rh.material_type===m?'selected':''}>${esc(m)}</option>`).join('')}
        </select>
      </div>
      <div class="field-group">
        <label>Estimated Age (years)</label>
        <input type="number" min="0" max="100" step="1" value="${rh.age_years||''}" placeholder="e.g. 12"
          onchange="rhSet('age_years',this.value)">
      </div>
      <div class="field-group">
        <label>Predominant Pitch</label>
        <input type="text" value="${esc(rh.pitch||'')}" placeholder="e.g. 6/12"
          onchange="rhSet('pitch',this.value)">
      </div>
      <div class="field-group">
        <label>Inspection Date</label>
        <input type="date" value="${rh.inspection_date||''}" onchange="rhSet('inspection_date',this.value)">
      </div>
    </div>
    <div class="field-group" style="margin-top:10px">
      <label>Summary / Overall Notes <span class="note-tag print">shown on report</span></label>
      <textarea rows="3" placeholder="Overall assessment for the customer or realtor…"
        onchange="rhSet('summary',this.value)">${esc(rh.summary||'')}</textarea>
    </div>`;

  // ── Findings ─────────────────────────────────────────────────────
  const findingsHtml = `
    <div class="rh-section">
      <div class="rh-section-hd">
        <h3>Findings</h3>
        <button class="btn-add" onclick="rhAddFinding()">+ Add Finding</button>
      </div>
      ${rh.findings.length ? `
        <table class="rh-table">
          <thead><tr>
            <th>Area / Location</th><th>Severity</th><th>Description</th><th style="width:36px"></th>
          </tr></thead>
          <tbody>
            ${rh.findings.map(f=>{
              const sc=RH_SEVERITIES.find(s=>s.v===f.severity)||{c:'#666'};
              return `<tr>
                <td><input type="text" value="${esc(f.area||'')}" placeholder="e.g. NW slope, ridge, gutters"
                  onchange="rhSetFinding('${f.id}','area',this.value)"></td>
                <td>
                  <select onchange="rhSetFinding('${f.id}','severity',this.value)">
                    ${RH_SEVERITIES.map(s=>`<option value="${s.v}" ${f.severity===s.v?'selected':''}>${s.l}</option>`).join('')}
                  </select>
                </td>
                <td><input type="text" value="${esc(f.description||'')}" placeholder="Describe the issue"
                  onchange="rhSetFinding('${f.id}','description',this.value)"></td>
                <td><button class="li-del" onclick="rhDelFinding('${f.id}')">×</button></td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>` : `<div class="rh-empty">No findings yet — click + Add Finding.</div>`}
    </div>`;

  // ── Recommendations ───────────────────────────────────────────────
  const recsHtml = `
    <div class="rh-section">
      <div class="rh-section-hd">
        <h3>Recommendations</h3>
        <button class="btn-add" onclick="rhAddRec()">+ Add Recommendation</button>
      </div>
      ${rh.recommendations.length ? `
        <table class="rh-table">
          <thead><tr>
            <th>Priority</th><th>Recommendation</th><th>Est. Cost</th><th style="width:36px"></th>
          </tr></thead>
          <tbody>
            ${rh.recommendations.map(r=>`<tr>
              <td>
                <select onchange="rhSetRec('${r.id}','priority',this.value)">
                  ${RH_PRIORITIES.map(p=>`<option value="${p.v}" ${r.priority===p.v?'selected':''}>${p.l}</option>`).join('')}
                </select>
              </td>
              <td><input type="text" value="${esc(r.description||'')}" placeholder="Describe the recommendation"
                onchange="rhSetRec('${r.id}','description',this.value)"></td>
              <td><input type="text" value="${esc(r.cost_range||'')}" placeholder="e.g. $1,500"
                onchange="rhSetRec('${r.id}','cost_range',this.value)"></td>
              <td><button class="li-del" onclick="rhDelRec('${r.id}')">×</button></td>
            </tr>`).join('')}
          </tbody>
        </table>` : `<div class="rh-empty">No recommendations yet — click + Add Recommendation.</div>`}
    </div>`;

  // ── Photo selection ───────────────────────────────────────────────
  const photosHtml = S.photos.length ? `
    <div class="rh-section">
      <div class="rh-section-hd">
        <h3>Photos for Report</h3>
        <span class="rh-photo-hint">Select photos to include in the printed health report</span>
      </div>
      <div class="rh-photo-grid">
        ${S.photos.map(p=>{
          const sel=rh.report_photo_ids.includes(p.id);
          return `<div class="rh-photo-thumb ${sel?'rh-photo-sel':''}" onclick="rhTogglePhoto('${p.id}')">
            <img src="${BASE}/uploads/${esc(p.filename)}" alt="${esc(p.caption||'')}">
            <div class="rh-photo-check">${sel?'✓':''}</div>
            ${p.caption?`<div class="rh-photo-cap">${esc(p.caption)}</div>`:''}
          </div>`;
        }).join('')}
      </div>
    </div>` : '';

  el.innerHTML = `
    <div class="rh-page">
      <div class="rh-page-header">
        <div>
          <h2>Roof Health Report</h2>
          <p class="rh-subtitle">A professional inspection summary for homeowners and realtors.</p>
        </div>
        ${cond.label ? `<div class="rh-badge" style="color:${cond.color};background:${cond.bg}">${cond.label} Condition</div>` : ''}
      </div>
      <div class="rh-body">
        ${metaFields}
        ${findingsHtml}
        ${recsHtml}
        ${photosHtml}
      </div>
    </div>`;
}

/* ── Page 9: Contract ───────────────────────────────────────────────── */

/* Once an estimate is signed, the Contract page is the first place a rep
   looks — so the executed PDF belongs here, not only three clicks away under
   Customer -> Documents. */
function renderSignedContractPanel() {
  const sig = S.signature;
  if (!sig) return '';
  const dt   = new Date(sig.signed_at);
  const when = isNaN(dt) ? '' : dt.toLocaleDateString('en-US',
    { month:'long', day:'numeric', year:'numeric' });
  const att  = signedContractAttachment();
  return `
  <div class="signed-doc-panel">
    <div class="signed-doc-head">
      <span class="signed-doc-check">✓</span>
      <div>
        <div class="signed-doc-title">Signed by ${esc(sig.name || 'customer')}</div>
        <div class="signed-doc-sub">${when ? esc(when) : ''}${
          sig.email ? ' · ' + esc(sig.email) : ''}</div>
      </div>
    </div>
    ${att
      ? `<div class="signed-doc-acts">
           <a class="btn-primary signed-doc-open" target="_blank" rel="noopener"
              href="${BASE}/uploads/${esc(att.filename)}">📄 Open signed PDF</a>
           <button class="signed-doc-alt" onclick="switchPage('documents')">
             All documents →</button>
         </div>`
      : `<div class="signed-doc-pending">The signed PDF is generating — it appears
           in Documents within a few seconds of signing.</div>`}
  </div>`;
}

function renderContractPage() {
  // Notes
  setTA('notes-internal', S.notes_internal);
  setTA('notes-customer', S.notes_customer);
  // Contract
  document.getElementById('contract-section').innerHTML =
    renderSignedContractPanel() +
    `<div class="panel-header">
      <h3>Contract Terms</h3>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        ${_meIsAdmin() ? `<button class="btn-save-defaults" onclick="saveContractDefaults()"
          title="Make this contract + initials the company default for every NEW ${_ctype(S.estimate_type)} estimate anyone creates (also editable in ⚙ Settings)">💾 Save as Company Default</button>` : ''}
        <label class="checkbox-label" style="font-size:11px">
          <input type="checkbox" ${S.print_contract!==false?'checked':''}
            onchange="S.print_contract=this.checked;setDirty()"> Print with estimate
        </label>
      </div>
    </div>
    <textarea id="contract-textarea" rows="14"
      onchange="S.contract_text=this.value;setDirty()">${esc(S.contract_text||globalContract(_ctype(S.estimate_type)))}</textarea>
    ${renderSigningRequirements()}`;
}

/* Admin: push the CURRENT estimate's contract + initials up as the company
   default for this estimate type — every new estimate seeds from it. */
async function saveContractDefaults() {
  const ct = _ctype(S.estimate_type);
  if (!confirm(`Make this contract and its customer-initial statements the company default for every NEW ${ct} estimate?\n\nExisting estimates are not changed.`)) return;
  const texts = (S.contract_initials || []).map(i => (i.text || '').trim()).filter(Boolean);
  appSettings['contract_' + ct] = (S.contract_text || '').trim();
  appSettings['initials_' + ct] = texts;
  try {
    const r = await fetch('/api/settings', {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(appSettings),
    });
    if (!r.ok) throw new Error('Server error');
    toast('✓ Saved — every new ' + (ins ? 'insurance' : 'retail') + ' estimate will use this contract');
  } catch (e) { alert('Could not save the company default: ' + e.message); }
}

function renderSigningRequirements() {
  const ss  = S.shingle_selection || {enabled:true, options:[], chosen:''};
  const sds = S.siding_selection  || {enabled:false, options:[], chosen:''};
  const initials = S.contract_initials || [];
  const ssOptText  = (ss.options  || []).join(', ');
  const sdsOptText = (sds.options || []).join(', ');
  // Only show the siding block when siding is actually on the estimate —
  // otherwise it's noise on a roof-only job.
  const sidingOn = !!(S.trades && S.trades.siding && S.trades.siding.enabled);

  const initialRows = initials.map((it, idx) => `
    <div class="sr-initial-row">
      <span class="sr-initial-num">${idx + 1}</span>
      <input type="text" class="sr-initial-input" value="${esc(it.text)}"
        placeholder="Statement the customer must initial…"
        onchange="setInitialText('${it.id}', this.value)">
      <button class="sr-initial-del" onclick="deleteInitial('${it.id}')" title="Remove">×</button>
    </div>`).join('');

  const sidingBlock = !sidingOn ? '' : `
    <div class="sr-block">
      <label class="sr-toggle">
        <input type="checkbox" ${sds.enabled ? 'checked' : ''}
          onchange="setSidingEnabled(this.checked)">
        <span>Ask the customer to confirm a <strong>siding color</strong> at signing</span>
      </label>
      <div class="sr-siding-body" style="${sds.enabled ? '' : 'display:none'}">
        <div class="field-group">
          <label>Color already chosen? <span class="sr-hint">leave blank to let the customer pick</span></label>
          <input type="text" list="siding-color-list" class="sr-chosen-input"
            value="${esc(sds.chosen || '')}" placeholder="e.g. Iron Gray — or leave blank"
            onchange="setSidingChosen(this.value)">
          <datalist id="siding-color-list">
            ${(sds.options || []).map(o => `<option value="${esc(o)}">`).join('')}
          </datalist>
        </div>
        <div class="field-group">
          <label>Extra siding color options
            <span class="sr-hint">optional — leave blank to use the picked bundle's colors</span></label>
          <textarea class="sr-options-input" rows="2"
            onchange="setSidingOptions(this.value)"
            placeholder="Arctic White, Iron Gray, Musket Brown…">${esc(sdsOptText)}</textarea>
        </div>
      </div>
    </div>`;

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
          <label>Extra shingle color options
            <span class="sr-hint">optional — leave blank to use the picked bundle's colors (IKO Nordic → IKO, CertainTeed → CertainTeed…)</span></label>
          <textarea class="sr-options-input" rows="2"
            onchange="setShingleOptions(this.value)"
            placeholder="Charcoal, Weathered Wood, Driftwood…">${esc(ssOptText)}</textarea>
        </div>
      </div>
    </div>

    ${sidingBlock}

    <div class="sr-block">
      <div class="sr-block-title">Items the customer must <strong>initial</strong></div>
      <p class="sr-hint" style="margin:0 0 8px">Each line gets its own initial box on the sign page, on top of the full signature.</p>
      <div class="sr-initials">${initialRows || '<div class="sr-empty">No initial items — the customer will just sign &amp; agree.</div>'}</div>
      <button class="btn-add" onclick="addInitial()">+ Add initial item</button>
    </div>
  </div>`;
}

function setShingleEnabled(v) {
  if (!S.shingle_selection) S.shingle_selection = {options:[], chosen:''};
  S.shingle_selection.enabled = v; setDirty();
  renderContractPage();
}
function setShingleChosen(v) {
  if (!S.shingle_selection) S.shingle_selection = {enabled:true, options:[]};
  S.shingle_selection.chosen = v.trim();
  // Keep roofing's color field in sync so it shows on prints
  if (S.trades.roofing) { S.trades.roofing.colors = S.trades.roofing.colors || {}; S.trades.roofing.colors.shingle_color = v.trim(); }
  setDirty();
}
function setShingleOptions(v) {
  if (!S.shingle_selection) S.shingle_selection = {enabled:true, chosen:''};
  // Empty = use bundle colors (IKO/CertainTeed/…); non-empty = rep override
  S.shingle_selection.options = v.split(',').map(s => s.trim()).filter(Boolean);
  setDirty();
}
function setSidingEnabled(v) {
  if (!S.siding_selection) S.siding_selection = {options:[], chosen:''};
  S.siding_selection.enabled = v; setDirty();
  renderContractPage();
}
function setSidingChosen(v) {
  if (!S.siding_selection) S.siding_selection = {enabled:true, options:[]};
  S.siding_selection.chosen = v.trim();
  if (S.trades.siding) { S.trades.siding.colors = S.trades.siding.colors || {}; S.trades.siding.colors.siding_color = v.trim(); }
  setDirty();
}
function setSidingOptions(v) {
  if (!S.siding_selection) S.siding_selection = {enabled:true, chosen:''};
  S.siding_selection.options = v.split(',').map(s => s.trim()).filter(Boolean);
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
  grid.innerHTML = S.photos.map((p, idx) => {
    const hasAnns = p.annotations && p.annotations.length > 0;
    return `
    <div class="photo-thumb photo-report-thumb ${p.id===S.cover_photo_id?'is-cover':''}">
      <div class="photo-img-wrap">
        ${hasAnns
          ? `<canvas class="photo-ann-canvas" id="ann-ph-${p.id}" data-src="${BASE}/uploads/${esc(p.filename)}" data-id="${p.id}"></canvas>`
          : `<img src="${BASE}/uploads/${esc(p.filename)}" alt="${esc(p.caption)}">`}
      </div>
      <div class="photo-report-controls">
        <input class="photo-caption" type="text" value="${esc(p.caption)}"
          placeholder="Caption / note…" onchange="photoCaption('${p.id}',this.value)">
        <div class="photo-report-btns">
          <button class="li-move-btn" onclick="photoMove('${p.id}',-1)" ${idx===0?'disabled':''} title="Move earlier in the report">↑</button>
          <button class="li-move-btn" onclick="photoMove('${p.id}',1)" ${idx===S.photos.length-1?'disabled':''} title="Move later in the report">↓</button>
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
      if (canvas) drawAnnotatedPhoto(canvas, BASE + '/uploads/' + p.filename, p.annotations);
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
  img.src = BASE + '/uploads/' + photo.filename;
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

  } else if (ann.type === 'line') {
    // Straight highlighter stroke — used to mark ridge segments to cut in.
    const x1 = ann.x1/100*W, y1 = ann.y1/100*H;
    const x2 = ann.x2/100*W, y2 = ann.y2/100*H;
    ctx.globalAlpha = ann.hl === false ? 1 : 0.5;
    ctx.lineWidth   = sw * (ann.hl === false ? 1 : 2.2);
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();

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
  return BASE + '/uploads/' + photo.filename;
}

/* ── Ventilation cut-in map editor ──────────────────────────────────────
   Rep marks WHERE the ridge vent gets cut in for ventilation, on the RoofR
   roof diagram (already uploaded + rasterized as attachment page images). We
   run ridge vent the full ridge for looks, but only cut the code footage —
   this map + the cut-in LF ride the crew's work order. Reuses the pure drawAnn
   renderer; strokes persist on S.vent_cutin (re-editable) and a flattened JPG
   is uploaded for the production packet. */
const vc = {
  pages: [], pageIdx: 0, img: null, canvas: null, ctx: null,
  tool: 'line', color: '#dc2626', sw: 6,
  annotations: [], drawing: false, sx: 0, sy: 0, preview: null, history: [],
};
// The RoofR report's rasterized page images, in page order (or [] if none).
function _roofrPageImages() {
  const atts = S.attachments || [];
  const roofr = atts.find(a => /roofr/i.test(a.label || '') && (a.pages || []).length)
             || atts.find(a => /\.pdf$/i.test(a.filename || '') && (a.pages || []).length);
  return (roofr && roofr.pages) || [];
}
function _ventCutinLF() {
  const m = S.measurements || {};
  const vent = atticVentilation(m);
  const ridgeLF = mnum(m.ridge_lf);
  const raw = Math.ceil(vent.ridge_lf_required);
  return ridgeLF > 0 ? Math.min(raw, ridgeLF) : raw;
}
function openVentCutinEditor() {
  const pages = _roofrPageImages();
  if (!pages.length) {
    alert('Import the RoofR PDF first so there is a roof diagram to mark up.');
    return;
  }
  vc.pages = pages;
  const saved = S.vent_cutin || {};
  vc.annotations = (saved.strokes || []).map(a => Object.assign({}, a));
  vc.history = []; vc.drawing = false; vc.preview = null;
  // Reopen on the saved page, else default to page 2 (the overview) when present.
  let idx = saved.source_page ? pages.indexOf(saved.source_page) : -1;
  if (idx < 0) idx = pages.length > 1 ? 1 : 0;
  vc.pageIdx = idx;
  setVentTool(vc.tool); setVentColor(vc.color);
  document.getElementById('vent-cutin-modal').classList.remove('hidden');
  _ventLoadPage();
}
function closeVentCutinEditor() {
  document.getElementById('vent-cutin-modal').classList.add('hidden');
}
function maybeCloseVentCutin(e) {
  if (e.target === document.getElementById('vent-cutin-modal')) closeVentCutinEditor();
}
function _ventLoadPage() {
  const label = document.getElementById('vent-cutin-page-label');
  if (label) label.textContent = `Page ${vc.pageIdx + 1} / ${vc.pages.length}`;
  const canvas = document.getElementById('vent-cutin-canvas');
  const img = new Image();
  img.onload = () => {
    vc.img = img;
    const wrap  = document.getElementById('vent-cutin-canvas-wrap');
    const maxW  = (wrap.clientWidth || 800) - 4;
    const maxH  = window.innerHeight * 0.60;
    const scale = Math.min(maxW / img.naturalWidth, maxH / img.naturalHeight, 1);
    canvas.width  = Math.round(img.naturalWidth  * scale);
    canvas.height = Math.round(img.naturalHeight * scale);
    _ventBindCanvas(canvas);
    _ventRedraw();
  };
  img.src = BASE + '/uploads/' + vc.pages[vc.pageIdx];
}
function ventCutinPage(delta) {
  const next = vc.pageIdx + delta;
  if (next < 0 || next >= vc.pages.length) return;
  vc.pageIdx = next;
  _ventLoadPage();
}
function setVentTool(t) {
  vc.tool = t;
  document.querySelectorAll('#vent-cutin-modal .ann-tool-btn').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('vent-tool-' + t);
  if (btn) btn.classList.add('active');
  if (vc.canvas) vc.canvas.style.cursor = t === 'text' ? 'text' : 'crosshair';
}
function setVentColor(c) {
  vc.color = c;
  document.querySelectorAll('#vent-cutin-modal .ann-color-btn').forEach(b => b.classList.remove('active'));
  const btn = document.querySelector(`#vent-cutin-modal .ann-color-btn[data-color="${c}"]`);
  if (btn) btn.classList.add('active');
}
function _ventRedraw() {
  const { canvas, ctx, img, annotations, preview } = vc;
  if (!canvas || !ctx || !img) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  annotations.forEach(a => drawAnn(ctx, a, canvas.width, canvas.height));
  if (preview) drawAnn(ctx, preview, canvas.width, canvas.height);
}
function _ventBindCanvas(canvas) {
  const fresh = canvas.cloneNode(true);
  canvas.parentNode.replaceChild(fresh, canvas);
  vc.canvas = fresh;
  vc.ctx = fresh.getContext('2d');
  canvas = fresh;
  const pct = (e) => {
    const r = canvas.getBoundingClientRect();
    const src = e.touches ? e.touches[0] : e;
    return {
      x: Math.max(0, Math.min(100, (src.clientX - r.left) / r.width  * 100)),
      y: Math.max(0, Math.min(100, (src.clientY - r.top)  / r.height * 100)),
    };
  };
  const onStart = (e) => {
    if (vc.tool === 'text') {
      const { x, y } = pct(e);
      const txt = prompt('Label text:', `~${_ventCutinLF()} LF cut-in`);
      if (txt && txt.trim()) {
        vc.history.push(vc.annotations.map(a => Object.assign({}, a)));
        vc.annotations.push({ id: 'vc_' + Date.now().toString(36), type: 'text',
                              color: vc.color, sw: 4, x, y, text: txt.trim() });
        _ventRedraw();
      }
      return;
    }
    e.preventDefault();
    const { x, y } = pct(e);
    vc.sx = x; vc.sy = y; vc.drawing = true;
  };
  const onMove = (e) => {
    if (!vc.drawing) return;
    e.preventDefault();
    const { x, y } = pct(e);
    vc.preview = { id: '__preview', type: vc.tool, color: vc.color, sw: vc.sw,
                   hl: vc.tool === 'line', x1: vc.sx, y1: vc.sy, x2: x, y2: y };
    _ventRedraw();
  };
  const onEnd = () => {
    if (!vc.drawing) return;
    vc.drawing = false;
    const p = vc.preview;
    if (p) {
      const dist = Math.hypot((p.x2 - p.x1) / 100 * canvas.width,
                              (p.y2 - p.y1) / 100 * canvas.height);
      if (dist > 4) {
        vc.history.push(vc.annotations.map(a => Object.assign({}, a)));
        vc.annotations.push(Object.assign({}, p, { id: 'vc_' + Date.now().toString(36) }));
      }
    }
    vc.preview = null;
    _ventRedraw();
  };
  canvas.addEventListener('mousedown',  onStart);
  canvas.addEventListener('mousemove',  onMove);
  canvas.addEventListener('mouseup',    onEnd);
  canvas.addEventListener('mouseleave', onEnd);
  canvas.addEventListener('touchstart', onStart, { passive: false });
  canvas.addEventListener('touchmove',  onMove,  { passive: false });
  canvas.addEventListener('touchend',   onEnd);
}
function ventCutinUndo() {
  if (!vc.history.length) return;
  vc.annotations = vc.history.pop();
  vc.preview = null;
  _ventRedraw();
}
function ventCutinClear() {
  if (!vc.annotations.length) return;
  vc.history.push(vc.annotations.map(a => Object.assign({}, a)));
  vc.annotations = [];
  _ventRedraw();
}
function ventCutinStampLF() {
  vc.history.push(vc.annotations.map(a => Object.assign({}, a)));
  vc.annotations.push({ id: 'vc_' + Date.now().toString(36), type: 'text',
                        color: vc.color, sw: 5, x: 4, y: 5,
                        text: `Cut in ~${_ventCutinLF()} LF ridge vent` });
  _ventRedraw();
}
async function saveVentCutin() {
  if (!vc.canvas) { closeVentCutinEditor(); return; }
  // Need a saved estimate to hang the upload + attachment on.
  if (!S.estimate_id) { await saveEstimate(); }
  if (!S.estimate_id) { alert('Save the estimate first, then mark the cut-in map.'); return; }
  const prev = (S.vent_cutin || {}).image_filename;
  const blob = await new Promise(res => vc.canvas.toBlob(res, 'image/jpeg', 0.9));
  let image_filename = prev;
  if (blob) {
    const fd = new FormData();
    fd.append('file', new File([blob], 'vent-cutin.jpg', { type: 'image/jpeg' }));
    try {
      const r = await fetch(`/api/uploads/${S.estimate_id}`, { method: 'POST', body: fd });
      if (r.ok) {
        image_filename = (await r.json()).filename;
        // Clean up the superseded map image so uploads don't accumulate.
        if (prev && prev !== image_filename) {
          const parts = prev.split('/');
          if (parts.length === 2) { try { await fetch(`/api/uploads/${parts[0]}/${parts[1]}`, { method: 'DELETE' }); } catch {} }
        }
      }
    } catch { /* keep strokes even if the image upload fails */ }
  }
  S.vent_cutin = {
    source_page: vc.pages[vc.pageIdx],
    strokes: vc.annotations.map(a => Object.assign({}, a)),
    cutin_lf: _ventCutinLF(),
    notes: (S.vent_cutin || {}).notes || '',
    image_filename,
  };
  setDirty();
  await saveEstimate();
  closeVentCutinEditor();
  if (activePage === 'scope') renderScopePage();
}

function setTA(id, v) { const el = document.getElementById(id); if (el && el.value !== (v||'')) el.value = v||''; }

/* ── Line item mutations ────────────────────────────────────────────── */

function findItem(trade, id) { return (S.trades[trade] && S.trades[trade].line_items || []).find(i=>i.id===id); }

function liSetName(trade, id, v) { const i=findItem(trade,id); if(!i)return; i.name=v; setDirty(); }
function liSetQty(trade, id, v) {
  const i=findItem(trade,id); if(!i)return;
  if (i.tiers) Object.values(i.tiers).forEach(t => { delete t.price_override; });
  i.quantity=parseFloat(v)||0;
  setDirty(); rerender();
}
function liSetUnit(trade, id, v) {
  const i=findItem(trade,id); if(!i)return;
  i.unit=v; setDirty();
  // unit is shared and now shown in every tier column — refresh the siblings
  if(activePage==='pricing')renderTradeContent();
}
function liSetTier(trade, id, tier, field, v) {
  const i=findItem(trade,id); if(!i)return;
  if(!i.tiers[tier]) i.tiers[tier]={material_unit_cost:0,labor_unit_cost:0,description:'',notes:''};
  i.tiers[tier][field]=v;
  // Hand-editing a variant-backed field (cost or label) means the rep is quoting a
  // one-off, so drop the variant selection — the picker then reads "Custom…".
  if ((field==='material_unit_cost' || field==='description') &&
      Array.isArray(i.tiers[tier].variants) && i.tiers[tier].variants.length) {
    i.tiers[tier].selected_variant = -1;
  }
  setDirty(); rerender();
  if(activePage==='pricing'){renderTradeContent();}
}
// Pick a product variant for one tier of a line item (any trade). Copies the
// variant's cost + label (+ notes) into the tier so all pricing/render code —
// which reads material_unit_cost/description — works unchanged. idx of -1 (or an
// out-of-range value) means "Custom", leaving the current cost/label in place.
function liSetVariant(trade, id, tier, idx) {
  const item = findItem(trade, id);
  if (!item || !item.tiers || !item.tiers[tier]) return;
  const t  = item.tiers[tier];
  const vi = parseInt(idx, 10);
  if (isNaN(vi) || vi < 0 || !Array.isArray(t.variants) || !t.variants[vi]) {
    t.selected_variant = -1;  // Custom — keep whatever cost/label is set now
  } else {
    const v = t.variants[vi];
    t.selected_variant   = vi;
    t.material_unit_cost = parseFloat(v.cost) || 0;
    t.description        = v.label || '';
    if (v.notes !== undefined) t.notes = v.notes;
    delete t.price_override;  // re-price to the new product's cost
    // Pull the product's "what's included" list onto this tier's Options-tab
    // package bullets — so choosing e.g. a metal system swaps the whole package
    // story, not just the line cost. Only when the product defines features, so
    // picking a product with none leaves the rep's existing bullets alone.
    if (Array.isArray(v.features) && v.features.length) {
      tradeTierContent(trade).features[tier] = v.features.slice();
    }
    // Same rule for the tagline: a variant's `description` becomes this tier's
    // Options-tab tagline. Empty description leaves the rep's tagline alone.
    const vd = typeof v.description === 'string' ? v.description.trim() : '';
    if (vd) tradeTierContent(trade).descriptions[tier] = vd;
  }
  setDirty(); rerender();
  if (activePage === 'pricing') renderTradeContent();
  if (activePage === 'options') renderOptionsPage();
  renderTotals();
}
function liDelete(trade, id) {
  S.trades[trade].line_items=S.trades[trade].line_items.filter(i=>i.id!==id);
  setDirty(); rerender();
  if(activePage==='pricing'){renderTradeContent();}
  if(activePage==='scope'){renderScopePage();}
}
// Reorder a line item within its trade — the order flows to the customer
// view, the printed estimate, and the production packet as-is. Moves stay
// inside the item's own section: the swap partner is the nearest item in
// the same display group, skipping items of other sections in the array.
function liMove(trade, id, dir) {
  const items = S.trades[trade].line_items;
  const i = items.findIndex(x => x.id === id);
  if (i < 0) return;
  const known = new Set(tradeSections(trade));
  const groupOf = it => known.has(itemSection(it)) ? itemSection(it) : '';
  let j = i + dir;
  while (j >= 0 && j < items.length && groupOf(items[j]) !== groupOf(items[i])) j += dir;
  if (j < 0 || j >= items.length) return;
  [items[i], items[j]] = [items[j], items[i]];
  setDirty(); renderTotals();
  if (activePage === 'pricing') renderTradeContent();
  if (activePage === 'scope') renderScopePage();
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
  if (on) item._showZero = true;  // re-included: keep it visible even at 0 qty
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
  renderTotals();
}
function liSetPriceOverride(trade, id, tier, value) {
  const item = findItem(trade, id);
  if (!item || !item.tiers || !item.tiers[tier]) return;
  const v = parseFloat(value);
  if (!value || isNaN(v)) {
    delete item.tiers[tier].price_override;
  } else {
    const calc = lineTotal(item.quantity, item.tiers[tier].material_unit_cost || 0,
                           item.tiers[tier].labor_unit_cost || 0, trade, tier);
    if (Math.abs(v - calc) < 0.01) {
      delete item.tiers[tier].price_override;
    } else {
      item.tiers[tier].price_override = v;
    }
  }
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
  renderTotals();
}
function liClearPriceOverride(trade, id, tier) {
  const item = findItem(trade, id);
  if (!item || !item.tiers || !item.tiers[tier]) return;
  delete item.tiers[tier].price_override;
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
  renderTotals();
}
function includeAllInTier(trade, tier) {
  const items = S.trades[trade]?.line_items || [];
  items.forEach(item => {
    if (!item.tiers) item.tiers = {};
    if (!item.tiers[tier]) item.tiers[tier] = {material_unit_cost:0, labor_unit_cost:0, description:'', notes:''};
    item.tiers[tier].included = true;
  });
  setDirty();
  if (activePage === 'pricing') renderTradeContent();
  renderTotals();
}
// Reveal a 0-qty item that's hidden from the pricing columns so a quantity can
// be entered. Transient UI flag — not part of the priced estimate.
function liRevealZero(trade, id) {
  const item = findItem(trade, id);
  if (!item) return;
  item._showZero = true;
  if (activePage === 'pricing') renderTradeContent();
}
// `tier` is the column the + Add Item button was clicked in (bundle trades pass
// it; everything else adds to all three tiers as before).
function addLineItem(trade, tier) {
  const td = S.trades[trade];
  // Other/Misc rows are hand-entered one-offs — an allowance, a haul-away, a
  // repair charge — and there is no measurement to auto-fill their quantity,
  // so qty 0 meant a rep typed a name and a sell price, saw the number in the
  // box, and shipped an estimate that priced it at $0 and never printed it
  // (tradeTotal drops zero-qty lines even with a locked price). The qty box
  // already showed a "1" placeholder; start there for real. Every other trade
  // keeps 0 — its quantity comes from the measurements.
  const item = {
    id:uid(), name:'', unit:'EA', quantity: trade==='other' ? 1 : 0, scope_note:'',
    customer_visible: true, _showZero: true,
    tiers:{
      good:  {material_unit_cost:0,labor_unit_cost:0,description:'',notes:''},
      better:{material_unit_cost:0,labor_unit_cost:0,description:'',notes:''},
      best:  {material_unit_cost:0,labor_unit_cost:0,description:'',notes:''},
    }
  };
  // Added from inside a Custom tier: that package is being built by hand, so the
  // row belongs to THAT tier only. Otherwise every blank line typed into a
  // custom Good also lands in the Better and Best bundles beside it.
  if (tier && isBundleTrade(trade) && ((td.tier_bundles || {})[tier] === '__custom__')) {
    TIERS.forEach(t => { item.tiers[t].included = (t === tier); });
  }
  td.line_items.push(item);
  setDirty(); rerender();
  if(activePage==='pricing'){renderTradeContent();}
}
function toggleTrade(trade, enabled) {
  S.trades[trade].enabled=enabled;
  // Shingle color is a roofing question — follow the roofing toggle so a
  // siding/windows-only job never asks the customer for a shingle color.
  // (The server hides/skips the step too whenever roofing is off.)
  if (trade === 'roofing' && S.shingle_selection) S.shingle_selection.enabled = enabled;
  // Auto-build priced defaults when enabling an empty trade, so measurements
  // flow straight through to a priced estimate with no extra clicks.
  if (enabled && (!S.trades[trade].line_items || S.trades[trade].line_items.length === 0)) {
    if (isBundleTrade(trade)) {
      buildBundleDefaults(trade);   // bundle-driven (roofing, siding); applies measurements itself
    } else if (templates) {
      S.trades[trade].line_items = buildTradeDefaults(trade);
      applyMeasurements();
    }
  }
  // A product newly added in G/B/B mode picks up its global package content
  if (enabled && tradeGbbMode(trade)) seedTradeFromDefaults(trade);
  _syncLegacyTier();
  setDirty(); rerender();
  if(activePage==='pricing'){renderTabBar();renderTradeContent();}
  if(activePage==='scope'){renderScopePage();}
}
// Build a trade's line items from the price book / templates (synchronous —
// relies on the global `templates` cache that loads at boot). Pulls per-tier
// products + costs (GBB) or the price (Simple) so the estimate is priced the
// moment items are created.
function buildTradeDefaults(trade) {
  const tpl = ((templates && templates[trade]) || []).filter(t => t.is_default !== false);
  const effectiveMode = effectiveTradeMode(trade, S.trades[trade]);
  if (effectiveMode === 'simple') {
    // Simple mode = the Good tier: pull Good's cost + product so it matches
    // GBB's Good package and the "default" catalog for one-click realtor jobs.
    return tpl.filter(t => t.in_good !== false).map(t => {
      const baseCost = t.cost !== undefined ? parseFloat(t.cost)||0 : 0;
      const price = t.cost_good !== undefined ? parseFloat(t.cost_good)||0 : baseCost;
      const r = tradeRate(trade);
      const unitPrice = S.pricing.mode === 'markup'
        ? Math.round(price * (1 + r / 100) * 100) / 100
        : (r >= 100 ? 0 : Math.round(price / (1 - r / 100) * 100) / 100);
      return {
        id:uid(), name:t.name, unit:t.unit, quantity:0,
        description:(t.product_good || t.desc_good || ''),
        unit_cost: price,
        unit_price: unitPrice,
        measure: t.measure || undefined,
        formula: t.formula || undefined,
        bundle_lf: t.bundle_lf || undefined,
        bundle_unit: t.bundle_unit || undefined,
        customer_visible: t.customer_visible !== false,
      };
    });
  }
  return tpl.map(t => buildItemFromTemplate(trade, t));
}
// Build one GBB-tiered estimate line item from a price-book/template row.
// Shared by buildTradeDefaults and the ventilation-panel injector so an
// on-demand vent item is priced identically to a Load-Defaults item.
function buildItemFromTemplate(trade, t) {
  const baseCost = t.cost !== undefined ? parseFloat(t.cost)||0 : 0;
  // Per-tier cost inherits UPWARD when unset: good→base, better→good, best→better.
  // This stops Better/Best from silently dropping to the base cost (lower than Good)
  // when only the Good tier has an explicit override.
  const costGood   = t.cost_good   !== undefined ? parseFloat(t.cost_good)||0   : baseCost;
  const costBetter = t.cost_better !== undefined ? parseFloat(t.cost_better)||0 : costGood;
  const costBest   = t.cost_best   !== undefined ? parseFloat(t.cost_best)||0   : costBetter;
  const descGood   = t.product_good   ? t.product_good   : (t.desc_good   || '');
  const descBetter = t.product_better ? t.product_better : (t.desc_better || '');
  const descBest   = t.product_best   ? t.product_best   : (t.desc_best   || '');
  const tiers = {
    good:  {material_unit_cost:costGood,   labor_unit_cost:0, description:descGood,   notes:t.notes_good||'',   included: t.in_good   !== false},
    better:{material_unit_cost:costBetter, labor_unit_cost:0, description:descBetter, notes:t.notes_better||'', included: t.in_better !== false},
    best:  {material_unit_cost:costBest,   labor_unit_cost:0, description:descBest,   notes:t.notes_best||'',   included: t.in_best   !== false},
  };
  applyTierVariants(trade, t, tiers);
  return {
    id:uid(), name:t.name, unit:t.unit, quantity:0, scope_note:'',
    measure: t.measure || undefined,
    formula: t.formula || undefined,
    bundle_lf: t.bundle_lf || undefined,
    bundle_unit: t.bundle_unit || undefined,
    customer_visible: t.customer_visible !== false,
    tiers
  };
}
// Look up a served template row for a trade by exact product name.
function findTemplate(trade, name) {
  return ((templates && templates[trade]) || []).find(t => t.name === name);
}
// Attach a tier's product-variant menu (from a price-book row's
// variants_<tier>) onto a freshly built estimate tiers object, seeding
// cost/description/notes from variant 0. No-op for tiers with no menu,
// so all other pricing is untouched.
function applyTierVariants(trade, t, tiers) {
  if (!tiers) return tiers;
  TIERS.forEach(tier => {
    const menu = t['variants_' + tier];
    if (!Array.isArray(menu) || !menu.length) return;
    const variants = menu.map(v => ({
      label: v.label || '', cost: parseFloat(v.cost)||0, notes: v.notes || '',
      description: v.description || '',
      features: Array.isArray(v.features) ? v.features.slice() : []
    }));
    const cell = tiers[tier] || (tiers[tier] = {material_unit_cost:0,labor_unit_cost:0,description:'',notes:'',included:true});
    cell.variants         = variants;
    cell.selected_variant = 0;
    cell.material_unit_cost = variants[0].cost;
    cell.description        = variants[0].label;
    if (variants[0].notes) cell.notes = variants[0].notes;
  });
  return tiers;
}
async function loadDefaults(trade) {
  if(S.trades[trade].line_items.length>0){
    if(!confirm(`Replace existing ${TRADE_LABELS[trade]} items with defaults?`))return;
  }
  S.trades[trade].enabled=true;
  // Roofing + siding build from their default bundles (catalog+bundles model);
  // other trades build the flat/tiered template item list.
  if (isBundleTrade(trade)) {
    buildBundleDefaults(trade);
  } else {
    if(!templates){
      try{const r=await fetch('/api/templates');templates=await r.json();}
      catch{alert('Failed to load templates');return;}
    }
    S.trades[trade].line_items = buildTradeDefaults(trade);
    applyMeasurements();
  }
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
  bind('cust-name', v => {
    if (S.share_token && S.customer.name && S.customer.name.trim() !== v.trim()) {
      const action = S.signature ? 'SIGNED' : 'SENT';
      if (!confirm(
        `⚠ This estimate has already been ${action} to "${S.customer.name}".\n\n` +
        `Changing the name to "${v}" will affect what the customer sees at their link.\n\n` +
        `Use New Estimate to start a fresh estimate for a different customer.`
      )) {
        setVal('cust-name', S.customer.name); return; // revert the input
      }
    }
    S.customer.name = v; rerender(); renderCoverPage();
  }, 'change');
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
  document.getElementById('crm-search').addEventListener('input',
    debounce(e=>crmSearch(e.target.value.trim()),300));
  const dz=document.getElementById('photo-drop-zone');
  const pi=document.getElementById('photo-input');
  dz.addEventListener('click',     ()=>pi.click());
  dz.addEventListener('dragover',  e=>{e.preventDefault();dz.classList.add('drag-over');});
  dz.addEventListener('dragleave', ()=>dz.classList.remove('drag-over'));
  dz.addEventListener('drop', e=>{e.preventDefault();dz.classList.remove('drag-over');uploadPhotos(e.dataTransfer.files);});
  pi.addEventListener('change', ()=>uploadPhotos(pi.files));
  // In-app camera capture (mobile opens the camera directly)
  const cam = document.getElementById('photo-camera-input');
  if (cam) cam.addEventListener('change', async () => { await uploadPhotos(cam.files); cam.value=''; });
  // Cover-photo direct upload
  const ci = document.getElementById('cover-photo-input');
  if (ci) ci.addEventListener('change', () => { uploadAsCoverPhoto(ci.files); });
}

function bind(id, setter, event='change', extra=null) {
  const el=document.getElementById(id); if(!el)return;
  el.addEventListener(event, e=>{setter(e.target.value);setDirty();if(extra)extra();});
}
function renderTierRates() {
  const el = document.getElementById('tier-rates');
  if (!el) return;
  if (!S.pricing.tier_rates) S.pricing.tier_rates = { good:35, better:35, best:35 };
  const tr  = S.pricing.tier_rates;
  const lbl = S.pricing.mode === 'markup' ? 'Markup' : 'Margin';
  el.innerHTML = `<div class="tier-rates-hint">Default ${lbl.toLowerCase()} per package — each trade's tab can override it.</div>` +
    TIERS.map(t => `
    <div class="tier-rate-row tier-rate-${t}">
      <label>${TIER_LABELS[t]} <span class="tier-rate-kind">${lbl}</span></label>
      <div class="tier-rate-input-wrap">
        <input type="number" min="0" max="99" step="0.5" value="${tr[t] ?? ''}"
          onchange="setTierRate('${t}', this.value)">
        <span class="tier-rate-pct">%</span>
      </div>
    </div>`).join('');
}
function setTierRate(tier, v) {
  if (!S.pricing.tier_rates) S.pricing.tier_rates = { good:35, better:35, best:35 };
  S.pricing.tier_rates[tier] = parseFloat(v) || 0;
  recalcSimpleItems();
  setDirty();
  renderTierRates();   // keep the sidebar inputs in sync with the grid inputs
  rerender();
  if (activePage === 'pricing') renderTradeContent();
}
// Simple-mode items store an explicit sell price derived from cost + margin;
// re-derive it whenever the applicable rate changes.
function recalcSimpleItems() {
  RETAIL_TRADE_KEYS.forEach(trade => {
    const td = S.trades[trade];
    if (!td?.enabled) return;
    if ((effectiveTradeMode(trade, td)) !== 'simple') return;
    (td.line_items || []).forEach(item => simpleApplyMargin(trade, item));
  });
}
function setPricingMode(mode) {
  S.pricing.mode=mode;
  recalcSimpleItems();
  setDirty(); renderPricingModeUI(); renderTierRates(); rerender();
  if(activePage==='pricing')renderTradeContent();
}
// Sidebar tier buttons act as a "set everything" switch: the global default
// AND every G/B/B product follow. Per-product picks live on the Options tab.
function setTier(tier) {
  S.selected_tier=tier;
  gbbTrades().forEach(tr => { S.trades[tr].selected_tier = tier; });
  setDirty(); renderTierButtons(); rerender();
  if(activePage==='pricing')renderTradeContent();
  if(activePage==='options')renderOptionsPage();
}
function setTradeOverride(trade,v) { S.pricing.per_trade_overrides[trade]=v===''?null:parseFloat(v); setDirty(); rerender(); if(activePage==='pricing')renderTradeContent(); }
// Per-trade, per-tier margin. tier is 'good'|'better'|'best' (GBB tabs) or
// 'simple' (a simple-mode trade's single margin). Blank clears the override so
// the trade falls back to the sidebar default.
function setTradeTierRate(trade, tier, v) {
  if (!S.pricing.trade_rates) S.pricing.trade_rates = {};
  if (!S.pricing.trade_rates[trade]) S.pricing.trade_rates[trade] = {};
  const n = parseFloat(v);
  S.pricing.trade_rates[trade][tier] = (v === '' || v === null || isNaN(n)) ? null : n;
  if (tier === 'simple') recalcSimpleItems();  // re-bake simple sell prices
  setDirty();
  rerender();
  if (activePage === 'pricing') renderTradeContent();
}

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
  // Guard: if a customer link is already sent/signed, confirm before overwriting
  const currentName = (S.customer.name||'').trim();
  const newName = (p.client_name||p.name||'').trim();
  if (S.share_token && currentName && currentName.toLowerCase() !== newName.toLowerCase()) {
    const action = S.signature ? 'SIGNED' : 'SENT';
    if (!confirm(
      `⚠ This estimate has already been ${action} to ${currentName}.\n\n` +
      `Replacing their info with "${newName}" will change what the customer sees at the signing link.\n\n` +
      `To create a new estimate for ${newName}, click Cancel and use New Estimate instead.`
    )) {
      closeCrm(); return;
    }
  }
  const a=parseCrmAddress(p.address);
  // crm_contact_id is what links this estimate back to the job's actual costs
  // in The Den. It used to be hardcoded null here even though the project the
  // rep just picked carries one, which left bid-vs-actual unanswerable.
  S.customer={crm_contact_id:p.contact_id||null,crm_project_id:p.id,crm_job_number:p.job_number||'',
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
function renderSentLockBanner() {
  const el = document.getElementById('sent-lock-banner');
  if (!el) return;
  if (S.signature) {
    el.style.display = '';
    el.className = 'sent-lock-banner sent-lock-signed';
    el.innerHTML = `🔒 <strong>Signed</strong> — editing customer info will affect the signed document`;
  } else if (S.share_token) {
    el.style.display = '';
    el.className = 'sent-lock-banner sent-lock-sent';
    el.innerHTML = `📤 <strong>Sent to ${esc(S.customer.name||'customer')}</strong> — changes here affect their signing link`;
  } else {
    el.style.display = 'none';
    el.className = 'sent-lock-banner';
    el.innerHTML = '';
  }
}

/* Outcome control for the estimate you are looking at.
   Only appears once an estimate exists and has been saved — there is no
   outcome to record on something that was never sent. A signature is a fact
   about what the customer did, so a signed estimate shows the fact and offers
   no control; the server enforces the same rule. */
function renderEstStatusBar() {
  const el = document.getElementById('est-status-bar');
  if (!el) return;
  if (!S.estimate_id) { el.style.display = 'none'; return; }

  if (S.signature) {
    el.className = 'est-status-bar est-status-signed';
    el.innerHTML = '✓ <strong>Signed</strong> — this job is won and has gone to The Den';
    el.style.display = 'block';
    return;
  }

  const st = S.status || 'draft';
  const opts = [['draft','Draft'], ['sent','Sent'], ['accepted','Accepted ✓'], ['lost','Lost ✗']];
  el.className = 'est-status-bar' + (st === 'lost' ? ' est-status-lost' : '');
  el.innerHTML = `
    <label for="est-status-select">Outcome</label>
    <select id="est-status-select" onchange="setEstStatus(this.value)">
      ${opts.map(([v, l]) => `<option value="${v}" ${st === v ? 'selected' : ''}>${l}</option>`).join('')}
    </select>
    ${st === 'lost' ? '<span class="est-status-note">Out of Outstanding and follow-ups. The lead stays open in the Pipeline.</span>' : ''}`;
  el.style.display = 'flex';
}

async function setEstStatus(status) {
  if (!S.estimate_id) return;
  try {
    const r = await fetch(`${BASE}/api/estimates/${S.estimate_id}/status`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ status }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || 'Could not update the outcome.');
    S.status = j.status;
  } catch (e) {
    alert(e.message);
  }
  renderEstStatusBar();
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

/* Downscale big photos client-side before upload — phone photos run 4–8 MB
   each and go straight to the server volume (and every backup). Falls back to
   the original file if the browser can't decode it (e.g. HEIC). */
async function compressImage(file) {
  if (!file.type || !file.type.startsWith('image/') || file.type === 'image/gif') return file;
  if (file.size < 800 * 1024) return file;  // already small
  try {
    const bmp  = await createImageBitmap(file);
    const maxW = 1800;
    const k = bmp.width > maxW ? maxW / bmp.width : 1;
    const W = Math.max(1, Math.round(bmp.width * k));
    const H = Math.max(1, Math.round(bmp.height * k));
    const cv = document.createElement('canvas');
    cv.width = W; cv.height = H;
    cv.getContext('2d').drawImage(bmp, 0, 0, W, H);
    const blob = await new Promise(res => cv.toBlob(res, 'image/jpeg', 0.85));
    if (!blob || blob.size >= file.size) return file;
    return new File([blob], file.name.replace(/\.[^.]+$/, '') + '.jpg', { type: 'image/jpeg' });
  } catch { return file; }
}

async function uploadPhotos(files) {
  if(!files.length)return;
  if(!S.estimate_id)await saveEstimate();
  for(const file of files){
    const isPdf = /\.pdf$/i.test(file.name) || file.type === 'application/pdf';
    const upFile = isPdf ? file : await compressImage(file);
    const fd=new FormData(); fd.append('file',upFile);
    try{
      const r=await fetch(`/api/uploads/${S.estimate_id}`,{method:'POST',body:fd});
      if(!r.ok){const e=await r.json();throw new Error(e.error||'Upload failed');}
      const res=await r.json();
      if(isPdf){
        if(!Array.isArray(S.attachments)) S.attachments=[];
        S.attachments.push({id:uid(),filename:res.filename,original_name:file.name,
          label:file.name.replace(/\.pdf$/i,''),show_in_estimate:true,
          pages:res.pages||undefined});
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
      <a class="att-view" href="${BASE}/uploads/${esc(att.filename)}" target="_blank" rel="noopener">View</a>
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
  if (activePage === 'documents') renderDocumentsPage();
}
function photoCaption(id,v){ const p=S.photos.find(x=>x.id===id);if(p){p.caption=v;setDirty();} }
function photoToggle(id,v){ const p=S.photos.find(x=>x.id===id);if(p){p.show_in_estimate=v;setDirty();warmPrintPhotos();} }
// Reorder photos — array order drives the printed Photo Report, the cover
// strip, and the customer view.
function photoMove(id, dir) {
  const i = S.photos.findIndex(p => p.id === id);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= S.photos.length) return;
  [S.photos[i], S.photos[j]] = [S.photos[j], S.photos[i]];
  setDirty(); renderPhotosPage(); renderCoverPage(); warmPrintPhotos();
}
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

let _dashData      = [];
let _dashRep       = null; // null until first open; then '' = all reps
let _dashView      = 'estimates'; // 'estimates' | 'analytics'
let _dashFilter    = null; // null = all | 'outstanding' | 'viewed' | 'sent' | 'signed' | 'draft'
let _analyticsData = null; // cached result from /api/analytics
let _analyticsSort = 'revenue'; // 'revenue' | 'margin' | 'jobs'

function daysAgoLabel(iso) {
  if (!iso) return '';
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (d <= 0) return 'today';
  if (d === 1) return '1 day ago';
  return `${d} days ago`;
}
function estStatusOf(e) {
  if (e.signed || e.status === 'accepted') return 'signed';
  // Lost is checked before viewed/sent, and that ordering is the whole point.
  // It used to be missing entirely, so a lost estimate still reported itself as
  // 'viewed' — which left it sitting in Outstanding, counting toward the
  // outstanding total, and nagging from the follow-up banner forever. Marking
  // one lost changed nothing a rep could see.
  if (e.status === 'lost' || e.status === 'declined') return 'lost';
  if (e.first_viewed_at) return 'viewed';
  if (e.sent) return 'sent';
  return 'draft';
}

async function openDashboard() {
  try {
    const r = await fetch('/api/estimates');
    _dashData = await r.json();
  } catch { _dashData = []; }
  rebuildCustCounts();
  if (_dashRep === null) _dashRep = _loggedInUser || '';
  renderDashboard();
  document.getElementById('dashboard-modal').classList.remove('hidden');
}
function closeDashboard() { document.getElementById('dashboard-modal').classList.add('hidden'); }
function maybeCloseDashboard(e) { if (e.target.id === 'dashboard-modal') closeDashboard(); }
function dashSetRep(v) { _dashRep = v; renderDashboard(); }
async function dashDuplicate(id) {
  const r = await fetch(`/api/estimates/${id}/duplicate`, { method: 'POST' });
  if (!r.ok) { alert('Could not duplicate.'); return; }
  const d = await r.json();
  closeDashboard();
  doLoadEstimate(d.estimate_id);
}
async function dashShare(id) {
  const est = _dashData.find(e => e.estimate_id === id);
  if (!est || !est.share_token) {
    alert('Open this estimate first to generate a share link, then use Send / Sign.');
    return;
  }
  const url = window.location.origin + '/sign/' + est.share_token;
  if (navigator.share) {
    const name = est.customer_name || '';
    await doNativeShare(url, name);
  } else {
    await navigator.clipboard.writeText(url).catch(() => {
      const inp = document.createElement('input');
      inp.value = url; document.body.appendChild(inp);
      inp.select(); document.execCommand('copy'); document.body.removeChild(inp);
    });
    alert('Link copied!');
  }
}
async function doNativeShare(url, customerName) {
  try {
    await navigator.share({
      title: 'Your Estimate — Project One Roofing',
      text: `Hi${customerName ? ' ' + customerName.split(' ')[0] : ''}! Your roofing estimate is ready to review:`,
      url,
    });
  } catch(e) {
    if (e.name !== 'AbortError') alert('Could not open share sheet: ' + e.message);
  }
}
async function dashDeleteEstimate(id, name) {
  if (!confirm(`Delete "${name}"? This cannot be undone.`)) return;
  const r = await fetch(`/api/estimates/${id}`, { method: 'DELETE' });
  if (!r.ok) { alert('Could not delete estimate.'); return; }
  _dashData = _dashData.filter(e => e.estimate_id !== id);
  rebuildCustCounts();
  renderDashboard();
}
async function dashUpdateStatus(id, status, selectEl) {
  const r = await fetch(`/api/estimates/${id}/status`, {
    method: 'PATCH',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({status}),
  });
  if (!r.ok) {
    const msg = await r.json().catch(() => ({}));
    alert(msg.error || 'Could not update status.');
    return renderDashboard();
  }
  const est = _dashData.find(e => e.estimate_id === id);
  if (est) est.status = status;
  // Re-render, so the row actually moves to its new section. Without this the
  // status changed on the server and the dashboard carried on showing the
  // estimate exactly where it was, which made marking one lost feel broken.
  renderDashboard();
}

function dashRow(e) {
  const st    = estStatusOf(e);
  const enum_ = e.estimate_id ? 'EST-' + e.estimate_id.split('-')[0].toUpperCase() : '';
  const chips = {
    signed: '<span class="dash-chip dash-chip-signed">✓ Signed</span>',
    viewed: '<span class="dash-chip dash-chip-viewed">👀 Viewed</span>',
    sent:   '<span class="dash-chip dash-chip-sent">📤 Sent</span>',
    draft:  '<span class="dash-chip dash-chip-draft">Draft</span>',
    lost:   '<span class="dash-chip dash-chip-lost">✗ Lost</span>',
  };
  let activity = '';
  if (st === 'signed')      activity = `${e.signed ? 'Signed' : 'Accepted'} ${daysAgoLabel(e.signed_at || e.updated_at)}`;
  else if (st === 'viewed') activity = `Viewed ${daysAgoLabel(e.last_viewed_at)}${e.view_count > 1 ? ` (${e.view_count}×)` : ''}`;
  else if (st === 'sent')   activity = `Sent ${daysAgoLabel(e.sent_at)} — not opened yet`;
  else if (st === 'lost')   activity = `Marked lost ${daysAgoLabel(e.updated_at)}`;
  else                      activity = `Updated ${daysAgoLabel(e.updated_at)}`;
  const typeLbl = e.estimate_type === 'commercial' ? '🏢 Commercial'
                : e.estimate_type === 'insurance' ? '🏛 Insurance'
    : (e.selected_tier ? e.selected_tier[0].toUpperCase() + e.selected_tier.slice(1) : 'Retail');
  const isSigned = st === 'signed';
  const statusSelect = isSigned ? chips[st] : `
    <select class="dash-status-select" title="Update status"
      onclick="event.stopPropagation()"
      onchange="dashUpdateStatus('${esc(e.estimate_id)}',this.value,this)">
      <option value="draft"    ${e.status==='draft'?'selected':''}>Draft</option>
      <option value="sent"     ${e.status==='sent'?'selected':''}>Sent</option>
      <option value="accepted" ${e.status==='accepted'?'selected':''}>Accepted ✓</option>
      <option value="lost"     ${st==='lost'?'selected':''}>Lost ✗</option>
    </select>`;
  // The customer file was reachable only from a home-screen search box and a
  // sidebar button that appears after a name is typed — so the rep looking at
  // a list of estimates had no way to see that three of them are one customer.
  const nEst = custEstimateCount(e.customer_name);
  const cfBadge = nEst > 1 ? `<button class="dash-cf-btn"
      title="${nEst} estimates for this customer — open their file"
      onclick="event.stopPropagation();closeDashboard();openCustomer('${jsq(e.customer_name)}')">📁 ${nEst}</button>` : '';
  return `<div class="dash-row${st==='viewed'?' dash-row-viewed':''}" onclick="doLoadEstimate('${esc(e.estimate_id)}');closeDashboard()">
    <div class="dash-row-main">
      <span class="dash-row-name"><strong>${esc(e.customer_name || '(no customer)')}</strong>${cfBadge}</span>
      <small>${esc(enum_)}${e.city ? ' · ' + esc(e.city) : ''} · ${esc(typeLbl)}${e.salesperson ? ' · ' + esc(cap(e.salesperson)) : ''}</small>
    </div>
    <div class="dash-row-side">
      <span class="dash-total">${fmtCur((e.total || 0) + (e.co_total || 0))}</span>
      ${e.co_count ? `<span class="dash-chip dash-chip-co" title="${e.co_count} change order${e.co_count!==1?'s':''}${e.co_pending ? ` (${e.co_pending} awaiting signature)` : ''}${e.co_total ? ` — ${fmtCur(e.co_total)} signed` : ''}">±${e.co_count} CO${e.co_pending ? ' ⏳' : ''}</span>` : ''}
      ${statusSelect}
      <small class="dash-activity">${esc(activity)}</small>
      ${e.share_token ? `<button class="dash-send-btn" title="Resend customer link"
        onclick="event.stopPropagation();dashShare('${esc(e.estimate_id)}')">📤</button>` : ''}
      <button class="dash-dup-btn" title="Duplicate estimate"
        onclick="event.stopPropagation();dashDuplicate('${esc(e.estimate_id)}')">⎘</button>
      <button class="dash-delete-btn" title="Delete estimate"
        onclick="event.stopPropagation();dashDeleteEstimate('${esc(e.estimate_id)}','${esc(e.customer_name||'this estimate')}')">🗑</button>
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
  const lost    = list.filter(e => estStatusOf(e) === 'lost')
                      .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));

  const outstanding   = [...viewed, ...sent];
  const outstandingSum = outstanding.reduce((s, e) => s + (e.total || 0), 0);
  const cutoff30   = Date.now() - 30 * 86400000;
  const signed30   = signed.filter(e => e.signed_at && new Date(e.signed_at).getTime() >= cutoff30);
  const signed30Sum = signed30.reduce((s, e) => s + (e.total || 0) + (e.co_total || 0), 0);

  const repOpts = ['<option value="">All reps</option>']
    .concat(TEAM.map(m => `<option value="${m}" ${m === _dashRep ? 'selected' : ''}>${cap(m)}</option>`))
    .join('');

  const section = (title, arr, cls) => arr.length
    ? `<div class="dash-section"><h4 class="${cls || ''}">${title} <span class="dash-count">${arr.length}</span></h4>
       ${arr.map(dashRow).join('')}</div>`
    : '';

  // Follow-up alerts: sent 3+ days without view, or viewed 2+ days without signing
  const now = Date.now();
  const needsFollowUp = list.filter(e => {
    const st = estStatusOf(e);
    if (st === 'sent' && e.sent_at) {
      const daysSent = (now - new Date(e.sent_at).getTime()) / 86400000;
      return daysSent >= 3;
    }
    if (st === 'viewed' && e.last_viewed_at) {
      const daysViewed = (now - new Date(e.last_viewed_at).getTime()) / 86400000;
      return daysViewed >= 2;
    }
    return false;
  }).sort((a, b) => (a.sent_at || a.last_viewed_at || '').localeCompare(b.sent_at || b.last_viewed_at || ''));

  body.innerHTML = `
    <div class="dash-toolbar">
      <div class="dash-view-tabs">
        <button class="dash-view-tab ${_dashView==='estimates'?'active':''}" onclick="dashSetView('estimates')">Estimates</button>
        <button class="dash-view-tab ${_dashView==='analytics'?'active':''}" onclick="dashSetView('analytics')">📊 Sales Analytics</button>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        ${_meIsAdmin() ? `<a href="${BASE}/api/backup" class="dash-backup-link" title="Download a zip of all estimates, photos, and settings">💾 Backup</a>` : ''}
        ${_meCanViewAll() ? `<select onchange="dashSetRep(this.value)" class="dash-rep-select">${repOpts}</select>` : ''}
      </div>
    </div>
    ${_dashView === 'analytics' ? renderDashboardAnalytics(list, _dashData) : `
    ${needsFollowUp.length ? `
    <div class="dash-followup-banner">
      <strong>⚠ Follow Up Needed (${needsFollowUp.length})</strong>
      <span class="dash-followup-sub">Estimates going cold — act now</span>
      <div class="dash-followup-list">
        ${needsFollowUp.map(e => {
          const st = estStatusOf(e);
          const dayLabel = st === 'sent'
            ? `Sent ${daysAgoLabel(e.sent_at)} — never opened`
            : `Viewed ${daysAgoLabel(e.last_viewed_at)} — not signed`;
          return `<div class="dash-followup-row" onclick="doLoadEstimate('${esc(e.estimate_id)}');closeDashboard()">
            <div class="dash-followup-main">
              <strong>${esc(e.customer_name||'(no customer)')}</strong>
              <small>${esc(dayLabel)}</small>
            </div>
            <div style="display:flex;gap:6px;align-items:center">
              <span class="dash-total">${fmtCur(e.total||0)}</span>
              ${e.share_token ? `<button class="dash-send-btn" title="Resend link" style="opacity:1"
                onclick="event.stopPropagation();dashShare('${esc(e.estimate_id)}')">📤</button>` : ''}
            </div>
          </div>`;
        }).join('')}
      </div>
    </div>` : ''}
    <div class="dash-cards">
      <div class="dash-card ${_dashFilter==='outstanding'?'dash-card-active':''}"
        onclick="dashSetFilter('outstanding')" title="Click to filter">
        <div class="dash-card-num">${outstanding.length}</div>
        <div class="dash-card-lbl">Outstanding</div>
        <div class="dash-card-sub">${viewed.length ? `<span class="dash-card-hot-count">🔥 ${viewed.length} viewed</span>` : fmtCur(outstandingSum)}</div>
      </div>
      <div class="dash-card ${_dashFilter==='sent'?'dash-card-active':''}"
        onclick="dashSetFilter('sent')" title="Click to filter">
        <div class="dash-card-num">${sent.length}</div>
        <div class="dash-card-lbl">Sent — never opened</div>
        <div class="dash-card-sub">re-send or call</div>
      </div>
      <div class="dash-card dash-card-won ${_dashFilter==='signed'?'dash-card-active':''}"
        onclick="dashSetFilter('signed')" title="Click to filter">
        <div class="dash-card-num">${signed30.length}</div>
        <div class="dash-card-lbl">Signed (30 days)</div>
        <div class="dash-card-sub">${fmtCur(signed30Sum)}</div>
      </div>
      <div class="dash-card ${_dashFilter==='draft'?'dash-card-active':''}"
        onclick="dashSetFilter('draft')" title="Click to filter">
        <div class="dash-card-num">${drafts.length}</div>
        <div class="dash-card-lbl">Drafts</div>
        <div class="dash-card-sub">not yet sent</div>
      </div>
    </div>
    ${_dashFilter ? `<div class="dash-filter-bar">
      Showing: <strong>${{outstanding:'Outstanding',sent:'Sent',signed:'Signed',draft:'Drafts',lost:'Lost'}[_dashFilter]||_dashFilter}</strong>
      <button class="dash-filter-clear" onclick="dashSetFilter(null)">× Show all</button>
    </div>` : ''}
    ${(!_dashFilter || _dashFilter==='outstanding') && (viewed.length||sent.length) ?
        section('🔥 Outstanding', [...viewed,...sent].sort((a,b)=>(b.last_viewed_at||b.sent_at||'').localeCompare(a.last_viewed_at||a.sent_at||'')), '') : ''}
    ${(!_dashFilter || _dashFilter==='sent') ?
        section('📤 Sent — not yet opened', sent) : ''}
    ${(!_dashFilter || _dashFilter==='draft') ?
        section('📝 Drafts', drafts) : ''}
    ${(!_dashFilter || _dashFilter==='signed') ?
        section('✅ Signed', _dashFilter==='signed' ? signed : signed.slice(0,15), 'dash-h-won') : ''}
    ${lost.length && (!_dashFilter || _dashFilter==='lost') ?
        section('✗ Lost', _dashFilter==='lost' ? lost : lost.slice(0,10), 'dash-h-lost') : ''}
    ${!list.length ? '<div class="dash-empty">No estimates yet for this rep.</div>' : ''}
    `}`;
}

function dashSetFilter(f) {
  _dashFilter = (_dashFilter === f) ? null : f;
  renderDashboard();
}
async function dashSetView(v) {
  _dashView = v;
  if (v === 'analytics' && !_analyticsData) {
    document.getElementById('dashboard-body').innerHTML =
      '<div style="text-align:center;padding:40px;color:var(--text-light)">Loading analytics…</div>';
    try {
      const r = await fetch('/api/analytics');
      _analyticsData = await r.json();
    } catch(e) {
      _analyticsData = { by_trade:{}, by_rep:{} };
    }
  }
  renderDashboard();
}

function _pbar(val, max, cls='') {
  const pct = max > 0 ? Math.min(100, Math.round(val/max*100)) : 0;
  return `<div class="a-bar-track"><div class="a-bar-fill ${cls}" style="width:${pct}%"></div></div>`;
}
function _clr(rate) {
  return rate >= 50 ? 'a-good' : rate >= 30 ? 'a-warn' : 'a-bad';
}

function renderDashboardAnalytics(filteredList, allData) {
  const ad  = _analyticsData || { by_trade:{}, by_rep:{}, monthly:[], funnel:{total:0,sent:0,viewed:0,signed:0,lost:0}, pipeline_aging:{}, by_type:{}, top_cities:[], ytd_revenue:0, avg_days_to_close:null };
  const now = Date.now();
  const ms30 = 30*86400000;

  // ── Core metrics ─────────────────────────────────────────────────────
  const allSigned  = allData.filter(e => estStatusOf(e) === 'signed');
  const allSent    = allData.filter(e => e.share_token);
  const s30        = allSigned.filter(e=>e.signed_at && now-new Date(e.signed_at)<ms30);
  const totalRev   = allSigned.reduce((s,e)=>s+(e.total||0),0);
  const rev30      = s30.reduce((s,e)=>s+(e.total||0),0);
  const closeRate  = allSent.length ? Math.round(allSigned.length/allSent.length*100) : 0;
  const avgDeal    = allSigned.length ? Math.round(totalRev/allSigned.length) : 0;
  const ytdRev     = ad.ytd_revenue || 0;
  const avgDTC     = ad.avg_days_to_close;
  const staleAll   = allData.filter(e=>{
    const st=estStatusOf(e);
    if(st==='sent'&&e.sent_at)         return (now-new Date(e.sent_at).getTime())/86400000>=3;
    if(st==='viewed'&&e.last_viewed_at) return (now-new Date(e.last_viewed_at).getTime())/86400000>=2;
    return false;
  });
  const pipeline   = allData.filter(e=>estStatusOf(e)!=='signed'&&estStatusOf(e)!=='draft'&&e.share_token);
  const pipelineVal= pipeline.reduce((s,e)=>s+(e.total||0),0);
  const repEntries = Object.entries(ad.by_rep).filter(([,d])=>d.sent>0||d.revenue>0);

  // ── Conversion funnel ────────────────────────────────────────────────
  const fn = ad.funnel||{total:0,sent:0,viewed:0,signed:0,lost:0};
  const funnelSteps = [
    {label:'Created', val:fn.total, pct:100},
    {label:'Sent',    val:fn.sent,    pct:fn.total?Math.round(fn.sent/fn.total*100):0},
    {label:'Viewed',  val:fn.viewed,  pct:fn.sent?Math.round(fn.viewed/fn.sent*100):0},
    {label:'Signed',  val:fn.signed,  pct:fn.viewed?Math.round(fn.signed/fn.viewed*100):0},
  ];
  const funnelHtml = funnelSteps.map(s=>`
    <div class="funnel-step">
      <div class="funnel-bar-wrap">
        <div class="funnel-bar" style="width:${s.pct}%"></div>
      </div>
      <div class="funnel-labels">
        <span class="funnel-name">${s.label}</span>
        <span class="funnel-val">${s.val}</span>
        <span class="funnel-pct">${s.pct}%</span>
      </div>
    </div>`).join('');
  const lostHtml = fn.lost ? `<div class="funnel-declined">✗ ${fn.lost} lost</div>` : '';

  // ── Pipeline aging ───────────────────────────────────────────────────
  const pa = ad.pipeline_aging||{};
  const agingBuckets = [
    {key:'fresh',  label:'Fresh (0–3d)',   color:'#16a34a'},
    {key:'active', label:'Active (4–14d)', color:'#0284c7'},
    {key:'stale',  label:'Stale (15–30d)', color:'#ea580c'},
    {key:'cold',   label:'Cold (30+d)',    color:'#dc2626'},
  ];
  const agingTotal = agingBuckets.reduce((s,b)=>s+(pa[b.key]?.value||0),0);
  const agingHtml = agingBuckets.map(b=>{
    const d=pa[b.key]||{count:0,value:0};
    const pct=agingTotal>0?Math.round(d.value/agingTotal*100):0;
    return `<div class="aging-row">
      <span class="aging-dot" style="background:${b.color}"></span>
      <span class="aging-label">${b.label}</span>
      <span class="aging-count">${d.count} deal${d.count!==1?'s':''}</span>
      <div class="aging-bar-wrap"><div class="aging-bar" style="width:${pct}%;background:${b.color}"></div></div>
      <span class="aging-val">${fmtCur(d.value)}</span>
    </div>`;
  }).join('');

  // ── Retail vs Insurance ──────────────────────────────────────────────
  const bt = ad.by_type||{};
  const typeTotal = Object.values(bt).reduce((s,d)=>s+(d.revenue||0),0);
  const typeHtml = Object.entries(bt).map(([type,d])=>{
    const pct = typeTotal>0?Math.round(d.revenue/typeTotal*100):0;
    return `<div class="type-row">
      <span class="type-label">${type==='insurance'?'🏛 Insurance':'🏠 Retail'}</span>
      <span class="type-count">${d.count} jobs</span>
      <div class="aging-bar-wrap"><div class="aging-bar" style="width:${pct}%;background:${type==='insurance'?'#6366f1':'#0284c7'}"></div></div>
      <span class="aging-val">${fmtCur(d.revenue)}</span>
    </div>`;
  }).join('');

  // ── Top cities ───────────────────────────────────────────────────────
  const cities = ad.top_cities||[];
  const maxCity = Math.max(1,...cities.map(([,v])=>v));
  const cityRows = cities.map(([city,rev])=>`
    <div class="city-row">
      <span class="city-name">${esc(city)}</span>
      <div class="aging-bar-wrap"><div class="aging-bar" style="width:${Math.round(rev/maxCity*100)}%;background:#8b5cf6"></div></div>
      <span class="aging-val">${fmtCur(rev)}</span>
    </div>`).join('');

  // ── Sort controls ────────────────────────────────────────────────────
  const sortBtns = ['revenue','close_rate','margin'].map(k =>
    `<button class="analytics-sort-btn ${_analyticsSort===k?'active':''}"
      onclick="_analyticsSort='${k}';renderDashboard()">${
        {revenue:'Revenue',close_rate:'Close %',margin:'Margin'}[k]}</button>`).join('');

  // ── Rep leaderboard ──────────────────────────────────────────────────
  const maxRevRep = Math.max(1,...repEntries.map(([,d])=>d.revenue));
  const sorted = [...repEntries].sort((a,b)=>{
    if(_analyticsSort==='close_rate') return (b[1].close_rate??0)-(a[1].close_rate??0);
    if(_analyticsSort==='margin')     return (b[1].margin_pct??-1)-(a[1].margin_pct??-1);
    return b[1].revenue-a[1].revenue;
  });
  const repRows = sorted.map(([name,d],idx)=>{
    const medal   = idx===0?'🥇':idx===1?'🥈':idx===2?'🥉':`#${idx+1}`;
    const crCls   = _clr(d.close_rate||0);
    const stC     = staleAll.filter(e=>(e.salesperson||'')===name).length;
    const stCell  = stC ? `<span class="analytics-stale-badge">${stC}</span>` : '—';
    const m       = d.margin_pct!=null ? `<span class="analytics-margin-badge">${d.margin_pct}%</span>` : '—';
    const dtc     = d.avg_days_to_close!=null ? `${d.avg_days_to_close}d` : '—';
    return `<tr>
      <td><span class="rep-rank">${medal}</span> <strong>${esc(cap(name))}</strong></td>
      <td class="analytics-num">${d.sent}</td>
      <td class="analytics-num">${d.signed}</td>
      <td class="analytics-num"><span class="a-rate-badge ${crCls}">${d.close_rate??0}%</span></td>
      <td class="analytics-num analytics-rev">${fmtCur(d.revenue)}
        ${_pbar(d.revenue,maxRevRep,'a-bar-rev')}</td>
      <td class="analytics-num">${fmtCur(d.avg_deal||0)}</td>
      <td class="analytics-num">${dtc}</td>
      <td class="analytics-num">${m}</td>
      <td class="analytics-num analytics-pipe">${fmtCur(d.pipeline)}</td>
      <td class="analytics-num">${stCell}</td>
    </tr>`;
  }).join('');

  // ── Trade breakdown ──────────────────────────────────────────────────
  const tl = {roofing:'🏠 Roofing',siding:'🏗 Siding',windows:'🪟 Windows',gutters:'🌧 Gutters',commercial:'🏢 Commercial',other:'📦 Other'};
  const maxTrade = Math.max(1,...Object.values(ad.by_trade).map(d=>d.revenue));
  const tradeRows = Object.entries(ad.by_trade).sort((a,b)=>b[1].revenue-a[1].revenue).map(([tk,d])=>{
    const m = d.margin_pct!=null?`<span class="analytics-margin-badge">${d.margin_pct}%</span>`:'—';
    const pct = totalRev>0?Math.round(d.revenue/totalRev*100):0;
    return `<tr>
      <td><strong>${tl[tk]||tk}</strong></td>
      <td class="analytics-num">${d.job_count}</td>
      <td class="analytics-num analytics-rev">${fmtCur(d.revenue)}
        <span class="analytics-pct">${pct}%</span>
        ${_pbar(d.revenue,maxTrade,'a-bar-rev')}</td>
      <td class="analytics-num">${m}</td>
      <td class="analytics-num analytics-pipe">${fmtCur(d.pipeline)} <span style="font-size:10px;color:#94a3b8">(${d.pipeline_count})</span></td>
    </tr>`;
  }).join('');

  const nd = (n)=>`<tr><td colspan="${n}" style="text-align:center;color:#94a3b8;padding:16px">No data yet</td></tr>`;

  return `
    <!-- ── KPI Cards ──────────────────────────────────────── -->
    <div class="analytics-cards a-cards-6">
      <div class="analytics-card analytics-card-rev">
        <div class="analytics-card-val">${fmtCur(totalRev)}</div>
        <div class="analytics-card-lbl">Total Revenue</div>
        <div class="analytics-card-sub">${allSigned.length} jobs closed</div>
      </div>
      <div class="analytics-card analytics-card-month">
        <div class="analytics-card-val">${fmtCur(ytdRev)}</div>
        <div class="analytics-card-lbl">YTD Revenue</div>
        <div class="analytics-card-sub">${new Date().getFullYear()}</div>
      </div>
      <div class="analytics-card" style="background:#eff6ff;border-color:#bfdbfe">
        <div class="analytics-card-val">${fmtCur(rev30)}</div>
        <div class="analytics-card-lbl">Last 30 Days</div>
        <div class="analytics-card-sub">${s30.length} jobs</div>
      </div>
      <div class="analytics-card analytics-card-q">
        <div class="analytics-card-val">${fmtCur(avgDeal)}</div>
        <div class="analytics-card-lbl">Avg Deal Size</div>
        <div class="analytics-card-sub">per signed job</div>
      </div>
      <div class="analytics-card analytics-card-rate">
        <div class="analytics-card-val">${closeRate}%</div>
        <div class="analytics-card-lbl">Close Rate</div>
        <div class="analytics-card-sub">${allSigned.length} of ${allSent.length} sent</div>
      </div>
      <div class="analytics-card" style="background:#f5f3ff;border-color:#ddd6fe">
        <div class="analytics-card-val" style="color:#7c3aed">${avgDTC!=null?avgDTC+'d':'—'}</div>
        <div class="analytics-card-lbl">Avg Days to Close</div>
        <div class="analytics-card-sub">sent → signed</div>
      </div>
    </div>

    <!-- ── Row 2: Funnel + Pipeline + Type ───────────────── -->
    <div class="a-row-3">
      <div class="analytics-section a-card">
        <h4 class="analytics-h">Conversion Funnel</h4>
        ${funnelHtml}
        ${lostHtml}
      </div>
      <div class="analytics-section a-card">
        <h4 class="analytics-h">Pipeline Health <span class="analytics-pct">${fmtCur(pipelineVal)}</span></h4>
        ${agingHtml || '<p style="color:#94a3b8;font-size:12px;padding:8px 0">No open pipeline</p>'}
      </div>
      <div class="analytics-section a-card">
        <h4 class="analytics-h">Retail vs Insurance</h4>
        ${typeHtml || '<p style="color:#94a3b8;font-size:12px;padding:8px 0">No data</p>'}
        ${cities.length ? `<h4 class="analytics-h" style="margin-top:14px">Top Markets</h4>${cityRows}` : ''}
      </div>
    </div>

    <!-- ── Monthly Trends & Goals ─────────────────────────── -->
    ${renderMonthlyTrends(ad)}

    <!-- ── Rep Leaderboard ────────────────────────────────── -->
    <div class="analytics-section a-card">
      <div class="analytics-sort-bar" style="margin-bottom:10px">
        <h4 class="analytics-h" style="margin:0">Rep Leaderboard</h4>
        <span class="analytics-sort-lbl" style="margin-left:auto">Sort:</span>${sortBtns}
        <button class="analytics-refresh-btn" onclick="_analyticsData=null;dashSetView('analytics')" title="Refresh data">↺</button>
      </div>
      <div class="analytics-table-wrap">
      <table class="analytics-table">
        <thead><tr>
          <th>Rep</th><th>Sent</th><th>Signed</th><th>Close %</th>
          <th>Revenue</th><th>Avg Deal</th><th>Avg Close</th>
          <th>Margin</th><th>Pipeline</th><th title="Cold estimates">Stale</th>
        </tr></thead>
        <tbody>${repRows || nd(10)}</tbody>
      </table>
      </div>
    </div>

    <!-- ── Revenue by Trade ───────────────────────────────── -->
    <div class="analytics-section a-card">
      <h4 class="analytics-h">Revenue by Trade</h4>
      <div class="analytics-table-wrap">
      <table class="analytics-table">
        <thead><tr><th>Trade</th><th>Jobs</th><th>Revenue</th><th>Avg Margin</th><th>Pipeline</th></tr></thead>
        <tbody>${tradeRows || nd(5)}</tbody>
      </table>
      </div>
    </div>`;
}

/* ── Monthly trends & sales goals ─────────────────────────────────────
   The monthly panel is the "did we hit our number?" view: every month is
   measured against the goal that was set for it, and the current month is
   measured against pace so the gap is actionable while there's still time to
   close it. All the math arrives from /api/analytics — this only formats. */

let _monRange = 12;   // months of history shown: 6 | 12 | 24

const _MON_ABBR = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function _monLabel(ym, withYear) {
  const [y, m] = String(ym || '').split('-');
  return (_MON_ABBR[+m - 1] || ym) + (withYear ? ` '${(y || '').slice(2)}` : '');
}
/* Compact money for chart labels and KPI tiles — fmtCur's cents are noise at
   six figures. Tables keep fmtCur so the numbers still reconcile. */
function _fmtK(n) {
  const a = Math.abs(n || 0), sign = n < 0 ? '−' : '';
  if (a >= 1e6) return sign + '$' + (a / 1e6).toFixed(a >= 1e7 ? 0 : 1) + 'M';
  if (a >= 1000) return sign + '$' + Math.round(a / 1000) + 'k';
  return sign + '$' + Math.round(a);
}
/* Attainment banding, shared by the hero, the bars, and the table. */
function _goalCls(pct) {
  if (pct == null) return 'g-none';
  return pct >= 100 ? 'g-hit' : pct >= 80 ? 'g-near' : 'g-miss';
}
function _monShift(ym, n) {
  const i = +ym.slice(0, 4) * 12 + (+ym.slice(5, 7) - 1) + n;
  return `${String(Math.floor(i / 12)).padStart(4, '0')}-${String(i % 12 + 1).padStart(2, '0')}`;
}

function renderMonthlyTrends(ad) {
  const all  = ad.monthly || [];
  const rows = all.slice(-_monRange);
  const cm   = ad.current_month || {};
  const bm   = ad.benchmarks || {};
  const canEdit = _meCanViewAll();

  const rangeBtns = [6, 12, 24].map(n =>
    `<button class="analytics-sort-btn ${_monRange === n ? 'active' : ''}"
      onclick="_monRange=${n};renderDashboard()">${n}m</button>`).join('');

  // ── Current month vs goal ──────────────────────────────────────────
  const hasGoal = (cm.goal || 0) > 0;
  const pct     = hasGoal ? (cm.pct || 0) : null;
  const heroCls = hasGoal ? (cm.on_pace ? 'g-hit' : _goalCls(pct)) : 'g-none';
  // The pace marker is where the month SHOULD be today on a straight line.
  const paceMark = hasGoal && cm.days_in_month
    ? Math.min(100, Math.round(cm.days_elapsed / cm.days_in_month * 100)) : null;
  const hero = `
    <div class="goal-hero ${heroCls}">
      <div class="goal-hero-main">
        <div class="goal-hero-top">
          <span class="goal-hero-month">${_monLabel(cm.month, true)}</span>
          <span class="goal-hero-days">Day ${cm.days_elapsed || 0} of ${cm.days_in_month || 0}
            · ${cm.days_left || 0} left</span>
        </div>
        <div class="goal-hero-nums">
          <span class="goal-hero-val">${_fmtK(cm.revenue || 0)}</span>
          ${hasGoal ? `<span class="goal-hero-of">of ${_fmtK(cm.goal)} goal</span>
            <span class="goal-hero-pct">${pct}%</span>` :
            `<span class="goal-hero-of">signed — no goal set for this month</span>`}
        </div>
        <div class="goal-track">
          <div class="goal-track-fill" style="width:${Math.min(100, pct || 0)}%"></div>
          ${paceMark != null ? `<div class="goal-track-pace" style="left:${paceMark}%"
            title="Where you should be today to finish on goal"></div>` : ''}
        </div>
        <div class="goal-hero-sub">
          ${cm.jobs || 0} job${cm.jobs === 1 ? '' : 's'} signed
          ${cm.goal_jobs ? ` of ${cm.goal_jobs} target` : ''}
          ${hasGoal ? ` · pace to date ${_fmtK(cm.expected_to_date)}` : ''}
        </div>
      </div>
      ${hasGoal ? `
      <div class="goal-hero-stats">
        <div class="goal-stat">
          <div class="goal-stat-val">${_fmtK(cm.projected || 0)}</div>
          <div class="goal-stat-lbl">Projected finish</div>
          <div class="goal-stat-sub ${cm.on_pace ? 'pos' : 'neg'}">
            ${cm.on_pace ? '✓ on pace' : '▼ behind pace'}</div>
        </div>
        <div class="goal-stat">
          <div class="goal-stat-val">${cm.gap > 0 ? _fmtK(cm.gap) : '✓'}</div>
          <div class="goal-stat-lbl">${cm.gap > 0 ? 'Still to sell' : 'Goal met'}</div>
          <div class="goal-stat-sub">${cm.gap > 0 ? `${_fmtK(cm.per_day_needed)}/day` : 'nice work'}</div>
        </div>
      </div>` : `
      <div class="goal-hero-stats">
        <div class="goal-stat">
          <div class="goal-stat-val" style="font-size:15px">—</div>
          <div class="goal-stat-lbl">No goal</div>
          <div class="goal-stat-sub">${canEdit ? 'set one below' : 'ask your manager'}</div>
        </div>
      </div>`}
    </div>`;

  // ── Bars: revenue against that month's goal line ───────────────────
  const scale = Math.max(1, ...rows.map(r => Math.max(r.revenue, r.goal || 0)));
  const bars = rows.map((r, i) => {
    const h    = Math.round(r.revenue / scale * 100);
    const gh   = r.goal > 0 ? Math.min(100, Math.round(r.goal / scale * 100)) : null;
    const cls  = _goalCls(r.pct_to_goal);
    const isCur = r.month === cm.month;
    return `<div class="mon-bar-wrap" title="${_monLabel(r.month, true)} — ${fmtCur(r.revenue)}${
        r.goal > 0 ? ` of ${fmtCur(r.goal)} goal (${r.pct_to_goal}%)` : ''}">
      <div class="mon-bar-track">
        <div class="mon-bar-fill ${cls} ${isCur ? 'is-current' : ''}" style="height:${h}%"></div>
        ${gh != null ? `<div class="mon-goal-line" style="bottom:${gh}%"></div>` : ''}
      </div>
      <!-- Year on the first bar and every January, so a 24-month view doesn't
           show two unlabelled "Apr"s. -->
      <div class="mon-bar-lbl ${isCur ? 'is-current' : ''}">${
        _monLabel(r.month, i === 0 || r.month.endsWith('-01'))}</div>
      <div class="mon-bar-val">${_fmtK(r.revenue)}</div>
      ${r.pct_to_goal != null
        ? `<div class="mon-bar-growth ${r.pct_to_goal >= 100 ? 'pos' : 'neg'}">${r.pct_to_goal}%</div>`
        : r.mom_pct != null
        ? `<div class="mon-bar-growth ${r.mom_pct >= 0 ? 'pos' : 'neg'}">${r.mom_pct >= 0 ? '+' : ''}${r.mom_pct}%</div>`
        : '<div class="mon-bar-growth">&nbsp;</div>'}
    </div>`;
  }).join('');

  // ── Detail table ───────────────────────────────────────────────────
  const dlt = (v, suffix = '%') => v == null ? '<span class="mon-dim">—</span>'
    : `<span class="${v >= 0 ? 'pos' : 'neg'}">${v >= 0 ? '+' : ''}${v}${suffix}</span>`;
  const tableRows = [...rows].reverse().map(r => {
    const vsGoal = r.goal > 0 ? r.revenue - r.goal : null;
    return `<tr class="${r.month === cm.month ? 'mon-row-current' : ''}">
      <td><strong>${_monLabel(r.month, true)}</strong>${r.month === cm.month
        ? ' <span class="mon-tag">MTD</span>' : ''}</td>
      <td class="analytics-num analytics-rev">${fmtCur(r.revenue)}</td>
      <td class="analytics-num">${r.goal > 0 ? fmtCur(r.goal) : '<span class="mon-dim">—</span>'}</td>
      <td class="analytics-num">${r.pct_to_goal != null
        ? `<span class="a-rate-badge ${_goalCls(r.pct_to_goal)}">${r.pct_to_goal}%</span>`
        : '<span class="mon-dim">—</span>'}</td>
      <td class="analytics-num">${vsGoal == null ? '<span class="mon-dim">—</span>'
        : `<span class="${vsGoal >= 0 ? 'pos' : 'neg'}">${vsGoal >= 0 ? '+' : '−'}${_fmtK(Math.abs(vsGoal))}</span>`}</td>
      <td class="analytics-num">${r.jobs}${r.goal_jobs ? `<span class="mon-dim"> / ${r.goal_jobs}</span>` : ''}</td>
      <td class="analytics-num">${r.avg_deal ? fmtCur(r.avg_deal) : '<span class="mon-dim">—</span>'}</td>
      <td class="analytics-num">${r.margin_pct != null
        ? `<span class="analytics-margin-badge">${r.margin_pct}%</span>` : '<span class="mon-dim">—</span>'}</td>
      <td class="analytics-num">${r.sent}</td>
      <td class="analytics-num">${r.close_rate != null
        ? `<span class="a-rate-badge ${_clr(r.close_rate)}">${r.close_rate}%</span>`
        : '<span class="mon-dim">—</span>'}</td>
      <td class="analytics-num">${dlt(r.mom_pct)}</td>
      <td class="analytics-num">${dlt(r.yoy_pct)}</td>
    </tr>`;
  }).join('');

  // ── This month by rep ──────────────────────────────────────────────
  const repRows = (ad.rep_month || []).map(r => {
    const p = r.pct;
    return `<div class="repgoal-row">
      <span class="repgoal-name">${esc(cap(r.rep))}</span>
      <div class="repgoal-track">
        <div class="repgoal-fill ${_goalCls(p)}" style="width:${Math.min(100, p || 0)}%"></div>
      </div>
      <span class="repgoal-val">${_fmtK(r.revenue)}${r.goal > 0 ? `<span class="mon-dim"> / ${_fmtK(r.goal)}</span>` : ''}</span>
      <span class="repgoal-pct ${_goalCls(p)}">${p != null ? p + '%' : '—'}</span>
    </div>`;
  }).join('');

  const benchLine = `
    <div class="mon-bench">
      <span>3-mo avg <strong>${_fmtK(bm.avg_3 || 0)}</strong></span>
      <span>6-mo avg <strong>${_fmtK(bm.avg_6 || 0)}</strong></span>
      <span>12-mo avg <strong>${_fmtK(bm.avg_12 || 0)}</strong></span>
      ${bm.best_month ? `<span>Best ever <strong>${_fmtK(bm.best_month.revenue)}</strong>
        <span class="mon-dim">(${_monLabel(bm.best_month.month, true)})</span></span>` : ''}
      <span class="mon-dim" style="margin-left:auto">closed months only — the current month is still running</span>
    </div>`;

  return `
    <div class="analytics-section a-card">
      <div class="analytics-sort-bar" style="margin-bottom:12px">
        <h4 class="analytics-h" style="margin:0">📈 Monthly Trends &amp; Goals</h4>
        <span class="analytics-sort-lbl" style="margin-left:auto">Show:</span>${rangeBtns}
        ${canEdit ? `<button class="btn-goal-edit" onclick="openGoalEditor()">🎯 Set Goals</button>` : ''}
      </div>
      ${hero}
      ${rows.length ? `<div class="mon-bars mon-bars-lg mon-bars-goal">${bars}</div>
      <div class="mon-legend">
        <span><i class="mon-key g-hit"></i>goal met</span>
        <span><i class="mon-key g-near"></i>80–99%</span>
        <span><i class="mon-key g-miss"></i>under 80%</span>
        <span><i class="mon-key-line"></i>month's goal</span>
      </div>` : '<p class="mon-dim" style="padding:8px 0">No signed estimates yet.</p>'}
      ${benchLine}
      <div class="analytics-table-wrap" style="margin-top:12px">
        <table class="analytics-table mon-table">
          <thead><tr>
            <th>Month</th><th>Signed</th><th>Goal</th><th>% Goal</th><th>+/−</th>
            <th>Jobs</th><th>Avg Deal</th><th>Margin</th>
            <th title="Estimates sent that month">Sent</th>
            <th title="Of the estimates sent that month, how many have closed">Close %</th>
            <th title="vs the previous month">MoM</th>
            <th title="vs the same month last year">YoY</th>
          </tr></thead>
          <tbody>${tableRows || '<tr><td colspan="12" style="text-align:center;color:#94a3b8;padding:16px">No data yet</td></tr>'}</tbody>
        </table>
      </div>
      ${repRows ? `<h4 class="analytics-h" style="margin-top:16px">
        ${_monLabel(cm.month, true)} by Rep</h4>${repRows}` : ''}
    </div>`;
}

/* ── Goal editor (manager+) ───────────────────────────────────────────
   Edits the whole goals doc at once: a company default, per-month overrides
   (roofing is seasonal — a flat monthly number is wrong half the year), and a
   per-rep default. Blank month inputs mean "use the default", so clearing a
   field is how you remove an override. */

function openGoalEditor() {
  const ad    = _analyticsData || {};
  const goals = ad.goals || { company: { default: {}, months: {} }, reps: {} };
  const cd    = goals.company?.default || {};
  const cmo   = goals.company?.months  || {};
  const cur   = (ad.current_month || {}).month || new Date().toISOString().slice(0, 7);
  const actual = {};
  (ad.monthly || []).forEach(r => { actual[r.month] = r; });

  // 12 months back through 6 forward: past months so history can be corrected,
  // future months so next season's targets can be set now.
  const months = [];
  for (let i = -11; i <= 6; i++) months.push(_monShift(cur, i));

  const monthRows = months.map(m => {
    const g = cmo[m] || {};
    const a = actual[m];
    const isCur = m === cur;
    return `<tr class="${isCur ? 'mon-row-current' : ''}${m > cur ? ' goal-row-future' : ''}">
      <td><strong>${_monLabel(m, true)}</strong>${isCur ? ' <span class="mon-tag">now</span>' : ''}</td>
      <td class="analytics-num mon-dim">${a && a.revenue ? fmtCur(a.revenue) : '—'}</td>
      <td><input type="number" min="0" step="1000" class="goal-input"
        data-goal-month="${m}" data-goal-field="revenue"
        placeholder="default" value="${g.revenue ? g.revenue : ''}"></td>
      <td><input type="number" min="0" step="1" class="goal-input goal-input-sm"
        data-goal-month="${m}" data-goal-field="jobs"
        placeholder="—" value="${g.jobs ? g.jobs : ''}"></td>
    </tr>`;
  }).join('');

  const repNames = [...new Set([
    ...Object.keys(ad.by_rep || {}).map(r => r.trim().toLowerCase()),
    ...Object.keys(goals.reps || {}),
  ])].filter(Boolean).sort();
  const repRows = repNames.map(r => {
    const g = goals.reps?.[r]?.default || {};
    return `<tr>
      <td><strong>${esc(cap(r))}</strong></td>
      <td><input type="number" min="0" step="1000" class="goal-input"
        data-goal-rep="${esc(r)}" data-goal-field="revenue"
        placeholder="no goal" value="${g.revenue ? g.revenue : ''}"></td>
      <td><input type="number" min="0" step="1" class="goal-input goal-input-sm"
        data-goal-rep="${esc(r)}" data-goal-field="jobs"
        placeholder="—" value="${g.jobs ? g.jobs : ''}"></td>
    </tr>`;
  }).join('');

  const bm = ad.benchmarks || {};
  document.getElementById('goals-body').innerHTML = `
    <div class="goal-hint">
      Goals drive the % and pace numbers on the analytics tab. For reference, your
      trailing averages are <strong>${_fmtK(bm.avg_3 || 0)}</strong> (3&nbsp;mo),
      <strong>${_fmtK(bm.avg_6 || 0)}</strong> (6&nbsp;mo) and
      <strong>${_fmtK(bm.avg_12 || 0)}</strong> (12&nbsp;mo).
    </div>
    <div class="field-group">
      <label>Company Default <span class="note-tag">used for any month without its own target</span></label>
      <div class="goal-default-row">
        <label class="goal-inline">Revenue / month
          <input type="number" min="0" step="1000" id="goal-default-rev"
            value="${cd.revenue ? cd.revenue : ''}" placeholder="0"></label>
        <label class="goal-inline">Jobs / month
          <input type="number" min="0" step="1" id="goal-default-jobs"
            value="${cd.jobs ? cd.jobs : ''}" placeholder="0"></label>
        <button class="btn-secondary btn-sm" onclick="_goalFillDefault()"
          title="Copy the default into every month below">Apply to all months</button>
      </div>
    </div>
    <div class="field-group">
      <label>Monthly Targets <span class="note-tag">blank = use the default — set the storm months higher</span></label>
      <div class="analytics-table-wrap goal-month-wrap">
        <table class="analytics-table goal-table">
          <thead><tr><th>Month</th><th>Actual</th><th>Revenue Goal</th><th>Jobs</th></tr></thead>
          <tbody>${monthRows}</tbody>
        </table>
      </div>
    </div>
    ${repRows ? `<div class="field-group">
      <label>Per-Rep Monthly Goal <span class="note-tag">each rep sees their own progress on the analytics tab</span></label>
      <div class="analytics-table-wrap">
        <table class="analytics-table goal-table">
          <thead><tr><th>Rep</th><th>Revenue / month</th><th>Jobs</th></tr></thead>
          <tbody>${repRows}</tbody>
        </table>
      </div>
    </div>` : ''}`;
  document.getElementById('goals-modal').classList.remove('hidden');
}

function _goalFillDefault() {
  const rev  = document.getElementById('goal-default-rev').value;
  const jobs = document.getElementById('goal-default-jobs').value;
  document.querySelectorAll('#goals-body [data-goal-month]').forEach(el => {
    el.value = el.dataset.goalField === 'revenue' ? rev : jobs;
  });
}

function closeGoalEditor() { document.getElementById('goals-modal').classList.add('hidden'); }
function maybeCloseGoals(e) { if (e.target === document.getElementById('goals-modal')) closeGoalEditor(); }

async function saveGoals() {
  const num = el => {
    const v = parseFloat(el.value);
    return isFinite(v) && v > 0 ? v : 0;
  };
  const doc = {
    company: {
      default: {
        revenue: num(document.getElementById('goal-default-rev')),
        jobs:    num(document.getElementById('goal-default-jobs')),
      },
      months: {},
    },
    reps: {},
  };
  document.querySelectorAll('#goals-body [data-goal-month]').forEach(el => {
    const v = num(el);
    if (!v) return;   // blank or 0 → no override for that month
    const m = el.dataset.goalMonth;
    (doc.company.months[m] = doc.company.months[m] || {})[el.dataset.goalField] = v;
  });
  document.querySelectorAll('#goals-body [data-goal-rep]').forEach(el => {
    const v = num(el);
    if (!v) return;
    const r = el.dataset.goalRep;
    doc.reps[r] = doc.reps[r] || { default: {}, months: {} };
    doc.reps[r].default[el.dataset.goalField] = v;
  });

  try {
    const r = await fetch('/api/goals', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(doc),
    });
    if (!r.ok) throw new Error(r.status === 403 ? 'Managers only' : 'Save failed');
    closeGoalEditor();
    _analyticsData = null;      // goals change every % on the panel — refetch
    await dashSetView('analytics');
    toast('🎯 Goals saved');
  } catch (e) {
    alert('Could not save goals: ' + e.message);
  }
}

/* ── Order Sheet ─────────────────────────────────────────────────────── */

function openOrderSheet() {
  const rows = [];
  RETAIL_TRADE_KEYS.forEach(trade => {
    const td = S.trades[trade];
    if (!td || !td.enabled || !(td.line_items||[]).length) return;
    const tier = tradeTier(trade);   // order materials for THIS product's chosen package
    const mode = effectiveTradeMode(trade, td);
    const tradeItems = (td.line_items||[]).filter(item => {
      if ((parseFloat(item.quantity)||0) <= 0) return false;
      if (mode === 'simple') return true;
      return (item.tiers?.[tier]?.included) !== false;
    });
    if (!tradeItems.length) return;
    rows.push(`<tr class="order-trade-header"><td colspan="3">${TRADE_LABELS[trade]}</td></tr>`);
    tradeItems.forEach(item => {
      const qty = parseFloat(item.quantity)||0;
      rows.push(`<tr>
        <td>${esc(item.name)}</td>
        <td class="order-qty">${qty}</td>
        <td>${esc(displayUnit(item))}</td>
      </tr>`);
    });
  });

  const html = rows.length
    ? `<table class="order-table">
        <thead><tr><th>Item</th><th>Qty</th><th>Unit</th></tr></thead>
        <tbody>${rows.join('')}</tbody>
       </table>`
    : '<p style="color:#94a3b8;text-align:center;padding:30px 0">No items with quantities yet.</p>';

  const c = S.customer;
  const addr = [c.address.street, c.address.city, c.address.state].filter(Boolean).join(', ');
  document.getElementById('order-sheet-body').innerHTML = `
    <div class="order-header">
      <strong>${esc(c.name || 'No customer')}</strong>
      ${addr ? `<span>${esc(addr)}</span>` : ''}
      <span>${esc(S.estimate_id ? 'EST-'+S.estimate_id.split('-')[0].toUpperCase() : 'DRAFT')}</span>
    </div>
    ${html}`;
  document.getElementById('order-sheet-modal').classList.remove('hidden');
}
function closeOrderSheet() { document.getElementById('order-sheet-modal').classList.add('hidden'); }
function maybeCloseOrderSheet(e) { if (e.target.id === 'order-sheet-modal') closeOrderSheet(); }
function printOrderSheet() {
  const content = document.getElementById('order-sheet-body').innerHTML;
  const w = window.open('', '_blank', 'width=600,height=700');
  w.document.write(`<!DOCTYPE html><html><head><title>Order Sheet</title>
    <style>body{font-family:system-ui,sans-serif;padding:20px;font-size:13px}
    table{width:100%;border-collapse:collapse}th,td{border:1px solid #e5e7eb;padding:6px 10px;text-align:left}
    th{background:#f8fafc;font-weight:700}
    tr.order-trade-header td{background:#1a3a5c;color:#fff;font-weight:700;padding:5px 10px}
    .order-header{margin-bottom:16px;display:flex;gap:16px;flex-wrap:wrap;font-size:13px}
    .order-header strong{font-size:15px}.order-qty{text-align:right}</style></head>
    <body><h2 style="margin:0 0 12px;font-size:16px">📋 Order Sheet — Project One Roofing</h2>
    ${content}</body></html>`);
  w.document.close();
  w.print();
}

/* ── Customer File (CRM layer) ─────────────────────────────────────────
   One customer, many estimates — the roof this spring, the siding in the
   autumn, the re-quote after the adjuster came back. The file is the place
   those live together.

   `custKey` is the grouping key, and it exists because there used to be two.
   the customer screen grouped with a substring `.includes()` while
   newEstimateForCustomer matched with `===`, so the two disagreed about who
   a customer is: "Jon Smith" dragged "Jon Smithson" into his file, and the
   follow-on estimate then pre-filled from whichever of them sorted first.
   Grouping is now an exact match on the normalized name on BOTH sides. The
   home search box stays a substring search — finding a customer and deciding
   two estimates belong to the same one are different jobs. */
function custKey(name) {
  return (name || '').toLowerCase().replace(/\s+/g, ' ').trim();
}

/* esc() escapes for HTML but not for the JS string literal an inline onclick
   drops the value into, so a customer named O'Brien closed the argument early
   and every button carrying her name was a syntax error — dead, silently.
   JS-escape first, then HTML-escape: the parser undoes the second before the
   handler is compiled, leaving the backslashes in place. */
function jsq(s) {
  return esc(String(s ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'"));
}

// custKey -> how many estimates that customer has, for the dashboard badge.
// Rebuilt whenever _dashData is replaced; reps only ever see their own rows,
// so this counts what they can actually open.
let _custCounts = {};
function rebuildCustCounts() {
  _custCounts = {};
  (_dashData || []).forEach(e => {
    const k = custKey(e.customer_name);
    if (k) _custCounts[k] = (_custCounts[k] || 0) + 1;
  });
}
function custEstimateCount(name) {
  return _custCounts[custKey(name)] || 0;
}

// Global customer search from the home screen
function homeCustomerSearch(q) {
  const el = document.getElementById('home-cust-results');
  if (!el) return;
  if (!q || q.length < 2) { el.classList.add('hidden'); el.innerHTML=''; return; }
  const lq = q.toLowerCase();
  const matches = _dashData.filter(e =>
    (e.customer_name||'').toLowerCase().includes(lq) ||
    (e.city||'').toLowerCase().includes(lq)
  );
  // Group on custKey, the same key the customer file opens with — a search
  // result that says "3 estimates" must open a file containing those three.
  const byName = {};
  matches.forEach(e => {
    const k = custKey(e.customer_name) || '(no name)';
    if (!byName[k]) byName[k] = { name: e.customer_name||'(no name)', count: 0, latest: e };
    byName[k].count++;
  });
  const rows = Object.values(byName).slice(0, 8);
  if (!rows.length) {
    el.innerHTML = '<div class="home-cust-none">No customers found</div>';
  } else {
    el.innerHTML = rows.map(r => `
      <div class="home-cust-row" onclick="document.getElementById('home-cust-search').value='';document.getElementById('home-cust-results').classList.add('hidden');openCustomer('${jsq(r.name)}')">
        <strong>${esc(r.name)}</strong>
        <small>${r.count} estimate${r.count!==1?'s':''} · ${esc(r.latest.city||r.latest.estimate_date||'')}</small>
      </div>`).join('');
  }
  el.classList.remove('hidden');
}

/* Open a customer. This used to be a modal listing their estimates on top of
   whatever the rep was doing; it is now a real screen they land on, because a
   customer is a place you go, not a thing you peek at.

   Getting there means loading one of their estimates — the customer screen
   reads S — so this picks the most recently touched. Two guards:
     * already inside one of this customer's estimates? Just navigate. Loading
       would refetch a doc the rep is arguably already in and discard whatever
       they have typed since the last save.
     * unsaved work on someone ELSE's estimate? Ask first. Every other route
       into a different estimate goes through a control the rep clicked
       deliberately; a customer badge on a dashboard row does not read like
       "throw away my draft". */
async function openCustomer(name) {
  if (!name) return;
  if (!_dashData.length) {
    try{ const r=await fetch('/api/estimates'); _dashData=await r.json(); rebuildCustCounts(); } catch{}
  }
  if (custKey((S.customer || {}).name) === custKey(name)) { switchPage('client'); return; }
  if (dirty && !S.estimate_id &&
      !confirm('You have an unsaved estimate. Open ' + name + ' anyway?')) return;
  // Exact key match. This used to be `.includes()`, which put every Smithson
  // estimate into Jon Smith's file and let one rep's customer absorb another's.
  const match = _dashData
    .filter(e=>custKey(e.customer_name)===custKey(name))
    .sort((a,b)=>(b.updated_at||'').localeCompare(a.updated_at||''))[0];
  if (!match) return;
  await doLoadEstimate(match.estimate_id);   // lands on the customer screen
}

// Customer-level notes, cached by customer key so renderClientPage — which
// runs on every renderAll() — reads memory instead of refetching. Loaded once
// per customer when their screen is opened.
let _custNotes = {key: '', text: ''};

async function loadCustomerNotes(name) {
  const key = custKey(name);
  if (!key || _custNotes.key === key) return;
  try {
    const r = await fetch(`/api/customer-notes/${encodeURIComponent(name)}`);
    const d = await r.json();
    _custNotes = {key, text: d.notes || ''};
    const ta = document.getElementById('cf-notes-ta');
    // Don't clobber what the rep is mid-way through typing.
    if (ta && document.activeElement !== ta) ta.value = _custNotes.text;
  } catch {}
}

async function saveCustomerNotes(name, text) {
  const flash = document.getElementById('cf-notes-flash');
  try {
    await fetch(`/api/customer-notes/${encodeURIComponent(name)}`, {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({notes: text}),
    });
    _custNotes = {key: custKey(name), text};
    if (flash) { flash.textContent = 'Saved ✓'; flash.style.opacity='1'; setTimeout(()=>{ flash.style.opacity='0'; }, 1800); }
  } catch {
    if (flash) { flash.textContent = 'Save failed'; flash.style.opacity='1'; }
  }
}

const EST_TYPE_ICON  = {insurance:'🏛', commercial:'🏢', report:'📋'};
const EST_TYPE_LABEL = {retail:'Retail Estimate', insurance:'Insurance Estimate',
                        commercial:'Commercial Estimate', report:'Condition Report'};
const EST_STATUS_CHIPS = {
  signed: '<span class="dash-chip dash-chip-signed">✓ Signed</span>',
  viewed: '<span class="dash-chip dash-chip-viewed">👀 Viewed</span>',
  sent:   '<span class="dash-chip dash-chip-sent">📤 Sent</span>',
  draft:  '<span class="dash-chip dash-chip-draft">Draft</span>',
  lost:   '<span class="dash-chip dash-chip-lost">✗ Lost</span>',
};

// One row shape for "an estimate belonging to a customer" — shared by the
// Customer File modal's timeline and the Documents door's estimate list, so
// the two can't quietly drift into showing different things for the same
// estimate. `current` marks the row as the one already on screen: non-
// clickable (reloading yourself is a wasted request), visually distinct.
// The type icon renders even when a custom label is set — a custom label
// used to swallow the type entirely, so a rep could not tell an Insurance
// estimate from a Retail one once either had a label.
function estRowHtml(e, {current=false, onClickExtra='', editable=false}={}) {
  const st = estStatusOf(e);
  const enum_ = e.estimate_id ? 'EST-' + e.estimate_id.split('-')[0].toUpperCase() : '';
  const icon = EST_TYPE_ICON[e.estimate_type] || '🏠';
  const label = e.estimate_label || EST_TYPE_LABEL[e.estimate_type] || EST_TYPE_LABEL.retail;
  const rowCls = 'cf-est-row' + (current ? ' cf-est-row-current' : '');
  const click = current ? '' : ` onclick="doLoadEstimate('${esc(e.estimate_id)}')${onClickExtra}"`;
  // The name is what tells one of a customer's estimates from another, so it
  // has to be fixable after the fact — a rep types "Roof" on the doorstep and
  // wants "Roof – Insurance Supplement" once the adjuster has been. Same
  // inline-input pattern the attachment labels on this page already use.
  // stopPropagation keeps a click landing in the box from loading the row.
  const labelHtml = editable
    ? `<input type="text" class="cf-est-label-input" value="${esc(e.estimate_label || '')}"
         placeholder="${esc(label)}" title="Rename this estimate"
         onclick="event.stopPropagation()"
         onkeydown="if(event.key==='Enter')this.blur()"
         onchange="renameEstimate('${esc(e.estimate_id || '')}', this.value)">`
    : `${esc(label)}`;
  return `<div class="${rowCls}"${click}>
      <div class="cf-est-main">
        <div class="cf-est-label"><span class="cf-est-type-ico" title="${esc(label)}">${icon}</span> ${labelHtml}${current ? ' <span class="cf-current-tag">— open now</span>' : ''}</div>
        <div class="cf-est-id">${esc(enum_ || 'unsaved')} · ${esc(e.estimate_date||'')} · ${esc(cap(e.salesperson||''))}</div>
      </div>
      <div class="cf-est-side">
        <strong>${fmtCur(e.total||0)}</strong>
        ${EST_STATUS_CHIPS[st]||''}
        ${e.signed?`<a href="/api/estimates/${esc(e.estimate_id)}/signed" target="_blank" onclick="event.stopPropagation()" class="cf-dl" title="Download signed contract">📄</a>`:''}
      </div>
    </div>`;
}

// Rename an estimate from the Documents door. Three cases, and the split
// matters:
//   * the estimate on screen but never saved — no id yet, so the name lives
//     only on S until the first save carries it up with everything else;
//   * the estimate on screen and saved — PATCH just the label rather than a
//     full-doc save, so renaming can never push the rest of an in-memory doc
//     over the top of newer server state;
//   * one of the customer's other estimates — PATCH, and mirror it into
//     _dashData so the list it was renamed in redraws with the new name.
// Clearing the box is allowed: an empty label falls back to the type name
// ("Retail Estimate"), which is what the row showed before it was ever named.
async function renameEstimate(estimateId, label) {
  label = (label || '').trim();
  const isCurrent = !estimateId || estimateId === S.estimate_id;
  if (isCurrent) S.estimate_label = label;
  if (estimateId) {
    try {
      await fetch(`/api/estimates/${estimateId}/label`, {
        method: 'PATCH', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({label}),
      });
      const row = _dashData.find(x => x.estimate_id === estimateId);
      if (row) row.estimate_label = label;
    } catch {}
  } else if (isCurrent) {
    setDirty();   // nothing on the server to patch yet
  }
  if (isCurrent) renderEstLabelBadge();
  if (activePage === 'documents') {
    const el = document.getElementById('doc-est-list');
    if (el) el.innerHTML = docEstimateListHtml();
  }
}

// The estimate on screen, shaped like a row from /api/estimates so it can sit
// in the same list as the saved ones. Until its first save it has NO id and is
// not in that endpoint's response at all, so anything reading only the fetched
// list shows a rep "no estimates" while they are actively working on one.
function currentEstimateRow() {
  return {
    estimate_id:     S.estimate_id || null,
    customer_name:   (S.customer || {}).name || '',
    estimate_label:  S.estimate_label || '',
    estimate_type:   S.estimate_type || 'retail',
    estimate_date:   S.estimate_date || '',
    salesperson:     S.salesperson || '',
    total:           (selectedTotal() || 0) + (insuranceTotal() || 0),
    status:          S.status || 'draft',
    signed:          !!S.signature,
    sent:            !!S.share_token,
    first_viewed_at: S.first_viewed_at || '',
  };
}

// Every estimate belonging to `name`, current-first — the one source both the
// Documents door and the Customer File modal read, so they can never disagree
// about what a customer has. Each entry is {e, current}.
//
// The open estimate is spliced in from S, but ONLY when it is this customer's:
// the modal opens for any customer (a dashboard row, the home search), and
// stamping the loaded estimate into a stranger's file would be worse than
// omitting it.
function customerEstimateRows(name) {
  const key = custKey(name);
  if (!key) return [];
  const isLoaded = custKey((S.customer || {}).name) === key;
  const others = _dashData
    .filter(e => custKey(e.customer_name) === key &&
                 !(isLoaded && e.estimate_id === S.estimate_id))
    .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
    .map(e => ({e, current: false}));
  return isLoaded ? [{e: currentEstimateRow(), current: true}].concat(others) : others;
}

// The ids that tie an estimate to the rest of the funnel. Copied onto every
// follow-on estimate for a customer, because a second estimate that lacks them
// is an orphan in two directions at once: the funnel cannot attribute it to
// the lead the door-knock came from (so the close rate silently undercounts),
// and _push_to_den() at signature creates a SECOND Den contact for someone
// The Den already has — which is exactly how bid-vs-actual ends up confident
// and wrong.
const CUSTOMER_LINK_FIELDS = ['crm_contact_id', 'crm_project_id',
                              'crm_job_number', 'crm_lead_id'];

async function newEstimateForCustomer(name, label, type) {
  // Pre-fill from the most recently touched estimate for this customer. Keyed
  // exactly, like the file itself — the two used to disagree, so the estimate
  // you opened and the one it copied from were not always the same customer.
  const existing = _dashData
    .filter(e=>custKey(e.customer_name)===custKey(name))
    .sort((a,b)=>(b.updated_at||'').localeCompare(a.updated_at||''))[0];
  newEstimateAction();
  S.customer.name  = name;
  S.estimate_label = label || '';
  if (type) setEstimateType(type);
  // Copy full contact info from existing estimate
  if (existing) {
    try {
      const r = await fetch(`/api/estimates/${existing.estimate_id}`);
      if (r.ok) {
        const full = await r.json();
        const c = full.customer || {};
        if(c.phone)  { S.customer.phone=c.phone; }
        if(c.email)  { S.customer.email=c.email; }
        if(c.address?.street) { Object.assign(S.customer.address, c.address); }
        // Only when blank, so a live CRM handoff — the rep arrived from the
        // Pipeline's "Start estimate" — still wins over the older estimate's
        // copy of the same ids.
        CUSTOMER_LINK_FIELDS.forEach(k => {
          if (!S.customer[k] && c[k]) S.customer[k] = c[k];
        });
      }
    } catch {}
  }
  setVal('cust-name', name);
  setDirty(); renderSidebar(); renderCoverPage();
}

/* ── Settings ───────────────────────────────────────────────────────── */

async function openSettings() {
  try {
    const r = await fetch('/api/settings');
    appSettings = await r.json() || {};
  } catch { appSettings = appSettings || {}; }
  document.getElementById('settings-colors').value = _globalShingleColors().join('\n');
  document.getElementById('settings-waste').value  = _globalWastePct();
  if (_meCanViewAll()) {
    document.getElementById('settings-gbb').classList.remove('hidden');
    try {
      const r = await fetch('/api/tier-defaults');
      tierDefaults = await r.json() || {};
    } catch {}
    _settingsGbb = JSON.parse(JSON.stringify(tierDefaults || {}));
    _gbbSetTrade(_gbbActiveTrade);
    await _jxOpen();
    await _fastenOpen();
  }
  if (_meIsAdmin()) {
    document.getElementById('settings-company').classList.remove('hidden');
    try {
      const r = await fetch('/api/company-content');
      _fillCompanyContent(await r.json() || {});
    } catch { _fillCompanyContent({}); }
    // Contract & initials defaults — prefill with the effective text (saved
    // global default or, before one exists, the built-in stock text) so the
    // admin edits from what new estimates actually get today.
    document.getElementById('settings-contract').classList.remove('hidden');
    document.getElementById('set-contract-retail').value = globalContract('retail');
    document.getElementById('set-contract-ins').value    = globalContract('insurance');
    document.getElementById('set-initials-retail').value = globalInitialTexts('retail').join('\n');
    document.getElementById('set-initials-ins').value    = globalInitialTexts('insurance').join('\n');
    // Commercial has no stock text of its own — show whatever is saved, and
    // leave it blank when nothing is, so "blank = use retail" stays honest.
    document.getElementById('set-contract-comm').value   = appSettings.contract_commercial || '';
    document.getElementById('set-initials-comm').value   = (appSettings.initials_commercial || []).join('\n');
  }
  document.getElementById('settings-modal').classList.remove('hidden');
}

/* Global G/B/B package content editor (⚙ Settings, manager+). Edits a working
   copy (_settingsGbb) per trade; saveSettings PUTs it to /api/tier-defaults. */
let _settingsGbb = {};
let _gbbActiveTrade = 'roofing';
const GBB_SET_TRADES = RETAIL_TRADE_KEYS;

/* Permit-jurisdiction editor (⚙ Settings, manager+). Edits a working copy
   (_settingsJx) of the whole jurisdictions doc — the CO baseline plus per-entry
   office/pull/phone/url/code_points overrides — and saveSettings PUTs it to
   /api/jurisdictions. Curates existing entries (every CO city + county is
   already seeded); no add/remove needed for normal use. */
let _settingsJx = null;
let _jxEditingId = null;
/* code_items are structured (the Insurance gap check matches on them), but
   managers edit them as one pipe-delimited line each:
     class | label | keyword,keyword | code basis | supplement note
   The key is slugged from the label, which is also how a jurisdiction's item
   overrides the baseline item of the same name. */
const _JX_CLASSES = ['code', 'common', 'conditional'];
function _jxSlug(s) {
  return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'item';
}
function _jxItemsToText(items) {
  return (items || []).map(it => [
    it.class || 'code', it.label || '', (it.match || []).join(', '), it.basis || '', it.note || '',
  ].join(' | ')).join('\n');
}
function _jxParseItems(text) {
  const out = [], seen = new Set();
  String(text || '').split('\n').forEach(line => {
    if (!line.trim()) return;
    const p = line.split('|').map(s => s.trim());
    const cls = _JX_CLASSES.includes((p[0] || '').toLowerCase()) ? p[0].toLowerCase() : 'code';
    const label = p[1] || '';
    if (!label) return;
    let key = _jxSlug(label), n = 2;
    while (seen.has(key)) key = _jxSlug(label) + '_' + n++;
    seen.add(key);
    out.push({ key, class: cls, label,
               match: (p[2] || '').split(',').map(s => s.trim().toLowerCase()).filter(Boolean),
               basis: p[3] || '', note: p[4] || '' });
  });
  return out;
}
/* ── Fastening table editor (⚙ Settings, manager+) ──────────────────────
   Edits a working copy; saveSettings PUTs it to /api/commercial-fastening.
   Density rows are one pipe-delimited line each, same shape as the
   jurisdiction code_items editor:
     psf | label | insul f,p,c | seam f_w,f_sp | p_w,p_sp | c_w,c_sp   */
let _settingsFasten = null;
function _fastenRowsToText(ratings) {
  return Object.keys(ratings || {})
    .filter(k => /^-?\d+$/.test(k)).map(Number).sort((a, b) => a - b)
    .map(k => {
      const r = ratings[String(k)] || {}, d = r.insul_per_board || {}, s = r.seam || {};
      const pair = z => `${(s[z] || {}).sheet_width_ft || ''},${(s[z] || {}).spacing_in || ''}`;
      return [k, r.label || '', `${d.field || ''},${d.perimeter || ''},${d.corner || ''}`,
              pair('field'), pair('perimeter'), pair('corner')].join(' | ');
    }).join('\n');
}
function _fastenParseRows(text) {
  const out = {};
  String(text || '').split('\n').forEach(line => {
    if (!line.trim()) return;
    const p = line.split('|').map(s => s.trim());
    const psf = parseInt(p[0], 10);
    if (!psf || psf <= 0) return;                       // unparseable row is dropped, not guessed
    const trio = (p[2] || '').split(',').map(s => parseFloat(s) || 0);
    const pair = i => {
      const q = (p[i] || '').split(',').map(s => parseFloat(s) || 0);
      return { sheet_width_ft: q[0] || 0, spacing_in: q[1] || 0 };
    };
    out[String(psf)] = {
      label: p[1] || (psf + ' psf'),
      insul_per_board: { field: trio[0] || 0, perimeter: trio[1] || 0, corner: trio[2] || 0 },
      seam: { field: pair(3), perimeter: pair(4), corner: pair(5) },
    };
  });
  return out;
}
async function _fastenOpen() {
  document.getElementById('settings-fastening').classList.remove('hidden');
  try {
    _settingsFasten = await (await fetch('/api/commercial-fastening')).json();
  } catch { _settingsFasten = { zone_rule:{}, ratings:{}, board_sf:32, waste_pct:5 }; }
  const t = _settingsFasten, zr = t.zone_rule || (t.zone_rule = {});
  document.getElementById('fastenset-note').textContent = t.source_note || '';
  document.getElementById('fastenset-board').value  = t.board_sf ?? 32;
  document.getElementById('fastenset-waste').value  = t.waste_pct ?? 0;
  document.getElementById('fastenset-corner').value = zr.corner_shape || 'L';
  document.getElementById('fastenset-apct').value   = zr.a_pct_least ?? 0.10;
  document.getElementById('fastenset-ahgt').value   = zr.a_pct_height ?? 0.40;
  document.getElementById('fastenset-amin').value   = zr.a_min_pct_least ?? 0.04;
  document.getElementById('fastenset-aminft').value = zr.a_min_ft ?? 3;
  document.getElementById('fastenset-rows').value   = _fastenRowsToText(t.ratings);
}
function _fastenCollect() {
  if (!_settingsFasten) return null;
  const num = (id, dflt) => {
    const v = parseFloat(document.getElementById(id).value);
    return isNaN(v) ? dflt : v;
  };
  const t = _settingsFasten;
  t.board_sf  = num('fastenset-board', 32) || 32;
  t.waste_pct = num('fastenset-waste', 0);
  t.zone_rule = Object.assign({}, t.zone_rule, {
    corner_shape:    document.getElementById('fastenset-corner').value === 'square' ? 'square' : 'L',
    a_pct_least:     num('fastenset-apct', 0.10),
    a_pct_height:    num('fastenset-ahgt', 0.40),
    a_min_pct_least: num('fastenset-amin', 0.04),
    a_min_ft:        num('fastenset-aminft', 3),
  });
  const rows = _fastenParseRows(document.getElementById('fastenset-rows').value);
  // Refuse to save an empty table — it would silently zero every commercial
  // estimate's fastener count.
  if (Object.keys(rows).length) t.ratings = rows;
  return t;
}

async function _jxOpen() {
  document.getElementById('settings-jurisdictions').classList.remove('hidden');
  try {
    _settingsJx = await (await fetch('/api/jurisdictions')).json();
  } catch { _settingsJx = { colorado_baseline:{code_points:[],verify_note:''}, jurisdictions:[] }; }
  if (!_settingsJx.colorado_baseline) _settingsJx.colorado_baseline = { code_points:[], verify_note:'' };
  const b = _settingsJx.colorado_baseline;
  document.getElementById('jxset-baseline-points').value = (b.code_points || []).join('\n');
  document.getElementById('jxset-verify').value = b.verify_note || '';
  document.getElementById('jxset-baseline-items').value = _jxItemsToText(b.code_items);
  _jxEditingId = null;
  document.getElementById('jxset-fields').classList.add('hidden');
  const s = document.getElementById('jxset-search'); if (s) s.value = '';
  _jxFillPicker('');
}
function _jxFillPicker(q) {
  const sel = document.getElementById('jxset-pick');
  if (!sel || !_settingsJx) return;
  q = String(q || '').trim().toLowerCase();
  const list = (_settingsJx.jurisdictions || []).filter(j =>
    !q || j.name.toLowerCase().includes(q) ||
    ((j.match && j.match.cities) || []).some(c => c.toLowerCase().includes(q))).slice(0, 250);
  sel.innerHTML = '<option value="">— pick a jurisdiction to curate —</option>' +
    list.map(j => `<option value="${j.id}" ${j.id === _jxEditingId ? 'selected' : ''}>${esc(j.name)}</option>`).join('');
}
function _jxCollectCurrent() {
  if (!_settingsJx || !_jxEditingId) return;
  const j = (_settingsJx.jurisdictions || []).find(x => x.id === _jxEditingId);
  if (!j) return;
  const v = id => (document.getElementById(id).value || '').trim();
  j.office = v('jxset-office');
  j.pull   = v('jxset-pull');
  j.phone  = v('jxset-phone');
  j.url    = v('jxset-url');
  j.code_points = document.getElementById('jxset-points').value.split('\n').map(s => s.trim()).filter(Boolean);
  const items = _jxParseItems(document.getElementById('jxset-items').value);
  if (items.length) j.code_items = items; else delete j.code_items;
}
function _jxPickEdit(id) {
  _jxCollectCurrent();              // persist edits to the previously picked entry
  _jxEditingId = id || null;
  const box = document.getElementById('jxset-fields');
  const j = id ? (_settingsJx.jurisdictions || []).find(x => x.id === id) : null;
  if (!j) { box.classList.add('hidden'); return; }
  document.getElementById('jxset-office').value = j.office || '';
  document.getElementById('jxset-pull').value   = j.pull || '';
  document.getElementById('jxset-phone').value  = j.phone || '';
  document.getElementById('jxset-url').value    = j.url || '';
  document.getElementById('jxset-points').value = (j.code_points || []).join('\n');
  document.getElementById('jxset-items').value  = _jxItemsToText(j.code_items);
  box.classList.remove('hidden');
}

function _gbbTradeDefaults(trade) {
  if (!_settingsGbb[trade]) _settingsGbb[trade] = {
    descriptions:{good:'',better:'',best:''}, features:{good:[],better:[],best:[]} };
  const d = _settingsGbb[trade];
  if (!d.descriptions) d.descriptions = {good:'',better:'',best:''};
  if (!d.features)     d.features     = {good:[],better:[],best:[]};
  return d;
}
function _gbbCollectActive() {
  const d = _gbbTradeDefaults(_gbbActiveTrade);
  TIERS.forEach(t => {
    const desc = document.getElementById(`gbb-set-desc-${t}`);
    const feat = document.getElementById(`gbb-set-feat-${t}`);
    if (desc) d.descriptions[t] = desc.value.trim();
    if (feat) d.features[t] = feat.value.split('\n').map(s=>s.trim()).filter(Boolean);
  });
}
function _gbbSetTrade(trade) {
  // Keep edits when hopping between product tabs
  if (document.getElementById(`gbb-set-feat-good`)) _gbbCollectActive();
  _gbbActiveTrade = trade;
  const d = _gbbTradeDefaults(trade);
  document.getElementById('gbb-set-tabs').innerHTML = GBB_SET_TRADES.map(t =>
    `<button type="button" class="gbb-set-tab ${t===trade?'active':''}"
       onclick="_gbbSetTrade('${t}')">${TRADE_LABELS[t]}</button>`).join('');
  // Roofing/siding copy now rides on the bundle, which overwrites whatever is
  // set here the moment a rep picks one — say so instead of letting a manager
  // type into a box that gets clobbered.
  const bundleHint = isBundleTrade(trade)
    ? `<div class="gbb-set-hint">${TRADE_LABELS[trade]} packages get their tagline and bullets from the
         <strong>bundle</strong> the rep picks — edit them in Price Book → ${esc(TRADE_LABELS[trade])} → Bundles.
         What's below only shows until a bundle is chosen.</div>`
    : '';
  document.getElementById('gbb-set-body').innerHTML = bundleHint + TIERS.map(t => `
    <div class="cc-block">
      <label class="cc-toggle">${TIER_LABELS[t]}</label>
      <input type="text" id="gbb-set-desc-${t}" class="cc-title"
        placeholder="Short tagline for the ${TIER_LABELS[t]} package…" value="${esc(d.descriptions[t]||'')}">
      <textarea id="gbb-set-feat-${t}" rows="5" spellcheck="false"
        placeholder="One bullet per line — what's included in ${TIER_LABELS[t]}…">${esc((d.features[t]||[]).join('\n'))}</textarea>
    </div>`).join('');
}

/* Company trust content (About/Warranty/Certs/Reviews on the customer link) */
function _fillCompanyContent(cc) {
  const g = id => document.getElementById(id);
  const about = cc.about || {}, warranty = cc.warranty || {},
        certs = cc.certifications || {}, reviews = cc.reviews || {};
  g('cc-about-on').checked    = about.enabled !== false;
  g('cc-about-title').value   = about.title || '';
  g('cc-about-body').value    = about.body || '';
  g('cc-warranty-on').checked  = warranty.enabled !== false;
  g('cc-warranty-title').value = warranty.title || '';
  g('cc-warranty-body').value  = warranty.body || '';
  g('cc-certs-on').checked    = certs.enabled !== false;
  g('cc-certs-title').value   = certs.title || '';
  g('cc-certs-items').value   = (certs.items || []).join('\n');
  g('cc-reviews-on').checked  = reviews.enabled !== false;
  g('cc-reviews-title').value = reviews.title || '';
  g('cc-reviews-items').value = (reviews.items || [])
    .map(r => `${r.stars || 5} | ${r.name || ''} | ${r.text || ''}`).join('\n');
}

function _collectCompanyContent() {
  const g = id => document.getElementById(id);
  const reviews = g('cc-reviews-items').value.split('\n')
    .map(line => line.trim()).filter(Boolean)
    .map(line => {
      const parts = line.split('|');
      if (parts.length >= 3) {
        const stars = parseInt(parts[0], 10);
        return { stars: isNaN(stars) ? 5 : Math.max(1, Math.min(5, stars)),
                 name: parts[1].trim(), text: parts.slice(2).join('|').trim() };
      }
      return { stars: 5, name: '', text: line };
    })
    .filter(r => r.text);
  return {
    about: { enabled: g('cc-about-on').checked,
             title: g('cc-about-title').value.trim(),
             body:  g('cc-about-body').value.trim() },
    warranty: { enabled: g('cc-warranty-on').checked,
                title: g('cc-warranty-title').value.trim(),
                body:  g('cc-warranty-body').value.trim() },
    certifications: { enabled: g('cc-certs-on').checked,
                      title: g('cc-certs-title').value.trim(),
                      items: g('cc-certs-items').value.split('\n')
                        .map(s => s.trim()).filter(Boolean) },
    reviews: { enabled: g('cc-reviews-on').checked,
               title: g('cc-reviews-title').value.trim(),
               items: reviews },
  };
}
function closeSettings() { document.getElementById('settings-modal').classList.add('hidden'); }
function maybeCloseSettings(e) { if (e.target.id === 'settings-modal') closeSettings(); }

async function testSignatureNotification() {
  const btn = document.getElementById('test-notif-btn');
  if (btn) { btn.textContent = 'Sending…'; btn.disabled = true; }
  try {
    const r = await fetch('/api/test-notification', { method: 'POST' });
    const d = await r.json();
    alert(d.ok
      ? `✓ Test email sent to ${d.sent_to}. Check your inbox — if it doesn't arrive, check Railway logs for the [notify] line.`
      : `✗ Failed to send: ${d.error || 'unknown error'}. Check Railway logs for details.`);
  } catch (e) {
    alert('✗ Network error: ' + e.message);
  } finally {
    if (btn) { btn.textContent = '📧 Test Email Notification'; btn.disabled = false; }
  }
}

async function saveSettings() {
  const colors = document.getElementById('settings-colors').value
    .split('\n').map(s => s.trim()).filter(Boolean);
  const waste = parseFloat(document.getElementById('settings-waste').value);
  appSettings = {
    ...appSettings,
    shingle_colors: colors,
    default_waste_pct: isNaN(waste) ? 10 : waste,
  };
  if (_meIsAdmin()) {
    const lines = id => document.getElementById(id).value.split('\n').map(s => s.trim()).filter(Boolean);
    appSettings.contract_retail    = document.getElementById('set-contract-retail').value.trim();
    appSettings.contract_insurance = document.getElementById('set-contract-ins').value.trim();
    appSettings.initials_retail    = lines('set-initials-retail');
    appSettings.initials_insurance = lines('set-initials-ins');
    appSettings.contract_commercial = document.getElementById('set-contract-comm').value.trim();
    appSettings.initials_commercial = lines('set-initials-comm');
  }
  try {
    const r = await fetch('/api/settings', {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(appSettings),
    });
    if (!r.ok) throw new Error('Save failed');
    // Manager+: save the per-product G/B/B package content alongside
    if (_meCanViewAll()) {
      _gbbCollectActive();
      const rg = await fetch('/api/tier-defaults', {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(_settingsGbb),
      });
      if (!rg.ok) throw new Error('G/B/B package content save failed');
      tierDefaults = JSON.parse(JSON.stringify(_settingsGbb));
      // Permit jurisdictions + code (manager+)
      if (_settingsJx) {
        _jxCollectCurrent();
        const b = _settingsJx.colorado_baseline || (_settingsJx.colorado_baseline = {});
        b.code_points = document.getElementById('jxset-baseline-points').value.split('\n').map(s => s.trim()).filter(Boolean);
        b.verify_note = (document.getElementById('jxset-verify').value || '').trim();
        b.code_items  = _jxParseItems(document.getElementById('jxset-baseline-items').value);
        const rj = await fetch('/api/jurisdictions', {
          method: 'PUT', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(_settingsJx),
        });
        if (!rj.ok) throw new Error('Jurisdiction save failed');
        _jurisdictions = JSON.parse(JSON.stringify(_settingsJx));   // refresh Scope panel source
      }
      const ft = _fastenCollect();
      if (ft) {
        const rf = await fetch('/api/commercial-fastening', {
          method: 'PUT', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(ft),
        });
        if (!rf.ok) throw new Error('Fastening table save failed');
        _fastenTable = JSON.parse(JSON.stringify(ft));   // Scope panel recalculates off this
      }
    }
    // Admin: save the customer-proposal company content alongside
    if (_meIsAdmin()) {
      const r2 = await fetch('/api/company-content', {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(_collectCompanyContent()),
      });
      if (!r2.ok) throw new Error('Company content save failed');
    }
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
  // Pre-send validation — insurance estimates are priced by sections, not
  // retail trades, so check the right side or every insurance send warns.
  const issues = [];
  if (!S.customer.name) issues.push('No customer name entered');
  if (!S.customer.email) issues.push('No customer email address');
  const isIns = (S.estimate_type || 'retail') === 'insurance';
  const hasItems = isIns
    ? (S.trades.insurance.sections || []).some(sec => (sec.items || []).length > 0)
    : RETAIL_TRADE_KEYS
        .some(t => S.trades[t].enabled && (S.trades[t].line_items||[]).length > 0);
  if (!hasItems) issues.push('No line items entered yet');
  const sendTotal = isIns ? insuranceTotal() : selectedTotal();
  if (sendTotal === 0) issues.push('Estimate total is $0');
  if (issues.length) {
    const go = confirm(`⚠ Heads up before sending:\n\n• ${issues.join('\n• ')}\n\nSend anyway?`);
    if (!go) return;
  }

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
    if (!S.status || S.status === 'draft') { S.status = 'sent'; setVal('est-status', 'sent'); renderEstStatusBar(); }
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

  const custEmail = (S.customer && S.customer.email || '').trim();
  document.getElementById('share-modal-body').innerHTML = `
    ${sigBlock}
    ${localhostWarning}
    ${!sig && custEmail ? `
    <button class="btn-primary" id="share-email-btn" style="width:100%;padding:13px;font-size:14px"
      onclick="emailEstimateLink()">
      ✉️ Email link to ${esc(custEmail)}
    </button>` : ''}
    ${navigator.share ? `
    <button class="share-native-btn" onclick="doNativeShare('${esc(fullUrl)}','${esc((S.customer&&S.customer.name)||'')}')">
      📤 Send Link — Text, Email, AirDrop…
    </button>` : ''}
    <div>
      <div class="share-step">${navigator.share ? 'Or copy the link manually' : '1. Copy the customer link'}</div>
      <div class="share-url-row">
        <input id="share-url-input" class="share-url-input" type="text" value="${esc(fullUrl)}" readonly>
        <button class="share-copy-btn" onclick="copyShareUrl()">Copy</button>
      </div>
      <div class="share-hint">Paste this into a text or email — the customer opens it on any phone or computer.</div>
    </div>
    ${!navigator.share ? `<div>
      <div class="share-step">2. Send via text or email</div>
      <div class="share-hint">No app needed on their end. They can review, choose a package, and sign electronically.</div>
    </div>` : ''}
    ${!sig ? `
    <div class="share-status-row">
      <span class="share-status-dot pending"></span>
      <span>Waiting for customer signature</span>
      <button class="btn-secondary" style="margin-left:auto" onclick="checkSignatureStatus()">Check Status</button>
    </div>` : ''}
    ${_meIsAdmin() ? `
    <div class="share-puburl-section">
      <div class="share-puburl-label">Public / ngrok URL override <span style="font-weight:400;opacity:.7">(optional — changes all future share links)</span></div>
      <div class="share-url-row">
        <input id="share-puburl-input" class="share-url-input" type="text"
          placeholder="https://abc123.ngrok-free.app"
          value="${esc(window._serverPublicUrl||'')}">
        <button class="share-copy-btn" style="background:#1a3a5c" onclick="savePublicUrl()">Save</button>
      </div>
    </div>` : ''}
    <div style="text-align:center;display:flex;gap:16px;justify-content:center;flex-wrap:wrap">
      <a href="${esc(relUrl||fullUrl)}" target="_blank" class="share-preview-link">Preview customer view ↗</a>
      <a href="${esc((relUrl||fullUrl).replace('/sign/','/present/'))}" target="_blank"
        class="share-preview-link" style="background:#0e2440;color:#fff;padding:6px 16px;border-radius:6px;text-decoration:none;font-weight:600;font-size:13px">📊 Present on Tablet</a>
    </div>`;

  document.getElementById('share-modal').classList.remove('hidden');
}

/* Open the tablet presentation for this estimate. Shares the same token as
   Send / Sign — presenting an estimate is also "sending" it, so a customer who
   watches the walkthrough can sign from the same link afterwards. */
async function presentEstimate() {
  // Grab the tab up front: Safari and iOS block window.open() once an await
  // has broken the click's gesture context, and getting a token needs one.
  const win = window.open('', '_blank');
  try {
    if (!S.estimate_id) {
      await saveEstimate();
      if (!S.estimate_id) { if (win) win.close(); return; }
    }
    if (dirty) await saveEstimate();

    let token = S.share_token;
    if (!token) {
      const r = await fetch(`/api/estimates/${S.estimate_id}/share`, { method: 'POST' });
      if (!r.ok) throw new Error('Could not generate a presentation link');
      const d = await r.json();
      token = d.token;
      if (!token) throw new Error('Could not generate a presentation link');
      S.share_token = token;
      if (!S.sent_at) S.sent_at = new Date().toISOString();
      if (!S.status || S.status === 'draft') { S.status = 'sent'; setVal('est-status', 'sent'); renderEstStatusBar(); }
    }

    const url = `${BASE}/present/${token}`;
    if (win) win.location = url; else window.open(url, '_blank');
  } catch (e) {
    if (win) win.close();
    alert('Error: ' + e.message);
  }
}

async function emailEstimateLink() {
  const btn = document.getElementById('share-email-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Sending…'; }
  try {
    if (dirty) await saveEstimate();
    const r = await fetch(`/api/estimates/${S.estimate_id}/send-email`, { method: 'POST' });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.ok) throw new Error(d.error || `Send failed (${r.status})`);
    if (d.full_url && !S.share_token) {
      // Server just generated the token as part of sending
      S.share_token = d.full_url.split('/sign/')[1] || S.share_token;
    }
    if (!S.status || S.status === 'draft') { S.status = 'sent'; setVal('est-status', 'sent'); renderEstStatusBar(); }
    if (btn) { btn.textContent = `✓ Sent to ${d.sent_to}`; btn.style.background = '#16a34a'; }
  } catch (e) {
    alert('Could not send email: ' + e.message);
    if (btn) { btn.disabled = false; btn.textContent = '✉️ Email link to customer'; }
  }
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
  applyCrmHandoff(S);   // carry the CRM's contact id onto this estimate
  seedTradeBundles('roofing', false); // load the default roofing bundles into the tiers
  activeTrade='roofing'; dirty=false;
  document.getElementById('save-indicator').textContent='';
  document.getElementById('save-indicator').className='save-indicator';
  renderAll(); switchPage('client');
}

async function openEstimate() {
  try{ const r=await fetch('/api/estimates'); showOpenModal(await r.json()); }
  catch{ alert('Failed to load estimate list'); }
}
function showOpenModal(list) {
  if (!_meCanViewAll()) list = list.filter(e => e.salesperson === _loggedInUser);
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
    if(!S.contract_text) S.contract_text=globalContract(_ctype(S.estimate_type));
    if(!S.cover_photo_id) S.cover_photo_id=null;
    if(S.intro_text===undefined) S.intro_text='';
    if(!S.page_visibility) S.page_visibility={intro:false,options:true,products:true,pricing:true,report:true};
    if(S.share_token===undefined) S.share_token=null;
    if(S.signature===undefined) S.signature=null;
    // `trades` can be absent altogether: POST /api/estimates stores the posted
    // JSON as-is (no schema defaulting), so a doc created by an integration —
    // the CRM's start-estimate hand-off — may never have had the key. The
    // per-trade backfill below would restore it, but the insurance migration
    // right here dereferences S.trades first and threw before reaching it.
    if(!S.trades) S.trades={};
    if(!S.trades.insurance) {
      S.trades.insurance={enabled:false,sections:[{id:'sec_'+uid(),name:'',items:[]}],scope_notes:'',claim_number:'',carrier:'',colors:{}};
    } else if(S.trades.insurance.line_items && !S.trades.insurance.sections) {
      S.trades.insurance.sections=[{id:'sec_'+uid(),name:'',items:S.trades.insurance.line_items}];
      delete S.trades.insurance.line_items;
    } else if(!S.trades.insurance.sections) {
      S.trades.insurance.sections=[{id:'sec_'+uid(),name:'',items:[]}];
    }
    // Backfill trades added after this estimate was saved. Seeded from a blank
    // estimate rather than a bare {} so a new trade arrives with its real
    // defaults (commercial must land on mode:'simple', not the 'gbb' fallback).
    const _blankTrades = blankEstimate().trades;
    TRADES.forEach(t=>{
      if(!S.trades[t]) S.trades[t] = _blankTrades[t] || {enabled:false,line_items:[],colors:{}};
      if(!S.trades[t].colors) S.trades[t].colors={};
    });
    if(!S.tier_features) S.tier_features={good:[],better:[],best:[]};
    // Per-trade G/B/B migration: older estimates carry one estimate-level set
    // of package content + one selected_tier. tradeTierContent() moves the
    // content into the first GBB trade; the selection seeds every GBB trade.
    gbbTrades().forEach(t=>{
      tradeTierContent(t);
      if(!TIERS.includes(S.trades[t].selected_tier))
        S.trades[t].selected_tier = TIERS.includes(S.selected_tier)?S.selected_tier:'better';
    });
    applyTierDefaults(S);   // fill any still-empty product content from globals
    // Migrate single global_rate → per-tier rates (all seeded from the old rate)
    if(!S.pricing) S.pricing={mode:'margin',global_rate:35,tier_rates:{good:35,better:35,best:35},per_trade_overrides:{}};
    if(!S.pricing.per_trade_overrides) S.pricing.per_trade_overrides={};
    if(!S.pricing.trade_rates) S.pricing.trade_rates={};
    if(!S.pricing.tier_rates){
      const base=parseFloat(S.pricing.global_rate); const r=isNaN(base)?35:base;
      S.pricing.tier_rates={good:r,better:r,best:r};
    }
    if(!S.roof_health) S.roof_health={condition:'',age_years:'',inspection_date:'',material_type:'',pitch:'',summary:'',findings:[],recommendations:[],report_photo_ids:[]};
    if(!S.estimate_type) S.estimate_type='retail';
    if(!Array.isArray(S.contract_initials)) S.contract_initials=defaultInitials(S.estimate_type);
    if(!S.shingle_selection||typeof S.shingle_selection!=='object')
      S.shingle_selection={enabled:true,options:DEFAULT_SHINGLE_COLORS.slice(),chosen:''};
    if(!Array.isArray(S.shingle_selection.options)||!S.shingle_selection.options.length)
      S.shingle_selection.options=DEFAULT_SHINGLE_COLORS.slice();
    if(!Array.isArray(S.attachments)) S.attachments=[];
    // Buildings. An estimate written before this has none, and every path falls
    // back to S.measurements for it — that is the whole compatibility story.
    // The section sync is belt-and-braces: a structure whose name went missing
    // from its trade's section list would still price (items carry the name)
    // but would stop printing under its own header.
    if(!Array.isArray(S.structures)) S.structures=[];
    TRADES.forEach(t => { if (S.trades[t] && tradeStructures(t).length) syncStructureSections(t); });
    if(!S.work_order || typeof S.work_order !== 'object') S.work_order = {};
    if(!S.roof_certificate || typeof S.roof_certificate !== 'object') S.roof_certificate = {};
    // Estimates created outside the UI (API, scripts) may lack these — a
    // missing photos array used to crash renderCoverPage and abort the load.
    if(!Array.isArray(S.photos)) S.photos=[];
    if(!S.customer||typeof S.customer!=='object') S.customer={name:'',phone:'',email:'',address:{}};
    if(!S.customer.address||typeof S.customer.address!=='object') S.customer.address={};
    if(!S.measurements||typeof S.measurements!=='object') S.measurements={waste_pct:_globalWastePct()};
    if(S.measurements.waste_pct===undefined) S.measurements.waste_pct=_globalWastePct();
    // Migrate old separate ridge_lf / hip_lf into combined ridge_hip_lf.
    // GUARDED on ridge_hip_lf being absent, which is the exact tell for a
    // pre-migration estimate. ridge_lf was later reintroduced as a first-class
    // RIDGES-ONLY field (ridge vent orders sticks off it), so without this guard
    // the migration ran on every single load of every modern estimate: it
    // deleted the imported ridge_lf and overwrote ridge_hip_lf with the
    // ridges-only number. Import 40 LF ridges + 20 LF hips, save, reopen, and
    // the Ridge Vent line silently dropped to 0 sticks while ridge+hip
    // under-billed by the hip footage. Present ridge_hip_lf => already migrated;
    // any ridge_lf beside it is the modern field and must be left alone.
    if(S.measurements.ridge_hip_lf === undefined &&
       (S.measurements.ridge_lf !== undefined || S.measurements.hip_lf !== undefined)) {
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
    const _otherQtyHealed = healOtherZeroQty(S);
    activeTrade='roofing'; closeModal(); setClean();
    if (_otherQtyHealed) setDirty();   // let the 60s autosave persist the fix
    // The label badge's "N other estimates" count reads _custCounts — make
    // sure it's populated regardless of how a rep got here (e.g. "Open"
    // without ever visiting Home/Dashboard first this session).
    if (!_dashData.length) {
      try { const r = await fetch('/api/estimates'); _dashData = await r.json(); rebuildCustCounts(); } catch {}
    }
    renderAll(); switchPage('client');
    warmPrintPhotos();
    backfillPdfPages();
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

/* Photo ids the print view needs baked into the cache. */
function _printNeededIds() {
  const needed = new Set();
  if (S.cover_photo_id) needed.add(S.cover_photo_id);
  (S.photos || []).forEach(p => { if (p.show_in_estimate) needed.add(p.id); });
  return needed;
}

/* True when every photo the print view will reference is already baked —
   the print path can then stay fully synchronous (iOS gesture-safe). */
function _printCacheReady() {
  // PDF attachment pages print as plain <img> URLs (never baked into the
  // cache) — take the waiting path whenever any will be included.
  if ((S.attachments || []).some(a => a.show_in_estimate !== false && (a.pages || []).length))
    return false;
  const byId = new Set((S.photos || []).map(p => p.id));
  for (const id of _printNeededIds()) {
    if (byId.has(id) && !_printPhotoCache[id]) return false;
  }
  return true;
}

/* Fill in `pages` for PDF attachments uploaded before page rendering existed.
   Fire-and-forget on estimate load — the server rasterizes (cached) on demand. */
async function backfillPdfPages() {
  for (const a of (S.attachments || [])) {
    if (a.pages || !a.filename || !/\.pdf$/i.test(a.filename) || !a.filename.includes('/')) continue;
    try {
      const [estId, fname] = a.filename.split('/');
      const r = await fetch(`/api/pdf-pages/${estId}/${fname}`);
      if (!r.ok) continue;
      const d = await r.json();
      if (d.pages && d.pages.length) { a.pages = d.pages; setDirty(); }
    } catch (e) { /* stays a link */ }
  }
}

/* Build data URLs for the cover photo + every "show in estimate" photo,
   baking in any annotations. Safe to call repeatedly (keeps cache warm). */
async function preparePrintPhotos() {
  const needed = _printNeededIds();

  for (const p of (S.photos || [])) {
    if (!needed.has(p.id)) continue;
    try {
      const img = await _loadImage(BASE + '/uploads/' + p.filename);
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

/* Company trust content (About / Warranty / Certifications / Reviews), cached
   for the printed credibility page. buildPrintContent() has to stay fully
   synchronous — iOS Safari only honors window.print() inside the click's
   gesture context — so this is warmed alongside the photos and read from the
   cache at build time. If it never arrived, the page is simply skipped. */
let _ccCache = null;
function warmCompanyContent() {
  return fetch(BASE + '/api/company-content')
    .then(r => r.ok ? r.json() : null)
    .then(cc => { if (cc) _ccCache = cc; })
    .catch(() => {});
}

/* Warm the cache opportunistically (fire-and-forget) so direct Ctrl+P works too.
   The in-flight promise is kept so doPrint can await it on a cold cache. */
let _printWarmPromise = null;
function warmPrintPhotos() {
  if (!_ccCache) warmCompanyContent();
  _ensureJurisdictions();   // permit page reads _jurisdictions synchronously
  _printWarmPromise = preparePrintPhotos().catch(()=>{});
  return _printWarmPromise;
}

/* Source for a photo in print: baked data URL if ready, else raw upload URL */
function printPhotoSrc(photo) {
  return _printPhotoCache[photo.id] || (BASE + '/uploads/' + photo.filename);
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

/* Tiny toast — the app's only transient notification surface. */
let _toastTimer = null;
function toast(msg, ms = 3000) {
  const el = document.getElementById('app-toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(_toastTimer);
  if (ms > 0) _toastTimer = setTimeout(hideToast, ms);
}
function hideToast() {
  const el = document.getElementById('app-toast');
  if (el) el.classList.remove('show');
}

let _printDialogOpened = false;
async function doPrint() {
  // Warm cache → fully synchronous path: iOS Safari only honors window.print()
  // inside the click's gesture context, and warmPrintPhotos() runs proactively
  // on estimate load + every photo change, so this is the common case.
  if (_printCacheReady()) {
    buildPrintContent();
    window.print();
    return;
  }
  // Cold cache (fresh load, photo just added/annotated, slow device): bake the
  // photos and wait for every print <img> to decode BEFORE opening the dialog.
  // Previously this path fell back to raw /uploads/ URLs that hadn't loaded
  // when the dialog opened, printing blank photos until a close-and-retry.
  toast('Preparing photos…', 15000);
  try {
    await (_printWarmPromise || warmPrintPhotos());
    if (!_printCacheReady()) await warmPrintPhotos(); // stale promise from an older photo set
  } catch (e) { /* fall through — fallback URLs still render after the wait */ }
  buildPrintContent();
  await _waitForPrintImages();
  hideToast();
  _printDialogOpened = false;
  try { window.print(); } catch (e) { /* handled by the check below */ }
  // If the deferred print() was ignored (iOS gesture expired), tell the user —
  // the cache is warm now, so their next tap takes the synchronous path.
  setTimeout(() => {
    if (!_printDialogOpened) toast('Photos ready — tap Print / PDF again');
  }, 600);
}
window.addEventListener('beforeprint', () => { _printDialogOpened = true; buildPrintContent(); });
window.addEventListener('afterprint',  ()=>{document.getElementById('print-content').innerHTML='';});

/* One trade's printed rows at ONE package tier. Returns {body, subtotal, sig},
   or body:'' when nothing in this tier is in scope — a tier the rep never built
   must not print an empty table under a package heading.

   `sig` is the rows plus the subtotal, so the caller can tell two packages that
   sell exactly the same scope at exactly the same price from two that differ.
   Pulled out of buildPrintContent so every offered package renders through the
   identical path — the selected tier having its own copy of this markup is how
   the printed unit prices once drifted from the printed subtotal. */
function printTradeBody(trade, tier, o) {
  const td = S.trades[trade] || {};
  const { showLP, tradeMode } = o;
  const inTier = (td.line_items || []).filter(item => {
    if ((parseFloat(item.quantity) || 0) <= 0) return false;
    if (tradeMode === 'simple') return true;
    return (item.tiers?.[tier]?.included) !== false;
  });
  if (!inTier.length) return { body: '', subtotal: 0, sig: '' };

  // Unit sell via lineTotalEffective so the printed prices honor per-tier
  // margins AND locked price overrides — the raw cost/(1-rate) formula here
  // previously priced every tier at the GOOD tier's rate, disagreeing with the
  // printed subtotal.
  const sellOf = item => {
    if (tradeMode === 'simple') return parseFloat(item.unit_price) || 0;
    const q = parseFloat(item.quantity) || 0;
    return q > 0 ? lineTotalEffective(item, tier, trade) / q : 0;
  };
  const rowFor = item => {
    let desc = '', notes = '';
    if (tradeMode === 'simple') { desc = (item.description || '').trim(); }
    else { const t = (item.tiers && item.tiers[tier]) || {}; desc = (t.description || '').trim(); notes = (t.notes || '').trim(); }
    const sell = sellOf(item);
    const tot  = sell * (parseFloat(item.quantity) || 0);
    return `<tr>
      <td>${esc(item.name)}
        ${desc?`<div class="p-desc-sub">${esc(desc).replace(/\n/g,'<br>')}</div>`:''}
        ${notes?`<div class="p-desc-sub" style="font-style:italic;color:#555">${esc(notes).replace(/\n/g,'<br>')}</div>`:''}
      </td>
      <td class="p-right">${item.quantity||0}</td>
      <td>${esc(displayUnit(item))}</td>
      ${showLP?`<td class="p-right">${fmtCur(sell)}</td><td class="p-right">${fmtCur(tot)}</td>`:''}
    </tr>`;
  };
  const hasSections = tradeSections(trade).length > 0;
  const cols = showLP ? 5 : 3;
  const body = groupedTradeItems(trade, inTier).map(g => {
    const rows = g.items.filter(i => i.customer_visible !== false).map(rowFor).join('');
    if (!g.items.length || (!rows && !hasSections)) return '';
    const hd = hasSections?`<tr class="p-section-row"><td colspan="${cols}">${esc(g.name||'General')}</td></tr>`:'';
    // Per-section subtotal (sections only) — includes customer-hidden items so
    // section subtotals sum to the trade subtotal.
    const secTot = g.items.reduce((s,i)=>s+sellOf(i)*(parseFloat(i.quantity)||0),0);
    const sub = hasSections?`<tr class="p-section-sub"><td colspan="${cols-1}" class="p-right">${esc(g.name||'General')} Subtotal</td><td class="p-right">${fmtCur(secTot)}</td></tr>`:'';
    return hd + rows + sub;
  }).join('');
  if (!body) return { body: '', subtotal: 0, sig: '' };
  const subtotal = tradeTotal(trade, tier);
  return { body, subtotal, sig: body + '|' + subtotal.toFixed(2) };
}

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
      <img src="${BASE}/static/logo.png" class="p-header-logo" alt="Project One Roofing">
      <div class="p-company-sub">${esc(COMPANY_ADDR_LINE)}</div>
    </div>
    <div class="p-est-badge"><span class="p-badge-num">${esc(estNum)}</span>${esc(S.estimate_date||'')}</div>
  </div>`;

  /* Section heading: small caps eyebrow over a serif line, with an optional
     orienting sentence. Headings name what the section answers rather than
     what the data is called — "What We Found" beats "Photo Report" on a
     document whose job is to be read by a homeowner, not filed. */
  const pHead2 = (eyebrow, title, lede) =>
    `<div class="p-eyebrow">${esc(eyebrow)}</div>
     <div class="p-h2">${esc(title)}</div>
     ${lede ? `<div class="p-lede">${esc(lede)}</div>` : ''}`;

  // ── Cover page: logo (top) · photo (center) · customer info (bottom) ──
  let html=`<div class="p-cover">
    <div class="p-cover-top">
      <img src="${BASE}/static/logo.png" class="p-cover-toplogo" alt="Project One Roofing">
      <div class="p-cover-tagline">Your Project Estimate</div>
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
      <div class="p-cover-company">${esc(COMPANY_ADDR_LINE)}</div>
    </div>
  </div>`;

  const pv = S.page_visibility || {};

  // ── At a glance — the digest that opens the document ─────────────
  // Sits above the letter deliberately: this is the page a homeowner reads
  // before deciding whether to read the rest, and the one they forward.
  html += _printGlanceHTML(pHeader, estNum);

  // ── Intro letter — always included when text exists ─────────────
  if (S.intro_text?.trim()) {
    html += `<div class="p-intro">
      <div class="p-intro-letterhead">
        <img src="${BASE}/static/logo.png" class="p-intro-logo" alt="Project One Roofing">
      </div>
      <div class="p-intro-body">${esc(S.intro_text)}</div>
    </div>`;
  }

  // ── What we found: photos, then the condition report that reads them ──
  const printPhotos = S.photos.filter(p => p.show_in_estimate && p.id !== S.cover_photo_id);
  if (printPhotos.length)
    html += `<div class="p-photos-page">
      ${pHeader}
      ${pHead2('Inspection', 'What We Found',
               'Photographs taken during your inspection. The report that follows explains what they show.')}
      <div class="p-photo-grid">
        ${printPhotos.map(p=>`<figure class="p-photo-fig">
          <img src="${printPhotoSrc(p)}" alt="${esc(p.caption)}">
          ${p.caption?`<figcaption>${esc(p.caption)}</figcaption>`:''}
        </figure>`).join('')}
      </div>
    </div>`;

  // ── Condition report — right after the photos it refers to ──────
  html += _printConditionHTML(pHeader);

  // ── Attached documents (uploaded PDFs) — every page printed in full ──
  (S.attachments || [])
    .filter(a => a.show_in_estimate !== false && (a.pages || []).length)
    .forEach(a => {
      const label = (a.label || a.original_name || 'Document').trim();
      html += `<div class="p-att-doc">
        ${pHeader}
        <h2 class="p-photos-title">${esc(label)}</h2>
        ${a.pages.map((pg, i) =>
          `<img class="p-att-page" src="${BASE}/uploads/${esc(pg)}" alt="${esc(label)} — page ${i + 1}">`).join('')}
      </div>`;
    });

  // ── Inner pages (Pricing) — skipped when "Pricing" toggle is off ─
  // Build into a local variable; only append if pricing is enabled.
  // Using an IIFE makes the gate unambiguous regardless of block structure.
  html += (pv.pricing === false) ? '' : (()=>{
  let ph='';
  ph+=`<div class="p-page">
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

  // Product Selection — brand/model/color choices per trade (shingle color,
  // drip edge, gutter color, etc.) — only prints when the toggle is on AND
  // at least one field is actually filled in.
  if (pv.products !== false) {
    const prodRows = [];
    TRADES.filter(t => t !== 'insurance').forEach(trade => {
      const td = S.trades[trade];
      if (!td || !td.enabled) return;
      (TRADE_COLOR_FIELDS[trade]||[]).forEach(f => {
        const v = ((td.colors||{})[f.key] || '').toString().trim();
        if (v) prodRows.push({ trade, label: f.label, value: v });
      });
    });
    if (prodRows.length) {
      ph += `<div class="p-products">
        ${pHead2('Specification', 'The Materials We’ll Use',
                 'The exact products and colors selected for your home.')}
        <table class="p-products-table"><tbody>
          ${prodRows.map(r => `<tr>
            <td class="p-products-trade">${esc(TRADE_LABELS[r.trade])}</td>
            <td class="p-products-label">${esc(r.label)}</td>
            <td class="p-products-value">${esc(r.value)}</td>
          </tr>`).join('')}
        </tbody></table>
      </div>`;
    }
  }

  // Per-trade package bullets: the rep-curated features, falling back to an
  // auto-detected list built from that trade's line items.
  const tradeDisplayItems=trade=>{
    const td=S.trades[trade];
    const content=tradeTierContent(trade);
    const out={};
    TIERS.forEach(t=>{
      const f=(content.features||{})[t];
      if(f&&f.length&&!tierBulletsAreStale(trade,t)){out[t]=f;return;}
      const items=[];
      const tradeMode=effectiveTradeMode(trade, td);
      (td.line_items||[]).forEach(item=>{
        if((parseFloat(item.quantity)||0)<=0)return;
        if(tradeMode==='simple'){
          items.push(item.description?`${item.name} — ${item.description}`:item.name);
          return;
        }
        const ti=(item.tiers||{})[t]||{};
        if(ti.included===false)return;  // excluded from this package tier
        items.push(ti.description?`${item.name} — ${ti.description}`:item.name);
      });
      out[t]=items;
    });
    return out;
  };

  const estType = S.estimate_type || 'retail';

  // Detect whether all enabled trades with items are in simple mode.
  // If so, suppress the G/B/B options comparison and tier banner entirely.
  const isAllSimple = RETAIL_TRADE_KEYS.every(t => {
    const td = S.trades[t];
    if (!td || !td.enabled || !(td.line_items||[]).length) return true;
    return effectiveTradeMode(t, td) === 'simple';
  });

  // The condition report is the whole estimate — see isReportOnly(). Its
  // recommended repairs are the scope AND the price; printing the package
  // comparison here would offer three $0 columns beside a report that has just
  // quoted the repairs.
  if (isReportOnly()) {
    ph += _printRepairsHTML(pHead2);
  }
  // Everything that isn't an insurance claim prints the same priced body.
  // This MUST NOT be `=== 'retail'`: a new estimate type would silently print
  // a blank PDF, which is exactly what happened to commercial before this.
  else if (estType !== 'insurance') {
    if (pv.options !== false && !isAllSimple) packageTrades().forEach(gt=>{
      const gtTier=tradeTier(gt);
      const disp=tradeDisplayItems(gt);
      const content=tradeTierContent(gt);
      const multi=packageTrades().length>1;
      ph+=`<div class="p-pkg-comparison">
      ${pHead2(multi ? TRADE_LABELS[gt] : 'Your Options',
               multi ? 'Choose Your ' + TRADE_LABELS[gt] + ' Package' : 'Choose Your Package',
               'Every package below is a complete job. They differ in materials and coverage, not in workmanship.')}
      <table class="p-pkg-table"><thead><tr>
        ${enabledTiers().map(t=>`<th class="col-${t} ${t===gtTier?'selected-col':''}">
          ${TIER_LABELS[t]} ${t===gtTier?'<br><span class="p-selected-tag">SELECTED</span>':''}
        </th>`).join('')}
      </tr></thead><tbody><tr>
        ${enabledTiers().map(t=>{
          const tot=tradeTotal(gt,t);
          // The tagline goes stale with the bullets it sits above — it names a
          // system ("Architectural laminate shingle system"), so printing it
          // over a hand-built package is the same lie in one line.
          const desc=tierBulletsAreStale(gt,t)?'':((content.descriptions||{})[t]||'');
          return `<td>
            <span class="p-pkg-price">${fmtCur(tot)}</span>
            ${desc?`<span class="p-pkg-desc">${esc(desc)}</span>`:''}
            ${disp[t].slice(0,10).map(i=>`<span class="p-pkg-item">· ${esc(i)}</span>`).join('')}
            ${disp[t].length>10?`<span class="p-pkg-item" style="color:#aaa">+ ${disp[t].length-10} more…</span>`:''}
          </td>`;
        }).join('')}
      </tr></tbody></table>
    </div>`;
    });
    const _pkgTrades = packageTrades();
    if (!isAllSimple && _pkgTrades.length) ph+=`<div class="p-package-banner">${
      _pkgTrades.length>1
        ? 'Packages: '+_pkgTrades.map(gt=>`${TRADE_LABELS[gt]} — ${TIER_LABELS[tradeTier(gt)]}`).join(' · ')
        : 'Package: '+TIER_LABELS[tradeTier(_pkgTrades[0])]
    }</div>`;
    /* ── Detail tables: ONE PER OFFERED PACKAGE ──────────────────────────
       A G/B/B trade prints every package it offers, not only the selected
       one. Online the customer taps between packages and the page swaps the
       item list underneath; on paper there is nothing to tap, so printing the
       selected tier alone left a Best package priced on the options card with
       its scope nowhere in the document — the customer could read the price of
       the TPO roof but never what it included. Turn it off with the "All
       Packages" print chip to go back to selected-only. */
    const showLP  = pv.linePrices === true;
    const allPkgs = pv.allPackages !== false;
    let multiPkgPrinted = false;
    TRADES.filter(t=>t!=='insurance').forEach(trade=>{
      // Belt-and-braces. doLoadEstimate backfills the full trade skeleton, so
      // this should always be populated — but printing is the last step before
      // a customer sees the document, and a missing key here used to throw and
      // kill Print / PDF outright, with the button appearing to do nothing.
      const td=S.trades[trade];
      if(!td?.enabled||!(td.line_items||[]).length)return;
      const tradeMode=effectiveTradeMode(trade, td);
      const selTier=tradeTier(trade);   // each product prints at ITS chosen package
      const tiers=(tradeMode==='simple'||!allPkgs)?[selTier]:enabledTiers();

      const built=tiers.map(t=>Object.assign({tier:t},
        printTradeBody(trade,t,{showLP,tradeMode}))).filter(b=>b.body);
      if(!built.length)return;
      // Collapse packages whose scope AND pricing are identical — three copies
      // of one table is not a comparison, it is three pages of noise.
      const groups=[];
      built.forEach(b=>{
        const last=groups[groups.length-1];
        if(last&&last.sig===b.sig){ last.tiers.push(b.tier); return; }
        groups.push({sig:b.sig,body:b.body,subtotal:b.subtotal,tiers:[b.tier]});
      });
      // One group covering every offered package means the packages don't
      // differ here — title it plainly rather than claiming a choice.
      const uniform=groups.length===1&&groups[0].tiers.length===tiers.length;
      if(!uniform) multiPkgPrinted=true;

      const colors=td.colors||{};
      const colorStr=Object.entries(colors).filter(([,v])=>v).map(([k,v])=>`${cap(k.replace(/_/g,' '))}: ${v}`).join(' · ');
      groups.forEach(g=>{
        const pkg=g.tiers.map(t=>TIER_LABELS[t]).join(' & ')
                 +' Package'+(g.tiers.length>1?'s':'');
        const title=uniform?TRADE_LABELS[trade]:`${TRADE_LABELS[trade]} — ${pkg}`;
        const subLbl=uniform?`${TRADE_LABELS[trade]} Subtotal`:`${pkg} Subtotal`;
        const sel=!uniform&&g.tiers.includes(selTier);
        ph+=`<div class="p-trade${sel?' p-trade-chosen':''}">
        <div class="p-trade-title">${esc(title)}
          ${sel?'<span class="p-trade-tag">SELECTED</span>':''}
          ${colorStr?`<span style="font-size:9pt;font-weight:400;color:#555;margin-left:10pt">· ${esc(colorStr)}</span>`:''}
        </div>
        <table class="p-table"><thead><tr>
          <th>Description</th><th class="p-right">Qty</th><th>Unit</th>
          ${showLP?`<th class="p-right">Unit Price</th><th class="p-right">Total</th>`:''}
        </tr></thead><tbody>${g.body}</tbody><tfoot><tr>
          <td colspan="${showLP?4:2}">${esc(subLbl)}</td>
          <td class="p-right">${fmtCur(g.subtotal)}</td>
        </tr></tfoot></table>
      </div>`;
      });
    });
    // With several packages laid out, an unqualified "Project Total" beside a
    // Best subtotal reads as arithmetic that doesn't add up. Name the package
    // the number is for.
    const totalLbl=multiPkgPrinted
      ? 'Project Total — '+(packageTrades().length>1
          ? packageTrades().map(gt=>`${TRADE_LABELS[gt]} ${TIER_LABELS[tradeTier(gt)]}`).join(' · ')
          : TIER_LABELS[tradeTier(packageTrades()[0]||'roofing')]+' Package')
      : 'Project Total';
    ph+=`<div class="p-grand-total"><span>${esc(totalLbl)}</span><span>${fmtCur(selectedTotal())}</span></div>`;
  } else {
    const insTd=S.trades.insurance;
    const insCarrier=insTd?.carrier?` — ${esc(insTd.carrier)}`:'';
    const insClaimNum=insTd?.claim_number?` &nbsp;·&nbsp; Claim #: ${esc(insTd.claim_number)}`:'';
    ph+=`<div class="p-package-banner">Insurance Estimate${insCarrier}${insClaimNum}</div>`;
    if(insTd?.enabled){
      const sections=insTd.sections||(insTd.line_items?[{name:'',items:insTd.line_items}]:[]);
      const activeSections=sections.filter(s=>(s.items||[]).length>0);
      activeSections.forEach(sec=>{
        const secTotal=(sec.items||[]).reduce((s,it)=>s+(parseFloat(it.acv)||0)+(parseFloat(it.depreciation)||0),0);
        ph+=`<div class="p-trade">`;
        if(sec.name) ph+=`<div class="p-trade-title">${esc(sec.name)}</div>`;
        ph+=`<table class="p-table"><thead><tr>
          <th>Item Name</th><th>Description</th>
          <th class="p-right">ACV</th><th class="p-right">Depreciation</th><th class="p-right">RCV</th>
        </tr></thead><tbody>
          ${(sec.items||[]).map(item=>{
            const acv=parseFloat(item.acv)||0;
            const dep=parseFloat(item.depreciation)||0;
            return `<tr>
              <td>${esc(item.name||'')}</td>
              <td>${esc(item.description||'').replace(/\n/g,'<br>')}</td>
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
        ph+=`<div class="p-grand-total"><span>Insurance Claim Total</span><span>${fmtCur(insuranceTotal())}</span></div>`;
    }
    if(insTd?.scope_notes?.trim())
      ph+=`<div class="p-notes">${pHead2('Scope','Scope of Work')}<p>${esc(insTd.scope_notes)}</p></div>`;
  }
  if(S.notes_customer?.trim())
    ph+=`<div class="p-notes">${pHead2('Additional','Notes')}<p>${esc(S.notes_customer)}</p></div>`;

  ph+=`</div>`;   // close p-page — the priced body ends at the notes

  /* Credibility sits between the number and the signature, which is where the
     objections actually are. Marketing content only, so it stays out of the
     signed hash and the signed PDF (see _cv_trust_blocks in app.py). */
  // The permit page is written for a replacement — it promises the permit is
  // priced into the estimate and lists what a re-roof has to satisfy. On a
  // repair bid that is a claim about work nobody quoted, so it is dropped the
  // same way the customer view drops it. Trust content is about the company,
  // not the scope, and stays.
  if (!isReportOnly()) ph += _printPermitHTML(pHeader, pHead2);
  ph += _printTrustHTML(pHeader, pHead2);

  /* Signing gets its own page. It used to fall wherever the notes happened to
     end, so on a long estimate the customer signed in the gutter beneath the
     last line item. */
  ph+=`<div class="p-page p-sign-page">${pHeader}
    ${pHead2('Agreement', 'Authorization to Proceed',
             'Signing below accepts the scope and pricing in this estimate.')}`;

  // Per-clause initial lines — same statements the online sign form collects,
  // printed with a blank line so a paper copy works for in-person signing too.
  const printInitials = (S.contract_initials||[]).filter(i => (i.text||'').trim());
  if (printInitials.length) {
    ph+=`<div class="p-initials">
      <div class="p-eyebrow">Please initial each item</div>
      ${printInitials.map((it,idx)=>`<div class="p-initial-row">
        <span class="p-initial-num">${idx+1}</span>
        <span class="p-initial-text">${esc(it.text)}</span>
        <div class="p-initial-sig"><div class="p-initial-line"></div><span class="p-initial-cap">Initials</span></div>
      </div>`).join('')}
    </div>`;
  }

  ph+=`<div class="p-signatures">
    <div class="p-sig-block"><div class="p-sig-line"></div><div class="p-sig-label">Homeowner Signature</div>
      <div class="p-sig-date"><div><div class="p-sig-date-line"></div><span>Date</span></div></div></div>
    <div class="p-sig-block"><div class="p-sig-line"></div><div class="p-sig-label">Project One Roofing Representative</div>
      <div class="p-sig-date"><div><div class="p-sig-date-line"></div><span>Date</span></div></div></div>
  </div></div>`; // close p-sign-page
  return ph;
  })(); // end pricing IIFE

  if(S.print_contract!==false&&S.contract_text?.trim())
    html+=`<div class="p-contract">${pHeader}
      ${pHead2('Legal', 'Terms & Conditions')}
      <div class="p-contract-body">${esc(S.contract_text)}</div></div>`;

  document.getElementById('print-content').innerHTML=html;
}

/* ── "At a glance" page ───────────────────────────────────────────────
   Five lines answering what a homeowner asks before reading anything else:
   what are you doing, what are my choices, what does it cost, what backs it,
   and how long do I have to decide. Every row is dropped rather than shown
   empty, so a thin estimate produces a short page instead of a page of
   dashes. Deliberately defensive — a throw in the print path used to make
   the Print / PDF button appear to do nothing at all. */
function _printGlanceHTML(pHeader, estNum) {
  const rows = [];
  try {
    const isIns = (S.estimate_type || 'retail') === 'insurance';
    const c     = S.customer || {};
    const addr  = S.project_address ||
                  [c.address?.street, c.address?.city].filter(Boolean).join(', ');

    if (isIns) {
      const carrier = S.trades.insurance?.carrier;
      rows.push(['Your project',
                 'Insurance claim scope' + (carrier ? ` — <strong>${esc(carrier)}</strong>` : '')]);
    } else {
      const scope = RETAIL_TRADE_KEYS
        .filter(t => { const td = S.trades[t]; return td?.enabled && (td.line_items||[]).length; })
        .map(t => TRADE_LABELS[t]);
      if (scope.length)
        rows.push(['Your project',
                   `<strong>${esc(scope.join(' · '))}</strong>${addr ? ' at ' + esc(addr) : ''}`]);
      else if (isReportOnly())
        rows.push(['Your project',
                   `<strong>Recommended repairs from your inspection</strong>${addr ? ' at ' + esc(addr) : ''}`]);

      // Only claim a choice when one is actually being offered. A report-only
      // estimate has packageTrades() non-empty (Roofing is enabled and empty on
      // every new estimate) and nothing to choose between.
      const gbb = isReportOnly() ? [] : packageTrades();
      const tiers = enabledTiers();
      if (gbb.length && tiers.length > 1)
        rows.push(['Your options',
                   `${esc(tiers.map(t => TIER_LABELS[t]).join(', '))} — each one a complete job, `
                   + 'priced in full on the pages that follow']);

      /* Deliberately NO price here. This page sits ahead of the photographs
         and the condition report, and a number on page 2 invites a decision
         before the customer has seen why the work is needed. The total lives
         after the scope, where it can be judged against something. Mirrors
         _cv_glance_block in app.py. */
    }

    // Warranty headline, from the same company content the sign page uses.
    // Skipped on a report-only estimate: the copy opens "Good and Better
    // packages carry…", so promoting it to the headline of a bid that offers
    // no packages is the estimator putting G/B/B back on the page by editorial
    // choice. The trust page still carries the warranty in full. Mirrors the
    // same suppression in _cv_glance_block.
    const wb = isReportOnly() ? '' : (_ccCache?.warranty?.body || '').trim();
    if (wb && _ccCache?.warranty?.enabled !== false) {
      const first = wb.split(/\n|(?<=\.)\s+/)[0].trim();
      if (first) rows.push(['Backed by', first.length > 190 ? first.slice(0, 187) + '…' : first]);
    }

    const insp = (S.property_condition?.inspection_date || '').trim();
    if (insp)
      rows.push(['Inspected',
                 `<strong>${esc(insp)}</strong> — full condition report follows, with photographs`]);

    if (S.valid_until)
      rows.push(['Pricing held until', `<strong>${esc(S.valid_until)}</strong>`]);
  } catch (e) { return ''; }

  if (!rows.length) return '';
  return `<div class="p-glance">
    ${pHeader}
    <div class="p-eyebrow">Summary</div>
    <div class="p-h2">At a Glance</div>
    <div class="p-lede">Everything below in five lines. The detail follows.</div>
    <div class="p-glance-list">
      ${rows.map(([k, v]) => `<div class="p-glance-row">
        <div class="p-glance-k">${esc(k)}</div>
        <div class="p-glance-v">${v}</div>
      </div>`).join('')}
    </div>
  </div>`;
}

/* ── Credibility page ─────────────────────────────────────────────────
   Warranty, certifications, reviews and the company story — the same
   content the sign link carries, which the printed estimate had no version
   of at all. The paper copy is the one that sits on the counter for a week
   while the homeowner collects other bids, so it is the copy that most needs
   an answer to "why you".

   Reads the cache warmed by warmCompanyContent(); returns '' when it never
   arrived, so print still works offline. Gated per-block by the same
   page_visibility.trust_* flags the sign page uses. */
function _printTrustHTML(pHeader, pHead2) {
  const cc = _ccCache;
  if (!cc) return '';
  const pv = S.page_visibility || {};
  const on = (key, block) =>
    pv['trust_' + key] !== false && block && block.enabled !== false;

  let body = '';

  const wr = cc.warranty || {};
  if (on('warranty', wr) && (wr.body || '').trim())
    body += `<div class="p-trust-block">
      <div class="p-trust-h">${esc(wr.title || 'Our Warranty')}</div>
      <div class="p-trust-body">${wr.body.trim().split(/\n{2,}/)
        .map(p => `<p>${esc(p.trim())}</p>`).join('')}</div>
    </div>`;

  const ct = cc.certifications || {};
  const certs = (ct.items || []).filter(x => (x || '').trim());
  if (on('certifications', ct) && certs.length)
    body += `<div class="p-trust-block">
      <div class="p-trust-h">${esc(ct.title || 'Certifications')}</div>
      <div class="p-trust-certs">${certs
        .map(x => `<div class="p-trust-cert">— ${esc(x.trim())}</div>`).join('')}</div>
    </div>`;

  const rv = cc.reviews || {};
  // Capped at six for the same reason the sign page caps them: a long review
  // list swamps the page it is meant to support.
  const revs = (rv.items || []).filter(r => (r.text || '').trim()).slice(0, 6);
  if (on('reviews', rv) && revs.length)
    body += `<div class="p-trust-block">
      <div class="p-trust-h">${esc(rv.title || 'What Our Customers Say')}</div>
      <div class="p-trust-revs">${revs.map(r => `<div class="p-trust-rev">
        <div class="p-trust-rev-stars">${'★'.repeat(Math.max(1, Math.min(5, parseInt(r.stars, 10) || 5)))}</div>
        <div class="p-trust-rev-text">${esc(r.text.trim())}</div>
        ${r.name ? `<div class="p-trust-rev-name">${esc(r.name)}</div>` : ''}
      </div>`).join('')}</div>
    </div>`;

  const ab = cc.about || {};
  if (on('about', ab) && (ab.body || '').trim())
    body += `<div class="p-trust-block">
      <div class="p-trust-h">${esc(ab.title || 'About Project One Roofing')}</div>
      <div class="p-trust-body">${ab.body.trim().split(/\n{2,}/)
        .map(p => `<p>${esc(p.trim())}</p>`).join('')}</div>
    </div>`;

  if (!body) return '';
  return `<div class="p-trust">${pHeader}
    ${pHead2('Why Project One', 'The Company Behind the Work',
             'Who you are hiring, what stands behind the job, and what your neighbors say.')}
    ${body}</div>`;
}

/* ── Permits & code page ──────────────────────────────────────────────
   Two facts only: who issues the permit for this address, and what that
   office requires of the roof install. The adopted-code citation, amendment
   sources, submittal mechanics and IRC section numbers belong in the
   production packet — in a proposal they bury the part a homeowner can act
   on. Mirrors _cv_permit_block / _code_requirements in app.py.

   Reads the _jurisdictions cache warmed by warmPrintPhotos(). If no
   jurisdiction is matched to the address the page says so rather than passing
   the Colorado statewide baseline off as local. */
function _codeRequirements(jur, base, shingleScope, limit = 10) {
  const out = [], seen = new Set();
  const add = t => {
    t = String(t || '').split(/\s+/).filter(Boolean).join(' ');
    if (!t) return;
    // Key on the first two significant words. The same rule arrives from up
    // to three sources phrased differently; two words collapses those while
    // leaving genuinely different rules distinct. Mirrors _code_requirements.
    const key = t.toLowerCase().replace(/[()::—]/g, ' ')
      .split(/\s+/).filter(Boolean).slice(0, 2).join(' ');
    if (seen.has(key)) return;
    seen.add(key);
    out.push(t);
  };
  if (!shingleScope) return [];
  (jur?.code_points || []).forEach(add);
  const vpRaw = jur?.verified_profile || null;
  const vp = (vpRaw && String(vpRaw.reviewed_at || '').trim()) ? vpRaw : null;
  (vp?.amendments || []).forEach(a => {
    const tx = (a?.text || '').trim();
    const tp = (a?.topic || '').trim();
    if (tx) add(tp ? `${tp}: ${tx}` : tx);
  });
  // Label only — the IRC section number is documentation, not a requirement
  // a homeowner can act on.
  (base?.code_items || []).forEach(ci => add(ci?.label));
  return out.slice(0, limit);
}

function _printPermitHTML(pHeader, pHead2) {
  const pj  = S.permit_jurisdiction || {};
  const eff = (pj.selected_id && _jxById(pj.selected_id)) ? pj.selected_id : pj.auto_id;
  const jur = eff ? _jxById(eff) : null;
  const base = (_jurisdictions && _jurisdictions.colorado_baseline) || {};
  const shingleScope = !!(S.trades?.roofing?.enabled);
  const reqs = _codeRequirements(jur, base, shingleScope);

  if (!reqs.length && !jur) return '';

  let head, lead;
  if (jur) {
    const meta = [jur.office, jur.county ? jur.county + ' County' : '', jur.phone]
      .filter(Boolean).map(esc).join(' · ');
    head = `<div class="p-perm-name">${esc(jur.name)}</div>`
         + (meta ? `<div class="p-perm-meta">${meta}</div>` : '');
    lead = `<div class="p-perm-lead">${esc(jur.name)} issues the permit for this address and
      inspects the finished roof. Everything below is required there — it is priced into your
      estimate, not an add-on.</div>`;
  } else {
    head = `<div class="p-perm-name p-perm-unknown">Permitting authority not yet confirmed</div>`;
    lead = `<div class="p-perm-lead">Colorado has no statewide residential building code — the
      city or county adopts and enforces its own. We confirm the authority for this address
      before pulling the permit, and the permit is included in your price either way.</div>`;
  }

  const reqsHtml = reqs.length
    ? `<div class="p-perm-sub"><div class="p-perm-h">${
        jur ? `Required on your roof in ${esc(jur.name)}` : 'Required on your roof'}</div>
       <ul class="p-perm-list">${reqs.map(r => `<li>${esc(r)}</li>`).join('')}</ul></div>`
    : '';

  return `<div class="p-permit">${pHeader}
    ${pHead2('Permits & code', 'Who Pulls Your Permit',
             'The office that issues the permit for this address, and what it requires.')}
    ${head}${lead}${reqsHtml}</div>`;
}

/* ── The priced body of a report-only estimate ────────────────────────
   Only reached from isReportOnly(). Deliberately just the number: the condition
   report pages above already list every recommendation with its price and total
   them by priority, so an itemized table here would be the same lines a second
   time under a second heading. This is the figure the customer signs against,
   and it is the same pcRepairTotals() the report printed. */
function _printRepairsHTML(pHead2) {
  const t = pcRepairTotals();
  if (!(t.total > 0)) return '';
  const plus = t.anyRange ? '+' : '';
  return `<div class="p-notes">
    ${pHead2('Scope', 'Recommended Repairs',
             'The repairs recommended in the condition report above, priced. '
             + 'Signing below approves that work.')}</div>
  <div class="p-grand-total"><span>Estimated Repair Total</span><span>${fmtCur(t.total)}${plus}</span></div>`;
}

/* ── Condition report print pages ─────────────────────────────────────
   Rendered right after the Photo Report (which now carries ALL photos —
   the report itself stays photo-free). Wording flips between the default
   homeowner voice and HOA/commercial via pc.audience. */
function _printConditionHTML(pHeader){
  const pv2=S.page_visibility||{};
  const pc=S.property_condition||(S.roof_health?.condition?pcGet():null);
  const enabledSections=pc?PC_SECTIONS.filter(s=>pc.sections?.[s.key]?.enabled&&pc.sections[s.key].grade):[];
  if(pv2.report===false || !enabledSections.length) return '';

  const isHoa=pc.audience==='hoa';
  const W={  // audience wording
    title:      isHoa?'Property Condition Report':'Home Condition Report',
    inspector:  isHoa?'Inspector':'Inspected By',
    investment: isHoa?'Estimated Repair Investment':'Estimated Repair Costs',
    signer:     isHoa?'Property Manager / HOA Representative':'Homeowner',
  };

  const cu=S.customer;
  const cityState=[cu.address.city,cu.address.state].filter(Boolean).join(', ');
  const addr=[cu.address.street,cityState].filter(Boolean).join(', ');
  const propName=pc.property_name||(cu.name?(isHoa?`${cu.name} — Property Report`:cu.name):addr)||W.title;

  // Cost totals per priority. Each recommendation carries ONE price now, so
  // these are exact and print without a suffix — that is what lets this report
  // stand as a bid. anyRange brings the '+' back only for estimates saved
  // before the change, whose totals really are a low-end sum.
  // pcRepairTotals() is the one parse (first number only, so a legacy
  // "$500–$1,500" reads its low end and keeps the '+'). It is also what prices
  // a report-only estimate, so the report and the bid can never disagree.
  const _rt=pcRepairTotals();
  const costImmediate=_rt.immediate, costSoon=_rt.soon, costMonitor=_rt.monitor;
  const plus=_rt.anyRange?'+':'';

  // Summary page
  const gradeGrid=enabledSections.map(s=>{
    const sec=pc.sections[s.key]; const g=PC_GRADES.find(x=>x.g===sec.grade)||{color:'#333',bg:'#f5f5f5',label:'—'};
    return `<div class="p-cond-grade-cell">
      <div class="p-cond-grade-lbl">${s.icon} ${s.label}</div>
      <div class="p-cond-grade-letter" style="color:${g.color}">${sec.grade}</div>
      <div class="p-cond-grade-desc" style="color:${g.color}">${g.label}</div>
    </div>`;
  }).join('');

  const costTotal=costImmediate+costSoon+costMonitor;
  const costRows=[[`Immediate repairs (D/F)`,costImmediate],[`Short-term (C grades)`,costSoon],[`Maintenance (B grades)`,costMonitor],[`Estimated Total`,costTotal]];

  let html=`<div class="p-roof-health p-cond-cover">
    ${pHeader}
    <div class="p-cond-title">
      <h2>${W.title}</h2>
      <div class="p-cond-prop">${esc(propName)}</div>
    </div>
    <div class="p-rh-meta">
      <div><label>Address</label><span>${esc(addr||'—')}</span></div>
      <div><label>Contact</label><span>${esc(cu.name||'—')}</span></div>
      <div><label>Inspection Date</label><span>${esc(pc.inspection_date||'—')}</span></div>
      ${S.salesperson?`<div><label>${W.inspector}</label><span>${esc(cap(S.salesperson))}</span></div>`:''}
    </div>
    <h3 class="p-rh-sh" style="margin-top:14pt">Condition Snapshot</h3>
    <div class="p-cond-grade-grid">${gradeGrid}</div>
    ${pc.executive_notes?`<div class="p-rh-summary" style="margin-top:12pt"><strong>Overall Assessment:</strong> ${esc(pc.executive_notes)}</div>`:''}
    ${costTotal>0?`<h3 class="p-rh-sh" style="margin-top:14pt">${W.investment}</h3>
      <table class="p-rh-table p-cond-cost-table">
        ${costRows.filter(([,v])=>v>0).map(([l,v],i)=>`<tr${i===costRows.filter(([,v])=>v>0).length-1?' class="p-cond-total-row"':''}>
          <td>${l}</td><td style="text-align:right;font-weight:${i===costRows.filter(([,v])=>v>0).length-1?800:400}">${fmtCur(v)}${plus}</td>
        </tr>`).join('')}
      </table>`:''}
    <div class="p-rh-footer">This ${W.title} was prepared by Project One Roofing following a visual inspection. Pricing is valid for 30 days from the inspection date. Concealed damage discovered once work begins may require a change order.</div>
  </div>`;

  // Per-section detail pages (photos live in the Photo Report, not here)
  enabledSections.forEach(s=>{
    const sec=pc.sections[s.key];
    const g=PC_GRADES.find(x=>x.g===sec.grade)||{color:'#333',bg:'#f5f5f5',label:'—'};
    const findRows=(sec.findings||[]).filter(f=>f.description||f.area).map(f=>{
      const sev=RH_SEVERITIES.find(sv=>sv.v===f.severity)||{l:f.severity||'',c:'#666'};
      return `<tr><td style="font-weight:600">${esc(f.area||'—')}</td>
        <td><span style="color:${sev.c};font-weight:700">${sev.l}</span></td>
        <td>${esc(f.description||'')}</td></tr>`;
    }).join('');
    const recRows=(sec.recommendations||[]).filter(r=>r.description).map(r=>{
      const pri=RH_PRIORITIES.find(p=>p.v===r.priority)||{l:r.priority||''};
      return `<tr><td><strong>${pri.l}</strong></td><td>${esc(r.description||'')}</td>
        <td style="white-space:nowrap">${esc(r.cost_range||'—')}</td></tr>`;
    }).join('');
    // Roof-specific meta
    const roofMeta=s.key==='roof'&&(sec.material_type||sec.age_years)?`
      <div class="p-rh-meta" style="margin-bottom:10pt">
        ${sec.material_type?`<div><label>Material</label><span>${esc(sec.material_type)}</span></div>`:''}
        ${sec.age_years?`<div><label>Est. Age</label><span>${sec.age_years} years</span></div>`:''}
        ${sec.pitch?`<div><label>Pitch</label><span>${esc(sec.pitch)}</span></div>`:''}
      </div>`:'';
    html+=`<div class="p-roof-health">
      ${pHeader}
      <div class="p-rh-titlebar">
        <h2>${s.icon} ${s.label}</h2>
        <div class="p-rh-badge" style="color:${g.color}">Grade ${sec.grade} — ${g.label}</div>
      </div>
      ${roofMeta}
      ${sec.summary?`<div class="p-rh-summary">${esc(sec.summary)}</div>`:''}
      ${findRows?`<h3 class="p-rh-sh">Findings</h3>
        <table class="p-rh-table"><thead><tr><th>Area</th><th>Severity</th><th>Description</th></tr></thead>
        <tbody>${findRows}</tbody></table>`:''}
      ${recRows?`<h3 class="p-rh-sh">Repair Options &amp; Recommendations</h3>
        <table class="p-rh-table"><thead><tr><th>Priority</th><th>Recommendation</th><th>Est. Cost</th></tr></thead>
        <tbody>${recRows}</tbody></table>`:''}
    </div>`;
  });

  // Signature page
  html+=`<div class="p-roof-health">
    ${pHeader}
    <h2>Sign-off &amp; Acknowledgment</h2>
    <div class="p-rh-summary">This report summarizes the visual inspection of the property listed above. It is prepared for informational purposes and to assist in prioritizing maintenance and repair decisions.</div>
    <div class="p-rh-sig" style="margin-top:40pt">
      <div class="p-sig-block"><div class="p-sig-line"></div><div class="p-sig-label">${W.signer}</div></div>
      <div class="p-sig-block"><div class="p-sig-line"></div><div class="p-sig-label">Project One Roofing Inspector</div></div>
    </div>
    <div class="p-rh-sig">
      <div class="p-sig-block"><div class="p-sig-line"></div><div class="p-sig-label">Date</div></div>
      <div class="p-sig-block"><div class="p-sig-line"></div><div class="p-sig-label">Date</div></div>
    </div>
  </div>`;
  return html;
}

/* ── Helpers ───────────────────────────────────────────────────────── */

function cap(s) {
  if(!s)return'';
  return s.replace(/_/g,' ').replace(/\./g,' ').split(' ')
    .map(w=>w.charAt(0).toUpperCase()+w.slice(1)).join(' ');
}

/* ── Home screen ────────────────────────────────────────────────────── */

async function renderHomePage() {
  const el = document.getElementById('home-content');
  if (!el) return;
  el.innerHTML = '<div class="home-loading">Loading…</div>';
  // Fetch estimates if not already cached
  if (!_dashData.length) {
    try { const r = await fetch('/api/estimates'); _dashData = await r.json(); rebuildCustCounts(); } catch {}
  }
  const myData = _meCanViewAll() ? _dashData : _dashData.filter(e => e.salesperson === _loggedInUser);
  const recent = [...myData]
    .sort((a,b)=>(b.updated_at||'').localeCompare(a.updated_at||''))
    .slice(0, 8);
  const now = Date.now();
  const stale = myData.filter(e=>{
    const st=estStatusOf(e);
    if(st==='sent'&&e.sent_at)          return (now-new Date(e.sent_at).getTime())/86400000>=3;
    if(st==='viewed'&&e.last_viewed_at) return (now-new Date(e.last_viewed_at).getTime())/86400000>=2;
    return false;
  });
  const h = new Date().getHours();
  const name = cap(_loggedInUser||'there');
  const greeting = h<12 ? `Good morning, ${name}` : h<17 ? `Good afternoon, ${name}` : `Good evening, ${name}`;
  const chips = {
    signed: '<span class="dash-chip dash-chip-signed">✓ Signed</span>',
    viewed: '<span class="dash-chip dash-chip-viewed">👀 Viewed</span>',
    sent:   '<span class="dash-chip dash-chip-sent">📤 Sent</span>',
    draft:  '<span class="dash-chip dash-chip-draft">Draft</span>',
    lost:   '<span class="dash-chip dash-chip-lost">✗ Lost</span>',
  };
  el.innerHTML = `
    <img src="${BASE}/static/logo.png" class="home-logo" alt="Project One Roofing">
    <div class="home-greeting">${esc(greeting)}</div>
    ${stale.length ? `<div class="home-followup-alert" onclick="openDashboard()">
      ⚠ ${stale.length} estimate${stale.length!==1?'s':''} need${stale.length===1?'s':''} follow-up</div>` : ''}
    <button class="home-new-btn" onclick="newEstimateAction()">📝 New Estimate</button>
    <div class="home-search-wrap">
      <input type="text" class="home-search-input" id="home-cust-search"
        placeholder="🔍 Search customer by name or address…"
        oninput="homeCustomerSearch(this.value)">
      <div id="home-cust-results" class="home-cust-results hidden"></div>
    </div>
    <div class="home-recents">
      <div class="home-recents-hd">
        <span>Recent Estimates</span>
        <button class="home-dash-link" onclick="openDashboard()">📊 Full Dashboard →</button>
      </div>
      ${recent.length ? recent.map(e=>{
        const st=estStatusOf(e);
        const nEst = custEstimateCount(e.customer_name);
        return `<div class="home-est-row" onclick="doLoadEstimate('${esc(e.estimate_id)}');closeDashboard()">
          <div class="home-est-main">
            <span class="dash-row-name"><strong>${esc(e.customer_name||'(no customer)')}</strong>${
              nEst > 1 ? `<button class="dash-cf-btn"
                title="${nEst} estimates for this customer — open their file"
                onclick="event.stopPropagation();openCustomer('${jsq(e.customer_name)}')">📁 ${nEst}</button>` : ''}</span>
            <small>${[e.estimate_label, [e.city,e.estimate_date].filter(Boolean).join(' · ')].filter(Boolean).map(esc).join(' — ')}</small>
          </div>
          <div class="home-est-side">
            <span class="home-est-total">${fmtCur(e.total||0)}</span>
            ${chips[st]||''}
          </div>
        </div>`;
      }).join('') : '<div class="home-empty">No estimates yet — create your first one above.</div>'}
    </div>`;
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
let _meInfo = null;
let _loginsForced = false;

function _meRole() { return (_meInfo && _meInfo.role) || 'rep'; }
function _meIsAdmin() { return _meRole() === 'admin'; }
function _meCanViewAll() { return _meRole() !== 'rep'; }

function applyRoleGates() {
  const isRep = _meRole() === 'rep';
  // Hide Price Book and app Settings from reps — they just scope and quote
  document.querySelectorAll('.btn-pricebook').forEach(b => b.style.display = isRep ? 'none' : '');
  document.querySelectorAll('.btn-settings').forEach(b => b.style.display = isRep ? 'none' : '');
  const mmPb  = document.getElementById('mm-pricebook');
  const mmSet = document.getElementById('mm-settings');
  if (mmPb)  mmPb.style.display  = isRep ? 'none' : '';
  if (mmSet) mmSet.style.display = isRep ? 'none' : '';
}

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
    // Apply global settings to the fresh blank estimate (it was created
    // before /api/settings resolved, so re-seed the settings-driven fields)
    if (!S.estimate_id) {
      if (S.shingle_selection) S.shingle_selection.options = _globalShingleColors();
      if (S.measurements) S.measurements.waste_pct = _globalWastePct();
      S.contract_text     = globalContract('retail');
      S.contract_initials = defaultInitials('retail');
      seedTradeBundles('roofing', false); // roofing tiers default to their bundles now that the price book is loaded
    }
    const si = await siRes.json();
    window._serverPublicUrl = si.public_url || '';
    window._serverBaseUrl   = si.base_url   || '';
    const me = await meRes.json();
    if (me.username) {
      _loggedInUser = me.username;
      _meInfo = me;
      const badge = document.getElementById('user-badge');
      const nameEl = document.getElementById('user-display-name');
      if (badge) badge.style.display = 'flex';
      if (nameEl) nameEl.textContent = me.display_name;
      // Auto-set salesperson on blank (new) estimate
      if (!S.salesperson) {
        S.salesperson = me.username;
        setVal('salesperson', me.username);
      }
      // Admin set a temporary password — force them to choose their own now.
      if (me.must_change) openLoginsModal(true);
      applyRoleGates();
    }
  } catch {}
  // Apply any saved defaults to the initial blank estimate
  applyTierDefaults(S);
  captureCrmHandoff();
  // Also apply it to the blank estimate that already exists at load. The rep
  // normally clicks New from the home screen (which applies it again), but if
  // any path ever writes into this one instead, the link would otherwise be
  // silently lost — and a missing link is indistinguishable from a job that
  // never came from the CRM.
  applyCrmHandoff(S);
  switchPage('home');   // home screen first — rep must choose New or open existing
});

/* ── CRM handoff ────────────────────────────────────────────────────────
   The sales CRM's "Start estimate" button sends the rep here with
   `?contact=<base44 contact id>&name=<customer>` (salescrm/app.py
   start_estimate). That contact id is the ONLY reliable key linking an
   estimate to its job in The Den — and therefore the only way to compare
   what we bid against what the job actually cost.

   It used to be dropped: `crm_contact_id` existed on S.customer but was
   never assigned, so every estimate saved a null and bid-vs-actual could
   only be attempted by matching customer names, which is how you get
   confident, wrong margin numbers.

   Stashed rather than written straight onto S.customer because the rep
   lands on the home screen and has not started an estimate yet — S is
   replaced when they click New. applyCrmHandoff() puts it on whichever
   estimate they actually create. */
let _crmHandoff = null;

function captureCrmHandoff() {
  try {
    const q = new URLSearchParams(location.search);
    const contact = (q.get('contact') || '').trim();
    const lead    = (q.get('lead') || '').trim();
    if (!contact && !lead) return;
    // The lead id is the half that makes the funnel joinable: without it the
    // estimator can report sent/viewed/signed but nothing can say which door
    // those came from. Either id alone is a valid handoff — a lead that has no
    // Den contact yet still needs its estimate tracked.
    _crmHandoff = { crm_contact_id: contact, crm_lead_id: lead,
                    name: (q.get('name') || '').trim() };
    // Drop the params from the address bar so a refresh, a bookmark, or a
    // shared URL cannot re-attach this contact to a different estimate.
    history.replaceState({}, '', location.pathname);
  } catch { _crmHandoff = null; }
}

function applyCrmHandoff(est) {
  if (!_crmHandoff || !est || !est.customer) return;
  // Never overwrite a link that is already set — an estimate opened from the
  // CRM twice, or a rep who picked the job from the CRM browser in between,
  // already has the right id.
  if (!est.customer.crm_contact_id) {
    est.customer.crm_contact_id = _crmHandoff.crm_contact_id;
    if (!est.customer.name && _crmHandoff.name) est.customer.name = _crmHandoff.name;
  }
  // Tracked separately from the contact id: a rep who picked the Den job by
  // hand has a contact but no lead, and that estimate still belongs to the
  // lead it was started from.
  if (!est.customer.crm_lead_id && _crmHandoff.crm_lead_id) {
    est.customer.crm_lead_id = _crmHandoff.crm_lead_id;
  }
}

/* ── Mobile navigation ─────────────────────────────────────────────── */
function toggleSidebar() {
  document.getElementById('app-layout').classList.toggle('sidebar-open');
}
function closeSidebar() {
  document.getElementById('app-layout').classList.remove('sidebar-open');
}
function toggleMoreMenu() {
  document.getElementById('header-overflow-menu').classList.toggle('open');
}
function closeMoreMenu() {
  document.getElementById('header-overflow-menu').classList.remove('open');
}
document.addEventListener('click', function(e) {
  const menu = document.getElementById('header-overflow-menu');
  const btn  = document.getElementById('more-menu-btn');
  if (menu && menu.classList.contains('open') && !menu.contains(e.target) && e.target !== btn) {
    menu.classList.remove('open');
  }
});

/* ── Passwords & team logins ───────────────────────────────────────── */
function openLoginsModal(forced) {
  _loginsForced = forced === true;
  const m       = document.getElementById('logins-modal');
  const banner  = document.getElementById('logins-forced-banner');
  const closeBtn= document.getElementById('logins-close');
  const title   = document.getElementById('logins-title');
  const teamSec = document.getElementById('team-logins-section');
  banner.style.display   = _loginsForced ? 'block' : 'none';
  closeBtn.style.display = _loginsForced ? 'none' : '';
  title.textContent      = _loginsForced ? '🔑 Set Your Password' : '🔑 Passwords & Logins';
  // Team management is admin-only and hidden while a forced change is pending.
  if (_meInfo && _meInfo.is_admin && !_loginsForced) { teamSec.style.display = ''; renderTeamLogins(); }
  else teamSec.style.display = 'none';
  document.getElementById('my-pw-msg').textContent = '';
  document.getElementById('my-new-pw').value = '';
  document.getElementById('my-new-pw2').value = '';
  m.classList.remove('hidden');
  closeMoreMenu();
}
function closeLoginsModal() {
  if (_loginsForced) return;   // must set a password before continuing
  document.getElementById('logins-modal').classList.add('hidden');
}
function maybeCloseLogins(e) { if (e.target.id === 'logins-modal') closeLoginsModal(); }

async function submitMyPassword() {
  const a   = document.getElementById('my-new-pw').value;
  const b   = document.getElementById('my-new-pw2').value;
  const msg = document.getElementById('my-pw-msg');
  if (a.length < 8) { msg.className = 'logins-msg err'; msg.textContent = 'Choose at least 8 characters.'; return; }
  if (a !== b)      { msg.className = 'logins-msg err'; msg.textContent = 'Passwords do not match.'; return; }
  const r = await fetch('/api/account/password', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ password: a }),
  });
  if (r.ok) {
    msg.className = 'logins-msg ok'; msg.textContent = '✓ Password saved.';
    document.getElementById('my-new-pw').value = '';
    document.getElementById('my-new-pw2').value = '';
    if (_meInfo) _meInfo.must_change = false;
    _loginsForced = false;
    document.getElementById('logins-forced-banner').style.display = 'none';
    document.getElementById('logins-close').style.display = '';
    setTimeout(closeLoginsModal, 700);
  } else {
    const e = await r.json().catch(() => ({}));
    msg.className = 'logins-msg err'; msg.textContent = e.error || 'Could not save password.';
  }
}

const ROLE_LABELS = { admin: 'Admin', manager: 'Manager', rep: 'Rep' };

async function renderTeamLogins() {
  const wrap = document.getElementById('team-logins-list');
  wrap.innerHTML = '<div class="tl-loading">Loading…</div>';
  let users;
  try {
    const r = await fetch('/api/users');
    if (!r.ok) { wrap.innerHTML = '<div class="tl-err">Admin access required.</div>'; return; }
    users = await r.json();
  } catch { wrap.innerHTML = '<div class="tl-err">Could not load team.</div>'; return; }

  const isSelf = u => u.username === _loggedInUser;
  wrap.innerHTML = users.map(u => {
    const status = u.enrolled ? (u.must_change ? 'Temp — not changed' : 'Active') : 'No login yet';
    const stcls  = u.enrolled ? (u.must_change ? 'tl-temp' : 'tl-active') : 'tl-none';
    const roleSel = `<select class="tl-role-select" onchange="adminSetRole('${u.username}',this.value)" ${isSelf(u)?'disabled':''}>
      <option value="rep"     ${u.role==='rep'?'selected':''}>Rep</option>
      <option value="manager" ${u.role==='manager'?'selected':''}>Manager</option>
      <option value="admin"   ${u.role==='admin'?'selected':''}>Admin</option>
    </select>`;
    return `<div class="tl-row">
      <div class="tl-info">
        <span class="tl-name">${esc(u.display_name)}</span>
        <span class="tl-role-badge tl-role-${u.role||'rep'}">${ROLE_LABELS[u.role||'rep']}</span>
        <span class="tl-status ${stcls}" id="tlst-${u.username}">${status}</span>
        ${u.locked ? `<span class="tl-status tl-locked">🔒 Locked ${Math.max(1,Math.round(u.locked/60))}m</span>` : ''}
      </div>
      <div class="tl-contact">
        <input type="text" class="tl-pw" id="tlphone-${u.username}" placeholder="Cell # shown on customer Call/Text buttons" value="${esc(u.phone||'')}" autocomplete="off">
        <input type="email" class="tl-pw" id="tlemail-${u.username}" placeholder="Email override (blank = ${esc(u.username)}@projectoneroofing.com)" value="${esc(u.email||'')}" autocomplete="off">
        <button class="tl-set btn-primary" onclick="adminSaveContact('${u.username}')">Save Contact</button>
      </div>
      <div class="tl-actions">
        ${roleSel}
        <input type="text" class="tl-pw" id="tlpw-${u.username}" placeholder="Temp password" autocomplete="off">
        <button class="tl-gen" onclick="tlGen('${u.username}')" title="Generate a password">🎲</button>
        <button class="tl-set btn-primary" onclick="adminSetPassword('${u.username}')">Set PW</button>
        ${u.locked ? `<button class="tl-set btn-primary" onclick="adminUnlockUser('${u.username}')" title="Clear the failed-login lockout">🔓 Unlock</button>` : ''}
        ${u.enrolled && !isSelf(u) ? `<button class="tl-reset" onclick="adminResetUser('${u.username}')">Clear</button>` : ''}
        ${!isSelf(u) ? `<button class="tl-remove" onclick="adminRemoveMember('${u.username}','${esc(u.display_name)}')">✕</button>` : ''}
      </div>
      <div class="tl-msg" id="tlmsg-${u.username}"></div>
    </div>`;
  }).join('') + `
  <div class="tl-add-form" id="tl-add-form">
    <h4 class="tl-add-title">Add Team Member</h4>
    <div class="tl-add-fields">
      <input type="text" id="tl-add-username" class="tl-pw" placeholder="Username (e.g. ryan)" autocomplete="off" style="flex:1">
      <input type="text" id="tl-add-display"  class="tl-pw" placeholder="Full name" style="flex:2">
      <select id="tl-add-role" class="tl-role-select">
        <option value="rep">Rep</option>
        <option value="manager">Manager</option>
        <option value="admin">Admin</option>
      </select>
      <button class="btn-primary tl-set" onclick="adminAddMember()">Add</button>
    </div>
    <div class="tl-msg" id="tl-add-msg"></div>
  </div>`;
}

async function adminSetRole(username, role) {
  const msg = document.getElementById('tlmsg-' + username);
  const r = await fetch(`/api/users/${username}/set-role`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ role }),
  });
  if (r.ok) {
    if (msg) { msg.className = 'tl-msg ok'; msg.textContent = `✓ Role updated to ${ROLE_LABELS[role]}.`; }
    renderTeamLogins();
  } else {
    const e = await r.json().catch(() => ({}));
    if (msg) { msg.className = 'tl-msg err'; msg.textContent = e.error || 'Could not update role.'; }
  }
}

async function adminAddMember() {
  const username    = (document.getElementById('tl-add-username').value || '').trim().toLowerCase();
  const displayName = (document.getElementById('tl-add-display').value  || '').trim();
  const role        = document.getElementById('tl-add-role').value;
  const msg         = document.getElementById('tl-add-msg');
  if (!username) { msg.className = 'tl-msg err'; msg.textContent = 'Username required.'; return; }
  const r = await fetch('/api/team', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ username, display_name: displayName, role }),
  });
  if (r.ok) {
    document.getElementById('tl-add-username').value = '';
    document.getElementById('tl-add-display').value  = '';
    renderTeamLogins();
  } else {
    const e = await r.json().catch(() => ({}));
    msg.className = 'tl-msg err'; msg.textContent = e.error || 'Could not add member.';
  }
}

async function adminSaveContact(username) {
  const phone = (document.getElementById('tlphone-' + username).value || '').trim();
  const email = (document.getElementById('tlemail-' + username).value || '').trim();
  const msg   = document.getElementById('tlmsg-' + username);
  const r = await fetch(`/api/team/${username}`, {
    method: 'PATCH', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ phone, email }),
  });
  if (r.ok) {
    if (msg) { msg.className = 'tl-msg ok'; msg.textContent = '✓ Contact info saved — shown on their customers\' sign pages.'; }
  } else {
    const e = await r.json().catch(() => ({}));
    if (msg) { msg.className = 'tl-msg err'; msg.textContent = e.error || 'Could not save contact info.'; }
  }
}

async function adminRemoveMember(username, displayName) {
  if (!confirm(`Remove ${displayName || username} from the team? Their login will be cleared.`)) return;
  const r = await fetch(`/api/team/${username}`, { method: 'DELETE' });
  if (r.ok) renderTeamLogins();
  else {
    const e = await r.json().catch(() => ({}));
    alert(e.error || 'Could not remove member.');
  }
}

function tlGen(u) {
  const words = ['roof','hail','shingle','gutter','ridge','eave','storm','peak','nail','flash'];
  const word  = words[Math.floor(Math.random() * words.length)];
  const num   = Math.floor(1000 + Math.random() * 9000);
  const el = document.getElementById('tlpw-' + u);
  if (el) el.value = `${word}-${num}`;
}

async function adminSetPassword(u) {
  const el  = document.getElementById('tlpw-' + u);
  const msg = document.getElementById('tlmsg-' + u);
  const pw  = (el && el.value || '').trim();
  if (pw.length < 6) { if (msg) { msg.className = 'tl-msg err'; msg.textContent = 'At least 6 characters.'; } return; }
  const r = await fetch(`/api/users/${u}/set-password`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ password: pw }),
  });
  if (r.ok) {
    if (msg) { msg.className = 'tl-msg ok'; msg.textContent = `✓ Temp password: ${pw} — text it to them. They'll set their own on first sign-in.`; }
    if (el) el.value = '';
    const st = document.getElementById('tlst-' + u);
    if (st) { st.textContent = 'Temp set — not changed yet'; st.className = 'tl-status tl-temp'; }
  } else {
    const e = await r.json().catch(() => ({}));
    if (msg) { msg.className = 'tl-msg err'; msg.textContent = e.error || 'Could not set password.'; }
  }
}

async function adminResetUser(u) {
  if (!confirm('Clear this login? They will need a new temporary password before they can sign in.')) return;
  const r = await fetch(`/api/users/${u}/reset`, { method: 'POST' });
  if (r.ok) renderTeamLogins();
}

// Releases a failed-login lockout. No confirm() — this is the fix for someone
// who is currently locked out and waiting, not a destructive action.
async function adminUnlockUser(u) {
  const r = await fetch(`/api/users/${u}/unlock`, { method: 'POST' });
  if (r.ok) renderTeamLogins();
}

// ── RoofR PDF import ─────────────────────────────────────────────────────

let _roofrData = null;
let _roofrFile = null;  // keep reference so we can save it as an attachment on apply

async function importRoofrPdf(input) {
  const file = input.files[0];
  if (!file) return;
  _roofrFile = file;
  input.value = '';
  const fd = new FormData();
  fd.append('file', file);
  let data;
  try {
    const r = await fetch('/api/parse-roofr', { method: 'POST', body: fd });
    data = await r.json();
    if (!r.ok) { alert(data.error || 'Could not parse PDF.'); return; }
  } catch { alert('Network error — could not reach server.'); return; }
  openRoofrModal(data);
}

function openRoofrModal(data) {
  _roofrData = data;
  const m = data.measurements;
  const fmt = (v, unit) => v !== undefined ? `${v} ${unit}` : '—';
  const addrLine = data.address?.street
    ? `${data.address.street}, ${data.address.city}, ${data.address.state} ${data.address.zip}`
    : null;

  document.getElementById('roofr-modal-body').innerHTML = `
    <p style="font-size:13px;color:var(--text-light);margin:0">Review the extracted measurements, then click <strong>Apply</strong> to fill the estimate.</p>
    <div class="roofr-preview">
      <span class="roofr-preview-label">Roof Squares</span>
      <span class="roofr-preview-value">${fmt(m.roof_squares, 'SQ')}</span>
      <span class="roofr-preview-label">Waste %</span>
      <span class="roofr-preview-value">${m.waste_pct !== undefined
        ? `${m.waste_pct}% <span class="roofr-preview-src">recommended by report</span>`
        : `${mnum(S.measurements.waste_pct, _globalWastePct())}% <span class="roofr-preview-src">not in report — keeping yours</span>`}</span>
      ${m.low_slope_squares !== undefined ? `
      <span class="roofr-preview-label">Low Slope ≤2/12 (rolled)</span>
      <span class="roofr-preview-value">${fmt(m.low_slope_squares, 'SQ')}</span>` : ''}
      ${m.steep_squares !== undefined ? `
      <span class="roofr-preview-label">Steep 7/12+</span>
      <span class="roofr-preview-value">${fmt(m.steep_squares, 'SQ')}</span>` : ''}
      <span class="roofr-preview-label">Ridge + Hip</span>
      <span class="roofr-preview-value">${fmt(m.ridge_hip_lf, 'LF')}</span>
      ${m.ridge_lf !== undefined ? `
      <span class="roofr-preview-label">Ridges (ridge vent)</span>
      <span class="roofr-preview-value">${fmt(m.ridge_lf, 'LF')}</span>` : ''}
      <span class="roofr-preview-label">Eaves</span>
      <span class="roofr-preview-value">${fmt(m.eave_lf, 'LF')}</span>
      <span class="roofr-preview-label">Valleys</span>
      <span class="roofr-preview-value">${fmt(m.valley_lf, 'LF')}</span>
      <span class="roofr-preview-label">Rakes</span>
      <span class="roofr-preview-value">${fmt(m.rake_lf, 'LF')}</span>
      <span class="roofr-preview-label">Step Flashing</span>
      <span class="roofr-preview-value">${fmt(m.step_flash_lf, 'LF')}</span>
      <span class="roofr-preview-label">Gutters (eave run)</span>
      <span class="roofr-preview-value">${fmt(m.gutter_lf, 'LF')}</span>
      ${addrLine ? `<span class="roofr-preview-addr">📍 ${esc(addrLine)}</span>` : ''}
    </div>
    <div class="roofr-modal-footer">
      <button class="btn-secondary" onclick="closeRoofrModal()">Cancel</button>
      <button class="btn-primary" onclick="applyRoofrImport()">✓ Apply to Estimate</button>
    </div>`;

  document.getElementById('roofr-modal').classList.remove('hidden');
}

function closeRoofrModal() {
  document.getElementById('roofr-modal').classList.add('hidden');
  _roofrData = null;
}

function maybeCloseRoofrModal(e) {
  if (e.target === document.getElementById('roofr-modal')) closeRoofrModal();
}

async function applyRoofrImport() {
  if (!_roofrData) return;
  const data = _roofrData;

  Object.assign(S.measurements, data.measurements);

  const a = S.customer.address;
  if (!a.street && data.address?.street) {
    Object.assign(a, data.address);
    setVal('cust-street', a.street);
    setVal('cust-city',   a.city);
    setVal('cust-state',  a.state);
    setVal('cust-zip',    a.zip);
  }

  if (!templates) {
    try { const r = await fetch('/api/templates'); templates = await r.json(); }
    catch { alert('Failed to load price book templates.'); return; }
  }

  S.trades.roofing.enabled = true;
  // Always rebuild from the roofing bundles on RoofR import — ensures tier
  // products/prices are current. If items already exist, confirm before overwriting.
  const hasExisting = (S.trades.roofing.line_items||[]).length > 0;
  if (!hasExisting || confirm('Rebuild roofing items from your price book? This reloads the Good/Better/Best bundles from your current price book settings.')) {
    buildBundleDefaults('roofing');
  }

  applyMeasurements();
  switchPage('pricing');
  renderAll();
  setDirty();

  // Save the RoofR PDF as an attachment so it lives in the customer file.
  // We save the estimate first (gets an ID), then upload the PDF.
  if (_roofrFile) {
    const file = _roofrFile;
    _roofrFile = null;
    try {
      await saveEstimate();
      if (S.estimate_id) {
        const ufd = new FormData();
        ufd.append('file', file);
        const ur = await fetch(`/api/uploads/${S.estimate_id}`, { method: 'POST', body: ufd });
        if (ur.ok) {
          const ures = await ur.json();
          if (!Array.isArray(S.attachments)) S.attachments = [];
          S.attachments.push({
            id: uid(), filename: ures.filename, original_name: file.name,
            label: 'RoofR Measurement Report', show_in_estimate: true,
            pages: ures.pages || undefined,
          });
          setDirty();
        }
      }
    } catch(e) { console.warn('Could not save RoofR PDF attachment:', e); }
  }

  closeRoofrModal();
}

// ── Insurance (Xactimate) carrier-estimate PDF import ────────────────────
// Parse the carrier's estimate server-side, review it in a modal where the
// rep unchecks lines we won't perform, then load the included lines into the
// existing insurance sections model — the contract renders from that as-is.

let _xactData = null;
let _xactFile = null;      // saved as an attachment on apply
let _xactExcluded = new Set();   // "si:ii" keys of excluded lines

async function importXactPdf(input) {
  const file = input.files[0];
  if (!file) return;
  _xactFile = file;
  input.value = '';
  const fd = new FormData();
  fd.append('file', file);
  let data;
  try {
    const r = await fetch('/api/parse-xactimate', { method: 'POST', body: fd });
    data = await r.json();
    if (!r.ok) { alert(data.error || 'Could not parse PDF.'); return; }
  } catch { alert('Network error — could not reach server.'); return; }
  openXactModal(data);
}

function openXactModal(data) {
  _xactData = data;
  _xactExcluded = new Set();
  const meta = data.meta || {};
  const addr = data.address || {};
  const sum  = data.summary || {};
  const addrLine = addr.street ? `${addr.street}, ${addr.city}, ${addr.state} ${addr.zip}` : null;

  const metaRow = (label, val) => val
    ? `<span class="xact-meta-label">${label}</span><span class="xact-meta-value">${esc(val)}</span>` : '';

  const sectionsHtml = (data.sections || []).map((sec, si) => {
    const rows = sec.items.map((it, ii) => `
      <tr class="xact-line-row" data-key="${si}:${ii}">
        <td class="xact-check-cell"><input type="checkbox" checked
          onchange="xactToggleLine(${si},${ii},this.checked)"></td>
        <td class="xact-name-cell">${esc(it.description)}</td>
        <td class="xact-qty-cell">${it.qty} ${esc(it.unit)} @ ${fmtCur(it.unit_price)}</td>
        <td class="xact-num-cell">${fmtCur(it.acv)}</td>
        <td class="xact-num-cell">${fmtCur(it.depreciation)}</td>
        <td class="xact-num-cell xact-rcv-cell">${fmtCur(it.rcv)}</td>
      </tr>`).join('');
    return `
      <div class="xact-section">
        <div class="xact-section-hd">
          <input type="checkbox" checked id="xact-sec-cb-${si}"
            onchange="xactToggleSection(${si},this.checked)" title="Include / exclude entire section">
          <input type="text" class="xact-section-name" value="${esc(sec.name || '')}"
            onchange="_xactData.sections[${si}].name=this.value">
        </div>
        <div class="other-table-wrap">
          <table class="other-table xact-table">
            <thead><tr>
              <th style="width:34px"></th><th>Line Item</th><th>Qty</th>
              <th class="other-th-price">ACV</th><th class="other-th-price">Depreciation</th>
              <th class="other-th-price">RCV</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>`;
  }).join('');

  // Symbility and Xactimate parse to the same shape, so only labels differ.
  // The detected format is shown because it is worth knowing which parser
  // produced the lines when a review looks wrong.
  const isSym = data.format === 'symbility';

  // Say up front whether the carrier's roof figures will be used, so the rep
  // is not left wondering why the Measurements page did or didn't change.
  // The rule itself lives in applyXactImport.
  const meas = data.measurements || {};
  const measHeld = mnum((S.measurements || {}).roof_squares) > 0;
  const measBits = [
    meas.roof_squares !== undefined ? `${meas.roof_squares} SQ roof` : '',
    meas.eave_lf     !== undefined ? `${meas.eave_lf} LF eaves`      : '',
    meas.ridge_lf    !== undefined ? `${meas.ridge_lf} LF ridge`     : '',
  ].filter(Boolean).join(' · ');

  document.getElementById('xact-modal-body').innerHTML = `
    <div class="xact-meta-card">
      ${metaRow('Format', isSym ? 'Symbility' : 'Xactimate')}
      ${metaRow('Carrier', meta.carrier)}
      ${metaRow('Claim #', meta.claim_number)}
      ${metaRow('Insured', meta.insured)}
      ${metaRow('Property', addrLine)}
      ${metaRow('Type of Loss', meta.type_of_loss)}
      ${metaRow('Adjuster', meta.adjuster)}
      ${metaRow(isSym ? 'Pricing Database' : 'Price List', meta.price_list)}
    </div>
    ${(data.warnings || []).length ? `<div class="xact-warn">⚠ ${data.warnings.map(esc).join('<br>')}</div>` : ''}
    ${measBits ? `<div class="xact-claim-note">📐 Roof measurements in this PDF: ${esc(measBits)} — ${
      measHeld
        ? 'left out. This estimate already has measurements, and those win.'
        : 'these will fill the Measurements page, which is currently empty.'}</div>` : ''}
    <p class="xact-hint">Uncheck any lines <strong>we will not be doing</strong> — they are dropped from the contract entirely.</p>
    ${sectionsHtml}
    <div class="xact-footer">
      <div class="xact-footer-totals" id="xact-footer-totals"></div>
      <div class="xact-footer-btns">
        <button class="btn-secondary" onclick="closeXactModal()">Cancel</button>
        <button class="btn-primary" onclick="applyXactImport()">✓ Load Into Estimate</button>
      </div>
    </div>
    ${sum.deductible !== undefined || sum.net_claim !== undefined ? `
    <div class="xact-claim-note">Claim summary: ${[
      sum.deductible !== undefined ? `deductible ${fmtCur(sum.deductible)}` : '',
      sum.net_claim !== undefined ? `net claim ${fmtCur(sum.net_claim)}` : '',
      sum.recoverable_depreciation !== undefined ? `recoverable depreciation ${fmtCur(sum.recoverable_depreciation)}` : '',
    ].filter(Boolean).join(' · ')} — saved to the estimate for internal reference.</div>` : ''}`;

  xactUpdateTotals();
  document.getElementById('xact-modal').classList.remove('hidden');
}

function xactToggleLine(si, ii, on) {
  const key = `${si}:${ii}`;
  if (on) _xactExcluded.delete(key); else _xactExcluded.add(key);
  const row = document.querySelector(`.xact-line-row[data-key="${si}:${ii}"]`);
  if (row) row.classList.toggle('xact-excluded', !on);
  xactUpdateTotals();
}

function xactToggleSection(si, on) {
  (_xactData.sections[si].items || []).forEach((_, ii) => {
    const key = `${si}:${ii}`;
    if (on) _xactExcluded.delete(key); else _xactExcluded.add(key);
    const row = document.querySelector(`.xact-line-row[data-key="${si}:${ii}"]`);
    if (row) {
      row.classList.toggle('xact-excluded', !on);
      const cb = row.querySelector('input[type="checkbox"]');
      if (cb) cb.checked = on;
    }
  });
  xactUpdateTotals();
}

function xactUpdateTotals() {
  if (!_xactData) return;
  let n = 0, total = 0, rcv = 0, acv = 0, dep = 0;
  (_xactData.sections || []).forEach((sec, si) => (sec.items || []).forEach((it, ii) => {
    total++;
    if (_xactExcluded.has(`${si}:${ii}`)) return;
    n++; rcv += it.rcv; acv += it.acv; dep += it.depreciation;
  }));
  const el = document.getElementById('xact-footer-totals');
  if (el) el.innerHTML =
    `<strong>${n} of ${total} lines</strong> — RCV ${fmtCur(rcv)}` +
    `<span class="xact-footer-sub">(ACV ${fmtCur(acv)} + Depreciation ${fmtCur(dep)})</span>`;
}

function closeXactModal() {
  document.getElementById('xact-modal').classList.add('hidden');
  _xactData = null;
}

function maybeCloseXactModal(e) {
  if (e.target === document.getElementById('xact-modal')) closeXactModal();
}

async function applyXactImport() {
  if (!_xactData) return;
  const data = _xactData;

  const hasExisting = (S.trades.insurance.sections || []).some(s => (s.items || []).length);
  if (hasExisting && !confirm('Replace the current insurance line items with the imported ones?')) return;

  // Real estimate-type toggle: enables the insurance trade, swaps contract
  // text + initials to the insurance defaults, switches to the Pricing page.
  setEstimateType('insurance');

  const newSections = [];
  (data.sections || []).forEach((sec, si) => {
    const items = (sec.items || [])
      .filter((_, ii) => !_xactExcluded.has(`${si}:${ii}`))
      .map(it => ({
        id: 'li_' + uid(),
        name: it.description,
        description: `${it.qty} ${it.unit} @ ${fmtCur(it.unit_price)}`,
        acv: it.acv,
        depreciation: it.depreciation,
      }));
    if (items.length) newSections.push({ id: 'sec_' + uid(), name: sec.name || '', items });
  });
  S.trades.insurance.sections = newSections.length
    ? newSections : [{ id: 'sec_' + uid(), name: '', items: [] }];

  const meta = data.meta || {};
  const td = S.trades.insurance;
  if (!td.carrier && meta.carrier) td.carrier = meta.carrier;
  if (!td.claim_number && meta.claim_number) td.claim_number = meta.claim_number;

  if (!S.customer.name && meta.insured) {
    S.customer.name = meta.insured;
    setVal('cust-name', S.customer.name);
  }
  const a = S.customer.address;
  if (!a.street && data.address?.street) {
    Object.assign(a, data.address);
    setVal('cust-street', a.street);
    setVal('cust-city',   a.city);
    setVal('cust-state',  a.state);
    setVal('cust-zip',    a.zip);
  }

  // The carrier's roof figures are a fallback, never an override. A RoofR (or
  // any other) report is a measured take-off; this is three numbers off the
  // adjuster's diagram. So it is all-or-nothing on roof_squares rather than
  // per-field: filling the gaps around an existing report would blend two
  // sources into one take-off nobody could later account for. Xactimate
  // exports carry no measurements, so this is a no-op for them.
  const carrierMeas = data.measurements || {};
  let carrierMeasUsed = false;
  if (Object.keys(carrierMeas).length && !(mnum((S.measurements || {}).roof_squares) > 0)) {
    if (!S.measurements) S.measurements = {};
    Object.assign(S.measurements, carrierMeas);
    carrierMeasUsed = true;
  }

  // Claim metadata for the internal reference card — never customer-facing.
  const sum = data.summary || {};
  S.insurance_claim = Object.fromEntries(Object.entries({
    policy_number: meta.policy_number,
    type_of_loss:  meta.type_of_loss,
    price_list:    meta.price_list,
    date_of_loss:  meta.date_of_loss,
    adjuster:      meta.adjuster,
    deductible:    sum.deductible,
    net_claim:     sum.net_claim,
    recoverable_depreciation: sum.recoverable_depreciation,
    net_claim_if_recovered:   sum.net_claim_if_recovered,
    // Symbility pays debris removal only once the work is done, so it sits
    // outside ACV — carried through so the card's figures reconcile.
    paid_when_incurred: sum.paid_when_incurred,
    rcv_total:     sum.rcv_total,
    acv_total:     sum.acv_total,
    // Insurance mode replaces the Measurements page with a notice, so figures
    // taken off the carrier PDF would otherwise be invisible until someone
    // switched back to Retail. Flagged here so the claim card can show them.
    measurements_from_carrier: carrierMeasUsed || undefined,
    source:        `${data.format || 'xactimate'}_import`,
    imported_at:   new Date().toISOString(),
  }).filter(([, v]) => v !== undefined));

  renderAll();
  setDirty();

  // Save the carrier PDF as an attachment (hidden from the customer packet
  // by default — flip it on per-estimate in Attachments if ever wanted).
  if (_xactFile) {
    const file = _xactFile;
    _xactFile = null;
    try {
      await saveEstimate();
      if (S.estimate_id) {
        const ufd = new FormData();
        ufd.append('file', file);
        const ur = await fetch(`/api/uploads/${S.estimate_id}`, { method: 'POST', body: ufd });
        if (ur.ok) {
          const ures = await ur.json();
          if (!Array.isArray(S.attachments)) S.attachments = [];
          S.attachments.push({
            id: uid(), filename: ures.filename, original_name: file.name,
            label: 'Insurance Estimate (Carrier)', show_in_estimate: false,
            pages: ures.pages || undefined,
          });
          setDirty();
        }
      }
    } catch(e) { console.warn('Could not save carrier PDF attachment:', e); }
  }

  closeXactModal();
}

/* ── Customer hub (per-client landing page) ────────────────────────────
   The first stop for every job: pick or type the customer, then choose
   a door — 📝 Estimate (opens the estimate tab flow) or 📁 Documents
   (permits, uploads, and future work orders / material orders). The
   estimate tab strip and pricing sidebar stay hidden until the user
   walks through the Estimate door (body.is-client CSS). */

function clientSet(field, v) {
  if (field === 'name') {
    // Mirror the sidebar's rename guard for sent/signed estimates
    if (S.share_token && S.customer.name && S.customer.name.trim() !== v.trim()) {
      const action = S.signature ? 'SIGNED' : 'SENT';
      if (!confirm(
        `⚠ This estimate has already been ${action} to "${S.customer.name}".\n\n` +
        `Changing the name to "${v}" will affect what the customer sees at their link.\n\n` +
        `Use New Estimate to start a fresh estimate for a different customer.`
      )) { renderClientPage(); return; }
    }
    S.customer.name = v;
  }
  else if (field === 'phone')  S.customer.phone = v;
  else if (field === 'email')  S.customer.email = v;
  else S.customer.address[field] = v;
  setDirty(); renderSidebar(); renderCoverPage();
  const hd = document.getElementById('client-hub-name');
  if (hd) hd.textContent = S.customer.name || 'New Customer';
}

let _clientSearchTimer = null;
function clientCrmSearch(q) {
  clearTimeout(_clientSearchTimer);
  const dd = document.getElementById('client-crm-dd');
  if (!q || q.trim().length < 2) { if (dd) dd.classList.add('hidden'); return; }
  _clientSearchTimer = setTimeout(async () => {
    try {
      const r = await fetch(`/api/crm/jobs?q=${encodeURIComponent(q.trim())}`);
      const list = await r.json();
      if (!dd) return;
      dd.innerHTML = list.length ? list.map(p => `
        <div class="crm-result" data-id="${esc(p.id)}">
          <strong>${esc(p.client_name || p.name)}</strong>
          <small>${esc([p.job_number, p.address].filter(Boolean).join(' · '))}</small>
        </div>`).join('')
        : '<div class="crm-no-results">No jobs found</div>';
      dd.querySelectorAll('.crm-result').forEach(el =>
        el.addEventListener('click', () => {
          selectJob(list.find(p => p.id === el.dataset.id));
          renderClientPage();
        }));
      dd.classList.remove('hidden');
    } catch { if (dd) dd.classList.add('hidden'); }
  }, 300);
}

function renderClientPage() {
  const el = document.getElementById('client-content');
  if (!el) return;
  const c = S.customer || {name:'',phone:'',email:'',address:{}};
  const a = c.address || {};
  const inp = (field, val, ph, extra='') =>
    `<input type="text" value="${esc(val || '')}" placeholder="${ph}"
       onchange="clientSet('${field}', this.value)" ${extra}>`;
  const started = !!(S.estimate_id || selectedTotal() > 0 || insuranceTotal() > 0);
  const rows = c.name ? customerEstimateRows(c.name) : [];
  const totalSigned = rows.filter(r => estStatusOf(r.e) === 'signed')
                          .reduce((s, r) => s + (r.e.total || 0), 0);
  const notes = _custNotes.key === custKey(c.name) ? _custNotes.text : '';
  el.innerHTML = `
  <div class="client-hub">
    <div class="client-hub-head">
      <div class="client-hub-avatar">👤</div>
      <div>
        <div class="client-hub-name" id="client-hub-name">${esc(c.name || 'New Customer')}</div>
        <div class="client-hub-sub">${rows.length
          ? esc(rows.length + ' estimate' + (rows.length !== 1 ? 's' : '')) +
            (totalSigned > 0 ? ' · ' + fmtCur(totalSigned) + ' signed' : '')
          : 'No estimates yet'}</div>
      </div>
      <button class="client-open-btn" onclick="switchPage('cover')">
        ${started ? '📝 Open Estimate' : '📝 Start Estimate'} →
      </button>
    </div>

    <div class="panel">
      <div class="pm-lookup">
        <input type="text" id="client-crm-q" placeholder="🔍 Search CRM jobs — name / job # / address…"
          oninput="clientCrmSearch(this.value)" autocomplete="off">
        <div id="client-crm-dd" class="pm-crm-results hidden"></div>
      </div>
      <div class="pm-grid client-grid">
        <div class="field-group pm-span2"><label>Name</label>${inp('name', c.name, 'Customer name')}</div>
        <div class="field-group"><label>Phone</label>${inp('phone', c.phone, '970-555-1234')}</div>
        <div class="field-group pm-span2"><label>Email</label>${inp('email', c.email, 'name@email.com')}</div>
        <div class="field-group"></div>
        <div class="field-group pm-span2"><label>Street</label>${inp('street', a.street, '123 Main St')}</div>
        <div class="field-group"><label>City</label>${inp('city', a.city, 'Loveland')}</div>
        <div class="field-group pm-state"><label>State</label>${inp('state', a.state, 'CO', 'maxlength="2"')}</div>
        <div class="field-group"><label>Zip</label>${inp('zip', a.zip, '80537', 'maxlength="10"')}</div>
      </div>
    </div>

    ${c.name ? `
    <div class="panel">
      <div class="panel-header"><h3>📋 Estimates</h3>
        <button class="doc-upload-btn" onclick="docToggleCreate()">＋ New Estimate</button>
      </div>
      <div id="doc-est-list">${docEstimateListHtml()}</div>
      <div id="doc-create-body" class="cf-create-body" style="display:none">
        <div class="cf-create-fields">
          <div class="field-group">
            <label>Estimate Label <span style="color:var(--danger)">*</span></label>
            <input type="text" id="doc-label-input" placeholder="e.g. Roof – Initial, Siding Quote, Re-roof with Gutters"
              onkeydown="if(event.key==='Enter')docCreateEstimate()">
          </div>
          <div class="field-group">
            <label>Type</label>
            <div class="toggle-row toggle-row-4">
              <button class="toggle-btn active" id="doc-type-retail"     onclick="docSetType('retail')">🏠 Retail</button>
              <button class="toggle-btn"        id="doc-type-insurance"  onclick="docSetType('insurance')">🏛 Insurance</button>
              <button class="toggle-btn"        id="doc-type-commercial" onclick="docSetType('commercial')">🏢 Commercial</button>
              <button class="toggle-btn"        id="doc-type-report"     onclick="docSetType('report')">📋 Report</button>
            </div>
          </div>
        </div>
        <button class="btn-primary" onclick="docCreateEstimate()">Create Estimate</button>
      </div>
    </div>

    <div class="panel">
      <div class="panel-header"><h3>🗒 Notes on ${esc(c.name)}</h3>
        <span id="cf-notes-flash" class="cf-notes-flash"></span>
      </div>
      <textarea id="cf-notes-ta" class="cf-notes-area"
        placeholder="Budget, preferences, HOA contact, follow-up reminders — anything true of the customer rather than one estimate."
        onblur="saveCustomerNotes('${jsq(c.name)}',this.value)">${esc(notes)}</textarea>
    </div>` : ''}
  </div>`;
  // Files, document generators and change orders render into the sibling
  // #documents-content on this same page.
  renderDocumentsPage();
}

/* ── Documents page (per-job document hub) ─────────────────────────────
   Each job's PDF documents in one place: uploads (S.attachments — the
   same list the customer estimate links to) plus generated documents.
   Generators live behind "Create a document" cards so job-specific
   paperwork (like the Loveland permit) only appears when you ask for
   it. Work orders and material order sheets slot in here later as new
   cards — add a card + a form renderer, nothing else changes. */

let _docGenerator = null;   // which generator form is open: 'permit' | null

// ── Documents door as the customer's estimate hub ───────────────────────
// Every estimate for the customer currently loaded, plus the create form
// that used to live in the Customer File modal. Kept separate from that
// modal's own (deleted) `_cf*`/`cf*` create state — `doc`-prefixed ids and
// vars so the modal and this door can never collide if both are mounted.
let _docCreateOpen = false;
let _docCreateType = 'retail';
function docToggleCreate(forceOpen) {
  _docCreateOpen = forceOpen === true ? true : !_docCreateOpen;
  const el = document.getElementById('doc-create-body');
  if (el) el.style.display = _docCreateOpen ? '' : 'none';
  if (_docCreateOpen) document.getElementById('doc-label-input')?.focus();
}

// Driven off ESTIMATE_TYPES, same reason as the modal's old cfSetType: a
// fourth estimate type must not silently miss this dialog again.
function docSetType(t) {
  _docCreateType = ESTIMATE_TYPES.includes(t) ? t : 'retail';
  ESTIMATE_TYPES.forEach(x => {
    const el = document.getElementById('doc-type-' + x);
    if (el) el.classList.toggle('active', x === _docCreateType);
  });
}

async function docCreateEstimate() {
  const labelInput = document.getElementById('doc-label-input');
  const label = (labelInput?.value || '').trim();
  if (!label) { labelInput?.focus(); labelInput?.classList.add('cf-input-error'); return; }
  labelInput?.classList.remove('cf-input-error');
  const name = (S.customer && S.customer.name) || '';
  await newEstimateForCustomer(name, label, _docCreateType);
  _docCreateOpen = false;
  _docCreateType = 'retail';
  // Land back on the customer screen, and do NOT assume we are already there.
  // Two things move underfoot inside newEstimateForCustomer:
  //   * it calls newEstimateAction() first, which renders the customer screen
  //     from a BLANK estimate — before the name is copied across — so without
  //     a re-render the screen reads "No estimates yet" for a customer who
  //     plainly has some;
  //   * setEstimateType('commercial') deliberately navigates to Scope (a flat
  //     roof is driven by the EagleView numbers), so a commercial estimate
  //     ends up on a different page entirely.
  switchPage('client');
}

// The estimate list panel's content: every estimate for the customer
// currently loaded, with whichever one is actually on screen marked current
// and shown FIRST — including when it has never been saved. That unsaved-row
// case is the literal bug being fixed: create estimate #2 for a customer and,
// before the first autosave, it must already be visible and distinguishable
// here, not indistinguishable from a blank new customer.
function docEstimateListHtml() {
  const name = (S.customer || {}).name || '';
  if (!name) return '<p class="pm-hint">Add a customer name to see their estimates here.</p>';
  // Same source the Customer File modal reads, so the two screens can never
  // disagree about what this customer has.
  //
  // Editable here and only here: the Documents door is where a rep manages a
  // customer's estimates. The modal's rows are a click target for loading, and
  // an input inside one would swallow the click that is the point of that list.
  const rows = customerEstimateRows(name)
    .map(r => estRowHtml(r.e, {current: r.current, editable: true}));
  return `<div class="cf-timeline">${rows.join('')}</div>`;
}

// Re-fetches the shared estimate cache and, if the rep is still on Documents
// when it lands, refreshes just the list panel. Only called on navigation
// INTO Documents (from switchPage) — renderDocumentsPage() itself stays
// synchronous off the cache, since it also runs from 11 in-page refresh call
// sites (upload a file, delete one, generate a document) that must not pay
// for a network round-trip just to redraw the attachments panel.
async function refreshDocCustData() {
  try {
    const r = await fetch('/api/estimates');
    _dashData = await r.json();
  } catch { return; }
  rebuildCustCounts();
  if (activePage === 'client') {
    const el = document.getElementById('doc-est-list');
    if (el) el.innerHTML = docEstimateListHtml();
  }
}

function renderDocumentsPage() {
  const el = document.getElementById('documents-content');
  if (!el) return;
  const atts = S.attachments || [];
  const custName = (S.customer||{}).name || '';
  el.innerHTML = `
  <div class="pm-wrap">
    <div class="panel">
      <div class="panel-header"><h3>📎 Files — ${esc(custName || 'this job')}</h3>
        <button class="doc-upload-btn" onclick="document.getElementById('doc-pdf-input').click()">📎 Upload PDF</button>
        <input type="file" id="doc-pdf-input" accept="application/pdf,.pdf" multiple style="display:none"
          onchange="docUploadPdf(this.files)">
      </div>
      ${atts.length ? atts.map(att => {
        const icon = att.doc_type === 'signed_contract'  ? '🖊'
                   : att.doc_type === 'permit_packet'    ? '🏛'
                   : att.doc_type === 'roof_certificate' ? '🏅'
                   : att.server_generated                ? '🛠'
                                                         : '📄';
        // Work orders don't auto-push — the rep fills in scheduled date /
        // dish / tear-off layers first, then clicks "↗ Push to Den".
        // Two internal packet docs now: the crew's work order carries the
        // fill-in form and the Push-to-Den button; the material order is just
        // a file to open.
        const isWO = att.doc_type === 'work_order';
        const isMO = att.doc_type === 'material_order';
        return `
      <div class="att-row">
        <span class="att-icon">${icon}</span>
        <input type="text" class="att-label" value="${esc(att.label||att.original_name||'Document')}"
          onchange="attSetLabel('${att.id}',this.value)" placeholder="Document name">
        ${att.crm_document_id
          ? '<span class="doc-crm-chip" title="Filed in the CRM under this job">✓ CRM</span>'
          : (isWO
              ? `<button class="doc-crm-push" onclick="pushWorkOrderToCrm()"
                   title="Regenerate with the latest job details and file in Den">↗ Push to Den</button>`
              : isMO
                ? ''
                : `<button class="doc-crm-push" onclick="pushDocToCrm('${att.id}')"
                   title="File this PDF in the CRM under the linked job">↗ CRM</button>`)}
        <label class="att-show" title="Show this document to the customer on their estimate">
          <input type="checkbox" ${att.show_in_estimate!==false?'checked':''}
            onchange="attToggle('${att.id}',this.checked)"> Customer
        </label>
        <a class="att-view" href="${BASE}/uploads/${esc(att.filename)}" target="_blank" rel="noopener">View</a>
        ${att.server_generated
          ? `<span class="doc-crm-chip" title="Auto-generated from the signed contract${isWO ? ' — edit the Work Order fields below then Regenerate' : ''}">auto</span>`
          : `<button class="att-del" onclick="attDelete('${att.id}')" title="Remove">×</button>`}
      </div>
      ${isWO ? renderWorkOrderForm() : ''}`;
      }).join('')
      : '<p class="pm-hint">No documents yet — upload a PDF or create one below.</p>'}
      ${!((S.customer||{}).crm_project_id) && atts.length
        ? '<p class="pm-hint">⚠ Not linked to a CRM job — documents stay local only. Link via the customer search to file them in the CRM.</p>' : ''}
    </div>

    <div class="panel">
      <div class="panel-header"><h3>➕ Create a document</h3></div>
      <div class="doc-cards">
        <button class="doc-card ${_docGenerator==='permit'?'doc-card-active':''}" onclick="docToggleGenerator('permit')">
          <span class="doc-card-icon">🏛</span>
          <span class="doc-card-name">Loveland Permit &amp; Affidavit</span>
          <span class="doc-card-sub">City reroof packet, auto-filled from this job</span>
        </button>
        <button class="doc-card ${_docGenerator==='roofcert'?'doc-card-active':''}" onclick="docToggleGenerator('roofcert')">
          <span class="doc-card-icon">🏅</span>
          <span class="doc-card-name">Roof Certificate</span>
          <span class="doc-card-sub">${atts.some(a => a.server_generated && a.doc_type === 'roof_certificate')
            ? 'Issued — reopen to edit and re-issue'
            : 'Realtor certification + short labor-only warranty'}</span>
        </button>
        ${S.signature ? `
        <button class="doc-card" onclick="generateProductionPacket(this)">
          <span class="doc-card-icon">🛠</span>
          <span class="doc-card-name">Work Order + Material Order</span>
          <span class="doc-card-sub">${atts.some(a => a.server_generated && a.doc_type === 'work_order')
            ? 'Regenerate both (does not push to Den — use ↗ Push to Den)'
            : 'Two documents: the crew work order and the buy list'}</span>
        </button>
        <button class="doc-card" onclick="generatePermitPacket(this)">
          <span class="doc-card-icon">🏛</span>
          <span class="doc-card-name">Permit Application Packet</span>
          <span class="doc-card-sub">${atts.some(a => a.server_generated && a.doc_type === 'permit_packet')
            ? 'Regenerate + push to Den'
            : 'Jurisdiction + squares + materials + cost split'}</span>
        </button>` : `
        <div class="doc-card doc-card-soon" title="Generated from the signed contract once the customer signs">
          <span class="doc-card-icon">🛠</span>
          <span class="doc-card-name">Production Packet</span>
          <span class="doc-card-sub">Available after signing</span>
        </div>
        <div class="doc-card doc-card-soon" title="Generated from the signed contract once the customer signs">
          <span class="doc-card-icon">🏛</span>
          <span class="doc-card-name">Permit Application Packet</span>
          <span class="doc-card-sub">Available after signing</span>
        </div>`}
      </div>
    </div>

    <div id="co-panel"></div>

    <div id="permit-form-container"></div>
    <div id="roofcert-form-container"></div>
  </div>`;
  if (_docGenerator === 'permit')   renderPermitForm();
  if (_docGenerator === 'roofcert') renderRoofCertForm();
  if (S.signature && S.estimate_id) loadChangeOrders();
}

/* ── Change orders (signed addendums on an accepted estimate) ─────────
   Server-authoritative: the estimate's change_orders array can only be
   changed through /api/estimates/<id>/change-orders endpoints — never
   via the full-doc save. COList mirrors the server response. */

let COList = null;      // last-fetched change orders for the open estimate
let _coDraft = null;    // editor state: {id?, title, description, line_items, pricing}

async function loadChangeOrders() {
  try {
    const r = await fetch(`/api/estimates/${S.estimate_id}/change-orders`);
    COList = r.ok ? await r.json() : [];
  } catch { COList = []; }
  renderCOPanel();
}

// MUST mirror _co_line_total in app.py — pricing parity rule (see app.py
// "Shared pricing math" note): the customer-facing CO page is priced
// server-side; this preview must show the rep the same number.
function _coLineTotal(it, pricing) {
  const po = it.price_override;
  if (po !== null && po !== undefined && po !== '' && !isNaN(parseFloat(po))) return parseFloat(po);
  const cost = (parseFloat(it.material_unit_cost) || 0) + (parseFloat(it.labor_unit_cost) || 0);
  const rate = parseFloat(pricing.rate) || 0;
  const unitSell = (pricing.mode || 'margin') === 'margin'
    ? (rate < 100 ? cost / (1 - rate / 100) : 0)
    : cost * (1 + rate / 100);
  return unitSell * (parseFloat(it.quantity) || 0);
}

function _coTotalOf(co) {
  return (co.line_items || []).reduce((s, it) => s + _coLineTotal(it, co.pricing || {}), 0);
}

function _fmtSigned(n) { return (n < 0 ? '-' : '') + fmtCur(Math.abs(n)); }

function renderCOPanel() {
  const el = document.getElementById('co-panel');
  if (!el) return;
  if (!S.signature) { el.innerHTML = ''; return; }
  const list = COList || [];
  const chip = st => ({
    draft:    '<span class="dash-chip dash-chip-draft">Draft</span>',
    sent:     '<span class="dash-chip dash-chip-sent">📤 Sent</span>',
    accepted: '<span class="dash-chip dash-chip-signed">✓ Signed</span>',
    declined: '<span class="dash-chip dash-chip-declined">✗ Declined</span>',
  }[st] || '');
  const rows = list.map(co => {
    const total = co.total != null ? co.total : _coTotalOf(co);
    const acts = [];
    if (co.status === 'draft') {
      acts.push(`<button class="doc-crm-push" onclick="coEdit('${co.id}')">✎ Edit</button>`);
      acts.push(`<button class="doc-crm-push" onclick="coShareLink('${co.id}')" title="Create the signing link and share it">📤 Send</button>`);
      acts.push(`<button class="att-del" onclick="coDelete('${co.id}')" title="Delete draft">×</button>`);
    } else if (co.status === 'sent') {
      acts.push(`<button class="doc-crm-push" onclick="coShareLink('${co.id}')" title="Share the signing link again">📤 Resend</button>`);
      acts.push(`<button class="doc-crm-push" onclick="coSetStatus('${co.id}','draft')" title="Pull it back for edits — the customer's link stops working">↩ Revert</button>`);
      acts.push(`<button class="doc-crm-push" onclick="coSetStatus('${co.id}','declined')">✗ Declined</button>`);
    } else if (co.status === 'declined') {
      acts.push(`<button class="doc-crm-push" onclick="coSetStatus('${co.id}','draft')">↩ Reopen</button>`);
      acts.push(`<button class="att-del" onclick="coDelete('${co.id}')" title="Delete">×</button>`);
    }
    acts.push(`<a class="att-view" href="/api/estimates/${S.estimate_id}/change-orders/${co.id}/pdf" target="_blank" rel="noopener">PDF</a>`);
    return `<div class="att-row">
      <span class="att-icon">±</span>
      <div class="co-row-main">
        <strong>${esc(co.number || 'CO')}${co.title ? ' — ' + esc(co.title) : ''}</strong>
        <small>${co.status === 'sent' && co.view_count ? `Viewed ${co.view_count}× · ` : ''}${co.signature ? 'Signed by ' + esc(co.signature.name || '') : ''}</small>
      </div>
      <span class="dash-total" style="${total < 0 ? 'color:#b91c1c' : ''}">${_fmtSigned(total)}</span>
      ${chip(co.status)} ${acts.join('')}
    </div>`;
  }).join('');
  const signedSum = list.filter(c2 => c2.status === 'accepted')
                        .reduce((s, c2) => s + (c2.total || 0), 0);
  el.innerHTML = `
    <div class="panel">
      <div class="panel-header"><h3>± Change Orders</h3>
        <button class="doc-upload-btn" onclick="coNew()">＋ New Change Order</button>
      </div>
      ${rows || '<p class="pm-hint">No change orders yet — use one any time the scope changes after signing (extra work or a credit). The customer signs it online, just like the estimate.</p>'}
      ${signedSum ? `<p class="pm-hint" style="text-align:right"><strong>${_fmtSigned(signedSum)} in signed change orders → job total ${fmtCur(((S.estimate_type === 'insurance') ? insuranceTotal() : selectedTotal()) + signedSum)}</strong></p>` : ''}
      <div id="co-editor"></div>
    </div>`;
  renderCOEditor();
}

function coNew() {
  const tier = (S.signature && S.signature.selected_tier) || S.selected_tier || 'better';
  _coDraft = {
    id: null, title: '', description: '',
    line_items: [{ name: '', description: '', quantity: 1, unit: 'EA',
                   material_unit_cost: 0, labor_unit_cost: 0, price_override: '' }],
    pricing: { mode: (S.pricing || {}).mode || 'margin',
               rate: tierRate('roofing', tier) },
  };
  renderCOPanel();
}

function coEdit(id) {
  const co = (COList || []).find(c2 => c2.id === id);
  if (!co) return;
  _coDraft = JSON.parse(JSON.stringify(
    { id: co.id, title: co.title || '', description: co.description || '',
      line_items: co.line_items || [], pricing: co.pricing || { mode: 'margin', rate: 35 } }));
  if (!_coDraft.line_items.length) coAddRow(false);
  renderCOPanel();
}

function renderCOEditor() {
  const el = document.getElementById('co-editor');
  if (!el) return;
  if (!_coDraft) { el.innerHTML = ''; return; }
  const d = _coDraft, p = d.pricing || {};
  const rows = (d.line_items || []).map((it, i) => `
    <div class="co-li-row">
      <input type="text" class="co-li-name" placeholder="Item — e.g. Skylight flashing kit" value="${esc(it.name || '')}"
        oninput="coSetItem(${i},'name',this.value)">
      <input type="number" class="co-li-num" placeholder="Qty" step="any" value="${it.quantity ?? ''}"
        oninput="coSetItem(${i},'quantity',this.value)">
      <input type="text" class="co-li-unit" placeholder="Unit" value="${esc(it.unit || '')}"
        oninput="coSetItem(${i},'unit',this.value)">
      <input type="number" class="co-li-num" placeholder="Mat \$" step="any" value="${it.material_unit_cost || ''}"
        oninput="coSetItem(${i},'material_unit_cost',this.value)" title="Material cost per unit">
      <input type="number" class="co-li-num" placeholder="Lab \$" step="any" value="${it.labor_unit_cost || ''}"
        oninput="coSetItem(${i},'labor_unit_cost',this.value)" title="Labor cost per unit">
      <input type="number" class="co-li-num" placeholder="Lock \$" step="any" value="${it.price_override ?? ''}"
        oninput="coSetItem(${i},'price_override',this.value)"
        title="Locked line total — overrides the margin math; negative = credit">
      <span class="co-li-total" id="co-li-total-${i}">${_fmtSigned(_coLineTotal(it, p))}</span>
      <button class="att-del" onclick="coRemoveRow(${i})" title="Remove line">×</button>
    </div>`).join('');
  const total = _coTotalOf(d);
  el.innerHTML = `
    <div class="co-editor-box">
      <div class="field-group"><label>Title</label>
        <input type="text" value="${esc(d.title)}" placeholder="e.g. Skylight flashing"
          oninput="_coDraft.title=this.value"></div>
      <div class="field-group"><label>What's changing (shown to the customer)</label>
        <textarea rows="3" oninput="_coDraft.description=this.value"
          placeholder="Two skylights discovered during tear-off need new flashing kits…">${esc(d.description)}</textarea></div>
      <div class="co-li-hdr"><span>Line items</span>
        <label class="co-rate">Margin %
          <input type="number" step="any" value="${p.rate ?? 35}"
            oninput="_coDraft.pricing.rate=this.value;renderCOEditor()"></label></div>
      ${rows}
      <button class="btn-secondary co-add-row" onclick="coAddRow()">＋ Add line</button>
      <div class="co-editor-total ${total < 0 ? 'co-credit' : ''}">
        ${total < 0 ? 'Credit to customer' : 'Change order total'}: <strong>${_fmtSigned(total)}</strong></div>
      <div class="co-editor-actions">
        <button class="btn-secondary" onclick="_coDraft=null;renderCOPanel()">Cancel</button>
        <button class="btn-primary" onclick="coSave()">✓ Save ${d.id ? 'Changes' : 'Change Order'}</button>
      </div>
    </div>`;
}

function coSetItem(i, key, val) {
  if (!_coDraft || !_coDraft.line_items[i]) return;
  _coDraft.line_items[i][key] = val;
  const cell = document.getElementById(`co-li-total-${i}`);
  if (cell) cell.textContent = _fmtSigned(_coLineTotal(_coDraft.line_items[i], _coDraft.pricing || {}));
  const tot = document.querySelector('.co-editor-total');
  if (tot) {
    const t = _coTotalOf(_coDraft);
    tot.classList.toggle('co-credit', t < 0);
    tot.innerHTML = `${t < 0 ? 'Credit to customer' : 'Change order total'}: <strong>${_fmtSigned(t)}</strong>`;
  }
}

function coAddRow(rerender = true) {
  if (!_coDraft) return;
  _coDraft.line_items.push({ name: '', description: '', quantity: 1, unit: 'EA',
                             material_unit_cost: 0, labor_unit_cost: 0, price_override: '' });
  if (rerender) renderCOEditor();
}

function coRemoveRow(i) {
  if (!_coDraft) return;
  _coDraft.line_items.splice(i, 1);
  renderCOEditor();
}

async function coSave() {
  if (!_coDraft) return;
  const body = JSON.stringify(_coDraft);
  const url  = `/api/estimates/${S.estimate_id}/change-orders` + (_coDraft.id ? `/${_coDraft.id}` : '');
  try {
    const r = await fetch(url, { method: _coDraft.id ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' }, body });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Save failed');
    _coDraft = null;
    await loadChangeOrders();
  } catch (e) { alert('Could not save the change order: ' + e.message); }
}

async function coDelete(id) {
  if (!confirm('Delete this change order?')) return;
  const r = await fetch(`/api/estimates/${S.estimate_id}/change-orders/${id}`, { method: 'DELETE' });
  if (!r.ok) { alert((await r.json()).error || 'Could not delete.'); return; }
  loadChangeOrders();
}

async function coSetStatus(id, status) {
  const r = await fetch(`/api/estimates/${S.estimate_id}/change-orders/${id}/status`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }) });
  if (!r.ok) { alert((await r.json()).error || 'Could not update.'); return; }
  loadChangeOrders();
}

async function coShareLink(id) {
  try {
    const r = await fetch(`/api/estimates/${S.estimate_id}/change-orders/${id}/share`, { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Could not create the link');
    const url = d.full_url || (window.location.origin + d.url);
    const email = (S.customer || {}).email;
    if (email && confirm(`Email the signing link to ${email}?\n(Cancel to copy/share the link instead.)`)) {
      const r2 = await fetch(`/api/estimates/${S.estimate_id}/change-orders/${id}/send-email`, { method: 'POST' });
      const d2 = await r2.json();
      if (!r2.ok) throw new Error(d2.error || 'Email failed');
      alert(`✓ Sent to ${d2.sent_to}`);
    } else if (navigator.share) {
      await doNativeShare(url, (S.customer || {}).name || '');
    } else {
      await navigator.clipboard.writeText(url).catch(() => {});
      alert('Signing link copied!');
    }
    loadChangeOrders();
  } catch (e) { alert(e.message); }
}

async function generateProductionPacket(btn) {
  if (!S.estimate_id) return;
  const sub = btn.querySelector('.doc-card-sub');
  if (sub) sub.textContent = 'Generating…';
  btn.disabled = true;
  try {
    const r = await fetch(`/api/estimates/${S.estimate_id}/production-packet`, { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Generation failed');
    // Server replaced any previous packet — mirror that in S (packet only;
    // other server-generated docs like signed change orders stay)
    if (!Array.isArray(S.attachments)) S.attachments = [];
    S.attachments = S.attachments.filter(a => !(a.server_generated
      && (a.doc_type === 'work_order' || a.doc_type === 'material_order')));
    S.attachments.push(d.attachment);
  } catch (e) {
    alert('Could not generate the production packet: ' + e.message);
  }
  renderDocumentsPage();
}

async function generatePermitPacket(btn) {
  if (!S.estimate_id) return;
  const sub = btn.querySelector('.doc-card-sub');
  if (sub) sub.textContent = 'Generating + pushing to Den…';
  btn.disabled = true;
  try {
    const r = await fetch(`/api/estimates/${S.estimate_id}/permit-packet`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({push_to_crm: true}),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Generation failed');
    if (!Array.isArray(S.attachments)) S.attachments = [];
    S.attachments = S.attachments.filter(a => !(a.server_generated && a.doc_type === 'permit_packet'));
    S.attachments.push(d.attachment);
  } catch (e) {
    alert('Could not generate the permit packet: ' + e.message);
  }
  renderDocumentsPage();
}

/* Work-order fields the rep fills in AFTER signing (schedule date, satellite
   dish, layers, height/access, hand load). Save writes to est.work_order without touching
   the packet PDF; Regenerate rebuilds so the numbers land on the sheet;
   Push to Den regenerates then files in Base44. */
function renderWorkOrderForm() {
  const wo = S.work_order || {};
  const dish = wo.satellite_dish || '';
  const dishOpts = ['', 'Reinstall', 'Remove', 'Leave in place', 'N/A — no dish'];
  // Height and hand load change crew size and the day rate, so the crew needs
  // them before they roll — not discovered in the driveway.
  const height = wo.height_access || '';
  const heightOpts = ['', '1-story', '2-story', '3-story / high', 'Walkout / split level'];
  const handLoad = wo.hand_load || '';
  const loadOpts = ['', 'No — boom/conveyor', 'YES — hand load', 'Partial hand load'];
  return `
  <div class="wo-form">
    <div class="wo-form-title">Work Order Details <span class="pm-hint" style="font-weight:normal">(fill in, then Regenerate — Push to Den when ready)</span></div>
    <div class="wo-form-grid">
      <label>Scheduled Date
        <input type="date" id="wo-sched" value="${esc(wo.scheduled_date || '')}">
      </label>
      <label>Tear-off Layers
        <input type="number" min="0" max="20" step="1" id="wo-layers"
          value="${wo.tear_off_layers != null ? esc(String(wo.tear_off_layers)) : ''}"
          placeholder="0 = new construction">
      </label>
      <label>Satellite Dish
        <select id="wo-dish">
          ${dishOpts.map(o => `<option value="${esc(o)}" ${o === dish ? 'selected' : ''}>${esc(o || '— choose —')}</option>`).join('')}
        </select>
      </label>
      <label>Height / Access
        <select id="wo-height">
          ${heightOpts.map(o => `<option value="${esc(o)}" ${o === height ? 'selected' : ''}>${esc(o || '— choose —')}</option>`).join('')}
        </select>
      </label>
      <label>Hand Load
        <select id="wo-handload">
          ${loadOpts.map(o => `<option value="${esc(o)}" ${o === handLoad ? 'selected' : ''}>${esc(o || '— choose —')}</option>`).join('')}
        </select>
      </label>
    </div>
    <div class="wo-form-btns">
      <button class="doc-crm-push" onclick="saveWorkOrderFields()">💾 Save</button>
      <button class="doc-crm-push" onclick="regenerateWorkOrder()">🔄 Save + Regenerate</button>
    </div>
  </div>`;
}

function _readWorkOrderForm() {
  const sched  = (document.getElementById('wo-sched')  || {}).value || '';
  const layers = (document.getElementById('wo-layers') || {}).value || '';
  const dish   = (document.getElementById('wo-dish')   || {}).value || '';
  const height = (document.getElementById('wo-height') || {}).value || '';
  const hload  = (document.getElementById('wo-handload') || {}).value || '';
  const out = {scheduled_date: sched, satellite_dish: dish,
               height_access: height, hand_load: hload};
  if (layers !== '') out.tear_off_layers = parseInt(layers, 10);
  return out;
}

async function saveWorkOrderFields() {
  if (!S.estimate_id) return null;
  const payload = _readWorkOrderForm();
  try {
    const r = await fetch(`/api/estimates/${S.estimate_id}/work-order`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Save failed');
    S.work_order = Object.assign({}, S.work_order || {}, d.work_order || {});
    return d.work_order;
  } catch (e) {
    alert('Could not save work-order fields: ' + e.message);
    return null;
  }
}

async function regenerateWorkOrder() {
  const saved = await saveWorkOrderFields();
  if (saved === null) return;
  try {
    const r = await fetch(`/api/estimates/${S.estimate_id}/production-packet`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({push_to_crm: false}),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Regeneration failed');
    if (!Array.isArray(S.attachments)) S.attachments = [];
    S.attachments = S.attachments.filter(a => !(a.server_generated
      && (a.doc_type === 'work_order' || a.doc_type === 'material_order')));
    S.attachments.push(d.attachment);
  } catch (e) {
    alert('Could not regenerate the work order: ' + e.message);
  }
  renderDocumentsPage();
}

async function pushWorkOrderToCrm() {
  if (!S.estimate_id) return;
  if (!((S.customer||{}).crm_project_id)) {
    alert('This estimate isn\'t linked to a CRM job yet — use the customer search to link it, then try again.');
    return;
  }
  // Save whatever's in the form fields first so the pushed packet has them
  await saveWorkOrderFields();
  try {
    const r = await fetch(`/api/estimates/${S.estimate_id}/production-packet`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({push_to_crm: true}),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Push failed');
    if (!Array.isArray(S.attachments)) S.attachments = [];
    S.attachments = S.attachments.filter(a => !(a.server_generated
      && (a.doc_type === 'work_order' || a.doc_type === 'material_order')));
    S.attachments.push(d.attachment);
  } catch (e) {
    alert('Could not push the work order to Den: ' + e.message);
  }
  renderDocumentsPage();
}

/* ── Roof certificate (realtor labor-only warranty) ────────────────────
   Standalone product: a realtor needs a roof signed off before closing, so
   the rep inspects, certifies condition and remaining life, and backs it
   with a short LABOR-ONLY leak warranty. There is no estimate to build and
   nothing to sign — the rep's name and the inspection date at the bottom of
   the PDF are the promise.

   Save writes the fields; Issue rebuilds the PDF. Same two-step as the work
   order, and for the same reason: a half-filled certificate must never be
   the thing that reaches a realtor. */
const RC_TERMS      = [6, 12, 24];
const RC_CONDITIONS = ['Excellent', 'Good', 'Fair', 'Serviceable with repairs'];

function renderRoofCertForm() {
  const el = document.getElementById('roofcert-form-container');
  if (!el) return;
  const rc   = S.roof_certificate || {};
  const c    = S.customer || {};
  const term = rc.term_months;
  // Sensible openers so the rep types as little as possible on a roof.
  const inspector = rc.inspector || cap(S.salesperson || _loggedInUser || '');
  const inspDate  = rc.inspection_date || fmtDate(new Date());
  const issued    = (S.attachments || []).some(
    a => a.server_generated && a.doc_type === 'roof_certificate');

  // .rc-f is a flex column, so a bare text node plus a trailing note-tag would
  // become two stacked rows. Wrapping them keeps the hint beside the label.
  const lab = (text, note) =>
    `<span class="rc-lab">${text}${note ? ` <span class="note-tag">${note}</span>` : ''}</span>`;

  const t = (id, label, val, ph, extra='') => `
    <label class="rc-f">${lab(label)}
      <input type="text" id="${id}" value="${esc(val || '')}" placeholder="${esc(ph||'')}" ${extra}>
    </label>`;

  el.innerHTML = `
  <div class="panel rc-panel">
    <div class="panel-header">
      <h3>🏅 Roof Certificate — ${esc(c.name || 'this property')}</h3>
      ${issued ? '<span class="rc-issued-chip">Issued</span>' : ''}
    </div>

    <p class="pm-hint rc-lede">Certifies the roof's condition for a real-estate
      transaction and backs it with a <strong>labor-only</strong> leak warranty
      for a short term. Not a workmanship warranty and not a manufacturer
      warranty.</p>

    <div class="rc-sec">Inspection</div>
    <div class="rc-grid">
      <label class="rc-f">${lab('Inspection Date', 'term starts here')}
        <input type="date" id="rc-date" value="${esc(inspDate)}">
      </label>
      ${t('rc-inspector', 'Inspected By', inspector, 'Rep name — signs the certificate')}
      ${t('rc-material', 'Roof Covering', rc.roof_material, 'e.g. Architectural asphalt shingle')}
      ${t('rc-age', 'Approximate Age', rc.roof_age, 'e.g. Approximately 11 years')}
      <label class="rc-f">${lab('Condition')}
        <select id="rc-condition">
          <option value="">— choose —</option>
          ${RC_CONDITIONS.map(o => `<option value="${esc(o)}" ${o === (rc.condition||'') ? 'selected' : ''}>${esc(o)}</option>`).join('')}
        </select>
      </label>
      ${t('rc-life', 'Est. Remaining Life', rc.remaining_life, 'e.g. 8-12 years')}
    </div>
    <label class="rc-f rc-wide">${lab('Findings', 'prints on the certificate')}
      <textarea id="rc-findings" rows="4"
        placeholder="What you saw: field condition, flashings, penetrations, attic check…">${esc(rc.findings || '')}</textarea>
    </label>
    <label class="rc-f rc-wide">${lab('Repairs Performed Before Certifying')}
      <textarea id="rc-repairs" rows="2"
        placeholder="Leave blank if none were needed">${esc(rc.repairs_made || '')}</textarea>
    </label>

    <div class="rc-sec">Warranty</div>
    <div class="rc-term-row">
      <div class="rc-term-btns" id="rc-term-btns">
        ${RC_TERMS.map(n => `<button type="button" class="rc-term-btn ${term === n ? 'active' : ''}"
          onclick="rcSetTerm(${n})">${n} months</button>`).join('')}
      </div>
      <label class="rc-f rc-price">${lab('Price')}
        <input type="number" id="rc-price" min="0" step="25"
          value="${rc.price != null ? esc(String(rc.price)) : ''}" placeholder="0">
      </label>
    </div>
    <div class="rc-expiry" id="rc-expiry"></div>
    <label class="rc-f rc-wide">${lab('Exclusions', 'blank = our standard language')}
      <textarea id="rc-exclusions" rows="3"
        placeholder="Leave blank to print the standard exclusions.">${esc(rc.exclusions || '')}</textarea>
    </label>
    <button class="rc-load-std" type="button" onclick="rcLoadStandardExclusions()">
      Load the standard text to edit it</button>

    <div class="rc-sec">Realtor &amp; Transaction <span class="note-tag">optional</span></div>
    <div class="rc-grid">
      ${t('rc-realtor', 'Requested By', rc.realtor_name, 'Realtor name')}
      ${t('rc-brokerage', 'Brokerage', rc.realtor_brokerage, '')}
      ${t('rc-rphone', 'Realtor Phone', rc.realtor_phone, '')}
      ${t('rc-remail', 'Realtor Email', rc.realtor_email, '')}
      ${t('rc-buyer', 'Buyer', rc.buyer_name, '')}
      ${t('rc-seller', 'Seller', rc.seller_name, '')}
      ${t('rc-closing', 'Closing Date', rc.closing_date, 'e.g. September 12, 2026')}
    </div>

    <div class="rc-btns">
      <button class="doc-crm-push" onclick="saveRoofCertFields()">💾 Save</button>
      <button class="btn-primary rc-issue" onclick="issueRoofCert()">
        🏅 ${issued ? 'Save + Re-issue' : 'Save + Issue Certificate'}</button>
      ${issued ? `<button class="doc-crm-push" onclick="issueRoofCert(true)">↗ Issue + File in Den</button>` : ''}
      <span class="rc-saved" id="rc-saved"></span>
    </div>
  </div>`;
  rcUpdateExpiry();
  const d = document.getElementById('rc-date');
  if (d) d.onchange = rcUpdateExpiry;
}

function rcSetTerm(n) {
  S.roof_certificate = Object.assign({}, S.roof_certificate || {}, {term_months: n});
  document.querySelectorAll('#rc-term-btns .rc-term-btn').forEach(b =>
    b.classList.toggle('active', b.textContent.trim() === n + ' months'));
  rcUpdateExpiry();
}

/* Mirrors _add_months in app.py: clamp to the last day of the target month so
   an inspection on the 31st does not roll into the following month. The rep
   sees the expiration before issuing, so this preview must agree with the PDF. */
function rcAddMonths(iso, months) {
  const p = String(iso || '').slice(0, 10).split('-').map(Number);
  if (p.length !== 3 || p.some(isNaN)) return null;
  let [y, m, d] = p;
  m = m - 1 + months;
  y += Math.floor(m / 12);
  m = ((m % 12) + 12) % 12;
  const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  const dt = new Date(Date.UTC(y, m, Math.min(d, last)));
  return dt.toLocaleDateString('en-US', {month:'long', day:'numeric', year:'numeric', timeZone:'UTC'});
}

function rcUpdateExpiry() {
  const el = document.getElementById('rc-expiry');
  if (!el) return;
  const iso  = (document.getElementById('rc-date') || {}).value || '';
  const term = (S.roof_certificate || {}).term_months;
  if (!iso || !RC_TERMS.includes(term)) {
    el.innerHTML = '<span class="rc-expiry-todo">Pick an inspection date and a term to see the coverage window.</span>';
    return;
  }
  const end = rcAddMonths(iso, term);
  el.innerHTML = `Covers <strong>${term} months, labor only</strong> — through <strong>${esc(end || '')}</strong>.`;
}

async function rcLoadStandardExclusions() {
  const ta = document.getElementById('rc-exclusions');
  if (!ta) return;
  if (ta.value.trim() && !confirm('Replace what you have typed with the standard exclusions?')) return;
  try {
    const r = await fetch(`/api/roof-certificate-defaults`);
    const d = await r.json();
    ta.value = d.exclusions || '';
  } catch (e) {
    alert('Could not load the standard exclusions: ' + e.message);
  }
}

function _readRoofCertForm() {
  const v = id => ((document.getElementById(id) || {}).value || '').trim();
  const out = {
    inspection_date:   v('rc-date'),
    inspector:         v('rc-inspector'),
    roof_material:     v('rc-material'),
    roof_age:          v('rc-age'),
    condition:         v('rc-condition'),
    remaining_life:    v('rc-life'),
    findings:          v('rc-findings'),
    repairs_made:      v('rc-repairs'),
    exclusions:        v('rc-exclusions'),
    realtor_name:      v('rc-realtor'),
    realtor_brokerage: v('rc-brokerage'),
    realtor_phone:     v('rc-rphone'),
    realtor_email:     v('rc-remail'),
    buyer_name:        v('rc-buyer'),
    seller_name:       v('rc-seller'),
    closing_date:      v('rc-closing'),
  };
  const term = (S.roof_certificate || {}).term_months;
  if (RC_TERMS.includes(term)) out.term_months = term;
  const price = v('rc-price');
  if (price !== '') out.price = parseFloat(price);
  return out;
}

async function saveRoofCertFields(quiet) {
  if (!S.estimate_id) await saveEstimate();
  if (!S.estimate_id) return null;
  const payload = _readRoofCertForm();
  try {
    const r = await fetch(`/api/estimates/${S.estimate_id}/roof-certificate`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Save failed');
    S.roof_certificate = Object.assign({}, S.roof_certificate || {}, d.roof_certificate || {});
    // Local confirmation only. setClean() would clear the header's global
    // unsaved flag, which this save does not earn — the estimate itself may
    // still have edits pending.
    const flag = document.getElementById('rc-saved');
    if (flag && !quiet) {
      flag.textContent = '✓ Saved';
      setTimeout(() => { if (flag.textContent === '✓ Saved') flag.textContent = ''; }, 2500);
    }
    return d.roof_certificate;
  } catch (e) {
    alert('Could not save the certificate: ' + e.message);
    return null;
  }
}

async function issueRoofCert(pushToCrm) {
  const saved = await saveRoofCertFields(true);
  if (saved === null) return;
  try {
    const r = await fetch(`/api/estimates/${S.estimate_id}/roof-certificate`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({push_to_crm: !!pushToCrm}),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Could not issue the certificate');
    if (!Array.isArray(S.attachments)) S.attachments = [];
    S.attachments = S.attachments.filter(
      a => !(a.server_generated && a.doc_type === 'roof_certificate'));
    S.attachments.push(d.attachment);
  } catch (e) {
    alert(e.message);
  }
  renderDocumentsPage();
}

function docToggleGenerator(which) {
  _docGenerator = (_docGenerator === which) ? null : which;
  if (_docGenerator === 'permit') {
    // Fresh open on a job: prefill from the estimate once per estimate
    if (!PermitState || PermitState.linked_estimate !== (S.estimate_id || '__none__')) {
      PermitState = null;  // rebuild with current defaults, then prefill below
    }
  }
  renderDocumentsPage();
}

async function docUploadPdf(files) {
  if (!files || !files.length) return;
  if (!S.estimate_id) await saveEstimate();
  for (const file of Array.from(files)) {
    const fd = new FormData(); fd.append('file', file);
    try {
      const r = await fetch(`/api/uploads/${S.estimate_id}`, {method:'POST', body: fd});
      if (!r.ok) throw new Error((await r.json()).error || 'Upload failed');
      const res = await r.json();
      if (!Array.isArray(S.attachments)) S.attachments = [];
      const att = {id: uid(), filename: res.filename, original_name: file.name,
        label: file.name.replace(/\.pdf$/i,''), show_in_estimate: false,
        pages: res.pages || undefined};
      S.attachments.push(att);
      setDirty();
      pushDocToCrm(att.id, {silent: true});   // auto-file in the CRM when job-linked
    } catch(e) { alert(`Could not upload ${file.name}: ${e.message}`); }
  }
  const inp = document.getElementById('doc-pdf-input'); if (inp) inp.value = '';
  renderDocumentsPage();
}

/* File a Documents-tab PDF in the Base44 CRM as a labeled Document record
   on the linked job. Auto-runs on upload/generation (silent — skips when
   the estimate isn't CRM-linked); the ↗ CRM button retries manually. */
async function pushDocToCrm(attId, opts = {}) {
  const att = (S.attachments || []).find(a => a.id === attId);
  if (!att || att.crm_document_id || !S.estimate_id) return;
  const docType = att.doc_type || (/permit/i.test(att.label || '') ? 'permit' : 'other');
  try {
    const r = await fetch(`/api/estimates/${S.estimate_id}/push-document`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({filename: att.filename, label: att.label || att.original_name, doc_type: docType}),
    });
    const res = await r.json();
    if (!r.ok) throw new Error(res.error || r.statusText);
    if (res.skipped) {
      if (!opts.silent) alert('This estimate isn\'t linked to a CRM job yet — use the customer search to link it, then try again.');
      return;
    }
    att.crm_document_id = res.crm_document_id;
    setDirty();
    if (activePage === 'documents') renderDocumentsPage();
  } catch(e) {
    if (!opts.silent) alert('Could not file in CRM: ' + e.message);
    else console.warn('CRM doc push skipped/failed:', e.message);
  }
}

/* ── Loveland permit & affidavit generator ─────────────────────────────
   Fills the city's flat 2-page reroof packet server-side (POST
   /api/permits/loveland/generate), downloads the finished PDF, and
   files it into this job's Documents. The template is pre-signed, so
   no signature capture is needed — only the job-specific blanks.
   Roofing-spec fields load sticky company defaults from
   /api/permit-defaults (manager-editable via "Save as defaults"). */

let PermitState = null;
let _permitDefaults = null;

function _permitToday() {
  const d = new Date();
  return `${String(d.getMonth()+1).padStart(2,'0')}/${String(d.getDate()+0).padStart(2,'0')}/${d.getFullYear()}`;
}

function _newPermitState() {
  const pd = _permitDefaults || {};
  return {
    owner_name: '', owner_phone: '',
    job_street: '', job_city: 'Loveland', job_state: 'CO', job_zip: '',
    owner_same_address: true,
    owner_street: '', owner_city: '', owner_state: 'CO', owner_zip: '',
    valuation: '', num_squares: '', work_description: '',
    roof_covering_type:  pd.roof_covering_type_default  || 'Asphalt Composition Shingle',
    roof_covering_class: pd.roof_covering_class_default || 'Class 4',
    replacing_sheathing: pd.replacing_sheathing_default || 'no',
    metal_noncombustible: !!pd.metal_noncombustible_default,
    astm_type: pd.astm_type_default || 'asphalt',
    astm_other_text: '',
    fastener_staples: pd.fastener_staples_default || '',
    fastener_nails:   pd.fastener_nails_default   || '',
    fastener_other:   pd.fastener_other_default   || '',
    underlayment_self_adhering: pd.underlayment_self_adhering_default !== false,
    underlayment_ice_barrier:   pd.underlayment_ice_barrier_default   !== false,
    date: _permitToday(),
    linked_estimate: null,
  };
}

function permitSet(key, val) {
  if (!PermitState) return;
  PermitState[key] = val;
  if (key === 'owner_same_address') renderPermitForm();
}

function permitPrefillFromEstimate() {
  if (!PermitState) return;
  const c = S.customer || {};
  const a = c.address || {};
  PermitState.owner_name  = c.name  || '';
  PermitState.owner_phone = c.phone || '';
  PermitState.job_street  = a.street || '';
  PermitState.job_city    = a.city   || 'Loveland';
  PermitState.job_state   = a.state  || 'CO';
  PermitState.job_zip     = a.zip    || '';
  const sq = parseFloat((S.measurements || {}).roof_squares);
  if (sq > 0) PermitState.num_squares = String(sq);
  const total = (typeof selectedTotal === 'function' && selectedTotal()) ||
                (typeof insuranceTotal === 'function' && insuranceTotal()) || 0;
  if (total > 0 && !PermitState.valuation)
    PermitState.valuation = total.toLocaleString('en-US', {minimumFractionDigits: 2});
  // Shingle brand/model from Product Selection, if chosen
  const colors = ((S.trades || {}).roofing || {}).colors || {};
  const parts = [colors.brand, colors.model].filter(v => String(v||'').trim());
  if (parts.length) PermitState.roof_covering_type = 'Asphalt Composition Shingle - ' + parts.join(' ');
  PermitState.linked_estimate = S.estimate_id || '__none__';
  renderPermitForm();
}

let _permitSearchTimer = null;
function permitCrmSearch(q) {
  clearTimeout(_permitSearchTimer);
  const box = document.getElementById('pm-crm-results');
  if (!q || q.trim().length < 2) { if (box) box.classList.add('hidden'); return; }
  _permitSearchTimer = setTimeout(async () => {
    try {
      const r = await fetch('/api/crm/contacts?q=' + encodeURIComponent(q.trim()));
      const list = await r.json();
      if (!box) return;
      box.innerHTML = list.length ? list.map(c => `
        <div class="pm-crm-hit" onclick='permitPickContact(${JSON.stringify(JSON.stringify(c))})'>
          <div class="pm-crm-name">${esc(c.name || '(no name)')}</div>
          <div class="pm-crm-sub">${esc([c.street_address, c.city].filter(Boolean).join(', ') || c.phone || '')}</div>
        </div>`).join('')
        : '<div class="pm-crm-hit pm-crm-empty">No CRM matches</div>';
      box.classList.remove('hidden');
    } catch { if (box) box.classList.add('hidden'); }
  }, 250);
}

function permitPickContact(jsonStr) {
  const c = JSON.parse(jsonStr);
  PermitState.owner_name  = c.name  || '';
  PermitState.owner_phone = c.phone || '';
  PermitState.job_street  = c.street_address || '';
  PermitState.job_city    = c.city  || 'Loveland';
  PermitState.job_state   = c.state || 'CO';
  PermitState.job_zip     = c.zip_code || '';
  renderPermitForm();
}

async function renderPermitForm() {
  const el = document.getElementById('permit-form-container');
  if (!el) return;
  if (!_permitDefaults) {
    try { _permitDefaults = await (await fetch('/api/permit-defaults')).json(); }
    catch { _permitDefaults = {}; }
  }
  if (!PermitState) {
    PermitState = _newPermitState();
    // Opening on a job with a customer: fill from the estimate automatically
    if (S && S.customer && S.customer.name) { permitPrefillFromEstimate(); return; }
    PermitState.linked_estimate = S.estimate_id || '__none__';
  }
  const P = PermitState;
  const inp = (key, ph, extra='') =>
    `<input type="text" value="${esc(String(P[key] ?? ''))}" placeholder="${ph}"
       oninput="permitSet('${key}', this.value)" ${extra}>`;
  const chk = key =>
    `<input type="checkbox" ${P[key] ? 'checked' : ''} onchange="permitSet('${key}', this.checked)">`;

  el.innerHTML = `
  <div class="pm-form">
    <div class="panel">
      <div class="panel-header"><h3>🏛 City of Loveland — Reroof Permit &amp; Affidavit</h3></div>
      <p class="pm-hint">Auto-filled from this job. Generate, then e-mail the PDF to
        <b>eplan-buildingfasttrack@cityofloveland.org</b> — a copy is also filed under Documents.</p>
      <div class="pm-lookup">
        <input type="text" id="pm-crm-q" placeholder="🔍 Different homeowner? Look up in CRM (name, phone, or email)…"
          oninput="permitCrmSearch(this.value)" autocomplete="off">
        <div id="pm-crm-results" class="pm-crm-results hidden"></div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-header"><h3>Homeowner &amp; Job Site</h3></div>
      <div class="pm-grid">
        <div class="field-group pm-span2"><label>Owner Name</label>${inp('owner_name','Owner full name')}</div>
        <div class="field-group"><label>Owner Phone</label>${inp('owner_phone','970-555-1234')}</div>
        <div class="field-group pm-span2"><label>Job Site Street Address</label>${inp('job_street','123 Main St')}</div>
        <div class="field-group"><label>City</label>${inp('job_city','Loveland')}</div>
        <div class="field-group pm-state"><label>State</label>${inp('job_state','CO')}</div>
        <div class="field-group"><label>Zip</label>${inp('job_zip','80537')}</div>
      </div>
      <label class="pm-check pm-same"><input type="checkbox" ${P.owner_same_address ? 'checked' : ''}
        onchange="permitSet('owner_same_address', this.checked)"> Owner mailing address is the same as the job site</label>
      ${P.owner_same_address ? '' : `
      <div class="pm-grid">
        <div class="field-group pm-span2"><label>Owner Mailing Street</label>${inp('owner_street','')}</div>
        <div class="field-group"><label>City</label>${inp('owner_city','')}</div>
        <div class="field-group pm-state"><label>State</label>${inp('owner_state','CO')}</div>
        <div class="field-group"><label>Zip</label>${inp('owner_zip','')}</div>
      </div>`}
    </div>

    <div class="panel">
      <div class="panel-header"><h3>Job Details</h3></div>
      <div class="pm-grid">
        <div class="field-group"><label>Valuation ($)</label>${inp('valuation','18,450.00')}</div>
        <div class="field-group"><label>Number of Squares</label>${inp('num_squares','28.5')}</div>
        <div class="field-group"><label>Date</label>${inp('date','MM/DD/YYYY')}</div>
      </div>
      <div class="field-group pm-desc">
        <label>Work Description <span class="pm-sub">(note if electrical meets minimum code — press Enter for a new line)</span></label>
        <textarea rows="5" placeholder="Full tear-off and replacement of asphalt shingle roof…"
          oninput="permitSet('work_description', this.value)">${esc(P.work_description)}</textarea>
      </div>
    </div>

    <div class="panel">
      <div class="panel-header"><h3>Roofing Affidavit Specs</h3>
        <button class="pm-save-defaults" onclick="permitSaveDefaults()" title="Save these specs as the company-wide defaults (managers only)">💾 Save as defaults</button>
      </div>
      <div class="pm-grid">
        <div class="field-group pm-span2"><label>Type of Roof Covering</label>${inp('roof_covering_type','Asphalt Composition Shingle')}</div>
        <div class="field-group"><label>Class of Roof Covering</label>${inp('roof_covering_class','Class 4')}</div>
      </div>
      <div class="pm-row">
        <span class="pm-lbl">Replacing sheathing?</span>
        <label class="pm-check"><input type="radio" name="pm-sheath" ${P.replacing_sheathing==='yes'?'checked':''}
          onchange="permitSet('replacing_sheathing','yes')"> Yes</label>
        <label class="pm-check"><input type="radio" name="pm-sheath" ${P.replacing_sheathing==='no'?'checked':''}
          onchange="permitSet('replacing_sheathing','no')"> No</label>
        <label class="pm-check pm-gap">${chk('metal_noncombustible')} Metal / noncombustible roofing</label>
      </div>
      <div class="pm-row">
        <span class="pm-lbl">Roofing information:</span>
        <label class="pm-check"><input type="radio" name="pm-astm" ${P.astm_type==='asphalt'?'checked':''}
          onchange="permitSet('astm_type','asphalt');renderPermitForm()"> Asphalt shingles (ASTM D 3161 F / D 7158 H)</label>
        <label class="pm-check"><input type="radio" name="pm-astm" ${P.astm_type==='other'?'checked':''}
          onchange="permitSet('astm_type','other');renderPermitForm()"> Other</label>
        ${P.astm_type==='other' ? inp('astm_other_text','ASTM # or UL #','class="pm-inline"') : ''}
      </div>
      <div class="pm-grid">
        <div class="field-group"><label>Staples (size / type / number)</label>${inp('fastener_staples','')}</div>
        <div class="field-group"><label>Nails (size / type / number)</label>${inp('fastener_nails','1-1/4" coil nails, 6 per shingle')}</div>
        <div class="field-group"><label>Other fasteners</label>${inp('fastener_other','')}</div>
      </div>
      <div class="pm-row">
        <span class="pm-lbl">Underlayment used:</span>
        <label class="pm-check">${chk('underlayment_self_adhering')} Self-adhering polymer-modified bitumen sheet</label>
        <label class="pm-check">${chk('underlayment_ice_barrier')} Ice barrier (two cemented layers)</label>
      </div>
    </div>

    <div class="pm-actions">
      <button class="pm-generate" onclick="permitGenerate(this)">📄 Generate Permit PDF</button>
      <span class="pm-note">Downloads the filled packet and files a copy under this job's Documents.</span>
    </div>
  </div>`;
}

async function permitSaveDefaults() {
  const P = PermitState;
  const body = {
    roof_covering_type_default:  P.roof_covering_type,
    roof_covering_class_default: P.roof_covering_class,
    replacing_sheathing_default: P.replacing_sheathing,
    metal_noncombustible_default: P.metal_noncombustible,
    astm_type_default: P.astm_type,
    fastener_staples_default: P.fastener_staples,
    fastener_nails_default:   P.fastener_nails,
    fastener_other_default:   P.fastener_other,
    underlayment_self_adhering_default: P.underlayment_self_adhering,
    underlayment_ice_barrier_default:   P.underlayment_ice_barrier,
  };
  const r = await fetch('/api/permit-defaults', {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  });
  if (r.ok) { _permitDefaults = body; alert('Saved — these specs are now the defaults for every permit.'); }
  else alert('Could not save defaults (managers only).');
}

async function permitGenerate(btn) {
  const P = PermitState;
  if (!P.owner_name.trim() || !P.job_street.trim()) {
    alert('Owner name and job site address are required.');
    return;
  }
  const jobAddr = [P.job_street.trim(),
                   P.job_city.trim(),
                   [P.job_state.trim(), P.job_zip.trim()].filter(Boolean).join(' ')]
                  .filter(Boolean).join(', ');
  const same = P.owner_same_address;
  const payload = {
    job_site_address: jobAddr,
    valuation: P.valuation, owner_name: P.owner_name, owner_phone: P.owner_phone,
    owner_address: same ? P.job_street : P.owner_street,
    owner_city:    same ? P.job_city   : P.owner_city,
    owner_state:   same ? P.job_state  : P.owner_state,
    owner_zip:     same ? P.job_zip    : P.owner_zip,
    num_squares: P.num_squares, work_description: P.work_description,
    date: P.date,
    roof_covering_type: P.roof_covering_type, roof_covering_class: P.roof_covering_class,
    replacing_sheathing: P.replacing_sheathing, metal_noncombustible: P.metal_noncombustible,
    astm_type: P.astm_type, astm_other_text: P.astm_other_text,
    fastener_staples: P.fastener_staples, fastener_nails: P.fastener_nails,
    fastener_other: P.fastener_other,
    underlayment_self_adhering: P.underlayment_self_adhering,
    underlayment_ice_barrier: P.underlayment_ice_barrier,
  };
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = 'Generating…';
  try {
    const r = await fetch('/api/permits/loveland/generate', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error((await r.json()).error || r.statusText);
    const blob = await r.blob();
    const fname = `Loveland_Permit_${(P.owner_name||'Permit').replace(/[^A-Za-z0-9 ]+/g,'').trim().replace(/ +/g,'_')}.pdf`;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = fname;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 30000);
    // File a copy into this job's Documents (internal — not customer-facing)
    try {
      if (!S.estimate_id) await saveEstimate();
      const fd = new FormData();
      fd.append('file', new File([blob], fname, {type: 'application/pdf'}));
      const ur = await fetch(`/api/uploads/${S.estimate_id}`, {method: 'POST', body: fd});
      if (ur.ok) {
        const ures = await ur.json();
        if (!Array.isArray(S.attachments)) S.attachments = [];
        const att = {id: uid(), filename: ures.filename, original_name: fname,
          label: `Loveland Permit — ${P.date || _permitToday()}`,
          doc_type: 'permit', show_in_estimate: false,
          pages: ures.pages || undefined};
        S.attachments.push(att);
        setDirty(); await saveEstimate();
        _docGenerator = null;          // collapse the form; show the filed doc
        renderDocumentsPage();
        pushDocToCrm(att.id, {silent: true});   // auto-file in the CRM when job-linked
      }
    } catch(e) { console.warn('Could not file permit into Documents:', e); }
  } catch (e) {
    alert('Permit generation failed: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = old;
  }
}

/* ── Visualizer ────────────────────────────────────────────────────────
   Photo of the house + automatic or refined surface masks + product color/style
   overlay. Stored under S.visualizer (mirrored to est.visualizer server-side)
   and rendered on the customer /sign page and the signed PDF.

   Optional fal / SAM 3 detection selects surfaces once per uploaded photo.
   Brushes and flood-fill remain under Refine selection. We composite colors with
   globalCompositeOperation='multiply' so the base photo's shading is
   preserved. Siding also tiles a style pattern (lap / board-and-batten /
   shingle-style / vertical panel) so a Hardie Statement Iron Gray lap reads
   differently from a Hardie Statement Iron Gray B&B — same color, real
   visual difference. ProVia choices currently preview finish only, not exact
   panel/glass geometry. Detection never changes pricing or quoted bundles.

   The state lives in three places, in sync:
   - vzState (module-level, live pixels + tool state) — the working copy the
     brush edits, plus the loaded HTMLImageElement of the photo and five
     off-screen mask canvases (roof, siding, trim, soffit, door). Not persisted
     directly.
   - S.visualizer (client mirror of the estimate JSON) — filenames, selections,
     tier_renders. Sent to the server on save.
   - est.visualizer (server, in the estimate doc) — same shape as S.visualizer,
     written by POST /api/estimates/<id>/visualizer/asset and PUT .../state.
*/

// Tileable SVG textures per siding style. Kept intentionally tiny — the
// pattern is a hint, not a photorealistic render. Drawn 'multiply' at ~40%
// opacity over the color layer, so the seams read as shadow lines and the
// underlying color stays true. Add a style by extending _SIDING_PATTERN_SVG
// AND the seed data in app.py (styles list per material product).
const _SIDING_PATTERN_SVG = {
  lap: `<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64'>
    <rect width='64' height='64' fill='white'/>
    <line x1='0' y1='12' x2='64' y2='12' stroke='rgb(60,60,60)' stroke-width='1'/>
    <line x1='0' y1='24' x2='64' y2='24' stroke='rgb(60,60,60)' stroke-width='1'/>
    <line x1='0' y1='36' x2='64' y2='36' stroke='rgb(60,60,60)' stroke-width='1'/>
    <line x1='0' y1='48' x2='64' y2='48' stroke='rgb(60,60,60)' stroke-width='1'/>
    <line x1='0' y1='60' x2='64' y2='60' stroke='rgb(60,60,60)' stroke-width='1'/>
  </svg>`,
  nickel_gap: `<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64'>
    <rect width='64' height='64' fill='white'/>
    <rect x='0' y='14' width='64' height='3' fill='rgb(48,48,48)'/>
    <rect x='0' y='30' width='64' height='3' fill='rgb(48,48,48)'/>
    <rect x='0' y='46' width='64' height='3' fill='rgb(48,48,48)'/>
    <rect x='0' y='62' width='64' height='2' fill='rgb(48,48,48)'/>
  </svg>`,
  bnb: `<svg xmlns='http://www.w3.org/2000/svg' width='96' height='64'>
    <rect width='96' height='64' fill='white'/>
    <rect x='0'  y='0' width='4' height='64' fill='rgb(50,50,50)'/>
    <rect x='32' y='0' width='4' height='64' fill='rgb(50,50,50)'/>
    <rect x='64' y='0' width='4' height='64' fill='rgb(50,50,50)'/>
  </svg>`,
  shake: `<svg xmlns='http://www.w3.org/2000/svg' width='48' height='48'>
    <rect width='48' height='48' fill='white'/>
    <path d='M0 12 L6 8 L12 12 L18 8 L24 12 L30 8 L36 12 L42 8 L48 12' stroke='rgb(60,60,60)' stroke-width='1' fill='none'/>
    <path d='M0 28 L6 24 L12 28 L18 24 L24 28 L30 24 L36 28 L42 24 L48 28' stroke='rgb(60,60,60)' stroke-width='1' fill='none'/>
    <path d='M0 44 L6 40 L12 44 L18 40 L24 44 L30 40 L36 44 L42 40 L48 44' stroke='rgb(60,60,60)' stroke-width='1' fill='none'/>
  </svg>`,
  shake_straight: `<svg xmlns='http://www.w3.org/2000/svg' width='64' height='48'>
    <rect width='64' height='48' fill='white'/>
    <path d='M0 16H64M0 32H64M0 48H64M8 0V16M24 0V16M40 0V16M56 0V16M0 16V32M16 16V32M32 16V32M48 16V32M64 16V32M8 32V48M24 32V48M40 32V48M56 32V48' stroke='rgb(60,60,60)' stroke-width='1'/>
  </svg>`,
  shake_staggered: `<svg xmlns='http://www.w3.org/2000/svg' width='64' height='48'>
    <rect width='64' height='48' fill='white'/>
    <path d='M0 13H8V17H24V12H40V16H56V11H64M0 29H16V33H32V28H48V32H64M0 45H8V48M24 45V48M40 44V48M56 46V48' stroke='rgb(60,60,60)' stroke-width='1' fill='none'/>
    <path d='M8 0V13M24 0V17M40 0V12M56 0V16M16 17V29M32 12V33M48 16V28M8 29V45M24 33V48M40 28V44M56 32V48' stroke='rgb(60,60,60)' stroke-width='1'/>
  </svg>`,
  panel: `<svg xmlns='http://www.w3.org/2000/svg' width='96' height='64'>
    <rect width='96' height='64' fill='white'/>
    <line x1='16' y1='0' x2='16' y2='64' stroke='rgb(70,70,70)' stroke-width='1'/>
    <line x1='48' y1='0' x2='48' y2='64' stroke='rgb(70,70,70)' stroke-width='1'/>
    <line x1='80' y1='0' x2='80' y2='64' stroke='rgb(70,70,70)' stroke-width='1'/>
  </svg>`,
  sierra8: `<svg xmlns='http://www.w3.org/2000/svg' width='96' height='64'>
    <rect width='96' height='64' fill='white'/>
    <path d='M12 0V64M16 0V64M44 0V64M48 0V64M76 0V64M80 0V64' stroke='rgb(60,60,60)' stroke-width='1'/>
  </svg>`,
  door_6panel: `<svg xmlns='http://www.w3.org/2000/svg' width='60' height='96'><rect width='60' height='96' fill='white'/><g fill='none' stroke='rgb(45,45,45)' stroke-width='2'><rect x='7' y='6' width='20' height='24'/><rect x='33' y='6' width='20' height='24'/><rect x='7' y='36' width='20' height='24'/><rect x='33' y='36' width='20' height='24'/><rect x='7' y='66' width='20' height='24'/><rect x='33' y='66' width='20' height='24'/></g></svg>`,
  door_3panel: `<svg xmlns='http://www.w3.org/2000/svg' width='60' height='96'><rect width='60' height='96' fill='white'/><g fill='none' stroke='rgb(45,45,45)' stroke-width='2'><rect x='7' y='6' width='46' height='24'/><rect x='7' y='36' width='46' height='24'/><rect x='7' y='66' width='46' height='24'/></g></svg>`,
  door_glass: `<svg xmlns='http://www.w3.org/2000/svg' width='60' height='96'><rect width='60' height='96' fill='white'/><rect x='9' y='7' width='42' height='50' fill='rgb(180,205,220)' stroke='rgb(45,45,45)' stroke-width='2'/><path d='M30 7V57M9 32H51' stroke='rgb(45,45,45)' stroke-width='1'/><rect x='9' y='65' width='42' height='24' fill='none' stroke='rgb(45,45,45)' stroke-width='2'/></svg>`,
  door_modern: `<svg xmlns='http://www.w3.org/2000/svg' width='60' height='96'><rect width='60' height='96' fill='white'/><path d='M8 25H52M8 49H52M8 73H52' stroke='rgb(45,45,45)' stroke-width='2'/></svg>`,
};
const _VZ_ROLES = ['roof', 'siding', 'trim', 'soffit', 'door', 'gutter', 'window', 'metal', 'shutter', 'stucco'];
const _VZ_DEFAULT_SCOPE = ['roof', 'siding', 'trim', 'soffit', 'door'];
const _VZ_ROLE_META = {
  roof:{label:'Roof',icon:'🏠',trade:'roofing',color:'rgba(220,50,50,0.38)'},
  siding:{label:'Siding',icon:'🏗',trade:'siding',color:'rgba(50,120,220,0.38)'},
  trim:{label:'Trim / Fascia',icon:'▦',trade:'trim',color:'rgba(168,85,247,0.42)'},
  soffit:{label:'Soffit',icon:'⌂',trade:'soffit',color:'rgba(20,184,166,0.42)'},
  door:{label:'Doors',icon:'🚪',trade:'doors',color:'rgba(234,135,25,0.42)'},
  gutter:{label:'Gutters',icon:'🌧',trade:'gutter',color:'rgba(59,130,246,0.45)'},
  window:{label:'Windows',icon:'🪟',trade:'window',color:'rgba(14,116,144,0.45)'},
  metal:{label:'Metal accents',icon:'◩',trade:'metal',color:'rgba(71,85,105,0.45)'},
  shutter:{label:'Shutters',icon:'▥',trade:'shutter',color:'rgba(124,58,237,0.45)'},
  stucco:{label:'Stucco',icon:'▧',trade:'stucco',color:'rgba(202,138,4,0.40)'}
};
const _VZ_COMPOSE_ORDER = ['roof','siding','stucco','soffit','trim','window','shutter','metal','gutter','door'];
const _VZ_PROJECTABLE_ROLES = ['roof','siding','stucco','metal','soffit'];
const _VZ_PLACEMENT_ROLES = ['door','window','shutter'];
const _VZ_MAX_PLANES = 16;
const _vzPatternImg = {};   // pattern_id -> HTMLImageElement (once loaded)
const _vzTextureImg = {};   // uploaded catalog texture ref -> HTMLImageElement
const _vzPlacementImg = {}; // catalog/job product cutout ref -> HTMLImageElement
const _vzPlacementSheetCache = new Map();
const _vzPlacementLayerCache = new Map();
const _vzProjectionCache = new Map(); // geometry + material -> unmasked warped layer

function _vzCacheCanvas(cache,key,canvas,maxBytes) {
  if (cache.has(key)) cache.delete(key);
  cache.set(key,canvas);
  let total=0;
  for (const value of cache.values()) total+=(value?.width||0)*(value?.height||0)*4;
  while (total>maxBytes && cache.size>1) {
    const oldest=cache.keys().next().value,removed=cache.get(oldest);
    cache.delete(oldest);
    total-=(removed?.width||0)*(removed?.height||0)*4;
  }
  return canvas;
}
function _vzCachedCanvas(cache,key) {
  const value=cache.get(key);
  if (!value) return null;
  cache.delete(key);cache.set(key,value);
  return value;
}
function _vzImageReady(img) {
  if (!img) return Promise.resolve();
  if (img.complete) return img.naturalWidth
    ? Promise.resolve(img)
    : Promise.reject(new Error('A design image could not be loaded.'));
  return new Promise((resolve,reject) => {
    let settled=false;
    const finish=(error) => {
      if(settled)return;settled=true;
      img.removeEventListener?.('load',onLoad);img.removeEventListener?.('error',onError);
      error?reject(error):resolve(img);
    };
    const onLoad=()=>finish();
    const onError=()=>finish(new Error('A design image could not be loaded.'));
    img.addEventListener?.('load',onLoad,{once:true});
    img.addEventListener?.('error',onError,{once:true});
    if(typeof img.decode==='function')img.decode().then(onLoad).catch(()=>{});
  });
}

// Get the tile Image for a pattern id, loading it on demand from the SVG
// data URI. Returns null on the first call and populates on next tick, so
// callers can just retry on the next render. Cheap because there are only
// four patterns total and each is a few hundred bytes.
function _vzGetPatternImg(pid) {
  if (!pid || typeof _SIDING_PATTERN_SVG[pid] !== 'string') return null;
  if (_vzPatternImg[pid]) return _vzPatternImg[pid];
  const img = new Image();
  const svg = _SIDING_PATTERN_SVG[pid].trim();
  img.src = 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
  _vzPatternImg[pid] = img;
  img.onload = () => {
    _vzProjectionCache.clear();
    if (activePage === 'visualizer') _vzRedrawAll();
  };
  return img;
}

function _vzGetTextureImg(ref) {
  if (!ref || !/^_catalog\/et_[0-9a-f]{32}\.png$/.test(ref)) return null;
  if (_vzTextureImg[ref]) return _vzTextureImg[ref];
  const img = new Image();
  img.src = BASE + '/uploads/' + ref;
  _vzTextureImg[ref] = img;
  img.onload = () => {
    _vzProjectionCache.clear();
    if (activePage === 'visualizer') _vzRedrawAll();
  };
  return img;
}

function _vzPlacementRefIsSafe(ref) {
  return /^_catalog\/ep_[0-9a-f]{32}\.png$/.test(ref || '') ||
    /^[A-Za-z0-9_-]+\/(?:vpl|vp)_[0-9a-f]{32}\.(?:png|jpe?g|webp)$/.test(ref || '');
}
function _vzGetPlacementImg(ref) {
  if (!_vzPlacementRefIsSafe(ref)) return null;
  if (_vzPlacementImg[ref]) return _vzPlacementImg[ref];
  const img = new Image();
  img.src = BASE + '/uploads/' + ref;
  _vzPlacementImg[ref] = img;
  img.onload = () => {
    _vzPlacementLayerCache.clear();
    if (activePage === 'visualizer') _vzRedrawAll();
  };
  return img;
}

let vzState = null;
let vzCapabilities = null;

function _vzResetState() {
  _vzProjectionCache.clear();
  _vzPlacementLayerCache.clear();
  vzState = {
    owner: S,
    photoKey: '',
    detecting: false,
    saving: false,
    refine: false,
    original: false,
    beforeSplit: 0,
    detectionStatus: {},
    photoImg: null,          // loaded HTMLImageElement, or null before load
    photoW: 0, photoH: 0,    // native pixel dimensions
    roofMask: null,          // OffscreenCanvas-like <canvas> matching photo size
    sidingMask: null,        // same
    trimMask: null,          // fascia + window/door/corner trim
    soffitMask: null,        // eave underside, independent finish
    doorMask: null,          // entry/garage door regions — independent layer
    canvas: null,            // visible canvas element
    ctx: null,
    activeTool: 'roof',      // one of _VZ_ROLES | 'erase'
    activeTier: 'better',    // 'good' | 'better' | 'best'
    brushSize: 30,
    magicWand: false,
    painting: false,
    lastX: 0, lastY: 0,
    dirty: false,            // has the rep painted since last save?
    selectionsChanged: false,
    pendingBaseDataUrl: null,  // photo data URL not yet uploaded
    pendingBaseExt: 'jpg',
    alignmentOpen: false,
    alignmentRole: 'roof',
    alignmentPlaneId: '',
    alignmentDragIndex: -1,
    alignmentFrame: 0,
    projectionChanged: false,
    placementOpen: false,
    placementSlotId: '',
    placementDragIndex: -1,
    placementMoveWhole: false,
    placementLastPoint: null,
    placementFrame: 0,
    placementsChanged: false,
    proviaUploading: false,
  };
  for (const role of _VZ_ROLES) vzState[role + 'Mask'] = null;
}

function _vzGet() {
  if (!S.visualizer) S.visualizer = {};
  const vz = S.visualizer;
  vz.selections = vz.selections || {};
  for (const trade of [...new Set(_VZ_ROLES.map(role => _VZ_ROLE_META[role].trade))]) {
    vz.selections[trade] = vz.selections[trade] || {};
  }
  vz.tier_renders = vz.tier_renders || {};
  vz.scope = Array.isArray(vz.scope) ? [...new Set(vz.scope.filter(r => _VZ_ROLES.includes(r)))] : [..._VZ_DEFAULT_SCOPE];
  if (!vz.scope.length) vz.scope = [..._VZ_DEFAULT_SCOPE];
  vz.concept_names = Object.assign({good:'Good',better:'Better',best:'Best'}, vz.concept_names || {});
  vz.provia_specs = vz.provia_specs && typeof vz.provia_specs === 'object' ? vz.provia_specs : {};
  vz.elevations = vz.elevations && typeof vz.elevations === 'object' && !Array.isArray(vz.elevations) ? vz.elevations : {};
  vz.elevations = Object.fromEntries(Object.entries(vz.elevations)
    .filter(([id,elevation]) => /^[a-z0-9_-]{1,40}$/.test(id) && elevation && typeof elevation === 'object'));
  const legacy = vz.base_image || Object.values(vz.tier_renders).some(Boolean) || _VZ_ROLES.some(role => vz[role + '_mask']);
  if (legacy && !vz.elevations.front) {
    const masks = {};
    for (const role of _VZ_ROLES) if (vz[role + '_mask']) masks[role] = vz[role + '_mask'];
    vz.elevations.front = {id:'front',name:'Front',base_image:vz.base_image || null,
      masks, tier_renders:Object.assign({}, vz.tier_renders)};
  }
  if (!Object.keys(vz.elevations).length) {
    vz.elevations.front = {id:'front',name:'Front',base_image:null,masks:{},tier_renders:{}};
  }
  for (const [id, elevation] of Object.entries(vz.elevations)) {
    elevation.id = id;
    elevation.name = elevation.name || id.replace(/_/g,' ').replace(/\b\w/g,c => c.toUpperCase());
    elevation.masks = elevation.masks && typeof elevation.masks === 'object' ? elevation.masks : {};
    elevation.tier_renders = elevation.tier_renders && typeof elevation.tier_renders === 'object' ? elevation.tier_renders : {};
    elevation.placements = _vzNormalizePlacements(elevation.placements);
    if (elevation.texture_projection) {
      const projection = _vzNormalizeTextureProjection(elevation.texture_projection);
      if (projection) elevation.texture_projection = projection;
      else delete elevation.texture_projection;
    }
  }
  vz.elevation_order = Array.isArray(vz.elevation_order)
    ? vz.elevation_order.filter(id => vz.elevations[id]) : [];
  for (const id of Object.keys(vz.elevations)) if (!vz.elevation_order.includes(id)) vz.elevation_order.push(id);
  if (!vz.elevations[vz.active_elevation_id]) vz.active_elevation_id = vz.elevation_order[0];
  return vz;
}

function _vzElevation() {
  const vz = _vzGet();
  return vz.elevations[vz.active_elevation_id];
}
function _vzScopeRoles() { return _vzGet().scope.filter(role => _VZ_ROLES.includes(role)); }

function _vzEmptyPlacements() {
  return {version:1,slots:{},concepts:{good:{},better:{},best:{}}};
}
function _vzNormalizePlacements(raw) {
  const result = _vzEmptyPlacements();
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return result;
  const sourceSlots = raw.slots && typeof raw.slots === 'object' && !Array.isArray(raw.slots) ? raw.slots : {};
  for (const [rawId, source] of Object.entries(sourceSlots).slice(0,64)) {
    const id = String(rawId || '').toLowerCase();
    if (!/^pl_[a-z0-9_-]{1,60}$/.test(id) || !source || typeof source !== 'object') continue;
    const role = String(source.role || '').toLowerCase();
    if (!_VZ_PLACEMENT_ROLES.includes(role)) continue;
    const quad = Array.isArray(source.quad) ? source.quad.map(point =>
      Array.isArray(point) ? point.map(_vzRoundCoord) : point) : null;
    if (!_vzQuadIsValid(quad)) continue;
    result.slots[id] = {id,role,label:String(source.label || _VZ_ROLE_META[role].label).slice(0,80),
      quad,z:Math.max(-1000,Math.min(1000,Math.round(Number(source.z)||0)))};
  }
  const concepts = raw.concepts && typeof raw.concepts === 'object' ? raw.concepts : {};
  for (const tier of TIERS) {
    const assignments = concepts[tier] && typeof concepts[tier] === 'object' ? concepts[tier] : {};
    for (const [slotId, source] of Object.entries(assignments)) {
      if (!result.slots[slotId] || !source || typeof source !== 'object' || !_vzPlacementRefIsSafe(source.asset_ref)) continue;
      const crop = source.source_crop && typeof source.source_crop === 'object' ? source.source_crop : {x:0,y:0,w:1,h:1};
      const x=Number(crop.x),y=Number(crop.y),w=Number(crop.w),h=Number(crop.h);
      if (![x,y,w,h].every(Number.isFinite) || x<0 || y<0 || w<=0 || h<=0 || x+w>1.000001 || y+h>1.000001) continue;
      const assignment = {asset_ref:source.asset_ref,source_crop:{x:_vzRoundCoord(x),y:_vzRoundCoord(y),w:_vzRoundCoord(w),h:_vzRoundCoord(h)},mirror_x:source.mirror_x===true};
      for (const field of ['product_id','product_name','color_name','style_name','selection_fingerprint']) {
        if (source[field]) assignment[field]=String(source[field]).slice(0,field==='product_name'?200:160);
      }
      result.concepts[tier][slotId]=assignment;
    }
  }
  return result;
}

function _vzRoundCoord(value) {
  return Math.round(Math.max(0, Math.min(1, Number(value) || 0)) * 10000) / 10000;
}

function _vzQuadIsValid(quad) {
  if (!Array.isArray(quad) || quad.length !== 4) return false;
  const points = quad.map(point => Array.isArray(point) && point.length === 2
    ? [Number(point[0]), Number(point[1])] : [NaN, NaN]);
  if (points.some(point => !point.every(Number.isFinite) || point.some(v => v < 0 || v > 1))) return false;
  let sign = 0;
  for (let i = 0; i < 4; i++) {
    const a = points[i], b = points[(i + 1) % 4], c = points[(i + 2) % 4];
    const cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
    if (Math.abs(cross) < 0.00000001) return false;
    const nextSign = Math.sign(cross);
    if (sign && nextSign !== sign) return false;
    sign = nextSign;
  }
  let area = 0;
  for (let i = 0; i < 4; i++) {
    const a = points[i], b = points[(i + 1) % 4];
    area += a[0] * b[1] - b[0] * a[1];
  }
  return Math.abs(area) >= 0.00002;
}

function _vzNormalizeTextureProjection(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const sourceRoles = raw.roles && typeof raw.roles === 'object' && !Array.isArray(raw.roles)
    ? raw.roles : {};
  const roles = {};
  for (const role of _VZ_PROJECTABLE_ROLES) {
    const source = sourceRoles[role];
    if (!source || source.mode !== 'perspective' || !Array.isArray(source.planes)) continue;
    const planes = [];
    for (const candidate of source.planes.slice(0, _VZ_MAX_PLANES)) {
      if (!candidate || typeof candidate !== 'object') continue;
      const id = String(candidate.id || '').toLowerCase();
      if (!/^[a-z0-9_-]{1,64}$/.test(id) || planes.some(plane => plane.id === id)) continue;
      const quad = Array.isArray(candidate.quad)
        ? candidate.quad.map(point => Array.isArray(point) ? point.map(_vzRoundCoord) : point) : null;
      if (!_vzQuadIsValid(quad)) continue;
      const scale = Math.max(0.1, Math.min(8, Number(candidate.scale) || 1));
      const quarterTurns = Math.max(0, Math.min(3, Math.round(Number(candidate.quarter_turns) || 0)));
      planes.push({id, quad, scale:Math.round(scale * 1000) / 1000, quarter_turns:quarterTurns});
    }
    if (planes.length) roles[role] = {mode:'perspective', planes};
  }
  return Object.keys(roles).length ? {version:1, roles} : null;
}

function _vzRoleProjection(role, create = false) {
  if (!_VZ_PROJECTABLE_ROLES.includes(role)) return null;
  const elevation = _vzElevation();
  if (!elevation.texture_projection && create) elevation.texture_projection = {version:1,roles:{}};
  const projection = elevation.texture_projection;
  if (!projection || !projection.roles) return null;
  if (!projection.roles[role] && create) projection.roles[role] = {mode:'perspective',planes:[]};
  return projection.roles[role] || null;
}

// Pull the trade's material product from the price book. The material is the
// first `s_*` or `m_*` product on the currently selected bundle for this tier
// — that's the one that carries `colors` and (for siding) `styles`.
function _vzBundleFor(trade, tier) {
  if (!priceBook) return null;
  const defaults = priceBook[trade + '_tier_defaults'] || {};
  const visual = (((S.visualizer || {}).selections || {})[trade] || {})[tier] || {};
  const td = (S.trades || {})[trade] || {};
  const bundleId = visual.bundle_id || (td.tier_bundles || {})[tier] || defaults[tier];
  const bundles = priceBook[trade + '_bundles'] || [];
  return bundles.find(b => b && b.id === bundleId) || null;
}
function _vzMaterialForBundle(trade, bundle) {
  if (!bundle || !priceBook) return null;
  const catalog = priceBook[trade + '_catalog'] || [];
  const pids = bundle.product_ids || [];
  // For roofing the material is `m_*`; for siding it's `s_*` (a bare shingle
  // or siding SKU, not an accessory prefixed `sa_`/`sl_`/`sx_`).
  for (const pid of pids) {
    if (trade === 'roofing' && pid.startsWith('m_')) {
      const p = catalog.find(x => x && x.id === pid);
      if (p) return p;
    }
    if (trade === 'siding' && pid.startsWith('s_') && !pid.startsWith('sa_') && !pid.startsWith('sl_') && !pid.startsWith('sx_')) {
      const p = catalog.find(x => x && x.id === pid);
      if (p) return p;
    }
  }
  return null;
}

// Colors for this ESTIMATE's picked bundle at the given tier — mirrors
// the server-side _bundle_colors_for_tier. Uses the estimate's actual
// td.tier_bundles pick when present; falls back to the price book default.
// Returns [{name, hex}] (hex may be missing).
function _bundleColorsForTradeTier(trade, tier) {
  if (!priceBook) return [];
  const td = (S.trades || {})[trade] || {};
  let bid = ((td.tier_bundles || {})[tier] || '').trim();
  if (!bid || bid === '__custom__') {
    bid = ((priceBook[trade + '_tier_defaults'] || {})[tier] || '').trim();
  }
  if (!bid) return [];
  const bundle = (priceBook[trade + '_bundles'] || []).find(b => b && b.id === bid);
  const mat = _vzMaterialForBundle(trade, bundle);
  const colors = (mat && mat.colors) || [];
  return colors
    .map(c => (typeof c === 'string' ? {name: c, hex: ''} : (c || {})))
    .filter(c => (c.name || '').trim());
}

async function renderVisualizerPage() {
  const container = document.getElementById('visualizer-content');
  if (!container) return;
  if (!vzState || vzState.owner !== S) _vzResetState();
  const state = vzState;
  if (!vzCapabilities) {
    try {
      const res = await fetch('/api/visualizer/capabilities');
      if (!res.ok) throw new Error('Unavailable');
      vzCapabilities = await res.json();
    } catch (_) { vzCapabilities = {auto_detect: false}; }
  }
  if (state !== vzState || state.owner !== S) return;
  _vzGet();
  const hasPhoto = !!(_vzElevation().base_image || vzState.pendingBaseDataUrl);
  container.innerHTML = _vzShellHtml(hasPhoto);
  _vzWireInputs();
  if (hasPhoto) {
    await _vzLoadWorkspacePhoto();
    _vzRenderPicker();
    _vzRedrawAll();
    _vzDetectionUI();
  }
}

function _vzShellHtml(hasPhoto) {
  if (!hasPhoto) {
    return `<div class="vz-wrap">
      <div class="vz-header">
        <div>
          <h2>🎨 Exterior Design Studio</h2>
          <p class="vz-sub">Upload each exterior elevation, then apply the products your team installs without painting every surface by hand.</p>
        </div>
      </div>
      ${_vzElevationTabsHtml()}
      <div class="vz-empty">
        <div class="vz-empty-actions">
          <button class="btn-primary vz-big-btn" onclick="_vzTriggerCamera()">📷 Take Photo</button>
          <button class="btn vz-big-btn" onclick="_vzTriggerUpload()">📁 Upload from Gallery</button>
        </div>
        <p class="vz-empty-tip">Tip: a clear front-of-house shot with the roof, walls, and entry door visible works best. No cars in the way if you can help it.</p>
        <p class="vz-picker-help">${vzCapabilities?.auto_detect ? 'Automatic selection is enabled. Only the project surfaces you check are sent to fal / SAM 3; each checked surface is one request.' : 'Automatic selection is not connected yet. A manager must enable the detection service before photo-only editing is available.'}</p>
      </div>
    </div>`;
  }
  const tier = vzState.activeTier;
  const brush = vzState.brushSize;
  return `<div class="vz-wrap">
    <div class="vz-header">
      <div>
        <h2>🎨 Exterior Design Studio</h2>
        <p class="vz-sub">Choose products and colors below. Automatic selection finds the surfaces; optional touch-ups stay under Refine selection.</p>
      </div>
      <div class="vz-header-actions">
        ${_meCanViewAll() ? '<button class="btn" onclick="openVisualizerOperations()">📊 Usage & storage</button>' : ''}
        <button class="btn" onclick="_vzTriggerUpload()">Replace this photo</button>
        <button class="btn" onclick="_vzShareDesign()" id="vz-share-btn">🔗 Share for approval</button>
        <button class="btn-primary" onclick="_vzSaveAll()" id="vz-save-btn">💾 Save Renderings</button>
      </div>
    </div>
    ${_vzElevationTabsHtml()}
    ${S.design_approval ? `<div class="vz-approval-status">Latest customer approval: <strong>${esc(S.design_approval.concept_name || S.design_approval.tier || 'a concept')}</strong> on ${esc((S.design_approval.approved_at || '').slice(0,10))}. Re-share after design changes; approval history is retained.</div>` : ''}
    <div id="vz-share-status" class="vz-share-status"></div>
    <div class="vz-body">
      <div class="vz-canvas-col">
        <div class="vz-workflow"><span>1 · Upload photo</span><span class="active">2 · Choose a look</span><span>3 · Save previews</span></div>
        <div class="vz-detection-panel">
          <div><strong>Automatic surface selection</strong><p id="vz-detection-message" role="status" aria-live="polite"></p></div>
          <button class="btn" id="vz-detect-btn" onclick="_vzAutoDetect()" ${vzCapabilities?.auto_detect ? '' : 'disabled'}>Detect surfaces</button>
        </div>
        ${_vzScopeHtml()}
        <div class="vz-preview-actions">
          <label><input type="checkbox" ${vzState.original?'checked':''} onchange="vzState.original=this.checked;_vzRedrawAll()"> Show original photo</label>
          <label class="vz-before-slider">Before / after <input type="range" min="0" max="100" value="${vzState.beforeSplit}" oninput="_vzSetBeforeSplit(this.value)"></label>
          <span>Approximate preview · confirm physical samples</span>
        </div>
        <div class="vz-canvas-wrap" id="vz-canvas-wrap">
          <canvas id="vz-canvas" class="vz-canvas"></canvas>
          <div class="vz-canvas-legend" id="vz-canvas-legend"></div>
        </div>
        <details class="vz-refine" ${vzState.refine?'open':''} ontoggle="_vzSetRefine(this.open)">
        <summary>Refine selection <span>Optional edge touch-ups</span></summary>
        <div class="vz-tools">
          ${_vzToolsHtml()}
          <div class="vz-tools-row">
            <label class="vz-brush">Brush
              <input type="range" min="8" max="120" value="${brush}" oninput="_vzSetBrush(this.value)">
              <span class="vz-brush-val">${brush}px</span>
            </label>
            <label class="vz-magic">
              <input type="checkbox" ${vzState.magicWand?'checked':''} onchange="_vzSetMagic(this.checked)">
              ✨ Magic Wand
            </label>
            ${_vzScopeRoles().map(role => `<button class="btn small" onclick="_vzClearMask('${role}')">Clear ${esc(_VZ_ROLE_META[role].label)}</button>`).join('')}
          </div>
        </div>
        </details>
        ${_vzAlignmentHtml()}
        ${_vzPlacementEditorHtml()}
      </div>
      <div class="vz-picker-col">
        <div class="vz-tier-tabs">
          ${['good','better','best'].map(t => `
            <button class="vz-tier-tab ${t===tier?'active':''}" data-tier="${t}" onclick="_vzSelectTier('${t}')">${_vzGet().favorite_tier===t?'★ ':''}${esc(_vzConceptName(t))}</button>
          `).join('')}
        </div>
        <div class="vz-concept-controls"><label>Concept name<input maxlength="40" value="${esc(_vzConceptName(tier))}" onchange="_vzRenameConcept('${tier}',this.value)"></label>
          <button class="btn small" onclick="_vzSetFavorite('${tier}')">${_vzGet().favorite_tier===tier?'★ Preferred':'☆ Mark preferred'}</button></div>
        <div id="vz-picker-body"></div>
      </div>
    </div>
    <div class="vz-triptych">
      <h3>Design concepts — side-by-side</h3>
      <div class="vz-triptych-grid" id="vz-triptych-grid">
        ${['good','better','best'].map(t => `
          <div class="vz-triptych-tile">
            <div class="vz-triptych-lbl">${_vzGet().favorite_tier===t?'★ ':''}${esc(_vzConceptName(t))}</div>
            <canvas class="vz-triptych-canvas" id="vz-thumb-${t}"></canvas>
            <div class="vz-triptych-cap" id="vz-thumb-cap-${t}"></div>
          </div>
        `).join('')}
      </div>
    </div>
  </div>`;
}

function _vzWireInputs() {
  const up = document.getElementById('vz-upload-input');
  const cam = document.getElementById('vz-camera-input');
  if (up)  up.onchange  = (e) => { _vzHandleFile(e.target.files[0]); e.target.value=''; };
  if (cam) cam.onchange = (e) => { _vzHandleFile(e.target.files[0]); e.target.value=''; };
}
function _vzTriggerCamera() { document.getElementById('vz-camera-input')?.click(); }
function _vzTriggerUpload() { document.getElementById('vz-upload-input')?.click(); }

function _vzElevationMetaPayload(extra) {
  const vz = _vzGet();
  const elevation = _vzElevation();
  return Object.assign({
    scope:[...vz.scope], concept_names:Object.assign({},vz.concept_names),
    favorite_tier:vz.favorite_tier || '', provia_specs:JSON.parse(JSON.stringify(vz.provia_specs || {})),
    active_elevation_id:vz.active_elevation_id,
    elevation_order:[...vz.elevation_order],
    elevation_names:Object.fromEntries(vz.elevation_order.map(id => [id,vz.elevations[id].name])),
    placement_updates:Object.fromEntries(vz.elevation_order.map(id => [id,
      JSON.parse(JSON.stringify(vz.elevations[id].placements || _vzEmptyPlacements()))])),
    projection_elevation_id:elevation.id,
    texture_projection:elevation.texture_projection
      ? JSON.parse(JSON.stringify(elevation.texture_projection)) : null
  }, extra || {});
}
async function _vzPersistMeta(extra) {
  if (!S.estimate_id) return;
  const res = await fetch('/api/estimates/' + encodeURIComponent(S.estimate_id) + '/visualizer/state', {
    method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(_vzElevationMetaPayload(extra))
  });
  if (!res.ok) throw new Error((await res.json().catch(()=>({}))).error || 'Could not save design settings.');
}
function _vzInvalidateRenders() {
  const vz = _vzGet();
  for (const ev of Object.values(vz.elevations)) ev.tier_renders = {};
  vz.tier_renders = {};
}
function _vzToggleScope(role, enabled) {
  if (!_VZ_ROLES.includes(role) || _vzVisualizerEditLocked()) return;
  const vz = _vzGet();
  const next = new Set(vz.scope);
  enabled ? next.add(role) : next.delete(role);
  if (!next.size) { alert('Keep at least one project surface selected.'); renderVisualizerPage(); return; }
  vz.scope = _VZ_ROLES.filter(r => next.has(r));
  if (vzState.activeTool !== 'erase' && !next.has(vzState.activeTool)) vzState.activeTool = vz.scope[0];
  _vzInvalidateRenders();
  _vzPlacementLayerCache.clear();
  vzState.selectionsChanged = true;
  vzState.dirty = true; setDirty();
  renderVisualizerPage();
}
function _vzRenameConcept(tier, value) {
  if (!TIERS.includes(tier)) return;
  _vzGet().concept_names[tier] = String(value || '').trim().slice(0,40) || TIER_LABELS[tier];
  setDirty(); renderVisualizerPage();
  _vzPersistMeta().catch(error => console.warn(error.message));
}
function _vzSetFavorite(tier) {
  if (!TIERS.includes(tier)) return;
  const vz = _vzGet();
  vz.favorite_tier = vz.favorite_tier === tier ? '' : tier;
  setDirty(); renderVisualizerPage();
  _vzPersistMeta().catch(error => console.warn(error.message));
}
function _vzSetBeforeSplit(value) {
  vzState.beforeSplit = Math.max(0, Math.min(100, parseInt(value,10) || 0));
  vzState.original = false;
  _vzRedrawAll();
}
function _vzNewElevationId(name) {
  const vz = _vzGet();
  const base = String(name || 'elevation').toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,'').slice(0,32) || 'elevation';
  let id = base, n = 2;
  while (vz.elevations[id]) id = (base.slice(0,28) + '_' + n++);
  return id;
}
async function _vzSwitchElevation(id) {
  const vz = _vzGet();
  if (!vz.elevations[id] || id === vz.active_elevation_id) return;
  if (vzState.dirty) { alert('Save the current elevation renderings before switching.'); return; }
  vz.active_elevation_id = id;
  _vzResetState();
  await renderVisualizerPage();
  _vzPersistMeta().catch(error => console.warn(error.message));
}
async function _vzAddElevation() {
  if (vzState.dirty) { alert('Save the current elevation renderings before adding another view.'); return; }
  if (_vzGet().elevation_order.length >= 12) { alert('A design project can contain up to 12 elevations.'); return; }
  const name = prompt('Name this view (for example Rear, Left side, or Garage):', 'Rear');
  if (!name || !name.trim()) return;
  const vz = _vzGet(), id = _vzNewElevationId(name);
  vz.elevations[id] = {id,name:name.trim().slice(0,60),base_image:null,masks:{},tier_renders:{},
    placements:_vzEmptyPlacements(),texture_projection:{version:1,roles:{}}};
  vz.elevation_order.push(id); vz.active_elevation_id = id;
  _vzResetState();
  setDirty();
  await _vzPersistMeta().catch(error => console.warn(error.message));
  await renderVisualizerPage();
  _vzTriggerUpload();
}
function _vzRenameElevation() {
  const ev = _vzElevation();
  const name = prompt('Elevation name:', ev.name);
  if (!name || !name.trim()) return;
  ev.name = name.trim().slice(0,60);
  setDirty(); renderVisualizerPage();
  _vzPersistMeta().catch(error => console.warn(error.message));
}
async function _vzDeleteElevation() {
  const vz = _vzGet(), ev = _vzElevation();
  if (vz.elevation_order.length < 2 || vzState.dirty) {
    if (vzState.dirty) alert('Save the current elevation before removing a view.');
    return;
  }
  if (!confirm('Remove ' + ev.name + ' from this design? Saved files remain on the server, but this view will no longer be shown.')) return;
  const removed = ev.id;
  delete vz.elevations[removed];
  vz.elevation_order = vz.elevation_order.filter(id => id !== removed);
  vz.active_elevation_id = vz.elevation_order[0];
  _vzResetState();
  await _vzPersistMeta({delete_elevation_id:removed});
  await renderVisualizerPage();
}
async function _vzShareDesign() {
  if (vzState?.dirty && !(await _vzSaveAll())) return;
  if (!S.estimate_id) { alert('Save the estimate and its design renderings first.'); return; }
  const button = document.getElementById('vz-share-btn');
  if (button) { button.disabled = true; button.textContent = 'Preparing link…'; }
  try {
    // Concept names and preferred status are metadata-only edits, so make
    // their save explicit before minting a customer-facing review link.
    await _vzPersistMeta();
    const res = await fetch('/api/estimates/' + encodeURIComponent(S.estimate_id) + '/visualizer/share', {method:'POST'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not create the review link.');
    const status = document.getElementById('vz-share-status');
    if (status) status.innerHTML = 'Customer review link: <a href="' + esc(data.full_url) + '" target="_blank" rel="noopener">' + esc(data.full_url) + '</a>';
    if (navigator.share) await navigator.share({title:'Exterior design review',url:data.full_url});
    else if (navigator.clipboard) { await navigator.clipboard.writeText(data.full_url); if (status) status.innerHTML += ' <strong>Copied.</strong>'; }
  } catch (error) { if (error.name !== 'AbortError') alert(error.message); }
  finally { if (button) { button.disabled = false; button.textContent = '🔗 Share for approval'; } }
}

async function _vzHandleFile(file) {
  if (!file || vzState?.saving || vzState?.proviaUploading) return;
  if (!vzState || vzState.owner !== S) _vzResetState();
  const owner = S;
  const previous = vzState;
  const currentElevation = _vzElevation();
  if (file.size > 25 * 1024 * 1024) { alert('Choose a photo smaller than 25 MB.'); return; }
  if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { alert('Choose a JPG, PNG, or WebP photo. Convert HEIC photos to JPG first.'); return; }
  if ((previous.pendingBaseDataUrl || currentElevation.base_image) &&
      !confirm('Replace the ' + currentElevation.name + ' photo? Its surface selections and saved previews will be cleared.')) return;
  try {
    const source = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('Could not read this photo.'));
      reader.readAsDataURL(file);
    });
    const img = await _vzReadImage(source);
    if (S !== owner || vzState !== previous) return;
    const scale = Math.min(1, 1200 / Math.max(img.naturalWidth, img.naturalHeight));
    const photo = _vzMakeMaskCanvas(Math.max(1, Math.round(img.naturalWidth * scale)), Math.max(1, Math.round(img.naturalHeight * scale)));
    photo.getContext('2d').drawImage(img, 0, 0, photo.width, photo.height);
    _vzResetState(); // invalidates any detection still running for the old photo
    vzState.pendingBaseDataUrl = photo.toDataURL('image/jpeg', 0.92);
    vzState.pendingBaseExt = 'jpg';
    vzState.photoKey = _vzGet().active_elevation_id + '_' + Date.now() + '_' + Math.random().toString(36).slice(2);
    const vz = _vzGet();
    const ev = _vzElevation();
    ev.masks = {}; ev.base_image = null; ev.tier_renders = {};
    ev.placements = _vzEmptyPlacements();
    delete ev.texture_projection;
    _vzProjectionCache.clear();
    if (ev.id === 'front') {
      for (const role of _VZ_ROLES) vz[role + '_mask'] = null;
      vz.base_image = null; vz.tier_renders = {};
    }
    vzState.dirty = true;
    setDirty();
    await renderVisualizerPage();
    if (S === owner && vzCapabilities?.auto_detect) await _vzAutoDetect(true);
  } catch (error) { alert(error.message || 'This photo could not be opened.'); }
}

function _vzReadImage(source) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('Could not load a design image. Please retry.'));
    img.src = source;
  });
}

function _vzIsCurrent(state, photoKey) {
  return vzState === state && state.owner === S && state.photoKey === photoKey;
}

async function _vzDetectionJSON(url, options) {
  const response = await fetch(url, options);
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || 'Automatic selection failed.');
  return result;
}

function _vzDetectionUI() {
  if (!vzState || vzState.owner !== S) return;
  const statuses = Object.entries(vzState.detectionStatus).map(([role, status]) => role + ': ' + status);
  const message = document.getElementById('vz-detection-message');
  if (message) message.textContent = statuses.length ? statuses.join(' · ')
    : !vzCapabilities?.auto_detect
    ? 'Not connected. A manager must configure EXTERIOR_AUTO_DETECT and FAL_KEY. No photo is sent until enabled.'
    : 'Detect ' + _vzScopeRoles().length + ' checked surface' + (_vzScopeRoles().length===1?'':'s') +
      ' using fal / SAM 3: ' + _vzScopeRoles().map(role => _VZ_ROLE_META[role].label).join(', ') + '. The photo is shared with fal once per checked surface; usage charges apply. Review the result before saving.';
  const button = document.getElementById('vz-detect-btn');
  if (button) {
    button.disabled = !vzCapabilities?.auto_detect || vzState.detecting || vzState.saving || vzState.proviaUploading || !vzState.photoImg;
    button.textContent = vzState.detecting ? 'Finding surfaces…' : 'Detect surfaces';
  }
  const save = document.getElementById('vz-save-btn');
  if (save) save.disabled = vzState.detecting || vzState.saving || vzState.proviaUploading || !vzState.photoImg;
}

async function _vzAutoDetect(initialUpload = false) {
  if (!vzCapabilities?.auto_detect || !vzState?.photoImg || vzState.detecting || vzState.saving || vzState.proviaUploading) return;
  const state = vzState;
  if (!state.photoKey) state.photoKey = 'photo_' + Date.now() + '_' + Math.random().toString(36).slice(2);
  const photoKey = state.photoKey;
  if (state.dirty && !initialUpload && !confirm('Detect again and replace any successfully detected surface selections?')) return;
  state.detecting = true;
  state.refine = false;
  const refinePanel = document.querySelector('.vz-refine');
  if (refinePanel) refinePanel.open = false;
  state.painting = false;
  const roles = _vzScopeRoles();
  state.detectionStatus = Object.fromEntries(roles.map(role => [role, 'waiting']));
  _vzDetectionUI();
  _vzRedrawAll();
  try {
    if (!S.estimate_id) await saveEstimate();
    if (!_vzIsCurrent(state, photoKey)) return;
    if (!S.estimate_id) throw new Error('Save the estimate before detecting surfaces.');
    const eid = S.estimate_id;
    const photo = _vzMakeMaskCanvas(state.canvas.width, state.canvas.height);
    photo.getContext('2d').drawImage(state.photoImg, 0, 0, photo.width, photo.height);
    const data = photo.toDataURL('image/jpeg', 0.9);
    await Promise.all(roles.map(async role => {
      try {
        const endpoint = '/api/estimates/' + encodeURIComponent(eid) + '/visualizer/detection';
        const job = await _vzDetectionJSON(endpoint, {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({role, photo_key: photoKey, image: data})});
        const deadline = Date.now() + 180000;
        while (_vzIsCurrent(state, photoKey) && Date.now() < deadline) {
          await new Promise(resolve => setTimeout(resolve, 2000));
          if (!_vzIsCurrent(state, photoKey)) return;
          const result = await _vzDetectionJSON(endpoint + '?ticket=' + encodeURIComponent(job.ticket));
          if (!_vzIsCurrent(state, photoKey)) return;
          if (result.status === 'pending') { state.detectionStatus[role] = 'detecting'; _vzDetectionUI(); continue; }
          if (result.photo_key !== photoKey || result.role !== role) throw new Error('Photo changed. Please detect again.');
          if (result.status === 'not_found') {
            state.detectionStatus[role] = 'not found — kept existing selection';
            _vzDetectionUI(); return;
          }
          if (result.status !== 'complete') throw new Error('Unexpected detection result.');
          const maskImg = await _vzReadImage(result.mask);
          if (!_vzIsCurrent(state, photoKey)) return;
          const mask = _vzMakeMaskCanvas(state.canvas.width, state.canvas.height);
          mask.getContext('2d').drawImage(maskImg, 0, 0, mask.width, mask.height);
          state[role + 'Mask'] = mask;
          state.detectionStatus[role] = 'ready — review edges';
          state.dirty = true;
          setDirty(); _vzRedrawAll(); _vzDetectionUI(); return;
        }
        if (_vzIsCurrent(state, photoKey)) throw new Error('Detection timed out. No new selection applied.');
      } catch (error) {
        if (_vzIsCurrent(state, photoKey)) { state.detectionStatus[role] = error.message; _vzDetectionUI(); }
      }
    }));
  } catch (error) {
    if (_vzIsCurrent(state, photoKey)) { state.detectionStatus = {photo: error.message}; _vzDetectionUI(); }
  } finally {
    state.detecting = false;
    if (_vzIsCurrent(state, photoKey)) { _vzDetectionUI(); _vzRedrawAll(); }
  }
}

function _vzSetRefine(open) {
  if (!vzState || vzState.owner !== S) return;
  vzState.refine = open;
  vzState.painting = false;
  if (open) {
    vzState.alignmentOpen = false;
    vzState.alignmentDragIndex = -1;
    vzState.placementOpen = false;
    vzState.placementDragIndex = -1;
    const align = document.querySelector('.vz-align');
    if (align) align.open = false;
    const placement = document.querySelector('.vz-place');
    if (placement) placement.open = false;
  }
  _vzRedrawAll();
}

async function _vzLoadWorkspacePhoto() {
  const state = vzState;
  const vz = _vzGet();
  const elevation = _vzElevation();
  try {
    const img = state.photoImg || await _vzReadImage(state.pendingBaseDataUrl || (BASE + '/uploads/' + elevation.base_image));
    if (state !== vzState || state.owner !== S) return;
    const scale = Math.min(1, 1200 / Math.max(img.naturalWidth, img.naturalHeight));
    const w = Math.max(1, Math.round(img.naturalWidth * scale));
    const h = Math.max(1, Math.round(img.naturalHeight * scale));
    const masks = await Promise.all(_VZ_ROLES.map(role =>
      state[role + 'Mask'] || _vzLoadMask((elevation.masks || {})[role] ||
        (elevation.id === 'front' ? vz[role + '_mask'] : null), w, h)));
    if (state !== vzState || state.owner !== S) return;
    const canvas = document.getElementById('vz-canvas');
    if (!canvas) return;
    state.photoImg = img;
    state.photoW = img.naturalWidth; state.photoH = img.naturalHeight;
    canvas.width = w; canvas.height = h;
    _VZ_ROLES.forEach((role, index) => { state[role + 'Mask'] = masks[index]; });
    _vzBindCanvasEvents(canvas);
  } catch (error) {
    if (state !== vzState || state.owner !== S) return;
    state.photoImg = null;
    state.detectionStatus = {photo: 'Could not restore this photo or its saved selections. Reload before editing or saving.'};
    _vzDetectionUI();
  }
}

async function _vzLoadMask(ref, w, h) {
  const canvas = _vzMakeMaskCanvas(w, h);
  if (ref) {
    const img = await _vzReadImage(BASE + '/uploads/' + ref);
    canvas.getContext('2d').drawImage(img, 0, 0, w, h);
  }
  return canvas;
}

function _vzMakeMaskCanvas(w, h) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  return c;
}

function _vzActiveMaskCanvas() {
  if (_VZ_ROLES.includes(vzState.activeTool)) return vzState[vzState.activeTool + 'Mask'];
  return null;   // erase applies to whichever mask has ink under the brush
}

function _vzCanvasCoords(canvas, evt) {
  const rect = canvas.getBoundingClientRect();
  const t = (evt.touches && evt.touches[0]) || evt.changedTouches && evt.changedTouches[0] || evt;
  const x = (t.clientX - rect.left) * (canvas.width  / rect.width);
  const y = (t.clientY - rect.top)  * (canvas.height / rect.height);
  return { x, y };
}

function _vzBindCanvasEvents(canvas) {
  // Rebuild by cloning to drop stale listeners from a prior render.
  const clone = canvas.cloneNode(false);
  canvas.parentNode.replaceChild(clone, canvas);
  vzState.canvas = clone;
  vzState.ctx = clone.getContext('2d');

  const onStart = (e) => {
    if (vzState.detecting || vzState.saving) return;
    if (vzState.placementOpen) {
      e.preventDefault();
      const {x,y}=_vzCanvasCoords(clone,e);
      const handle=_vzPlacementHandleAt(x,y,clone);
      const slot=_vzActivePlacementSlot();
      if(handle<0 && (!slot || !_vzPointInQuad(x/clone.width,y/clone.height,slot.quad)))return;
      vzState.placementDragIndex=handle;
      vzState.placementMoveWhole=handle<0;
      vzState.placementLastPoint=[x/clone.width,y/clone.height];
      clone.setPointerCapture?.(e.pointerId);
      _vzRedrawAll(true);
      return;
    }
    if (vzState.alignmentOpen) {
      e.preventDefault();
      const {x,y} = _vzCanvasCoords(clone,e);
      const hit = _vzProjectionHandleAt(x,y,clone);
      if (hit < 0) return;
      vzState.alignmentDragIndex = hit;
      clone.setPointerCapture?.(e.pointerId);
      _vzRedrawAll(true);
      return;
    }
    if (!vzState.refine || vzState.original) return;
    e.preventDefault();
    clone.setPointerCapture?.(e.pointerId);
    if (vzState.magicWand) {
      const { x, y } = _vzCanvasCoords(clone, e);
      _vzFloodFill(Math.round(x), Math.round(y));
      _vzRedrawAll();
      return;
    }
    vzState.painting = true;
    const { x, y } = _vzCanvasCoords(clone, e);
    vzState.lastX = x; vzState.lastY = y;
    _vzPaintDot(x, y);
    _vzRedrawAll();
  };
  const onMove = (e) => {
    if (vzState.saving) return;
    if (vzState.placementDragIndex>=0 || vzState.placementMoveWhole) {
      e.preventDefault();
      const {x,y}=_vzCanvasCoords(clone,e);
      _vzMovePlacement(x,y,clone);
      return;
    }
    if (vzState.alignmentDragIndex >= 0) {
      e.preventDefault();
      const {x,y} = _vzCanvasCoords(clone,e);
      _vzMoveProjectionHandle(x,y,clone);
      return;
    }
    if (!vzState.painting) return;
    e.preventDefault();
    const { x, y } = _vzCanvasCoords(clone, e);
    _vzPaintLine(vzState.lastX, vzState.lastY, x, y);
    vzState.lastX = x; vzState.lastY = y;
    _vzRedrawAll();
  };
  const onEnd = (e) => {
    if (vzState.placementDragIndex>=0 || vzState.placementMoveWhole) {
      const slot=_vzActivePlacementSlot();
      if(slot)slot.quad=slot.quad.map(point=>point.map(_vzRoundCoord));
      vzState.placementDragIndex=-1;vzState.placementMoveWhole=false;vzState.placementLastPoint=null;
      _vzPlacementChanged();
    }
    if (vzState.alignmentDragIndex >= 0) {
      const plane = _vzActiveProjectionPlane();
      if (plane) plane.quad = plane.quad.map(point => point.map(_vzRoundCoord));
      vzState.alignmentDragIndex = -1;
      _vzProjectionChanged();
    }
    vzState.painting = false;
    if (e?.pointerId != null && clone.hasPointerCapture?.(e.pointerId)) clone.releasePointerCapture(e.pointerId);
  };

  clone.addEventListener('pointerdown', onStart);
  clone.addEventListener('pointermove', onMove);
  clone.addEventListener('pointerup', onEnd);
  clone.addEventListener('pointercancel', onEnd);
  clone.addEventListener('lostpointercapture', onEnd);
}

function _vzProjectionHandleAt(x,y,canvas) {
  const plane = _vzActiveProjectionPlane();
  if (!plane) return -1;
  const rect = canvas.getBoundingClientRect();
  const radius = Math.max(14, 22 * canvas.width / Math.max(1,rect.width));
  let nearest = -1, nearestDistance = radius;
  plane.quad.forEach((point,index) => {
    const distance = Math.hypot(x - point[0] * canvas.width, y - point[1] * canvas.height);
    if (distance <= nearestDistance) { nearest = index; nearestDistance = distance; }
  });
  return nearest;
}
function _vzMoveProjectionHandle(x,y,canvas) {
  const plane = _vzActiveProjectionPlane(), index = vzState.alignmentDragIndex;
  if (!plane || index < 0 || index > 3) return;
  const candidate = plane.quad.map(point => [...point]);
  candidate[index] = [Math.max(0,Math.min(1,x/canvas.width)),Math.max(0,Math.min(1,y/canvas.height))];
  if (!_vzQuadIsValid(candidate)) return;
  plane.quad = candidate;
  vzState.dirty = true;
  vzState.projectionChanged = true;
  _vzProjectionCache.clear();
  setDirty();
  _vzQueueAlignmentRedraw();
}

function _vzPointInQuad(x,y,quad) {
  if(!_vzQuadIsValid(quad))return false;
  let inside=false;
  for(let i=0,j=quad.length-1;i<quad.length;j=i++){
    const xi=quad[i][0],yi=quad[i][1],xj=quad[j][0],yj=quad[j][1];
    if(((yi>y)!==(yj>y)) && x < (xj-xi)*(y-yi)/(yj-yi)+xi)inside=!inside;
  }
  return inside;
}
function _vzPlacementHandleAt(x,y,canvas) {
  const slot=_vzActivePlacementSlot();if(!slot)return -1;
  const rect=canvas.getBoundingClientRect();
  const radius=Math.max(14,22*canvas.width/Math.max(1,rect.width));
  let nearest=-1,distanceLimit=radius;
  slot.quad.forEach((point,index)=>{const distance=Math.hypot(x-point[0]*canvas.width,y-point[1]*canvas.height);if(distance<=distanceLimit){nearest=index;distanceLimit=distance;}});
  return nearest;
}
function _vzMovePlacement(x,y,canvas) {
  const slot=_vzActivePlacementSlot();if(!slot)return;
  const point=[Math.max(0,Math.min(1,x/canvas.width)),Math.max(0,Math.min(1,y/canvas.height))];
  let candidate=slot.quad.map(value=>[...value]);
  if(vzState.placementMoveWhole){
    const last=vzState.placementLastPoint||point,dx=point[0]-last[0],dy=point[1]-last[1];
    const minX=Math.min(...candidate.map(p=>p[0])),maxX=Math.max(...candidate.map(p=>p[0]));
    const minY=Math.min(...candidate.map(p=>p[1])),maxY=Math.max(...candidate.map(p=>p[1]));
    const safeDx=Math.max(-minX,Math.min(1-maxX,dx)),safeDy=Math.max(-minY,Math.min(1-maxY,dy));
    candidate=candidate.map(([px,py])=>[px+safeDx,py+safeDy]);
    // Track the pointer itself even when the opening is pinned to an edge.
    // Otherwise reversing direction first has to cross the overshoot distance.
    vzState.placementLastPoint=point;
  }else if(vzState.placementDragIndex>=0){candidate[vzState.placementDragIndex]=point;}
  if(!_vzQuadIsValid(candidate))return;
  slot.quad=candidate;vzState.dirty=true;vzState.placementsChanged=true;setDirty();_vzQueuePlacementRedraw();
}
function _vzQueuePlacementRedraw(){
  if(vzState.placementFrame)return;
  const schedule=typeof requestAnimationFrame==='function'?requestAnimationFrame:(fn=>setTimeout(fn,16));
  vzState.placementFrame=schedule(()=>{vzState.placementFrame=0;_vzRedrawAll(true);});
}

function _vzPaintDot(x, y) {
  const r = vzState.brushSize / 2;
  if (vzState.activeTool === 'erase') {
    // Erase from both masks so the rep doesn't have to remember which layer
    // an old stroke landed in.
    for (const c of _VZ_ROLES.map(role => vzState[role + 'Mask'])) {
      const ctx = c.getContext('2d');
      ctx.save();
      ctx.globalCompositeOperation = 'destination-out';
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
    }
    vzState.dirty = true; setDirty();
    return;
  }
  const mask = _vzActiveMaskCanvas();
  if (!mask) return;
  const ctx = mask.getContext('2d');
  ctx.save();
  ctx.fillStyle = 'rgba(255,255,255,1)';
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
  ctx.restore();
  vzState.dirty = true; setDirty();
}
function _vzPaintLine(x1, y1, x2, y2) {
  // Coarser than a per-pixel line — the brush dot fills between samples on
  // most gestures. This just guarantees no gap on a fast swipe.
  const dx = x2 - x1, dy = y2 - y1;
  const steps = Math.max(1, Math.ceil(Math.hypot(dx, dy) / (vzState.brushSize / 3)));
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    _vzPaintDot(x1 + dx * t, y1 + dy * t);
  }
}

// Scanline flood-fill on the base photo, adding pixels within a color
// tolerance of the tap point to the active mask. Tolerance 40 in each
// channel is coarse on purpose — a real roof photo has JPEG noise plus
// shingle granule contrast, so a strict test fills nothing.
function _vzFloodFill(sx, sy) {
  if (vzState.activeTool === 'erase') return;
  const mask = _vzActiveMaskCanvas();
  if (!mask || !vzState.photoImg) return;
  const W = mask.width, H = mask.height;
  if (sx < 0 || sy < 0 || sx >= W || sy >= H) return;
  // Cache the down-sampled photo pixel data on the off-screen canvas so we
  // don't re-decode every flood.
  if (!vzState._photoData) {
    const pc = document.createElement('canvas');
    pc.width = W; pc.height = H;
    const pctx = pc.getContext('2d');
    pctx.drawImage(vzState.photoImg, 0, 0, W, H);
    vzState._photoData = pctx.getImageData(0, 0, W, H);
  }
  const photoData = vzState._photoData;
  const srcPx = photoData.data;
  const idx0 = (sy * W + sx) * 4;
  const r0 = srcPx[idx0], g0 = srcPx[idx0 + 1], b0 = srcPx[idx0 + 2];
  const TOL = 40;
  const maskCtx = mask.getContext('2d');
  const maskData = maskCtx.getImageData(0, 0, W, H);
  const dst = maskData.data;
  const stack = [[sx, sy]];
  const visited = new Uint8Array(W * H);
  visited[sy * W + sx] = 1;
  while (stack.length) {
    const [x, y] = stack.pop();
    const p = (y * W + x) * 4;
    const dr = Math.abs(srcPx[p]     - r0);
    const dg = Math.abs(srcPx[p + 1] - g0);
    const db = Math.abs(srcPx[p + 2] - b0);
    if (dr > TOL || dg > TOL || db > TOL) continue;
    dst[p + 3] = 255;   // opaque in the mask
    dst[p]     = 255;
    dst[p + 1] = 255;
    dst[p + 2] = 255;
    for (const [nx, ny] of [[x-1,y],[x+1,y],[x,y-1],[x,y+1]]) {
      if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
      const nk = ny * W + nx;
      if (visited[nk]) continue;
      visited[nk] = 1;
      stack.push([nx, ny]);
    }
  }
  maskCtx.putImageData(maskData, 0, 0);
  vzState.dirty = true; setDirty();
}

function _vzSetTool(t)   { if (t === 'erase' || _vzScopeRoles().includes(t)) vzState.activeTool = t; renderVisualizerPage(); }
function _vzSetBrush(v)  { vzState.brushSize = parseInt(v, 10) || 30; const el = document.querySelector('.vz-brush-val'); if (el) el.textContent = vzState.brushSize + 'px'; }
function _vzSetMagic(on) { vzState.magicWand = !!on; }
function _vzClearMask(role) {
  if (vzState.detecting || vzState.saving) return;
  const c = _VZ_ROLES.includes(role) ? vzState[role + 'Mask'] : null;
  if (!c) return;
  c.getContext('2d').clearRect(0, 0, c.width, c.height);
  vzState.dirty = true; setDirty();
  _vzRedrawAll();
}
function _vzSelectTier(t) {
  vzState.activeTier = t;
  renderVisualizerPage();
}

function _vzPalette(product) {
  return ((product || {}).colors || []).filter(c => c && typeof c === 'object' &&
    typeof c.name === 'string' && /^#[0-9a-f]{6}$/i.test(c.hex || ''));
}
function _vzExteriorGroups(trade) {
  const entries = Array.isArray((priceBook || {}).exterior_catalog)
    ? priceBook.exterior_catalog : [];
  const wanted = trade === 'roofing' ? 'roof'
    : trade === 'doors' ? 'door' : trade;
  const groups = new Map();
  for (const entry of entries) {
    if (!entry || entry.active === false) continue;
    const applies = entry.category === 'paint' ? entry.applies_to : entry.category;
    if (applies !== wanted) continue;
    const id = entry.product_id || [entry.category, entry.brand, entry.product,
      entry.applies_to, entry.price_book_bundle].join('|');
    if (!groups.has(id)) {
      const brand = (entry.brand || '').trim(), productName = (entry.product || '').trim();
      let label = (brand && !productName.toLowerCase().startsWith(brand.toLowerCase())
        ? brand + ' ' + productName : productName || brand) || 'Unnamed product';
      if (entry.category !== 'siding' && (entry.style || '').trim()) label += ' — ' + entry.style.trim();
      groups.set(id, {
        id: id, category: entry.category, brand: entry.brand || '',
        product: entry.product || '', name: (entry.category === 'paint' ? 'Paint · ' : '') + label,
        bundle_id: entry.price_book_bundle || '', colors: [], styles: []
      });
    }
    const group = groups.get(id);
    if (!group.bundle_id && entry.price_book_bundle) group.bundle_id = entry.price_book_bundle;
    const colorLabel = entry.color + (entry.color_code ? ' · ' + entry.color_code : '');
    const existingColor = group.colors.find(c => c.name === colorLabel && c.hex === entry.hex);
    if (!existingColor) {
      group.colors.push({name: colorLabel, hex: entry.hex, code: entry.color_code || '',
        texture_ref:entry.texture_ref || '', texture_scale:Number(entry.texture_scale) || 96,
        placement_image_ref:entry.placement_image_ref || ''});
    } else {
      // A siding color is repeated once per profile. Older catalogs may only
      // have an asset on a later row, so do not let the first profile silently
      // discard that texture/cutout while the rows are folded into one picker.
      if (!existingColor.texture_ref && entry.texture_ref) {
        existingColor.texture_ref = entry.texture_ref;
        existingColor.texture_scale = Number(entry.texture_scale) || 96;
      }
      if (!existingColor.placement_image_ref && entry.placement_image_ref) {
        existingColor.placement_image_ref = entry.placement_image_ref;
      }
    }
    const styleName = entry.category === 'siding' ? (entry.style || '').trim() : '';
    if (styleName && !group.styles.some(s => s.name === styleName)) {
      group.styles.push({
        id: styleName.toLowerCase().replace(/[^a-z0-9]+/g, '_'),
        name: styleName, pattern_id: entry.pattern_id || ''
      });
    }
  }
  return Array.from(groups.values()).sort((a, b) => {
    if (a.category === 'paint' && b.category !== 'paint') return 1;
    if (b.category === 'paint' && a.category !== 'paint') return -1;
    return a.name.localeCompare(b.name);
  });
}
const _VZ_EXTERIOR_PRODUCT_ALIASES = {
  // The shipped LP bundle used a spaced "Expert Finish" label before the
  // canonical catalog migration. Saved estimates keep their snapshot id.
  extp_edc4c81d9e23ed: 'extp_ecb4a2dc0ddd14'
};
function _vzExteriorProductForSelection(trade, selected) {
  const groups = _vzExteriorGroups(trade);
  if (!groups.length) return null;
  selected = selected || {};
  const savedId = selected.exterior_product_id || selected.option_id || '';
  const resolvedId = _VZ_EXTERIOR_PRODUCT_ALIASES[savedId] || savedId;
  const direct = groups.find(p => p.id === resolvedId);
  if (direct) return direct;
  // More than one visual family may legitimately share a price-book bundle
  // (for example LP core and Naturals). Use the saved color to disambiguate an
  // older snapshot before falling back to the first product in that bundle.
  const colorMatch = groups.find(p => p.bundle_id && p.bundle_id === selected.bundle_id &&
    _vzPalette(p).some(c => c.name === selected.color_name && c.hex === selected.color_hex));
  return colorMatch || groups.find(p => p.bundle_id && p.bundle_id === selected.bundle_id) || null;
}
function _vzCatalogColorForSelection(trade, selected) {
  const product = _vzExteriorProductForSelection(trade, selected || {});
  const palette = _vzPalette(product);
  if (!palette.length) return null;
  return palette.find(c => c.name === selected.color_name && c.hex === selected.color_hex) ||
    palette.find(c => c.name === selected.color_name) ||
    palette.find(c => c.hex === selected.color_hex) || null;
}
function _vzEffectiveExteriorSelection(trade, selected) {
  if (!selected || selected.texture_ref) return selected || {};
  const catalogColor = _vzCatalogColorForSelection(trade, selected);
  if (!catalogColor || !catalogColor.texture_ref) return selected;
  // Resolve newly delivered catalog assets for rendering without rewriting
  // the historical selection snapshot stored on the estimate.
  return Object.assign({}, selected, {
    texture_ref: catalogColor.texture_ref,
    texture_scale: catalogColor.texture_scale || selected.texture_scale || 96
  });
}
function _vzExteriorSelection(product) {
  const color = _vzPalette(product)[0] || {};
  const style = (product.styles || [])[0] || {};
  return {
    exterior_product_id: product.id, product_name: product.name,
    product_category: product.category, bundle_id: product.bundle_id || '',
    bundle_name: product.name, color_hex: color.hex || '', color_name: color.name || '',
    texture_ref:color.texture_ref || '', texture_scale:color.texture_scale || 96,
    placement_image_ref:color.placement_image_ref || '',
    style_id: style.id || '', style_name: style.name || '', pattern_id: style.pattern_id || ''
  };
}

function _vzConceptName(tier) {
  return (_vzGet().concept_names || {})[tier] || TIER_LABELS[tier] || tier;
}
function _vzElevationTabsHtml() {
  const vz = _vzGet();
  const tabs = vz.elevation_order.map(id => {
    const ev = vz.elevations[id];
    return `<button class="vz-elevation-tab ${id===vz.active_elevation_id?'active':''}" onclick="_vzSwitchElevation('${id}')">
      <span>${esc(ev.name)}</span>${ev.base_image?'<small>saved</small>':'<small>needs photo</small>'}</button>`;
  }).join('');
  return `<div class="vz-elevation-bar"><div class="vz-elevation-tabs">${tabs}</div>
    <div class="vz-elevation-actions"><button class="btn small" onclick="_vzRenameElevation()">Rename</button>
    <button class="btn small" onclick="_vzAddElevation()">+ Elevation</button>
    ${vz.elevation_order.length>1?'<button class="btn small danger" onclick="_vzDeleteElevation()">Remove</button>':''}</div></div>`;
}
function _vzScopeHtml() {
  const selected = new Set(_vzScopeRoles());
  return `<div class="vz-scope"><strong>Surfaces on this project</strong><span>Each checked surface uses one fal request when detected.</span>
    <div class="vz-scope-grid">${_VZ_ROLES.map(role => `<label class="${selected.has(role)?'active':''}">
      <input type="checkbox" ${selected.has(role)?'checked':''} onchange="_vzToggleScope('${role}',this.checked)">
      ${_VZ_ROLE_META[role].icon} ${esc(_VZ_ROLE_META[role].label)}</label>`).join('')}</div></div>`;
}
function _vzToolsHtml() {
  const roles = _vzScopeRoles();
  return `<div class="vz-tools-row">${roles.map(role => `<label class="vz-tool ${vzState.activeTool===role?'active':''}">
      <input type="radio" name="vz-tool" value="${role}" ${vzState.activeTool===role?'checked':''} onchange="_vzSetTool('${role}')">
      <span>${_VZ_ROLE_META[role].icon} ${esc(_VZ_ROLE_META[role].label)}</span></label>`).join('')}
    <label class="vz-tool ${vzState.activeTool==='erase'?'active':''}"><input type="radio" name="vz-tool" value="erase" ${vzState.activeTool==='erase'?'checked':''} onchange="_vzSetTool('erase')"><span>🧽 Erase</span></label></div>`;
}
function _vzProjectableScopeRoles() {
  return _vzScopeRoles().filter(role => _VZ_PROJECTABLE_ROLES.includes(role));
}
function _vzAlignmentHtml() {
  const roles = _vzProjectableScopeRoles();
  if (!roles.length) return '';
  if (!roles.includes(vzState.alignmentRole)) vzState.alignmentRole = roles[0];
  const role = vzState.alignmentRole;
  const projection = _vzRoleProjection(role);
  const planes = (projection && projection.planes) || [];
  if (!planes.some(plane => plane.id === vzState.alignmentPlaneId)) {
    vzState.alignmentPlaneId = planes[0]?.id || '';
  }
  const plane = planes.find(candidate => candidate.id === vzState.alignmentPlaneId);
  const trade = _VZ_ROLE_META[role].trade;
  const selected = _vzEffectiveExteriorSelection(trade,
    ((_vzGet().selections[trade] || {})[vzState.activeTier] || {}));
  const hasVisualLayer = !!(selected.texture_ref || selected.pattern_id);
  return `<details class="vz-align" ${vzState.alignmentOpen?'open':''} ontoggle="_vzSetAlignmentOpen(this.open)">
    <summary>Align material perspective <span>Optional realism for roof and wall planes</span></summary>
    <div class="vz-align-body">
      <div class="vz-align-controls">
        <label class="vz-field">Surface<select onchange="_vzSetAlignmentRole(this.value)">
          ${roles.map(item => `<option value="${item}" ${item===role?'selected':''}>${esc(_VZ_ROLE_META[item].label)}</option>`).join('')}
        </select></label>
        ${planes.length ? `<label class="vz-field">Plane<select onchange="_vzSetAlignmentPlane(this.value)">
          ${planes.map((item,index) => `<option value="${item.id}" ${item.id===vzState.alignmentPlaneId?'selected':''}>Plane ${index+1}</option>`).join('')}
        </select></label>` : ''}
      </div>
      <p class="vz-align-help">${hasVisualLayer
        ? 'Drag the four numbered corners clockwise around one flat roof or wall plane. Add another plane for a second slope or receding wall.'
        : 'Choose a product texture or generated style pattern first. You can still outline the plane now and the alignment will be reused across all three concepts.'}</p>
      ${plane ? `<label class="vz-align-scale">Material size
          <input type="range" min="10" max="800" step="5" value="${Math.round(plane.scale*100)}"
            oninput="_vzSetProjectionScale(this.value,false)" onchange="_vzSetProjectionScale(this.value,true)">
          <span>${Math.round(plane.scale*100)}%</span></label>` : ''}
      <div class="vz-align-actions">
        <button class="btn small" onclick="_vzAddProjectionPlane()" ${planes.length>=_VZ_MAX_PLANES?'disabled':''}>+ Plane</button>
        ${plane ? '<button class="btn small" onclick="_vzResetProjectionPlane()">Reset to mask</button><button class="btn small" onclick="_vzRotateProjectionPlane()">Rotate 90°</button><button class="btn small danger" onclick="_vzRemoveProjectionPlane()">Remove plane</button>' : ''}
        ${projection ? '<button class="btn small" onclick="_vzUseFlatTexture()">Use flat texture</button>' : ''}
      </div>
      ${plane ? '<div class="vz-align-key"><span>1 Top left</span><span>2 Top right</span><span>3 Bottom right</span><span>4 Bottom left</span></div>' : '<p class="vz-align-empty">Add a plane to begin.</p>'}
    </div>
  </details>`;
}

function _vzSetAlignmentOpen(open) {
  if (!vzState || vzState.owner !== S) return;
  if (_vzVisualizerEditLocked() && !!open !== vzState.alignmentOpen) return;
  vzState.alignmentOpen = !!open;
  vzState.alignmentDragIndex = -1;
  if (open) {
    vzState.refine = false;
    vzState.placementOpen = false;
    vzState.placementDragIndex = -1;
    vzState.original = false;
    const refine = document.querySelector('.vz-refine');
    if (refine) refine.open = false;
    const placement = document.querySelector('.vz-place');
    if (placement) placement.open = false;
  }
  _vzRedrawAll();
}
function _vzSetAlignmentRole(role) {
  if (_vzVisualizerEditLocked()) return;
  if (!_vzProjectableScopeRoles().includes(role)) return;
  vzState.alignmentRole = role;
  const planes = (_vzRoleProjection(role) || {}).planes || [];
  vzState.alignmentPlaneId = planes[0]?.id || '';
  renderVisualizerPage();
}
function _vzSetAlignmentPlane(id) {
  if (_vzVisualizerEditLocked()) return;
  const planes = (_vzRoleProjection(vzState.alignmentRole) || {}).planes || [];
  if (!planes.some(plane => plane.id === id)) return;
  vzState.alignmentPlaneId = id;
  _vzRedrawAll();
}
function _vzActiveProjectionPlane() {
  const planes = (_vzRoleProjection(vzState.alignmentRole) || {}).planes || [];
  return planes.find(plane => plane.id === vzState.alignmentPlaneId) || null;
}
function _vzMaskBoundsQuad(role) {
  const mask = _VZ_PROJECTABLE_ROLES.includes(role) ? vzState[role + 'Mask'] : null;
  if (!mask) return null;
  const data = mask.getContext('2d').getImageData(0,0,mask.width,mask.height).data;
  let minX = mask.width, minY = mask.height, maxX = -1, maxY = -1;
  for (let y = 0; y < mask.height; y++) {
    for (let x = 0; x < mask.width; x++) {
      if (!data[(y * mask.width + x) * 4 + 3]) continue;
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
    }
  }
  if (maxX < minX || maxY < minY) return null;
  let x0 = Math.max(0, minX / mask.width - 0.01);
  let y0 = Math.max(0, minY / mask.height - 0.01);
  let x1 = Math.min(1, (maxX + 1) / mask.width + 0.01);
  let y1 = Math.min(1, (maxY + 1) / mask.height + 0.01);
  const minSpan = 0.04;
  if (x1 - x0 < minSpan) { const mid=(x0+x1)/2; x0=Math.max(0,mid-minSpan/2); x1=Math.min(1,mid+minSpan/2); }
  if (y1 - y0 < minSpan) { const mid=(y0+y1)/2; y0=Math.max(0,mid-minSpan/2); y1=Math.min(1,mid+minSpan/2); }
  const quad = [[x0,y0],[x1,y0],[x1,y1],[x0,y1]].map(point => point.map(_vzRoundCoord));
  return _vzQuadIsValid(quad) ? quad : null;
}
function _vzProjectionChanged(fullRedraw = true) {
  const vz = _vzGet(), elevation = _vzElevation();
  elevation.tier_renders = {};
  if (elevation.id === 'front') vz.tier_renders = {};
  vzState.projectionChanged = true;
  vzState.dirty = true;
  _vzProjectionCache.clear();
  setDirty();
  if (fullRedraw) _vzRedrawAll();
}
function _vzVisualizerEditLocked() {
  return !vzState || vzState.saving || vzState.detecting || vzState.proviaUploading;
}
function _vzAddProjectionPlane() {
  if (_vzVisualizerEditLocked()) return;
  const role = vzState.alignmentRole;
  const quad = _vzMaskBoundsQuad(role);
  if (!quad) { alert('Select this surface first, then add its perspective plane.'); return; }
  const projection = _vzRoleProjection(role, true);
  if (!projection || projection.planes.length >= _VZ_MAX_PLANES) return;
  let number = 1;
  while (projection.planes.some(plane => plane.id === 'plane_' + number)) number++;
  const plane = {id:'plane_' + number,quad,scale:1,quarter_turns:0};
  projection.planes.push(plane);
  vzState.alignmentPlaneId = plane.id;
  vzState.alignmentOpen = true;
  _vzProjectionChanged(false);
  renderVisualizerPage();
}
function _vzRemoveProjectionPlane() {
  if (_vzVisualizerEditLocked()) return;
  const elevation = _vzElevation(), projection = _vzRoleProjection(vzState.alignmentRole);
  if (!projection) return;
  projection.planes = projection.planes.filter(plane => plane.id !== vzState.alignmentPlaneId);
  vzState.alignmentPlaneId = projection.planes[0]?.id || '';
  if (!projection.planes.length) delete elevation.texture_projection.roles[vzState.alignmentRole];
  if (!Object.keys(elevation.texture_projection.roles).length) delete elevation.texture_projection;
  _vzProjectionChanged(false);
  renderVisualizerPage();
}
function _vzUseFlatTexture() {
  if (_vzVisualizerEditLocked()) return;
  const elevation = _vzElevation();
  if (!elevation.texture_projection?.roles?.[vzState.alignmentRole]) return;
  delete elevation.texture_projection.roles[vzState.alignmentRole];
  if (!Object.keys(elevation.texture_projection.roles).length) delete elevation.texture_projection;
  vzState.alignmentPlaneId = '';
  _vzProjectionChanged(false);
  renderVisualizerPage();
}
function _vzResetProjectionPlane() {
  if (_vzVisualizerEditLocked()) return;
  const plane = _vzActiveProjectionPlane(), quad = _vzMaskBoundsQuad(vzState.alignmentRole);
  if (!plane || !quad) return;
  plane.quad = quad; plane.scale = 1; plane.quarter_turns = 0;
  _vzProjectionChanged();
}
function _vzRotateProjectionPlane() {
  if (_vzVisualizerEditLocked()) return;
  const plane = _vzActiveProjectionPlane();
  if (!plane) return;
  plane.quarter_turns = ((plane.quarter_turns || 0) + 1) % 4;
  _vzProjectionChanged();
  renderVisualizerPage();
}
function _vzSetProjectionScale(value, complete) {
  if (_vzVisualizerEditLocked()) return;
  const plane = _vzActiveProjectionPlane();
  if (!plane) return;
  plane.scale = Math.round(Math.max(10, Math.min(800, Number(value) || 100))) / 100;
  _vzProjectionChanged(false);
  const label = document.querySelector('.vz-align-scale span');
  if (label) label.textContent = Math.round(plane.scale * 100) + '%';
  complete ? _vzRedrawAll() : _vzQueueAlignmentRedraw();
}

function _vzPlacementDoc() {
  const elevation = _vzElevation();
  elevation.placements = _vzNormalizePlacements(elevation.placements);
  return elevation.placements;
}
function _vzPlacementSlots() {
  return Object.values(_vzPlacementDoc().slots).sort((a,b) => (a.z||0)-(b.z||0) || a.id.localeCompare(b.id));
}
function _vzActivePlacementSlot() {
  return _vzPlacementDoc().slots[vzState.placementSlotId] || null;
}
function _vzDoorConfigurationFingerprint(selected,provia) {
  return [selected.exterior_product_id||selected.option_id||'',selected.color_name||'',
    provia.access_code||'',provia.series||'',provia.model||'',provia.glass||'',
    provia.hardware||'',provia.swing||''].join('|').slice(0,200);
}
function _vzMarkProViaImageStale(tier=vzState.activeTier) {
  const spec=(_vzGet().provia_specs||{})[tier];
  if(spec?.configured_image)spec.configured_for='stale';
}
function _vzPlacementSource(role, tier = vzState.activeTier) {
  if (!_VZ_PLACEMENT_ROLES.includes(role)) return null;
  const vz = _vzGet(), trade = _VZ_ROLE_META[role].trade;
  const selected = (vz.selections[trade] || {})[tier] || {};
  const provia = role === 'door' ? (vz.provia_specs[tier] || {}) : {};
  const doorFingerprint=role==='door'?_vzDoorConfigurationFingerprint(selected,provia):'';
  if(role==='door'&&provia.configured_image&&!provia.configured_for)provia.configured_for=doorFingerprint;
  const configuredCurrent=role==='door'&&provia.configured_image&&provia.configured_for===doorFingerprint;
  // A current Envision upload is more specific than the catalog cutout (it
  // can include the chosen glass, hardware, and handing). A stale upload is
  // ignored and the selected catalog/color cutout remains the safe fallback.
  const assetRef = configuredCurrent?provia.configured_image:selected.placement_image_ref;
  if (!_vzPlacementRefIsSafe(assetRef)) return null;
  const proviaName = [provia.series,provia.model].filter(Boolean).join(' ');
  const productName = selected.product_name || selected.option_name || selected.bundle_name || proviaName || _VZ_ROLE_META[role].label;
  const fingerprint = [selected.exterior_product_id||selected.option_id||'',selected.color_name||'',assetRef,provia.access_code||''].join('|').slice(0,160);
  const result={asset_ref:assetRef,source_crop:{x:0,y:0,w:1,h:1},mirror_x:false};
  const fields={product_id:selected.exterior_product_id||selected.option_id||'',product_name:productName,
    color_name:selected.color_name||'',style_name:selected.style_name||'',selection_fingerprint:fingerprint};
  for(const [field,value] of Object.entries(fields))if(value)result[field]=String(value).slice(0,field==='product_name'?200:160);
  return result;
}
function _vzDefaultPlacementQuad(role, offset = 0) {
  const presets = {
    door:[[.40,.27],[.58,.27],[.58,.86],[.40,.86]],
    window:[[.31,.35],[.53,.35],[.53,.60],[.31,.60]],
    shutter:[[.24,.33],[.32,.33],[.32,.64],[.24,.64]]
  };
  const base = presets[role] || presets.window;
  const dx = Math.min(.22, Math.max(0,offset) * .035);
  return base.map(point => [_vzRoundCoord(Math.min(.98,point[0]+dx)),point[1]]);
}
function _vzPlacementChanged(render = true) {
  const vz = _vzGet(), elevation = _vzElevation();
  elevation.tier_renders = {};
  if (elevation.id === 'front') vz.tier_renders = {};
  vzState.placementsChanged = true;
  vzState.dirty = true;
  _vzPlacementLayerCache.clear();
  setDirty();
  if (render) _vzRedrawAll();
}
function _vzSyncPlacementAssignmentsForTrade(trade, tier = vzState.activeTier) {
  const role=_VZ_PLACEMENT_ROLES.find(item=>_VZ_ROLE_META[item].trade===trade);
  if(!role)return;
  const source=_vzPlacementSource(role,tier),vz=_vzGet();
  for(const elevation of Object.values(vz.elevations)){
    const doc=_vzNormalizePlacements(elevation.placements);elevation.placements=doc;
    let changed=false;
    for(const slot of Object.values(doc.slots)){
      if(slot.role!==role||!doc.concepts[tier][slot.id])continue;
      if(source)doc.concepts[tier][slot.id]=JSON.parse(JSON.stringify(source));
      else delete doc.concepts[tier][slot.id];
      changed=true;
    }
    if(changed)elevation.tier_renders={};
  }
}
function _vzAddPlacement(role) {
  if (!_VZ_PLACEMENT_ROLES.includes(role) || _vzVisualizerEditLocked()) return;
  const source = _vzPlacementSource(role);
  if (!source) {
    alert(role === 'door'
      ? 'Choose a door with a product cutout or upload the configured ProVia image first.'
      : 'This product does not have a cutout yet. Ask a manager to add a transparent product image in Exterior Catalog.');
    return;
  }
  const doc = _vzPlacementDoc();
  if (Object.keys(doc.slots).length >= 64) { alert('This elevation already has the maximum number of placed openings.'); return; }
  let id = 'pl_' + role + '_' + Date.now().toString(36).toLowerCase();
  let suffix = 2; while (doc.slots[id]) id = id.replace(/_\d+$/,'') + '_' + suffix++;
  const count = Object.values(doc.slots).filter(slot => slot.role === role).length;
  doc.slots[id] = {id,role,label:_VZ_ROLE_META[role].label.replace(/s$/,'') + ' ' + (count+1),
    quad:_vzDefaultPlacementQuad(role,count),z:Object.keys(doc.slots).length};
  doc.concepts[vzState.activeTier][id] = source;
  vzState.placementSlotId = id;
  vzState.placementOpen = true;
  vzState.refine = false; vzState.alignmentOpen = false;
  _vzPlacementChanged(false);
  renderVisualizerPage();
}
function _vzSetPlacementOpen(open) {
  if (!vzState || vzState.owner !== S) return;
  if (_vzVisualizerEditLocked() && !!open !== vzState.placementOpen) return;
  vzState.placementOpen = !!open;
  vzState.placementDragIndex = -1; vzState.placementMoveWhole = false;
  if (open) {
    vzState.refine = false; vzState.alignmentOpen = false; vzState.original = false;
    const refine=document.querySelector('.vz-refine'); if(refine) refine.open=false;
    const align=document.querySelector('.vz-align'); if(align) align.open=false;
    if (!_vzActivePlacementSlot()) vzState.placementSlotId = _vzPlacementSlots()[0]?.id || '';
  }
  _vzRedrawAll();
}
function _vzSelectPlacement(id) {
  if (_vzVisualizerEditLocked()) return;
  if (!_vzPlacementDoc().slots[id]) return;
  vzState.placementSlotId=id; vzState.placementOpen=true; renderVisualizerPage();
}
function _vzUpdatePlacementLabel(value) {
  if (_vzVisualizerEditLocked()) return;
  const slot=_vzActivePlacementSlot(); if(!slot) return;
  slot.label=String(value||slot.role).trim().slice(0,80) || _VZ_ROLE_META[slot.role].label;
  _vzPlacementChanged(false);
}
function _vzAssignCurrentProduct(allConcepts = false) {
  if (_vzVisualizerEditLocked()) return;
  const slot=_vzActivePlacementSlot(); if(!slot) return;
  const tiers=allConcepts?TIERS:[vzState.activeTier];
  const currentSource=_vzPlacementSource(slot.role,vzState.activeTier);
  const doc=_vzPlacementDoc();
  let applied=0;
  for(const tier of tiers){
    const source=allConcepts?currentSource:_vzPlacementSource(slot.role,tier);
    if(source){doc.concepts[tier][slot.id]=JSON.parse(JSON.stringify(source));applied++;}
  }
  if(!applied){alert('The selected product does not have a product cutout for this concept yet.');return;}
  _vzPlacementChanged(false); renderVisualizerPage();
}
function _vzClearPlacementAssignment() {
  if (_vzVisualizerEditLocked()) return;
  const slot=_vzActivePlacementSlot(); if(!slot)return;
  delete _vzPlacementDoc().concepts[vzState.activeTier][slot.id];
  _vzPlacementChanged(false); renderVisualizerPage();
}
function _vzMirrorPlacement() {
  if (_vzVisualizerEditLocked()) return;
  const slot=_vzActivePlacementSlot(); if(!slot)return;
  const assignment=_vzPlacementDoc().concepts[vzState.activeTier][slot.id];
  if(!assignment)return;
  assignment.mirror_x=!assignment.mirror_x; _vzPlacementChanged(false); renderVisualizerPage();
}
function _vzSetPlacementCrop(edge,value) {
  if (_vzVisualizerEditLocked() || !['left','right','top','bottom'].includes(edge)) return;
  const slot=_vzActivePlacementSlot();if(!slot)return;
  const assignment=_vzPlacementDoc().concepts[vzState.activeTier][slot.id];if(!assignment)return;
  const crop=assignment.source_crop||{x:0,y:0,w:1,h:1};
  const margins={
    left:Math.round((Number(crop.x)||0)*100),
    right:Math.round(Math.max(0,1-(Number(crop.x)||0)-(Number(crop.w)||1))*100),
    top:Math.round((Number(crop.y)||0)*100),
    bottom:Math.round(Math.max(0,1-(Number(crop.y)||0)-(Number(crop.h)||1))*100)
  };
  const opposite={left:'right',right:'left',top:'bottom',bottom:'top'}[edge];
  margins[edge]=Math.max(0,Math.min(45,Math.round(Number(value)||0),90-margins[opposite]));
  assignment.source_crop={
    x:_vzRoundCoord(margins.left/100),y:_vzRoundCoord(margins.top/100),
    w:_vzRoundCoord((100-margins.left-margins.right)/100),
    h:_vzRoundCoord((100-margins.top-margins.bottom)/100)
  };
  _vzPlacementChanged(false);renderVisualizerPage();
}
function _vzDuplicatePlacement() {
  if (_vzVisualizerEditLocked()) return;
  const slot=_vzActivePlacementSlot(); if(!slot)return;
  const doc=_vzPlacementDoc(),base='pl_'+slot.role+'_'+Date.now().toString(36).toLowerCase();
  let id=base,suffix=2;while(doc.slots[id])id=base+'_'+suffix++;
  const shifted=slot.quad.map(([x,y])=>[_vzRoundCoord(Math.min(.99,x+.035)),y]);
  if(!_vzQuadIsValid(shifted))return;
  doc.slots[id]={...JSON.parse(JSON.stringify(slot)),id,label:slot.label+' copy',quad:shifted,z:Object.keys(doc.slots).length};
  for(const tier of TIERS)if(doc.concepts[tier][slot.id])doc.concepts[tier][id]=JSON.parse(JSON.stringify(doc.concepts[tier][slot.id]));
  vzState.placementSlotId=id; _vzPlacementChanged(false); renderVisualizerPage();
}
function _vzRemovePlacement() {
  if (_vzVisualizerEditLocked()) return;
  const slot=_vzActivePlacementSlot(); if(!slot)return;
  const doc=_vzPlacementDoc(); delete doc.slots[slot.id];
  for(const tier of TIERS)delete doc.concepts[tier][slot.id];
  vzState.placementSlotId=_vzPlacementSlots()[0]?.id||'';
  _vzPlacementChanged(false); renderVisualizerPage();
}
function _vzPlacementEditorHtml() {
  for(const tier of TIERS)_vzEnsureTier(tier);
  const roles=_VZ_PLACEMENT_ROLES.filter(role=>_vzScopeRoles().includes(role));
  if(!roles.length)return '';
  const roleSet=new Set(roles),slots=_vzPlacementSlots().filter(slot=>roleSet.has(slot.role));
  if(!slots.some(slot=>slot.id===vzState.placementSlotId))vzState.placementSlotId=slots[0]?.id||'';
  const slot=_vzActivePlacementSlot();
  const assignment=slot?(_vzPlacementDoc().concepts[vzState.activeTier][slot.id]||null):null;
  const locked=_vzVisualizerEditLocked(),disabled=locked?' disabled':'';
  const crop=assignment?.source_crop||{x:0,y:0,w:1,h:1};
  const cropMargins=assignment?{
    left:Math.round(crop.x*100),right:Math.round(Math.max(0,1-crop.x-crop.w)*100),
    top:Math.round(crop.y*100),bottom:Math.round(Math.max(0,1-crop.y-crop.h)*100)}:null;
  const cropSlider=(edge,label)=>`<label>${esc(label)} <span>${cropMargins[edge]}%</span><input type="range" min="0" max="45" step="1" value="${cropMargins[edge]}" onchange="_vzSetPlacementCrop('${edge}',this.value)"${disabled}></label>`;
  return `<details class="vz-place" ${vzState.placementOpen?'open':''} ontoggle="_vzSetPlacementOpen(this.open)">
    <summary>Place exact doors, windows & shutters <span>Four-corner photo fit</span></summary>
    <div class="vz-place-body">
      <p class="vz-place-help">Choose a product in the ${esc(_vzConceptName(vzState.activeTier))} concept, add its opening once, then drag the four corners to the frame. The opening stays aligned when you switch concepts.</p>
      <div class="vz-place-add">${roles.map(role=>`<button class="btn small" onclick="_vzAddPlacement('${role}')" ${_vzPlacementSource(role)&&!locked?'':'disabled'}>+ ${esc(_VZ_ROLE_META[role].label.replace(/s$/,''))}</button>`).join('')}</div>
      ${slots.length?`<label class="vz-field">Opening<select onchange="_vzSelectPlacement(this.value)"${disabled}>${slots.map(item=>`<option value="${item.id}" ${item.id===vzState.placementSlotId?'selected':''}>${esc(item.label)} · ${esc(_VZ_ROLE_META[item.role].label)}</option>`).join('')}</select></label>`:'<p class="vz-place-empty">No exact products placed yet. Products need a catalog cutout; ProVia doors can use the configured image you upload.</p>'}
      ${slot?`<div class="vz-place-editor"><label class="vz-field">Opening label<input maxlength="80" value="${esc(slot.label)}" onchange="_vzUpdatePlacementLabel(this.value)"${disabled}></label>
        <div class="vz-place-status"><strong>${esc(_vzConceptName(vzState.activeTier))}:</strong> ${assignment?esc([assignment.product_name,assignment.color_name].filter(Boolean).join(' · ')||'Product image assigned'):'No product assigned to this opening'}</div>
        ${assignment?`<div class="vz-place-crop"><strong>Crop image margins</strong><span>Trim screenshot borders; transparent PNGs work best.</span><div>${cropSlider('left','Left')}${cropSlider('right','Right')}${cropSlider('top','Top')}${cropSlider('bottom','Bottom')}</div></div>`:''}
        <div class="vz-place-actions"><button class="btn small" onclick="_vzAssignCurrentProduct(false)"${disabled}>Use selected product</button><button class="btn small" onclick="_vzAssignCurrentProduct(true)"${disabled}>Fill all concepts</button>${assignment?`<button class="btn small" onclick="_vzMirrorPlacement()"${disabled}>↔ Mirror</button><button class="btn small" onclick="_vzClearPlacementAssignment()"${disabled}>Clear this concept</button>`:''}<button class="btn small" onclick="_vzDuplicatePlacement()"${disabled}>Duplicate opening</button><button class="btn small danger" onclick="_vzRemovePlacement()"${disabled}>Remove opening</button></div>
        <div class="vz-place-key"><span>1 Top left</span><span>2 Top right</span><span>3 Bottom right</span><span>4 Bottom left</span><span>Drag inside to move</span></div></div>`:''}
    </div></details>`;
}
function _vzComponentProducts(trade, tier) {
  if (!['trim', 'soffit'].includes(trade)) return [];
  const siding = _vzGet().selections.siding[tier] || {};
  return _vzExteriorGroups(trade).filter(product =>
    product.bundle_id && product.bundle_id === siding.bundle_id);
}
function _vzEnsureTier(tier) {
  const vz = _vzGet();
  for (const trade of ['roofing', 'siding']) {
    if (vz.selections[trade][tier]) continue;
    const products = _vzExteriorGroups(trade);
    if (products.length) {
      const bundle = _vzBundleFor(trade, tier);
      const product = products.find(p => p.bundle_id && p.bundle_id === bundle?.id) || products[0];
      vz.selections[trade][tier] = _vzExteriorSelection(product);
      continue;
    }
    const bundle = _vzBundleFor(trade, tier);
    const material = _vzMaterialForBundle(trade, bundle);
    const color = _vzPalette(material)[0];
    if (!bundle || !color) continue;
    const style = (material.styles || [])[0] || {};
    vz.selections[trade][tier] = {bundle_id: bundle.id, bundle_name: bundle.name,
      color_hex: color.hex, color_name: color.name,
      style_id: style.id || '', style_name: style.name || '', pattern_id: style.pattern_id || ''};
  }
  for (const trade of ['trim', 'soffit']) {
    const choices = _vzComponentProducts(trade, tier);
    if (!choices.length || vz.selections[trade][tier]) continue;
    vz.selections[trade][tier] = _vzExteriorSelection(choices[0]);
  }
  for (const role of ['gutter','window','metal','shutter','stucco']) {
    if (!_vzGet().scope.includes(role)) continue;
    const trade = _VZ_ROLE_META[role].trade;
    if (vz.selections[trade][tier]) continue;
    const choices = _vzExteriorGroups(trade);
    if (choices.length) vz.selections[trade][tier] = _vzExteriorSelection(choices[0]);
  }
}
function _vzSelectedProduct(trade) {
  if (trade === 'doors') {
    const selected = _vzGet().selections.doors[vzState.activeTier] || {};
    const exterior = _vzExteriorProductForSelection('doors', selected);
    if (exterior) return exterior;
    return ((priceBook || {}).exterior_doors || []).find(d => d.id === selected.option_id);
  }
  const selected = _vzGet().selections[trade][vzState.activeTier] || {};
  const exterior = _vzExteriorProductForSelection(trade, selected);
  if (exterior) return exterior;
  if (['trim', 'soffit'].includes(trade)) return null;
  return _vzMaterialForBundle(trade, _vzBundleFor(trade, vzState.activeTier));
}
function _vzRenderPicker() {
  const host = document.getElementById('vz-picker-body');
  if (!host) return;
  for (const tier of TIERS) _vzEnsureTier(tier);
  const tier = vzState.activeTier, vz = _vzGet(), scope = new Set(_vzScopeRoles());
  const option = (value, label, selected) => '<option value="' + esc(value) + '"' +
    (selected ? ' selected' : '') + '>' + esc(label) + '</option>';
  const colors = trade => {
    const palette = _vzPalette(_vzSelectedProduct(trade));
    const selected = vz.selections[trade][tier] || {};
    const found = palette.some(c => c.name === selected.color_name && c.hex === selected.color_hex);
    const options = (!found ? option('', selected.color_name || 'Choose a color', true) : '') +
      palette.map((c, i) => option(String(i), c.name, c.name === selected.color_name && c.hex === selected.color_hex)).join('');
    return '<label class="vz-field">Color <select onchange="_vzChooseColor(\'' + trade + '\', this.value)">' +
      options + '</select></label>' +
      '<div class="vz-selected-color"><span style="background:' +
      (/^#[0-9a-f]{6}$/i.test(selected.color_hex || '') ? selected.color_hex : '#fff') +
      '"></span>' + esc(selected.color_name || 'No color chosen') + '</div>';
  };
  const tradePanel = trade => {
    const bundle = _vzBundleFor(trade, tier);
    const exteriorChoices = _vzExteriorGroups(trade);
    const legacyChoices = ((priceBook || {})[trade + '_bundles'] || [])
      .filter(b => _vzPalette(_vzMaterialForBundle(trade, b)).length)
      .map(b => ({id:b.id, name:b.name, bundle_id:b.id}));
    const choices = Array.isArray((priceBook || {}).exterior_catalog) ? exteriorChoices : legacyChoices;
    const selected = vz.selections[trade][tier] || {};
    const styles = (_vzSelectedProduct(trade) || {}).styles || [];
    const resolvedProduct = _vzExteriorProductForSelection(trade, selected);
    const selectedId = resolvedProduct?.id || selected.exterior_product_id ||
      (choices.find(p => p.bundle_id && p.bundle_id === selected.bundle_id) || {}).id || bundle?.id || '';
    const stylePicker = trade === 'siding' && styles.length
      ? '<label class="vz-field">Siding style<select onchange="_vzChooseStyle(this.value)">' +
        (!styles.some(s => s.id === selected.style_id) ? option('', selected.style_name || 'Choose style', true) : '') +
        styles.map(s => option(s.id, s.name, s.id === selected.style_id)).join('') + '</select></label>' : '';
    return '<section class="vz-picker-section"><h3 class="vz-picker-title">' +
      (trade === 'roofing' ? 'Roof' : 'Siding') + '</h3>' +
      '<label class="vz-field">Product<select onchange="_vzChooseProduct(\'' + trade + '\', this.value)">' +
      (!choices.some(p => p.id === selectedId) ? option('', 'Choose a product', true) : '') +
      choices.map(p => option(p.id, p.name, p.id === selectedId)).join('') +
      '</select></label>' + stylePicker + colors(trade) + '</section>';
  };
  const componentPanel = (trade, title) => {
    const choices = _vzComponentProducts(trade, tier);
    const selected = vz.selections[trade][tier] || {};
    if (!choices.length) {
      return '<div class="vz-component"><h4>' + esc(title) + '</h4>' +
        '<p class="vz-picker-help">No linked manufacturer finish catalog is available for this siding product.</p></div>';
    }
    const selectedId = selected.exterior_product_id || '';
    return '<div class="vz-component"><h4>' + esc(title) + '</h4>' +
      '<label class="vz-field">Product / profile<select onchange="_vzChooseProduct(\'' + trade + '\', this.value)">' +
      (!choices.some(p => p.id === selectedId) ? option('', 'Choose a product', true) : '') +
      choices.map(p => option(p.id, p.name, p.id === selectedId)).join('') +
      '</select></label>' + colors(trade) + '</div>';
  };
  const genericPanel = role => {
    const meta = _VZ_ROLE_META[role], trade = meta.trade;
    const choices = _vzExteriorGroups(trade);
    const selected = vz.selections[trade][tier] || {};
    if (!choices.length) return '<section class="vz-picker-section"><h3 class="vz-picker-title">' +
      meta.icon + ' ' + esc(meta.label) + '</h3><p class="vz-picker-help">No installed ' +
      esc(meta.label.toLowerCase()) + ' products are in the Exterior Catalog yet. A manager can add them without changing pricing.</p></section>';
    const selectedId = selected.exterior_product_id || '';
    return '<section class="vz-picker-section"><h3 class="vz-picker-title">' + meta.icon + ' ' + esc(meta.label) + '</h3>' +
      '<label class="vz-field">Installed product<select onchange="_vzChooseProduct(\'' + trade + '\',this.value)">' +
      (!choices.some(p => p.id === selectedId) ? option('', selected.product_name || 'Choose a product', true) : '') +
      choices.map(p => option(p.id,p.name,p.id === selectedId)).join('') + '</select></label>' + colors(trade) + '</section>';
  };
  const exteriorDoors = _vzExteriorGroups('doors');
  const doorOptions = Array.isArray((priceBook || {}).exterior_catalog)
    ? exteriorDoors : ((priceBook || {}).exterior_doors || []);
  const ds = vz.selections.doors[tier] || {};
  const selectedDoorId = ds.exterior_product_id || ds.option_id;
  const provia = vz.provia_specs[tier] || {};
  const proviaImageCurrent = !!provia.configured_image &&
    (!provia.configured_for || provia.configured_for === _vzDoorConfigurationFingerprint(ds,provia));
  const pf = (field,label,placeholder) => '<label class="vz-field">' + label + '<input value="' +
    esc(provia[field] || '') + '" placeholder="' + esc(placeholder || '') + '" onchange="_vzSetProVia(\'' + field + '\',this.value)"></label>';
  const doorPanel = '<section class="vz-picker-section vz-door-section"><h3 class="vz-picker-title">Entry door</h3>' +
    '<label class="vz-field">Door series<select onchange="_vzPickDoorOption(this.value)">' +
    option('', 'Keep existing door', !selectedDoorId) +
    (selectedDoorId && !doorOptions.some(d => d.id === selectedDoorId) ? option(selectedDoorId, ds.option_name + ' (saved selection)', true) : '') +
    doorOptions.map(d => option(d.id, d.name, d.id === selectedDoorId)).join('') + '</select></label>' +
    (selectedDoorId ? colors('doors') : '') +
    '<div class="vz-provia-spec"><h4>Exact ProVia specification handoff</h4><div class="vz-provia-grid">' +
      pf('access_code','Envision access code','Paste the ProVia design code') + pf('series','Series','Signet, Embarq, Heritage…') +
      pf('model','Model / style','Door model or style number') + pf('glass','Glass','Glass family / privacy') +
      pf('hardware','Hardware','Finish and handleset') + pf('swing','Swing / handing','Inswing, handing') +
    '</div><label class="vz-field">Notes<textarea rows="2" onchange="_vzSetProVia(\'notes\',this.value)">' + esc(provia.notes || '') + '</textarea></label>' +
    (provia.configured_image ? '<img class="vz-provia-preview' + (proviaImageCurrent?'':' stale') + '" src="' + BASE + '/uploads/' + esc(provia.configured_image) + '" alt="Saved ProVia configuration">' +
      (proviaImageCurrent?'':'<p class="vz-provia-stale">This image belongs to the previous door configuration. Re-upload the current ProVia image before placing it.</p>') : '') +
    '<div class="vz-provia-actions"><a class="vz-provia-link" href="https://www.provia.com/design-center/envision/" target="_blank" rel="noopener noreferrer">Open ProVia Envision ↗</a>' +
    '<label class="btn small">Upload configured door image<input type="file" accept="image/png,image/jpeg,image/webp" hidden onchange="_vzUploadProViaImage(this)"></label></div></div></section>';
  const primary = (scope.has('roof') ? tradePanel('roofing') : '') + (scope.has('siding') ? tradePanel('siding') : '');
  const details = (scope.has('trim') || scope.has('soffit'))
    ? '<section class="vz-picker-section vz-component-section"><h3 class="vz-picker-title">Siding details</h3>' +
      (scope.has('trim') ? componentPanel('trim','Trim & fascia') : '') +
      (scope.has('soffit') ? componentPanel('soffit','Soffit') : '') + '</section>' : '';
  const additions = ['gutter','window','metal','shutter','stucco'].filter(role => scope.has(role)).map(genericPanel).join('');
  host.innerHTML = '<p class="vz-picker-help">Design choices only. Update Products / Pricing separately to quote this look. Uploaded textures and screen colors are approximate; verify manufacturer availability and physical samples.</p>' +
    primary + details + additions + (scope.has('door') ? doorPanel : '');
}
function _vzChanged() {
  _vzInvalidateRenders();
  _vzProjectionCache.clear();
  _vzPlacementLayerCache.clear();
  vzState.selectionsChanged = true;
  vzState.dirty = true;
  setDirty(); _vzRenderPicker(); _vzRedrawAll();
}
function _vzChooseBundle(trade, id) {
  if (!['roofing', 'siding'].includes(trade) || _vzVisualizerEditLocked()) return;
  const bundle = ((priceBook || {})[trade + '_bundles'] || []).find(b => b.id === id);
  const product = _vzMaterialForBundle(trade, bundle);
  const color = _vzPalette(product)[0];
  if (!bundle || !color) return;
  const style = (product.styles || [])[0] || {};
  _vzGet().selections[trade][vzState.activeTier] = {bundle_id: bundle.id, bundle_name: bundle.name,
    color_hex: color.hex, color_name: color.name, style_id: style.id || '',
    style_name: style.name || '', pattern_id: style.pattern_id || ''};
  if (trade === 'siding') {
    delete _vzGet().selections.trim[vzState.activeTier];
    delete _vzGet().selections.soffit[vzState.activeTier];
    _vzEnsureTier(vzState.activeTier);
  }
  _vzChanged();
}
function _vzChooseProduct(trade, id) {
  const allowed = [...new Set(_VZ_ROLES.map(role => _VZ_ROLE_META[role].trade))].filter(t => t !== 'doors');
  if (!allowed.includes(trade) || _vzVisualizerEditLocked()) return;
  const choices = ['trim', 'soffit'].includes(trade)
    ? _vzComponentProducts(trade, vzState.activeTier) : _vzExteriorGroups(trade);
  const product = choices.find(p => p.id === id);
  if (!product) { _vzChooseBundle(trade, id); return; }
  _vzGet().selections[trade][vzState.activeTier] = _vzExteriorSelection(product);
  _vzSyncPlacementAssignmentsForTrade(trade);
  if (trade === 'siding') {
    delete _vzGet().selections.trim[vzState.activeTier];
    delete _vzGet().selections.soffit[vzState.activeTier];
    _vzEnsureTier(vzState.activeTier);
  }
  _vzChanged();
}
function _vzChooseColor(trade, index) {
  const allowed = [...new Set(_VZ_ROLES.map(role => _VZ_ROLE_META[role].trade))];
  if (!allowed.includes(trade) || _vzVisualizerEditLocked() || !/^\d+$/.test(index)) return;
  const color = _vzPalette(_vzSelectedProduct(trade))[Number(index)];
  if (!color) return;
  if(trade==='doors')_vzMarkProViaImageStale();
  Object.assign(_vzGet().selections[trade][vzState.activeTier], {color_hex: color.hex, color_name: color.name,
    texture_ref:color.texture_ref || '', texture_scale:color.texture_scale || 96,
    placement_image_ref:color.placement_image_ref || ''});
  _vzSyncPlacementAssignmentsForTrade(trade);
  _vzChanged();
}
function _vzChooseStyle(id) {
  if (_vzVisualizerEditLocked()) return;
  const style = ((_vzSelectedProduct('siding') || {}).styles || []).find(s => s.id === id);
  if (!style) return;
  Object.assign(_vzGet().selections.siding[vzState.activeTier], {
    style_id: style.id, style_name: style.name, pattern_id: style.pattern_id || ''});
  _vzChanged();
}
function _vzPickDoorOption(id) {
  if (_vzVisualizerEditLocked()) return;
  const vz = _vzGet(), tier = vzState.activeTier;
  _vzMarkProViaImageStale(tier);
  if (!id) { delete vz.selections.doors[tier]; _vzSyncPlacementAssignmentsForTrade('doors',tier); _vzChanged(); return; }
  const exterior = _vzExteriorGroups('doors').find(d => d.id === id);
  if (exterior) {
    const selected = _vzExteriorSelection(exterior);
    vz.selections.doors[tier] = Object.assign(selected, {
      option_id: exterior.id, option_name: exterior.name, preview_only: true
    });
    _vzSyncPlacementAssignmentsForTrade('doors',tier);
    _vzChanged();
    return;
  }
  const door = ((priceBook || {}).exterior_doors || []).find(d => d.id === id);
  if (!door) return;
  const color = _vzPalette(door)[0] || {};
  vz.selections.doors[tier] = {option_id: door.id, option_name: door.name,
    preview_only: !!door.preview_only, pattern_id: door.pattern_id || '',
    color_hex: color.hex || '', color_name: color.name || ''};
  _vzSyncPlacementAssignmentsForTrade('doors',tier);
  _vzChanged();
}

function _vzSetProVia(field, value) {
  if (!['access_code','series','model','glass','hardware','swing','notes'].includes(field) || _vzVisualizerEditLocked()) return;
  const vz = _vzGet(), tier = vzState.activeTier;
  vz.provia_specs[tier] = vz.provia_specs[tier] || {};
  if(field!=='notes'&&vz.provia_specs[tier].configured_image)_vzMarkProViaImageStale(tier);
  vz.provia_specs[tier][field] = String(value || '').trim().slice(0, field === 'notes' ? 1000 : 120);
  if(field!=='notes'){
    _vzSyncPlacementAssignmentsForTrade('doors',tier);
    _vzPlacementChanged(false);renderVisualizerPage();
  }else{vzState.dirty = true;setDirty();}
}
async function _vzUploadProViaImage(input) {
  if (_vzVisualizerEditLocked()) { if(input)input.value=''; return; }
  const file = input?.files?.[0];
  if (!file) return;
  const state=vzState,tier=state.activeTier;
  state.proviaUploading=true;_vzDetectionUI();
  try {
    if (file.size > 12*1024*1024 || !/^image\/(png|jpeg|webp)$/.test(file.type)) {
      throw new Error('Choose a PNG, JPG, or WebP image smaller than 12 MB.');
    }
    if (!S.estimate_id) await saveEstimate();
    if (!S.estimate_id) throw new Error('Save the estimate before attaching the ProVia configuration.');
    const data = await new Promise((resolve,reject) => {
      const reader = new FileReader(); reader.onload=()=>resolve(reader.result); reader.onerror=()=>reject(new Error('Could not read that image.')); reader.readAsDataURL(file);
    });
    const ext = file.type === 'image/jpeg' ? 'jpg' : file.type.split('/')[1];
    const result = await _vzPostAsset(S.estimate_id, {kind:'provia',tier,ext,
      content_b64:String(data).split(',')[1]});
    if(state!==vzState||state.owner!==S)throw new Error('The estimate changed before the ProVia image finished uploading.');
    const vz = _vzGet();
    vz.provia_specs[tier] = vz.provia_specs[tier] || {};
    vz.provia_specs[tier].configured_image = result.filename;
    const selected=(vz.selections.doors||{})[tier]||{};
    vz.provia_specs[tier].configured_for=_vzDoorConfigurationFingerprint(selected,vz.provia_specs[tier]);
    _vzSyncPlacementAssignmentsForTrade('doors',tier);
    _vzPlacementChanged(false); renderVisualizerPage();
  } catch (error) { alert(error.message); }
  finally {
    state.proviaUploading=false;
    if(state===vzState&&state.owner===S)_vzDetectionUI();
    input.value='';
  }
}

// Perspective helpers are kept independent of DOM layout so the exact same
// normalized plane geometry drives the editor, thumbnails, and saved JPG.
// Canvas 2D has affine transforms but no projective draw call, so a source
// sheet is mapped through a homography as a deterministic triangle mesh.
function _vzHomographyForQuad(quad, width = 1, height = 1) {
  if (!_vzQuadIsValid(quad)) return null;
  const p = quad.map(point => ({x:point[0]*width,y:point[1]*height}));
  const [p0,p1,p2,p3] = p;
  const dx1=p1.x-p2.x, dx2=p3.x-p2.x, dx3=p0.x-p1.x+p2.x-p3.x;
  const dy1=p1.y-p2.y, dy2=p3.y-p2.y, dy3=p0.y-p1.y+p2.y-p3.y;
  const denominator=dx1*dy2-dx2*dy1;
  if (Math.abs(denominator) < 1e-9) return null;
  const g=(dx3*dy2-dx2*dy3)/denominator;
  const h=(dx1*dy3-dx3*dy1)/denominator;
  const result={
    a:p1.x-p0.x+g*p1.x,b:p3.x-p0.x+h*p3.x,c:p0.x,
    d:p1.y-p0.y+g*p1.y,e:p3.y-p0.y+h*p3.y,f:p0.y,g,h
  };
  return Object.values(result).every(Number.isFinite) ? result : null;
}
function _vzProjectPoint(h,u,v) {
  const denominator=h.g*u+h.h*v+1;
  if (!Number.isFinite(denominator) || Math.abs(denominator)<1e-9) return null;
  return {x:(h.a*u+h.b*v+h.c)/denominator,y:(h.d*u+h.e*v+h.f)/denominator};
}
function _vzAffineTriangle(source,destination) {
  const [s0,s1,s2]=source,[d0,d1,d2]=destination;
  const denominator=s0.x*(s1.y-s2.y)+s1.x*(s2.y-s0.y)+s2.x*(s0.y-s1.y);
  if (Math.abs(denominator)<1e-9) return null;
  const solve = values => ({
    x:(values[0]*(s1.y-s2.y)+values[1]*(s2.y-s0.y)+values[2]*(s0.y-s1.y))/denominator,
    y:(values[0]*(s2.x-s1.x)+values[1]*(s0.x-s2.x)+values[2]*(s1.x-s0.x))/denominator,
    z:(values[0]*(s1.x*s2.y-s2.x*s1.y)+values[1]*(s2.x*s0.y-s0.x*s2.y)+values[2]*(s0.x*s1.y-s1.x*s0.y))/denominator
  });
  const x=solve([d0.x,d1.x,d2.x]), y=solve([d0.y,d1.y,d2.y]);
  const matrix=[x.x,y.x,x.y,y.y,x.z,y.z];
  return matrix.every(Number.isFinite) ? matrix : null;
}
function _vzExpandTriangle(points,amount=0.7) {
  const center={x:(points[0].x+points[1].x+points[2].x)/3,y:(points[0].y+points[1].y+points[2].y)/3};
  return points.map(point => {
    const dx=point.x-center.x,dy=point.y-center.y,length=Math.hypot(dx,dy)||1;
    return {x:point.x+dx/length*amount,y:point.y+dy/length*amount};
  });
}
function _vzDrawWarpTriangle(ctx,sheet,source,destination) {
  const matrix=_vzAffineTriangle(source,destination);
  if (!matrix) return;
  const clip=_vzExpandTriangle(destination);
  ctx.save();
  ctx.beginPath();ctx.moveTo(clip[0].x,clip[0].y);ctx.lineTo(clip[1].x,clip[1].y);ctx.lineTo(clip[2].x,clip[2].y);ctx.closePath();ctx.clip();
  ctx.transform(...matrix);ctx.drawImage(sheet,0,0);ctx.restore();
}
function _vzRotatedTile(image,quarterTurns) {
  const turns=((quarterTurns||0)%4+4)%4;
  if (!turns) return image;
  const swap=turns%2===1;
  const tile=_vzMakeMaskCanvas(swap?image.naturalHeight:image.naturalWidth,swap?image.naturalWidth:image.naturalHeight);
  const ctx=tile.getContext('2d');
  ctx.translate(tile.width/2,tile.height/2);ctx.rotate(turns*Math.PI/2);
  ctx.drawImage(image,-image.naturalWidth/2,-image.naturalHeight/2);
  return tile;
}
function _vzRepeatedSheet(image,repeatX,repeatY,quarterTurns) {
  const sheet=_vzMakeMaskCanvas(512,512),ctx=sheet.getContext('2d');
  const tile=_vzRotatedTile(image,quarterTurns);
  if (!tile.width || !tile.height) return sheet;
  const pattern=ctx.createPattern(tile,'repeat');
  if (!pattern) return sheet;
  ctx.save();
  ctx.scale(sheet.width/(tile.width*repeatX),sheet.height/(tile.height*repeatY));
  ctx.fillStyle=pattern;ctx.fillRect(0,0,tile.width*repeatX,tile.height*repeatY);
  ctx.restore();
  return sheet;
}
function _vzWarpSheetToQuad(ctx,sheet,quad,width,height,grid) {
  const homography=_vzHomographyForQuad(quad,width,height);
  if (!homography) return false;
  const corners=quad.map(point => ({x:point[0]*width,y:point[1]*height}));
  ctx.save();ctx.beginPath();ctx.moveTo(corners[0].x,corners[0].y);
  for (let i=1;i<4;i++) ctx.lineTo(corners[i].x,corners[i].y);
  ctx.closePath();ctx.clip();
  for (let row=0;row<grid;row++) for (let col=0;col<grid;col++) {
    const u0=col/grid,u1=(col+1)/grid,v0=row/grid,v1=(row+1)/grid;
    const d00=_vzProjectPoint(homography,u0,v0),d10=_vzProjectPoint(homography,u1,v0);
    const d11=_vzProjectPoint(homography,u1,v1),d01=_vzProjectPoint(homography,u0,v1);
    if (!d00||!d10||!d11||!d01) continue;
    const s00={x:u0*sheet.width,y:v0*sheet.height},s10={x:u1*sheet.width,y:v0*sheet.height};
    const s11={x:u1*sheet.width,y:v1*sheet.height},s01={x:u0*sheet.width,y:v1*sheet.height};
    _vzDrawWarpTriangle(ctx,sheet,[s00,s10,s11],[d00,d10,d11]);
    _vzDrawWarpTriangle(ctx,sheet,[s00,s11,s01],[d00,d11,d01]);
  }
  ctx.restore();return true;
}
function _vzPlacementSheet(ref,image,assignment) {
  const crop=assignment.source_crop||{x:0,y:0,w:1,h:1};
  const key=JSON.stringify([ref,crop,assignment.mirror_x===true,image.naturalWidth,image.naturalHeight]);
  const cached=_vzCachedCanvas(_vzPlacementSheetCache,key);if(cached)return cached;
  const sourceW=Math.max(1,Math.round(image.naturalWidth*crop.w));
  const sourceH=Math.max(1,Math.round(image.naturalHeight*crop.h));
  const scale=Math.min(1,720/Math.max(sourceW,sourceH));
  const sheet=_vzMakeMaskCanvas(Math.max(1,Math.round(sourceW*scale)),Math.max(1,Math.round(sourceH*scale)));
  const ctx=sheet.getContext('2d');
  if(assignment.mirror_x){ctx.translate(sheet.width,0);ctx.scale(-1,1);}
  ctx.drawImage(image,image.naturalWidth*crop.x,image.naturalHeight*crop.y,sourceW,sourceH,0,0,sheet.width,sheet.height);
  return _vzCacheCanvas(_vzPlacementSheetCache,key,sheet,24*1024*1024);
}
function _vzCompositePlacements(ctx,W,H,tier) {
  const doc=_vzPlacementDoc(),assignments=doc.concepts[tier]||{};
  const scope=new Set(_vzScopeRoles());
  const slots=Object.values(doc.slots).filter(slot=>scope.has(slot.role))
    .sort((a,b)=>(a.z||0)-(b.z||0)||a.id.localeCompare(b.id));
  if(!slots.length)return;
  const dragging=vzState.placementDragIndex>=0||vzState.placementMoveWhole;
  const signature=slots.map(slot=>[slot.id,slot.quad,assignments[slot.id]||null]);
  const key=JSON.stringify([_vzElevation().id,tier,W,H,signature]);
  if(!dragging){
    const cached=_vzCachedCanvas(_vzPlacementLayerCache,key);
    if(cached){ctx.save();ctx.globalCompositeOperation='source-over';ctx.globalAlpha=1;ctx.drawImage(cached,0,0);ctx.restore();return;}
  }
  const layer=_vzMakeMaskCanvas(W,H),layerCtx=layer.getContext('2d');
  for(const slot of slots){
    const assignment=assignments[slot.id];if(!assignment)continue;
    const image=_vzGetPlacementImg(assignment.asset_ref);
    if(!image||!image.complete||!image.naturalWidth)continue;
    const sheet=_vzPlacementSheet(assignment.asset_ref,image,assignment);
    _vzWarpSheetToQuad(layerCtx,sheet,slot.quad,W,H,dragging?5:10);
  }
  if(!dragging)_vzCacheCanvas(_vzPlacementLayerCache,key,layer,32*1024*1024);
  ctx.save();ctx.globalCompositeOperation='source-over';ctx.globalAlpha=1;ctx.drawImage(layer,0,0);ctx.restore();
}
function _vzProjectedLayer(role,tier,image,tileSize,cacheId,width,height) {
  const projection=_vzRoleProjection(role);
  const planes=(projection&&projection.mode==='perspective'&&projection.planes)||[];
  if (!planes.length) return null;
  const stablePlanes=planes.filter(plane => _vzQuadIsValid(plane.quad));
  if (!stablePlanes.length) return null;
  const grid=vzState.alignmentDragIndex>=0?5:10;
  const signature=stablePlanes.map(plane => [plane.id,plane.quad.map(point => point.map(v => Math.round(v*10000)/10000)),plane.scale,plane.quarter_turns]);
  const key=JSON.stringify([_vzElevation().id,role,tier,cacheId,Number(tileSize)||0,width,height,grid,signature]);
  const cached=_vzCachedCanvas(_vzProjectionCache,key);if(cached)return cached;
  const layer=_vzMakeMaskCanvas(width,height),ctx=layer.getContext('2d');
  const canonicalWidth=vzState.canvas?.width||width,canonicalHeight=vzState.canvas?.height||height;
  let drawn=false;
  for (const plane of stablePlanes) {
    const q=plane.quad.map(point => ({x:point[0]*canonicalWidth,y:point[1]*canonicalHeight}));
    const averageWidth=(Math.hypot(q[1].x-q[0].x,q[1].y-q[0].y)+Math.hypot(q[2].x-q[3].x,q[2].y-q[3].y))/2;
    const averageHeight=(Math.hypot(q[3].x-q[0].x,q[3].y-q[0].y)+Math.hypot(q[2].x-q[1].x,q[2].y-q[1].y))/2;
    const base=Math.max(16,Math.min(512,Number(tileSize)||96))*Math.max(0.25,Math.min(4,Number(plane.scale)||1));
    const repeatX=Math.max(0.5,Math.min(64,averageWidth/base));
    const repeatY=Math.max(0.5,Math.min(64,averageHeight/base));
    const sheet=_vzRepeatedSheet(image,repeatX,repeatY,plane.quarter_turns);
    drawn=_vzWarpSheetToQuad(ctx,sheet,plane.quad,width,height,grid)||drawn;
  }
  if (!drawn) return null;
  return _vzCacheCanvas(_vzProjectionCache,key,layer,48*1024*1024);
}
function _vzFillCanonicalFlat(ctx,W,H,image,tileSize,mode) {
  const canonicalW=Math.max(1,vzState.canvas?.width||W),canonicalH=Math.max(1,vzState.canvas?.height||H);
  const targetScale=Math.max(0.01,Math.min(W/canonicalW,H/canonicalH));
  const baseSize=Math.max(16,Math.min(512,Number(tileSize)||96));
  const tileW=Math.max(1,Math.round((mode==='native'?image.naturalWidth:baseSize)*targetScale));
  const tileH=Math.max(1,Math.round((mode==='native'?image.naturalHeight:baseSize)*targetScale));
  const tile=_vzMakeMaskCanvas(tileW,tileH);
  tile.getContext('2d').drawImage(image,0,0,tileW,tileH);
  const pattern=ctx.createPattern(tile,'repeat');if(!pattern)return false;
  ctx.fillStyle=pattern;ctx.fillRect(0,0,W,H);return true;
}
function _vzCompositeProjected(ctx,W,H,mask,role,tier,image,tileSize,cacheId,alpha,flatMode,blendMode) {
  const projected=_vzProjectedLayer(role,tier,image,tileSize,cacheId,W,H);
  if (!projected) return false;
  const clipped=_vzMakeMaskCanvas(W,H),clippedCtx=clipped.getContext('2d');
  if(!_vzFillCanonicalFlat(clippedCtx,W,H,image,tileSize,flatMode))return false;
  clippedCtx.globalCompositeOperation='source-over';clippedCtx.drawImage(projected,0,0);
  clippedCtx.globalCompositeOperation='destination-in';clippedCtx.drawImage(mask,0,0,W,H);
  ctx.save();ctx.globalCompositeOperation=blendMode||'multiply';ctx.globalAlpha=alpha;
  ctx.drawImage(clipped,0,0);ctx.restore();
  return true;
}

// Render one tier's composite into any target canvas at any size. Shared
// between the main preview and the G/B/B triptych thumbnails.
function _vzComposeInto(target, tier, opts) {
  const showMaskOverlay = !!(opts && opts.showMaskOverlay);
  if (!vzState || !vzState.photoImg) return;
  const ctx = target.getContext('2d');
  const W = target.width, H = target.height;
  ctx.clearRect(0, 0, W, H);
  ctx.drawImage(vzState.photoImg, 0, 0, W, H);

  const vz = _vzGet();
  const scope = new Set(vz.scope || []);
  for (const role of _VZ_COMPOSE_ORDER) {
    if (!scope.has(role)) continue;
    const meta = _VZ_ROLE_META[role], mask = vzState[role + 'Mask'];
    const selected = _vzEffectiveExteriorSelection(meta.trade,
      (vz.selections[meta.trade] || {})[tier] || {});
    if (!mask || !selected.color_hex) continue;
    const texture = selected.texture_ref ? _vzGetTextureImg(selected.texture_ref) : null;
    const textureReady = !!(texture && texture.complete && texture.naturalWidth);
    // Manufacturer swatches already carry the product's color. Applying the
    // hex multiply first used to tint the same pixels twice and made dark roof
    // and siding options look nearly black. Keep the hex as a loading/fallback
    // preview; once the image is ready, composite the full-color swatch once.
    if (!textureReady) _vzCompositeColor(ctx, W, H, mask, selected.color_hex);
    if (textureReady) {
      if (!_vzCompositeProjected(ctx,W,H,mask,role,tier,texture,Number(selected.texture_scale)||96,
        'texture:'+selected.texture_ref,0.82,'square','source-over')) {
        _vzCompositeTexture(ctx, W, H, mask, texture, Number(selected.texture_scale) || 96);
      }
    }
    const pattern = selected.pattern_id ? _vzGetPatternImg(selected.pattern_id) : null;
    if (pattern && pattern.complete && pattern.naturalWidth) {
      if (!_vzCompositeProjected(ctx,W,H,mask,role,tier,pattern,pattern.naturalWidth||64,
        'pattern:'+selected.pattern_id,0.55,'native')) {
        _vzCompositePattern(ctx, W, H, mask, pattern);
      }
    }
  }
  _vzCompositePlacements(ctx,W,H,tier);

  // Editing overlay: on the main canvas, tint the currently-active mask so
  // the rep can see where they're painting. Skipped on the triptych.
  if (showMaskOverlay) {
    const which = _VZ_ROLES.includes(vzState.activeTool) ? vzState[vzState.activeTool + 'Mask'] : null;
    if (which) {
      const tint = _VZ_ROLE_META[vzState.activeTool].color;
      ctx.save();
      ctx.globalCompositeOperation = 'source-over';
      const tc = _vzTintMask(which, tint);
      ctx.drawImage(tc, 0, 0, W, H);
      ctx.restore();
    }
  }
}

function _vzCompositeColor(ctx, W, H, mask, hex) {
  // Off-screen: color-fill clipped to the mask.
  const oc = document.createElement('canvas'); oc.width = W; oc.height = H;
  const octx = oc.getContext('2d');
  octx.drawImage(mask, 0, 0, W, H);
  octx.globalCompositeOperation = 'source-in';
  octx.fillStyle = hex;
  octx.fillRect(0, 0, W, H);
  ctx.save();
  ctx.globalCompositeOperation = 'multiply';
  ctx.drawImage(oc, 0, 0);
  ctx.restore();
}

function _vzCompositePattern(ctx, W, H, mask, patImg) {
  const oc = document.createElement('canvas'); oc.width = W; oc.height = H;
  const octx = oc.getContext('2d');
  const pat = octx.createPattern(patImg, 'repeat');
  if (!pat) return;
  octx.fillStyle = pat;
  octx.fillRect(0, 0, W, H);
  // Clip to mask.
  octx.globalCompositeOperation = 'destination-in';
  octx.drawImage(mask, 0, 0, W, H);
  ctx.save();
  ctx.globalCompositeOperation = 'multiply';
  ctx.globalAlpha = 0.55;
  ctx.drawImage(oc, 0, 0);
  ctx.restore();
}

function _vzCompositeTexture(ctx, W, H, mask, texture, tileSize) {
  const oc = document.createElement('canvas'); oc.width = W; oc.height = H;
  const octx = oc.getContext('2d');
  const size = Math.max(16, Math.min(512, tileSize || 96));
  const tile = document.createElement('canvas'); tile.width = size; tile.height = size;
  tile.getContext('2d').drawImage(texture, 0, 0, size, size);
  const pattern = octx.createPattern(tile, 'repeat');
  if (!pattern) return;
  octx.fillStyle = pattern; octx.fillRect(0,0,W,H);
  octx.globalCompositeOperation = 'destination-in'; octx.drawImage(mask,0,0,W,H);
  ctx.save(); ctx.globalCompositeOperation = 'source-over'; ctx.globalAlpha = 0.82;
  ctx.drawImage(oc,0,0); ctx.restore();
}

function _vzTintMask(mask, color) {
  const W = mask.width, H = mask.height;
  const oc = document.createElement('canvas'); oc.width = W; oc.height = H;
  const octx = oc.getContext('2d');
  octx.drawImage(mask, 0, 0);
  octx.globalCompositeOperation = 'source-in';
  octx.fillStyle = color;
  octx.fillRect(0, 0, W, H);
  return oc;
}

function _vzDrawPlacementOverlay() {
  if(!vzState.placementOpen||!vzState.canvas||!vzState.ctx)return;
  const ctx=vzState.ctx,canvas=vzState.canvas,rect=canvas.getBoundingClientRect();
  const cssScale=canvas.width/Math.max(1,rect.width),radius=Math.max(9,13*cssScale);
  ctx.save();ctx.globalCompositeOperation='source-over';
  const scope=new Set(_vzScopeRoles());
  for(const slot of _vzPlacementSlots().filter(item=>scope.has(item.role))){
    const active=slot.id===vzState.placementSlotId;
    const points=slot.quad.map(point=>({x:point[0]*canvas.width,y:point[1]*canvas.height}));
    ctx.beginPath();ctx.moveTo(points[0].x,points[0].y);for(let i=1;i<4;i++)ctx.lineTo(points[i].x,points[i].y);ctx.closePath();
    ctx.fillStyle=active?'rgba(34,197,94,.10)':'rgba(14,165,233,.05)';ctx.fill();
    ctx.strokeStyle=active?'#22c55e':'rgba(125,211,252,.9)';ctx.lineWidth=Math.max(2,2.5*cssScale);ctx.setLineDash(active?[]:[6*cssScale,5*cssScale]);ctx.stroke();
    if(!active)continue;
    ctx.setLineDash([]);ctx.textAlign='center';ctx.textBaseline='middle';ctx.font=`bold ${Math.max(11,12*cssScale)}px sans-serif`;
    points.forEach((point,index)=>{ctx.beginPath();ctx.arc(point.x,point.y,radius,0,Math.PI*2);ctx.fillStyle='#fff';ctx.fill();ctx.lineWidth=Math.max(2,2*cssScale);ctx.strokeStyle='#22c55e';ctx.stroke();ctx.fillStyle='#14532d';ctx.fillText(String(index+1),point.x,point.y);});
  }
  ctx.restore();
}

function _vzDrawAlignmentOverlay() {
  if (!vzState.alignmentOpen || !vzState.canvas || !vzState.ctx) return;
  const projection=_vzRoleProjection(vzState.alignmentRole),planes=(projection&&projection.planes)||[];
  if (!planes.length) return;
  const ctx=vzState.ctx,canvas=vzState.canvas,rect=canvas.getBoundingClientRect();
  const cssScale=canvas.width/Math.max(1,rect.width),radius=Math.max(9,13*cssScale);
  ctx.save();ctx.globalCompositeOperation='source-over';
  for (const plane of planes) {
    const active=plane.id===vzState.alignmentPlaneId;
    const points=plane.quad.map(point => ({x:point[0]*canvas.width,y:point[1]*canvas.height}));
    ctx.beginPath();ctx.moveTo(points[0].x,points[0].y);for(let i=1;i<4;i++)ctx.lineTo(points[i].x,points[i].y);ctx.closePath();
    ctx.fillStyle=active?'rgba(245,158,11,.10)':'rgba(14,165,233,.06)';ctx.fill();
    ctx.strokeStyle=active?'#f59e0b':'rgba(125,211,252,.9)';ctx.lineWidth=Math.max(2,2.5*cssScale);ctx.setLineDash(active?[]:[6*cssScale,5*cssScale]);ctx.stroke();
    if (!active) continue;
    ctx.setLineDash([]);ctx.textAlign='center';ctx.textBaseline='middle';ctx.font=`bold ${Math.max(11,12*cssScale)}px sans-serif`;
    points.forEach((point,index) => {
      ctx.beginPath();ctx.arc(point.x,point.y,radius,0,Math.PI*2);ctx.fillStyle='#fff';ctx.fill();
      ctx.lineWidth=Math.max(2,2*cssScale);ctx.strokeStyle='#f59e0b';ctx.stroke();ctx.fillStyle='#7c2d12';ctx.fillText(String(index+1),point.x,point.y);
    });
  }
  ctx.restore();
}
function _vzQueueAlignmentRedraw() {
  if (vzState.alignmentFrame) return;
  const schedule=typeof requestAnimationFrame==='function'?requestAnimationFrame:(fn=>setTimeout(fn,16));
  vzState.alignmentFrame=schedule(() => { vzState.alignmentFrame=0;_vzRedrawAll(true); });
}

function _vzRedrawAll(mainOnly = false) {
  if (!vzState || !vzState.canvas) return;
  vzState.canvas.classList.toggle('vz-editing',!!(vzState.placementOpen||vzState.alignmentOpen||
    (vzState.refine&&!vzState.original&&!vzState.detecting)));
  if (vzState.original && vzState.photoImg) {
    vzState.ctx.drawImage(vzState.photoImg, 0, 0, vzState.canvas.width, vzState.canvas.height);
  } else {
    _vzComposeInto(vzState.canvas, vzState.activeTier, { showMaskOverlay: vzState.refine && !vzState.detecting });
    if (vzState.beforeSplit > 0 && vzState.photoImg) {
      const x = Math.round(vzState.canvas.width * vzState.beforeSplit / 100);
      vzState.ctx.save(); vzState.ctx.beginPath(); vzState.ctx.rect(0,0,x,vzState.canvas.height); vzState.ctx.clip();
      vzState.ctx.drawImage(vzState.photoImg,0,0,vzState.canvas.width,vzState.canvas.height); vzState.ctx.restore();
      vzState.ctx.save(); vzState.ctx.strokeStyle='#fff'; vzState.ctx.lineWidth=4; vzState.ctx.beginPath();
      vzState.ctx.moveTo(x,0); vzState.ctx.lineTo(x,vzState.canvas.height); vzState.ctx.stroke(); vzState.ctx.restore();
    }
  }
  _vzDrawAlignmentOverlay();
  _vzDrawPlacementOverlay();
  vzState.canvas.style.cursor = vzState.placementOpen
    ? ((vzState.placementDragIndex>=0||vzState.placementMoveWhole)?'grabbing':'grab')
    : vzState.alignmentOpen
    ? (vzState.alignmentDragIndex>=0?'grabbing':'grab')
    : vzState.refine && !vzState.original && !vzState.detecting ? 'crosshair' : 'default';
  // Triptych thumbnails.
  if (!mainOnly) for (const t of ['good', 'better', 'best']) {
    const tc = document.getElementById('vz-thumb-' + t);
    if (!tc || !vzState.photoImg) continue;
    const wrap = tc.parentElement;
    const cw = Math.max(220, wrap.clientWidth || 260);
    tc.width = cw;
    tc.height = Math.round(cw * (vzState.photoH / vzState.photoW || 0.6));
    _vzComposeInto(tc, t, { showMaskOverlay: false });
    const capEl = document.getElementById('vz-thumb-cap-' + t);
    if (capEl) {
      const vz = _vzGet();
      const parts = [];
      for (const role of _vzScopeRoles()) {
        const meta = _VZ_ROLE_META[role], selected = (vz.selections[meta.trade] || {})[t] || {};
        const value = selected.color_name || selected.option_name || selected.product_name;
        if (value) parts.push(meta.label + ': ' + value + (selected.style_name ? ' (' + selected.style_name + ')' : ''));
      }
      const placementDoc=_vzPlacementDoc(),placementAssignments=placementDoc.concepts[t]||{};
      for(const slot of _vzPlacementSlots()){
        const assignment=placementAssignments[slot.id];
        if(assignment&&_vzScopeRoles().includes(slot.role))parts.push(slot.label+': '+(assignment.product_name||'placed product'));
      }
      capEl.textContent = parts.join(' · ');
    }
  }
  const legend = document.getElementById('vz-canvas-legend');
  if (legend) {
    legend.textContent = vzState.placementOpen
      ? 'Exact product placement — drag a numbered corner to fit the frame, or drag inside the outline to move it.'
      : vzState.alignmentOpen
      ? 'Perspective alignment — drag the numbered corners around one flat material plane.'
      : !vzState.refine || vzState.original || vzState.detecting
      ? (vzState.original ? 'Original photo' : 'Design preview — use the dropdowns to explore options. Review automatic selections before saving.')
      : vzState.activeTool === 'erase'
      ? 'Erase mode — swipe to remove marks from either layer.'
      : (vzState.magicWand
          ? `Magic Wand — tap any point of the ${vzState.activeTool} to fill it.`
          : `Painting: ${_VZ_ROLE_META[vzState.activeTool]?.icon || ''} ${_VZ_ROLE_META[vzState.activeTool]?.label || vzState.activeTool}`);
  }
}

// ── Save ────────────────────────────────────────────────────────────────
// Upload base + masks + tier renders + selections. Each blob goes through
// POST .../visualizer/asset; the state PUT rides last so the pointer fields
// are current on the server side by the time selections write.
function _vzFinalizeVisualizerInteraction() {
  if(!vzState)return;
  const slot=(vzState.placementDragIndex>=0||vzState.placementMoveWhole)?_vzActivePlacementSlot():null;
  if(slot)slot.quad=slot.quad.map(point=>point.map(_vzRoundCoord));
  const plane=vzState.alignmentDragIndex>=0?_vzActiveProjectionPlane():null;
  if(plane)plane.quad=plane.quad.map(point=>point.map(_vzRoundCoord));
  vzState.placementDragIndex=-1;vzState.placementMoveWhole=false;vzState.placementLastPoint=null;
  vzState.alignmentDragIndex=-1;vzState.painting=false;
  for(const field of ['placementFrame','alignmentFrame']){
    const id=vzState[field];if(!id)continue;
    if(typeof cancelAnimationFrame==='function')cancelAnimationFrame(id);
    clearTimeout(id);vzState[field]=0;
  }
}
async function _vzSaveAll() {
  if (!vzState?.photoImg || vzState.owner !== S || vzState.detecting || vzState.saving || vzState.proviaUploading) return false;
  _vzFinalizeVisualizerInteraction();
  const roles = _vzScopeRoles();
  const hasSurface = roles.some(role => {
    const mask = vzState[role + 'Mask'];
    if (!mask) return false;
    const pixels = mask.getContext('2d').getImageData(0, 0, mask.width, mask.height).data;
    for (let i = 3; i < pixels.length; i += 4) if (pixels[i]) return true;
    return false;
  });
  const placementDoc=_vzPlacementDoc(),scopeSet=new Set(roles);
  const hasPlacement=Object.values(placementDoc.slots).some(slot=>scopeSet.has(slot.role)&&
    TIERS.some(tier=>!!placementDoc.concepts[tier]?.[slot.id]));
  if (!hasSurface && !hasPlacement) { alert('No project surfaces or exact products are selected yet. Run automatic selection, use Refine selection, or place a product before saving.'); return false; }
  const eid = S.estimate_id;
  if (!eid) { alert('Save the estimate first so this design has a customer file.'); return false; }
  const state = vzState, vz = _vzGet(), elevation = _vzElevation();
  const btn = document.getElementById('vz-save-btn');
  let succeeded = false;
  state.saving = true;
  state.painting = false;
  _vzDetectionUI();
  if (btn) btn.textContent = 'Saving…';
  try {
    for (const tier of TIERS) _vzEnsureTier(tier);
    const selections = JSON.parse(JSON.stringify(vz.selections));
    const selectedRows = Object.entries(selections).flatMap(([trade, tiers]) =>
      Object.values(tiers || {}).map(selected =>
        _vzEffectiveExteriorSelection(trade, selected)));
    const patterns = new Set(selectedRows.map(s => s.pattern_id).filter(Boolean));
    const textures = new Set(selectedRows.map(s => s.texture_ref).filter(Boolean));
    await Promise.all([...patterns].map(pid => _vzImageReady(_vzGetPatternImg(pid))));
    await Promise.all([...textures].map(ref => _vzImageReady(_vzGetTextureImg(ref))));
    const placementRefs=new Set();
    for(const assignments of Object.values(_vzPlacementDoc().concepts||{}))for(const assignment of Object.values(assignments||{}))if(assignment.asset_ref)placementRefs.add(assignment.asset_ref);
    await Promise.all([...placementRefs].map(ref=>_vzImageReady(_vzGetPlacementImg(ref))));
    if (state !== vzState || state.owner !== S) throw new Error('Estimate changed before saving the design. Return to it and save again.');
    // Snapshot all pixels before the first upload so another estimate/tier
    // cannot slip into a save while network requests are in flight.
    const uploads = [];
    const elevationMeta = {elevation_id:elevation.id,elevation_name:elevation.name};
    if (state.pendingBaseDataUrl) uploads.push({body: {kind: 'base', ext: state.pendingBaseExt,
      content_b64: state.pendingBaseDataUrl.split(',')[1], ...elevationMeta}, key: 'base_image'});
    for (const role of roles) uploads.push({body: {kind: 'mask', role, ext: 'png',
      content_b64: state[role + 'Mask'].toDataURL('image/png').split(',')[1], ...elevationMeta}, role});
    for (const tier of TIERS) {
      const off = _vzMakeMaskCanvas(state.canvas.width, state.canvas.height);
      _vzComposeInto(off, tier, {showMaskOverlay: false});
      uploads.push({body: {kind: 'render', tier, ext: 'jpg', content_b64: off.toDataURL('image/jpeg', 0.9).split(',')[1], ...elevationMeta}, tier});
    }
    for (const asset of uploads) {
      const result = await _vzPostAsset(eid, asset.body);
      if (asset.tier) elevation.tier_renders[asset.tier] = result.filename;
      else if (asset.role) elevation.masks[asset.role] = result.filename;
      else if (asset.key === 'base_image') { elevation.base_image = result.filename; state.pendingBaseDataUrl = null; }
      if (elevation.id === 'front') {
        vz.base_image = elevation.base_image;
        vz.tier_renders = Object.assign({},elevation.tier_renders);
        if (asset.role) vz[asset.role + '_mask'] = result.filename;
      }
    }
    const response = await fetch('/api/estimates/' + encodeURIComponent(eid) + '/visualizer/state', {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(_vzElevationMetaPayload({selections,
        invalidate_other_renders:state.selectionsChanged}))});
    if (!response.ok) throw new Error('Images uploaded, but design choices were not saved. Please retry Save Renderings.');
    state.dirty = false;
    state.selectionsChanged = false;
    state.projectionChanged = false;
    state.placementsChanged = false;
    succeeded = true;
    if (state === vzState && state.owner === S) {
      // The parent estimate can have unsaved work; don't mark the whole file clean.
      setDirty();
      if (btn) btn.textContent = 'Saved renderings';
    }
  } catch (error) {
    if (state === vzState && state.owner === S) {
      alert('Design save failed: ' + error.message);
      if (btn) btn.textContent = 'Retry Save Renderings';
    }
  } finally {
    state.saving = false;
    if (state === vzState && state.owner === S) _vzDetectionUI();
  }
  return succeeded;
}

async function _vzPostAsset(eid, body) {
  const r = await fetch(`/api/estimates/${eid}/visualizer/asset`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`asset POST failed (${r.status}): ${t.slice(0, 200)}`);
  }
  return r.json();
}

// ── Service Worker registration (Android PWA install + offline support) ──
// Scoped to the mount prefix, not '/'. Before the merge this worker and the
// CRM's both claimed root scope with different cache names, so on one origin
// whichever registered last would win and silently serve the other app's
// shell. A worker's scope cannot be broader than the path it is served from,
// so /estimate/sw.js can only ever control /estimate/.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register(BASE + '/sw.js', { scope: BASE + '/' })
      .catch(err => console.warn('[SW] registration failed:', err));
  });
}
