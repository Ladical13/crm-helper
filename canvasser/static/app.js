/* P1 Canvasser — main app logic */

// Mount prefix: '/canvass' inside the portal (portal/mounts.py), '' when this
// app is served standalone. Derived from the URL so one bundle works both ways.
// Declared up here rather than beside api() because api() is called during
// initial page setup, which would hit the const's temporal dead zone.
const BASE = location.pathname.startsWith('/canvass') ? '/canvass' : '';

const PIN_TYPES = {};  // populated from /api/config
const PIN_STAGE = {};  // pin type → Pipeline stage, same source
let map, currentUser, markers = {}, hailLayer = null, pinLayer = null;
let teamMarkers = {}, teamTimer = null, locationTimer = null, teamEnabled = true;
let hailResultLayer = null;
let activeHailQuery = null, hailMoveTimer = null, hailRequestId = 0;
let archiveStorms = [];   // the radar archive's own storm days, for the picker
let pendingLatLng = null;   // where the next pin will land
let selectedPinType = 'not_home';
let editingPinId = null;
let activeFilters = new Set();  // empty = show all
const TEAM_PREF_KEY = 'p1canvass.teamLocations';
let allPins = [];

// ── Bootstrap ──────────────────────────────────────────────────────────────

const OFFLINE_SESSION = 'p1canvass.offlineSession';
let offlineMode = false;
async function boot() {
  let cached;
  try { cached = JSON.parse(localStorage.getItem(OFFLINE_SESSION) || 'null'); } catch(e) {}
  try {
    const me = await api('/api/me');
    if (!me.authenticated) {
      try { localStorage.removeItem(OFFLINE_SESSION); } catch(e) {}
      window.location = '/login'; return;
    }
    currentUser = me;
    const cfg = await api('/api/config');
    Object.assign(PIN_TYPES, cfg.pin_types);
    Object.assign(PIN_STAGE, cfg.pin_stage || {});
    try { localStorage.setItem(OFFLINE_SESSION, JSON.stringify({me, cfg, saved:Date.now()})); } catch(e) {}
  } catch(e) {
    if (!cached || Date.now() - cached.saved > 14 * 86400000 || e.message === 'Unauthorized') {
      const notice = document.createElement('p');
      notice.style.cssText = 'padding:32px;color:#eee;text-align:center';
      notice.textContent = 'Connect to the internet and reload to sign in before capturing doors offline.';
      document.body.appendChild(notice);
      return;
    }
    currentUser = cached.me;
    Object.assign(PIN_TYPES, cached.cfg.pin_types);
    Object.assign(PIN_STAGE, cached.cfg.pin_stage || {});
    offlineMode = true;
  }
  showApp();
  if (offlineMode) showMapNotice('Offline capture for ' + currentUser.username + '. Map tiles and team data need a connection.');
}

// ── App init ───────────────────────────────────────────────────────────────

function showApp() {
  show('app');
  $('rep-name-badge').textContent = `Rep: ${displayName(currentUser.username)}`;
  buildQuickBtns();
  buildRepFilters();
  initMap();
  loadPins().then(restorePendingPins).then(flushOutbox);
  setInterval(refreshFieldData, 30000);
  loadTeamPref();
  startTeamTracking();
  if (currentUser.is_admin) show('team-admin-btn');
}

function buildQuickBtns() {
  const bar = $('quick-btns');
  bar.innerHTML = '';
  const order = ['not_home','come_back','interested','appointment','inspected','closed'];
  order.forEach(type => {
    const meta = PIN_TYPES[type] || { label: type, color: '#6B7280' };
    const btn = document.createElement('button');
    btn.className = 'quick-pin-btn';
    btn.style.background = meta.color;
    btn.textContent = meta.label;
    btn.dataset.type = type;
    btn.addEventListener('click', () => openDropPinModal(null, type));
    bar.appendChild(btn);
  });
}

// ── Map ────────────────────────────────────────────────────────────────────

function initMap() {
  if (map) return;  // logout → login reuses the same map container
  // Default to Fort Collins, CO (northern CO market)
  map = L.map('map', {
    zoomControl: false, attributionControl: true,
    preferCanvas: true,   // hail circles render on canvas instead of SVG DOM nodes
    maxZoom: 20,
  }).setView([40.5853, -105.0844], 14);

  // Satellite tile layer (ESRI World Imagery — free, no API key).
  // On retina phones fetch one zoom level deeper and render at 2x density;
  // past Esri's native imagery depth, upscale instead of showing gray tiles.
  const retina = window.devicePixelRatio > 1;
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    maxNativeZoom: retina ? 18 : 19, maxZoom: 20,
    detectRetina: retina,
    keepBuffer: 4, updateWhenZooming: false,
    attribution: 'Tiles &copy; Esri'
  }).addTo(map);

  // Road/place labels — CARTO's retina-aware label tiles are far sharper than
  // Esri's dated Boundaries_and_Places raster layer
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    subdomains: 'abcd', maxNativeZoom: 19, maxZoom: 20,
    keepBuffer: 4, updateWhenZooming: false, opacity: .95,
  }).addTo(map);

  // Pin layer: clustered when zoomed out, individual pins at street level
  pinLayer = (L.markerClusterGroup ? L.markerClusterGroup({
    maxClusterRadius: 46,
    disableClusteringAtZoom: 17,
    spiderfyOnMaxZoom: false,
    showCoverageOnHover: false,
    iconCreateFunction: makeClusterIcon,
  }) : L.layerGroup()).addTo(map);

  // Tap on empty map → drop pin
  map.on('click', (e) => {
    // Don't open modal if a marker was clicked
    if (e.originalEvent._markerClick) return;
    openDropPinModal(e.latlng, selectedPinType);
  });

  map.on('moveend', () => {
    hailRequestId++;
    clearTimeout(hailMoveTimer);
    if (activeHailQuery) hailMoveTimer = setTimeout(refreshHailOverlay, 350);
  });

  // Locate me button
  $('locate-btn').addEventListener('click', locateMe);

  // Try to jump to user's location on load
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      pos => map.setView([pos.coords.latitude, pos.coords.longitude], 17),
      () => {} // ignore errors — stay at default
    );
  }
}

function locateMe() {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(
    pos => map.setView([pos.coords.latitude, pos.coords.longitude], 18),
    () => alert('Could not get your location. Check browser permissions.')
  );
}

// ── Pins ───────────────────────────────────────────────────────────────────

async function loadPins() {
  try {
    // The endpoint returns {pins, truncated, window_days} rather than a bare
    // array, because a map that has quietly dropped pins looks exactly like a
    // street nobody has knocked.
    const res = await api('/api/pins');
    const pending = allPins.filter(p => p.pending && !(res.pins || []).some(r => r.client_id === p.client_id && r.rep === p.rep));
    allPins = [...pending, ...(res.pins || [])];
    renderPins();
    updateRepFilters();
    if (res.truncated) {
      showMapNotice(`Showing the most recent ${allPins.length} pins of the last `
        + `${res.window_days} days — some are not on the map.`);
    }
  } catch(e) { console.error('Failed to load pins', e); }
}

// A one-line banner over the map. Deliberately not an alert(): a rep in a
// driveway should not have to dismiss a dialog to see the street.
function showMapNotice(text) {
  const el = $('map-notice');
  if (!el) return;
  el.textContent = text;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 8000);
}

function renderPins() {
  pinLayer.clearLayers();
  markers = {};

  const filtered = activeFilters.size > 0
    ? allPins.filter(p => activeFilters.has(p.rep))
    : allPins;

  const batch = filtered.map(pin => buildPinMarker(pin));
  // markercluster's addLayers is a fast bulk insert; layerGroup fallback loops
  if (pinLayer.addLayers) pinLayer.addLayers(batch);
  else batch.forEach(m => pinLayer.addLayer(m));
}

function buildPinMarker(pin, animate = false) {
  const meta  = PIN_TYPES[pin.pin_type] || { label: pin.pin_type, color: '#6B7280' };
  const initials = (pin.rep || '?').substring(0, 2).toUpperCase();

  const icon = L.divIcon({
    className: '',
    html: `<div class="pin-marker${animate ? ' drop' : ''}${pin.pending ? ' pending' : ''}" title="${pin.pending ? 'Waiting to sync — ' : ''}${meta.label} — ${escHtml(displayName(pin.rep))}">
      <svg viewBox="0 0 30 40" width="30" height="40">
        <path d="M15 39C15 39 27 22.5 27 13.5 27 6.6 21.6 1.5 15 1.5 8.4 1.5 3 6.6 3 13.5 3 22.5 15 39 15 39Z"
              fill="${meta.color}" stroke="rgba(255,255,255,.95)" stroke-width="1.8"/>
      </svg>
      <span class="pin-initials">${escHtml(initials)}</span>
    </div>`,
    iconSize:   [30, 40],
    iconAnchor: [15, 38],
  });

  const marker = L.marker([pin.lat, pin.lng], { icon });
  marker.on('click', (e) => {
    e.originalEvent._markerClick = true;
    // A queued pin has no server id yet, so every control on the detail panel
    // (edit, delete, add to Pipeline) would address a row that does not exist.
    // Say what it is instead of opening a panel that cannot work.
    if (pin.pending) {
      showMapNotice('This door is saved on your phone and will sync when you have signal.');
      return;
    }
    showPinDetail(pin);
  });
  markers[pin.id] = marker;
  return marker;
}

