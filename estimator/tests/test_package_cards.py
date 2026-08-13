"""What a Good/Better/Best card actually promises the customer.

The card's tagline and What's Included bullets live in
`td.tier_features` / `td.tier_descriptions`. They are written by a bundle pick
and, before it was retired, by the Options tab — and nothing rewrites them when
a rep builds the package by hand. That shipped an estimate whose Better card
sold an "Architectural laminate shingle system" with CertainTeed Landmark
bullets over a single hand-typed "Rolled Roofing" line item, and with the
Options tab gone there was no way for the rep to correct it.

So a tier's stored copy is trusted only while it still describes what that tier
sells. When it doesn't, the card is built from the tier's own line items.

The other half: `other` is a G/B/B trade by data shape only. Its Pricing tab
shows one tier at a time and writes cost and description to all three, so
offering it as a package printed three identical columns — on the estimate that
prompted this, "Other — Your Options: $0.00 / $0.00".
"""
import re

import pytest


BUNDLE = 'b_landmark'          # a real roofing bundle in the shipped price book

# Sentinels, not real copy. The page renders several things that legitimately
# quote the price book — a tier with no bundle set falls back to the book's
# default, and its material bullets are printed too — so asserting on real
# Landmark wording matches cards these tests aren't about.
STALE_BULLET  = 'ZZ-STALE-BULLET'
STALE_TAGLINE = 'ZZ-STALE-TAGLINE'


def _pb(A):
    return A._ensure_bundle_catalogs(A._load_price_book())


def _bundle_pids(A, bundle_id=BUNDLE):
    for b in _pb(A).get('roofing_bundles') or []:
        if b.get('id') == bundle_id:
            return list(b.get('product_ids') or [])
    raise AssertionError(f'{bundle_id} missing from the price book')


def _cell(cost=200, included=True):
    return {'material_unit_cost': cost, 'labor_unit_cost': 0,
            'description': '', 'notes': '', 'included': included}


def _roof_est(A, tier_bundles, line_items, **over):
    """A retail roofing estimate whose Better card carries stale bundle copy."""
    est = {
        'estimate_type': 'retail', 'salesperson': 'luke', 'selected_tier': 'better',
        'customer': {'name': 'Fran Gruchy', 'email': 'f@example.com',
                     'phone': '3035550101',
                     'address': {'street': '1 Gap Rd', 'city': 'Golden', 'state': 'CO'}},
        'shingle_selection': {'enabled': False, 'options': [], 'chosen': ''},
        'pricing': {'mode': 'margin', 'global_rate': 35, 'tier_rates': {},
                    'trade_rates': {}, 'per_trade_overrides': {}},
        'trades': {'roofing': {
            'enabled': True, 'mode': 'gbb', 'selected_tier': 'better',
            'tier_bundles': tier_bundles,
            'tier_features': {'good': [], 'best': [], 'better': [STALE_BULLET]},
            'tier_descriptions': {'good': '', 'best': '', 'better': STALE_TAGLINE},
            'line_items': line_items,
        }},
    }
    est.update(over)
    return est


def _bundle_items(A, qty=30):
    """Line items as applyBundleToTier builds them: catalog_id per product,
    included in Better only."""
    return [{'id': f'i{n}', 'catalog_id': pid, 'name': f'Product {n}',
             'unit': 'SQ', 'quantity': qty, 'customer_visible': True,
             'tiers': {'good': _cell(included=False),
                       'better': _cell(),
                       'best': _cell(included=False)}}
            for n, pid in enumerate(_bundle_pids(A))]


def _hand_item(qty=10):
    return {'id': 'hand1', 'name': 'Rolled Roofing', 'unit': 'SQ',
            'quantity': qty, 'customer_visible': True,
            'tiers': {'good': _cell(included=False),
                      'better': _cell(700),
                      'best': _cell(included=False)}}


def _customer_page(client, est):
    r = client.post('/api/estimates', json=est)
    assert r.status_code in (200, 201), r.data
    est_id = r.get_json()['estimate_id']
    token = client.post(f'/api/estimates/{est_id}/share').get_json()['token']
    page = client.get(f'/sign/{token}')
    assert page.status_code == 200
    return page.get_data(as_text=True)


# ── The tier still sells what the copy describes ────────────────────────────

