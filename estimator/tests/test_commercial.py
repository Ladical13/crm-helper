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


# ── GAF price sheet, quoted 2026-05-19 ─────────────────────────────────
#
# The commercial catalog shipped entirely unpriced, waiting on a supplier
# sheet. These hold down the conversion from that sheet's roll/box pricing to
# the catalog's SQ/LF/EA pricing, and — more importantly — the two ways a
# priced catalog can still quietly bid $0.


def _cat(client):
    return {p['id']: p for p in client.get('/api/pricebook').get_json()['commercial_catalog']}


def test_membranes_carry_the_sheet_price_per_square(client):
    """A 10'x100' roll is 1,000 sf = 10 SQ, so the per-SQ cost is the roll
    price / 10. Each sheet's narrower roll is the check."""
    cat = _cat(client)
    # Carlisle, 2026-08-19 — the priced supplier for both membranes.
    assert cat['cm_tpo_ma']['cost'] == 80.68       # 806.82 / 10 (6'x100' agrees)
    assert cat['cm_epdm_fa']['cost'] == 96.59      # 965.91 / 10 (10'x50' agrees)
    assert cat['cm_epdm_fa_taped']['cost'] == 104.55   # 1,045.45 / 10, tape included
    # Fully adhered is the same roll as mechanically fastened; the difference
    # is the adhesive line, not the membrane.
    assert cat['cm_tpo_fa']['cost'] == cat['cm_tpo_ma']['cost']
    # GAF specialty membranes Carlisle did not quote. Still priced off the
    # EXPIRED 2026-05-19 sheet — kept to scope with, re-quote before selling.
    assert cat['cm_tpo45_ma']['cost'] == 72.73     # 727.27 / 10
    assert cat['cm_tpo80_ma']['cost'] == 135.23    # 1,352.27 / 10
    assert cat['cm_tpo_sa']['cost'] == 172.16      # 1,721.59 / 10
    assert cat['cm_tpo_fb60']['cost'] == 139.21    # 1,392.05 / 10


def test_polyiso_is_priced_at_every_thickness_the_sheet_lists(client):
    """Polyiso is already quoted per SQ, so these are lifted straight across.
    R-value is spec-driven, so a rep has to be able to pick the right board."""
    cat = _cat(client)
    # Carlisle quoted 2.0" and 2.6" plus the 1/2" HD cover board.
    assert cat['ca_iso_20']['cost'] == 100.00
    assert cat['ca_iso']['cost'] == 130.00     # 2.6", the default
    assert cat['ca_cover']['cost'] == 96.34    # 1/2" SecureShield HD
    # The rest are GAF thicknesses Carlisle did not quote, off the expired sheet.
    assert cat['ca_iso_10']['cost'] == 65.34
    assert cat['ca_iso_15']['cost'] == 74.15
    assert cat['ca_iso_22']['cost'] == 108.75
    assert cat['ca_iso_30']['cost'] == 148.30
    assert cat['ca_iso_40']['cost'] == 197.73
    for pid in ('ca_iso', 'ca_iso_10', 'ca_iso_40'):
        assert cat[pid]['measure'] == 'comm_sq_waste'
        assert cat[pid]['unit'] == 'SQ'