function addPinMarker(pin, animate = false) {
  pinLayer.addLayer(buildPinMarker(pin, animate));
}

function makeClusterIcon(cluster) {
  const n = cluster.getChildCount();
  const size = n < 10 ? 34 : n < 50 ? 40 : 46;
  return L.divIcon({
    html: `<div class="cluster-bubble" style="width:${size}px;height:${size}px">${n}</div>`,
    className: '', iconSize: [size, size], iconAnchor: [size / 2, size / 2],
  });
}

// ── Live team tracking ─────────────────────────────────────────────────────

async function pushLocationNow() {
  if (!teamEnabled) return;
  try {
    const pos = await getGPS();
    await api('/api/location', 'POST', {
      lat: pos.coords.latitude, lng: pos.coords.longitude,
      accuracy: pos.coords.accuracy || 0, heading: pos.coords.heading ?? -1,
    });
  } catch(e) { /* no GPS permission or offline — skip this ping */ }
}

function startTeamTracking() {
  stopTeamTracking();
  // Push my location every 30s (silently — only if permission already granted).
  //
  // The teamEnabled check here is the whole point of the toggle. It used to
  // gate only the PULL below, so a rep who switched "Team Locations" off
  // stopped seeing their teammates and kept broadcasting their own position to
  // everyone else — including from their kitchen table at 9pm, because the
  // timer runs as long as the tab is open. A switch that says Off and does not
  // stop is worse than no switch: the rep believes something about their own
  // phone that is not true.
  pushLocationNow();
  locationTimer = setInterval(pushLocationNow, 30000);

  // Pull teammates every 30s
  const pullTeam = async () => {
    if (!teamEnabled) return;
    try {
      const team = await api('/api/team-locations');
      renderTeamMarkers(team);
    } catch(e) {}
  };
  pullTeam();
  teamTimer = setInterval(pullTeam, 30000);
}

function stopTeamTracking() {
  if (teamTimer)     { clearInterval(teamTimer);     teamTimer = null; }
  if (locationTimer) { clearInterval(locationTimer); locationTimer = null; }
  clearTeamMarkers();
}

function clearTeamMarkers() {
  Object.values(teamMarkers).forEach(m => map.removeLayer(m));
  teamMarkers = {};
}

function renderTeamMarkers(team) {
  clearTeamMarkers();
  team.forEach(t => {
    const isMe = t.username === currentUser.username;
    const initials = t.username.substring(0, 2).toUpperCase();
    const icon = L.divIcon({
      className: '',
      html: `<div class="team-marker ${isMe ? 'me' : ''}" title="${escHtml(displayName(t.username))} — ${timeAgo(t.updated_at)}">
               <div class="team-pulse"></div><span>${escHtml(initials)}</span>
             </div>`,
      iconSize: [34, 34], iconAnchor: [17, 17],
    });
    teamMarkers[t.username] = L.marker([t.lat, t.lng], { icon, zIndexOffset: 900 })
      .bindPopup(`<b>${escHtml(displayName(t.username))}</b><br>Active ${timeAgo(t.updated_at)}`)
      .addTo(map);
  });
}

$('team-toggle-btn').addEventListener('click', async () => {
  teamEnabled = !teamEnabled;
  $('team-toggle-state').textContent = teamEnabled ? 'On' : 'Off';
  try { localStorage.setItem(TEAM_PREF_KEY, teamEnabled ? '1' : '0'); } catch(e) {}
  if (!teamEnabled) {
    clearTeamMarkers();
    // Drop the position already on the server rather than letting it sit on
    // everyone else's map for the 15 minutes it stays "live". Off should mean
    // off now, not off soon.
    try { await api('/api/location', 'DELETE', null); } catch(e) {}
  } else {
    pushLocationNow();
  }
});

// Read the saved preference before the first push, so a rep who turned
// tracking off does not get one more broadcast on every app launch.
function loadTeamPref() {
  try {
    const saved = localStorage.getItem(TEAM_PREF_KEY);
    if (saved !== null) teamEnabled = saved === '1';
  } catch(e) { /* private mode or blocked storage — default stays On */ }
  const state = $('team-toggle-state');
  if (state) state.textContent = teamEnabled ? 'On' : 'Off';
}

// ── Drop pin modal ─────────────────────────────────────────────────────────

function openDropPinModal(latlng, defaultType) {
  selectedPinType = defaultType || 'not_home';
  pendingLatLng   = latlng; // null = use GPS

  buildTypeSelector('type-selector', (type) => { selectedPinType = type; });
  setSelectedType('type-selector', selectedPinType);

  // Show contact fields for types that need them
  updateContactFieldsVisibility();

  // Clear fields
  ['pin-address','pin-contact-name','pin-contact-phone','pin-contact-email',
   'pin-notes','pin-appointment-at'].forEach(id => {
    const el = $(id);
    if (el) el.value = '';
  });

  show('drop-pin-modal');
  const nearbyBox = $('pin-nearby-warning');
  if (nearbyBox) {
    const nearby = latlng ? allPins.filter(p => Math.hypot((p.lat-latlng.lat)*111000, (p.lng-latlng.lng)*85000) < 35) : [];
    nearbyBox.textContent = nearby.map(p => `${PIN_TYPES[p.pin_type]?.label || p.pin_type} — ${displayName(p.rep)}, ${timeAgo(p.created_at)}`).join(' · ');
    nearbyBox.classList.toggle('hidden', !nearby.length);
  }

  // Auto-fill the address from the tapped location (best-effort)
  if (latlng) {
    api(`/api/geocode/reverse?lat=${latlng.lat}&lng=${latlng.lng}`)
      .then(a => {
        const parts = [a.street, a.city, a.state].filter(Boolean).join(', ');
        if (parts && !$('pin-address').value) $('pin-address').value = parts;
      })
      .catch(() => {});
  }
}

function updateContactFieldsVisibility() {
  const contactTypes = ['come_back','interested','appointment','inspected','closed'];
  $('contact-fields').style.display = contactTypes.includes(selectedPinType) ? 'block' : 'none';

  // An appointment pin is the only one that has a time, and it is the whole
  // reason this field exists: the pin used to map to `appt_set` carrying no
  // date at all, so nothing downstream could remind anybody and a door-set
  // appointment lived only in the rep's head.
  const appt = $('appointment-fields');
  if (!appt) return;
  const isAppt = selectedPinType === 'appointment';
  appt.classList.toggle('hidden', !isAppt);
  const box = $('pin-appointment-at');
  // A default the rep can accept with one tap beats an empty box they skip
  // past. Tomorrow at 5pm is the ordinary shape of a door-set appointment;
  // it is a starting point, not a guess about their day.
  if (isAppt && box && !box.value) box.value = defaultApptLocal();
}

function defaultApptLocal() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(17, 0, 0, 0);
  // datetime-local wants LOCAL wall-clock text. toISOString() would convert to
  // UTC and hand a Colorado rep a time six or seven hours off their own field.
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}` +
         `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function prettyAppt(v) {
  if (!v) return '';
  const [d, t] = String(v).split('T');
  const [y, m, day] = (d || '').split('-').map(Number);
  const [hh, mm] = (t || '').split(':').map(Number);
  if (!y || hh == null) return v;
  // Built from the parts, never `new Date(v)`: Safari and Chrome disagree about
  // whether a bare 'YYYY-MM-DDTHH:MM' is local or UTC, and the wrong branch
  // shifts a 6pm appointment by the offset.
  return new Date(y, m - 1, day, hh, mm).toLocaleString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit',
  });
}

$('close-drop-modal').addEventListener('click', () => hide('drop-pin-modal'));

