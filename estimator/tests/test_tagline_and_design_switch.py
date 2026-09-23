"""The package tagline a rep can actually change, and the Design Studio toggle.

Tagline. A bundle pick copies the price book's tagline into the estimate, so a
manager editing the book never reached an estimate that already had one, and
the only per-estimate editor (the Options tab) was retired. Worse, on a Custom
tier the staleness rule threw away anything stored, so there was no way to put
a line on that card at all. The Pricing tab now has a box per package; what the
rep types there is flagged `tier_tagline_edited` and survives the stale rule.

Design Studio. Still being built, so each estimate carries a 🎨 Design Studio
section toggle (`page_visibility.design`) beside its other Print Pages chips —
and unlike them it defaults OFF. It gates the /sign block, the signed PDF page,
the /design link and minting that link.
"""
import base64
import io
import os
import re

from PIL import Image


HERE = os.path.dirname(os.path.abspath(__file__))
APP_JS = os.path.join(HERE, '..', 'static', 'app.js')


def _js():
    with open(APP_JS, encoding='utf-8') as fh:
        return fh.read()


def _cell(cost=200, included=True):
    return {'material_unit_cost': cost, 'labor_unit_cost': 0,
            'description': '', 'notes': '', 'included': included}


def _custom_est(edited):
    return {
        'estimate_type': 'retail', 'salesperson': 'luke', 'selected_tier': 'better',
        'customer': {'name': 'Tess Tagline', 'email': 't@example.com',
                     'address': {'street': '1 Card Rd', 'city': 'Golden', 'state': 'CO'}},
        'pricing': {'mode': 'margin', 'global_rate': 35, 'tier_rates': {},
                    'trade_rates': {}, 'per_trade_overrides': {}},
        'trades': {'roofing': {
            'enabled': True, 'mode': 'gbb', 'selected_tier': 'better',
            'tier_bundles': {'good': '', 'better': '__custom__', 'best': ''},
            'tier_features': {'good': [], 'better': [], 'best': []},
            'tier_descriptions': {'good': '', 'better': 'ZZ-REP-TAGLINE', 'best': ''},
            'tier_tagline_edited': {'better': edited},
            'line_items': [{'id': 'h1', 'name': 'Rolled Roofing', 'unit': 'SQ',
                            'quantity': 10, 'customer_visible': True,
                            'tiers': {'good': _cell(included=False), 'better': _cell(700),
                                      'best': _cell(included=False)}}],
        }},
    }


def _sign_page(client, est):
    est_id = client.post('/api/estimates', json=est).get_json()['estimate_id']
    token = client.post(f'/api/estimates/{est_id}/share').get_json()['token']
    page = client.get(f'/sign/{token}')
    assert page.status_code == 200
    return est_id, token, page.get_data(as_text=True)


# ── Tagline ─────────────────────────────────────────────────────────────────

def test_a_tagline_the_rep_typed_survives_a_custom_tier(client):
    est_id, _, html = _sign_page(client, _custom_est(True))
    try:
        assert 'ZZ-REP-TAGLINE' in html
    finally:
        client.delete(f'/api/estimates/{est_id}')


def test_leftover_bundle_copy_on_a_custom_tier_still_drops(client):
    est_id, _, html = _sign_page(client, _custom_est(False))
    try:
        assert 'ZZ-REP-TAGLINE' not in html
    finally:
        client.delete(f'/api/estimates/{est_id}')


def test_only_a_literal_true_marks_a_tagline_as_the_reps(A):
    for junk in ('yes', 1, None, 'true'):
        assert A._tier_tagline_edited(_custom_est(junk), 'roofing', 'better') is False
    assert A._tier_tagline_edited(_custom_est(True), 'roofing', 'better') is True


def test_seed_taglines_are_one_short_line(A):
    for b in A.ROOFING_BUNDLES_SEED:
        assert len(b['description']) <= 90, f"{b['id']} tagline is a paragraph again"
    assert 'Class 3' not in next(b['description'] for b in A.ROOFING_BUNDLES_SEED
                                 if b['id'] == 'b_landmark')


def test_an_untouched_old_seed_tagline_moves_to_the_short_one(A):
    old = A._BUNDLE_DESCRIPTION_MIGRATIONS['roofing']['b_iko_nordic'][0]
    pb = A._ensure_bundle_catalogs(A._load_price_book())
    live = next(b for b in pb['roofing_bundles'] if b['id'] == 'b_iko_nordic')
    live['description'] = old
    A._ensure_bundle_catalogs(pb)
    assert live['description'] == next(b['description'] for b in A.ROOFING_BUNDLES_SEED
                                       if b['id'] == 'b_iko_nordic')


def test_a_managers_own_tagline_is_never_migrated(A):
    pb = A._ensure_bundle_catalogs(A._load_price_book())
    live = next(b for b in pb['roofing_bundles'] if b['id'] == 'b_northgate')
    live['description'] = 'Our best hail roof.'
    A._ensure_bundle_catalogs(pb)
    assert live['description'] == 'Our best hail roof.'


def test_every_migrated_bundle_is_still_seeded(A):
    ids = {b['id'] for b in A.ROOFING_BUNDLES_SEED}
    assert set(A._BUNDLE_DESCRIPTION_MIGRATIONS['roofing']) <= ids