def test_every_priced_line_that_drives_a_measurement_actually_has_a_cost(client):
    """The failure this exists for: a catalog full of real-looking line items
    where one is still 0, so the bid prints complete and comes in short. Every
    product wired to a MEASUREMENT must be priced — the only unpriced lines
    allowed are the lump sums a rep fills in per job, the superseded legacy
    fastener line, and the three membranes this sheet does not cover."""
    cat = _cat(client)
    # This set is a live checklist, asserted EXACTLY rather than as a floor.
    # A new unpriced line fails it; so does filling one of these in without
    # deleting it here. That is the point - the list is meant to shrink.
    awaiting_quote = {
        # Superseded by the two zone-calculated fastener lines.
        'ca_fasteners',
        # Carlisle's 2026-08-19 quote lists only NON-reinforced EPDM, which is
        # specified for adhered and ballasted assemblies. A mechanically
        # fastened EPDM roof needs the reinforced sheet (Sure-Tough), and that
        # is not on the quote - so this package still has no membrane price.
        'cm_epdm_mf',
        # The legacy generic EPDM line, superseded by the fastened/adhered pair.
        'cm_epdm',
        # Never quoted by either house.
        'cm_modbit', 'cm_coating',
        # No 1/4" board on the Carlisle sheet either - its 1/4" entries are
        # TAPERED panels, where 1/4" is the slope per foot, not the thickness.
        'ca_cover_quarter',
        # Layover labor: the four rates Luke is supplying. Until they land, a
        # layover bid prices its materials and none of its work.
        'cl_tpo_lo_mf', 'cl_tpo_lo_fa', 'cl_epdm_lo_mf', 'cl_epdm_lo_fa',
    }
    unpriced = {p['id'] for p in cat.values() if p.get('measure') and not p.get('cost')}
    assert unpriced == awaiting_quote, (
        f'newly unpriced: {sorted(unpriced - awaiting_quote)}; '
        f'now priced, remove from the list: {sorted(awaiting_quote - unpriced)}')


def test_no_default_tier_sells_a_membrane_that_costs_nothing(client):
    """EPDM, mod-bit and coating are real bundles a rep can pick, but they are
    not on this sheet. Pointing a DEFAULT at one ships a bid with no membrane
    in it and nothing on screen says so."""
    pb = client.get('/api/pricebook').get_json()
    cat = {p['id']: p for p in pb['commercial_catalog']}
    bundles = {b['id']: b for b in pb['commercial_bundles']}
    defaults = list(pb['commercial_tier_defaults'].values()) + [pb['commercial_simple_default']]
    for bid in defaults:
        membrane = next(p for p in bundles[bid]['product_ids'] if p.startswith('cm_'))
        assert cat[membrane]['cost'] > 0, f'default bundle {bid} sells an unpriced membrane'


def test_only_adhered_systems_carry_bonding_adhesive(client):
    """A mechanically attached roof welds its seams and screws the membrane
    down — it buys no field adhesive. Carrying the $83/SQ line on an MA bid
    would add thousands of dollars of material that never ships."""
    pb = client.get('/api/pricebook').get_json()
    cat = {p['id']: p for p in pb['commercial_catalog']}
    # Carlisle publishes ~60 sq ft of finished surface per gallon, so a 5-gal
    # pail covers 3 SQ: 188.58 / 3 = 62.86. Same rate on the EPDM adhesive.
    assert cat['ca_adhesive']['cost'] == 62.86
    assert cat['ca_epdm_adhesive']['cost'] == 62.86
    for b in pb['commercial_bundles']:
        membrane = next((p for p in b['product_ids'] if p.startswith('cm_')), None)
        if membrane is None:
            continue
        has_adhesive = 'ca_adhesive' in b['product_ids']
        if cat[membrane].get('attach') == 'mechanical':
            assert not has_adhesive, f'{b["id"]} is mechanically attached but buys adhesive'
        # Self-adhered brings its own bond: the roll is the adhesive.
        if membrane == 'cm_tpo_sa':
            assert not has_adhesive, 'self-adhered TPO must not also buy bonding adhesive'


