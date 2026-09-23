"""Intake vent: sized by the 1/300 code rule, capped at the eaves.

The "Install Intake Vent" checkbox and the Intake Vent product both used
`measure: 'eave'`, so intake priced the WHOLE eave run. A 3,000 SF attic needs
720 sq in of intake - 80 LF at 9 sq in per LF - and a 250 LF eave billed 250.
Landmark and IKO Nordic carry Intake Vent in the bundle itself, so that was
every job on those two packages, checkbox or not.

The code figure already existed (atticVentilation) and nothing priced off it.
"""
import json
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_JS = os.path.join(os.path.dirname(HERE), 'static', 'app.js')

# Lifts mnum, the NFA constants, atticVentilation and MEASURE_DEFS out of the
# real app.js - the same approach as measure_runner.js, which cannot run a
# measure that calls atticVentilation.
_RUNNER = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
function grab(h, end) {
  const i = src.indexOf(h);
  if (i < 0) throw new Error('not found in app.js: ' + h);
  const j = src.indexOf(end, i);
  return src.slice(i, j + end.length);
}
const consts = ['NFA_TURTLE_SQIN', 'NFA_RIDGE_SQIN_LF', 'NFA_INTAKE_SQIN_LF', 'VENT_RULE_DIVISOR']
  .map(n => grab('const ' + n + ' ', ';')).join('\n');
const code = grab('function mnum(v, dflt) {', '\n}') + '\n' + consts + '\n'
  + grab('function atticVentilation(m) {', '\n}') + '\n'
  + 'function commercialFastening() { throw new Error("n/a"); }\nconst _fastenTable = null;\n'
  + grab('const MEASURE_DEFS = {', '\n};') + '\nreturn { MEASURE_DEFS, atticVentilation };';
