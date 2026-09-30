"""Labor is hidden on everything the customer sees, unless the rep says so.

Labor prices into the package but is not broken out for the homeowner:
"Install Labor — $9,400" invites a line-item negotiation over the one number
that is really the crew. The seeded labor products carry customer_visible:
false for exactly that reason, and the customer estimate's "Line Prices" chip
has always defaulted off. Two places ignored both:

  * the signed-contract PDF printed Unit Price and Total on every line whatever
    the Line Prices chip said — so the one document a customer KEEPS was the
    one that gave them the full breakdown;
  * the invoice listed every line with its price by default (see
    test_invoice.py for that half).

The rule now, on every customer document: a hidden line folds into its trade's
subtotal unless that document's Labor toggle is on. Totals never move — only
which rows print. And a labor product a manager forgot to hide is REPORTED by
the price book audit, never hidden by inference: cost_class may only ever move
the internal cost split (see test_cost_split.py).
"""
import io
import os
import re

import pytest

import app as A

APPJS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'static', 'app.js')
PRICING = {'mode': 'margin', 'rate': 35,
           'tier_rates': {'good': 35, 'better': 35, 'best': 35}}


def _item(name, qty, cost, **extra):
    return dict({'name': name, 'quantity': qty, 'unit': 'SQ',
                 'tiers': {t: {'material_unit_cost': cost, 'labor_unit_cost': 0,
                               'description': ''} for t in ('good', 'better', 'best')}},
                **extra)


def _est(**pv):
    return {
        'estimate_id': 'labor-vis', 'estimate_type': 'retail', 'pricing': PRICING,
        'selected_tier': 'better', 'page_visibility': pv,
        'customer': {'name': 'Pat Homeowner', 'address': {}},
        'trades': {'roofing': {'enabled': True, 'mode': 'gbb', 'line_items': [
            _item('Shingles', 30, 100),
            _item('Install Labor', 30, 145, customer_visible=False),
        ]}},
    }


def _pdf_text(raw):
    from pypdf import PdfReader
    return '\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(raw)).pages)


def _appjs():
    with open(APPJS, encoding='utf-8') as f:
        return f.read()


# ── the customer estimate: web page, signed PDF, browser print ─────────────

def test_the_web_page_folds_labor_unless_the_chip_is_on():
    html, total = A.render_line_items(_est())
    assert 'Install Labor' not in html and 'Shingles' in html
    shown, shown_total = A.render_line_items(_est(labor=True))
    assert 'Install Labor' in shown
    assert shown_total == pytest.approx(total), 'showing a row must never move a total'


def test_the_signed_pdf_obeys_the_unit_prices_chip():
    """It printed Unit Price and Total on every line whatever the chip said.
    The unit price is opt-in now; the line total is on unless turned off."""
    assert 'Unit Price' not in _pdf_text(A.build_signed_pdf(_est(), signed=False))
    assert 'Unit Price' in _pdf_text(A.build_signed_pdf(_est(unitPrices=True), signed=False))


def test_the_signed_pdf_obeys_the_labor_chip():
    assert 'Install Labor' not in _pdf_text(A.build_signed_pdf(_est(), signed=False))
    assert 'Install Labor' in _pdf_text(A.build_signed_pdf(_est(labor=True), signed=False))


def test_the_chip_is_default_off_on_both_sides():
    """Only a literal True shows labor — on the server and in the browser."""
    assert A._show_labor_lines({}) is False
    assert A._show_labor_lines({'page_visibility': {'labor': 'yes'}}) is False
    assert A._show_labor_lines({'page_visibility': {'labor': True}}) is True
    js = _appjs()
    m = re.search(r'const PAGE_DEFAULT_OFF = \[([^\]]*)\]', js)
    assert m and "'labor'" in m.group(1), \
        'a default-off chip missing from PAGE_DEFAULT_OFF needs two taps to turn on'
    assert "id:'labor'" in js, 'no Labor chip in the Print Pages bar'


def test_the_browser_print_folds_labor_unless_the_chip_is_on():
    js = _appjs()
    i = js.index('function printTradeBody(')
    body = js[i:js.index('\n}', i)]
    assert 'showLab ? entries' in body and 'foldHiddenLines(trade, entries)' in body
    assert 'printTradeBody(trade,t,{showTot,showUnit,fold,showLab,tradeMode})' in js
    assert 'const showLab = pv.labor === true;' in js


# ── the price book: report a visible labor product, never hide it ──────────

def _pb():
    return {
        'roofing_catalog': [
            {'id': 'l_vis', 'name': 'Install Labor', 'unit': 'SQ', 'cost': 145,
             'cost_class': 'labor', 'measure': 'squares'},
            {'id': 'l_hid', 'name': 'Tear-Off Labor', 'unit': 'SQ', 'cost': 60,
             'cost_class': 'labor', 'measure': 'squares', 'customer_visible': False},
            {'id': 'm_vis', 'name': 'Shingle', 'unit': 'SQ', 'cost': 142,
             'measure': 'squares'},
        ],
        'roofing_bundles': [{'id': 'b', 'name': 'B',
                             'product_ids': ['l_vis', 'l_hid', 'm_vis']}],
    }


def _codes(pid):
    for f in A.pricebook_audit(_pb())['findings']:
        if f['product_id'] == pid:
            return {i['code'] for i in f['issues']}
    return set()


def test_a_labor_product_shown_to_customers_is_reported():
    assert 'labor_visible' in _codes('l_vis')
    assert 'labor_visible' not in _codes('l_hid')
    assert _codes('m_vis') == set(), 'a material product is nobody\'s concern here'


def test_the_report_is_low_severity_because_no_money_is_wrong():
    """High means a price is wrong. This is a row a customer should not see —
    the fix list must not bury the costing faults under it."""
    for f in A.pricebook_audit(_pb())['findings']:
        for i in f['issues']:
            if i['code'] == 'labor_visible':
                assert i['severity'] == 'low'


def test_cost_class_alone_never_hides_a_line():
    """The audit reports; it does not gate. A labor-classed line the manager
    left visible still prints — a stored decision, not an inference."""
    est = _est()
    est['trades']['roofing']['line_items'][1]['customer_visible'] = True
    est['trades']['roofing']['line_items'][1]['cost_class'] = 'labor'
    html, _t = A.render_line_items(est)
    assert 'Install Labor' in html
