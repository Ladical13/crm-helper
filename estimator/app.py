import os
import json
import uuid
import shutil
import socket
import secrets
import hashlib
import html as _html
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory

try:
    import requests as http
except ImportError:
    http = None

app = Flask(__name__, static_folder='static')

BASE_URL = "https://base44.app/api/apps/69320ef0c647fee442697971"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJsdWtlQHByb2plY3RvbmVyb29maW5nLmNvbSIsImV4cCI6MTc4MTIwNzk5OSwiaWF0IjoxNzczNDMxOTk5fQ.ExSmR97vp50U-VaNgTRF3FawGffSjpsoznXcyfvRS2I"
CO_LOCATION_ID = "6984bb86d86d9c92d6827a17"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# DATA_DIR: where estimates, uploads, and config files live.
# Set DATA_DIR env var to a persistent volume path on Railway (e.g. /data).
# Falls back to BASE_DIR so local development works unchanged.
DATA_DIR = os.environ.get('DATA_DIR', BASE_DIR)

ESTIMATES_DIR = os.path.join(DATA_DIR, 'estimates')
UPLOADS_DIR   = os.path.join(DATA_DIR, 'uploads')
ALLOWED_EXT   = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif'}

PRICE_BOOK_FILE    = os.path.join(DATA_DIR, 'price_book.json')
TIER_DEFAULTS_FILE = os.path.join(DATA_DIR, 'tier_defaults.json')

# Optional override for the public-facing base URL (e.g. ngrok or a real domain).
# Set PUBLIC_URL in environment or in estimator/config.json as {"public_url": "https://..."}
def _load_public_url():
    env = os.environ.get('PUBLIC_URL', '').rstrip('/')
    if env:
        return env
    cfg = os.path.join(DATA_DIR, 'config.json')
    if os.path.exists(cfg):
        try:
            with open(cfg) as f:
                return json.load(f).get('public_url', '').rstrip('/')
        except Exception:
            pass
    return ''

