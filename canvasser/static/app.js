/* P1 Canvasser — main app logic */

const PIN_TYPES = {};  // populated from /api/config
let map, currentUser, markers = {}, hailLayer = null;
let pendingLatLng = null;   // where the next pin will land
let selectedPinType = 'not_home';
let editingPinId = null;
let activeFilters = new Set();  // empty = show all
let allPins = [];

// ── Bootstrap ──────────────────────────────────────────────────────────────

async function boot() {
  // Load pin type config
  try {
    const cfg = await api('/api/config');
    Object.assign(PIN_TYPES, cfg.pin_types);
  } catch(e) { /* use defaults */ }

  const me = await api('/api/me');
  if (me.authenticated) {
    currentUser = me;
    showApp();
  } else {
    showLogin();
  }
}

// ── Login ──────────────────────────────────────────────────────────────────

function showLogin() {
  show('login-screen'); hide('app');
}

$('login-btn').addEventListener('click', async () => {
  const username = $v('login-username');
  const password = $v('login-password');
  $('login-error').textContent = '';
  try {
    currentUser = await api('/api/login', 'POST', { username, password });
    showApp();
  } catch(e) {
    $('login-error').textContent = e.message || 'Login failed';
  }
});

$('signup-btn').addEventListener('click', async () => {
  const username  = $v('login-username');
  const password  = $v('signup-password');
  const signup_code = $v('signup-code');
  $('login-error').textContent = '';
  try {
    currentUser = await api('/api/signup', 'POST', { username, password, signup_code });
    showApp();
  } catch(e) {
    $('login-error').textContent = e.message || 'Signup failed';
  }
});

['login-username','login-password','signup-password'].forEach(id => {
  $(id).addEventListener('keydown', e => { if (e.key === 'Enter') $('login-btn').click(); });
});

// ── App init ───────────────────────────────────────────────────────────────

function showApp() {
  hide('login-screen'); show('app');
  $('rep-name-badge').textContent = `Rep: ${displayName(currentUser.username)}`;
  buildQuickBtns();
  buildRepFilters();
  initMap();
  loadPins();
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
  // Default to Fort Collins, CO (northern CO market)
  map = L.map('map', { zoomControl: false, attributionControl: false }).setView([40.5853, -105.0844], 14);

  // Satellite tile layer (ESRI World Imagery — free, no API key)
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 20,
    attribution: 'Tiles &copy; Esri'
  }).addTo(map);

  // Labels overlay (roads/addresses)
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 20, opacity: 0.8
  }).addTo(map);

  // Tap on empty map → drop pin
  map.on('click', (e) => {
    // Don't open modal if a marker was clicked
    if (e.originalEvent._markerClick) return;
    openDropPinModal(e.latlng, selectedPinType);
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
    allPins = await api('/api/pins?limit=2000');
    renderPins();
  } catch(e) { console.error('Failed to load pins', e); }
}

function renderPins() {
  // Clear existing markers
  Object.values(markers).forEach(m => map.removeLayer(m));
  markers = {};

  const filtered = activeFilters.size > 0
    ? allPins.filter(p => activeFilters.has(p.rep))
    : allPins;

  filtered.forEach(pin => addPinMarker(pin));
}

function addPinMarker(pin) {
  const meta  = PIN_TYPES[pin.pin_type] || { label: pin.pin_type, color: '#6B7280' };
  const initials = (pin.rep || '?').substring(0, 2).toUpperCase();

  const icon = L.divIcon({
    className: '',
    html: `<div class="pin-marker" style="background:${meta.color}" title="${meta.label} — ${displayName(pin.rep)}">${initials}</div>`,
    iconSize:   [28, 28],
    iconAnchor: [14, 14],
  });

  const marker = L.marker([pin.lat, pin.lng], { icon }).addTo(map);
  marker.on('click', (e) => {
    e.originalEvent._markerClick = true;
    showPinDetail(pin);
  });
  markers[pin.id] = marker;
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
  ['pin-address','pin-contact-name','pin-contact-phone','pin-contact-email','pin-notes'].forEach(id => {
    const el = $(id);
    if (el) el.value = '';
  });

  show('drop-pin-modal');
}