def test_the_new_tpo_systems_reach_a_book_that_already_has_commercial(A):
    """Same trap the QXO siding line hit: the copy-field backfill reads a
    missing seed id as a deletion, so bundles added after books were saved
    reach nobody unless they are on the late-arrival list."""
    # A book saved BEFORE the tear-off/layover restructure: the three original
    # bundles, each still carrying the single cl_labor_reroof line. Built by
    # hand rather than from the seed, because the seed has since moved on and
    # copying it would test nothing.
    old_shared = ['ca_iso', 'ca_cover', 'ca_fast_insul', 'ca_fast_seam', 'ca_edge',
                  'ca_coping', 'ca_termbar', 'ca_pipe_flash', 'ca_drain', 'ca_curb',
                  'ca_pitchpan', 'ca_walkway', 'cl_labor_reroof', 'cl_labor_new',
                  'cx_misc', 'cx_permit']
    saved = {
        'commercial_catalog': [dict(p) for p in A.COMMERCIAL_CATALOG_SEED
                               if p['id'] in ('cm_tpo_ma', 'cm_tpo_fa', 'cm_epdm')],
        'commercial_bundles': [
            {'id': 'cb_tpo_ma', 'name': 'TPO - Mechanically Attached',
             'product_ids': ['cm_tpo_ma'] + old_shared},
            {'id': 'cb_tpo_fa', 'name': 'TPO - Fully Adhered',
             'product_ids': ['cm_tpo_fa'] + old_shared},
            {'id': 'cb_epdm', 'name': 'EPDM Rubber',
             'product_ids': ['cm_epdm'] + old_shared},
        ],
        'commercial_tier_defaults': dict(A.COMMERCIAL_TIER_DEFAULTS_SEED),
    }
    pb = A._ensure_bundle_catalogs(saved)
    ids = {b['id'] for b in pb['commercial_bundles']}
    for late in ('cb_tpo_lo_mf', 'cb_tpo_lo_fa', 'cb_epdm_mf',
                 'cb_epdm_lo_mf', 'cb_epdm_lo_fa'):
        assert late in ids, f'{late} never reached a book that already had commercial'
    # ...and the products they sell have to arrive with them.
    cat = {p['id']: p for p in pb['commercial_catalog']}
    assert cat['cm_tpo80_ma']['cost'] == 135.23
    assert 'ca_cover_quarter' in cat and 'cm_epdm_mf' in cat
    # The old single labor line must NOT have been joined by a per-package one
    # inside an already-saved bundle - that would bill the tear-off twice.
    saved_tpo = next(b for b in pb['commercial_bundles'] if b['id'] == 'cb_tpo_ma')
    labor = [pid for pid in saved_tpo['product_ids'] if pid.startswith('cl_')]
    assert labor == ['cl_labor_reroof', 'cl_labor_new'], labor


def test_placeholder_zero_costs_are_filled_but_real_ones_are_left_alone(A):
    """The commercial catalog seeded at 0 pending a supplier sheet, so a live
    book's 0 means 'never priced', not 'priced at nothing' — the backfill has
    to reach it or the sheet never leaves the repo. It must stay strictly
    one-directional: a number the manager set is theirs."""
    saved = {
        'commercial_catalog': [
            {'id': 'cm_tpo_ma', 'name': 'TPO', 'unit': 'SQ', 'cost': 0},        # placeholder
            {'id': 'ca_iso', 'name': 'Polyiso', 'unit': 'SQ', 'cost': 99.00},   # manager's
            {'id': 'cl_labor_reroof', 'name': 'Labor', 'unit': 'SQ', 'cost': 450},
        ],
        'commercial_bundles': [dict(b) for b in A.COMMERCIAL_BUNDLES_SEED
                               if b['id'] == 'cb_tpo_ma'],
        'commercial_tier_defaults': dict(A.COMMERCIAL_TIER_DEFAULTS_SEED),
    }
    cat = {p['id']: p for p in A._ensure_bundle_catalogs(saved)['commercial_catalog']}
    assert cat['cm_tpo_ma']['cost'] == 80.68   # 0 -> the sheet
    assert cat['ca_iso']['cost'] == 99.00      # left alone
    assert cat['cl_labor_reroof']['cost'] == 450


