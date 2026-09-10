"""The material / labor split — derived at read time, and never moving a total.

The Cost & Profit panel reported Labor $0.00 on every estimate ever written,
because every seeding path dropped a product's single `cost` into
material_unit_cost and hard-wrote labor_unit_cost: 0. Labor was priced the whole
time — l_install has been $145/SQ on production — it was just filed as material.

Two properties make it safe to fix that retroactively, and this file exists to
hold both:

1. **Nothing stored is rewritten.** The split is derived from the catalog on
   every read, so an estimate written months ago reports a real split the moment
   it is opened. Same house rule as _norm_est_status: normalize on read.

2. **The split is a whole-line bucket assignment, never a ratio**, so
   material + labor equals the cost that was already there BY CONSTRUCTION. Total
   Cost, Sell Price, the margin floors, the analytics tab and every
   customer-facing number read that sum, and the sum cannot move.

If test_the_split_never_changes_the_total fails, something started prorating and
a customer's price is now downstream of a manager's bookkeeping choice.
"""
import json
import os
import shutil
import subprocess

import pytest

import app as A

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, 'cost_split_runner.js')


# ── Fixtures ──────────────────────────────────────────────────────────────

PB = {
    'roofing_catalog': [
        {'id': 'm_shingle', 'name': 'Shingles', 'unit': 'SQ', 'cost': 137,
         'cost_class': 'material'},
        {'id': 'l_install', 'name': 'Install Labor', 'unit': 'SQ', 'cost': 145,
         'cost_class': 'labor'},
        {'id': 'x_dumpster', 'name': 'Dumpster', 'unit': 'LS', 'cost': 600,
         'cost_class': 'material'},
        # No cost_class at all — must read as material, i.e. today's behaviour.
        {'id': 'a_drip', 'name': 'Drip Edge', 'unit': 'LF', 'cost': 2},
    ],
    'roofing_bundles': [],
    'gutters_catalog': [
        {'id': 'gl_install', 'name': 'Gutter Install Labor', 'unit': 'LF',
         'cost': 3, 'cost_class': 'labor'},
        {'id': 'gm_kstyle', 'name': 'K-Style Gutter', 'unit': 'LF', 'cost': 5,
         'cost_class': 'material'},
    ],
    'gutters_bundles': [],
}

STD_PRICING = {'mode': 'margin', 'global_rate': 35,
               'tier_rates': {'good': 30, 'better': 35, 'best': 40},
               'per_trade_overrides': {}}


def _cell(mat, lab=0):
    return {'material_unit_cost': mat, 'labor_unit_cost': lab, 'included': True}


def _gbb(name, qty, mat, lab=0, **kw):
    it = {'name': name, 'quantity': qty,
          'tiers': {t: _cell(mat, lab) for t in ('good', 'better', 'best')}}
    it.update(kw)
    return it


def _est(trades, **kw):
    e = {'pricing': json.loads(json.dumps(STD_PRICING)),
         'selected_tier': 'better', 'trades': trades}
    e.update(kw)
    return e


FIXTURES = [
    ('labor line joined by catalog_id', _est({'roofing': {
        'enabled': True, 'mode': 'gbb', 'line_items': [
            _gbb('Shingles', 30, 137, catalog_id='m_shingle'),
            _gbb('Install Labor', 30, 145, catalog_id='l_install'),
        ]}})),

    ('labor line joined only by name', _est({'roofing': {
        'enabled': True, 'mode': 'gbb', 'line_items': [
            _gbb('Shingles', 30, 137),
            _gbb('Install Labor', 30, 145),
        ]}})),

    ('a name the catalog has never heard of', _est({'roofing': {
        'enabled': True, 'mode': 'gbb', 'line_items': [
            _gbb('Something hand typed', 10, 50),
        ]}})),

    ('an unclassified product stays material', _est({'roofing': {
        'enabled': True, 'mode': 'gbb', 'line_items': [
            _gbb('Drip Edge', 200, 2, catalog_id='a_drip'),
        ]}})),

    ('an explicit labor_unit_cost wins', _est({'roofing': {
        'enabled': True, 'mode': 'gbb', 'line_items': [
            _gbb('Shingles', 30, 100, 40, catalog_id='m_shingle'),
        ]}})),

    ('a job extra is material not labor', _est({'roofing': {
        'enabled': True, 'mode': 'gbb', 'line_items': [
            _gbb('Dumpster', 1, 600, catalog_id='x_dumpster'),
            _gbb('Install Labor', 30, 145, catalog_id='l_install'),
        ]}})),

    ('excluded and zero-qty lines are skipped', _est({'roofing': {
        'enabled': True, 'mode': 'gbb', 'line_items': [
            _gbb('Install Labor', 0, 145, catalog_id='l_install'),
            dict(_gbb('Shingles', 30, 137, catalog_id='m_shingle'),
                 tiers={'good': dict(_cell(137), included=False),
                        'better': _cell(137), 'best': _cell(137)}),
        ]}})),

    ('simple mode splits the same way', _est({'gutters': {
        'enabled': True, 'mode': 'simple', 'line_items': [
            {'name': 'K-Style Gutter', 'quantity': 120, 'unit_cost': 5,
             'unit_price': 9, 'catalog_id': 'gm_kstyle'},
            {'name': 'Gutter Install Labor', 'quantity': 120, 'unit_cost': 3,
             'unit_price': 6, 'catalog_id': 'gl_install'},
        ]}})),

    ('simple and gbb together', _est({
        'roofing': {'enabled': True, 'mode': 'gbb', 'line_items': [
            _gbb('Install Labor', 30, 145, catalog_id='l_install')]},
        'gutters': {'enabled': True, 'mode': 'simple', 'line_items': [
            {'name': 'K-Style Gutter', 'quantity': 120, 'unit_cost': 5,
             'unit_price': 9, 'catalog_id': 'gm_kstyle'}]},
    })),
]