def _get_lan_ip():
    """Return this machine's LAN IP (fallback: 127.0.0.1)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

PUBLIC_URL = _load_public_url()
LAN_IP     = _get_lan_ip()

os.makedirs(ESTIMATES_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

def _seed_data_dir():
    """On first run with a new DATA_DIR (e.g. Railway persistent volume),
    copy seed files from the app directory so defaults are available."""
    if DATA_DIR == BASE_DIR:
        return  # local dev — nothing to seed
    for fname in ('price_book.json', 'tier_defaults.json'):
        src = os.path.join(BASE_DIR, fname)
        dst = os.path.join(DATA_DIR, fname)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)

_seed_data_dir()

_contact_cache = None


def crm_headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def fetch_all_contacts():
    global _contact_cache
    if _contact_cache is not None:
        return _contact_cache
    if http is None:
        _contact_cache = []
        return _contact_cache
    try:
        r = http.get(f"{BASE_URL}/entities/Contact", headers=crm_headers(), timeout=15)
        r.raise_for_status()
        all_contacts = r.json()
        _contact_cache = [c for c in all_contacts if c.get('location_id') == CO_LOCATION_ID]
    except Exception as e:
        print(f"[CRM] fetch failed: {e}")
        _contact_cache = []
    return _contact_cache


# ── Static ─────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(UPLOADS_DIR, filename)


# ── Estimates CRUD ─────────────────────────────────────────────────────────

@app.route('/api/estimates', methods=['GET'])
def list_estimates():
    result = []
    try:
        files = sorted(os.listdir(ESTIMATES_DIR), reverse=True)
    except OSError:
        return jsonify([])
    for fname in files:
        if not fname.endswith('.json'):
            continue
        path = os.path.join(ESTIMATES_DIR, fname)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                d = json.load(f)
            result.append({
                'estimate_id':   d.get('estimate_id', ''),
                'customer_name': d.get('customer', {}).get('name', ''),
                'estimate_date': d.get('estimate_date', ''),
                'status':        d.get('status', 'draft'),
                'selected_tier': d.get('selected_tier', 'better'),
                'updated_at':    d.get('updated_at', ''),
            })
        except Exception:
            pass
    return jsonify(result)


@app.route('/api/estimates', methods=['POST'])
def create_estimate():
    data = request.get_json(force=True)
    est_id = data.get('estimate_id') or str(uuid.uuid4())
    data['estimate_id'] = est_id
    now = datetime.utcnow().isoformat() + 'Z'
    data.setdefault('created_at', now)
    data['updated_at'] = now
    path = os.path.join(ESTIMATES_DIR, f"{est_id}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    return jsonify({'estimate_id': est_id}), 201


@app.route('/api/estimates/<est_id>', methods=['GET'])
def get_estimate(est_id):
    path = os.path.join(ESTIMATES_DIR, f"{est_id}.json")
    if not os.path.exists(path):
        return jsonify({'error': 'Not found'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        return jsonify(json.load(f))


@app.route('/api/estimates/<est_id>', methods=['PUT'])
def save_estimate(est_id):
    data = request.get_json(force=True)
    data['estimate_id'] = est_id
    data['updated_at'] = datetime.utcnow().isoformat() + 'Z'
    path = os.path.join(ESTIMATES_DIR, f"{est_id}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    return jsonify({'estimate_id': est_id})


@app.route('/api/estimates/<est_id>', methods=['DELETE'])
def delete_estimate(est_id):
    path = os.path.join(ESTIMATES_DIR, f"{est_id}.json")
    if os.path.exists(path):
        os.remove(path)
    upload_dir = os.path.join(UPLOADS_DIR, est_id)
    if os.path.exists(upload_dir):
        shutil.rmtree(upload_dir)
    return jsonify({'ok': True})


# ── Photo uploads ──────────────────────────────────────────────────────────

@app.route('/api/uploads/<est_id>', methods=['POST'])
def upload_photo(est_id):
    if 'file' not in request.files:
        return jsonify({'error': 'No file field'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Empty filename'}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return jsonify({'error': 'File type not allowed'}), 400
    dest_dir = os.path.join(UPLOADS_DIR, est_id)
    os.makedirs(dest_dir, exist_ok=True)
    safe_name = str(uuid.uuid4()) + ext
    f.save(os.path.join(dest_dir, safe_name))
    return jsonify({'filename': f"{est_id}/{safe_name}", 'url': f"/uploads/{est_id}/{safe_name}"}), 201


@app.route('/api/uploads/<est_id>/<filename>', methods=['DELETE'])
def delete_photo(est_id, filename):
    path = os.path.join(UPLOADS_DIR, est_id, filename)
    if os.path.exists(path):
        os.remove(path)
    return jsonify({'ok': True})


# ── CRM proxy ──────────────────────────────────────────────────────────────

@app.route('/api/crm/contacts')
def search_contacts():
    q = request.args.get('q', '').lower().strip()
    contacts = fetch_all_contacts()
    if q:
        contacts = [c for c in contacts if
                    q in (c.get('name') or '').lower() or
                    q in (c.get('phone') or '').lower() or
                    q in (c.get('email') or '').lower()]
    slim = [{
        'id':             c.get('id'),
        'name':           c.get('name', ''),
        'phone':          c.get('phone', ''),
        'email':          c.get('email', ''),
        'street_address': c.get('street_address', ''),
        'city':           c.get('city', ''),
        'state':          c.get('state', ''),
        'zip_code':       c.get('zip_code', ''),
    } for c in contacts[:25]]
    return jsonify(slim)


@app.route('/api/crm/contacts/<contact_id>')
def get_contact(contact_id):
    for c in fetch_all_contacts():
        if c.get('id') == contact_id:
            return jsonify(c)
    return jsonify({'error': 'Not found'}), 404


# ── E-Signature helpers ────────────────────────────────────────────────────

def he(s):
    """HTML-escape a value."""
    return _html.escape(str(s)) if s is not None else ''

def fc(n):
    """Format as currency."""
    try:
        return f'${float(n):,.2f}'
    except Exception:
        return '$0.00'

def find_by_token(token):
    """Return (est_dict, filepath) for the estimate matching share_token, or None."""
    if not token:
        return None
    for fname in os.listdir(ESTIMATES_DIR):
        if not fname.endswith('.json'):
            continue
        path = os.path.join(ESTIMATES_DIR, fname)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                est = json.load(f)
            if est.get('share_token') == token:
                return est, path
        except Exception:
            pass
    return None

def calc_tier_total(est, tier):
    """Compute grand sell total for a given tier (excludes insurance)."""
    pricing = est.get('pricing', {})
    mode    = pricing.get('mode', 'margin')
    grate   = float(pricing.get('global_rate') or 35)
    ovr     = pricing.get('per_trade_overrides', {})
    trades  = est.get('trades', {})
    total   = 0.0

    def rate(tk):
        v = ovr.get(tk)
        return float(v) if v is not None else grate

    def sell(cost, r):
        return cost / (1 - r / 100) if mode == 'margin' and r < 100 else cost * (1 + r / 100)

    for tk in ['roofing', 'siding', 'windows', 'gutters', 'other']:
        td = trades.get(tk, {})
        if not td.get('enabled'):
            continue
        trade_mode = td.get('mode', 'simple' if tk == 'gutters' else 'gbb')
        r = rate(tk)
        for item in td.get('line_items', []):
            qty = float(item.get('quantity') or 0)
            if trade_mode == 'simple':
                sp = float(item.get('unit_price') or 0)
            else:
                t    = (item.get('tiers') or {}).get(tier, {})
                cost = float(t.get('material_unit_cost') or 0) + float(t.get('labor_unit_cost') or 0)
                sp   = sell(cost, r)
            total += sp * qty
    return total


def render_line_items(est, tier=None):
    """Build trade line-item tables for customer view. Returns (html, grand_total)."""
    if tier is None:
        tier = est.get('selected_tier', 'better')
    pricing  = est.get('pricing', {})
    mode     = pricing.get('mode', 'margin')
    grate    = float(pricing.get('global_rate') or 35)
    ovr      = pricing.get('per_trade_overrides', {})

    def rate(trade):
        v = ovr.get(trade)
        return float(v) if v is not None else grate

    def sell(cost, r):
        return cost / (1 - r / 100) if mode == 'margin' and r < 100 else cost * (1 + r / 100)

    labels  = dict(roofing='Roofing', siding='Siding', windows='Windows', gutters='Gutters', other='Other / Misc')
    trades  = est.get('trades', {})
    parts   = []
    gtotal  = 0.0

    for tk in ['roofing', 'siding', 'windows', 'gutters', 'other']:
        td = trades.get(tk, {})
        if not td.get('enabled') or not td.get('line_items'):
            continue
        # Determine trade mode: gutters always simple; others default gbb
        trade_mode = td.get('mode', 'simple' if tk == 'gutters' else 'gbb')
        r    = rate(tk)
        rows = []
        sub  = 0.0
        hidden_count = 0
        for item in td['line_items']:
            qty  = float(item.get('quantity') or 0)
            if trade_mode == 'simple':
                sp   = float(item.get('unit_price') or 0)
                desc = (item.get('description') or '').strip()
            else:
                t    = (item.get('tiers') or {}).get(tier, {})
                cost = float(t.get('material_unit_cost') or 0) + float(t.get('labor_unit_cost') or 0)
                sp   = sell(cost, r)
                desc = t.get('description', '')
            line  = sp * qty
            sub  += line
            if not item.get('customer_visible', True):
                hidden_count += 1
                continue
            rows.append(f'''<tr>
              <td class="cvn">{he(item.get("name",""))}
                {'<div class="cvd">'+he(desc)+'</div>' if desc else ''}</td>
              <td class="cvc">{qty:g}</td>
              <td class="cvc">{he(item.get("unit",""))}</td>
              <td class="cvr">{fc(sp)}</td>
              <td class="cvr">{fc(line)}</td></tr>''')
        if hidden_count:
            rows.append(f'<tr><td colspan="5" class="cvhidden-note">Additional materials &amp; supplies included in total</td></tr>')
        gtotal += sub
        lbl = labels.get(tk, tk.title())
        parts.append(f'''<div class="cvtrade">
          <div class="cvtrade-hd">{lbl}</div>
          <table class="cvt"><thead><tr>
            <th>Description</th><th class="cvth-c">Qty</th>
            <th class="cvth-c">Unit</th><th class="cvth-r">Unit Price</th>
            <th class="cvth-r">Total</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
          <tfoot><tr><td colspan="4" class="cvsub-l">{lbl} Subtotal</td>
            <td class="cvr cvsub">{fc(sub)}</td></tr></tfoot>
          </table></div>''')

    return '\n'.join(parts), gtotal


_CV_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;font-size:14px;color:#1f2937;background:#f3f4f6}
.cvhdr{background:#fff;padding:16px 22px;display:flex;align-items:center;justify-content:space-between;gap:14px;box-shadow:0 2px 10px rgba(0,0,0,.08)}
.cvhdr-logo-wrap{display:inline-flex;align-items:center}
.cvhdr img{height:56px;width:auto;display:block}
.cvhdr-contact{text-align:right;line-height:1.45}
.cvhdr-contact a{color:#1a3a5c;font-weight:800;font-size:16px;text-decoration:none;display:block}
.cvhdr-contact span{color:#6b7280;font-size:11px}
.cvbrand-stripe{height:6px;background:linear-gradient(90deg,#22c7da 0 33.3%,#ffd400 33.3% 66.6%,#ee3d42 66.6% 100%)}
.cvhero{background:linear-gradient(135deg,#1a3a5c,#0e2440);color:#fff;padding:30px 20px;text-align:center;position:relative}
.cvhero-brand{font-size:12px;font-weight:800;letter-spacing:2.5px;text-transform:uppercase;color:#22c7da;margin-bottom:9px}
.cvhero h1{font-size:22px;font-weight:800;margin-bottom:5px}
.cvhero p{font-size:13px;opacity:.85}
.cvhero.ok{background:linear-gradient(135deg,#16a34a,#14532d)}
.cv-check{font-size:52px;line-height:1;margin-bottom:8px}
.cv-print-btn{margin-top:14px;background:rgba(255,255,255,.2);border:2px solid rgba(255,255,255,.5);
  color:#fff;padding:10px 22px;border-radius:6px;font-size:14px;font-weight:700;cursor:pointer}
.cv-print-btn:hover{background:rgba(255,255,255,.3)}
.cvc-card{background:#fff;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.1);margin:14px 14px 0;padding:16px}
.cvgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.cvgi label{font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:#6b7280;font-weight:700;display:block;margin-bottom:2px}
.cvgi strong{font-size:13px;color:#111}
.cvpkg{text-align:center;padding:18px;border-radius:8px}
.cvpkg-lbl{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:3px}
.cvpkg-total{font-size:34px;font-weight:900;margin-bottom:5px}
.cvpkg-desc{font-size:12px;color:#555;font-style:italic}
.cvtrade{margin:14px 14px 0}
.cvtrade-hd{background:#1a3a5c;color:#fff;padding:8px 14px;font-size:12px;font-weight:700;border-radius:6px 6px 0 0}
.cvt{width:100%;border-collapse:collapse;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.1);border-radius:0 0 6px 6px;overflow:hidden}
.cvt th{padding:7px 10px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.4px;background:#f9fafb;border-bottom:1px solid #e5e7eb;color:#6b7280}
.cvth-c{width:48px;text-align:center !important}
.cvth-r{width:82px;text-align:right !important}
.cvt td{padding:7px 10px;border-bottom:1px solid #f3f4f6;font-size:12px}
.cvt tr:last-child td{border-bottom:none}
.cvn{font-weight:500}
.cvd{font-size:10px;color:#6b7280;font-style:italic;margin-top:2px}
.cvc{text-align:center;color:#6b7280}
.cvc-desc{font-weight:400;color:#6b7280;font-style:italic}
.cvr{text-align:right;font-weight:600}
.cvt tfoot td{background:#f9fafb;font-weight:700;padding:8px 10px;border-top:2px solid #e5e7eb;font-size:12px}
.cvsub-l{text-align:right;color:#6b7280;font-size:11px;padding-right:12px}
.cvsub{color:#1a3a5c}
.cvgrand{margin:14px 14px 0;background:#1a3a5c;color:#fff;padding:13px 16px;border-radius:6px;
  display:flex;justify-content:space-between;align-items:center}
.cvgrand-lbl{font-size:12px;font-weight:600;opacity:.8}
.cvgrand-amt{font-size:22px;font-weight:800}
.cvnotes{background:#fff;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.1);margin:14px 14px 0;padding:14px}
.cvnotes h3{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#6b7280;margin-bottom:6px}
.cvnotes p{font-size:13px;line-height:1.6;color:#374151;white-space:pre-wrap}
.cvcontract{margin:14px;background:#fff;border-radius:8px;border:1px solid #e5e7eb;overflow:hidden}
.cvcontract summary{padding:13px 16px;cursor:pointer;font-weight:600;font-size:13px;color:#1a3a5c;list-style:none}
.cvcontract summary::-webkit-details-marker{display:none}
.cvcontract[open] summary{border-bottom:1px solid #e5e7eb}
.cvcontract-body{padding:14px 16px;font-size:11px;line-height:1.7;color:#4b5563;white-space:pre-wrap;
  max-height:280px;overflow-y:auto;background:#f9fafb}
.cvsig{margin:14px;padding:20px;background:#fff;border-radius:8px;border:2px solid #1a3a5c;
  box-shadow:0 4px 14px rgba(0,0,0,.1)}
.cvsig h2{font-size:17px;font-weight:800;color:#1a3a5c;margin-bottom:4px}
.cvsig .sub{font-size:12px;color:#6b7280;margin-bottom:16px}
.cvinput{width:100%;border:1px solid #d1d5db;border-radius:6px;padding:11px 13px;font-size:14px;
  margin-bottom:10px;font-family:inherit;outline:none;color:#111}
.cvinput:focus{border-color:#1a3a5c;box-shadow:0 0 0 3px rgba(26,58,92,.12)}
.cvagree{display:flex;align-items:flex-start;gap:10px;font-size:12px;color:#374151;
  margin-bottom:16px;line-height:1.5;cursor:pointer}
.cvagree input{margin-top:2px;flex-shrink:0;width:16px;height:16px;cursor:pointer}
.cvbtn{width:100%;padding:15px;background:#1a3a5c;color:#fff;border:none;border-radius:8px;
  font-size:16px;font-weight:700;cursor:pointer;margin-bottom:10px;transition:background .15s}
.cvbtn:hover{background:#0e2440}
.cvlegal{font-size:10px;color:#9ca3af;text-align:center;line-height:1.5}
.cert{margin:14px;background:#fff;border:2px solid #1a3a5c;border-radius:8px;padding:18px}
.cert-title{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:1.5px;color:#1a3a5c;
  margin-bottom:13px;padding-bottom:10px;border-bottom:2px solid #1a3a5c}
.cert-tbl{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:10px}
.cert-tbl td{padding:5px 0;vertical-align:top}
.cert-tbl td:first-child{color:#6b7280;font-weight:600;width:110px;font-size:10px;text-transform:uppercase;letter-spacing:.4px}
.cert-tbl td:last-child{font-weight:600;color:#111;word-break:break-all}
.cert-legal{font-size:10px;color:#9ca3af;line-height:1.5;border-top:1px solid #f3f4f6;padding-top:10px}
.mono{font-family:monospace;font-size:10px}
.cvftr{text-align:center;padding:28px 16px 36px;background:#1a3a5c;color:rgba(255,255,255,.6);line-height:1.6;margin-top:20px}
.cvftr-logo{height:42px;width:auto;background:#fff;padding:7px 15px;border-radius:8px;margin-bottom:13px}
.cvftr strong{color:#fff;font-size:15px;display:block;margin-bottom:4px;letter-spacing:.3px}
.cvftr-c{font-size:12px}
.cvftr-sub{font-size:10px;margin-top:8px;opacity:.7}
.cvhidden-note{font-size:10px;color:#9ca3af;font-style:italic;padding:5px 10px;text-align:left}
.cv-tier-section{margin:0 14px}
.cv-tier-heading{font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.8px;color:#1a3a5c;padding:16px 0 10px}
.cv-tier-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:4px}
.cv-tier-card{border:2.5px solid #d1d5db;border-radius:10px;padding:14px 10px;text-align:center;
  cursor:pointer;transition:all .18s;background:#fff;position:relative;-webkit-user-select:none;user-select:none}
.cv-tier-card:hover{transform:translateY(-2px);box-shadow:0 4px 14px rgba(0,0,0,.12)}
.cv-tier-card.cv-tier-selected{box-shadow:0 4px 16px rgba(0,0,0,.15);transform:translateY(-2px)}
.cv-tier-popular{position:absolute;top:-10px;left:50%;transform:translateX(-50%);background:#16a34a;
  color:#fff;font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.5px;
  padding:2px 8px;border-radius:20px;white-space:nowrap}
.cv-tier-name{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.8px;margin-bottom:3px}
.cv-tier-price{font-size:22px;font-weight:900;margin-bottom:5px}
.cv-tier-desc{font-size:10px;color:#6b7280;font-style:italic;margin-bottom:6px;line-height:1.4}
.cv-tier-check{font-size:11px;font-weight:700;color:#6b7280;border:1px solid #d1d5db;border-radius:20px;
  padding:3px 9px;display:inline-block;margin-top:2px;transition:all .15s}
.cv-tier-selected .cv-tier-check{background:#6b7280;color:#fff}
@media(max-width:400px){.cvgrid{grid-template-columns:1fr}.cvpkg-total{font-size:26px}.cv-tier-cards{grid-template-columns:1fr}}
@media print{.cv-print-btn{display:none}body{background:#fff}.cert{border-width:1.5pt;page-break-inside:avoid}}
"""


