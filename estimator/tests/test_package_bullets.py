"""What a package card promises, and who gets to write it.

The customer's card printed the description and then every line item in the
tier — ten of them, then "+ 11 more items". That is an inventory, not a
promise, and it was unreadable next to another contractor's bid. Worse, the
only editor for that copy (the Options tab) was retired, so a rep looking at a
card they didn't like had nowhere to change it.

Two rules now. An untouched card shows the first `_CARD_BULLET_DEFAULT`
bullets of whatever built it. Once the rep writes the list themselves it is
shown exactly as typed, however many lines — `tier_features_edited` is the
flag, the same one the tagline uses, and it survives the staleness rule that
throws bundle copy away. Nothing is truncated with a "+ N more" line anywhere.
"""
import os
import re

import pytest


HERE = os.path.dirname(os.path.abspath(__file__))
APP_JS = os.path.join(HERE, '..', 'static', 'app.js')


def _js():
    with open(APP_JS, encoding='utf-8') as fh:
        return fh.read()


def _cell(cost=200, included=True):
    return {'material_unit_cost': cost, 'labor_unit_cost': 0,
            'description': '', 'notes': '', 'included': included}


def _est(features, edited=None, bundle='', **over):
    est = {
        'estimate_type': 'retail', 'salesperson': 'luke', 'selected_tier': 'better',
        'customer': {'name': 'Bea Bullet', 'email': 'b@example.com',
                     'address': {'street': '1 Card Rd', 'city': 'Golden', 'state': 'CO'}},
        'pricing': {'mode': 'margin', 'global_rate': 35, 'tier_rates': {},
                    'trade_rates': {}, 'per_trade_overrides': {}},
        'trades': {'roofing': {
            'enabled': True, 'mode': 'gbb', 'selected_tier': 'better',
            'tier_bundles': {'good': '', 'better': bundle, 'best': ''},
            'tier_features': {'good': [], 'better': list(features), 'best': []},
            'tier_descriptions': {'good': '', 'better': '', 'best': ''},
            'line_items': [{'id': 'h1', 'name': 'Rolled Roofing', 'unit': 'SQ',
                            'quantity': 10, 'customer_visible': True,
                            'tiers': {'good': _cell(included=False), 'better': _cell(700),
                                      'best': _cell(included=False)}}],
        }},
    }
    if edited is not None:
        est['trades']['roofing']['tier_features_edited'] = {'better': edited}
    est.update(over)
    return est


def _content(A, est):
    pb = A._ensure_bundle_catalogs(A._load_price_book())
    tfeat, tdesc = A._trade_tier_content(est, 'roofing')
    return A._tier_card_content(pb, est, 'roofing', 'better', tfeat, tdesc)


ELEVEN = [f'Bullet {i}' for i in range(1, 12)]


# ── the default, and the rep's own list ────────────────────────────────────

def test_an_untouched_card_stops_at_the_default(A):
    feats, _ = _content(A, _est(ELEVEN))
    assert feats == ELEVEN[:A._CARD_BULLET_DEFAULT]


def test_an_edited_card_is_shown_in_full(A):
    feats, _ = _content(A, _est(ELEVEN, edited=True))
    assert feats == ELEVEN


def test_the_default_is_six(A):
    """The number the rep was asked for. Changing it is a decision, not a tidy."""
    assert A._CARD_BULLET_DEFAULT == 6


def test_blank_lines_never_reach_the_card(A):
    feats, _ = _content(A, _est(['Real bullet', '   ', ''], edited=True))
    assert feats == ['Real bullet']


def test_a_card_with_no_copy_falls_back_to_the_line_items(A):
    feats, _ = _content(A, _est([]))
    assert feats == ['Rolled Roofing']


# ── the rep's bullets outrank the staleness rule ───────────────────────────

def test_rep_bullets_survive_a_custom_tier(A):
    """The tagline already works this way. Bundle copy on a tier that no longer
    sells that bundle is thrown away; what the rep typed for THIS package is
    what they want the customer to read, Custom tier included."""
    est = _est(['Hand-written promise'], edited=True, bundle='__custom__')
    feats, _ = _content(A, est)
    assert feats == ['Hand-written promise']


def test_bundle_copy_on_a_custom_tier_is_still_dropped(A):
    est = _est(['Landmark bullet'], edited=False, bundle='__custom__')
    feats, _ = _content(A, est)
    assert 'Landmark bullet' not in feats
    assert feats == ['Rolled Roofing']


# ── what the customer actually sees ────────────────────────────────────────

def _sign_page(client, est):
    est_id = client.post('/api/estimates', json=est).get_json()['estimate_id']
    token = client.post(f'/api/estimates/{est_id}/share').get_json()['token']
    page = client.get(f'/sign/{token}')
    assert page.status_code == 200
    return page.get_data(as_text=True)


def test_the_customer_page_never_says_how_many_it_left_out(client, A):
    html = _sign_page(client, _est(ELEVEN))
    assert 'Bullet 6' in html
    assert 'Bullet 7' not in html
    assert 'more included' not in html
    assert 'cv-tier-more' not in html


def test_the_customer_page_prints_every_rep_written_bullet(client, A):
    html = _sign_page(client, _est(ELEVEN, edited=True))
    assert 'Bullet 11' in html
    assert 'more included' not in html


# ── the editor, and the two implementations agreeing ───────────────────────

def test_the_pricing_tab_has_a_box_per_package():
    js = _js()
    assert 'function tierBulletsEditorHtml(trade, tier)' in js
    assert '${tierBulletsEditorHtml(trade, t)}' in js, (
        'the editor is not rendered in the tier column'
    )
    assert "onchange=\"setTierBullets('${trade}','${tier}',this.value)\"" in js


def test_emptying_the_box_goes_back_to_the_default():
    """An empty card is never what a rep meant, so a blank box is "use the
    default", not "promise nothing"."""
    m = re.search(r'function setTierBullets\(trade, tier, text\).*?\n\}', _js(), re.S)
    assert m and 'td.tier_features_edited[tier] = lines.length > 0;' in m.group(0)


def test_picking_a_bundle_clears_the_rep_flag():
    """Swapping Best from laminate to standing seam replaces the bullets, so
    the card goes back to the default few — the same reset the tagline gets."""
    m = re.search(r'function applyBundleToTier\(trade, tier, bundleId, autoOpen\).*?\n\}',
                  _js(), re.S)
    assert m and 'td.tier_features_edited[tier] = false;' in m.group(0)


def test_the_browser_mirrors_the_default_and_the_flag():
    js = _js()
    assert 'const CARD_BULLET_DEFAULT = 6;' in js, (
        'the browser default drifted from _CARD_BULLET_DEFAULT in app.py'
    )
    assert 'function tierFeaturesEdited(trade, tier)' in js
    assert 'function tierCardBullets(trade, tier)' in js
    assert 'function autofillTierBullets(trade, tier)' in js, (
        'the browser needs its own copy of the line-item fallback, or the PDF '
        'and the /sign page describe the same package differently'
    )


@pytest.mark.parametrize('gone', ['+ ${disp[t].length-10} more', 'disp[t].slice(0,10)'])
def test_the_printed_card_no_longer_truncates(gone):
    """Anchored on the package card's own expression — a bare slice(0,10)
    matches an unrelated dashboard list and the test would never fail."""
    assert gone not in _js()