IDS = [n for n, _ in FIXTURES]


# ── The Python side, on its own ───────────────────────────────────────────

def _py_lines(est, trade, tier='better'):
    """Every line's class and split, the way _cost_split_by_trade walks them."""
    td = (est.get('trades') or {}).get(trade) or {}
    by_name = A._catalog_class_by_name(PB, trade)
    simple = A._trade_mode(trade, td) == 'simple'
    out = []
    for item in td.get('line_items') or []:
        qty = float(item.get('quantity') or 0)
        if simple:
            m, l = A._simple_cost_split(PB, trade, item, qty, by_name)
        else:
            cell = (item.get('tiers') or {}).get(tier) or {}
            m, l = A._line_cost_split(PB, trade, item, cell, qty, by_name)
        out.append({'name': item.get('name') or '',
                    'cls': A._cost_class_of(PB, trade, item, by_name),
                    'material': round(m, 2), 'labor': round(l, 2)})
    return out


@pytest.mark.parametrize('name,est', FIXTURES, ids=IDS)
def test_the_split_never_changes_the_total(name, est):
    """The one invariant everything else rests on. material + labor must equal
    the cost that was already stored, to the cent — not close to it. A ratio
    would pass most of these and fail on a rounding boundary, in production,
    on a number a customer is looking at."""
    for trade, td in (est.get('trades') or {}).items():
        simple = A._trade_mode(trade, td) == 'simple'
        for item, row in zip(td['line_items'], _py_lines(est, trade)):
            qty = float(item.get('quantity') or 0)
            if simple:
                before = A._mnum(item.get('unit_cost')) * qty
            else:
                cell = (item.get('tiers') or {}).get('better') or {}
                before = (A._mnum(cell.get('material_unit_cost'))
                          + A._mnum(cell.get('labor_unit_cost'))) * qty
            assert row['material'] + row['labor'] == pytest.approx(before, abs=0.005), (
                f'{name}: {row["name"]} split to '
                f'{row["material"]} + {row["labor"]} but stored cost was {before}')


def test_a_labor_product_moves_its_cost_out_of_material():
    """The actual bug: l_install at $145/SQ was reported as material."""
    rows = _py_lines(FIXTURES[0][1], 'roofing')
    lab = next(r for r in rows if r['name'] == 'Install Labor')
    assert lab['cls'] == 'labor'
    assert lab['labor'] == 4350.0 and lab['material'] == 0


def test_a_line_with_no_catalog_id_falls_back_to_its_name():
    """About half the estimates on the volume predate catalog_id. Template-built
    lines carry the exact seed names, so the fallback reaches nearly all of
    them — without it the fix would only help estimates written this year."""
    rows = _py_lines(FIXTURES[1][1], 'roofing')
    assert next(r for r in rows if r['name'] == 'Install Labor')['cls'] == 'labor'


def test_a_name_that_matches_nothing_stays_material():
    """Giving up lands on exactly what the panel reported before any of this
    existed, so an unclassifiable line is the status quo, not a regression."""
    rows = _py_lines(FIXTURES[2][1], 'roofing')
    assert rows[0]['cls'] == 'material' and rows[0]['labor'] == 0


def test_a_product_with_no_class_reads_as_material():
    """Absence is the test. a_drip carries no cost_class at all."""
    rows = _py_lines(FIXTURES[3][1], 'roofing')
    assert rows[0]['cls'] == 'material' and rows[0]['material'] == 400.0


def test_an_explicit_labor_unit_cost_wins_over_the_class():
    """Nothing writes this today, but change-order items carry the shape and a
    stored split must never be overruled by a guess about the product."""
    rows = _py_lines(FIXTURES[4][1], 'roofing')
    assert rows[0]['material'] == 3000.0 and rows[0]['labor'] == 1200.0


def test_a_simple_mode_line_is_split_the_same_way():
    """tierProfit's simple branch hard-coded labor: 0, so a trade flipped to
    Simple reported its whole cost as material no matter what it was."""
    rows = _py_lines(FIXTURES[7][1], 'gutters')
    lab = next(r for r in rows if r['name'] == 'Gutter Install Labor')
    assert lab['cls'] == 'labor' and lab['labor'] == 360.0 and lab['material'] == 0