def _build_insurance_cv(est, token):
    """Customer-facing page for insurance-mode estimates (no GBB tier selection)."""
    c         = est.get('customer', {})
    a         = c.get('address', {})
    cs        = ', '.join(filter(None, [a.get('city'), a.get('state')]))
    addr      = ', '.join(filter(None, [a.get('street'), cs]))
    eid       = est.get('estimate_id', '')
    enum      = 'EST-' + eid.split('-')[0].upper() if eid else 'DRAFT'
    notes     = (est.get('notes_customer') or '').strip()
    ctext     = (est.get('contract_text') or '').strip()
    sp        = (est.get('salesperson') or '').replace('.', ' ').replace('_', ' ').title()

    ins_td      = est.get('trades', {}).get('insurance', {})
    items       = ins_td.get('line_items', [])
    carrier     = (ins_td.get('carrier') or '').strip()
    claim_num   = (ins_td.get('claim_number') or '').strip()
    scope_notes = (ins_td.get('scope_notes') or '').strip()

    ins_total = sum(
        float(i.get('acv') or 0) + float(i.get('rcv') or 0) for i in items
    )

    ins_rows = ''
    for item in items:
        acv   = float(item.get('acv') or 0)
        rcv   = float(item.get('rcv') or 0)
        total = acv + rcv
        desc  = (item.get('description') or '').strip()
        ins_rows += f'''<tr>
          <td class="cvn">{he(item.get("name",""))}</td>
          <td class="cvn cvc-desc">{he(desc)}</td>
          <td class="cvr">{fc(acv)}</td>
          <td class="cvr">{fc(rcv)}</td>
          <td class="cvr">{fc(total)}</td></tr>'''

    notes_html   = f'<div class="cvnotes"><h3>Notes</h3><p>{he(notes)}</p></div>' if notes else ''
    ctext_html   = f'''<details class="cvcontract"><summary>&#128203; View Full Terms &amp; Conditions</summary>
      <div class="cvcontract-body">{he(ctext)}</div></details>''' if ctext else ''
    sp_html      = f'<div class="cvgi"><label>Salesperson</label><strong>{he(sp)}</strong></div>' if sp else ''
    carrier_row  = f'<div class="cvgi"><label>Insurance Carrier</label><strong>{he(carrier)}</strong></div>' if carrier else ''
    claim_row    = f'<div class="cvgi"><label>Claim #</label><strong>{he(claim_num)}</strong></div>' if claim_num else ''
    scope_html   = f'<div class="cvnotes"><h3>Scope of Work</h3><p>{he(scope_notes)}</p></div>' if scope_notes else ''

    ins_table = ''
    if items:
        ins_table = f'''<div class="cvtrade">
          <div class="cvtrade-hd">Insurance Estimate Items</div>
          <table class="cvt"><thead><tr>
            <th>Item Name</th><th>Description</th>
            <th class="cvth-r">ACV</th>
            <th class="cvth-r">RCV</th>
            <th class="cvth-r">Total</th></tr></thead>
          <tbody>{ins_rows}</tbody>
          <tfoot><tr><td colspan="4" class="cvsub-l">Insurance Claim Total</td>
            <td class="cvr cvsub">{fc(ins_total)}</td></tr></tfoot>
          </table></div>
        <div class="cvgrand">
          <span class="cvgrand-lbl">Insurance Claim Total</span>
          <span class="cvgrand-amt">{fc(ins_total)}</span>
        </div>'''
    else:
        ins_table = '<div class="cvnotes" style="text-align:center;color:#9ca3af">No insurance line items entered yet.</div>'

    return f'''<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Your Insurance Estimate &mdash; Project One Roofing</title>
<style>{_CV_CSS}</style></head><body>

<header class="cvhdr">
  <div class="cvhdr-logo-wrap"><img src="/static/logo.png" alt="Project One Roofing"></div>
  <div class="cvhdr-contact">
    <a href="tel:9707750945">970-775-0945</a>
    <span>projectoneroofingcolorado.com</span>
  </div>
</header>
<div class="cvbrand-stripe"></div>

<div class="cvhero">
  <div class="cvhero-brand">Project One Roofing</div>
  <h1>Your Insurance Estimate is Ready</h1>
  <p>Review your scope below, then sign at the bottom to accept</p>
</div>

<div class="cvc-card">
  <div class="cvgrid">
    <div class="cvgi"><label>Prepared For</label><strong>{he(c.get("name","—"))}</strong></div>
    <div class="cvgi"><label>Estimate #</label><strong>{he(enum)}</strong></div>
    <div class="cvgi"><label>Address</label><strong>{he(addr or "—")}</strong></div>
    <div class="cvgi"><label>Date</label><strong>{he(est.get("estimate_date","—"))}</strong></div>
    {carrier_row}
    {claim_row}
    {sp_html}
    <div class="cvgi"><label>Valid Until</label><strong>{he(est.get("valid_until","—"))}</strong></div>
  </div>
</div>

{ins_table}
{scope_html}
{notes_html}
{ctext_html}

<div class="cvsig">
  <h2>Sign to Accept</h2>
  <p class="sub">Your electronic signature confirms you have reviewed and agreed to the insurance estimate above and all terms &amp; conditions.</p>
  <form method="POST" action="/sign/{he(token)}">
    <input type="hidden" name="selected_tier" value="insurance">
    <input class="cvinput" name="sig_name" placeholder="Your full legal name *" required autocomplete="name">
    <input class="cvinput" name="sig_email" placeholder="Email address (optional)" type="email" autocomplete="email">
    <label class="cvagree">
      <input type="checkbox" name="agree" required>
      I have read this insurance estimate and I agree to all terms &amp; conditions.
    </label>
    <button type="submit" class="cvbtn">&#10003; Accept &mdash; Sign Electronically</button>
    <p class="cvlegal">By clicking Accept, you are electronically signing this contract. This signature is legally
    binding under the federal E-SIGN Act (15 U.S.C. &sect;&nbsp;7001) and the Uniform Electronic Transactions Act.</p>
  </form>
</div>

<div class="cvftr">
  <img src="/static/logo.png" class="cvftr-logo" alt="Project One Roofing">
  <strong>Project One Roofing</strong>
  <div class="cvftr-c">115 E 5th St &middot; Loveland, CO 80537<br>970-775-0945 &middot; projectoneroofingcolorado.com</div>
</div>
</body></html>'''


