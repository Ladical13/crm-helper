"""How the fastener calculator is wired in: catalog products, the price-book
migration, the measurement mirror, and the production packet.

test_fastening.py covers the math. This file covers the plumbing around it —
which is where a working calculator quietly stops reaching the bid.
"""
import io
import os
import re

import pytest


# ── price-book migration ───────────────────────────────────────────────
# _ensure_bundle_catalogs only SEEDS when a catalog is absent, and
# PUT /api/pricebook persists whatever the client last saw. So a book saved
# before the fastener products existed would never get them, and every
# commercial bid would silently lose its fastener lines.

def _stale_book(A):
    """A commercial price book as it looked before the zone calculator."""
    cat = [dict(p) for p in A.COMMERCIAL_CATALOG_SEED
           if p['id'] not in ('ca_fast_insul', 'ca_fast_seam')]
    for p in cat:
        p.pop('attach', None)
    bundles = [dict(b, product_ids=[i for i in b['product_ids']
                                    if not i.startswith('ca_fast')] + ['ca_fasteners'])
               for b in A.COMMERCIAL_BUNDLES_SEED]
    return {'commercial_catalog': cat, 'commercial_bundles': bundles,
            'commercial_tier_defaults': dict(A.COMMERCIAL_TIER_DEFAULTS_SEED)}


def test_stale_book_gains_the_fastener_products(A):
    pb = A._ensure_bundle_catalogs(_stale_book(A))
    ids = {p['id'] for p in pb['commercial_catalog']}
    assert {'ca_fast_insul', 'ca_fast_seam'} <= ids
    assert 'ca_fasteners' in ids, 'the legacy product must stay - estimates reference it'


def test_stale_book_gains_the_attach_tags(A):
    """Without `attach` every system falls to the fail-closed branch and seam
    fasteners silently drop to zero."""
    pb = A._ensure_bundle_catalogs(_stale_book(A))
    by_id = {p['id']: p for p in pb['commercial_catalog']}
    assert by_id['cm_tpo_ma']['attach'] == 'mechanical'
    assert by_id['cm_coating']['attach'] == 'coating'


def test_stale_seeded_bundles_swap_the_superseded_product(A):
    pb = A._ensure_bundle_catalogs(_stale_book(A))
    b = next(x for x in pb['commercial_bundles'] if x['id'] == 'cb_tpo_ma')
    assert 'ca_fasteners' not in b['product_ids']
    assert 'ca_fast_insul' in b['product_ids'] and 'ca_fast_seam' in b['product_ids']


def test_manager_created_bundle_is_never_rewritten(A):
    """The swap applies to SEEDED bundles only - a manager's own bundle is theirs."""
    pb = {'commercial_catalog': _stale_book(A)['commercial_catalog'],
          'commercial_bundles': [{'id': 'mine', 'name': 'Mine', 'product_ids': ['ca_fasteners']}],
          'commercial_tier_defaults': {}}
    A._ensure_bundle_catalogs(pb)
    assert pb['commercial_bundles'][0]['product_ids'] == ['ca_fasteners']


def test_a_cleared_product_field_stays_cleared(A):
    """Absence is the test, not falsiness - the same contract as the bundle copy
    fields. A manager who blanks `attach` must not have it restored on every GET."""
    pb = _stale_book(A)
    for p in pb['commercial_catalog']:
        if p['id'] == 'cm_tpo_ma':
            p['attach'] = ''
    A._ensure_bundle_catalogs(pb)
    by_id = {p['id']: p for p in pb['commercial_catalog']}
    assert by_id['cm_tpo_ma']['attach'] == ''


# ── shipped catalog ────────────────────────────────────────────────────

def test_shipped_bundles_all_carry_the_fastener_lines(client):
    """Every package needs somewhere for the zone calculator to land, on both
    layers. Checked by MEASURE rather than by product id: a tear-off fastens
    insulation (ca_fast_insul) and a layover fastens the cover board through
    the existing roof (ca_fast_cover), and those are different products with
    different fastener lengths — but both answer comm_fast_insul."""
    pb = client.get('/api/pricebook').get_json()
    cat = {p['id']: p for p in pb['commercial_catalog']}
    assert {'ca_fast_insul', 'ca_fast_seam'} <= set(cat)
    for b in pb['commercial_bundles']:
        measures = {cat[pid].get('measure') for pid in b['product_ids'] if pid in cat}
        assert 'comm_fast_insul' in measures, b['name'] + ' has no insulation/cover fasteners'
        assert 'comm_fast_seam' in measures, b['name'] + ' has no seam fasteners'