def test_the_pricing_tab_has_a_tagline_box_per_package():
    js = _js()
    assert '${tierTaglineEditorHtml(trade, t)}' in js
    m = re.search(r'function setTierTagline\(trade, tier, v\).*?\n\}', js, re.S)
    assert m and 'td.tier_tagline_edited[tier] = !!text;' in m.group(0)
    m = re.search(r'function resetTierTagline\(trade, tier\).*?\n\}', js, re.S)
    assert m and 'priceBookTagline(trade, tier)' in m.group(0)


def test_repicking_a_bundle_hands_the_tagline_back_to_the_bundle():
    m = re.search(r'function applyBundleToTier\(trade, tier, bundleId, autoOpen\).*?\n\}',
                  _js(), re.S)
    body = m.group(0)
    assert body.count('td.tier_tagline_edited[tier] = false') == 2, (
        'both a bundle pick and a switch to Custom must clear the rep flag')


# ── Design Studio: a per-estimate section toggle, default OFF ──────────────

def _jpeg_bytes():
    out = io.BytesIO()
    Image.new('RGB', (4, 4), (90, 90, 90)).save(out, 'JPEG')
    return out.getvalue()


def _set_design(client, eid, on):
    doc = client.get(f'/api/estimates/{eid}').get_json()
    doc['page_visibility'] = dict(doc.get('page_visibility') or {}, design=on)
    assert client.put(f'/api/estimates/{eid}', json=doc).status_code == 200


def test_the_toggle_defaults_off(A):
    assert A._design_studio_customer_on({}) is False
    assert A._design_studio_customer_on({'page_visibility': None}) is False
    assert A._design_studio_customer_on({'page_visibility': {'intro': True}}) is False
    assert A._design_studio_customer_on({'page_visibility': {'design': 'true'}}) is False, (
        'only a literal True turns it on')
    assert A._design_studio_customer_on({'page_visibility': {'design': True}}) is True


def test_the_sign_page_shows_renders_only_when_the_estimate_says_so(client, A):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        doc = client.get(f'/api/estimates/{eid}').get_json()
        doc.update({'customer': {'name': 'Ada', 'address': {'street': '1 St', 'city': 'Tyler',
                                                            'state': 'TX'}},
                    'estimate_type': 'retail', 'salesperson': 'luke', 'selected_tier': 'better',
                    'trades': {'roofing': {'enabled': True, 'selected_tier': 'better',
                                           'line_items': [{'name': 'x', 'quantity': 1, 'unit': 'SQ',
                                                           'tiers': {'better': {'included': True,
                                                                                'material_unit_cost': 100}}}]}}})
        os.makedirs(os.path.join(A.UPLOADS_DIR, eid), exist_ok=True)
        render = f'{eid}/vr_switch.jpg'
        with open(os.path.join(A.UPLOADS_DIR, render), 'wb') as f:
            f.write(_jpeg_bytes())
        doc['visualizer'] = {'tier_renders': {'better': render}}
        assert client.put(f'/api/estimates/{eid}', json=doc).status_code == 200
        token = client.post(f'/api/estimates/{eid}/share').get_json()['token']

        assert 'See It on Your Home' not in client.get(f'/sign/{token}').get_data(as_text=True), (
            'an estimate that never touched the chip must not show the studio')
        _set_design(client, eid, True)
        assert 'See It on Your Home' in client.get(f'/sign/{token}').get_data(as_text=True)
        _set_design(client, eid, False)
        assert 'See It on Your Home' not in client.get(f'/sign/{token}').get_data(as_text=True)
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_sharing_and_the_review_link_follow_the_estimates_toggle(client, anon):
    eid = client.post('/api/estimates', json={
        'page_visibility': {'design': True},
    }).get_json()['estimate_id']
    try:
        assert client.post(f'/api/estimates/{eid}/visualizer/asset', json={
            'kind': 'render', 'tier': 'better', 'elevation_id': 'front',
            'elevation_name': 'Front', 'ext': 'jpg',
            'content_b64': base64.b64encode(_jpeg_bytes()).decode('ascii'),
        }).status_code == 201
        token = client.post(f'/api/estimates/{eid}/visualizer/share').get_json()['token']
        assert anon.get(f'/design/{token}').status_code == 200

        _set_design(client, eid, False)
        refused = client.post(f'/api/estimates/{eid}/visualizer/share')
        assert refused.status_code == 403
        assert 'Design Studio' in refused.get_json()['error']
        assert anon.get(f'/design/{token}').status_code == 404, (
            'a link already sent must stop opening when the section goes off')
        assert anon.post(f'/design/{token}', data={'approved_tier': 'better'}).status_code == 404
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_the_signed_pdf_skips_renders_when_off(A):
    est = {'visualizer': {'tier_renders': {'better': 'x/y.jpg'}}}
    # pdf=None: touching it at all would raise, so returning is the proof.
    assert A._emit_visualizer_pdf_page(None, est, 0, 0) is None


def test_the_chip_sits_in_the_print_pages_bar_and_defaults_off():
    js = _js()
    m = re.search(r'function renderPrintPagesBar\(\).*?\n\}', js, re.S)
    assert m and "id:'design'" in m.group(0)
    assert 'pv.design === true' in m.group(0), 'the chip must read OFF when the key is absent'
    m = re.search(r'function togglePagePrint\(page\).*?\n\}', js, re.S)
    assert m and 'PAGE_DEFAULT_OFF' in m.group(0), (
        'a default-off chip toggled with the default-on formula needs two clicks to turn on')


def test_the_share_button_follows_the_estimate_not_a_global_setting():
    js = _js()
    assert 'design_studio_customer' not in js, 'the global Settings switch is gone'
    assert "'settings-design'" not in js
    assert '(S.page_visibility || {}).design === true' in js
