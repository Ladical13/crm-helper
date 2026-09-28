"""What the permit packet and the warranty certificate say is going on the roof.

Both used to read the manifest's material_name, which comes from
_bundle_id_for_tier — and that falls back to the price book's DEFAULT bundle
for a tier the estimate never picked, and keeps naming a bundle's shingle after
the rep has hand-built the tier into something else. With the seeded ladder the
default Better package is Northgate, so the permit packet filed "CertainTeed
Northgate" as the roof covering over roofs that were not Northgate: the one
document where the covering is what the Class-4 question and the roofing
affidavit turn on, and a warranty certificate suggesting a shingle that was not
installed.

_installed_product_name() names the covering from THIS estimate or not at all:
a picked bundle that still describes the tier → the covering actually priced in
the tier → the rep's Product Selection → the rep's own package name → nothing.
"""
import inspect
import io

import pytest


def _pdf_text(raw):
    from pypdf import PdfReader
    return '\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(raw)).pages)


def _item(name, cost, catalog_id=None, qty=32):
    it = {'name': name, 'quantity': qty, 'unit': 'SQ',
          'tiers': {t: {'material_unit_cost': cost, 'labor_unit_cost': 0, 'description': ''}
                    for t in ('good', 'better', 'best')}}
    if catalog_id:
        it['catalog_id'] = catalog_id
    return it


def _est(items, mode='gbb', **roofing):
    td = dict({'enabled': True, 'mode': mode, 'line_items': items}, **roofing)
    return {
        'estimate_id': 'cafe0001-0000-0000-0000-000000000000',
        'estimate_number': 1042, 'estimate_type': 'retail', 'selected_tier': 'better',
        'pricing': {'mode': 'margin', 'rate': 35,
                    'tier_rates': {'good': 35, 'better': 35, 'best': 35}},
        'customer': {'name': 'Ada Lovelace', 'phone': '9705550100',
                     'address': {'street': '12 Analytical Way', 'city': 'Loveland',
                                 'state': 'CO', 'zip': '80537'}},
        'signature': {'signed_at': '2026-09-01T10:00:00Z', 'selected_tier': 'better'},
        'measurements': {'roof_squares': 30, 'waste_pct': 10},
        'trades': {'roofing': td},
    }


def _catalog_name(A, pid):
    pb = A._ensure_bundle_catalogs(A._load_price_book())
    return next(p['name'] for p in pb['roofing_catalog'] if p.get('id') == pid)


def _covering(A, est):
    text = _pdf_text(A.build_permit_packet_pdf(est))
    return text


def test_the_seed_still_defaults_better_to_northgate(A):
    """The premise: if this stops being true the tests below stop proving
    anything, because they rely on the default being a DIFFERENT shingle."""
    pb = A._ensure_bundle_catalogs(A._load_price_book())
    assert pb['roofing_tier_defaults']['better'] == 'b_northgate'


def test_a_package_never_picked_is_not_named_after_the_default(A):
    est = _est([_item('Architectural Shingles', 142, 'm_landmark')])
    landmark = _catalog_name(A, 'm_landmark')
    assert A._installed_product_name(est, 'roofing') == landmark
    text = _covering(A, est)
    assert 'Roof Covering'.upper() in text.upper()
    assert 'Landmark' in text
    assert 'Northgate' not in text, 'the price book default reached the permit packet'


def test_a_hand_built_tier_is_not_named_after_the_bundle_it_left(A):
    """tier_bundles still says Northgate, but none of Northgate's products is
    priced in the tier any more — the rep rebuilt it as steel."""
    est = _est([_item('Steel Shingles', 400, 'm_edco')],
               tier_bundles={'good': 'b_landmark', 'better': 'b_northgate',
                             'best': 'b_standing_seam'})
    assert A._installed_product_name(est, 'roofing') == _catalog_name(A, 'm_edco')
    assert 'Northgate' not in _covering(A, est)


def test_a_bundle_the_estimate_did_pick_still_names_its_product(A):
    """The case that always worked must keep working."""
    est = _est([_item('Impact Shingles', 175, 'm_northgate')],
               tier_bundles={'good': 'b_landmark', 'better': 'b_northgate',
                             'best': 'b_standing_seam'})
    assert A._installed_product_name(est, 'roofing') == _catalog_name(A, 'm_northgate')


def test_product_selection_is_the_answer_when_no_line_names_a_product(A):
    est = _est([_item('Shingles', 142)],
               colors={'manufacturer': 'CertainTeed', 'product_line': 'Landmark Pro'})
    assert A._installed_product_name(est, 'roofing') == 'CertainTeed Landmark Pro'
    # The manufacturer is not said twice when the product line already says it.
    est['trades']['roofing']['colors']['product_line'] = 'CertainTeed Landmark Pro'
    assert A._installed_product_name(est, 'roofing') == 'CertainTeed Landmark Pro'


def test_the_reps_own_package_name_is_the_last_resort(A):
    est = _est([_item('Shingles', 142)], tier_bundles={'better': '__custom__'},
               tier_bundle_names={'better': 'Summit Special'})
    assert A._installed_product_name(est, 'roofing') == 'Summit Special'


def test_nothing_to_go_on_prints_no_covering_rather_than_a_guess(A):
    est = _est([_item('Shingles', 142)])
    assert A._installed_product_name(est, 'roofing') == ''
    text = _covering(A, est).upper()
    assert 'ROOF COVERING' not in text
    assert 'NORTHGATE' not in text


def test_a_simple_mode_roof_gets_a_covering_line_too(A):
    """The manifest carries no tiers for a simple-mode trade, so the old loop
    printed no covering at all for one."""
    est = _est([{'name': 'Shingles', 'quantity': 32, 'unit': 'SQ', 'unit_cost': 142}],
               mode='simple', colors={'manufacturer': 'IKO', 'product_line': 'Nordic'})
    assert 'IKO Nordic' in _covering(A, est)


def test_the_warranty_suggestion_follows_the_same_rule(A):
    est = _est([_item('Architectural Shingles', 142, 'm_landmark')])
    _term, product, _basis = A._warranty_manifest(est)
    assert product == _catalog_name(A, 'm_landmark')
    assert 'Northgate' not in product


def test_both_documents_read_the_one_helper():
    """Two copies of "what is on the roof" is how the permit clerk and the
    homeowner's certificate end up naming different shingles."""
    import app as A
    for fn in (A.build_permit_packet_pdf, A._warranty_manifest):
        src = inspect.getsource(fn)
        assert '_installed_product_name(' in src, fn.__name__
        assert "get('material_name')" not in src, fn.__name__


def test_the_material_sku_rule_is_shared_with_the_bundle_lookup():
    import app as A
    assert '_is_material_sku(' in inspect.getsource(A._material_product_for_bundle)
    assert A._is_material_sku('roofing', 'm_landmark')
    assert not A._is_material_sku('roofing', 'a_ice_water')
    assert A._is_material_sku('siding', 's_lp_smart') and not A._is_material_sku('siding', 'sl_install')
