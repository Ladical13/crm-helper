"""_build_estimate_manifest — the structured summary consumed by the /sign
page's JSON-LD + details block AND by the signed PDF's About-This-Estimate
page.

These invariants hold both surfaces to the same shape, so an AI reader
uploading the PDF sees the same substance the web page shows.
"""


def _retail_estimate(**overrides):
    est = {
        'estimate_id':   'retail-1-xxxxxxxx',
        'estimate_date': '2026-08-01',
        'customer': {
            'name':    'Jane Doe',
            'address': {'street': '1 Test St', 'city': 'Loveland',
                        'state': 'CO', 'zip': '80537'},
        },
        'trades': {
            'roofing': {
                'enabled': True, 'mode': 'gbb',
                'tier_bundles': {'good': 'b_landmark', 'better': 'b_northgate',
                                 'best': 'b_standing_seam'},
                'line_items': [
                    {'name': 'Shingles', 'quantity': 30, 'unit': 'SQ',
                     'measure': 'squares_waste',
                     'tiers': {'good':   {'material_unit_cost': 142},
                               'better': {'material_unit_cost': 175},
                               'best':   {'material_unit_cost': 400}}},
                ],
            },
        },
        'measurements': {'attic_sqft': 1800, 'turtle_vents': 0},
    }
    est.update(overrides)
    return est


def _insurance_estimate():
    return {
        'estimate_id':   'ins-1-yyyyyyyy',
        'estimate_type': 'insurance',
        'customer': {'name': 'Sam Carrier',
                     'address': {'street': '2 Test St', 'city': 'Fort Collins',
                                 'state': 'CO'}},
        'trades': {'insurance': {'enabled': True, 'carrier': 'State Farm',
                                 'claim_number': 'X-12345',
                                 'sections': [{'name': 'Roof',
                                               'items': [{'name': 'Shingles',
                                                          'acv': 8000, 'depreciation': 2000}]}]}},
    }


def test_retail_manifest_has_three_tiers_per_gbb_trade(A):
    m = A._build_estimate_manifest(_retail_estimate())
    assert not m['is_insurance']
    assert len(m['trades']) == 1
    tr = m['trades'][0]
    assert tr['key'] == 'roofing' and tr['mode'] == 'gbb'
    assert [t['tier'] for t in tr['tiers']] == ['good', 'better', 'best']
    # Each tier resolves to a real bundle from the seed (name comes from the
    # bundle in the price book; not customized on the estimate).
    names = {t['tier']: t['package_name'] for t in tr['tiers']}
    assert names['good']   == 'CertainTeed Landmark'
    assert names['better'] == 'CertainTeed Northgate'
    assert names['best']   == 'Standing Seam'


def test_retail_manifest_carries_material_bullets(A):
    tr = A._build_estimate_manifest(_retail_estimate())['trades'][0]
    better = next(t for t in tr['tiers'] if t['tier'] == 'better')
    # A shopper (or an AI) can name the specific product and its warranty.
    assert any('Northgate' in b for b in better['material_bullets'])
    assert any('Class 4' in b for b in better['material_bullets'])
    assert any('warranty' in b.lower() for b in better['material_bullets'])


def test_insurance_manifest_carries_carrier_and_no_tiers(A):
    m = A._build_estimate_manifest(_insurance_estimate())
    assert m['is_insurance']
    assert m['carrier'] == 'State Farm'
    assert m['claim_number'] == 'X-12345'
    # No G/B/B trade block for insurance-only scopes.
    assert m['trades'] == []
    # Grand total sums ACV + depreciation
    assert m['grand_total'] == 10000.0


