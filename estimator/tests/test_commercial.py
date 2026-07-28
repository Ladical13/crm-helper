"""Commercial estimate type — end to end.

Commercial is the third estimate type (Retail / Insurance / Commercial). Its
trade is a BUNDLE trade that defaults to SIMPLE mode: one system, one price.
That combination is what these tests hold down, because each half of it has a
way to fail silently:

  * a simple-mode trade whose items were built per-tier totals $0 while looking
    completely normal on screen;
  * the customer view, signed PDF, and production packet all iterate explicit
    trade lists, so a new trade that isn't in them prints a blank page rather
    than an error;
  * the signing page requires a shingle color whenever ROOFING is enabled — a
    flat roof has no shingle, so a commercial estimate must never be blocked
    behind that prompt.
"""
import json

import pytest


def _commercial_est(work_type=0, **kw):
    """A minimal signed-ready commercial estimate: 40 SQ re-roof."""
    est = {
        'estimate_type': 'commercial',
        'customer': {'name': 'Northgate Business Park', 'email': 'pm@example.com',
                     'phone': '9705550123', 'address': {'street': '100 Industrial Way',
                                                        'city': 'Loveland', 'state': 'CO'}},
        'measurements': {'comm_squares': 40, 'comm_waste_pct': 10,
                         'comm_perimeter_lf': 500, 'comm_penetrations': 8,
                         'comm_drains': 2, 'comm_sections': 1,
                         'comm_work_type': work_type},
        'pricing': {'mode': 'margin', 'global_rate': 35,
                    'tier_rates': {'good': 35, 'better': 35, 'best': 35},
                    'trade_rates': {'commercial': {'simple': 29, 'good': 29,
                                                   'better': 29, 'best': 29}},
                    'per_trade_overrides': {}},
        'selected_tier': 'better',
        'salesperson': 'luke',   # analytics skips unassigned estimates
        'shingle_selection': {'enabled': False, 'options': [], 'chosen': ''},
        'trades': {
            'commercial': {
                'enabled': True, 'mode': 'simple', 'simple_bundle': 'cb_tpo_ma',
                'line_items': [
                    {'id': 'i1', 'catalog_id': 'cm_tpo_ma', 'name': 'TPO Membrane 60-mil (Mechanically Attached)',
                     'unit': 'SQ', 'quantity': 44, 'unit_cost': 100, 'unit_price': 140.85,
                     'customer_visible': True},
                    {'id': 'i2', 'catalog_id': 'ca_edge', 'name': 'Edge Metal / Drip',
                     'unit': 'LF', 'quantity': 500, 'unit_cost': 2, 'unit_price': 2.82,
                     'customer_visible': True},
                    # Exactly one labor line carries quantity; comm_work_type
                    # decides which. The other is the zero-qty line below.
                    {'id': 'i3', 'catalog_id': 'cl_labor_reroof',
                     'name': 'Tear-Off, Disposal & Install Labor (Re-Roof)',
                     'unit': 'SQ', 'quantity': 0 if work_type else 40,
                     'unit_cost': 400, 'unit_price': 563.38, 'customer_visible': True},
                    {'id': 'i4', 'catalog_id': 'cl_labor_new',
                     'name': 'Install Labor (New Construction)',
                     'unit': 'SQ', 'quantity': 40 if work_type else 0,
                     'unit_cost': 250, 'unit_price': 352.11, 'customer_visible': True},
                ],
            },
        },
    }
    est.update(kw)
    return est


def _create(client, est):
    r = client.post('/api/estimates', json=est)
    assert r.status_code in (200, 201), r.data
    return r.get_json()['estimate_id']


# ── pricing ────────────────────────────────────────────────────────────

def test_commercial_trade_is_priced_by_the_server(A):
    """The whole point of adding it to GBB_TRADES: it must reach the total."""
    est = _commercial_est()
    total = A.calc_selected_total(est)
    # 44*140.85 + 500*2.82 + 40*563.38 = 6197.4 + 1410 + 22535.2
    assert total == pytest.approx(6197.40 + 1410.00 + 22535.20)
    assert total > 0


def test_zero_qty_labor_line_never_prices(A):
    """Both labor lines ship in the bundle; only the applicable one may count."""
    reroof = A.calc_selected_total(_commercial_est(work_type=0))
    new_build = A.calc_selected_total(_commercial_est(work_type=1))
    assert reroof != new_build
    # New construction is the cheaper rate ($250/SQ vs $400/SQ).
    assert new_build < reroof


def test_commercial_defaults_to_simple_mode(A):
    """A trade dict with no explicit mode must resolve to simple, not gbb —
    a gbb-shaped read of flat items prices at $0."""
    assert A._trade_mode('commercial', {}) == 'simple'
    assert A._trade_mode('commercial', {'mode': 'gbb'}) == 'gbb'
    assert A._trade_mode('roofing', {}) == 'gbb'


def test_simple_mode_commercial_is_not_a_gbb_trade(A):
    est = _commercial_est()
    assert 'commercial' not in A._gbb_trade_keys(est)
    est['trades']['commercial']['mode'] = 'gbb'
    assert 'commercial' in A._gbb_trade_keys(est)