def test_the_backfill_does_not_touch_other_trades(A):
    """Roofing and siding carry manager-set prices; a 0 there is a decision,
    not a placeholder, and the seed must not fight it on every read."""
    roof_seed = {p['id']: p for p in A.ROOFING_CATALOG_SEED}
    priced = next(p for p in A.ROOFING_CATALOG_SEED if p.get('cost'))
    saved = {'roofing_catalog': [dict(roof_seed[priced['id']], cost=0)],
             'roofing_bundles': [dict(b) for b in A.ROOFING_BUNDLES_SEED],
             'roofing_tier_defaults': dict(A.ROOFING_TIER_DEFAULTS_SEED)}
    cat = {p['id']: p for p in A._ensure_bundle_catalogs(saved)['roofing_catalog']}
    assert cat[priced['id']]['cost'] == 0, 'a zeroed roofing price was overwritten by the seed'


def test_a_mechanically_attached_bid_prices_end_to_end(client, A):
    """The whole point: build the default commercial sell off the seeded book
    and confirm a real dollar figure comes out, with the membrane, the
    build-up and the labor all contributing."""
    pb = client.get('/api/pricebook').get_json()
    cat = {p['id']: p for p in pb['commercial_catalog']}
    bundle = next(b for b in pb['commercial_bundles']
                  if b['id'] == pb['commercial_simple_default'])
    m = {'comm_squares': 40, 'comm_waste_pct': 10, 'comm_perimeter_lf': 500,
         'comm_parapet_lf': 200, 'comm_penetrations': 8, 'comm_drains': 2,
         'comm_curbs': 3, 'comm_pitch_pans': 2, 'comm_walkway_pads': 6,
         'comm_work_type': 0, 'comm_sections': 1}
    sq_waste = 40 * 1.10
    qty_for = {'comm_sq_waste': sq_waste, 'comm_perimeter': 500, 'comm_parapet': 200,
               'comm_penetrations': 8, 'comm_drains': 2, 'comm_curbs': 3,
               'comm_pitch_pans': 2, 'comm_walkway_pads': 6,
               'comm_labor_reroof': 40, 'comm_labor_new': 0,
               'comm_fast_insul': 0, 'comm_fast_seam': 0}
    items, cost = [], 0.0
    for pid in bundle['product_ids']:
        p = cat[pid]
        q = qty_for.get(p.get('measure'), 0)
        cost += q * p['cost']
        items.append({'id': pid, 'catalog_id': pid, 'name': p['name'], 'unit': p['unit'],
                      'quantity': q, 'unit_cost': p['cost'],
                      'unit_price': round(p['cost'] / (1 - 0.29), 2),
                      'customer_visible': True})
    membrane = cat[next(p for p in bundle['product_ids'] if p.startswith('cm_'))]
    assert round(membrane['cost'] * sq_waste, 2) == 3549.92   # 44 SQ of 60-mil TPO
    assert cost > 30000, f'a 40 SQ TPO re-roof costed out at only {cost:.2f}'

    est = _commercial_est()
    est['trades']['commercial']['line_items'] = items
    total = A.calc_selected_total(est)
    assert total > cost, 'the sell price has to clear the cost'


def test_a_flat_roof_bid_does_not_quote_shingle_code(client, A):
    """The Colorado baseline is all steep-slope asphalt content — ice barrier at
    the eave, 1/300 attic ventilation, hip and ridge cap, Class 4 shingles. A
    commercial TPO roof has none of those things, and printing IRC R905 next to
    a welded membrane is how a bid stops looking like it was written for the
    building it is for."""
    est_id = _create(client, _commercial_est())
    est = A.est_load(est_id)
    share = client.post(f'/api/estimates/{est_id}/share').get_json()
    html = client.get(share['url']).get_data(as_text=True).lower()
    for residential in ('ice barrier', 'attic ventilation', 'hip / ridge cap',
                        'class 4', 'starter course', 'r905'):
        assert residential not in html, f'commercial bid quotes {residential!r}'
    # The permit and the jurisdiction still apply to a commercial reroof.
    packet = A.build_production_packet_pdf(est)
    assert packet[:4] == b'%PDF'