// Hail lookup for the tapped structure — uses the exact tap coordinates
$('hail-here-btn').addEventListener('click', async () => {
  let lat, lng;
  if (pendingLatLng) {
    lat = pendingLatLng.lat; lng = pendingLatLng.lng;
  } else {
    try {
      const pos = await getGPS();
      lat = pos.coords.latitude; lng = pos.coords.longitude;
    } catch(e) {
      const c = map.getCenter(); lat = c.lat; lng = c.lng;
    }
  }
  hide('drop-pin-modal');
  $('hail-address-input').value = $('pin-address').value || '';
  $('hail-address-results').innerHTML = '';
  $('hail-address-status').textContent = 'Checking radar hail history for this spot...';
  show('hail-address-modal');
  try {
    const days   = $v('hail-address-days')   || 1825;
    const radius = $v('hail-address-radius') || 10;
    const data = await api(`/api/hail/address?lat=${lat}&lng=${lng}&days=${days}&radius=${radius}`);
    // Show the tapped address (or coords) as the result label
    if (!data.resolved) data.resolved = $('pin-address').value || `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    if (!data.query)    data.query    = data.resolved;
    renderHailAddressResults(data);
  } catch(e) {
    $('hail-address-status').textContent = 'Lookup failed: ' + e.message;
  }
});

$('save-pin-btn').addEventListener('click', async () => {
  let lat, lng;

  if (pendingLatLng) {
    lat = pendingLatLng.lat;
    lng = pendingLatLng.lng;
  } else {
    // Try GPS first, fall back to map center
    try {
      const pos = await getGPS();
      lat = pos.coords.latitude;
      lng = pos.coords.longitude;
    } catch(e) {
      const center = map.getCenter();
      lat = center.lat;
      lng = center.lng;
    }
  }

  const payload = {
    lat, lng,
    pin_type:      selectedPinType,
    address:       $v('pin-address'),
    notes:         $v('pin-notes'),
    contact_name:  $v('pin-contact-name'),
    contact_phone: $v('pin-contact-phone'),
    contact_email: $v('pin-contact-email'),
    appointment_tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
    appointment_at: selectedPinType === 'appointment' ? $v('pin-appointment-at') : '',
  };

  // The one required field in this app, and only on this one pin type.
  //
  // An appointment with no name cannot become a lead: the Pipeline needs
  // somebody to follow up with, so it gets no cadence, no task, no reminder
  // and no leaderboard credit — the rep did the hardest work of the day and
  // the system recorded a coloured dot. If you set an appointment you spoke
  // to someone, so the name exists; it just was not being asked for.
  if (selectedPinType === 'appointment' && !payload.contact_name.trim()) {
    alert('An appointment needs a name — without one it cannot become a lead, ' +
          'so nothing will remind you about it.');
    $('pin-contact-name').focus();
    return;
  }

  try {
    const result = await savePinThroughOutbox(payload);
    hide('drop-pin-modal');
    const pin = result.pin;
    allPins.unshift(pin);
    addPinMarker(pin, true);
    map.panTo([pin.lat, pin.lng]);
    if (result.queued) {
      showMapNotice('No signal — saved on this phone. It will sync by itself.');
    } else {
      // Hand off on the spot rather than behind a second button the rep has to
      // remember, on a screen they have already walked away from.
      await autoHandoff(pin);
    }
  } catch(e) {
    // Everything retryable was queued rather than thrown, so reaching here
    // means the server understood the pin and refused it. That is worth an
    // alert: it will not fix itself, and it is the rep's to correct.
    alert('Failed to save pin: ' + e.message);
  }
});

// ── Offline outbox ─────────────────────────────────────────────────────────
//
// This tool exists for driveways on one bar of signal, and until now a pin
// POST that failed was shown in an `alert()` and thrown away — the one write
// that matters, lost, at the exact moment the app was built for. A rep who
// loses a door once stops trusting the app and goes back to a notepad, so
// this is the gate on rolling the canvasser out at all.
//
// Three decisions worth keeping:
//
// **IndexedDB, not memory and not localStorage.** The queue has to survive iOS
// reclaiming a backgrounded tab, which is the normal end of a canvassing
// session, not an edge case. localStorage would survive too but it is
// synchronous on the main thread and shares one string budget with everything
// else; a queue belongs in a store built for records.
//
// **No Background Sync, deliberately.** It is the textbook answer and it does
// not work here: WebKit has never shipped it, and every rep on this team runs
// the app as an installed PWA on an iPhone. A `sync` handler would be dead
// code on precisely the devices this exists for, while reading as though the
// problem were handled. The flush is driven by the page instead — on boot, on
// `online`, and on `visibilitychange`, which is what actually fires when a rep
// pockets the phone in a dead zone and pulls it out two streets later.
//
// **Every queued save carries a `client_id`.** A retry whose first attempt
// actually landed must not write a second door: two reps then each believe the
// other knocked that street, and the leaderboard that pays them inflates. The
// server dedupes on it (`create_pin`), so a replay is free.

const OUTBOX_DB    = 'p1canvass';
const OUTBOX_STORE = 'outbox';
let outboxFlushing = false;

function idbOpen() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) return reject(new Error('no indexedDB'));
    const req = indexedDB.open(OUTBOX_DB, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(OUTBOX_STORE)) {
        db.createObjectStore(OUTBOX_STORE, { keyPath: 'client_id' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror   = () => reject(req.error);
  });
}

function idbDo(mode, fn) {
  return idbOpen().then(db => new Promise((resolve, reject) => {
    const tx = db.transaction(OUTBOX_STORE, mode);
    const req = fn(tx.objectStore(OUTBOX_STORE));
    tx.oncomplete = () => { db.close(); resolve(req ? req.result : undefined); };
    tx.onerror    = () => { db.close(); reject(tx.error); };
    tx.onabort    = () => { db.close(); reject(tx.error); };
  }));
}

// Private browsing, a blocked-storage setting and a quota refusal all throw
// here. A rep losing the queue is bad; a rep unable to drop a pin at all
// because the queue would not open is worse, so every caller degrades.
const outboxAll    = () => idbDo('readonly',  st => st.getAll()).catch(() => []);
const outboxPut    = e  => idbDo('readwrite', st => st.put(e));
const outboxDelete = id => idbDo('readwrite', st => st.delete(id)).catch(() => null);

function newClientId() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return 'c-' + Date.now() + '-' + Math.random().toString(16).slice(2);
}

// A POST that reports rather than redirects. api() bounces to /login on a 401,
// which is right for a tap the rep is watching and wrong for a background
// flush: it would throw a rep out of the app mid-street to re-authenticate a
// queue that was going to wait anyway.
async function postPinRaw(payload) {
  const res = await fetch(BASE + '/api/pins', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(payload),
  });
  let json = null;
  try { json = await res.json(); } catch(e) { /* an HTML error page */ }
  return { ok: res.ok, status: res.status, json };
}

function pendingPinFrom(payload) {
  return {
    ...payload,
    id:         'pending:' + payload.client_id,
    rep:        payload.owner || '',
    created_at: new Date().toISOString(),
    pending:    true,
  };
}

// Returns { pin, queued }. `pin` is always something to draw, so the rep sees
// their door either way; `queued` says whether the server has it yet.
async function savePinThroughOutbox(payload) {
  payload.client_id = payload.client_id || newClientId();
  payload.owner = currentUser.username;
  const entry = {...payload, queued_at:Date.now()};
  let durable = false;
  try { await outboxPut(entry); durable = true; } catch(e) {}
  let out;
  try { out = await postPinRaw(payload); }
  catch(e) {
    if (!durable) throw new Error('Not saved: no connection and phone storage is unavailable. Keep this form open and reconnect.');
    updateOutboxBadge();
    return {pin:pendingPinFrom(payload), queued:true};
  }
  if (out.ok) {
    if (durable) await outboxDelete(payload.client_id);
    return {pin:out.json, queued:false};
  }
  if (out.status >= 500 || out.status === 401 || out.status === 403) {
    if (!durable) throw new Error('Not saved: phone storage is unavailable. Keep this form open and retry.');
    updateOutboxBadge();
    return {pin:pendingPinFrom(payload), queued:true};
  }
  if (durable) await outboxPut({...entry, failed:(out.json && out.json.error) || `HTTP ${out.status}`});
  throw new Error((out.json && out.json.error) || `HTTP ${out.status}`);
}

async function flushOutbox() {
  if (outboxFlushing || !navigator.onLine) return;
  outboxFlushing = true;
  let landed = 0;
  try {
    const queued = await outboxAll();
    for (const entry of queued) {
      if (entry.owner !== currentUser.username || entry.failed) continue;
      const { queued_at, failed, ...payload } = entry;
      let out;
      try {
        out = await postPinRaw(payload);
      } catch(e) {
        break;   // still offline; leave this and everything after it queued
      }
      if (out.status === 401 || out.status === 403) break;              // session expired: it waits
      if (out.ok) {
        await outboxDelete(entry.client_id);
        replacePendingPin(entry.client_id, out.json);
        // A door queued in a dead zone still owes the Pipeline a lead. Doing it
        // here rather than at save time is the only place it can happen: there
        // was no network when the rep tapped Save, and the CRM — not this app —
        // owns what a lead is, so it cannot be queued offline alongside the pin.
        await autoHandoff(out.json);
        landed++;
      } else if (out.status < 500) {
        const reason = (out.json && out.json.error) || `HTTP ${out.status}`;
        await outboxPut({...entry, failed:reason});
        showMapNotice('A door needs attention and remains on this phone: ' + reason);
      } else {
        break;   // 5xx: the server is having a bad time, try again later
      }
    }
  } finally {
    outboxFlushing = false;
    updateOutboxBadge();
  }
  if (landed) {
    renderPins();
    showMapNotice(`${landed} door${landed > 1 ? 's' : ''} synced.`);
  }
}

function replacePendingPin(clientId, pin) {
  const i = allPins.findIndex(p => p.id === 'pending:' + clientId);
  if (i >= 0) allPins[i] = pin; else allPins.unshift(pin);
}

function dropPendingPin(clientId) {
  const i = allPins.findIndex(p => p.id === 'pending:' + clientId);
  if (i >= 0) { allPins.splice(i, 1); renderPins(); }
}

async function updateOutboxBadge() {
  const el = $('outbox-badge');
  if (!el) return;
  const entries = await outboxAll();
  const n = entries.filter(e => !e.owner || e.owner === currentUser?.username).length;
  el.textContent = `${n} saved on phone · tap to review`;
  el.classList.toggle('hidden', n === 0);
}

// Restore queued doors onto the map so a rep who force-quit the app in a dead
// zone still sees the street they worked, rather than an empty map plus a
// badge telling them something exists somewhere.
async function restorePendingPins() {
  const queued = await outboxAll();
  if (!queued.length) return;
  queued.filter(e => e.owner === currentUser.username).forEach(e => {
    const { queued_at, failed, ...payload } = e;
    if (!allPins.some(p => p.id === 'pending:' + payload.client_id)) {
      allPins.unshift(pendingPinFrom(payload));
    }
  });
  renderPins();
}

window.addEventListener('online', refreshFieldData);
// The one that actually fires on iOS. `online` is unreliable when the app was
// backgrounded through the change of signal, which is the normal case here:
// phone in pocket between streets.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') refreshFieldData();
});

let fieldRefreshing = false;
async function refreshFieldData() {
  if (fieldRefreshing || !currentUser || !navigator.onLine || document.visibilityState === 'hidden') return;
  fieldRefreshing = true;
  try {
    const me = await api('/api/me');
    if (!me.authenticated || me.username !== currentUser.username) { window.location.reload(); return; }
    offlineMode = false;
    await flushOutbox();
    await loadPins();
    await restorePendingPins();
    for (const pin of allPins.filter(p => p.rep === currentUser.username && canHandoff(p)).slice(0, 20)) {
      try { await handoffToPipeline(pin); } catch(e) { break; }
    }
  } catch(e) {} finally { fieldRefreshing = false; }
}

$('outbox-badge')?.addEventListener('click', async () => {
  let entries = await outboxAll();
  const legacy = entries.filter(e => !e.owner);
  if (legacy.length && confirm(`${legacy.length} older saved doors have no recorded owner. Confirm these are YOUR doors before syncing them as ${currentUser.username}:\n` + legacy.map(e => e.address || 'Address not entered').join('\n'))) {
    for (const entry of legacy) await outboxPut({...entry, owner:currentUser.username});
  }
  entries = (await outboxAll()).filter(e => e.owner === currentUser.username);
  const failed = entries.filter(e => e.failed);
  if (failed.length) alert('These doors remain on your phone and need correction:\n' + failed.map(e => `${e.address || 'Door'}: ${e.failed}`).join('\n'));
  if (entries.length && confirm('Download a backup of your saved doors before retrying?')) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(entries, null, 2)], {type:'application/json'}));
    const link = document.createElement('a'); link.href = url; link.download = 'saved-doors.json'; link.click(); URL.revokeObjectURL(url);
  }
  await restorePendingPins();
  await refreshFieldData();
});

async function getGPS() {
  if (!navigator.geolocation) throw new Error('unavailable');
  // Check permission first so we never block on the browser dialog
  if (navigator.permissions) {
    const status = await navigator.permissions.query({ name: 'geolocation' });
    if (status.state === 'denied') throw new Error('denied');
    if (status.state === 'prompt') throw new Error('prompt'); // don't show dialog mid-save
  }
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(resolve, reject,
      { timeout: 4000, maximumAge: 10000, enableHighAccuracy: true });
  });
}

// ── Pin detail ─────────────────────────────────────────────────────────────

function showPinDetail(pin) {
  const meta = PIN_TYPES[pin.pin_type] || { label: pin.pin_type, color: '#6B7280' };

  $('pin-detail-title').textContent = meta.label;

  const isOwner = pin.rep === currentUser.username || currentUser.is_admin;
  // Any door with a name and a reason to come back belongs in the Pipeline —
  // not only a closed deal, which is what the old Den sync was limited to.
  const canPipeline = !!PIN_STAGE[pin.pin_type];
  const alreadyInPipeline = !!pin.crm_lead_id;

  const rows = [
    ['Rep',    escHtml(displayName(pin.rep))],
    pin.address       ? ['Address', escHtml(pin.address)] : null,
    pin.contact_name  ? ['Contact', escHtml(pin.contact_name)] : null,
    pin.contact_phone ? ['Phone',   `<a href="tel:${escHtml(pin.contact_phone)}" style="color:#10B981">${escHtml(pin.contact_phone)}</a>`] : null,
    pin.contact_email ? ['Email',   escHtml(pin.contact_email)] : null,
    // Above the notes on purpose: this is the only row on the panel that is a
    // commitment the rep has to keep, rather than a record of what happened.
    pin.appointment_at ? ['Appointment',
      `<b style="color:#8B5CF6">${escHtml(prettyAppt(pin.appointment_at))}</b>`] : null,
    pin.notes         ? ['Notes',   escHtml(pin.notes)] : null,
    ['When',   timeAgo(pin.created_at)],
  ].filter(Boolean);

  let html = `
    <div class="pin-type-badge" style="background:${meta.color}">
      ${meta.label}
    </div>
    ${rows.map(([label, val]) => `
      <div class="pin-detail-row">
        <div class="pin-detail-label">${label}</div>
        <div>${val}</div>
      </div>`).join('')}
    <div class="pin-action-btns">
  `;

  if (isOwner) {
    html += `<button class="pin-action-btn" onclick="openEditPin('${escHtml(pin.id)}')">✏️ Edit</button>`;
  }
  if (canPipeline && pin.contact_name && !alreadyInPipeline && isOwner) {
    html += `<button class="pin-action-btn crm-btn" onclick="addToPipeline('${pin.id}')">📋 Add to Pipeline</button>`;
  }
  if (alreadyInPipeline) {
    html += `<div style="font-size:12px;color:#10B981;padding:8px 0">✓ In the Pipeline</div>`;
  }
  html += `</div>`;

  $('pin-detail-body').innerHTML = html;
  show('pin-detail');
}

$('close-pin-detail').addEventListener('click', () => hide('pin-detail'));

// ── Pipeline handoff ───────────────────────────────────────────────────────

// The lead is created against the CRM's own API rather than through a
// canvasser endpoint: all four apps share one origin and one cookie, so the
// rep's session already authorizes it, and the CRM stays the only thing that
// decides what a lead is — stage rules, dedupe, and which cadence starts.

// A pin's appointment time is LOCAL wall clock ("Thursday at six" means six in
// that driveway); the CRM's `due_at` is UTC and is compared as text against
// UTC. The browser is the only party that knows the rep's offset, so the
// conversion happens here. Seconds are trimmed to the CRM's own spelling: a
// stored '...:00.000Z' sorts before '...:00Z' as text and would read as due
// fractionally earlier than every other task in the system.
function apptToUtc(local) {
  const [d, t] = String(local || '').split('T');
  const [y, m, day] = (d || '').split('-').map(Number);
  const [hh, mm] = (t || '').split(':').map(Number);
  if (!y || hh == null || isNaN(hh)) return '';
  return new Date(y, m - 1, day, hh, mm).toISOString().slice(0, 19) + 'Z';
}

async function crmPost(path, body) {
  const res = await fetch('/crm' + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(body),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);
  return json;
}

// Creates the lead and everything that hangs off it. Shared by the automatic
// handoff on save and by the manual button, so the two can never drift into
// producing different leads from the same door.
async function handoffToPipeline(pin) {
  const result = await api(`/api/pins/${encodeURIComponent(pin.id)}/handoff`, 'POST', {});
  pin.crm_lead_id = result.id;
  pin.pipeline_pending = false;
  return result;
}

function canHandoff(pin) {
  return !!(pin && !pin.pending && pin.id && (PIN_STAGE[pin.pin_type] || pin.crm_lead_id)
            && (pin.contact_name || '').trim() && (!pin.crm_lead_id || pin.pipeline_pending)
            && (pin.rep === currentUser.username || currentUser.is_admin));
}

// Fires by itself when a pin that should become a lead is saved. Never fatal:
// the door is already recorded, and a rep whose handoff failed still has the
// manual button on the pin. It is also why `handoffToPipeline` records the
// lead id back onto the pin — that is what stops a retry making a second lead.
async function autoHandoff(pin) {
  if (!canHandoff(pin)) return;
  try {
    await handoffToPipeline(pin);
    showMapNotice(pin.appointment_at
      ? `✓ In the Pipeline — appointment ${prettyAppt(pin.appointment_at)} is on your list.`
      : '✓ In the Pipeline. The first follow-up is already on your list.');
  } catch(e) {
    showMapNotice('Saved. Could not add to the Pipeline yet — use the pin\'s ' +
                  'Add to Pipeline button, or leave the app open to retry: ' + e.message);
  }
}

async function addToPipeline(pinId) {
  const pin = allPins.find(p => p.id === pinId);
  if (!pin) return;
  if (!PIN_STAGE[pin.pin_type]) return;
  if (!confirm(`Add ${pin.contact_name} to the Pipeline? Follow-up starts automatically.`)) return;
  try {
    await handoffToPipeline(pin);
    alert('✓ In the Pipeline. The first follow-up is already on your list.');
    hide('pin-detail');
  } catch(e) {
    alert('Could not add to the Pipeline: ' + e.message);
  }
}

// ── Edit pin ───────────────────────────────────────────────────────────────

async function openEditPin(pinId) {
  hide('pin-detail');
  editingPinId = pinId;
  const pin = allPins.find(p => p.id === pinId);
  if (!pin) return;

  // Rescheduling is the common edit on an appointment pin, so the type
  // selector has to move the field too — a rep who taps Appt Set here and
  // cannot enter a time is back to the pin that knows nothing about when.
  const syncEditAppt = (type) => {
    $('edit-appointment-fields').classList.toggle('hidden', type !== 'appointment');
    const box = $('edit-appointment-at');
    if (type === 'appointment' && box && !box.value) box.value = defaultApptLocal();
  };
  buildTypeSelector('edit-type-selector', syncEditAppt);
  setSelectedType('edit-type-selector', pin.pin_type);

  $('edit-appointment-at').value = pin.appointment_at || '';
  syncEditAppt(pin.pin_type);

  $('edit-address').value       = pin.address       || '';
  $('edit-contact-name').value  = pin.contact_name  || '';
  $('edit-contact-phone').value = pin.contact_phone || '';
  $('edit-contact-email').value = pin.contact_email || '';
  $('edit-notes').value         = pin.notes         || '';

  show('edit-pin-modal');
}

$('close-edit-modal').addEventListener('click', () => hide('edit-pin-modal'));

$('update-pin-btn').addEventListener('click', async () => {
  const selectedType = $('edit-type-selector').querySelector('.type-btn.selected')?.dataset.type
    || editingPinId && allPins.find(p=>p.id===editingPinId)?.pin_type;

  const payload = {
    pin_type:      selectedType,
    address:       $('edit-address').value,
    contact_name:  $('edit-contact-name').value,
    contact_phone: $('edit-contact-phone').value,
    contact_email: $('edit-contact-email').value,
    notes:         $('edit-notes').value,
    appointment_tz: allPins.find(p => p.id === editingPinId)?.appointment_tz || Intl.DateTimeFormat().resolvedOptions().timeZone,
    appointment_at: selectedType === 'appointment'
      ? $('edit-appointment-at').value : '',
  };

  try {
    const updated = await api(`/api/pins/${editingPinId}`, 'PUT', payload);
    // Update local
    const idx = allPins.findIndex(p => p.id === editingPinId);
    if (idx >= 0) allPins[idx] = updated;
    // Re-render
    if (markers[editingPinId]) pinLayer.removeLayer(markers[editingPinId]);
    delete markers[editingPinId];
    addPinMarker(updated);
    hide('edit-pin-modal');
    // A door upgraded after the fact — Not Home becomes Appt Set, or a name
    // finally gets typed — is the same event as setting it that way at the
    // door, so it takes the same automatic path. `canHandoff` already refuses
    // a pin that has a lead, so this cannot make a second one.
    await autoHandoff(updated);
  } catch(e) {
    alert('Update failed: ' + e.message);
  }
});

$('delete-pin-btn').addEventListener('click', async () => {
  if (!confirm('Delete this pin?')) return;
  try {
    await api(`/api/pins/${editingPinId}`, 'DELETE', null);
    if (markers[editingPinId]) pinLayer.removeLayer(markers[editingPinId]);
    delete markers[editingPinId];
    allPins = allPins.filter(p => p.id !== editingPinId);
    hide('edit-pin-modal');
  } catch(e) {
    alert('Delete failed: ' + e.message);
  }
});

// ── Leaderboard ────────────────────────────────────────────────────────────

$('show-leaderboard-btn').addEventListener('click', async () => {
  hide('side-menu');
  $('leaderboard-body').innerHTML = '<div class="loading-msg">Loading...</div>';
  show('leaderboard-panel');
  try {
    const res = await api('/api/leaderboard');
    renderLeaderboard(res.reps || [], res.days);
  } catch(e) {
    $('leaderboard-body').innerHTML = '<div class="loading-msg">Failed to load.</div>';
  }
});

// The headline number is APPOINTMENTS, not doors. Doors moved to the small
// print as the volume the rates are built from — a rep can tap "Not Home"
// fifteen times walking down a sidewalk, and whatever sits in the big bold
// position is what they will optimise for.
function renderLeaderboard(rows, days) {
  const body = $('leaderboard-body');
  if (!rows.length) {
    body.innerHTML = `<div class="loading-msg">No doors knocked in the last `
      + `${days || 7} days.</div>`;
    return;
  }
  const rankClass = (i) => i === 0 ? 'gold' : i === 1 ? 'silver' : i === 2 ? 'bronze' : '';
  const pct = (v) => `${Math.round((v || 0) * 100)}%`;
  body.innerHTML = `<div class="lb-window">Last ${days || 7} days</div>` + rows.map((r, i) => `
    <div class="lb-row">
      <div class="lb-rank ${rankClass(i)}">${i < 3 ? ['🥇','🥈','🥉'][i] : i+1}</div>
      <div class="lb-rep">
        <div class="lb-rep-name">${displayName(r.rep)}</div>
        <div class="lb-stats">
          ${r.total_doors} doors · ${r.contacts} contacts · ${pct(r.set_rate)} set rate
        </div>
      </div>
      <div>
        <div class="lb-doors">${r.appointments}</div>
        <div class="lb-doors-label">appts</div>
      </div>
    </div>
  `).join('');
}

// ── My Pins ────────────────────────────────────────────────────────────────

$('show-my-pins-btn').addEventListener('click', async () => {
  hide('side-menu');
  $('my-pins-body').innerHTML = '<div class="loading-msg">Loading...</div>';
  show('my-pins-panel');
  try {
    const res = await api(`/api/pins?rep=${currentUser.username}&limit=200`);
    renderMyPins(res.pins || []);
  } catch(e) {
    $('my-pins-body').innerHTML = '<div class="loading-msg">Failed to load.</div>';
  }
});

function renderMyPins(pins) {
  if (!pins.length) {
    $('my-pins-body').innerHTML = '<div class="loading-msg">No pins yet — get knocking!</div>';
    return;
  }
  $('my-pins-body').innerHTML = pins.map(pin => {
    const meta = PIN_TYPES[pin.pin_type] || { label: pin.pin_type, color: '#6B7280' };
    const sub  = [pin.address, pin.contact_name].filter(Boolean).join(' · ') || 'No address';
    return `
      <div class="pin-list-item" onclick="jumpToPin('${pin.id}')">
        <div class="pin-list-dot" style="background:${meta.color}"></div>
        <div class="pin-list-info">
          <div class="pin-list-type">${meta.label}</div>
          <div class="pin-list-sub">${sub}</div>
        </div>
        <div class="pin-list-time">${timeAgo(pin.created_at)}</div>
      </div>
    `;
  }).join('');
}

function jumpToPin(pinId) {
  hide('my-pins-panel');
  const pin = allPins.find(p => p.id === pinId);
  if (pin) {
    map.setView([pin.lat, pin.lng], 18);
    showPinDetail(pin);
  }
}

// ── Rep filter ─────────────────────────────────────────────────────────────

function buildRepFilters() {
  // Will populate after pins load; re-render on load
}

function updateRepFilters() {
  const reps = [...new Set(allPins.map(p => p.rep))].sort();
  const container = $('rep-filter-list');
  container.innerHTML = '';
  reps.forEach(rep => {
    const chip = document.createElement('div');
    chip.className = 'rep-chip' + (activeFilters.has(rep) ? ' active' : '');
    chip.textContent = displayName(rep);
    chip.addEventListener('click', () => {
      if (activeFilters.has(rep)) activeFilters.delete(rep);
      else activeFilters.add(rep);
      chip.classList.toggle('active', activeFilters.has(rep));
      renderPins();
    });
    container.appendChild(chip);
  });
}

// ── Hail overlay ───────────────────────────────────────────────────────────

$('hail-overlay-btn').addEventListener('click', async () => {
  hide('side-menu');
  const today = new Date().toISOString().split('T')[0];
  $('hail-date').value = today;
  $('hail-status').textContent = '';
  show('hail-modal');
  await loadStormPicker();
  await loadArchiveState();
});

$('close-hail-modal').addEventListener('click', () => hide('hail-modal'));

// ── The archive, and filling it ──────────────────────────────────────────────
//
// The archive shipped EMPTY and nothing said so: every address lookup fell
// through to the NOAA spotter reports and answered "no hail" about roofs that
// had been hit. A real 1.91" storm sat over Loveland on 2024-07-21 and the tool
// could not see it. So the coverage is on screen whether or not it is good
// news, and a manager can fix it without a shell.

let backfillPoll = null;

function prettyCoverage(a) {
  if (!a || !a.days_held) {
    return 'Radar archive is EMPTY — hail lookups are falling back to NOAA ' +
           'spotter call-ins, which say nothing about a specific roof.';
  }
  return `Radar archive: ${a.days_held.toLocaleString()} day${a.days_held === 1 ? '' : 's'}, ` +
         `${prettyDate(a.first)} → ${prettyDate(a.last)}. ${a.unverified_days || 0} older day(s) need verification; Fill this season will repair them.`;
}

// Seasons the archive can hold. MRMS on AWS starts 2020-10-14, so anything
// earlier is not a gap somebody can fill — offering it would be a button that
// always fails.
const ARCHIVE_FIRST_YEAR = 2020;

function seasonOptions() {
  const now = new Date().getFullYear();
  const out = [];
  for (let y = now; y >= ARCHIVE_FIRST_YEAR; y--) out.push(y);
  return out;
}

async function loadArchiveState() {
  const box = $('hail-archive-coverage');
  let st;
  try {
    st = await api('/api/hail/backfill');
  } catch (e) {
    // A rep gets 403 here, which is not an error worth showing them — the
    // coverage line is the manager's tool. Fall back to what the picker knows.
    box.textContent = archiveStorms.length
      ? `Radar archive holds ${archiveStorms.length} storm day(s).`
      : 'Radar archive is empty for this period.';
    return;
  }
  show('hail-archive-admin');
  const sel = $('hail-backfill-season');
  if (!sel.options.length) {
    sel.innerHTML = seasonOptions().map(y => `<option value="${y}">${y} season</option>`).join('');
  }
  renderBackfill(st);
}

function renderBackfill(st) {
  $('hail-archive-coverage').textContent = prettyCoverage(st.archive);
  const job = st.job;
  const out = $('hail-backfill-status');
  if (!job) { out.textContent = ''; return; }
  if (job.status === 'running') {
    const pct = job.total ? Math.round(job.done / job.total * 100) : 0;
    out.textContent = `Filling ${job.label} — ${job.done}/${job.total} days (${pct}%), ` +
                      `${job.storm_days} with hail.`;
    $('hail-backfill-btn').disabled = true;
    if (!backfillPoll) backfillPoll = setInterval(pollBackfill, 2000);
    return;
  }
  $('hail-backfill-btn').disabled = false;
  if (backfillPoll) { clearInterval(backfillPoll); backfillPoll = null; }
  out.textContent = job.note ? `${job.label}: ${job.note}` : '';
}

async function pollBackfill() {
  try {
    const st = await api('/api/hail/backfill');
    renderBackfill(st);
    // A finished run means new storm days, so the picker beside it is stale.
    if ((st.job || {}).status !== 'running') await loadStormPicker();
  } catch (e) {
    clearInterval(backfillPoll); backfillPoll = null;
  }
}

$('hail-backfill-btn').addEventListener('click', async () => {
  const year = $('hail-backfill-season').value;
  const out = $('hail-backfill-status');
  $('hail-backfill-btn').disabled = true;
  out.textContent = 'Starting…';
  try {
    const r = await api(`/api/hail/backfill?season=${encodeURIComponent(year)}`, 'POST');
    if (r.status === 'nothing_to_do') {
      out.textContent = `${year} is already in the archive.`;
      $('hail-backfill-btn').disabled = false;
      renderBackfill(r);
      return;
    }
    // Minutes, not seconds — say so, or it reads as hung.
    out.textContent = `Filling ${year}: ${r.days} day(s) to fetch` +
      (r.skipped_already_held ? `, ${r.skipped_already_held} already held` : '') +
      '. This takes a few minutes; you can close this.';
    if (!backfillPoll) backfillPoll = setInterval(pollBackfill, 2000);
  } catch (e) {
    out.textContent = e.message || 'Could not start the backfill.';
    $('hail-backfill-btn').disabled = false;
  }
});


// The archive's own storm days, so a rep picks a storm instead of guessing a
// date. Without this the only way to find a storm is to already know when it
// was — which is the thing the tool is supposed to tell them.
async function loadStormPicker() {
  const sel = $('hail-storm-picker');
  const group = $('hail-storm-picker-group');
  sel.innerHTML = '<option value="">Loading…</option>';
  let data;
  try {
    data = await api('/api/hail/storms');
  } catch(e) {
    data = { storms: [] };
  }
  archiveStorms = data.storms || [];
  if (!archiveStorms.length) {
    // No archive for this period: the date boxes still work and fall through
    // to the NOAA spotter reports, which is what this screen did before.
    group.classList.add('hidden');
    $('hail-modal-hint').textContent =
      'No radar archive loaded yet, so this falls back to NOAA spotter ' +
      'reports — call-ins near a place, not measurements of it.';
    return;
  }
  group.classList.remove('hidden');
  sel.innerHTML = '<option value="">— pick a storm, or use the dates below —</option>'
    + archiveStorms.map(st =>
        `<option value="${escHtml(st.event_date)}">${escHtml(prettyDate(st.event_date))}` +
        ` — up to ${st.max_size_in.toFixed(2)}"</option>`).join('');
  sel.onchange = () => {
    if (!sel.value) return;
    $('hail-date').value = sel.value;
    $('hail-date-end').value = sel.value;
  };
}

$('load-hail-btn').addEventListener('click', async () => {
  const start = $v('hail-date');
  const end = $v('hail-date-end') || start;
  if (!start) { $('hail-status').textContent = 'Pick a date first.'; return; }
  activeHailQuery = {start: start < end ? start : end, end:start < end ? end : start, min:$v('hail-min-size')};
  await refreshHailOverlay();
});

function hailCoverageText(cov) {
  if (!cov) return 'Coverage has not been verified.';
  if (cov.outside_area) return 'Outside the Colorado radar archive. No conclusion about hail is available here.';
  return `${cov.days_verified || 0} ${cov.point_checked ? 'radar-covered days at this point' : 'decoded archive days (coverage varies by cell)'} of ${cov.days_requested || 0}; ` +
    `${cov.days_missing || 0} day(s) missing or unchecked. Archive threshold: ${cov.threshold_in || 1} inches.`;
}

async function refreshHailOverlay() {
  if (!activeHailQuery) return;
  const query = {...activeHailQuery}, requestId = ++hailRequestId;
  $('hail-status').textContent = 'Reading radar history…';
  try {
    const b = map.getBounds();
    const box = `south=${b.getSouth()}&west=${b.getWest()}&north=${b.getNorth()}&east=${b.getEast()}`;
    const data = await api(`/api/hail/cells?start=${query.start}&end=${query.end}&${box}&min_size=${query.min || 0}`);
    if (requestId !== hailRequestId) return;
    if (data.coverage?.outside_area) {
      clearHailLayer();
      $('hail-status').textContent = hailCoverageText(data.coverage);
    } else if (data.count) {
      drawHailCells(data, query.start, query.end);
    } else if (data.days_held) {
      clearHailLayer();
      $('hail-status').textContent = `No archived radar cells meet this filter in the area on screen. ` + hailCoverageText(data.coverage);
    } else {
      await loadSpcReports(query.start, query.end, requestId);
    }
    if (requestId === hailRequestId && $('hail-map-status')) {
      $('hail-map-status').textContent = $('hail-status').textContent;
      show('hail-map-status');
    }
  } catch(e) {
    if (requestId !== hailRequestId) return;
    clearHailLayer();
    $('hail-status').textContent = 'Radar layer unavailable: ' + e.message;
    if ($('hail-map-status')) { $('hail-map-status').textContent = $('hail-status').textContent; show('hail-map-status'); }
  }
}

function drawHailCells(data, startVal, endVal) {
  clearHailLayer();
  hailLayer = L.layerGroup();
  data.cells.forEach(c => {
    const color = hailColor(c.size);
    // A rectangle at the cell's real extent. The old overlay drew
    // max(500, size * 800)-metre circles around spotter points — a damage
    // footprint that exists nowhere in the data. These claim nothing beyond
    // the ground the radar estimated over.
    L.rectangle([[c.s, c.w], [c.n, c.e]], {
      color, fillColor: color, fillOpacity: .45, weight: 0, stroke: false,
    }).bindPopup(`<b>${c.size}" hail</b><br>Radar estimate in this cell, not verified roof damage.<br>${escHtml(c.note || '')}`)
      .addTo(hailLayer);
  });
  hailLayer.addTo(map);
  const span = startVal === endVal ? prettyDate(startVal)
             : `${prettyDate(startVal)} – ${prettyDate(endVal)}`;
  // Truncation is said out loud rather than left as a quietly thinner map,
  // which is the same failure the pin list already refuses to have.
  const cut = data.truncated
    ? ` Showing the ${data.count} largest on screen — zoom in for the rest.`
    : '';
  $('hail-status').textContent =
    `✓ ${span} · up to ${Number(data.max_size).toFixed(2)}" · ${data.count} radar cells.${cut} ` +
    hailCoverageText(data.coverage) + ' Rolling 24-hour windows ending 23:30 UTC.';
}

// The old behaviour, kept: NOAA spotter reports, drawn as the circles they
// have always been drawn as, and labelled as call-ins rather than
// measurements so the two views cannot be confused for each other.
async function loadSpcReports(startVal, endVal, requestId = hailRequestId) {
  const url = (endVal && endVal !== startVal)
    ? `/api/hail/range?start=${startVal.replace(/-/g,'')}&end=${endVal.replace(/-/g,'')}`
    : `/api/hail?date=${startVal.replace(/-/g,'')}`;
  const data = await api(url);
  if (requestId !== hailRequestId) return;
  clearHailLayer();
  if (data.partial) {
    $('hail-status').textContent = 'Some NOAA report dates could not be retrieved; coverage is incomplete.';
    if (!data.features?.length) return;
  }
  if (!data.features || !data.features.length) {
    $('hail-status').textContent =
      'No radar archive for those dates, and no NOAA spotter reports either.';
    return;
  }
  hailLayer = L.layerGroup();
  data.features.forEach(f => {
    const [lon, lat] = f.geometry.coordinates;
    const size = f.properties.size || 0.5;
    const color = hailColor(size);
    L.circle([lat, lon], {
      radius: Math.max(500, size * 800), color, fillColor: color,
      fillOpacity: .35, weight: 1,
    }).bindPopup(`<b>${size}" hail</b><br>${escHtml(f.properties.location)}, ` +
                 `${escHtml(f.properties.state)}<br>${escHtml(f.properties.time)} UTC` +
                 `<br><i>Spotter report — a call-in near here, not a measurement ` +
                 `of this spot.</i>`).addTo(hailLayer);
  });
  hailLayer.addTo(map);
  $('hail-status').textContent =
    `No radar archive for those dates. Showing ${data.features.length} NOAA ` +
    `spotter reports instead — call-ins near a place, not measurements of it.`;
}

$('clear-hail-btn').addEventListener('click', () => {
  activeHailQuery = null; hailRequestId++; clearTimeout(hailMoveTimer);
  clearHailLayer();
  hide('hail-map-status');
  $('hail-status').textContent = 'Hail overlay cleared.';
});

function clearHailLayer() {
  if (hailLayer) { map.removeLayer(hailLayer); hailLayer = null; }
}

function hailColor(sizeInches) {
  if (sizeInches >= 2.0) return '#DC2626';   // 2"+ — severe
  if (sizeInches >= 1.5) return '#EA580C';   // 1.5" — significant
  if (sizeInches >= 1.0) return '#D97706';   // 1" — quarter
  if (sizeInches >= 0.75) return '#CA8A04';  // 0.75" — penny
  return '#65A30D';                           // small
}

// ── Hail by Address ────────────────────────────────────────────────────────

$('hail-address-btn').addEventListener('click', () => {
  hide('side-menu');
  $('hail-address-status').textContent = '';
  $('hail-address-results').innerHTML = '';
  show('hail-address-modal');
});
$('close-hail-address-modal').addEventListener('click', () => hide('hail-address-modal'));

$('hail-address-search-btn').addEventListener('click', async () => {
  const q = $v('hail-address-input');
  if (!q) { $('hail-address-status').textContent = 'Enter an address first.'; return; }
  const days   = $v('hail-address-days');
  const radius = $v('hail-address-radius');
  $('hail-address-status').textContent = 'Checking radar hail history...';
  $('hail-address-results').innerHTML = '';
  try {
    const data = await api(`/api/hail/address?q=${encodeURIComponent(q)}&days=${days}&radius=${radius}`);
    renderHailAddressResults(data);
  } catch(e) {
    $('hail-address-status').textContent = 'Search failed: ' + e.message;
  }
});

function escHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function prettyDate(iso) {
  const [y, m, d] = String(iso).split('-').map(Number);
  if (!y || !m || !d) return iso;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' });
}

// Two data products reach this renderer and they support very different
// sentences, so it branches on data.source rather than on the shape of the
// rows. MESH is the radar estimate over THIS roof; SPC is somebody's call-in
// some miles away. Rendering the second one in the first one's language is how
// a rep ends up telling a homeowner their house took 1.75" when what actually
// happened is that a spotter four miles off phoned something in.
function renderHailAddressResults(data) {
  if (data.source === 'mrms_mesh') return renderMeshHistory(data);
  return renderSpcReports(data);
}

function renderMeshHistory(data) {
  const st    = $('hail-address-status');
  const where = escHtml(data.resolved || data.query || 'this location');
  const cov   = data.coverage || {};
  const covLine = cov.days_held
    ? `${cov.days_held} radar day${cov.days_held > 1 ? 's' : ''} on file, ${prettyDate(cov.first)} to ${prettyDate(cov.last)}`
    : '';

  if (!data.storm_count) {
    const thin = cov.status !== 'complete';
    st.textContent = '';
    const headline = cov.outside_area ? 'Outside radar archive' :
      (thin ? 'Not enough radar history' : `No archived hail ≥${cov.threshold_in || 1} inches`);
    $('hail-address-results').innerHTML = `
      <div class="hail-summary"><div class="hail-summary-big is-thin">${escHtml(headline)}</div>
      <div class="hail-summary-sub">${where}</div></div>
      <div class="hail-coverage">${escHtml(hailCoverageText(cov))}
      An empty result does not establish that this roof was never hit or damaged.
      ${escHtml(cov.window_note || '')}</div>`;
    return;
  }

  st.textContent = '';
  // The headline is the worst hail this roof ever took, and the date beside it
  // has to be that storm's date rather than the newest one, or the big number
  // and the line under it describe two different days.
  const worst = data.storms.find(s => s.size === data.max_size) || data.storms[0];
  // A number a rep will read to a homeowner. MESH runs high and the archive
  // holds cells above the largest hailstone ever recorded, so the caveat
  // travels with the figure rather than living in a doc nobody opens.
  const note = data.max_note
    ? `<div class="hail-caveat ${data.max_caveat === 'impossible' ? 'is-impossible' : ''}">${escHtml(data.max_note)}</div>`
    : '';
  $('hail-address-results').innerHTML = `
    <div class="hail-summary">
      <div class="hail-summary-big is-headline" style="color:${hailColorHex(data.max_size)}">${data.max_size}"</div>
      <div class="hail-summary-sub">
        radar-estimated over this roof &middot; ${escHtml(prettyDate(worst.date))}<br>${where}
      </div>
    </div>
    ${note}
    <div class="hail-day-list">
      ${data.storms.map(s => `
        <div class="hail-report-row">
          <span class="hail-size-chip" style="background:${hailColorHex(s.size)}">${s.size}"</span>
          <span class="hail-report-loc">${escHtml(prettyDate(s.date))}</span>
        </div>`).join('')}
    </div>
    <div class="hail-coverage">${escHtml(covLine)}. ${escHtml(hailCoverageText(cov))}<br>${escHtml(cov.window_note || '')} Radar estimates describe the surrounding cell, not verified roof damage.</div>
    <button class="btn-secondary" id="hail-show-on-map-btn">Show on Map</button>
  `;

  $('hail-show-on-map-btn').addEventListener('click', () => {
    hide('hail-address-modal');
    if (hailResultLayer) map.removeLayer(hailResultLayer);
    hailResultLayer = L.layerGroup();
    // A marker on the address and nothing else. The old SPC view drew
    // max(400, size * 800)-metre circles around each report, a damage
    // footprint that exists nowhere in the data; MESH knows one cell, and
    // drawing anything wider than the address would be inventing the rest.
    L.marker([data.lat, data.lng]).bindPopup(
      `<b>${data.max_size}" hail</b><br>${escHtml(where)}<br>` +
      data.storms.map(s => `${escHtml(prettyDate(s.date))} — ${s.size}"`).join('<br>')
    ).addTo(hailResultLayer);
    hailResultLayer.addTo(map);
    map.setView([data.lat, data.lng], 16);
  });
}

function renderSpcReports(data) {
  const st = $('hail-address-status');
  // The archive had nothing for this window, so these are call-ins near the
  // address rather than the roof itself. Say so, in the result, every time.
  const fallbackNote = `
    <div class="hail-coverage">No radar archive for this period — these are
    NOAA spotter reports near the address, not measurements of this roof.</div>`;

  if (!data.report_count) {
    st.textContent = data.partial ? `NOAA reports are incomplete: ${data.days_failed} dates failed. No reliable negative result is available.` : `No reports found within ${data.radius_miles} mi on ${data.days_scanned} checked days. This does not mean no hail occurred.`;
    $('hail-address-results').innerHTML = fallbackNote;
    return;
  }
  st.textContent = '';
  const byDate = {};
  data.reports.forEach(r => { (byDate[r.date] = byDate[r.date] || []).push(r); });
  const dates = Object.keys(byDate).sort().reverse();

  $('hail-address-results').innerHTML = `
    <div class="hail-summary">
      <div class="hail-summary-big">${data.report_count} report${data.report_count>1?'s':''} &middot; max ${data.max_size}"</div>
      <div class="hail-summary-sub">${dates.length} storm day${dates.length>1?'s':''} within ${data.radius_miles} mi of<br>${escHtml(data.resolved || data.query)}</div>
    </div>
    ${fallbackNote}
    ${dates.map(d => {
      const rows = byDate[d].sort((a,b) => a.distance_miles - b.distance_miles);
      const max = Math.max(...rows.map(r => r.size));
      return `
        <div class="hail-day">
          <div class="hail-day-header">
            <span>${escHtml(d)}</span>
            <span class="hail-day-max" style="color:${hailColorHex(max)}">${max}" max</span>
          </div>
          ${rows.slice(0,5).map(r => `
            <div class="hail-report-row">
              <span class="hail-size-chip" style="background:${hailColorHex(r.size)}">${r.size}"</span>
              <span class="hail-report-loc">${escHtml(r.location)}, ${escHtml(r.state)}</span>
              <span class="hail-report-dist">${r.distance_miles} mi</span>
            </div>`).join('')}
        </div>`;
    }).join('')}
    <button class="btn-secondary" id="hail-show-on-map-btn">Show on Map</button>
  `;

  $('hail-show-on-map-btn').addEventListener('click', () => {
    hide('hail-address-modal');
    if (hailResultLayer) map.removeLayer(hailResultLayer);
    hailResultLayer = L.layerGroup();
    L.marker([data.lat, data.lng]).bindPopup(`<b>${escHtml(data.query)}</b>`).addTo(hailResultLayer);
    data.reports.forEach(r => {
      L.circle([r.lat, r.lng], {
        radius: Math.max(400, r.size * 800),
        color: hailColorHex(r.size), fillColor: hailColorHex(r.size),
        fillOpacity: .3, weight: 1,
      }).bindPopup(`<b>${r.size}" hail</b><br>${escHtml(r.date)}<br>${escHtml(r.location)}, ${escHtml(r.state)} (${r.distance_miles} mi away)`)
        .addTo(hailResultLayer);
    });
    hailResultLayer.addTo(map);
    map.setView([data.lat, data.lng], 12);
  });
}

function hailColorHex(size) { return hailColor(size); }

// ── Manage Team (admin) ────────────────────────────────────────────────────

$('team-admin-btn').addEventListener('click', () => {
  hide('side-menu');
  hide('invite-result');
  $('invite-username').value = '';
  show('team-admin-panel');
  refreshInvites();
  refreshTeamUsers();
});

$('create-invite-btn').addEventListener('click', async () => {
  try {
    const inv = await portalApi('/api/invites', 'POST', { username: $v('invite-username') });
    $('invite-link-text').textContent = inv.link;
    show('invite-result');
    refreshInvites();
  } catch(e) {
    alert('Failed to create invite: ' + e.message);
  }
});

$('copy-invite-btn').addEventListener('click', async () => {
  const link = $('invite-link-text').textContent;
  try {
    await navigator.clipboard.writeText(link);
    $('copy-invite-btn').textContent = '✓ Copied!';
  } catch(e) {
    // Clipboard API unavailable (http / old browser) — fall back to select
    prompt('Copy this link:', link);
  }
  setTimeout(() => { $('copy-invite-btn').textContent = '📋 Copy Link'; }, 2000);
});

async function refreshInvites() {
  try {
    const invites = await portalApi('/api/invites');
    const pending = invites.filter(i => i.status === 'active');
    $('invite-list').innerHTML = pending.length ? pending.map(i => `
      <div class="team-row">
        <div class="team-row-info">
          <div class="team-row-name">${i.username ? displayName(i.username) : 'Open invite'}</div>
          <div class="team-row-sub">expires ${i.expires_at.split('T')[0]}</div>
        </div>
        <button class="mini-btn" onclick="copyInviteLink('${i.link}')">Copy</button>
        <button class="mini-btn danger" onclick="revokeInvite('${i.code}')">Revoke</button>
      </div>`).join('')
      : '<div class="team-empty">No pending invites.</div>';
  } catch(e) {
    $('invite-list').innerHTML = '<div class="team-empty">Failed to load.</div>';
  }
}

async function copyInviteLink(link) {
  try { await navigator.clipboard.writeText(link); alert('Link copied!'); }
  catch(e) { prompt('Copy this link:', link); }
}

async function revokeInvite(code) {
  if (!confirm('Revoke this invite?')) return;
  await portalApi(`/api/invites/${code}`, 'DELETE', null);
  refreshInvites();
}

async function refreshTeamUsers() {
  try {
    const users = await api('/api/users');
    $('team-user-list').innerHTML = users.map(u => {
      const isMe = u.username === currentUser.username;
      return `
      <div class="team-row">
        <div class="team-row-info">
          <div class="team-row-name">${displayName(u.username)}${u.is_admin ? ' <span class="admin-tag">admin</span>' : ''}${isMe ? ' (you)' : ''}</div>
          <div class="team-row-sub">joined ${(u.created_at || '').split('T')[0]}</div>
        </div>
        ${isMe ? '' : `
          <button class="mini-btn" onclick="resetUserPw('${u.username}')">Reset PW</button>
          <button class="mini-btn" onclick="toggleAdmin('${u.username}', ${u.is_admin ? 'false' : 'true'})">${u.is_admin ? 'Demote' : 'Admin'}</button>
          <button class="mini-btn danger" onclick="removeUser('${u.username}')">✕</button>`}
      </div>`;
    }).join('');
  } catch(e) {
    $('team-user-list').innerHTML = '<div class="team-empty">Failed to load.</div>';
  }
}

async function resetUserPw(username) {
  const pw = prompt(`New password for ${displayName(username)} (8+ chars):`);
  if (!pw) return;
  try {
    await portalApi(`/api/users/${username}/password`, 'POST', { password: pw });
    alert('Password reset.');
  } catch(e) { alert('Failed: ' + e.message); }
}

async function toggleAdmin(username, makeAdmin) {
  try {
    await portalApi(`/api/users/${username}/role`, 'POST', { role: makeAdmin ? 'admin' : 'rep' });
    refreshTeamUsers();
  } catch(e) { alert('Failed: ' + e.message); }
}

async function removeUser(username) {
  if (!confirm(`Remove ${displayName(username)}? Their pins stay on the map.`)) return;
  try {
    await portalApi(`/api/users/${username}`, 'DELETE', null);
    refreshTeamUsers();
  } catch(e) { alert('Failed: ' + e.message); }
}

// ── Menu & overlays ────────────────────────────────────────────────────────

$('menu-btn').addEventListener('click', () => {
  // Refresh rep filters when opening menu
  updateRepFilters();
  show('side-menu');
});

// The portal owns sign-out (`/logout` in portal/app.py) — this app has had no
// /api/logout since the three tools merged onto one cookie, and no login screen
// to fall back to. Stop the GPS timers first so the last thing this tab does on
// the way out is not a location push.
$('logout-btn').addEventListener('click', () => {
  stopTeamTracking();
  try { localStorage.removeItem(OFFLINE_SESSION); } catch(e) {}
  window.location = '/logout';
});

// Close overlays
document.querySelectorAll('.close-overlay').forEach(btn => {
  btn.addEventListener('click', () => hide(btn.dataset.target));
});

// Close bottom sheet / modals on backdrop click
document.querySelectorAll('.modal').forEach(modal => {
  modal.addEventListener('click', (e) => {
    if (e.target === modal) hide(modal.id);
  });
});

// ── Type selector builder ──────────────────────────────────────────────────

// Very dark pin colors (e.g. No Soliciting) are unreadable as text on the dark UI
function uiColor(hex) {
  const n = parseInt(hex.slice(1), 16);
  const lum = (n >> 16 & 255) * .299 + (n >> 8 & 255) * .587 + (n & 255) * .114;
  return lum < 60 ? '#94A3B8' : hex;
}

function buildTypeSelector(containerId, onSelect) {
  const container = $(containerId);
  container.innerHTML = '';
  Object.entries(PIN_TYPES).forEach(([type, meta]) => {
    const btn = document.createElement('button');
    btn.className = 'type-btn';
    btn.dataset.type = type;
    btn.style.color = uiColor(meta.color);
    btn.innerHTML = `<span class="type-dot" style="background:${meta.color}"></span>${meta.label}`;
    btn.addEventListener('click', () => {
      container.querySelectorAll('.type-btn').forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
      selectedPinType = type;
      if (onSelect) onSelect(type);
      // Show/hide contact fields in drop modal
      if (containerId === 'type-selector') updateContactFieldsVisibility();
    });
    container.appendChild(btn);
  });
}

function setSelectedType(containerId, type) {
  const container = $(containerId);
  container.querySelectorAll('.type-btn').forEach(btn => {
    btn.classList.toggle('selected', btn.dataset.type === type);
  });
}

// ── Helpers ────────────────────────────────────────────────────────────────

async function api(path, method='GET', body=null) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
  };
  if (body !== null) opts.body = JSON.stringify(body);
  const res = await fetch(BASE + path, opts);
  // Session expired or never signed in: the portal owns login, so hand off
  // rather than trying to parse an HTML redirect as JSON.
  if (res.status === 401) { window.location = '/login'; throw new Error('Unauthorized'); }
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);
  return json;
}

