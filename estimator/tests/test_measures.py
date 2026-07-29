"""Siding auto-quantity measures — the soffit width menu and the trim split.

MEASURE_DEFS lives only in app.js: the browser computes an auto-quantity and
stores the resulting number on the line item, so the server never recomputes it
and the pricing parity suite never sees it. That leaves these formulas
unguarded, which is why the two changes here get their own tests:

* **Soffit width** turns an eave run into coverage. It is the classic
  `parseFloat(v) || 0` trap — a missing width must fall back to the identity
  multiplier (12"), never to 0. Falling back to 0 would zero the soffit line
  while the Scope page still showed a soffit run filled in.
* **Trim vs J-Channel** used to be one measurement feeding both. EDCO bundles
  run J-channel, LP and Hardie run 5/4 trim board, and pointing both at one
  number silently billed one of them off the other's footage.

The last test walks the SHIPPED price book rather than the seed constants —
test_api.py::test_seeded_bundle_measures_are_known_keys only covers
BUNDLE_SEEDS, so a typo in price_book.json's measure key had nothing catching
it. A bad key means the item never auto-fills and quietly prices at zero.
"""
import json
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, 'measure_runner.js')
APP_JS = os.path.join(os.path.dirname(HERE), 'static', 'app.js')
PRICE_BOOK = os.path.join(os.path.dirname(HERE), 'price_book.json')


# ── soffit width + the trim split (real app.js formulas under node) ────────

# (name, measure key, measurements, expected)
FIXTURES = [
    # 100 LF of run under a 24" overhang is 200 SF of soffit, not 100.
    ('soffit 24in doubles the run', 'siding_soffit_sf',
     {'siding_soffit_lf': 100, 'siding_soffit_width': 24}, 200.0),
    ('soffit 12in is the run', 'siding_soffit_sf',
     {'siding_soffit_lf': 100, 'siding_soffit_width': 12}, 100.0),
    ('soffit 16in', 'siding_soffit_sf',
     {'siding_soffit_lf': 90, 'siding_soffit_width': 16}, 120.0),
    ('soffit 36in triples', 'siding_soffit_sf',
     {'siding_soffit_lf': 50, 'siding_soffit_width': 36}, 150.0),
    ('soffit 48in quadruples', 'siding_soffit_sf',
     {'siding_soffit_lf': 25, 'siding_soffit_width': 48}, 100.0),

    # The trap. Both of these must degrade to the pre-menu behaviour (identity),
    # so an estimate written before the width menu existed prices unchanged.
    ('missing width falls back to 12in, not 0', 'siding_soffit_sf',
     {'siding_soffit_lf': 100}, 100.0),
    ('explicit 0 width falls back to 12in, not 0', 'siding_soffit_sf',
     {'siding_soffit_lf': 100, 'siding_soffit_width': 0}, 100.0),
    ('junk width falls back to 12in, not 0', 'siding_soffit_sf',
     {'siding_soffit_lf': 100, 'siding_soffit_width': ''}, 100.0),

    # No run means no soffit regardless of width — width alone must not invent one.
    ('no run means no soffit', 'siding_soffit_sf',
     {'siding_soffit_width': 24}, 0.0),

    # The SQ flavour is the SF one over 100.
    ('soffit SQ is SF over 100', 'siding_soffit_sq',
     {'siding_soffit_lf': 100, 'siding_soffit_width': 24}, 2.0),

    # Run stays available untouched for anything priced by the linear foot.
    ('soffit run is still raw LF', 'siding_soffit',
     {'siding_soffit_lf': 100, 'siding_soffit_width': 24}, 100.0),

    # Trim and J-channel read different numbers now.
    ('j-channel reads its own footage', 'j_channel',
     {'siding_j_channel_lf': 40, 'siding_trim_lf': 175}, 40.0),
    ('trim reads its own footage', 'siding_trim',
     {'siding_j_channel_lf': 40, 'siding_trim_lf': 175}, 175.0),
    ('trim with no j-channel measured', 'siding_trim',
     {'siding_trim_lf': 175}, 175.0),
    ('j-channel with no trim measured', 'j_channel',
     {'siding_j_channel_lf': 40}, 40.0),
]