def test_a_shingle_bid_still_gets_the_code_block(client, A):
    """The flip side: gating on the roofing trade must not strip the code
    section from the steep-slope bids it was written for."""
    est = _commercial_est()
    est['estimate_type'] = 'retail'
    est['measurements'].update({'roof_squares': 30, 'roof_pitch': '6/12'})
    est['trades']['roofing'] = {
        'enabled': True, 'mode': 'simple', 'simple_bundle': '',
        'line_items': [{'id': 'r1', 'catalog_id': 'm_shingle', 'name': 'Shingles',
                        'unit': 'SQ', 'quantity': 30, 'unit_cost': 100,
                        'unit_price': 140.85, 'customer_visible': True}],
    }
    est_id = _create(client, est)
    share = client.post(f'/api/estimates/{est_id}/share').get_json()
    html = client.get(share['url']).get_data(as_text=True).lower()
    assert 'ice barrier' in html, 'the shingle code block vanished from a roofing bid'


def test_the_customer_page_does_not_talk_to_a_homeowner(client, A):
    """A $288K warehouse bid that promises to leave your home spotless and
    warrants the roof for as long as you own the home reads as a residential
    template nobody changed. Same commitments, addressed to a building owner."""
    est_id = _create(client, _commercial_est())
    share = client.post(f'/api/estimates/{est_id}/share').get_json()
    html = client.get(share['url']).get_data(as_text=True)
    # 'homeowner reviews' further down is left alone on purpose - those
    # reviews really were written by homeowners, and relabelling them would
    # misstate where they came from. What has to go is the copy addressed TO
    # this customer.
    for residential in ('own the home', 'walkthrough with the homeowner',
                        'landscaping', 'A/C units', 'driveway, walks and yard'):
        assert residential not in html, f'commercial bid says {residential!r}'
    assert 'own the building' in html
    assert 'owner or property manager' in html


def test_a_residential_bid_keeps_its_own_process_copy(client, A):
    """The commercial pair must not leak onto the retail bids it was split from."""
    est = _commercial_est()
    est['estimate_type'] = 'retail'
    est['trades']['roofing'] = {
        'enabled': True, 'mode': 'simple', 'simple_bundle': '',
        'line_items': [{'id': 'r1', 'catalog_id': 'm_shingle', 'name': 'Shingles',
                        'unit': 'SQ', 'quantity': 30, 'unit_cost': 100,
                        'unit_price': 140.85, 'customer_visible': True}],
    }
    est_id = _create(client, est)
    share = client.post(f'/api/estimates/{est_id}/share').get_json()
    html = client.get(share['url']).get_data(as_text=True)
    assert 'own the home' in html
    assert 'landscaping' in html


# ── the eight-package matrix ────────────────────────────────────────────
#
# membrane (TPO / EPDM) x attachment (fastened / adhered) x substrate approach
# (tear-off / layover). What separates them is not cosmetic: a layover keeps
# the existing insulation and adds a 1/4" board, a fastened system buys screws
# instead of adhesive, and EPDM tapes its seams where TPO welds them.

PACKAGES = {
    'cb_tpo_ma':      ('tpo',  'fastened', 'tearoff'),
    'cb_tpo_fa':      ('tpo',  'adhered',  'tearoff'),
    'cb_tpo_lo_mf':   ('tpo',  'fastened', 'layover'),
    'cb_tpo_lo_fa':   ('tpo',  'adhered',  'layover'),
    'cb_epdm_mf':     ('epdm', 'fastened', 'tearoff'),
    'cb_epdm':        ('epdm', 'adhered',  'tearoff'),
    'cb_epdm_lo_mf':  ('epdm', 'fastened', 'layover'),
    'cb_epdm_lo_fa':  ('epdm', 'adhered',  'layover'),
}


def _bundles(client):
    pb = client.get('/api/pricebook').get_json()
    return ({b['id']: b for b in pb['commercial_bundles']},
            {p['id']: p for p in pb['commercial_catalog']})