// Calls the PORTAL's API rather than this app's — note the missing BASE. Team
// administration (passwords, roles, invites) moved there when the three tools
// merged onto one user store, but the panel that drives it still lives here.
async function portalApi(path, method='GET', body=null) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
  };
  if (body !== null) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (res.status === 401) { window.location = '/login'; throw new Error('Unauthorized'); }
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);
  return json;
}

function $(id)    { return document.getElementById(id); }
function $v(id)   { return ($(id)?.value || '').trim(); }
function show(id) { $(id)?.classList.remove('hidden'); }
function hide(id) { $(id)?.classList.add('hidden'); }

function displayName(username) {
  if (!username) return '';
  return username.split(/[._]/).map(p => p.charAt(0).toUpperCase() + p.slice(1)).join(' ');
}

function timeAgo(isoStr) {
  const ms   = Date.now() - new Date(isoStr).getTime();
  const mins = Math.floor(ms / 60000);
  if (mins < 1)   return 'just now';
  if (mins < 60)  return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24)   return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

// ── Start ──────────────────────────────────────────────────────────────────

window.openEditPin    = openEditPin;
window.addToPipeline  = addToPipeline;
window.jumpToPin      = jumpToPin;
window.copyInviteLink = copyInviteLink;
window.revokeInvite   = revokeInvite;
window.resetUserPw    = resetUserPw;
window.toggleAdmin    = toggleAdmin;
window.removeUser     = removeUser;

boot();