def test_a_tier_still_on_its_bundle_keeps_the_bundle_copy(client, A):
    """The normal case, and the reason this isn't just "always use line items":
    "may qualify for an insurance premium discount" is sales copy no line item
    can express, and it is accurate as long as the bundle is what's priced."""
    est = _roof_est(A, {'good': '', 'better': BUNDLE, 'best': ''},
                    _bundle_items(A))
    html = _customer_page(client, est)
    assert STALE_BULLET in html
    assert STALE_TAGLINE in html


def test_a_custom_tier_drops_the_previous_bundle_copy(client, A):
    """The reported bug. Better was seeded from Landmark, then switched to
    Custom and given one hand-typed line — the Landmark bullets and tagline
    stayed on the card and sold a shingle roof the estimate does not contain."""
    items = _bundle_items(A)
    for it in items:                       # Custom excludes the bundle's products
        it['tiers']['better']['included'] = False
    est = _roof_est(A, {'good': '', 'better': '__custom__', 'best': ''},
                    items + [_hand_item()])
    html = _customer_page(client, est)
    assert STALE_BULLET not in html
    assert STALE_TAGLINE not in html, (
        'the tagline names a system too — it goes stale with the bullets'
    )
    assert 'Rolled Roofing' in html, 'the card must list what the tier actually sells'


def test_replacing_every_bundle_product_by_hand_also_drops_the_copy(client, A):
    """A rep can gut a tier without ever touching the bundle dropdown. The
    dropdown still says Landmark; not one Landmark product is priced."""
    items = _bundle_items(A, qty=0)        # bundle products left unmeasured
    est = _roof_est(A, {'good': '', 'better': BUNDLE, 'best': ''},
                    items + [_hand_item()])
    html = _customer_page(client, est)
    assert STALE_BULLET not in html
    assert 'Rolled Roofing' in html


def test_a_hand_shaped_trade_keeps_its_own_bullets(client, A):
    """No line item carries a catalog_id, so bundles never built this trade and
    there is no leftover bundle copy to suspect — even on a Custom tier. Those
    bullets are the rep's own and are the best copy the estimate will ever have,
    because the editor that wrote them is gone."""
    est = _roof_est(A, {'good': '', 'better': '__custom__', 'best': ''},
                    [_hand_item()])
    assert STALE_BULLET in _customer_page(client, est)


def test_a_pre_bundle_estimate_is_never_judged(A):
    """No tier_bundles key at all — the estimate predates bundles entirely."""
    est = _roof_est(A, None, [_hand_item()])
    del est['trades']['roofing']['tier_bundles']
    assert A._tier_bullets_are_stale(_pb(A), est, 'roofing', 'better') is False


def test_a_bundle_deleted_from_the_price_book_makes_no_call(A):
    """The manager removed the bundle. Its copy may be the only description of
    this package left; guessing it stale would replace real sales copy with a
    parts list on nothing more than a missing lookup."""
    est = _roof_est(A, {'good': '', 'better': 'b_no_such_bundle', 'best': ''},
                    _bundle_items(A))
    assert A._tier_bullets_are_stale(_pb(A), est, 'roofing', 'better') is False


# ── `other` is not a package choice ─────────────────────────────────────────

def test_other_is_never_offered_as_a_package(A):
    est = {'trades': {
        'roofing': {'enabled': True, 'mode': 'gbb', 'line_items': [_hand_item()]},
        'other':   {'enabled': True, 'mode': 'gbb', 'line_items': [_hand_item()]},
    }}
    assert 'other' in A._gbb_trade_keys(est), 'still a G/B/B trade by data shape'
    assert A._package_trade_keys(est) == ['roofing'], (
        'the Other tab writes cost and description to all three tiers, so a '
        'package card for it is three identical columns'
    )