@pytest.fixture(scope='module')
def js_results(tmp_path_factory):
    """Run every fixture through the real app.js MEASURE_DEFS under node."""
    if shutil.which('node') is None:
        pytest.skip('node not installed — the measure formulas cannot be run')
    d = tmp_path_factory.mktemp('measures')
    fx, out = d / 'fixtures.json', d / 'js.json'
    fx.write_text(json.dumps([{'name': n, 'key': k, 'm': m}
                              for n, k, m, _e in FIXTURES]), encoding='utf-8')
    proc = subprocess.run(['node', RUNNER, str(fx), str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f'measure_runner.js failed:\n{proc.stderr}'
    return {r['name']: r['value'] for r in json.loads(out.read_text(encoding='utf-8'))}


@pytest.mark.parametrize('name,key,m,expected', FIXTURES, ids=[f[0] for f in FIXTURES])
def test_measure_formula(js_results, name, key, m, expected):
    assert js_results[name] == pytest.approx(expected), name


# ── the shipped price book points at measures that actually exist ──────────

def _known_measure_keys():
    """The MEASURE_DEFS keys defined in app.js."""
    import re
    js = open(APP_JS, encoding='utf-8').read()
    block = js[js.index('const MEASURE_DEFS'):]
    block = block[:block.index('\n};')]
    return set(re.findall(r'^\s{2}(\w+):', block, re.M))


def test_shipped_price_book_measures_are_known_keys():
    """A measure key that MEASURE_DEFS doesn't define never auto-fills, so the
    line sits at qty 0 and prices at nothing while looking perfectly normal."""
    known = _known_measure_keys()
    with open(PRICE_BOOK, encoding='utf-8') as f:
        pb = json.load(f)
    checked = 0
    for key, value in pb.items():
        if not key.endswith('_catalog') or not isinstance(value, list):
            continue
        for p in value:
            measure = p.get('measure')
            if not measure:      # '' is the explicit Manual-qty contract
                continue
            checked += 1
            assert measure in known, (
                f"unknown measure {measure!r} on {key} product {p.get('name')!r}")
    assert checked, 'no catalog products checked — did the price book shape change?'


def test_siding_trim_and_soffit_products_read_the_new_measures():
    """The reason the fields were split: LP and Hardie bundles carry 5/4 trim
    board and must read trim footage, while EDCO's J-channel reads J-channel."""
    with open(PRICE_BOOK, encoding='utf-8') as f:
        cat = {p['id']: p for p in json.load(f)['siding_catalog']}

    for pid in ('sa_hardie_statement_trim', 'sa_hardie_primed_trim',
                'sa_lp_expert_trim', 'sa_lp_standard_trim'):
        assert cat[pid]['measure'] == 'siding_trim', pid
    assert cat['sa_edco_jchannel']['measure'] == 'j_channel'

    # Soffit is measured and priced by the linear foot — that is the shape the
    # measurement report comes back in. The Soffit Width menu is a spec for the
    # order, NOT a quantity multiplier, so nothing here may drift to per-SF
    # without the costs being re-derived at the same time.
    for pid in ('sa_hardie_statement_soffit', 'sa_hardie_primed_soffit',
                'sa_lp_expert_soffit', 'sa_lp_standard_soffit', 'sa_edco_soffit'):
        assert cat[pid]['measure'] == 'siding_soffit', pid
        assert cat[pid]['unit'] == 'LF', pid


def test_app_js_and_app_py_agree_on_the_siding_fields(A):
    """MEASURE_FIELDS (app.js, the Scope page) and MEASURE_LABELS (app.py, the
    production packet) are hand-mirrored. A field added to one and not the other
    is either uncollectable or unprintable."""
    import re
    js = open(APP_JS, encoding='utf-8').read()
    block = js[js.index("{ group:'Siding'"):]
    block = block[:block.index(']},')]
    js_keys = re.findall(r"key:'(\w+)'", block)

    py_keys = [k for group, fields in A.MEASURE_LABELS if group == 'Siding'
               for k, _label, _unit in fields]
    assert js_keys == py_keys