def test_all_eight_packages_ship(client):
    bundles, _ = _bundles(client)
    missing = [b for b in PACKAGES if b not in bundles]
    assert not missing, f'packages missing from the price book: {missing}'


def test_tearoff_builds_a_new_assembly_and_layover_does_not(client):
    """The layover keeps the building's existing insulation - that is the whole
    point of it. Shipping new polyiso in a layover package would bill the
    customer for boards that never leave the yard."""
    bundles, _ = _bundles(client)
    for bid, (_, _, approach) in PACKAGES.items():
        ids = bundles[bid]['product_ids']
        if approach == 'tearoff':
            assert 'ca_iso' in ids, f'{bid} tears off but installs no insulation'
            assert 'ca_cover' in ids
            assert 'ca_cover_quarter' not in ids
            assert 'ca_fast_insul' in ids
        else:
            assert 'ca_cover_quarter' in ids, f'{bid} is a layover with no quarter-inch board'
            assert 'ca_iso' not in ids, f'{bid} is a layover but bills new insulation'
            assert 'ca_cover' not in ids
            # Longer screws: a layover fastener passes through the whole
            # existing assembly to reach the deck.
            assert 'ca_fast_cover' in ids and 'ca_fast_seam_lo' in ids


def test_only_adhered_packages_buy_adhesive_and_only_layovers_survey(client):
    bundles, _ = _bundles(client)
    for bid, (membrane, attachment, approach) in PACKAGES.items():
        ids = bundles[bid]['product_ids']
        adhesive = 'ca_epdm_adhesive' if membrane == 'epdm' else 'ca_adhesive'
        if attachment == 'adhered':
            assert adhesive in ids, f'{bid} is adhered but buys no adhesive'
        else:
            assert 'ca_adhesive' not in ids and 'ca_epdm_adhesive' not in ids, \
                f'{bid} is mechanically fastened but buys adhesive'
        # GAF strongly recommends a moisture survey before any recover, and
        # requires one where perlite or wood fibre stays in the assembly.
        assert ('cx_moisture_survey' in ids) == (approach == 'layover'), bid


def test_epdm_tapes_its_seams_and_tpo_welds_them(client):
    """EPDM has no hot-air weld - its splices are primer plus seam tape, a
    consumable a TPO system never buys."""
    bundles, _ = _bundles(client)
    for bid, (membrane, _, _) in PACKAGES.items():
        ids = bundles[bid]['product_ids']
        assert ('ca_epdm_seam' in ids) == (membrane == 'epdm'), bid


def test_mechanically_fastened_epdm_uses_the_reinforced_sheet(client):
    """A manufacturer requirement, not a preference: a fastened system loads
    the sheet at every plate, so it needs the scrim-reinforced membrane.
    Adhered systems spread the load and run non-reinforced."""
    bundles, cat = _bundles(client)
    assert 'Reinforced' in cat['cm_epdm_mf']['name']
    assert cat['cm_epdm_mf']['attach'] == 'mechanical'
    assert cat['cm_epdm_fa']['attach'] == 'adhered'
    assert 'cm_epdm_mf' in bundles['cb_epdm_mf']['product_ids']
    assert 'cm_epdm_mf' in bundles['cb_epdm_lo_mf']['product_ids']
    assert 'cm_epdm_fa' in bundles['cb_epdm']['product_ids']
    assert 'cm_epdm_fa' in bundles['cb_epdm_lo_fa']['product_ids']