def test_ventilation_block_computes_when_attic_sqft_present(A):
    m = A._build_estimate_manifest(_retail_estimate())
    v = m['ventilation']
    assert v is not None
    assert v['attic_sqft'] == 1800
    # 1800/300 * 144 sq in = 864, split evenly
    assert v['required_total_sqin'] == 864.0
    assert v['required_intake_sqin'] == v['required_exhaust_sqin'] == 432.0
    # No existing exhaust → deficit is the full required exhaust
    assert v['deficit_exhaust_sqin'] == 432.0
    # And a code basis a reader can cite
    assert 'IRC' in v['code_basis']


def test_ventilation_absent_when_no_measurements(A):
    est = _retail_estimate()
    est['measurements'] = {}
    assert A._build_estimate_manifest(est)['ventilation'] is None


def test_ventilation_absent_for_insurance_only(A):
    """Insurance scope may include roofing implicitly, but the retail
    ventilation calc doesn't apply — carrier scope drives ventilation."""
    assert A._build_estimate_manifest(_insurance_estimate())['ventilation'] is None


def test_code_block_populates_from_statewide_baseline(A):
    m = A._build_estimate_manifest(_retail_estimate())
    code = m['code']
    assert code is not None
    # Statewide-baseline fallback when no jurisdiction is picked
    assert 'Colorado' in code['jurisdiction_name']
    # Baseline code items carry IRC citations
    assert any('IRC' in (ci.get('basis') or '') for ci in code['code_items'])


def test_code_block_uses_selected_jurisdiction(A):
    est = _retail_estimate()
    est['permit_jurisdiction'] = {'selected_id': 'loveland', 'confirmed': True}
    m = A._build_estimate_manifest(est)
    assert m['code']['jurisdiction_name'] == 'City of Loveland'


def test_warranty_is_tiered_matching_data_source(A):
    w = A._build_estimate_manifest(_retail_estimate())['warranty_by_tier']
    assert 'Lifetime' in w['best']
    assert '5-year' in w['good'] and '5-year' in w['better']


def test_insurance_manifest_carries_no_tiered_warranty(A):
    """A claim sells the one scope the carrier approved — there is no
    Good/Better/Best to choose between, so a three-package warranty table
    describes a decision the customer was never offered. Empty here is what
    removes it from the /sign details block, from the signed PDF's
    'Workmanship Warranty by Package', and from the glance block's
    'Backed by' row (which then falls back to warranty_body)."""
    m = A._build_estimate_manifest(_insurance_estimate())
    assert m['is_insurance']
    assert m['warranty_by_tier'] == {}
    # The company's own warranty copy still travels — it is not tier-specific.
    assert 'warranty_body' in m


def test_company_block_carries_certifications(A):
    m = A._build_estimate_manifest(_retail_estimate())
    comp = m['company']
    assert comp['name'] == 'Project One Roofing'
    assert comp['phone']
    # Company_content.json ships two credentials — the manifest exposes them
    # so JSON-LD can emit hasCredential and readers can cite them.
    assert isinstance(comp['certifications'], list)
    assert comp['certifications']


def test_summary_is_a_one_liner_with_specifics(A):
    m = A._build_estimate_manifest(_retail_estimate())
    s = m['summary']
    # Money and city — the two hooks a preview needs.
    assert 'Loveland' in s and 'CO' in s
    assert '$' in s


def test_reviews_summary_when_present(A):
    m = A._build_estimate_manifest(_retail_estimate())
    # company_content.json ships two seeded 5-star reviews
    revs = m['reviews']
    assert revs is not None
    assert revs['count'] == 2
    assert revs['average'] == 5.0


def test_manifest_is_pure_does_not_mutate_estimate(A):
    import copy
    est = _retail_estimate()
    snap = copy.deepcopy(est)
    A._build_estimate_manifest(est)
    assert est == snap


def test_tiers_enabled_subset_is_honored(A):
    est = _retail_estimate()
    est['tiers_enabled'] = {'good': False, 'better': True, 'best': True}
    tr = A._build_estimate_manifest(est)['trades'][0]
    assert [t['tier'] for t in tr['tiers']] == ['better', 'best']