function updateContactFieldsVisibility() {
  const contactTypes = ['interested','appointment','inspected','closed'];
  $('contact-fields').style.display = contactTypes.includes(selectedPinType) ? 'block' : 'none';
}

$('close-drop-modal').addEventListener('click', () => hide('drop-pin-modal'));

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
  };

  try {
    const pin = await api('/api/pins', 'POST', payload);
    hide('drop-pin-modal');
    allPins.unshift(pin);
    addPinMarker(pin);
    // Flash the map to the pin
    map.panTo([pin.lat, pin.lng]);
  } catch(e) {
    alert('Failed to save pin: ' + e.message);
  }
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
  const isClosed = pin.pin_type === 'closed';
  const alreadySynced = !!pin.crm_contact_id;

  const rows = [
    ['Rep',    displayName(pin.rep)],
    pin.address       ? ['Address', pin.address] : null,
    pin.contact_name  ? ['Contact', pin.contact_name] : null,
    pin.contact_phone ? ['Phone',   `<a href="tel:${pin.contact_phone}" style="color:#10B981">${pin.contact_phone}</a>`] : null,
    pin.contact_email ? ['Email',   pin.contact_email] : null,
    pin.notes         ? ['Notes',   pin.notes] : null,
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
    html += `<button class="pin-action-btn" onclick="openEditPin('${pin.id}')">✏️ Edit</button>`;
  }
  if (isClosed && pin.contact_name && !alreadySynced && isOwner) {
    html += `<button class="pin-action-btn crm-btn" onclick="syncToCRM('${pin.id}')">📋 Add to CRM</button>`;
  }
  if (alreadySynced) {
    html += `<div style="font-size:12px;color:#10B981;padding:8px 0">✓ Synced to CRM</div>`;
  }
  html += `</div>`;

  $('pin-detail-body').innerHTML = html;
  show('pin-detail');
}

$('close-pin-detail').addEventListener('click', () => hide('pin-detail'));

// ── CRM Sync ───────────────────────────────────────────────────────────────

async function syncToCRM(pinId) {
  if (!confirm('Add this deal to the Base44 CRM? This will create a Contact and Project.')) return;
  try {
    const result = await api(`/api/crm/sync/${pinId}`, 'POST', {});
    // Update local pin
    const pin = allPins.find(p => p.id === pinId);
    if (pin) {
      pin.crm_contact_id = result.crm_contact_id;
      pin.crm_project_id = result.crm_project_id;
    }
    alert('✓ Added to CRM successfully!');
    hide('pin-detail');
  } catch(e) {
    alert('CRM sync failed: ' + e.message);
  }
}

// ── Edit pin ───────────────────────────────────────────────────────────────

async function openEditPin(pinId) {
  hide('pin-detail');
  editingPinId = pinId;
  const pin = allPins.find(p => p.id === pinId);
  if (!pin) return;

  buildTypeSelector('edit-type-selector', (type) => {});
  setSelectedType('edit-type-selector', pin.pin_type);

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
  };

  try {
    const updated = await api(`/api/pins/${editingPinId}`, 'PUT', payload);
    // Update local
    const idx = allPins.findIndex(p => p.id === editingPinId);
    if (idx >= 0) allPins[idx] = updated;
    // Re-render
    if (markers[editingPinId]) map.removeLayer(markers[editingPinId]);
    delete markers[editingPinId];
    addPinMarker(updated);
    hide('edit-pin-modal');
  } catch(e) {
    alert('Update failed: ' + e.message);
  }
});

$('delete-pin-btn').addEventListener('click', async () => {
  if (!confirm('Delete this pin?')) return;
  try {
    await api(`/api/pins/${editingPinId}`, 'DELETE', null);
    if (markers[editingPinId]) map.removeLayer(markers[editingPinId]);
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
    const rows = await api('/api/leaderboard');
    renderLeaderboard(rows);
  } catch(e) {
    $('leaderboard-body').innerHTML = '<div class="loading-msg">Failed to load.</div>';
  }
});

