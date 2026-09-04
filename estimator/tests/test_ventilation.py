"""Ventilation update: ridges-only RoofR parse, the NFA mirror's cut-in figure,
and the work-order (production packet) cut-in section.

Ridge vent now ORDERS the full ridge (ridge_lf) while the crew CUTS IN only the
code-required footage (atticVentilation deficit). Here we guard the pieces that
live in app.py.

The JS<->PY agreement of atticVentilation itself is covered by
test_fastening.py::test_attic_ventilation_js_and_py_agree — NOT by the pricing
parity suite, which only extracts the rate/total functions. (This docstring used
to claim otherwise, and nothing checked the two copies of the 1/300 math.)
Stick-count rounding still lives in app.js measuredQty and is manual-QA only.
"""
import os

from conftest import TEST_DATA_DIR


def _roofr_pdf(lines):
    """Build a tiny PDF whose text lines mimic a RoofR Report Summary, so the
    real _parse_roofr_pdf path (pypdf text extraction) runs end to end."""
    import app as A
    pdf = A.FPDF()
    pdf.add_page()
    pdf.set_font('Helvetica', '', 12)
    for line in lines:
        pdf.cell(0, 8, line, new_x='LMARGIN', new_y='NEXT')
    return bytes(pdf.output())


# ── Workstream A: ridges-only parse ────────────────────────────────────

def test_roofr_parses_ridges_only(A):
    data = _roofr_pdf([
        'Report summary',
        'Total roof area 3200 sqft',
        'Total ridges 40ft 0in',
        'Total hips 20ft 0in',
        'Total eaves 100ft 0in',
    ])
    meas = A._parse_roofr_pdf(data)['measurements']
    # Ridges alone drive ridge-vent ordering...
    assert meas['ridge_lf'] == 40.0
    # ...while the combined ridge+hip figure (hips + ridges) is still available.
    assert meas['ridge_hip_lf'] == 60.0


def test_roofr_omits_ridge_lf_when_absent(A):
    # No "Total ridges" line -> key omitted so a re-import can't null a manual entry.
    data = _roofr_pdf(['Report summary', 'Total eaves 100ft 0in'])
    meas = A._parse_roofr_pdf(data)['measurements']
    assert 'ridge_lf' not in meas


# ── Workstream B: the report's RECOMMENDED waste ───────────────────────
# Roofr flags the recommended column of its waste ladder with a "Recommended"
# label centred over it. Plain text loses the column, so the parser matches the
# label's horizontal centre to a percentage's — verified against a real
# 3-structure export where all four tables matched to the decimal.
# These build PDFs with pymupdf rather than the FPDF helper above, because the
# geometry IS the thing under test.

_LADDER = ['0%', '10%', '12%', '13%', '15%', '17%', '20%']
_LADDER_Y = 300.0
_COL_X = {p: 100.0 + 60.0 * i for i, p in enumerate(_LADDER)}   # left edge


def _roofr_geo_pdf(recommended=None, label_dx=0.0, label_y=None, footnote=True):
    """A one-page waste ladder. `recommended` names the column the label is
    centred over; label_dx nudges it off-centre to test the tolerance."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), 'Report summary', fontsize=11)
    page.insert_text((72, 120), 'Total roof area 3200 sqft', fontsize=11)
    page.insert_text((72, _LADDER_Y), 'Waste %', fontsize=11)
    for pct in _LADDER:
        page.insert_text((_COL_X[pct], _LADDER_Y), pct, fontsize=11)
    if recommended:
        # Centre the label over its column: same centre, minus half the label's
        # own width. fitz reports y as the BASELINE, so a smaller y is higher.
        w = fitz.get_text_length('Recommended', fontsize=11)
        col_w = fitz.get_text_length(recommended, fontsize=11)
        cx = _COL_X[recommended] + col_w / 2 + label_dx
        y = _LADDER_Y - 20 if label_y is None else label_y
        page.insert_text((cx - w / 2, y), 'Recommended', fontsize=11)
    if footnote:
        page.insert_text(
            (72, _LADDER_Y + 60),
            'Recommended waste is based on an asphalt shingle roof with a closed valley system.',
            fontsize=8)
    out = doc.tobytes()
    doc.close()
    return out


def test_roofr_reads_recommended_waste(A):
    meas = A._parse_roofr_pdf(_roofr_geo_pdf(recommended='13%'))['measurements']
    assert meas['waste_pct'] == 13.0


def test_roofr_reads_recommended_waste_at_either_end(A):
    # Nothing may assume the recommendation sits mid-ladder.
    for pct in ('0%', '20%'):
        assert A._parse_roofr_waste(_roofr_geo_pdf(recommended=pct)) == float(pct.rstrip('%'))


def test_roofr_omits_waste_when_no_recommendation(A):
    """The regression this replaced: waste_pct was a hardcoded 10, and because a
    literal always survives the `is not None` filter, EVERY import reset the
    rep's waste — and the company default — to 10%. Absent means absent."""
    meas = A._parse_roofr_pdf(_roofr_geo_pdf(recommended=None))['measurements']
    assert 'waste_pct' not in meas


def test_roofr_waste_ignores_the_footnote_paragraph(A):
    """'Recommended waste is based on...' also starts with the marker word, but
    sits BELOW the ladder. Reading it would pick a column at random."""
    assert A._parse_roofr_waste(_roofr_geo_pdf(recommended=None, footnote=True)) is None