def test_other_items_still_reach_the_customer(client, A):
    """Dropping the package card must not drop the trade. Other renders with
    the simple-mode trades and its own G/B/B math still applies — it is only
    the Good/Better/Best *choice* that goes away."""
    est = _roof_est(A, {'good': '', 'better': BUNDLE, 'best': ''}, _bundle_items(A))
    est['trades']['other'] = {
        'enabled': True, 'mode': 'gbb', 'selected_tier': 'better',
        'line_items': [{'id': 'o1', 'name': 'Dumpster & haul-away', 'unit': 'EA',
                        'quantity': 1, 'customer_visible': True,
                        'tiers': {t: _cell(500) for t in ('good', 'better', 'best')}}],
    }
    html = _customer_page(client, est)
    assert 'Dumpster &amp; haul-away' in html or 'Dumpster & haul-away' in html
    # 500 / 0.65 = 769.23, and it is in the number the customer signs
    assert A.calc_selected_total(est) == pytest.approx(
        A._trade_subtotal(est, 'roofing', 'better') + 500 / 0.65)


# ── Both implementations, one rule ──────────────────────────────────────────

def _js():
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, '..', 'static', 'app.js'), encoding='utf-8') as fh:
        return fh.read()


def test_the_browser_mirrors_the_staleness_rule():
    """Print builds its cards in the browser and the customer page builds them
    on the server. A rule that lived in only one of them would put a different
    promise on the PDF than on the link the customer signs."""
    m = re.search(r'function tierBulletsAreStale\(trade, tier\).*?\n\}', _js(), re.S)
    assert m, 'tierBulletsAreStale not found in app.js'
    body = m.group(0)
    assert "'__custom__'" in body
    assert 'catalog_id' in body, 'the bundle-built guard has to be on both sides'
    assert 'tier_bundles' in body


def test_print_uses_the_rule_for_both_bullets_and_tagline():
    js = _js()
    assert re.search(r'if\(f&&f\.length&&!tierBulletsAreStale\(trade,t\)\)', js), (
        'the print cards went back to preferring the stored bullets outright'
    )
    assert 'tierBulletsAreStale(gt,t)?\'\':' in js, (
        'the printed tagline names a system — it must follow the same rule'
    )


def test_print_leaves_other_out_of_the_options_comparison():
    js = _js()
    m = re.search(r'function packageTrades\(\)[^\n]*\n', js)
    assert m and "!== 'other'" in m.group(0), 'packageTrades no longer drops other'
    assert 'if (pv.options !== false && !isAllSimple) packageTrades()' in js, (
        'the options comparison went back to every G/B/B trade, so an Other tab '
        'with items prints a $0.00 / $0.00 package card'
    )


def test_print_gives_every_offered_package_its_own_scope_table():
    """Online the customer taps between packages and the item list swaps
    underneath. On paper there is nothing to tap, so printing only the selected
    tier put the Best package's price on the options card with its scope nowhere
    in the document — a TPO roof quoted at $19,461 and never described."""
    js = _js()
    m = re.search(r'const tiers=\(tradeMode===\'simple\'\|\|!allPkgs\)\?\[selTier\]:enabledTiers\(\);', js)
    assert m, (
        'the trade loop went back to a single tier — every offered package '
        'needs its own detail table'
    )
    assert 'const allPkgs = pv.allPackages !== false;' in js, (
        'the All Packages print chip must default ON'
    )


def test_print_collapses_packages_that_sell_the_same_thing():
    """Three copies of one table is not a comparison. Two packages merge only
    when scope AND price match — `sig` is the rendered rows plus the subtotal,
    so a tier that differs by one line or one dollar still prints separately."""
    js = _js()
    m = re.search(r'function printTradeBody\(trade, tier, o\).*?\n\}', js, re.S)
    assert m, 'printTradeBody not found'
    assert "sig: body + '|' + subtotal.toFixed(2)" in m.group(0), (
        'the signature stopped covering price, so two packages with the same '
        'scope at different money would collapse into one'
    )
    assert 'if(last&&last.sig===b.sig){ last.tiers.push(b.tier); return; }' in js


def test_an_unbuilt_package_prints_no_empty_table():
    """A tier the rep never built has no items in scope. It must not print a
    heading over an empty table with a $0.00 subtotal — that reads as a package
    being offered for nothing."""
    m = re.search(r'function printTradeBody\(trade, tier, o\).*?\n\}', _js(), re.S)
    body = m.group(0)
    assert "if (!inTier.length) return { body: '', subtotal: 0, sig: '' };" in body
    assert "if (!body) return { body: '', subtotal: 0, sig: '' };" in body, (
        'a tier whose only items are customer-hidden still renders no rows'
    )
    assert '.filter(b=>b.body);' in _js(), 'the caller must drop the empty tiers'