function renderLeaderboard(rows) {
  if (!rows.length) {
    $('leaderboard-body').innerHTML = '<div class="loading-msg">No data yet — start knocking!</div>';
    return;
  }
  const rankClass = (i) => i === 0 ? 'gold' : i === 1 ? 'silver' : i === 2 ? 'bronze' : '';
  $('leaderboard-body').innerHTML = rows.map((r, i) => `
    <div class="lb-row">
      <div class="lb-rank ${rankClass(i)}">${i < 3 ? ['🥇','🥈','🥉'][i] : i+1}</div>
      <div class="lb-rep">
        <div class="lb-rep-name">${displayName(r.rep)}</div>
        <div class="lb-stats">
          ${r.appointments} appts · ${r.inspections} inspections · ${r.closed} closed
        </div>
      </div>
      <div>
        <div class="lb-doors">${r.total_doors}</div>
        <div class="lb-doors-label">doors</div>
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
    const pins = await api(`/api/pins?rep=${currentUser.username}&limit=200`);
    renderMyPins(pins);
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

$('hail-overlay-btn').addEventListener('click', () => {
  hide('side-menu');
  // Default date to today
  const today = new Date().toISOString().split('T')[0];
  $('hail-date').value = today;
  $('hail-status').textContent = '';
  show('hail-modal');
});

$('close-hail-modal').addEventListener('click', () => hide('hail-modal'));

$('load-hail-btn').addEventListener('click', async () => {
  const dateVal = $v('hail-date');
  let dateParam = 'today';
  if (dateVal) {
    dateParam = dateVal.replace(/-/g, '');  // YYYYMMDD
  }
  $('hail-status').textContent = 'Loading NOAA hail data...';
  try {
    const data = await api(`/api/hail?date=${dateParam}`);
    clearHailLayer();
    if (!data.features || data.features.length === 0) {
      $('hail-status').textContent = 'No hail reports found for this date.';
      return;
    }
    hailLayer = L.layerGroup();
    data.features.forEach(f => {
      const [lon, lat] = f.geometry.coordinates;
      const size = f.properties.size || 0.5;
      const radius = Math.max(500, size * 800);  // meters
      const color  = hailColor(size);
      L.circle([lat, lon], {
        radius, color, fillColor: color, fillOpacity: .35, weight: 1,
      }).bindPopup(`
        <b>${size}" hail</b><br>
        ${f.properties.location}, ${f.properties.state}<br>
        ${f.properties.time} UTC
      `).addTo(hailLayer);
    });
    hailLayer.addTo(map);
    $('hail-status').textContent = `✓ ${data.features.length} hail reports loaded.`;
  } catch(e) {
    $('hail-status').textContent = 'Failed to load: ' + e.message;
  }
});

$('clear-hail-btn').addEventListener('click', () => {
  clearHailLayer();
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

// ── Menu & overlays ────────────────────────────────────────────────────────

$('menu-btn').addEventListener('click', () => {
  // Refresh rep filters when opening menu
  updateRepFilters();
  show('side-menu');
});

$('logout-btn').addEventListener('click', async () => {
  await api('/api/logout', 'POST', {});
  currentUser = null;
  allPins = [];
  Object.values(markers).forEach(m => map.removeLayer(m));
  markers = {};
  showLogin();
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

function buildTypeSelector(containerId, onSelect) {
  const container = $(containerId);
  container.innerHTML = '';
  Object.entries(PIN_TYPES).forEach(([type, meta]) => {
    const btn = document.createElement('button');
    btn.className = 'type-btn';
    btn.dataset.type = type;
    btn.style.color = meta.color;
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
  const res = await fetch(path, opts);
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

window.openEditPin = openEditPin;
window.syncToCRM   = syncToCRM;
window.jumpToPin   = jumpToPin;

boot();
