"""A "Supplements" section on a G/B/B trade.

The "if needed" lines — extra decking by the sheet, a second layer — sit at
quantity 0 because nobody can measure them until the roof is open. A zero-qty
line is "not in scope" everywhere, so they priced at nothing and never reached
the customer. They are their own block now: out of the package total, cost and
margin, printed separately with their own subtotal, a blank quantity pricing
as one unit. JS parity for the same rules lives in test_parity.py.
"""
import pytest

PRICING = {'mode': 'margin', 'global_rate': 35,
           'tier_rates': {'good': 35, 'better': 35, 'best': 35},
           'per_trade_overrides': {}}


def _item(name, qty, cost, section='', **tier_kw):
    return {'name': name, 'quantity': qty, 'unit': 'EA', 'section': section,
            'tiers': {t: dict({'material_unit_cost': cost, 'labor_unit_cost': 0,
                               'description': ''}, **tier_kw)
                      for t in ('good', 'better', 'best')}}


def _est(items, sections=('Supplements',)):
    return {'estimate_type': 'retail', 'pricing': PRICING, 'selected_tier': 'better',
            'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
                                   'sections': list(sections), 'line_items': items}}}


ROOF = _item('Shingles', 30, 100)
SELL_ROOF = 30 * 100 / 0.65
SELL_SHEET = 65 / 0.65


def test_supplement_lines_stay_out_of_the_package_total(A):
    e = _est([ROOF, _item('Decking (per sheet)', 0, 65, 'Supplements'),
              _item('Second layer', 4, 50, 'Supplements')])
    assert A.calc_tier_total(e, 'better') == pytest.approx(SELL_ROOF, abs=0.01)
    assert A.calc_selected_total(e) == pytest.approx(SELL_ROOF, abs=0.01)
    assert A._trade_cost_subtotal(e, 'roofing', 'better') == pytest.approx(3000, abs=0.01)


def test_a_blank_quantity_prices_as_one_unit(A):
    e = _est([ROOF, _item('Decking (per sheet)', 0, 65, 'Supplements'),
              _item('Second layer', 4, 50, 'Supplements')])
    rows, total = A.trade_supplements(e, 'roofing', 'better')
    assert [r[0]['name'] for r in rows] == ['Decking (per sheet)', 'Second layer']
    assert rows[0][2] == pytest.approx(SELL_SHEET, abs=0.01)
    assert total == pytest.approx(SELL_SHEET + 4 * 50 / 0.65, abs=0.01)


def test_any_section_named_supplement_counts_case_insensitively(A):
    e = _est([ROOF, _item('Fascia', 0, 10, 'Insurance SUPPLEMENTS')],
             sections=('Insurance SUPPLEMENTS',))
    assert A.trade_supplements(e, 'roofing', 'good')[1] > 0


def test_a_tag_for_a_section_the_trade_no_longer_lists_is_general(A):
    """Same rule as groupedTradeItems: an unknown tag prints under General."""
    e = _est([ROOF, _item('Vents', 2, 40, 'Supplements')], sections=())
    assert A.trade_supplements(e, 'roofing', 'better') == ([], 0.0)
    assert A.calc_tier_total(e, 'better') == pytest.approx(SELL_ROOF + 80 / 0.65, abs=0.01)


def test_tier_exclusion_and_price_override_are_honoured(A):
    excluded = _item('Decking', 0, 65, 'Supplements')
    excluded['tiers']['good']['included'] = False
    locked = _item('Chimney flashing', 0, 0, 'Supplements', price_override=450)
    e = _est([ROOF, excluded, locked])
    assert A.trade_supplements(e, 'roofing', 'good')[1] == pytest.approx(450)
    assert A.trade_supplements(e, 'roofing', 'best')[1] == pytest.approx(450 + SELL_SHEET, abs=0.01)


def test_customer_page_shows_the_block_without_moving_the_total(A):
    e = _est([ROOF, _item('Decking (per sheet)', 0, 65, 'Supplements')])
    html, grand = A.render_line_items(e, 'better')
    assert grand == pytest.approx(A.calc_tier_total(e, 'better'), abs=0.01)
    assert 'Supplements Subtotal' in html
    assert 'Decking (per sheet)' in html
    assert 'If needed' in html
    assert 'not included in the total' in html


def test_a_trade_with_only_supplements_still_shows_them(A):
    e = _est([_item('Decking (per sheet)', 0, 65, 'Supplements')])
    html, grand = A.render_line_items(e, 'better')
    assert grand == 0
    assert 'Supplements Subtotal' in html


def test_supplements_are_not_package_bullets(A):
    e = _est([ROOF, _item('Decking (per sheet)', 0, 65, 'Supplements')])
    assert all('Decking' not in f for f in A._autofill_tier_features(e, 'roofing', 'better'))


def test_signed_pdf_builds_with_a_supplements_block(A):
    e = _est([ROOF, _item('Decking (per sheet)', 0, 65, 'Supplements')])
    e.update({'estimate_id': 'supp-test', 'customer': {'name': 'Test', 'address': {}},
              'signature': {'name': 'Test', 'signed_at': '2026-09-14T00:00:00Z',
                            'selected_tier': 'better'}})
    pdf = A.build_signed_pdf(e)
    assert pdf[:5] == b'%PDF-'


def _simple_est(items):
    return {'estimate_type': 'retail', 'pricing': PRICING, 'selected_tier': 'better',
            'trades': {'roofing': {'enabled': True, 'mode': 'simple',
                                   'sections': ['Supplements'], 'line_items': items}}}


def test_simple_mode_supplements_price_outside_the_total(A):
    e = _simple_est([{'name': 'Shingles', 'quantity': 30, 'unit_price': 150},
                     {'name': 'Decking (per sheet)', 'quantity': 0, 'unit_price': 85,
                      'section': 'Supplements'}])
    assert A.calc_selected_total(e) == pytest.approx(4500)
    html, grand = A.render_line_items(e)
    assert grand == pytest.approx(4500)
    assert 'Supplements Subtotal' in html and '$85.00' in html


def test_an_unpriced_supplement_is_a_notice_not_a_zero_charge(A):
    """"There may be supplements" with no price must not print $0.00."""
    e = _simple_est([{'name': 'Shingles', 'quantity': 30, 'unit_price': 150},
                     {'name': 'Additional decking or repairs', 'quantity': 0, 'unit_price': 0,
                      'section': 'Supplements'}])
    html, grand = A.render_line_items(e)
    assert grand == pytest.approx(4500)
    assert 'Additional decking or repairs' in html
    assert 'Quoted if needed' in html
    assert 'Supplements may be needed' in html
    assert 'Supplements Subtotal' not in html
    supp = html[html.index('Supplements'):]
    assert '$0.00' not in supp


def test_signed_pdf_builds_with_an_unpriced_supplement(A):
    e = _simple_est([{'name': 'Shingles', 'quantity': 30, 'unit_price': 150},
                     {'name': 'Additional decking or repairs', 'quantity': 0, 'unit_price': 0,
                      'section': 'Supplements'}])
    e.update({'estimate_id': 'supp-notice', 'customer': {'name': 'Test', 'address': {}},
              'signature': {'name': 'Test', 'signed_at': '2026-09-14T00:00:00Z'}})
    assert A.build_signed_pdf(e)[:5] == b'%PDF-'