def build_customer_view(est, token):
    # Branch: insurance estimates get a simpler, no-tier-selection view
    if est.get('estimate_type') == 'insurance':
        return _build_insurance_cv(est, token)

    c    = est.get('customer', {})
    a    = c.get('address', {})
    cs   = ', '.join(filter(None, [a.get('city'), a.get('state')]))
    addr = ', '.join(filter(None, [a.get('street'), cs]))
    default_tier = est.get('selected_tier', 'better')
    eid  = est.get('estimate_id', '')
    enum = 'EST-' + eid.split('-')[0].upper() if eid else 'DRAFT'
    notes  = (est.get('notes_customer') or '').strip()
    ctext  = (est.get('contract_text') or '').strip()
    sp     = (est.get('salesperson') or '').replace('.', ' ').replace('_', ' ').title()
    tdesc  = est.get('tier_descriptions') or {}

    notes_html = f'<div class="cvnotes"><h3>Notes</h3><p>{he(notes)}</p></div>' if notes else ''
    ctext_html = f'''<details class="cvcontract"><summary>&#128203; View Full Terms &amp; Conditions</summary>
      <div class="cvcontract-body">{he(ctext)}</div></details>''' if ctext else ''
    sp_html    = f'<div class="cvgi"><label>Salesperson</label><strong>{he(sp)}</strong></div>' if sp else ''

    # Pre-render line items for all 3 tiers
    tier_data = {}
    for t in ['good', 'better', 'best']:
        li_html, total = render_line_items(est, tier=t)
        tier_data[t] = {'html': li_html, 'total': total}

    tier_clrs = dict(good='#2563eb', better='#16a34a', best='#b45309')
    tier_bgs  = dict(good='#dbeafe', better='#dcfce7', best='#fef3c7')
    tier_lbls = dict(good='Good',    better='Better',  best='Best')

    # Build the 3 package selection cards
    cards_html = ''
    for t in ['good', 'better', 'best']:
        total  = tier_data[t]['total']
        desc   = (tdesc.get(t) or '').strip()
        clr    = tier_clrs[t]
        bg     = tier_bgs[t]
        lbl    = tier_lbls[t]
        is_sel = t == default_tier
        popular_badge = '<div class="cv-tier-popular">Most Popular</div>' if t == 'better' else ''
        desc_el = f'<div class="cv-tier-desc">{he(desc)}</div>' if desc else ''
        cards_html += f'''<div class="cv-tier-card {'cv-tier-selected' if is_sel else ''}"
          data-tier="{t}" data-total="{total:.2f}"
          style="border-color:{clr};{'background:'+bg if is_sel else ''}"
          onclick="selectCvTier('{t}')">
          {popular_badge}
          <div class="cv-tier-name" style="color:{clr}">{lbl}</div>
          <div class="cv-tier-price" style="color:{clr}">{fc(total)}</div>
          {desc_el}
          <div class="cv-tier-check" id="cv-check-{t}">{'&#10003; Selected' if is_sel else 'Select'}</div>
        </div>'''

    # Build hidden/visible line item blocks for each tier
    tier_blocks_html = ''
    for t in ['good', 'better', 'best']:
        vis = '' if t == default_tier else 'display:none'
        tier_blocks_html += f'<div id="tier-items-{t}" style="{vis}">{tier_data[t]["html"]}</div>\n'

    default_total = tier_data[default_tier]['total']
    default_lbl   = tier_lbls[default_tier]

    return f'''<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Your Estimate — Project One Roofing</title>
<style>{_CV_CSS}</style></head><body>

<header class="cvhdr">
  <div class="cvhdr-logo-wrap"><img src="/static/logo.png" alt="Project One Roofing"></div>
  <div class="cvhdr-contact">
    <a href="tel:9707750945">970-775-0945</a>
    <span>projectoneroofingcolorado.com</span>
  </div>
</header>
<div class="cvbrand-stripe"></div>

<div class="cvhero">
  <div class="cvhero-brand">Project One Roofing</div>
  <h1>Your Estimate is Ready to Review</h1>
  <p>Choose your package below, then sign at the bottom to accept</p>
</div>

<div class="cvc-card">
  <div class="cvgrid">
    <div class="cvgi"><label>Prepared For</label><strong>{he(c.get("name","—"))}</strong></div>
    <div class="cvgi"><label>Estimate #</label><strong>{he(enum)}</strong></div>
    <div class="cvgi"><label>Address</label><strong>{he(addr or "—")}</strong></div>
    <div class="cvgi"><label>Date</label><strong>{he(est.get("estimate_date","—"))}</strong></div>
    {sp_html}
    <div class="cvgi"><label>Valid Until</label><strong>{he(est.get("valid_until","—"))}</strong></div>
  </div>
</div>

<div class="cv-tier-section">
  <div class="cv-tier-heading">Step 1 &mdash; Choose Your Package</div>
  <div class="cv-tier-cards" id="tier-cards">
    {cards_html}
  </div>
</div>

{tier_blocks_html}

<div class="cvgrand" style="margin-top:14px" id="cv-grand-bar">
  <span class="cvgrand-lbl" id="cv-grand-lbl">Total &mdash; {he(default_lbl)} Package</span>
  <span class="cvgrand-amt" id="cv-grand-amt">{fc(default_total)}</span>
</div>

{notes_html}
{ctext_html}

<div class="cvsig">
  <h2>Step 2 &mdash; Sign to Accept</h2>
  <p class="sub" id="cv-sig-sub">Your electronic signature confirms you have reviewed and agreed to the
    <strong id="cv-sig-tier">{he(default_lbl)}</strong> Package and all terms above.</p>
  <form method="POST" action="/sign/{he(token)}">
    <input type="hidden" name="selected_tier" id="cv-tier-input" value="{he(default_tier)}">
    <input class="cvinput" name="sig_name" placeholder="Your full legal name *" required autocomplete="name">
    <input class="cvinput" name="sig_email" placeholder="Email address (optional)" type="email" autocomplete="email">
    <label class="cvagree">
      <input type="checkbox" name="agree" required>
      I have read this estimate, selected my package, and I agree to all terms &amp; conditions.
    </label>
    <button type="submit" class="cvbtn" id="cv-sign-btn">&#10003; Accept &mdash; Sign Electronically</button>
    <p class="cvlegal">By clicking Accept, you are electronically signing this contract. This signature is legally
    binding under the federal E-SIGN Act (15 U.S.C. &sect;&nbsp;7001) and the Uniform Electronic Transactions Act.</p>
  </form>
</div>

<div class="cvftr">
  <img src="/static/logo.png" class="cvftr-logo" alt="Project One Roofing">
  <strong>Project One Roofing</strong>
  <div class="cvftr-c">115 E 5th St &middot; Loveland, CO 80537<br>970-775-0945 &middot; projectoneroofingcolorado.com</div>
</div>

<script>
var _tier_totals = {{"good":{tier_data['good']['total']:.2f},"better":{tier_data['better']['total']:.2f},"best":{tier_data['best']['total']:.2f}}};
var _tier_lbls   = {{good:'Good',better:'Better',best:'Best'}};
var _tier_clrs   = {{good:'#2563eb',better:'#16a34a',best:'#b45309'}};
var _tier_bgs    = {{good:'#dbeafe',better:'#dcfce7',best:'#fef3c7'}};
var _cur_tier    = '{he(default_tier)}';
function _fmt(n){{return'$'+Math.abs(n).toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g,',');}}
function selectCvTier(tier){{
  _cur_tier=tier;
  ['good','better','best'].forEach(function(t){{
    var card=document.querySelector('[data-tier="'+t+'"]');
    var chk=document.getElementById('cv-check-'+t);
    if(t===tier){{
      card.classList.add('cv-tier-selected');
      card.style.background=_tier_bgs[t];
      chk.innerHTML='&#10003; Selected';
    }}else{{
      card.classList.remove('cv-tier-selected');
      card.style.background='';
      chk.innerHTML='Select';
    }}
    document.getElementById('tier-items-'+t).style.display=(t===tier?'':'none');
  }});
  document.getElementById('cv-grand-lbl').textContent='Total — '+_tier_lbls[tier]+' Package';
  document.getElementById('cv-grand-amt').textContent=_fmt(_tier_totals[tier]);
  document.getElementById('cv-grand-bar').style.borderLeftColor=_tier_clrs[tier];
  document.getElementById('cv-tier-input').value=tier;
  document.getElementById('cv-sig-tier').textContent=_tier_lbls[tier];
  document.getElementById('cv-sign-btn').textContent='✓ Accept — '+_tier_lbls[tier]+' Package';
}}
</script>
</body></html>'''


