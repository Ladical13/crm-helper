"""The Other tab has to charge for what the rep typed.

Its rows are hand-entered one-offs — an allowance, a haul-away, a deck rebuild
— so nothing measures them and nothing fills their quantity. A zero quantity is
dropped by BOTH totals (tradeTotal in app.js, _trade_subtotal in app.py), so a
row with a real cost in it showed a 0.00 sell price, added nothing to the
subtotal and never printed, while looking perfectly filled in.

Two guards, both tested here against the real functions in static/app.js:
opening an estimate heals the legacy rows, and typing money into a row gives it
the quantity of 1 the box has always shown as a placeholder.
"""
import json
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, 'other_qty_runner.js')
APP_JS = os.path.join(HERE, '..', 'static', 'app.js')

pytestmark = pytest.mark.skipif(shutil.which('node') is None,
                                reason='node not installed — the real app.js cannot be run')

MARGIN = {'mode': 'margin', 'global_rate': 38,
          'tier_rates': {'good': 38, 'better': 38, 'best': 38},
          'per_trade_overrides': {}}


def _row(qty, cost=0.0, override=None, name='New Deck'):
    """One Other-tab row: the tab writes the same cost to all three tiers."""
    tiers = {t: {'material_unit_cost': cost, 'labor_unit_cost': 0} for t in
             ('good', 'better', 'best')}
    if override is not None:
        for t in tiers:
            tiers[t]['price_override'] = override
    return {'id': 'i1', 'name': name, 'unit': 'EA', 'quantity': qty, 'tiers': tiers}


def _est(rows, **kw):
    est = {'pricing': MARGIN,
           'trades': {'other': {'enabled': True, 'mode': 'gbb',
                                'selected_tier': 'better', 'line_items': rows}}}
    est.update(kw)
    return est


# (name, estimate, expected heal count, expected quantities after)
HEAL = [
    # The bug as it reached a real estimate: a $9,548.65 deck, priced at $0.
    ('legacy cost row', _est([_row(0, cost=9548.65)]), 1, [1]),
    ('legacy locked price', _est([_row(0, override=15000)]), 1, [1]),
    # An empty row is scaffolding, not money — leave it where the rep put it.
    ('empty row untouched', _est([_row(0, name='')]), 0, [0]),
    # A quantity the rep actually set is theirs, healed or not.
    ('real quantity kept', _est([_row(2, cost=500)]), 0, [2]),
    # After signature the total is a number the customer agreed to; it moves
    # through a change order, not through a migration that runs on open.
    ('signed estimate skipped', _est([_row(0, cost=9548.65)],
                                     signature={'name': 'Fran Gruchy'}), 0, [0]),
    ('mixed rows', _est([_row(0, cost=100), _row(0, name=''), _row(4, cost=50)]),
     1, [1, 0, 4]),
]

# (name, quantity on the row, what the rep typed, expected quantity after)
ENSURE = [
    ('cost into a blank row', 0, 9548.65, 1),
    ('cost into an empty-string row', '', 9548.65, 1),
    ('a set quantity is never overwritten', 3, 9548.65, 3),
    ('clearing the box conjures nothing', 0, '', 0),
    ('a zero price conjures nothing', 0, 0, 0),
]


@pytest.fixture(scope='module')
def js(tmp_path_factory):
    """Run the real app.js guards under node."""
    d = tmp_path_factory.mktemp('other-qty')
    fx, out = d / 'fixtures.json', d / 'out.json'
    payload = {
        'heal': [{'name': n, 'est': json.loads(json.dumps(e))} for n, e, _, _ in HEAL],
        'ensure': [{'name': n, 'quantity': q, 'entered': v} for n, q, v, _ in ENSURE],
    }
    fx.write_text(json.dumps(payload), encoding='utf-8')
    proc = subprocess.run(['node', RUNNER, str(fx), str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, 'other_qty_runner.js failed: ' + proc.stderr
    res = json.loads(out.read_text(encoding='utf-8'))
    return {'heal': {r['name']: r for r in res['heal']},
            'ensure': {r['name']: r for r in res['ensure']}}


@pytest.mark.parametrize('name,est,healed,quantities', HEAL,
                         ids=[h[0] for h in HEAL])
def test_open_heals_zero_qty_rows(js, name, est, healed, quantities):
    got = js['heal'][name]
    assert got['healed'] == healed, f'{name}: healed {got["healed"]} rows, expected {healed}'
    assert got['quantities'] == quantities, f'{name}: quantities {got["quantities"]}'


@pytest.mark.parametrize('name,quantity,entered,expected', ENSURE,
                         ids=[e[0] for e in ENSURE])
def test_typing_money_gives_the_row_a_quantity(js, name, quantity, entered, expected):
    assert js['ensure'][name]['quantity'] == expected


def test_healed_row_actually_prices_on_the_server(A, js):
    """The point of the fix: the server — which builds the PDF, the customer
    view and the signed contract — must now charge for the deck."""
    est = js['heal']['legacy cost row']['est']
    total = A.calc_tier_total(est, 'better')
    assert total == pytest.approx(9548.65 / (1 - 0.38), abs=0.01), (
        f'healed row still prices at {total:.2f} — the fix never reached the money')


def test_signed_estimate_still_prices_at_zero(A, js):
    """The other half of that: a signed estimate is left exactly as signed."""
    est = js['heal']['signed estimate skipped']['est']
    assert A.calc_tier_total(est, 'better') == 0


def test_guards_are_wired_into_the_ui():
    """Guard the guard. The functions can be perfect and still never run: this
    fails loudly if app.js stops calling them, or renames them out from under
    other_qty_runner.js."""
    src = open(APP_JS, encoding='utf-8').read()
    for fn in ('healOtherZeroQty', 'otherEnsureQty'):
        assert f'function {fn}(' in src, (
            f'app.js no longer defines {fn}() as a top-level function — '
            'other_qty_runner.js can no longer extract it; update the runner')
    assert 'healOtherZeroQty(S)' in src, (
        'doLoadEstimate no longer heals zero-qty Other rows on open')
    for setter in ('otherSetUnitCost', 'otherSetPrice'):
        i = src.index(f'function {setter}(')
        assert 'otherEnsureQty(' in src[i:i + 400], (
            f'{setter}() no longer gives a zero-qty row a quantity — '
            'a cost typed into the Other tab will price at $0 again')