def test_every_membrane_declares_how_it_attaches(client):
    """An untagged membrane falls to the fail-closed branch and loses its seam
    fasteners. Every SHIPPED system must be explicit."""
    pb = client.get('/api/pricebook').get_json()
    for p in pb['commercial_catalog']:
        if p['id'].startswith('cm_'):
            assert p.get('attach') in ('mechanical', 'adhered', 'coating'), \
                p['name'] + ' does not declare attach'


def test_fastener_products_are_driven_by_the_calculator(client):
    pb = client.get('/api/pricebook').get_json()
    by_id = {p['id']: p for p in pb['commercial_catalog']}
    assert by_id['ca_fast_insul']['measure'] == 'comm_fast_insul'
    assert by_id['ca_fast_seam']['measure'] == 'comm_fast_seam'
    # Raw EA counts, not buckets: the seeded cost is a placeholder, so a
    # unit/rounding mismatch would mis-price by 1000x silently.
    assert by_id['ca_fast_insul']['unit'] == 'EA'
    assert 'bundle_lf' not in by_id['ca_fast_insul']


# ── measurement mirror ─────────────────────────────────────────────────

def test_measure_fields_and_labels_stay_in_sync(A):
    """MEASURE_FIELDS (app.js) and MEASURE_LABELS (app.py) are mirrored by hand.
    A key in one and not the other means the packet prints a blank, or the rep
    cannot enter something the calculator reads."""
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, '..', 'static', 'app.js'), encoding='utf-8') as f:
        js = f.read()
    block = js[js.index('const MEASURE_FIELDS'):]
    block = block[:block.index('\n];')]
    js_keys = set(re.findall(r"key:'(\w+)'", block))
    py_keys = {k for _g, fields in A.MEASURE_LABELS for k, _l, _u in fields}
    assert js_keys == py_keys, ('only in app.js: %s; only in app.py: %s'
                               % (sorted(js_keys - py_keys), sorted(py_keys - js_keys)))


# ── production packet ──────────────────────────────────────────────────

def _packet_text(A, est):
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(A.build_production_packet_pdf(est)))
    return ' '.join((p.extract_text() or '') for p in r.pages)


def _signed_commercial(**meas):
    return {
        'estimate_id': 'fz1', 'estimate_type': 'commercial', 'selected_tier': 'better',
        'customer': {'name': 'Northgate',
                     'address': {'street': '1 Way', 'city': 'Loveland', 'state': 'CO'}},
        'signature': {'name': 'Dana', 'signed_at': '2026-07-28T12:00:00Z',
                      'selected_tier': 'better'},
        'pricing': {'mode': 'margin', 'global_rate': 35, 'tier_rates': {},
                    'trade_rates': {}, 'per_trade_overrides': {}},
        'measurements': meas,
        'trades': {'commercial': {'enabled': True, 'mode': 'simple', 'line_items': [
            {'id': 'a', 'name': 'Insulation Fasteners & Plates', 'unit': 'EA',
             'quantity': 998, 'unit_cost': 0.4, 'unit_price': 0.56}]}},
    }


def test_packet_prints_the_per_zone_schedule(A):
    txt = _packet_text(A, _signed_commercial(
        comm_length_ft=100, comm_width_ft=50, comm_height_ft=20, comm_uplift=90,
        comm_seam_attach=1, comm_insul_attach=1, comm_squares=50))
    assert 'Fastening Schedule' in txt
    assert 'FM 1-90' in txt
    # The crew lays the roof out from the spacing, so it has to reach the sheet -
    # and the corner is not the same as the field.
    assert '6" o.c.' in txt, 'corner spacing missing'
    assert '12" o.c.' in txt, 'field spacing missing'
    assert 'GENERIC' in txt.upper(), 'the disclaimer must travel with the numbers'


def test_packet_shouts_when_the_schedule_was_not_calculated(A):
    """Sending is deliberately not blocked, so this section is the last defence."""
    txt = _packet_text(A, _signed_commercial(comm_squares=50))
    assert 'NOT CALCULATED' in txt
    assert 'DO NOT ORDER FASTENERS' in txt
    assert 'No uplift rating was selected' in txt


def test_packet_names_the_missing_input(A):
    txt = _packet_text(A, _signed_commercial(comm_uplift=90, comm_squares=50))
    assert 'NOT CALCULATED' in txt
    assert 'length, width, and height' in txt
