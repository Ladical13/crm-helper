"""Base labor folds into the main material line; unit prices are opt-in.

Internally labor stays its own line — the Cost & Profit panel, the permit
packet's Labor column and the work order read the raw items. On the customer's
documents a hidden line (customer_visible: false — how base labor is marked)
used to simply vanish: the subtotal still held it, but with prices showing the
rows no longer added up and a note papered over the gap. Now its price rides
inside the covering's row — the shingle, the siding, the window — so the rows
sum to the subtotal and "Install Labor — $9,400" never appears. Extras a
manager leaves visible (steep, an extra layer, two-story) stay their own row.

And the per-unit sell price is hidden by default on every customer document,
with a per-document toggle: the line total is what the customer is buying.
A document signed before any of this keeps the shape it was signed in.
"""
import io
import json
import os
import shutil
import subprocess

import pytest

import app as A

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, 'labor_fold_runner.js')
PRICING = {'mode': 'margin', 'rate': 35,
           'tier_rates': {'good': 35, 'better': 35, 'best': 35}}


def _item(name, qty, cost, **extra):
    return dict({'name': name, 'quantity': qty, 'unit': 'SQ',
                 'tiers': {t: {'material_unit_cost': cost, 'labor_unit_cost': 0,
                               'description': ''} for t in ('good', 'better', 'best')}},
                **extra)


def _est(items=None, signature=None, **pv):
    est = {
        'estimate_id': 'labor-fold', 'estimate_type': 'retail', 'pricing': PRICING,
        'selected_tier': 'better', 'page_visibility': pv,
        'customer': {'name': 'Pat Homeowner', 'address': {}},
        'trades': {'roofing': {'enabled': True, 'mode': 'gbb', 'line_items': items or [
            _item('Shingles', 30, 100, catalog_id='m_landmark'),
            _item('Drip Edge', 30, 10, catalog_id='a_drip_edge'),
            _item('Install Labor', 30, 145, catalog_id='l_install', customer_visible=False),
            _item('Steep Charge', 10, 40, cost_class='labor'),
        ]}},
    }
    if signature:
        est['signature'] = signature
    return est


def _pdf_text(raw):
    from pypdf import PdfReader
    return '\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(raw)).pages)


# ── the fold itself ────────────────────────────────────────────────────────

def _entries(*specs):
    return [({'name': n, 'catalog_id': cid, **({'customer_visible': False} if hid else {})},
             1.0, line, '') for n, cid, line, hid in specs]


def test_labor_lands_on_the_covering_and_the_rows_still_add_up():
    rows, unfolded = A._fold_hidden_lines('roofing', _entries(
        ('Shingles', 'm_landmark', 3000.0, False),
        ('Drip Edge', 'a_drip_edge', 300.0, False),
        ('Install Labor', 'l_install', 4350.0, True)))
    assert unfolded == 0
    assert [(e[0]['name'], e[2]) for e in rows] == [('Shingles', 7350.0), ('Drip Edge', 300.0)]


def test_two_coverings_split_pro_rata_and_sum_exactly():
    rows, _ = A._fold_hidden_lines('siding', _entries(
        ('Lap', 's_lp_standard', 1000.0, False),
        ('Panel', 's_lp_panel', 3000.0, False),
        ('Install Labor', 'sl_install', 1000.01, True)))
    assert rows[0][2] == pytest.approx(1250.0025)
    assert sum(e[2] for e in rows) == pytest.approx(5000.01, abs=1e-9)


def test_no_covering_falls_to_the_largest_visible_line():
    rows, _ = A._fold_hidden_lines('gutters', _entries(
        ('Gutter', 'g_k5', 800.0, False),
        ('Downspout', 'g_ds', 200.0, False),
        ('Labor', 'gl_install', 300.0, True)))
    assert [e[2] for e in rows] == [1100.0, 200.0]


def test_nothing_visible_keeps_the_old_note():
    rows, unfolded = A._fold_hidden_lines('roofing', _entries(
        ('Install Labor', 'l_install', 10.0, True)))
    assert rows == [] and unfolded == 1


# ── the customer estimate: web page, signed PDF ───────────────────────────

def test_the_web_page_folds_labor_into_the_shingle_row_and_keeps_extras():
    est = _est()
    html, total = A.render_line_items(est)
    assert 'Install Labor' not in html and 'Steep Charge' in html
    shingles_with_labor = (100 + 145) / 0.65 * 30
    assert A.fc(shingles_with_labor) in html
    assert 'included in total' not in html, 'every hidden line found a home'
    _shown, shown_total = A.render_line_items(_est(labor=True))
    assert total == pytest.approx(shown_total), 'folding must never move a total'


def test_default_columns_are_line_total_on_unit_price_off():
    html, _t = A.render_line_items(_est())
    assert '>Total<' in html and 'Unit Price' not in html
    html, _t = A.render_line_items(_est(unitPrices=True))
    assert 'Unit Price' in html
    html, _t = A.render_line_items(_est(lineTotals=False))
    assert '>Total<' not in html.split('<tfoot>')[0]


def test_the_signed_pdf_defaults_and_toggles():
    txt = _pdf_text(A.build_signed_pdf(_est(), signed=False))
    assert 'Unit Price' not in txt and 'Install Labor' not in txt and 'Steep Charge' in txt
    assert A.fc((100 + 145) / 0.65 * 30) in txt
    assert 'Unit Price' in _pdf_text(A.build_signed_pdf(_est(unitPrices=True), signed=False))