def test_each_package_carries_exactly_one_labor_line_for_its_own_work(client):
    """Eight packages, eight labor rates - tearing a roof off is not the same
    job as laying over it, and welding TPO is not taping EPDM. Two re-roof
    labor lines on one package would bill the work twice."""
    bundles, cat = _bundles(client)
    expected = {
        'cb_tpo_ma': 'cl_tpo_to_mf',       'cb_tpo_fa': 'cl_tpo_to_fa',
        'cb_tpo_lo_mf': 'cl_tpo_lo_mf',    'cb_tpo_lo_fa': 'cl_tpo_lo_fa',
        'cb_epdm_mf': 'cl_epdm_to_mf',     'cb_epdm': 'cl_epdm_to_fa',
        'cb_epdm_lo_mf': 'cl_epdm_lo_mf',  'cb_epdm_lo_fa': 'cl_epdm_lo_fa',
    }
    for bid, labor_id in expected.items():
        ids = bundles[bid]['product_ids']
        reroof = [p for p in ids if cat[p].get('measure') == 'comm_labor_reroof']
        assert reroof == [labor_id], f'{bid} carries {reroof}, expected [{labor_id}]'
        # New construction only makes sense where there is no existing roof.
        is_layover = 'ca_cover_quarter' in ids
        assert ('cl_labor_new' in ids) != is_layover, bid


def test_the_layover_labor_rates_are_still_missing(client):
    """Documents the open gap rather than hiding it: the four layover rates
    have not been supplied yet, so a layover bid prices its materials and none
    of its work. Delete this test once the numbers land."""
    _, cat = _bundles(client)
    for pid in ('cl_tpo_lo_mf', 'cl_tpo_lo_fa', 'cl_epdm_lo_mf', 'cl_epdm_lo_fa'):
        assert cat[pid]['cost'] == 0
    for pid in ('cl_tpo_to_mf', 'cl_tpo_to_fa', 'cl_epdm_to_mf', 'cl_epdm_to_fa'):
        assert cat[pid]['cost'] == 400, 'tear-off starts at the existing standard rate'


def test_the_crew_packet_states_the_layover_rules_only_on_a_layover(client, A):
    """A recover has rules a tear-off does not, all of them decided on the roof
    before the first board goes down. They belong on the sheet in the crew's
    hands, not in a manual back at the office."""
    est = _commercial_est()
    est['trades']['commercial']['line_items'].append(
        {'id': 'i5', 'catalog_id': 'ca_cover_quarter', 'name': 'Cover Board 1/4 in',
         'unit': 'SQ', 'quantity': 44, 'unit_cost': 0, 'unit_price': 0,
         'customer_visible': True})
    assert A._est_is_layover(est)
    assert A.build_production_packet_pdf(est)[:4] == b'%PDF'

    # ...and a tear-off must not carry them.
    assert not A._est_is_layover(_commercial_est())

    # The rules themselves have to name the hard stops.
    joined = ' '.join(A.COMMERCIAL_LAYOVER_RULES).lower()
    assert 'two or more' in joined
    assert '10' in joined and 'sections' in joined
    assert 'moisture survey' in joined
    assert 'coal-tar' in joined
    assert 'epdm cannot contact asphalt' in joined


def test_the_two_flag_lists_stay_in_sync(client, A):
    """COMM_FLAG_LABELS is mirrored by hand in app.js. Drift means the rep
    ticks a box the crew packet never prints - and the two flags that rule a
    layover out are exactly the ones you cannot afford to lose."""
    import os
    import re
    here = os.path.dirname(os.path.abspath(__file__))
    js = open(os.path.join(here, '..', 'static', 'app.js'), encoding='utf-8').read()
    block = re.search(r'const COMM_FLAGS = \[(.*?)\n\];', js, re.S)
    assert block, 'COMM_FLAGS not found in app.js'
    pairs = re.findall(r"key:'([^']+)'\s*,\s*label:'([^']+)'", block.group(1))
    assert pairs == list(A.COMM_FLAG_LABELS), f'js={pairs}\npy={list(A.COMM_FLAG_LABELS)}'
    keys = dict(A.COMM_FLAG_LABELS)
    assert 'existing_layers_2plus' in keys and 'wet_insulation' in keys