def build_signed_confirmation(est):
    sig  = est.get('signature', {}) or {}
    sname = sig.get('name', '')
    semail= sig.get('email', '')
    stime = sig.get('signed_at', '')
    ip    = sig.get('ip_address', '')
    dhash = sig.get('document_hash', '')

    try:
        dt = datetime.fromisoformat(stime.replace('Z', '+00:00'))
        stime_fmt = dt.strftime('%B %d, %Y at %I:%M %p UTC')
    except Exception:
        stime_fmt = stime

    c    = est.get('customer', {})
    a    = c.get('address', {})
    cs   = ', '.join(filter(None, [a.get('city'), a.get('state')]))
    addr = ', '.join(filter(None, [a.get('street'), cs]))
    tier = est.get('selected_tier', 'better')
    tlbl = dict(good='Good', better='Better', best='Best').get(tier, tier.title())
    eid  = est.get('estimate_id', '')
    enum = 'EST-' + eid.split('-')[0].upper() if eid else 'DRAFT'
    li_html, gtotal = render_line_items(est)

    notes  = (est.get('notes_customer') or '').strip()
    ctext  = (est.get('contract_text') or '').strip()
    notes_html = f'<div class="cvnotes"><h3>Notes</h3><p>{he(notes)}</p></div>' if notes else ''
    ctext_html = f'''<details class="cvcontract"><summary>&#128203; View Terms &amp; Conditions</summary>
      <div class="cvcontract-body">{he(ctext)}</div></details>''' if ctext else ''
    email_row  = f'<tr><td>Email</td><td>{he(semail)}</td></tr>' if semail else ''
    hash_disp  = (dhash[:32] + '&hellip;') if len(dhash) > 32 else he(dhash)

    return f'''<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Estimate Accepted &mdash; Project One Roofing</title>
<style>{_CV_CSS}</style></head><body>

<header class="cvhdr">
  <div class="cvhdr-logo-wrap"><img src="/static/logo.png" alt="Project One Roofing"></div>
  <div class="cvhdr-contact">
    <a href="tel:9707750945">970-775-0945</a>
    <span>projectoneroofingcolorado.com</span>
  </div>
</header>
<div class="cvbrand-stripe"></div>

<div class="cvhero ok">
  <div class="cvhero-brand" style="color:#86efac">Project One Roofing</div>
  <div class="cv-check">&#10003;</div>
  <h1>Estimate Accepted!</h1>
  <p>Thank you, {he(sname)}. Project One Roofing will be in touch soon to schedule your project.</p>
  <button class="cv-print-btn" onclick="window.print()">&#128424; Save / Print Signed Copy</button>
</div>

<div class="cert">
  <div class="cert-title">&#128274; Electronic Signature Certificate</div>
  <table class="cert-tbl">
    <tr><td>Document</td><td>{he(enum)} &mdash; {he(c.get("name",""))}</td></tr>
    <tr><td>Signed By</td><td>{he(sname)}</td></tr>
    {email_row}
    <tr><td>Signed On</td><td>{he(stime_fmt)}</td></tr>
    <tr><td>IP Address</td><td>{he(ip)}</td></tr>
    <tr><td>Estimate ID</td><td><span class="mono">{he(eid)}</span></td></tr>
    <tr><td>Doc Hash</td><td><span class="mono">{hash_disp}</span></td></tr>
  </table>
  <p class="cert-legal">This document was electronically signed in accordance with the federal Electronic
  Signatures in Global and National Commerce Act (E-SIGN Act, 15 U.S.C. &sect;&nbsp;7001) and the Uniform
  Electronic Transactions Act (UETA). The electronic signature has the same legal effect as a handwritten
  signature. The Document Hash above is a SHA-256 fingerprint of the estimate at the time of signing &mdash;
  any modification to the document would produce a different hash value.</p>
</div>

<div class="cvc-card">
  <div class="cvgrid">
    <div class="cvgi"><label>Customer</label><strong>{he(c.get("name","—"))}</strong></div>
    <div class="cvgi"><label>Estimate #</label><strong>{he(enum)}</strong></div>
    <div class="cvgi"><label>Address</label><strong>{he(addr or "—")}</strong></div>
    <div class="cvgi"><label>Package</label><strong>{he(tlbl)}</strong></div>
  </div>
</div>

{li_html}

<div class="cvgrand" style="margin-top:14px">
  <span class="cvgrand-lbl">Total &mdash; {he(tlbl)} Package</span>
  <span class="cvgrand-amt">{fc(gtotal)}</span>
</div>

{notes_html}
{ctext_html}

<div class="cvftr">
  <img src="/static/logo.png" class="cvftr-logo" alt="Project One Roofing">
  <strong>Project One Roofing</strong>
  <div class="cvftr-c">115 E 5th St &middot; Loveland, CO 80537<br>970-775-0945 &middot; projectoneroofingcolorado.com</div>
  <div class="cvftr-sub">Signed: {he(stime_fmt)} &middot; IP: {he(ip)}</div>
</div>
</body></html>'''


# ── E-Signature routes ──────────────────────────────────────────────────────

@app.route('/api/server-info', methods=['GET'])
def server_info():
    """Return network info so the frontend can build share URLs correctly."""
    base = PUBLIC_URL or f'http://{LAN_IP}:5000'
    return jsonify({'base_url': base, 'lan_ip': LAN_IP, 'public_url': PUBLIC_URL})


@app.route('/api/server-info', methods=['PUT'])
def save_server_info():
    """Persist a custom public_url to config.json."""
    data = request.get_json(force=True)
    new_url = (data.get('public_url') or '').strip().rstrip('/')
    cfg = os.path.join(DATA_DIR, 'config.json')
    try:
        cfg_data = json.load(open(cfg)) if os.path.exists(cfg) else {}
    except Exception:
        cfg_data = {}
    cfg_data['public_url'] = new_url
    with open(cfg, 'w') as f:
        json.dump(cfg_data, f, indent=2)
    global PUBLIC_URL
    PUBLIC_URL = new_url
    base = PUBLIC_URL or f'http://{LAN_IP}:5000'
    return jsonify({'ok': True, 'base_url': base})


