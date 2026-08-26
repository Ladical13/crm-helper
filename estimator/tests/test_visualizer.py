"""Visualizer — the product-photo overlay that ships with the /sign page and the signed PDF.

The rep uploads a house photo, paints roof + siding masks, picks a color per
tier (and, for siding, a style), and saves three composites — one per
Good/Better/Best tier. Server-side we store the pointers under
`est['visualizer']` and embed the renders into the customer-facing PDF and
sign page.

Invariants worth pinning:
  * `colors` (roofing + siding) and `styles` (siding) survive on a live
    price book — a book saved before the fields existed picks them up on
    the next GET. Without this the field pickers stay empty forever.
  * The `visualizer` block round-trips through PUT /api/estimates unchanged —
    it's not on any server-managed whitelist, and _merge does not strip
    unknown top-level keys. If somebody adds a whitelist someday, this
    catches it.
  * POST .../visualizer/asset decodes base64, writes to UPLOADS_DIR/<est>/
    and updates the pointer in est.visualizer.
  * When tier_renders are present the signed PDF is meaningfully bigger
    (an image was embedded), and the /sign page emits an <img> pointing
    at /uploads/.
"""
import base64
import io
import json
import os

import pytest

from conftest import TEST_DATA_DIR


# ── seed backfill ─────────────────────────────────────────────────────

def test_colors_backfill_onto_a_live_roofing_book(client, A):
    """A price book that shipped before `colors` existed must adopt the
    seed's colors on the next GET — otherwise every existing manager's
    Landmark product renders an empty color picker."""
    book = client.get('/api/pricebook').get_json()
    landmark = next(p for p in book['roofing_catalog'] if p['id'] == 'm_landmark')
    landmark.pop('colors', None)
    r = client.put('/api/pricebook', json=book)
    assert r.status_code == 200

    fresh = client.get('/api/pricebook').get_json()
    fresh_landmark = next(p for p in fresh['roofing_catalog'] if p['id'] == 'm_landmark')
    assert isinstance(fresh_landmark.get('colors'), list) and fresh_landmark['colors']
    assert all(isinstance(c, dict) and c.get('hex') for c in fresh_landmark['colors'])


def test_styles_backfill_onto_a_live_siding_book(client, A):
    """Siding styles (lap / B&B / shake / panel) are the visualizer's other
    axis for siding — a Hardie ColorPlus lap and a Hardie ColorPlus
    board-and-batten are the same color but different renders. Without
    styles the style row on the picker is empty."""
    book = client.get('/api/pricebook').get_json()
    hardie = next(p for p in book['siding_catalog'] if p['id'] == 's_hardie_primed')
    hardie.pop('styles', None)
    hardie.pop('colors', None)
    r = client.put('/api/pricebook', json=book)
    assert r.status_code == 200

    fresh = client.get('/api/pricebook').get_json()
    fresh_hardie = next(p for p in fresh['siding_catalog'] if p['id'] == 's_hardie_primed')
    assert isinstance(fresh_hardie.get('styles'), list) and fresh_hardie['styles']
    assert isinstance(fresh_hardie.get('colors'), list) and fresh_hardie['colors']
    assert {s['id'] for s in fresh_hardie['styles']} >= {'s_lap', 's_bnb', 's_shake', 's_panel'}


def test_seed_colors_are_deep_copied(A):
    """Regression: the copy helpers must not share the color/style dicts
    with the seed, or a client PUT that edits a color mutates the seed
    constant for every other request in the process."""
    seed = A.ROOFING_CATALOG_SEED
    landmark_seed = next(p for p in seed if p['id'] == 'm_landmark')
    copied = A._copy_seed_product(landmark_seed)
    copied['colors'][0]['name'] = 'MUTATED'
    assert landmark_seed['colors'][0]['name'] != 'MUTATED'


# ── estimate round-trip ────────────────────────────────────────────────