def test_roofr_waste_rejects_a_loose_match(A):
    """Off-centre beyond tolerance means the layout changed. No answer beats a
    confident wrong column — the waste is what the whole roof is ordered on."""
    assert A._parse_roofr_waste(_roofr_geo_pdf(recommended='13%', label_dx=30)) is None


def test_roofr_waste_prefers_the_report_summary_page(A):
    """A multi-structure property repeats the ladder per structure with DIFFERENT
    recommendations, then once more for the whole property. Picking the first
    would bid the garage's waste on the house."""
    import fitz
    whole = fitz.open('pdf', _roofr_geo_pdf(recommended='13%'))
    struct = fitz.open('pdf', _roofr_geo_pdf(recommended='20%'))
    # Structure page first, without the "Report summary" heading.
    for pg in struct:
        for inst in pg.search_for('Report summary'):
            pg.add_redact_annot(inst)
        pg.apply_redactions()
    struct.insert_pdf(whole)
    data = struct.tobytes()
    struct.close(); whole.close()
    assert A._parse_roofr_waste(data) == 13.0


# ── The imported ridge_lf has to survive a reload ──────────────────────

def test_the_ridge_migration_is_guarded_against_modern_estimates():
    """doLoadEstimate carries a legacy migration folding ridge_lf + hip_lf into
    ridge_hip_lf. ridge_lf was LATER reintroduced as a first-class ridges-only
    field, so unguarded that migration ran on every load of every modern
    estimate: it deleted the imported ridge_lf and overwrote ridge_hip_lf with
    the ridges-only number. Import 40 LF ridges + 20 LF hips, save, reopen, and
    the Ridge Vent line silently dropped to 0 sticks while ridge+hip under-billed
    by the hips.

    A present ridge_hip_lf is the exact tell for an already-migrated estimate.
    Asserted against source because doLoadEstimate is far too large for the
    *_runner.js extraction the other JS-mirror tests use.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, '..', 'static', 'app.js'), encoding='utf-8') as f:
        js = f.read()
    i = js.index('Migrate old separate ridge_lf / hip_lf')
    block = js[i:i + 2000]
    guard = block.index('S.measurements.ridge_hip_lf === undefined')
    combine = block.index('S.measurements.ridge_hip_lf = (parseFloat')
    delete = block.index('delete S.measurements.ridge_lf')
    assert guard < combine < delete, \
        'the ridge_hip_lf guard must gate the migration, not follow it'


# ── NFA mirror: the cut-in figure ──────────────────────────────────────

def test_attic_ventilation_cutin_deficit(A):
    # attic 3000 sf -> required total 1440 sq in, exhaust 720; no turtle vents
    # -> deficit 720 sq in -> cut-in 720/18 = 40 LF of ridge vent.
    v = A.attic_ventilation({'roof_squares': 30, 'turtle_vents': 0})
    assert v['needs_ridge'] is True
    assert round(v['ridge_lf_required'], 2) == 40.0


def test_attic_ventilation_meets_code_with_turtles(A):
    # Enough turtle vents to cover exhaust -> nothing to cut in.
    v = A.attic_ventilation({'roof_squares': 6, 'turtle_vents': 10})
    assert v['needs_ridge'] is False
    assert v['ridge_lf_required'] == 0


# ── Workstream E: production packet cut-in section ─────────────────────

def _signed_est(**over):
    est = {
        'estimate_id': 'vent-test',
        'estimate_type': 'retail',
        'selected_tier': 'better',
        'customer': {'name': 'Test Owner', 'phone': '555-0000',
                     'address': {'street': '1 A St', 'city': 'Tyler',
                                 'state': 'TX', 'zip': '75701'}},
        'signature': {'signed_at': '2026-07-21T00:00:00Z', 'selected_tier': 'better'},
        'measurements': {'roof_squares': 32, 'turtle_vents': 1,
                         'ridge_lf': 40, 'eave_lf': 100},
        'trades': {'roofing': {
            'enabled': True, 'mode': 'gbb', 'selected_tier': 'better',
            'line_items': [{
                'name': 'Ridge Vent', 'unit': 'LF', 'quantity': 10,
                'vent_role': 'ridge',
                'tiers': {'better': {'included': True, 'material_unit_cost': 34}},
            }],
        }},
    }
    est.update(over)
    return est


def test_packet_builds_with_cutin_block(A):
    est = _signed_est(vent_cutin={
        'cutin_lf': 22, 'notes': 'cut both main ridges', 'strokes': [],
        'image_filename': None,
    })
    out = A.build_production_packet_pdf(est)
    assert isinstance(out, bytes) and len(out) > 500


def test_packet_builds_without_cutin(A):
    # No ridge vent, no vent_cutin -> section is skipped, packet still builds.
    est = _signed_est()
    est['trades']['roofing']['line_items'][0].pop('vent_role')
    out = A.build_production_packet_pdf(est)
    assert isinstance(out, bytes) and len(out) > 500


def test_packet_survives_missing_cutin_image(A):
    # image_filename points at a file that isn't on disk -> guarded, no raise.
    est = _signed_est(vent_cutin={'image_filename': 'vent-test/nope.jpg', 'notes': ''})
    out = A.build_production_packet_pdf(est)
    assert isinstance(out, bytes) and len(out) > 500