def test_a_layover_bid_does_not_promise_a_tear_off(client, A):
    """"Torn off and dried in by section" is exactly the sentence a property
    manager quotes back when the invoice has no disposal on it. A recover says
    what a recover actually does."""
    est = _commercial_est()
    est['trades']['commercial']['line_items'].append(
        {'id': 'i5', 'catalog_id': 'ca_cover_quarter', 'name': 'Cover Board 1/4 in',
         'unit': 'SQ', 'quantity': 44, 'unit_cost': 0, 'unit_price': 0,
         'customer_visible': True})
    est_id = _create(client, est)
    share = client.post(f'/api/estimates/{est_id}/share').get_json()
    html = client.get(share['url']).get_data(as_text=True)
    assert 'Torn off and dried in' not in html
    assert 'scanned for trapped moisture' in html
    assert 'cut and prepped to manufacturer requirement' in html

    # A tear-off bid keeps the tear-off wording.
    to_id = _create(client, _commercial_est())
    to_share = client.post(f'/api/estimates/{to_id}/share').get_json()
    to_html = client.get(to_share['url']).get_data(as_text=True)
    assert 'Torn off and dried in' in to_html
    assert 'scanned for trapped moisture' not in to_html


def test_the_catalog_says_which_sheet_each_price_came_from(A):
    """Two suppliers are live in one catalog and one of the sheets has expired.
    Without provenance, nobody can tell a current Carlisle number from a stale
    GAF one, and 'is this price still good?' becomes unanswerable.

    Deliberately no date arithmetic here - a test that starts failing on its own
    the day a quote lapses is a time bomb, not a safeguard. The GAF sheet is
    marked expired explicitly instead.
    """
    sheets = A.COMMERCIAL_PRICE_SHEETS
    assert set(sheets) == {'carlisle', 'gaf'}
    assert sheets['carlisle']['supplier'] == 'Carlisle'
    assert sheets['carlisle']['quoted'] == '2026-08-19'
    # Carlisle is the current sheet and the one the eight packages are built on.
    assert A.COMMERCIAL_PRICE_SHEET is sheets['carlisle']
    assert not sheets['carlisle'].get('expired')
    # GAF survives only for the membranes Carlisle did not quote, and its sheet
    # lapsed on 2026-06-30 - anything still priced from it needs re-quoting.
    assert sheets['gaf'].get('expired') is True
    # The gaps in the current quote are written down, not left to memory.
    gaps = sheets['carlisle']['gaps'].lower()
    assert 'reinforced' in gaps
    assert '1/4' in gaps


def test_the_polyiso_seed_records_its_supplier_per_thickness(A):
    """Carlisle quoted 2.0" and 2.6"; the other thicknesses are GAF's, off the
    expired sheet. Mixing them without a marker is how a stale number gets
    quoted as a current one."""
    by_id = {i[0]: i for i in A.COMMERCIAL_ISO_SEED}
    assert by_id['ca_iso_20'][3] == 'carlisle'
    assert by_id['ca_iso_40'][3] == 'gaf'
    for row in A.COMMERCIAL_ISO_SEED:
        assert len(row) == 4, row
        assert row[3] in ('carlisle', 'gaf'), row


def test_epdm_fully_adhered_is_now_a_sellable_package(client):
    """The point of the Carlisle quote. Before it there was no priced EPDM roof
    at all; this pins the one that is complete so a future edit cannot quietly
    take it back out."""
    pb = client.get('/api/pricebook').get_json()
    cat = {p['id']: p for p in pb['commercial_catalog']}
    bundle = next(b for b in pb['commercial_bundles'] if b['id'] == 'cb_epdm')
    for pid in bundle['product_ids']:
        p = cat[pid]
        # Lump sums are typed per job; everything measured must carry a price.
        if p.get('measure'):
            assert p['cost'] > 0, f'cb_epdm carries an unpriced line: {p["name"]}'
    # ...and it is genuinely the EPDM build, not TPO wearing a label.
    assert 'cm_epdm_fa' in bundle['product_ids']
    assert 'ca_epdm_adhesive' in bundle['product_ids']
    assert 'ca_epdm_seam' in bundle['product_ids']
    assert 'ca_adhesive' not in bundle['product_ids']