def test_visualizer_block_round_trips_through_put(client):
    """The whole `visualizer` sub-doc must be preserved by a normal
    estimate PUT — the frontend PUTs the entire estimate on save, and the
    server does not whitelist visualizer under SERVER_MANAGED_FIELDS."""
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        doc = client.get(f'/api/estimates/{eid}').get_json()
        doc['visualizer'] = {
            'base_image': f'{eid}/vb_test.jpg',
            'roof_mask':  f'{eid}/vm_roof.png',
            'siding_mask': f'{eid}/vm_side.png',
            'selections': {
                'roofing': {'better': {'bundle_id': 'b_northgate',
                                       'color_hex': '#2f2d2b',
                                       'color_name': 'Charcoal Black'}},
                'siding': {'better': {'bundle_id': 'b_hardie_primed',
                                      'style_id': 's_lap',
                                      'style_name': 'Lap Siding',
                                      'color_hex': '#4a4d4f',
                                      'color_name': 'Iron Gray'}},
            },
            'tier_renders': {'better': f'{eid}/vr_better.jpg'},
        }
        assert client.put(f'/api/estimates/{eid}', json=doc).status_code == 200
        fresh = client.get(f'/api/estimates/{eid}').get_json()
        assert fresh['visualizer']['selections']['roofing']['better']['color_hex'] == '#2f2d2b'
        assert fresh['visualizer']['tier_renders']['better'] == f'{eid}/vr_better.jpg'
    finally:
        client.delete(f'/api/estimates/{eid}')


# ── asset + state endpoints ────────────────────────────────────────────

# One-pixel JPEG (base64) — a real, valid image so the asset endpoint's
# strict base64 validation and file write run end-to-end without loading a
# camera photo. Generated once with PIL.new('RGB',(1,1)).save('JPEG').
_ONE_PX_JPEG_B64 = (
    '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHR'
    'ofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgy'
    'IRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMj'
    'L/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/'
    '8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAk'
    'M2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dn'
    'd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW'
    '19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBg'
    'cICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHB'
    'CSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2'
    'hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbH'
    'yMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDnqKKK8k/Qz//Z'
)