def test_a_contract_signed_before_the_chips_keeps_its_shape():
    """No new keys + a signature = the old rule: linePrices showed both columns
    or neither, and hidden lines dropped out rather than folding."""
    sig = {'signed_at': '2026-09-01T00:00:00Z', 'name': 'Pat'}
    assert A._line_price_view(_est(signature=sig)) == {'total': False, 'unit': False, 'fold': False}
    assert A._line_price_view(_est(signature=sig, linePrices=True)) == \
        {'total': True, 'unit': True, 'fold': False}
    html, _t = A.render_line_items(_est(signature=sig, linePrices=True))
    assert A.fc(100 / 0.65 * 30) in html, 'a signed shingle price must not move'
    # Once the rep sets either chip, the new rule applies.
    assert A._line_price_view(_est(signature=sig, unitPrices=False))['fold'] is True


# ── the invoice / basic estimate ──────────────────────────────────────────

def test_the_basic_estimate_rows_add_up_without_a_labor_row():
    est = _est()
    est['invoice'] = {'kind': 'quote'}
    data = A.invoice_rows(est)
    roof = next(s for s in data['sections'] if s['key'] == 'roofing')
    names = [r[0] for r in roof['rows']]
    assert 'Install Labor' not in names and any('Steep Charge' in n for n in names)
    assert sum(r[4] for r in roof['rows']) == pytest.approx(roof['subtotal'])
    assert roof['folded'] == 0


def test_unit_prices_default_off_and_only_a_real_boolean_turns_them_on():
    assert A.invoice_fields({})['unit_prices'] is False
    assert A._sanitize_invoice({'unit_prices': 'yes'}) == {}
    assert A._sanitize_invoice({'unit_prices': True}) == {'unit_prices': True}


def test_a_signed_invoice_without_the_flag_keeps_its_shape():
    inv = A.invoice_fields({'invoice': {'signature': {'name': 'Dana'}}})
    assert inv['unit_prices'] is True and inv['fold'] is False


# ── the price book ─────────────────────────────────────────────────────────

def test_the_seeded_roofing_labor_is_hidden_from_customers():
    """It was not — siding and windows carried customer_visible:false, roofing
    did not, so Install Labor printed on every roofing proposal."""
    seeds = {p['id']: p for p in A.ROOFING_CATALOG_SEED}
    for pid in ('l_install', 'l_tearoff'):
        assert seeds[pid]['customer_visible'] is False


# ── the browser mirrors the server ─────────────────────────────────────────

_FOLD_FIXTURES = [
    ('roofing', [('Shingles', 'm_landmark', 3000.0, False), ('Drip', 'a_drip_edge', 300.0, False),
                 ('Install Labor', 'l_install', 4350.0, True)]),
    ('siding', [('Lap', 's_lp_standard', 1000.0, False), ('Panel', 's_lp_panel', 3000.0, False),
                ('Trim', 'sa_trim', 50.0, False), ('Install', 'sl_install', 1000.01, True)]),
    ('windows', [('Aeris', 'w_provia_aeris', 900.0, False), ('Simonton', 'w_simonton', 1800.0, False),
                 ('Install', 'wl_install', 700.0, True), ('Removal', 'wl_removal', 70.0, True)]),
    ('commercial', [('TPO', 'cm_tpo_ma', 0.0, False), ('ISO', 'ca_iso', 0.0, False),
                    ('Labor', 'cl_labor_reroof', 400.0, True)]),
    ('gutters', [('Gutter', 'g_k5', 800.0, False), ('DS', 'g_ds', 200.0, False),
                 ('Labor', 'gl_install', 300.0, True)]),
    ('roofing', [('Install Labor', 'l_install', 10.0, True)]),
    ('roofing', [('Shingles', 'm_landmark', 3000.0, False)]),
]
_VIEW_FIXTURES = [
    ({}, False), ({'unitPrices': True}, False), ({'lineTotals': False}, False),
    ({}, True), ({'linePrices': True}, True), ({'linePrices': True, 'unitPrices': False}, True),
]


def test_app_js_folds_and_resolves_columns_exactly_like_app_py(tmp_path):
    if shutil.which('node') is None:
        pytest.skip('node not installed')
    fx = {'folds': [{'trade': t, 'entries': [
              {'item': {'name': n, 'catalog_id': c, **({'customer_visible': False} if h else {})},
               'line': line} for n, c, line, h in specs]} for t, specs in _FOLD_FIXTURES],
          'views': [{'pv': pv, 'signed': sg} for pv, sg in _VIEW_FIXTURES]}
    fx_path, out_path = tmp_path / 'fx.json', tmp_path / 'out.json'
    fx_path.write_text(json.dumps(fx))
    proc = subprocess.run(['node', RUNNER, str(fx_path), str(out_path)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f'labor_fold_runner.js failed:\n{proc.stderr}'
    got = json.loads(out_path.read_text())
    for (trade, specs), js in zip(_FOLD_FIXTURES, got['folds']):
        rows, unfolded = A._fold_hidden_lines(trade, _entries(*specs))
        assert js['unfolded'] == unfolded, trade
        assert [n for n, _l in js['rows']] == [e[0]['name'] for e in rows], trade
        for (_n, jl), e in zip(js['rows'], rows):
            assert jl == pytest.approx(e[2], abs=1e-9), trade
    for (pv, sg), js in zip(_VIEW_FIXTURES, got['views']):
        est = {'page_visibility': pv, **({'signature': {'n': 1}} if sg else {})}
        assert js == A._line_price_view(est), (pv, sg)