const { MEASURE_DEFS, atticVentilation } = new Function(code)();
const fx = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
fs.writeFileSync(process.argv[4], JSON.stringify(fx.map(m => ({
  qty: MEASURE_DEFS.intake_vent_code.calc(m),
  vent: atticVentilation(m),
}))));
"""

# (measurements, expected intake LF)
CASES = [
    ({'roof_squares': 30, 'eave_lf': 250}, 80.0),       # 3000 SF: 720 sq in / 9
    ({'roof_squares': 30, 'eave_lf': 50}, 50.0),        # code wants 80, eave is 50
    ({'roof_squares': 30}, 80.0),                       # no eaves: the code figure
    ({'attic_sqft': 1800, 'turtle_vents': 10, 'eave_lf': 200}, 48.0),
]


@pytest.fixture(scope='module')
def js(tmp_path_factory):
    if shutil.which('node') is None:
        pytest.skip('node not installed - the measure formulas cannot be run')
    d = tmp_path_factory.mktemp('intake')
    runner, fx, out = d / 'runner.js', d / 'fx.json', d / 'out.json'
    runner.write_text(_RUNNER, encoding='utf-8')
    fx.write_text(json.dumps([m for m, _e in CASES]), encoding='utf-8')
    proc = subprocess.run(['node', str(runner), APP_JS, str(fx), str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(out.read_text(encoding='utf-8'))


@pytest.mark.parametrize('i', range(len(CASES)))
def test_intake_is_sized_by_code_and_capped_at_the_eaves(js, i):
    assert js[i]['qty'] == pytest.approx(CASES[i][1])


@pytest.mark.parametrize('i', range(len(CASES)))
def test_js_and_py_agree_on_the_intake_footage(A, js, i):
    assert A.attic_ventilation(CASES[i][0])['intake_lf_required'] == \
        pytest.approx(js[i]['vent']['intake_lf_required'])


def test_intake_is_required_even_when_turtles_cover_exhaust(A):
    """Turtle vents answer the exhaust side. The intake side still needs its
    half, which is why intake_lf_required is not gated on needs_ridge."""
    v = A.attic_ventilation({'roof_squares': 6, 'turtle_vents': 10})
    assert v['needs_ridge'] is False
    assert v['intake_lf_required'] == pytest.approx(16.0)   # 288 / 2 / 9


# ── every path onto a job uses the code figure ────────────────────────────

def test_the_checkbox_sizes_intake_by_code():
    js_src = open(APP_JS, encoding='utf-8').read()
    assert "intake: { name:'Intake Vent', measure:'intake_vent_code' }" in js_src


def test_the_seed_product_sizes_intake_by_code(A):
    catalog = {p['id']: p for p in A.BUNDLE_SEEDS['roofing'][0]}
    assert catalog['a_intake_vent']['measure'] == 'intake_vent_code'


def _live(**fields):
    import app as A
    p = {'id': 'a_intake_vent', 'name': 'Intake Vent', 'unit': 'LF', 'cost': 10.15}
    p.update(fields)
    out = A._ensure_bundle_catalogs({'roofing_catalog': [p], 'roofing_bundles': [],
                                     'roofing_tier_defaults': {}})
    return {x['id']: x for x in out['roofing_catalog']}['a_intake_vent']


def test_the_live_product_moves_off_the_eave_run():
    live = _live(measure='eave')
    assert live['measure'] == 'intake_vent_code'
    assert live['cost'] == 10.15


def test_a_manager_chosen_measure_is_left_alone():
    assert _live(measure='eave_rake')['measure'] == 'eave_rake'


# ── the work order prints what was priced ─────────────────────────────────

def _pdf_text(raw):
    try:
        from pypdf import PdfReader
    except ImportError:
        pytest.skip('pypdf not installed')
    import io
    return '\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(raw)).pages)


def _signed(item):
    return {'estimate_id': 'intake-test', 'estimate_type': 'retail',
            'selected_tier': 'better',
            'customer': {'name': 'Test Owner', 'phone': '555-0000',
                         'address': {'street': '1 A St', 'city': 'Loveland',
                                     'state': 'CO', 'zip': '80537'}},
            'signature': {'signed_at': '2026-09-15T00:00:00Z', 'selected_tier': 'better'},
            'measurements': {'roof_squares': 30, 'eave_lf': 250},
            'trades': {'roofing': {'enabled': True, 'mode': 'simple',
                                   'line_items': [item]}}}


def test_the_work_order_prints_the_priced_intake_not_the_eaves(A):
    text = _pdf_text(A.build_production_packet_pdf(_signed(
        {'name': 'Intake Vent', 'unit': 'LF', 'quantity': 80,
         'vent_role': 'intake', 'measure': 'intake_vent_code'})))
    assert '80 LF at the eaves' in text
    assert '250 LF at the eaves' not in text
    # kv() prints its labels in capitals; the value beside it is the check.
    assert 'INTAKE FOR CODE ~80 LF' in text.upper()


# ── the intake map, marked like the ridge cut-in ──────────────────────────

def _map_jpeg(A, name):
    from PIL import Image
    import io
    folder = os.path.join(A.UPLOADS_DIR, 'intake-test')
    os.makedirs(folder, exist_ok=True)
    out = io.BytesIO()
    Image.new('RGB', (40, 30), (37, 99, 235)).save(out, 'JPEG')
    with open(os.path.join(folder, name), 'wb') as f:
        f.write(out.getvalue())
    return f'intake-test/{name}'


def _intake_item():
    return {'name': 'Intake Vent', 'unit': 'LF', 'quantity': 80,
            'vent_role': 'intake', 'measure': 'intake_vent_code'}


def test_the_work_order_prints_the_marked_intake_map(A):
    est = _signed(_intake_item())
    est['vent_intake'] = {'image_filename': _map_jpeg(A, 'vent-intake.jpg'),
                          'strokes': [], 'intake_lf': 80}
    text = _pdf_text(A.build_production_packet_pdf(est)).upper()
    assert 'MARKED INTAKE MAP' in text
    assert 'NO ROOF DIAGRAM MARKED' not in text


def test_both_maps_print_when_both_are_marked(A):
    est = _signed(_intake_item())
    est['vent_cutin'] = {'image_filename': _map_jpeg(A, 'vent-cutin.jpg'), 'strokes': []}
    est['vent_intake'] = {'image_filename': _map_jpeg(A, 'vent-intake-2.jpg'), 'strokes': []}
    text = _pdf_text(A.build_production_packet_pdf(est)).upper()
    assert 'MARKED CUT-IN MAP' in text and 'MARKED INTAKE MAP' in text


def test_a_missing_intake_map_image_does_not_break_the_packet(A):
    est = _signed(_intake_item())
    est['vent_intake'] = {'image_filename': 'intake-test/not-there.jpg'}
    text = _pdf_text(A.build_production_packet_pdf(est)).upper()
    assert 'NO ROOF DIAGRAM MARKED' in text


def test_the_editor_marks_intake_on_its_own_map():
    js = open(APP_JS, encoding='utf-8').read()
    assert "key: 'vent_intake'" in js and "key: 'vent_cutin'" in js, (
        'the two maps must save to separate keys, or re-marking one erases the other')
    assert 'onclick="openVentCutinEditor(\'intake\')"' in js
    m = __import__('re').search(r'async function saveVentCutin\(\).*?\n\}', js, __import__('re').S)
    assert m and 'S[mode.key] =' in m.group(0)
    assert '_ventCutinLF()' not in m.group(0), 'saving must use the mode, not the ridge figure'


def test_bundle_intake_reaches_the_work_order(A):
    """No vent_role - it came in with the Landmark bundle - and it is still on
    the job. It used to print 'Intake vent: NOT on this job'."""
    text = _pdf_text(A.build_production_packet_pdf(_signed(
        {'name': 'Intake Vent', 'unit': 'LF', 'quantity': 80,
         'catalog_id': 'a_intake_vent', 'measure': 'intake_vent_code'})))
    assert '80 LF at the eaves' in text