def test_the_grand_total_names_its_package_when_several_are_shown():
    """"Project Total $10,907.69" printed under a Best subtotal of $19,461.54
    reads as arithmetic that doesn't add up."""
    js = _js()
    assert "const totalLbl=multiPkgPrinted" in js
    assert "'Project Total — '" in js
    assert re.search(r"if\(!uniform\) multiPkgPrinted=true;", js), (
        'packages that all sell the same scope print one plain table, so the '
        'total should stay plain too'
    )


def test_a_new_other_row_starts_at_quantity_one():
    """tradeTotal drops zero-qty lines even when the sell price is locked, so a
    hand-entered allowance with a blank qty priced at $0 and never printed —
    while the box beside it showed the number the rep typed. The qty input has
    always shown a "1" placeholder; the value now matches it."""
    m = re.search(r'function addLineItem\(trade, tier\)\s*\{.*?\n\}', _js(), re.S)
    assert m, 'addLineItem not found'
    assert "quantity: trade==='other' ? 1 : 0" in m.group(0), (
        'Other rows are back to qty 0 — no measurement ever fills them in'
    )


def test_changing_an_other_quantity_keeps_the_price_the_rep_typed():
    """liSetQty deletes every locked price, which is right for a G/B/B trade —
    a locked line total was locked against a quantity, so 10 SQ and 40 SQ must
    not both cost $4,000, and the item's cost still drives a number afterwards.
    On the Other tab the sell price is the ONLY price a rep enters, so deleting
    it left $0, a $0 subtotal, and a row that never printed."""
    js = _js()
    m = re.search(r'function otherSetQty\(id, v\).*?\n\}', js, re.S)
    assert m, 'otherSetQty not found'
    body = m.group(0)
    assert 'delete' not in body, 'the Other tab is wiping locked prices again'
    assert 'cell.price_override = Math.round(unit * newQty * 100) / 100;' in body, (
        'the per-unit price the rep typed has to survive a quantity change'
    )
    assert "onchange=\"otherSetQty('${item.id}',this.value)\"" in js, (
        'the Other qty box went back to liSetQty'
    )
    # liSetQty calls rerender() but never renderTradeContent(), so the Other
    # table kept showing money the data no longer held.
    assert 'if (activePage === \'pricing\') renderTradeContent();' in body


def test_the_other_sell_price_box_is_per_unit():
    """It sits beside "Unit Cost" and matches the Simple tab. price_override
    still stores the LINE TOTAL — tradeTotal, the PDF and the server all read
    that — so the box divides on the way in and multiplies on the way out, and
    no existing estimate changes value."""
    js = _js()
    assert 'const unitSell = qty > 0 ? tot / qty : 0;' in js
    m = re.search(r'function otherSetPrice\(id, value\).*?\n\}', js, re.S)
    assert m and 'Math.round(unit * (qty > 0 ? qty : 1) * 100) / 100' in m.group(0), (
        'otherSetPrice stopped converting the per-unit box back to a line total'
    )
    assert '<th class="other-th-price">Total</th>' in js, (
        'the Total column is what makes the per-unit box unambiguous'
    )
    assert '<td colspan="5" style="text-align:right' in js, (
        'the Subtotal row has to span the new column count'
    )


def test_a_zero_qty_other_row_shows_the_zero_it_contributes():
    """tradeTotal drops zero-qty lines, so printing the stored locked figure in
    the Total column contradicted the subtotal directly below it."""
    assert 'fmtCur(qty > 0 ? tot : 0)' in _js()


def test_the_other_tab_locks_a_sell_price_on_every_tier():
    """The tab renders ONE tier and has no package UI, so a per-tier override
    was invisible: a $500 allowance typed on Better was $0 on Good and Best."""
    js = _js()
    assert re.search(r'function otherSetPrice\(id, value\)\s*\{[^}]*TIERS\.forEach', js, re.S), (
        'otherSetPrice must write every tier, like otherSetUnitCost and otherSetDesc'
    )
    assert "onchange=\"otherSetPrice('${item.id}',this.value)\"" in js, (
        'the Other tab price box went back to the per-tier liSetPriceOverride'
    )
