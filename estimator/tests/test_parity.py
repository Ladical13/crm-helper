"""app.js <-> app.py pricing parity.

Pricing math is deliberately implemented twice: the rep's browser needs instant
recalculation as they type, and the server must compute money independently
because it can't trust the client (PDFs, customer view, signed contracts).

That duplication is by design and is not going away — so this test exists to
make drift impossible to miss. Every fixture is priced by the real functions in
static/app.js (via node) AND by app.py, and the two must agree to the cent.

If this fails: you changed pricing in one file and not the other.
"""
import json
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, 'parity_runner.js')

pytestmark = pytest.mark.skipif(shutil.which('node') is None,
                                reason='node not installed — parity cannot be checked')


def _gbb(qty, costs, **kw):
    it = {'name': 'x', 'quantity': qty,
          'tiers': {t: {'material_unit_cost': c[0], 'labor_unit_cost': c[1]}
                    for t, c in costs.items()}}
    it.update(kw)
    return it


STD = {'mode': 'margin', 'global_rate': 35,
       'tier_rates': {'good': 30, 'better': 35, 'best': 40},
       'per_trade_overrides': {}}


def _pricing(**kw):
    p = {'mode': 'margin', 'per_trade_overrides': {}}
    p.update(kw)
    return p


# (name, estimate) — each is priced by both engines at every tier.
FIXTURES = [
    ('typical margin', {'pricing': STD, 'trades': {'roofing': {
        'enabled': True, 'mode': 'gbb', 'selected_tier': 'better',
        'line_items': [_gbb(30, {'good': (100, 50), 'better': (130, 55), 'best': (170, 60)})]}}}),

    ('markup mode', {'pricing': dict(STD, mode='markup'), 'trades': {'roofing': {
        'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}),

    ('per-trade override', {'pricing': dict(STD, per_trade_overrides={'roofing': 50}),
        'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}),

    # per-trade, per-tier margins: each tier prices at its own rate
    ('per-trade per-tier margins', {'pricing': dict(STD, trade_rates={'roofing': {'good': 20, 'better': 50, 'best': 60}}),
        'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}),

    # partial trade_rates: better overridden, good/best inherit tier_rates
    ('trade_rates partial inherit', {'pricing': dict(STD, trade_rates={'roofing': {'better': 55}}),
        'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}),

    # per-tier trade rate outranks a legacy flat per-trade override; blank slots fall to it
    ('trade_rates over flat override', {'pricing': dict(STD, trade_rates={'roofing': {'better': 50}},
        per_trade_overrides={'roofing': 10}),
        'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}),

    # trade_rates are per-trade: a windows rate must not touch roofing
    ('trade_rates scoped per trade', {'pricing': dict(STD, trade_rates={'windows': {'better': 10}}),
        'trades': {
        'roofing': {'enabled': True, 'mode': 'gbb',
                    'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]},
        'windows': {'enabled': True, 'mode': 'gbb', 'selected_tier': 'better',
                    'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}),

    ('zero qty with price_override', {'pricing': STD, 'trades': {'roofing': {
        'enabled': True, 'mode': 'gbb', 'line_items': [
            {'name': 'x', 'quantity': 0, 'tiers': {t: {'material_unit_cost': 100,
                                                       'price_override': 5000}
                                                   for t in ('good', 'better', 'best')}}]}}}),

    ('price_override honored', {'pricing': STD, 'trades': {'roofing': {
        'enabled': True, 'mode': 'gbb', 'line_items': [
            {'name': 'x', 'quantity': 3, 'tiers': {'good': {'price_override': 1000},
                                                   'better': {'price_override': 2000},
                                                   'best': {'price_override': 3000}}}]}}}),

    ('included:false', {'pricing': STD, 'trades': {'roofing': {
        'enabled': True, 'mode': 'gbb', 'line_items': [
            {'name': 'x', 'quantity': 5, 'tiers': {'good': {'included': False},
                                                   'better': {'included': False},
                                                   'best': {'material_unit_cost': 20,
                                                            'labor_unit_cost': 0}}}]}}}),

    ('simple gutters', {'pricing': STD, 'trades': {'gutters': {
        'enabled': True, 'mode': 'simple',
        'line_items': [{'name': 'g', 'quantity': 100, 'unit_price': 9}]}}}),

    ('disabled trade', {'pricing': STD, 'trades': {'roofing': {
        'enabled': False, 'mode': 'gbb',
        'line_items': [_gbb(30, {'good': (100, 50), 'better': (130, 55), 'best': (170, 60)})]}}}),

    ('margin >= 100', {'pricing': dict(STD, tier_rates={'good': 100, 'better': 120, 'best': 40}),
        'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}),

    ('mixed per-trade tiers', {'pricing': STD, 'trades': {
        'roofing': {'enabled': True, 'mode': 'gbb', 'selected_tier': 'best',
                    'line_items': [_gbb(30, {'good': (100, 50), 'better': (130, 55), 'best': (170, 60)})]},
        'siding': {'enabled': True, 'mode': 'gbb', 'selected_tier': 'good',
                   'line_items': [_gbb(20, {'good': (80, 40), 'better': (90, 45), 'best': (110, 50)})]}}}),

    ('decimal quantity', {'pricing': STD, 'trades': {'roofing': {
        'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(12.33, {'good': (97.5, 33.25), 'better': (131.75, 44.1),
                                    'best': (168.4, 51.9)})]}}}),

    # ── fallback paths: these are where the two engines drifted apart ──
    ('global_rate=0, no tier_rates', {'pricing': _pricing(global_rate=0), 'trades': {'roofing': {
        'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}),

    ('no tier_rates, global 25', {'pricing': _pricing(global_rate=25), 'trades': {'roofing': {
        'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}),

    ('tier_rate = 0', {'pricing': _pricing(global_rate=35, tier_rates={'good': 0, 'better': 0, 'best': 0}),
        'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}),

    ('per_trade_override = 0', {'pricing': dict(STD, per_trade_overrides={'roofing': 0}),
        'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}),

    ('empty pricing', {'pricing': {}, 'trades': {'roofing': {
        'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}),

    ('global_rate missing', {'pricing': _pricing(), 'trades': {'roofing': {
        'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}),

    ('global_rate junk', {'pricing': _pricing(global_rate='abc'), 'trades': {'roofing': {
        'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}),

    # mode absent must mean margin, not markup, on both sides
    ('mode absent', {'pricing': {'global_rate': 35, 'per_trade_overrides': {}},
        'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}),

    # any non-'margin' mode means markup on both sides
    ('mode junk', {'pricing': _pricing(mode='wat', global_rate=35),
        'trades': {'roofing': {'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(10, {'good': (100, 0), 'better': (100, 0), 'best': (100, 0)})]}}}),

    # ── commercial ────────────────────────────────────────────────────
    # Commercial sells single-price, so it must total identically at every
    # tier. It is also the first trade whose mode DEFAULT is simple — if one
    # side resolves an unset mode to 'gbb' it reads flat items as tiered and
    # totals $0, which is invisible until a bid goes out at nothing.
    ('commercial simple', {'pricing': STD, 'trades': {'commercial': {
        'enabled': True, 'mode': 'simple',
        'line_items': [{'name': 'TPO', 'quantity': 44, 'unit_price': 140.85},
                       {'name': 'Edge', 'quantity': 500, 'unit_price': 2.82},
                       {'name': 'Labor', 'quantity': 40, 'unit_price': 563.38}]}}}),

    ('commercial mode absent defaults to simple', {'pricing': STD, 'trades': {'commercial': {
        'enabled': True,
        'line_items': [{'name': 'TPO', 'quantity': 44, 'unit_price': 140.85}]}}}),

    # The labor line that doesn't apply sits at qty 0 and must contribute
    # nothing on either side.
    ('commercial zero-qty labor line', {'pricing': STD, 'trades': {'commercial': {
        'enabled': True, 'mode': 'simple',
        'line_items': [{'name': 'TPO', 'quantity': 44, 'unit_price': 140.85},
                       {'name': 'Re-Roof Labor', 'quantity': 40, 'unit_price': 563.38},
                       {'name': 'New Const Labor', 'quantity': 0, 'unit_price': 352.11}]}}}),

    ('commercial gbb with an excluded line', {'pricing': STD, 'trades': {'commercial': {
        'enabled': True, 'mode': 'gbb',
        'line_items': [_gbb(44, {'good': (90, 380), 'better': (100, 400), 'best': (130, 400)}),
                       dict(_gbb(2, {'good': (250, 0), 'better': (250, 0), 'best': (250, 0)}),
                            tiers={'good':   {'material_unit_cost': 250, 'labor_unit_cost': 0, 'included': False},
                                   'better': {'material_unit_cost': 250, 'labor_unit_cost': 0},
                                   'best':   {'material_unit_cost': 250, 'labor_unit_cost': 0}})]}}}),

    # The 29% commercial default, scoped to the trade, must resolve the same
    # both sides — and must not leak onto roofing.
    ('commercial 29% trade rate', {
        'pricing': dict(STD, trade_rates={'commercial': {'simple': 29, 'good': 29,
                                                         'better': 29, 'best': 29}}),
        'trades': {'commercial': {'enabled': True, 'mode': 'gbb',
                                  'line_items': [_gbb(40, {'good': (100, 400), 'better': (100, 400), 'best': (100, 400)})]},
                   'roofing': {'enabled': True, 'mode': 'gbb',
                               'line_items': [_gbb(30, {'good': (100, 50), 'better': (130, 55), 'best': (170, 60)})]}},
        'rate_probe': {'trade': 'commercial', 'tier': 'better'}}),

    # The fastener counts the zone calculator produces, priced as ordinary EA
    # lines — closes the loop from commercial_fastening() through to the total.
    ('commercial fastener lines priced', {'pricing': STD, 'trades': {'commercial': {
        'enabled': True, 'mode': 'simple',
        'line_items': [{'name': 'Insulation Fasteners & Plates', 'quantity': 998, 'unit_price': 0.56},
                       {'name': 'Membrane Seam Fasteners & Plates', 'quantity': 830, 'unit_price': 0.70},
                       # An adhered system zeroes the seam line; it must not price.
                       {'name': 'Seam (n/a on this system)', 'quantity': 0, 'unit_price': 0.70}]}}}),

    # A commercial roof alongside residential trades, each at its own tier.
    ('commercial mixed with roofing', {'pricing': STD, 'trades': {
        'commercial': {'enabled': True, 'mode': 'simple', 'selected_tier': 'good',
                       'line_items': [{'name': 'TPO', 'quantity': 44, 'unit_price': 140.85}]},
        'roofing': {'enabled': True, 'mode': 'gbb', 'selected_tier': 'best',
                    'line_items': [_gbb(30, {'good': (100, 50), 'better': (130, 55), 'best': (170, 60)})]},
        'gutters': {'enabled': True, 'mode': 'simple',
                    'line_items': [{'name': 'g', 'quantity': 100, 'unit_price': 9}]}}}),
]


@pytest.fixture(scope='module')
def js_totals(tmp_path_factory):
    """Price every fixture with the real app.js functions under node."""
    d = tmp_path_factory.mktemp('parity')
    fx, out = d / 'fixtures.json', d / 'js.json'

    payload = []
    for name, est in FIXTURES:
        state = json.loads(json.dumps(est))
        # app.js indexes S.pricing.per_trade_overrides directly; the real client
        # guarantees it via migrateState() before any pricing runs.
        state.setdefault('pricing', {}).setdefault('per_trade_overrides', {})
        payload.append({'name': name, 'state': state})

    fx.write_text(json.dumps(payload), encoding='utf-8')
    proc = subprocess.run(['node', RUNNER, str(fx), str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f'parity_runner.js failed:\n{proc.stderr}'
    return {r['name']: r for r in json.loads(out.read_text(encoding='utf-8'))}


@pytest.mark.parametrize('name,est', FIXTURES, ids=[n for n, _ in FIXTURES])
@pytest.mark.parametrize('tier', ['good', 'better', 'best'])
def test_tier_total_matches_js(A, js_totals, name, est, tier):
    py = A.calc_tier_total(est, tier)
    js = js_totals[name][tier]
    assert py == pytest.approx(js, abs=0.01), (
        f'{name} @ {tier}: app.py={py:.2f} but app.js={js:.2f} — '
        'pricing changed in one file and not the other')


@pytest.mark.parametrize('name,est', FIXTURES, ids=[n for n, _ in FIXTURES])
def test_selected_total_matches_js(A, js_totals, name, est):
    py = A.calc_selected_total(est)
    js = js_totals[name]['selected']
    assert py == pytest.approx(js, abs=0.01), (
        f'{name} selected: app.py={py:.2f} but app.js={js:.2f}')


def test_runner_uses_the_real_bundle():
    """Guard the guard: if app.js is refactored so the functions can't be
    extracted, the parity test must fail loudly rather than silently pass."""
    proc = subprocess.run(['node', '-e', f'''
      const {{execFileSync}} = require('child_process');
      process.argv[2] = ''; process.argv[3] = '';
    '''], capture_output=True, text=True)
    src = open(os.path.join(HERE, '..', 'static', 'app.js'), encoding='utf-8').read()
    for fn in ('tierRate', 'tradeTotal', 'grandTotal', 'selectedTotal'):
        assert f'function {fn}(' in src, (
            f'app.js no longer defines {fn}() as a top-level function — '
            'parity_runner.js can no longer extract it; update the runner')