def test_asset_endpoint_writes_the_upload_and_updates_the_pointer(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        r = client.post(
            f'/api/estimates/{eid}/visualizer/asset',
            json={'kind': 'render', 'tier': 'better', 'ext': 'jpg',
                  'content_b64': _ONE_PX_JPEG_B64},
        )
        assert r.status_code == 201, r.get_data(as_text=True)
        body = r.get_json()
        assert body['url'].startswith(f'/uploads/{eid}/')
        # The file lives on disk under UPLOADS_DIR/<est>/.
        rel = body['filename']
        on_disk = os.path.join(TEST_DATA_DIR, 'uploads', rel)
        assert os.path.isfile(on_disk)
        # And est.visualizer.tier_renders.better points at it.
        fresh = client.get(f'/api/estimates/{eid}').get_json()
        assert fresh['visualizer']['tier_renders']['better'] == rel
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_asset_endpoint_rejects_junk_base64(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        r = client.post(
            f'/api/estimates/{eid}/visualizer/asset',
            json={'kind': 'render', 'tier': 'good', 'ext': 'jpg',
                  'content_b64': 'not-real-base64!!!'},
        )
        assert r.status_code == 400
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_asset_endpoint_validates_kind_role_tier(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        # mask without role
        r = client.post(f'/api/estimates/{eid}/visualizer/asset',
                        json={'kind': 'mask', 'ext': 'png',
                              'content_b64': _ONE_PX_JPEG_B64})
        assert r.status_code == 400
        # render without tier
        r = client.post(f'/api/estimates/{eid}/visualizer/asset',
                        json={'kind': 'render', 'ext': 'jpg',
                              'content_b64': _ONE_PX_JPEG_B64})
        assert r.status_code == 400
        # unknown kind
        r = client.post(f'/api/estimates/{eid}/visualizer/asset',
                        json={'kind': 'garbage', 'ext': 'jpg',
                              'content_b64': _ONE_PX_JPEG_B64})
        assert r.status_code == 400
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_asset_endpoint_accepts_and_stores_a_door_mask(client):
    """Doors are a third independent design layer, not a siding workaround."""
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        r = client.post(
            f'/api/estimates/{eid}/visualizer/asset',
            json={'kind': 'mask', 'role': 'door', 'ext': 'png',
                  'content_b64': _ONE_PX_JPEG_B64},
        )
        assert r.status_code == 201, r.get_data(as_text=True)
        fresh = client.get(f'/api/estimates/{eid}').get_json()
        assert fresh['visualizer']['door_mask'] == r.get_json()['filename']
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_pricebook_exposes_door_options_for_the_designer(client):
    """Exterior door visual choices must arrive with every live price book."""
    book = client.get('/api/pricebook').get_json()
    assert book['exterior_doors']
    assert all(d.get('name') and d.get('brand') == 'ProVia' and d.get('colors')
               for d in book['exterior_doors'])


def test_provia_replaces_placeholder_menu_without_mutating_custom_or_saved_choices(A):
    book = {'exterior_doors': [{'id': 'steel-6-panel'}, {'id': 'custom-door', 'name': 'Dealer custom'}]}
    A._ensure_bundle_catalogs(book)
    assert {d['id'] for d in book['exterior_doors']} == {
        'custom-door', 'provia-signet', 'provia-ascent', 'provia-legacy'}
    assert all(not d.get('pattern_id') for d in book['exterior_doors'] if d.get('brand') == 'ProVia')
    book['exterior_doors'][1]['colors'][0]['name'] = 'Manager edit'
    assert A.EXTERIOR_DOOR_OPTIONS_SEED[0]['colors'][0]['name'] == 'Snow Mist'
    empty = {'exterior_doors': []}
    A._ensure_bundle_catalogs(empty)
    assert empty['exterior_doors'] == []


@pytest.fixture
def detector(monkeypatch):
    from estimator import exterior_detection as d
    monkeypatch.setenv('EXTERIOR_AUTO_DETECT', '1')
    monkeypatch.setenv('FAL_KEY', 'test-only-not-a-live-key')
    # All tests must explicitly stub inference. No real customer image or
    # billable model call can leave the suite even if local env keys exist.
    monkeypatch.setattr(d, '_json', lambda *a, **kw: pytest.fail('Unmocked inference call'))
    return d


def _image_uri(image):
    buf = io.BytesIO()
    image.save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def test_detection_submit_disables_fal_payload_storage(monkeypatch):
    from estimator import exterior_detection as d
    monkeypatch.setenv('FAL_KEY', 'test-only-not-a-live-key')
    captured = {}

    class Response:
        status_code = 202

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_content(self, _size):
            yield b'{"request_id":"test"}'

    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return Response()

    monkeypatch.setattr(d.requests, 'request', fake_request)
    assert d._json('POST', d.MODEL_URL, {'image_url': 'private'}) == {'request_id': 'test'}
    assert captured['headers']['X-Fal-Store-IO'] == '0'
    assert captured['headers']['X-Fal-Object-Lifecycle-Preference'] == (
        '{"expiration_duration_seconds":3600}')
    assert captured['headers']['Authorization'] == 'Key test-only-not-a-live-key'


def test_detection_converts_binary_masks_to_alpha_and_rejects_low_confidence(detector):
    from PIL import Image
    mask = Image.new('L', (3, 2), 0)
    mask.putpixel((1, 0), 255)
    uri = _image_uri(mask)
    result = detector.combine_masks({'masks': [{'url': uri}], 'scores': [0.9]}, (3, 2))
    image = detector.decode_image(result['mask'])
    assert image.getpixel((1, 0)) == (255, 255, 255, 255)
    assert image.getpixel((0, 0))[3] == 0
    assert detector.combine_masks({'masks': [{'url': uri}], 'scores': [0.1]}, (3, 2))['status'] == 'not_found'
    assert detector.combine_masks({'masks': []}, (3, 2))['status'] == 'not_found'
    with pytest.raises(detector.DetectionError):
        detector.combine_masks({'masks': [{'url': uri}]}, (4, 3))
    with pytest.raises(detector.DetectionError):
        detector.combine_masks({'masks': [{'url': 'https://untrusted.invalid/image.png'}]}, (3, 2))


def test_detection_requires_explicit_setup_and_existing_authorized_estimate(client, anon, detector, monkeypatch):
    eid = client.post('/api/estimates', json={'salesperson': 'luke'}).get_json()['estimate_id']
    endpoint = f'/api/estimates/{eid}/visualizer/detection'
    try:
        assert anon.post(endpoint, json={}).status_code == 401
        with anon.session_transaction() as session:
            session['user'] = 'someone-else'
        assert anon.post(endpoint, json={}).status_code == 403
        assert client.post('/api/estimates/not-real/visualizer/detection', json={}).status_code == 404
        monkeypatch.delenv('EXTERIOR_AUTO_DETECT')
        assert client.get('/api/visualizer/capabilities').get_json()['auto_detect'] is False
        assert client.post(endpoint, json={}).status_code == 503
        monkeypatch.setenv('EXTERIOR_AUTO_DETECT', '1')
        assert client.post(endpoint, json={'role': 'other'}).status_code == 400
        assert client.post(endpoint, json={'role': 'roof', 'photo_key': 'photo1', 'image': 'bad'}).status_code == 400
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_detection_queue_ticket_scope_throttle_and_mask_round_trip(client, detector, monkeypatch):
    from PIL import Image
    uri = _image_uri(Image.new('RGB', (4, 3), 'white'))
    rid = 'test-request'
    root = 'https://queue.fal.run/fal-ai/sam-3/requests/' + rid
    calls = []

    def fake_json(method, url, payload=None):
        calls.append((method, url, payload))
        if method == 'POST':
            assert payload['prompt'] == 'roof'
            assert payload['sync_mode'] is True and payload['apply_mask'] is False
            return {'request_id': rid, 'status_url': root + '/status', 'response_url': root}
        if url.endswith('/status'):
            return {'status': 'COMPLETED'}
        return {'masks': [{'url': uri}], 'scores': [0.95]}

    monkeypatch.setattr(detector, '_json', fake_json)
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    other = client.post('/api/estimates', json={}).get_json()['estimate_id']
    endpoint = f'/api/estimates/{eid}/visualizer/detection'
    body = {'role': 'roof', 'photo_key': 'photo1', 'image': uri}
    try:
        response = client.post(endpoint, json=body)
        assert response.status_code == 202, response.get_json()
        ticket = response.get_json()['ticket']
        assert 'test-only-not-a-live-key' not in ticket
        assert client.get(endpoint, query_string={'ticket': ticket + 'bad'}).status_code == 400
        assert client.get(f'/api/estimates/{other}/visualizer/detection', query_string={'ticket': ticket}).status_code == 403
        result = client.get(endpoint, query_string={'ticket': ticket}).get_json()
        assert result['status'] == 'complete' and result['photo_key'] == 'photo1' and result['role'] == 'roof'
        mask = client.post(f'/api/estimates/{eid}/visualizer/asset', json={
            'kind': 'mask', 'role': 'roof', 'ext': 'png', 'content_b64': result['mask']})
        assert mask.status_code == 201
        # A normal estimate save (or hostile stale client) cannot reset the
        # submission cooldown and turn a click into unlimited paid requests.
        client.put(f'/api/estimates/{eid}', json={'_visualizer_detection_attempts': {'roof': 1}})
        assert client.post(endpoint, json=body).status_code == 429
        assert len([c for c in calls if c[0] == 'POST']) == 1
    finally:
        client.delete(f'/api/estimates/{eid}')
        client.delete(f'/api/estimates/{other}')


def test_detection_never_follows_untrusted_queue_urls(detector, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(detector, '_json', lambda *a, **kw: {
        'request_id': 'test', 'status_url': 'https://evil.invalid/status', 'response_url': 'https://evil.invalid/result'})
    with pytest.raises(detector.DetectionError):
        detector.submit('roof', _image_uri(Image.new('RGB', (2, 2), 'white')))


def test_dropdowns_use_live_catalog_and_ignore_stale_detection_in_node():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for designer UI behavior checks')
    script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const src = fs.readFileSync(process.argv[1], 'utf8');
const from = src.indexOf('const _SIDING_PATTERN_SVG =');
const to = src.indexOf('// ── Service Worker registration', from);
const picker = {innerHTML: ''};
const doc = {getElementById: id => id === 'vz-picker-body' ? picker : null, querySelector:()=>null,
  querySelectorAll: () => [], createElement: () => ({getContext: () => ({drawImage(){}}), toDataURL: () => 'data:image/jpeg;base64,abcd'})};
const context = vm.createContext({assert, document: doc, setTimeout: fn => fn(),
  S: {estimate_id:'a', trades:{roofing:{tier_bundles:{better:'chosen'}}}},
  priceBook: {roofing_tier_defaults:{good:'default',better:'default',best:'default'},
    roofing_bundles:[{id:'default',name:'Default',product_ids:['m_default']},{id:'chosen',name:'Quoted',product_ids:['m_chosen']}],
    roofing_catalog:[{id:'m_default', colors:[{name:'White',hex:'#ffffff'}]}, {id:'m_chosen', colors:[{name:'Black',hex:'#222222'}]}],
    exterior_doors:[{id:'provia-signet',name:'ProVia Signet',preview_only:true,colors:[{name:'Snow Mist',hex:'#eeeeee'}]}]},
  TIERS:['good','better','best'], setDirty(){}, esc:s=>String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;'),
  confirm:()=>true, alert:()=>{}});
vm.runInContext(src.slice(from,to),context);
vm.runInContext(`
  _vzResetState();
  assert.equal(_vzBundleFor('roofing','better').id, 'chosen');
  _vzRenderPicker();
  assert.equal(_vzGet().selections.roofing.good.color_name, 'White');
  assert.equal(_vzGet().selections.roofing.better.color_name, 'Black');
  assert(document.getElementById('vz-picker-body').innerHTML.includes('<select'));
  assert(document.getElementById('vz-picker-body').innerHTML.includes('ProVia Signet'));
  _vzChooseBundle('roofing','default');
  assert.equal(S.trades.roofing.tier_bundles.better, 'chosen'); // no pricing mutation
  _vzPickDoorOption('provia-signet');
  assert.equal(_vzGet().selections.doors.better.preview_only, true);
  assert.equal(_vzGet().selections.doors.better.pattern_id, '');
  _vzPickDoorOption('');
  assert.equal(_vzGet().selections.doors.better, undefined);
  globalThis.oldState = vzState;
  vzState.photoKey = 'first';
  assert(_vzIsCurrent(vzState,'first'));
  vzState.photoKey = 'second';
  assert(!_vzIsCurrent(vzState,'first'));
  S = {estimate_id:'b'};
  assert(!_vzIsCurrent(oldState,'second'));
  _vzResetState();
  assert.equal(vzState.roofMask, null);
`,context);
// Exercise the asynchronous result boundary: another photo becomes current
// while the submit request is in flight. No polling or pixel write may follow.
vm.runInContext(`
  vzCapabilities = {auto_detect:true};
  vzState.photoImg = {}; vzState.canvas = {width:10,height:10};
  vzState.photoKey = 'test-photo';
  globalThis.submits = 0;
  _vzDetectionJSON = async () => { submits++; S = {estimate_id:'c'}; return {ticket:'ticket'}; };
  _vzRedrawAll = () => {};
  _vzDetectionUI = () => {};
  globalThis.finished = _vzAutoDetect();
`,context);
context.finished.then(() => {
  assert.equal(context.S.estimate_id,'c');
  assert.equal(context.submits,3);
}).catch(e => { console.error(e); process.exitCode=1; });
"""
    source = Path(__file__).resolve().parents[1] / 'static' / 'app.js'
    result = subprocess.run([node, '-e', script, str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_state_endpoint_updates_selections_only(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        # Seed a tier_renders pointer that must NOT get wiped when the
        # rep PUTs a fresh selections dict.
        doc = client.get(f'/api/estimates/{eid}').get_json()
        doc['visualizer'] = {'tier_renders': {'good': f'{eid}/vr_good.jpg'}}
        client.put(f'/api/estimates/{eid}', json=doc)

        r = client.put(
            f'/api/estimates/{eid}/visualizer/state',
            json={'selections': {'roofing': {'good': {'color_hex': '#333333'}}}},
        )
        assert r.status_code == 200
        fresh = client.get(f'/api/estimates/{eid}').get_json()
        assert fresh['visualizer']['selections']['roofing']['good']['color_hex'] == '#333333'
        # tier_renders survived — selections doesn't touch it.
        assert fresh['visualizer']['tier_renders']['good'] == f'{eid}/vr_good.jpg'
    finally:
        client.delete(f'/api/estimates/{eid}')


# ── PDF integration ────────────────────────────────────────────────────

def _minimal_signed_estimate(eid):
    """Just enough of an estimate to make build_signed_pdf produce output."""
    return {
        'estimate_id': eid,
        'estimate_type': 'retail',
        'customer': {'name': 'Ada Lovelace',
                     'address': {'street': '1 Analytical Engine',
                                 'city': 'Tyler', 'state': 'TX', 'zip': '75701'}},
        'salesperson': 'luke',
        'estimate_date': '2026-08-04',
        'trades': {'roofing': {'enabled': True, 'selected_tier': 'better',
                               'line_items': [{'name': 'Landmark shingle',
                                              'quantity': 30, 'unit': 'SQ',
                                              'tiers': {'better': {'included': True,
                                                                   'material_unit_cost': 142}}}]}},
        'pricing': {'mode': 'margin', 'margin_pct': 25},
        'selected_tier': 'better',
        'signature': {'name': 'Ada Lovelace',
                      'signed_at': '2026-08-04T12:00:00Z',
                      'ip_address': '127.0.0.1',
                      'document_hash': 'a' * 64,
                      'shingle_color': 'Charcoal Black'},
    }


def test_signed_pdf_embeds_the_render_when_present(client, A):
    """A tier render on the estimate must add a page to the signed PDF —
    the whole point of the feature is that it prints on the contract."""
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        est_no_vz = _minimal_signed_estimate(eid)
        base_bytes = A.build_signed_pdf(est_no_vz)

        # Write a real 1px JPEG into UPLOADS_DIR the way the asset
        # endpoint would, then reference it from the estimate.
        os.makedirs(os.path.join(TEST_DATA_DIR, 'uploads', eid), exist_ok=True)
        render_name = f'{eid}/vr_test_signed.jpg'
        with open(os.path.join(TEST_DATA_DIR, 'uploads', render_name), 'wb') as f:
            f.write(base64.b64decode(_ONE_PX_JPEG_B64))
        est_with_vz = _minimal_signed_estimate(eid)
        est_with_vz['visualizer'] = {
            'tier_renders': {'better': render_name},
            'selections': {'roofing': {'better': {'color_name': 'Charcoal Black'}}},
        }
        with_bytes = A.build_signed_pdf(est_with_vz)
        assert len(with_bytes) > len(base_bytes) + 200, (
            f'expected the visualizer page to add bytes; base={len(base_bytes)} '
            f'with={len(with_bytes)}')
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_signed_pdf_skips_visualizer_page_when_no_renders(A):
    """No renders saved = no visualizer page, no error. Otherwise a fresh
    estimate that never touched the tab would blow up on print."""
    est = _minimal_signed_estimate('e_no_vz')
    # No visualizer key at all — must still build.
    A.build_signed_pdf(est)
    # Empty tier_renders — same.
    est['visualizer'] = {'tier_renders': {}}
    A.build_signed_pdf(est)


# ── /sign page integration ─────────────────────────────────────────────

def test_sign_page_shows_visualizer_img_when_renders_are_saved(client, A):
    """When the estimate carries tier_renders, the customer's /sign page
    embeds an <img> tag pointing at /uploads/... so the customer sees the
    rendering before signing."""
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        # Fill out enough estimate data to render a customer view.
        doc = client.get(f'/api/estimates/{eid}').get_json()
        doc['customer'] = {'name': 'Ada Lovelace',
                           'address': {'street': '1 Analytical Engine',
                                       'city': 'Tyler', 'state': 'TX'}}
        doc['estimate_type'] = 'retail'
        doc['salesperson'] = 'luke'
        doc['trades'] = {'roofing': {'enabled': True, 'selected_tier': 'better',
                                     'line_items': [{'name': 'x', 'quantity': 1, 'unit': 'SQ',
                                                     'tiers': {'better': {'included': True,
                                                                          'material_unit_cost': 100}}}]}}
        doc['selected_tier'] = 'better'
        # Save it and mint a share token by posting to the share endpoint.
        assert client.put(f'/api/estimates/{eid}', json=doc).status_code == 200

        # Write a render to disk + point est.visualizer at it.
        os.makedirs(os.path.join(TEST_DATA_DIR, 'uploads', eid), exist_ok=True)
        render_name = f'{eid}/vr_sign_page.jpg'
        with open(os.path.join(TEST_DATA_DIR, 'uploads', render_name), 'wb') as f:
            f.write(base64.b64decode(_ONE_PX_JPEG_B64))

        doc = client.get(f'/api/estimates/{eid}').get_json()
        doc['visualizer'] = {
            'tier_renders': {'better': render_name},
            'selections': {'roofing': {'better': {'color_name': 'Charcoal Black'}},
                           'siding':  {'better': {'color_name': 'Iron Gray',
                                                  'style_name': 'Lap Siding'}}},
        }
        client.put(f'/api/estimates/{eid}', json=doc)

        # Get share link via the send-preview endpoint or by peeking at
        # the share_token field. Simpler: use the /api/estimates/<id>/share
        # endpoint if it exists, else construct from the token in the doc.
        share = client.post(f'/api/estimates/{eid}/share')
        assert share.status_code in (200, 201)
        token = share.get_json().get('token') or share.get_json().get('share_token')
        assert token

        page = client.get(f'/sign/{token}')
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert render_name in html or f'/uploads/{render_name}' in html
        # And the tier label appears near the render, plus the color caption.
        assert 'See It on Your Home' in html
        assert 'Charcoal Black' in html
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_sign_page_omits_visualizer_when_no_renders(client, A):
    """Empty tier_renders must not emit a stub section — the customer
    shouldn't see a 'See It on Your Home' header pointing at nothing."""
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        doc = client.get(f'/api/estimates/{eid}').get_json()
        doc['customer'] = {'name': 'Ada', 'address': {'street': '1 St'}}
        doc['estimate_type'] = 'retail'
        doc['salesperson'] = 'luke'
        client.put(f'/api/estimates/{eid}', json=doc)
        share = client.post(f'/api/estimates/{eid}/share')
        token = share.get_json().get('token') or share.get_json().get('share_token')
        page = client.get(f'/sign/{token}')
        assert page.status_code == 200
        assert 'See It on Your Home' not in page.get_data(as_text=True)
    finally:
        client.delete(f'/api/estimates/{eid}')