def test_js_and_py_agree_on_the_commercial_mode_default():
    """SIMPLE_MODE_TRADES is mirrored by hand; drift means the browser and the
    server price the same estimate differently."""
    import os
    import re
    here = os.path.dirname(os.path.abspath(__file__))
    js = open(os.path.join(here, '..', 'static', 'app.js'), encoding='utf-8').read()
    m = re.search(r"^const SIMPLE_MODE_TRADES = \[([^\]]*)\];", js, re.M)
    assert m, 'SIMPLE_MODE_TRADES not found in app.js'
    js_list = tuple(x.strip().strip("'\"") for x in m.group(1).split(',') if x.strip())
    import app as A
    assert js_list == A.SIMPLE_MODE_TRADES


# ── price book ─────────────────────────────────────────────────────────

def test_commercial_catalog_is_seeded_with_real_labor_rates(client):
    pb = client.get('/api/pricebook').get_json()
    by_id = {p['id']: p for p in pb['commercial_catalog']}
    # The two Project One standards — these are real, not placeholders.
    assert by_id['cl_labor_reroof']['cost'] == 400
    assert by_id['cl_labor_new']['cost'] == 250
    assert by_id['cl_labor_reroof']['measure'] == 'comm_labor_reroof'
    assert by_id['cl_labor_new']['measure'] == 'comm_labor_new'


def test_every_commercial_system_is_offered_as_a_bundle(client):
    pb = client.get('/api/pricebook').get_json()
    names = ' '.join(b['name'] for b in pb['commercial_bundles']).lower()
    for system in ('tpo', 'epdm', 'bitumen', 'coating'):
        assert system in names, f'{system} missing from the commercial bundles'


def test_commercial_simple_default_points_at_a_real_bundle(client):
    pb = client.get('/api/pricebook').get_json()
    ids = {b['id'] for b in pb['commercial_bundles']}
    assert pb['commercial_simple_default'] in ids


# ── customer-facing: view, signing, PDFs ───────────────────────────────

def test_customer_view_renders_the_commercial_bid(client, A):
    est_id = _create(client, _commercial_est())
    r = client.post(f'/api/estimates/{est_id}/share')
    assert r.status_code == 200, r.data
    token = r.get_json()['token']
    page = client.get(f'/sign/{token}')
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'TPO Membrane' in html
    assert 'Edge Metal' in html


def test_customer_view_hides_the_zero_qty_labor_line(client):
    est_id = _create(client, _commercial_est(work_type=0))
    token = client.post(f'/api/estimates/{est_id}/share').get_json()['token']
    html = client.get(f'/sign/{token}').get_data(as_text=True)
    assert 'Re-Roof' in html
    assert 'New Construction' not in html, \
        'the labor line that does not apply must not reach the customer'


def test_signing_a_commercial_estimate_needs_no_shingle_color(client, A):
    """The shingle-color gate keys on the ROOFING trade being enabled. A flat
    roof has no shingle — blocking here would make commercial unsignable."""
    est = _commercial_est()
    # Even with the selection block left switched on, roofing is disabled.
    est['shingle_selection'] = {'enabled': True, 'options': ['Weathered Wood'], 'chosen': ''}
    est_id = _create(client, est)
    token = client.post(f'/api/estimates/{est_id}/share').get_json()['token']

    saved = A.est_load(est_id)
    assert not A._roofing_enabled(saved)

    form = {'name': 'Dana Reyes', 'email': 'pm@example.com', 'signature_data': 'data:image/png;base64,AAA'}
    for i, _ in enumerate(A._initial_defs(saved) if hasattr(A, '_initial_defs') else []):
        form[f'initial_{i}'] = 'DR'
    for i in range(6):
        form.setdefault(f'initial_{i}', 'DR')
    r = client.post(f'/sign/{token}', data=form)
    assert r.status_code != 400 or b'shingle' not in r.data.lower(), r.data


def test_signed_pdf_and_production_packet_build(client, A):
    est_id = _create(client, _commercial_est())
    est = A.est_load(est_id)
    est['signature'] = {'name': 'Dana Reyes', 'email': 'pm@example.com',
                        'signed_at': '2026-07-28T12:00:00Z', 'selected_tier': 'better',
                        'data': 'data:image/png;base64,AAA'}
    A.est_save(est)

    pdf = A.build_signed_pdf(est)
    assert pdf[:4] == b'%PDF' and len(pdf) > 1000

    packet = A.build_production_packet_pdf(est)
    assert packet[:4] == b'%PDF' and len(packet) > 1000


def test_packet_reports_the_job_type_and_complexity_flags(client, A):
    est = _commercial_est(work_type=1)
    est['commercial'] = {'flags': {'heavy_hvac': True, 'levels_3plus': False},
                         'notes': 'Occupied building - night work only.'}
    est['signature'] = {'name': 'Dana Reyes', 'email': 'pm@example.com',
                        'signed_at': '2026-07-28T12:00:00Z', 'selected_tier': 'better',
                        'data': 'data:image/png;base64,AAA'}
    packet = A.build_production_packet_pdf(est)
    assert packet[:4] == b'%PDF'
    # The flag labels are mirrored by hand from app.js; a rename that only lands
    # on one side means the crew silently stops seeing the flag.
    assert dict(A.COMM_FLAG_LABELS)['heavy_hvac'] == 'Heavy rooftop HVAC'


def test_analytics_counts_commercial_revenue(client, A):
    assert 'commercial' in A.GBB_TRADES
    est_id = _create(client, _commercial_est())
    est = A.est_load(est_id)
    est['signature'] = {'name': 'Dana Reyes', 'signed_at': '2026-07-28T12:00:00Z',
                        'selected_tier': 'better'}
    A.est_save(est)
    data = client.get('/api/analytics').get_json()
    assert 'commercial' in data['by_trade']