# ── The permit packet ─────────────────────────────────────────────────────

@pytest.mark.parametrize('name,est', FIXTURES, ids=IDS)
def test_the_permit_packet_cost_total_is_unchanged_by_the_split(name, est):
    """The packet prints Cost Total = materials + labor, and a jurisdiction may
    fee on it. Only the two columns beside it are allowed to move."""
    rows = A._cost_split_by_trade(est, PB)
    for r in rows:
        td = est['trades'][r['trade']]
        tier = A._trade_tier(est, r['trade'])
        expect = A._trade_cost_subtotal(est, r['trade'], tier)
        assert r['materials_cost'] + r['labor_cost'] == pytest.approx(expect, abs=0.01), (
            f'{name}: {r["trade"]} cost total moved')


def test_an_unclassified_book_reports_exactly_what_it_reported_before():
    """The golden master. With no cost_class anywhere, every line must land in
    material and every labor figure must be zero — byte-identical to the
    behaviour this replaced. If this fails the change is not backwards
    compatible and old estimates would move."""
    bare = {'roofing_catalog': [{'id': p['id'], 'name': p['name'], 'cost': p['cost']}
                                for p in PB['roofing_catalog']],
            'gutters_catalog': [{'id': p['id'], 'name': p['name'], 'cost': p['cost']}
                                for p in PB['gutters_catalog']]}
    for name, est in FIXTURES:
        for r in A._cost_split_by_trade(est, bare):
            tier = A._trade_tier(est, r['trade'])
            expect_lab = sum(
                A._mnum(((i.get('tiers') or {}).get(tier) or {}).get('labor_unit_cost'))
                * float(i.get('quantity') or 0)
                for i in est['trades'][r['trade']].get('line_items') or [])
            assert r['labor_cost'] == pytest.approx(expect_lab, abs=0.01), (
                f'{name}: an unclassified book reported labor that was not stored')


def test_the_split_reads_the_catalog_and_never_writes_the_estimate():
    """Nothing may be persisted onto a saved estimate. The whole reason this
    could be applied to every old estimate is that it touches none of them."""
    est = json.loads(json.dumps(FIXTURES[0][1]))
    before = json.dumps(est, sort_keys=True)
    A._cost_split_by_trade(est, PB)
    assert json.dumps(est, sort_keys=True) == before


# ── JS <-> PY ─────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def js(tmp_path_factory):
    """Split every fixture with the real app.js functions under node."""
    if shutil.which('node') is None:
        pytest.skip('node not installed')
    d = tmp_path_factory.mktemp('costsplit')
    fx, out = d / 'fixtures.json', d / 'js.json'
    fx.write_text(json.dumps(
        [{'name': n, 'state': json.loads(json.dumps(e)), 'price_book': PB}
         for n, e in FIXTURES]), encoding='utf-8')
    proc = subprocess.run(['node', RUNNER, str(fx), str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f'cost_split_runner.js failed:\n{proc.stderr}'
    return {r['name']: r for r in json.loads(out.read_text(encoding='utf-8'))}


pytestmark_node = pytest.mark.skipif(
    shutil.which('node') is None,
    reason='node not installed — the split mirror cannot be checked')


@pytestmark_node
@pytest.mark.parametrize('name,est', FIXTURES, ids=IDS)
def test_js_and_py_classify_every_line_the_same_way(js, name, est):
    """Two implementations, one rule. A keyword added to the guess on one side
    and not the other puts a line in Material on the screen and Labor in the
    permit packet, for the same job."""
    for trade in (est.get('trades') or {}):
        want = _py_lines(est, trade)
        got = js[name]['lines'].get(trade) or []
        assert [r['cls'] for r in got] == [r['cls'] for r in want], (
            f'{name}/{trade}: js={[r["cls"] for r in got]} '
            f'py={[r["cls"] for r in want]}')


@pytestmark_node
@pytest.mark.parametrize('name,est', FIXTURES, ids=IDS)
def test_js_and_py_split_every_line_to_the_same_cent(js, name, est):
    for trade in (est.get('trades') or {}):
        want = _py_lines(est, trade)
        got = js[name]['lines'].get(trade) or []
        assert len(got) == len(want)
        for g, w in zip(got, want):
            assert g['material'] == pytest.approx(w['material'], abs=0.01), (
                f'{name}/{trade}/{w["name"]} material')
            assert g['labor'] == pytest.approx(w['labor'], abs=0.01), (
                f'{name}/{trade}/{w["name"]} labor')


@pytestmark_node
def test_the_runner_uses_the_real_bundle():
    """If the runner ever stops lifting from app.js it agrees with itself
    forever while the shipped bundle drifts."""
    src = open(RUNNER, encoding='utf-8').read()
    assert 'static' in src and 'app.js' in src
    for name in ('costClassOf', 'lineCostSplit', 'simpleCostSplit', 'tierProfit',
                 'COST_CLASS_LABOR_WORDS'):
        assert name in src, f'{name} is no longer lifted from app.js'