@app.route('/api/estimates/<est_id>/share', methods=['POST'])
def create_share_link(est_id):
    path = os.path.join(ESTIMATES_DIR, f'{est_id}.json')
    if not os.path.exists(path):
        return jsonify({'error': 'Not found'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        est = json.load(f)
    token = est.get('share_token') or secrets.token_urlsafe(24)
    est['share_token'] = token
    est['updated_at']  = datetime.utcnow().isoformat() + 'Z'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(est, f, indent=2)
    # Use PUBLIC_URL if set, otherwise fall back to the LAN IP so customers can reach the link
    base = PUBLIC_URL or f'http://{LAN_IP}:5000'
    return jsonify({'token': token, 'url': f'/sign/{token}', 'full_url': f'{base}/sign/{token}'})


@app.route('/sign/<token>', methods=['GET', 'POST'])
def customer_sign(token):
    result = find_by_token(token)
    if not result:
        return '<h2 style="font-family:sans-serif;padding:40px">Link not found or expired.</h2>', 404
    est, path = result

    if request.method == 'POST':
        sig_name      = (request.form.get('sig_name') or '').strip()
        sig_email     = (request.form.get('sig_email') or '').strip()
        selected_tier = (request.form.get('selected_tier') or '').strip()
        if not sig_name:
            return 'Full name is required.', 400
        # Save the customer-chosen tier back to the estimate
        if selected_tier in ('good', 'better', 'best'):
            est['selected_tier'] = selected_tier
        # Hash the document BEFORE adding signature so hash represents what was signed
        content   = json.dumps(est, sort_keys=True, separators=(',', ':')).encode('utf-8')
        doc_hash  = hashlib.sha256(content).hexdigest()
        signed_at = datetime.utcnow()
        est['signature'] = {
            'name':          sig_name,
            'email':         sig_email,
            'signed_at':     signed_at.isoformat() + 'Z',
            'ip_address':    request.remote_addr,
            'user_agent':    request.headers.get('User-Agent', ''),
            'document_hash': doc_hash,
            'token':         token,
            'selected_tier': est.get('selected_tier', 'better'),
        }
        est['status']     = 'accepted'
        est['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(est, f, indent=2)
        return build_signed_confirmation(est)

    # Already signed — show the confirmation instead of the form
    if est.get('signature'):
        return build_signed_confirmation(est)

    return build_customer_view(est, token)


# ── Templates ──────────────────────────────────────────────────────────────

TEMPLATES = {
    "roofing": [
        {"name": "Shingles", "unit": "SQ",
         "desc_good":   "3-Tab",
         "desc_better": "Architectural",
         "desc_best":   "Designer / Premium",
         "notes_good":   "3-Tab asphalt shingles provide reliable, code-compliant leak protection at a competitive price point. Clean, classic look with a 25-year manufacturer limited warranty.",
         "notes_better": "Architectural laminate shingles add dimensional shadow lines and a high-end appearance. Enhanced wind resistance rated up to 130 mph. Lifetime limited warranty — the most popular choice for long-term value.",
         "notes_best":   "Premium designer shingles replicate the look of natural slate or cedar shake with superior impact resistance. Class 4 impact rating may qualify your homeowner for an insurance premium discount. Lifetime limited warranty."},
        {"name": "Synthetic Underlayment", "unit": "SQ",
         "desc_good":   "Standard Felt",
         "desc_better": "Synthetic",
         "desc_best":   "Premium Synthetic",
         "notes_good":   "Standard 15# felt paper provides a reliable moisture barrier during installation.",
         "notes_better": "Synthetic underlayment is 4× stronger than felt with superior tear resistance and moisture protection. Rated for 6-month UV exposure if left exposed — built for Colorado's unpredictable weather.",
         "notes_best":   "Premium synthetic underlayment with integrated self-sealing nail strips for maximum protection. Virtually eliminates fastener-driven moisture intrusion."},
        {"name": "Ice & Water Shield", "unit": "SQ",
         "desc_good":   "Eaves & Valleys",
         "desc_better": "Eaves, Valleys & Penetrations",
         "desc_best":   "Full Deck Coverage",
         "notes_good":   "Self-adhering waterproof membrane installed at eaves and valleys — the minimum protection required for Colorado's freeze-thaw climate.",
         "notes_better": "Ice & water barrier installed at all eaves, rakes, valleys, and pipe penetrations. Protects the areas most vulnerable to ice damming and wind-driven rain infiltration.",
         "notes_best":   "Full-coverage ice & water barrier across the entire roof deck — the gold standard for hail country. Provides maximum protection regardless of weather severity."},
        {"name": 'Decking (OSB 7/16")', "unit": "SQ",
         "desc_good":   "Replace Damaged Only",
         "desc_better": "Replace Damaged + Inspection",
         "desc_best":   "Full Inspection + Replace as Needed",
         "notes_good":   "7/16\" OSB panels replaced only where structurally compromised.",
         "notes_better": "7/16\" OSB structural sheathing replaced in all deteriorated sections discovered during tear-off. Full deck inspection ensures a solid nailing base for all roofing components.",
         "notes_best":   "Comprehensive deck inspection with replacement of all questionable panels. Nail protrusion check performed before installation to maximize shingle performance and warranty validity."},
        {"name": "Drip Edge", "unit": "LF",
         "desc_good":   "Galvanized Steel",
         "desc_better": "Pre-finished Galvanized Steel",
         "desc_best":   "Heavy-Gauge Aluminum",
         "notes_good":   "Standard galvanized steel drip edge installed at eaves and rakes.",
         "notes_better": "Pre-finished galvanized steel drip edge installed at all eaves and rakes per manufacturer specs — directs water cleanly away from fascia and prevents wood rot.",
         "notes_best":   "Heavy-gauge pre-painted aluminum drip edge color-matched to your shingles for a sharp, finished appearance with maximum longevity."},
        {"name": "Ridge Cap", "unit": "LF",
         "desc_good":   "Cut-Shingle Ridge",
         "desc_better": "Pre-formed Hip & Ridge Cap",
         "desc_best":   "High-Definition Ridge Cap",
         "notes_good":   "Cut-shingle ridge cap provides a watertight seal at all peaks.",
         "notes_better": "Pre-formed hip and ridge cap shingles installed at all peaks and hips — a finished, professional look with superior wind resistance.",
         "notes_best":   "High-definition ridge cap with 4-layer construction delivers a bold architectural profile and enhanced ventilation at the ridge — the crown jewel of a premium installation."},
        {"name": "Starter Strip", "unit": "LF",
         "desc_good":   "Standard Starter Strip",
         "desc_better": "Self-Sealing Starter Strip",
         "desc_best":   "Extended Self-Sealing Starter",
         "notes_good":   "Starter strip installed along eaves to seal the first course of shingles.",
         "notes_better": "Self-sealing starter strip installed at all eaves and rakes — seals the shingle edge and is a critical defense against wind uplift and blow-off.",
         "notes_best":   "Extended-width self-sealing starter strip with reinforced sealant bead provides maximum wind resistance — recommended for Colorado's high-wind regions."},
        {"name": "Pipe Boots", "unit": "EA",
         "desc_good":   "Standard Rubber Boots",
         "desc_better": "Rubber Boots + Aluminum Flashing",
         "desc_best":   "Premium Metal Boots",
         "notes_good":   "Standard rubber pipe boots seal all plumbing penetrations.",
         "notes_better": "Flexible rubber pipe boots with galvanized aluminum sleeves installed at every plumbing penetration — one of the most common leak points on any roof, done right.",
         "notes_best":   "Premium lead-flashed or heavy-gauge metal pipe boots — maximum lifespan and a weather-tight seal guaranteed at every penetration."},
        {"name": "Skylight Flashing", "unit": "EA",
         "desc_good":   "Step & Counter Flashing",
         "desc_better": "Full Flashing Kit",
         "desc_best":   "Custom Fabricated Flashing",
         "notes_good":   "Step flashing and counter flashing installed at all skylights.",
         "notes_better": "Complete step, counter, and saddle flashing kit at all skylights — properly integrated with the roofing system to prevent leaks at this critical junction.",
         "notes_best":   "Custom-fabricated copper or heavy-gauge aluminum flashing at all skylights — the premium solution engineered for decades of leak-free performance."},
        {"name": "Step / Wall Flashing", "unit": "LF",
         "desc_good":   "Aluminum Step Flashing",
         "desc_better": "Step + Counter Flashing",
         "desc_best":   "Copper / Stainless Flashing",
         "notes_good":   "Aluminum step flashing at all wall-to-roof junctions.",
         "notes_better": "Step flashing and counter flashing at all vertical wall transitions — properly integrated with housewrap and siding to manage water at every joint.",
         "notes_best":   "Copper or stainless step and counter flashing — the highest-performing solution for permanent, maintenance-free water management at all wall transitions."},
        {"name": "Tear-Off Labor", "unit": "SQ",
         "desc_good":   "Single Layer Tear-Off",
         "desc_better": "Full Tear-Off & Deck Inspection",
         "desc_best":   "Full Tear-Off + Nail Check",
         "notes_good":   "Removal and disposal of one layer of existing roofing materials.",
         "notes_better": "Complete removal and disposal of all existing roofing layers. Full deck inspection performed before installation begins — we find problems before they become your problem.",
         "notes_best":   "Full tear-off with detailed deck inspection, nail protrusion check across entire deck, and documentation of all replaced materials for your records."},
        {"name": "Install Labor", "unit": "SQ",
         "desc_good":   "Certified Crew Installation",
         "desc_better": "Factory-Certified Installation",
         "desc_best":   "Master Installer — Certified",
         "notes_good":   "Professional installation by our experienced, certified crew.",
         "notes_better": "Factory-certified professional installation following all manufacturer specifications — required to preserve the full manufacturer's warranty. Our crew lead brings 10+ years of roofing experience.",
         "notes_best":   "Master-installer-led crew with factory certification and documented installation photos provided for warranty registration. The peace of mind that comes with the best."},
        {"name": "Dumpster", "unit": "LS",
         "desc_good":   "Standard Dumpster Service",
         "desc_better": "Full-Service + Magnetic Sweep",
         "desc_best":   "Premium Cleanup Package",
         "notes_good":   "Dumpster for debris removal and off-site disposal.",
         "notes_better": "Full-service dumpster rental with on-site debris management. Magnetic sweep of driveway and lawn performed after job completion to collect nails and fasteners.",
         "notes_best":   "Premium cleanup — same-day debris removal, three-pass magnetic nail sweep, gutter check, and a final walkthrough with the homeowner before we leave the job site."},
        {"name": "Permit", "unit": "LS",
         "desc_good":   "Building Permit",
         "desc_better": "Permit + Inspection",
         "desc_best":   "Full Permit Management",
         "notes_good":   "Required local building permit obtained by Project One Roofing.",
         "notes_better": "All required local building permits pulled by Project One Roofing. Final inspection scheduled and passed — fully documented before project closeout.",
         "notes_best":   "Complete permit management — permit pulled, inspection scheduled and passed, full documentation package provided to homeowner for personal records and future property disclosure."},
    ],
    "siding": [
        {"name": "Vinyl Siding", "unit": "SQ",
         "desc_good": "Economy Vinyl", "desc_better": "Premium Vinyl", "desc_best": "Engineered Wood / Fiber Cement",
         "notes_good": "Economy-grade vinyl siding provides durable, low-maintenance protection at an accessible price point.",
         "notes_better": "Premium vinyl siding with thicker wall construction, deeper shadow lines, and a wider color palette. Resists fading and impact for decades with zero maintenance.",
         "notes_best": "Engineered wood or fiber cement siding offers the natural look of real wood with dramatically superior durability and fire resistance. The premium choice for lasting curb appeal."},
        {"name": "House Wrap", "unit": "SQ",
         "desc_good": "Standard WRB", "desc_better": "Premium WRB", "desc_best": "Fully Adhered WRB",
         "notes_good": "Standard weather-resistant barrier installed under siding.",
         "notes_better": "Premium weather-resistant barrier with enhanced moisture management and air sealing properties — keeps your home dry and energy-efficient.",
         "notes_best": "Fully adhered self-sealing weather-resistant barrier — the ultimate moisture and air barrier for maximum energy performance and water protection."},
        {"name": "Trim Board", "unit": "LF", "desc_good": "Vinyl Trim", "desc_better": "PVC Trim", "desc_best": "Premium PVC / Composite",
         "notes_good": "Vinyl trim boards at corners, windows, and doors.", "notes_better": "Cellular PVC trim — rot-proof, paint-ready, and dimensionally stable for a crisp, lasting finish.", "notes_best": "Premium composite trim for the highest-quality appearance and zero maintenance."},
        {"name": "J-Channel", "unit": "LF", "desc_good": "Standard", "desc_better": "Standard", "desc_best": "Standard",
         "notes_good": "J-channel at all window and door openings.", "notes_better": "J-channel at all window and door openings, properly lapped for water drainage.", "notes_best": "J-channel at all openings with additional caulking and flashing integration for maximum weather protection."},
        {"name": "Soffit", "unit": "SQ", "desc_good": "Solid Vinyl", "desc_better": "Vented Vinyl", "desc_best": "Aluminum / Premium Vented",
         "notes_good": "Solid vinyl soffit panels.", "notes_better": "Vented vinyl soffit promotes attic airflow, reduces moisture buildup, and protects eaves from pests and weather.", "notes_best": "Premium vented aluminum soffit for superior durability and enhanced attic ventilation."},
        {"name": "Fascia", "unit": "LF", "desc_good": "Vinyl Fascia Cover", "desc_better": "PVC Fascia", "desc_best": "Aluminum / Composite Fascia",
         "notes_good": "Vinyl fascia cover over existing wood.", "notes_better": "PVC fascia board — fully rot-proof replacement that provides a clean, finished edge and a solid gutter attachment point.", "notes_best": "Aluminum or composite fascia for maximum longevity and the cleanest appearance."},
        {"name": "Corner Posts", "unit": "EA", "desc_good": "Standard Posts", "desc_better": "Premium Posts", "desc_best": "Premium Posts",
         "notes_good": "Standard vinyl corner posts.", "notes_better": "Premium vinyl corner posts with built-in J-channel for a seamless, finished appearance.", "notes_best": "Heavy-gauge corner posts for maximum durability and a sharp architectural corner."},
        {"name": "Tear-Off Labor", "unit": "SQ", "desc_good": "Remove Old Siding", "desc_better": "Remove + Inspect Sheathing", "desc_best": "Remove + Full Inspection",
         "notes_good": "Removal and disposal of existing siding.", "notes_better": "Complete removal of existing siding with sheathing inspection for rot and damage before new installation.", "notes_best": "Full removal with comprehensive sheathing inspection and documentation. All problem areas identified and reported before new materials are installed."},
        {"name": "Install Labor", "unit": "SQ", "desc_good": "Professional Installation", "desc_better": "Certified Installation", "desc_best": "Master Installer",
         "notes_good": "Professional siding installation by our experienced crew.", "notes_better": "Factory-trained siding installers following manufacturer best practices for maximum warranty coverage.", "notes_best": "Master installer-led team delivering precise, detail-oriented workmanship documented with completion photos."},
        {"name": "Dumpster", "unit": "LS", "desc_good": "Dumpster Service", "desc_better": "Full Cleanup", "desc_best": "Premium Cleanup",
         "notes_good": "Dumpster for debris removal.", "notes_better": "Full-service cleanup with debris hauled off-site and site broom-swept upon completion.", "notes_best": "Premium cleanup package — complete debris removal and a homeowner walkthrough before we leave the property."},
        {"name": "Permit", "unit": "LS", "desc_good": "Building Permit", "desc_better": "Permit + Inspection", "desc_best": "Full Permit Management",
         "notes_good": "Required building permit.", "notes_better": "All required permits pulled and final inspection scheduled.", "notes_best": "Complete permit management with all documentation provided to homeowner."},
    ],
    "windows": [
        {"name": "Window Unit", "unit": "EA",
         "desc_good": "Double-Pane Vinyl", "desc_better": "Double-Pane Low-E", "desc_best": "Triple-Pane Low-E",
         "notes_good": "Double-pane vinyl window — reliable energy performance and low maintenance.",
         "notes_better": "Double-pane Low-E coated window with argon gas fill — significantly reduces heat transfer, UV fading, and outside noise. Energy Star certified.",
         "notes_best": "Triple-pane Low-E window with krypton gas fill — the highest energy performance available. Superior sound reduction and maximum insulation value for Colorado's climate extremes."},
        {"name": "Window Trim Kit", "unit": "EA",
         "desc_good": "Standard Trim", "desc_better": "PVC Trim Kit", "desc_best": "Premium Composite Trim",
         "notes_good": "Standard exterior trim kit for a finished appearance.", "notes_better": "PVC exterior trim kit — rot-proof, clean finish that protects the window rough opening for decades.", "notes_best": "Premium composite trim kit for the most refined exterior appearance and maximum longevity."},
        {"name": "Exterior Casing", "unit": "LF",
         "desc_good": "Standard Casing", "desc_better": "PVC Casing", "desc_best": "Premium Composite",
         "notes_good": "Exterior window casing and flashing.", "notes_better": "PVC exterior casing with proper flashing integration — rot-proof and maintenance-free.", "notes_best": "Premium composite casing with full flashing tape system for the ultimate weather protection."},
        {"name": "Install Labor", "unit": "EA",
         "desc_good": "Standard Install", "desc_better": "Certified Install", "desc_best": "Master Install",
         "notes_good": "Professional window installation by our trained crew.", "notes_better": "Certified window installation with proper flashing, insulation, and air sealing per manufacturer specs.", "notes_best": "Master installer ensures each window is perfectly level, plumb, and square with full foam insulation and documented completion."},
        {"name": "Permit", "unit": "LS",
         "desc_good": "Permit", "desc_better": "Permit + Inspection", "desc_best": "Full Permit Management",
         "notes_good": "Required building permit.", "notes_better": "All required permits pulled and inspection coordinated.", "notes_best": "Complete permit management with documentation provided to homeowner."},
    ],
    "gutters": [
        {"name": '5" K-Style Gutter', "unit": "LF",
         "desc_good": '5" Aluminum', "desc_better": '5" Heavy-Gauge Aluminum', "desc_best": '5" Copper / Steel',
         "notes_good": 'Standard 5" K-style aluminum gutter — the most common residential gutter size, handles typical rainfall volume.',
         "notes_better": 'Heavy-gauge 5" K-style aluminum gutter — thicker walls resist denting, maintain shape, and last significantly longer than standard-gauge gutters.',
         "notes_best": 'Premium copper or galvanized steel 5" K-style gutter — the most durable and visually striking option, engineered for a lifetime of performance.'},
        {"name": '6" K-Style Gutter', "unit": "LF",
         "desc_good": '6" Aluminum', "desc_better": '6" Heavy-Gauge Aluminum', "desc_best": '6" Copper / Steel',
         "notes_good": '6" K-style aluminum gutter — larger capacity for steep-pitch roofs or high-rainfall areas.',
         "notes_better": '6" heavy-gauge aluminum gutter — maximum capacity with superior durability. Recommended for complex rooflines and higher-elevation homes.',
         "notes_best": '6" copper or galvanized steel gutter — the premium choice for maximum capacity and lasting beauty.'},
        {"name": "Downspout", "unit": "LF",
         "desc_good": "Standard", "desc_better": "Heavy-Gauge", "desc_best": "Copper / Steel",
         "notes_good": "Standard aluminum downspout directs water away from the foundation.", "notes_better": "Heavy-gauge aluminum downspout — resists denting and damage from ladders and yard equipment.", "notes_best": "Copper or galvanized steel downspout — maximum durability and visual impact."},
        {"name": "Gutter Guard / Screen", "unit": "LF",
         "desc_good": "Mesh Screen", "desc_better": "Micro-Mesh Guard", "desc_best": "Premium Micro-Mesh",
         "notes_good": "Aluminum mesh screens keep large debris out of gutters and reduce cleaning frequency.", "notes_better": "Micro-mesh gutter guards block even small debris like pine needles and shingle grit while allowing full water flow. Dramatically reduces maintenance.", "notes_best": "Premium micro-mesh guards with stainless steel mesh — the most effective debris protection available, backed by a no-clog guarantee."},
        {"name": "End Caps", "unit": "EA",
         "desc_good": "Standard", "desc_better": "Standard", "desc_best": "Copper / Matching",
         "notes_good": "End caps seal all gutter runs.", "notes_better": "Sealed end caps at all gutter terminations.", "notes_best": "Color-matched or copper end caps for a cohesive, finished appearance."},
        {"name": "Drop Outlets", "unit": "EA",
         "desc_good": "Standard", "desc_better": "Standard", "desc_best": "Heavy-Gauge",
         "notes_good": "Drop outlets connecting gutters to downspouts.", "notes_better": "Properly positioned drop outlets for optimized water flow and drainage.", "notes_best": "Heavy-gauge drop outlets with sealed connections for maximum longevity."},
        {"name": "Remove Old Gutters", "unit": "LF",
         "desc_good": "Remove & Haul", "desc_better": "Remove & Haul", "desc_best": "Remove & Haul",
         "notes_good": "Removal and disposal of existing gutter system.", "notes_better": "Complete removal of old gutters with inspection of fascia board condition before new installation.", "notes_best": "Full removal with fascia board inspection and documentation of any rot or damage discovered."},
        {"name": "Install Labor", "unit": "LF",
         "desc_good": "Professional Install", "desc_better": "Certified Install", "desc_best": "Master Install",
         "notes_good": "Professional gutter installation by our experienced crew.", "notes_better": "Certified installation with proper slope (1/16\" per foot) and secure hanger spacing for optimal performance.", "notes_best": "Master installer-led installation with precision slope calibration, hidden hanger system, and completion documentation."},
        {"name": "Permit", "unit": "LS",
         "desc_good": "Permit", "desc_better": "Permit", "desc_best": "Permit",
         "notes_good": "Required building permit where applicable.", "notes_better": "Required building permit obtained and inspection coordinated.", "notes_best": "Full permit management."},
    ],
    "other": [
        {"name": "Custom Item", "unit": "EA",
         "desc_good": "", "desc_better": "", "desc_best": "",
         "notes_good": "", "notes_better": "", "notes_best": ""},
    ],
}


# ── Price Book helpers ─────────────────────────────────────────────────────

def _load_price_book():
    if os.path.exists(PRICE_BOOK_FILE):
        try:
            with open(PRICE_BOOK_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {'intros': [], 'materials': {}}

def _save_price_book(pb):
    with open(PRICE_BOOK_FILE, 'w') as f:
        json.dump(pb, f, indent=2)


@app.route('/api/templates')
def get_templates():
    """Merge price book costs/visibility into template items so Load Defaults
    returns rich descriptions (from TEMPLATES) + user costs (from price book)."""
    pb = _load_price_book()
    pb_mats = pb.get('materials') or {}
    result = {}

    for trade, items in TEMPLATES.items():
        pb_items = pb_mats.get(trade, [])
        # Build cost/visibility lookup keyed by item name
        pb_by_name = {}
        for it in pb_items:
            name = it.get('name', '')
            if 'cost' in it:
                cost = float(it.get('cost') or 0)
            else:
                # Backward compat: old mat_better + lab_better format
                cost = float(it.get('mat_better') or 0) + float(it.get('lab_better') or 0)
            pb_by_name[name] = {
                'cost': cost,
                'customer_visible': it.get('customer_visible', True),
            }

        merged = []
        template_names = set()
        for item in items:
            name = item.get('name', '')
            template_names.add(name)
            merged_item = dict(item)
            if name in pb_by_name:
                merged_item['cost'] = pb_by_name[name]['cost']
                merged_item['customer_visible'] = pb_by_name[name]['customer_visible']
            else:
                merged_item.setdefault('cost', 0)
                merged_item.setdefault('customer_visible', True)
            merged.append(merged_item)

        # Append price-book-only items (user-added, not in hardcoded TEMPLATES)
        for pb_item in pb_items:
            if pb_item.get('name') not in template_names:
                merged.append(pb_item)

        result[trade] = merged

    # Include any trades in price book that aren't in hardcoded TEMPLATES
    for trade, pb_items in pb_mats.items():
        if trade not in result:
            result[trade] = pb_items

    return jsonify(result)


@app.route('/api/pricebook', methods=['GET'])
def get_pricebook():
    pb = _load_price_book()
    pb.setdefault('intros', [])
    pb.setdefault('materials', {})
    return jsonify(pb)


@app.route('/api/pricebook', methods=['PUT'])
def put_pricebook():
    _save_price_book(request.get_json(force=True))
    return jsonify({'ok': True})


@app.route('/api/pricebook/intros', methods=['POST'])
def upsert_intro():
    pb = _load_price_book()
    pb.setdefault('intros', [])
    tpl = request.get_json(force=True)
    for t in pb['intros']:
        if t.get('id') == tpl.get('id'):
            t.update(tpl)
            _save_price_book(pb)
            return jsonify({'ok': True, 'intros': pb['intros']})
    if not tpl.get('id'):
        tpl['id'] = str(uuid.uuid4())[:8]
    pb['intros'].append(tpl)
    _save_price_book(pb)
    return jsonify({'ok': True, 'intros': pb['intros']})


@app.route('/api/pricebook/intros/<tid>', methods=['DELETE'])
def delete_intro(tid):
    pb = _load_price_book()
    pb['intros'] = [t for t in pb.get('intros', []) if t.get('id') != tid]
    _save_price_book(pb)
    return jsonify({'ok': True, 'intros': pb['intros']})


# ── Tier defaults (global G/B/B package bullet points) ─────────────────────

def _load_tier_defaults():
    if os.path.exists(TIER_DEFAULTS_FILE):
        try:
            with open(TIER_DEFAULTS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {'good': [], 'better': [], 'best': []}

@app.route('/api/tier-defaults', methods=['GET'])
def get_tier_defaults():
    return jsonify(_load_tier_defaults())

@app.route('/api/tier-defaults', methods=['PUT'])
def put_tier_defaults():
    data = request.get_json(force=True)
    with open(TIER_DEFAULTS_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    return jsonify({'ok': True})


# ── Launch ─────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import threading, webbrowser
    port = int(os.environ.get('PORT', 5000))
    if port == 5000:
        threading.Timer(1.2, lambda: webbrowser.open('http://localhost:5000')).start()
    base = PUBLIC_URL or f'http://{LAN_IP}:{port}'
    print(f"  Estimate Builder running at http://localhost:{port}")
    print(f"  Customer share links will use: {base}")
    if LAN_IP == '127.0.0.1' and not PUBLIC_URL:
        print("  ⚠  Could not detect LAN IP — share links will only work on this machine.")
        print("     Set a PUBLIC_URL in estimator/config.json for external access.")
    app.run(debug=False, port=port, host='0.0.0.0')
