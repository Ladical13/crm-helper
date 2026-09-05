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


# ── Workstream C: the shapes a real report prints that the parser missed ──
# Three defects found by feeding the parser report shapes it hadn't been tried
# against. All three fail the same way — silently, with a plausible number or
# no number at all, on an estimate that otherwise looks finished.

def test_roofr_reads_linear_feet_past_a_thousand(A):
    """Roofr commas any four-digit figure, so a big or multi-structure property
    prints "1,204ft 3in". The finder demanded `\\d+ft\\s+\\d+in`, which matches
    nothing in that string, so the key was DROPPED — and because apply does an
    Object.assign of only the keys present, the estimate kept its zeros. Eaves,
    valleys, rakes and gutters all priced at 0 LF on exactly the largest jobs,
    with no error anywhere."""
    meas = A._parse_roofr_pdf(_roofr_pdf([
        'Report summary',
        'Total roof area 12,480 sqft',
        'Total eaves 1,204ft 3in',
        'Total valleys 1,032ft 0in',
        'Hips + ridges 1,110ft 6in',
    ]))['measurements']
    assert meas['eave_lf'] == 1204.25
    assert meas['valley_lf'] == 1032.0
    assert meas['ridge_hip_lf'] == 1110.5
    # Gutters are ordered off the eave run, so they inherit the same fix.
    assert meas['gutter_lf'] == 1204.25


def test_roofr_reads_a_bare_feet_value(A):
    """Inches are optional. _parse_roofr_lf always carried a bare-feet branch,
    but the finder's regex could never produce a string that reached it, so a
    "Total eaves 210ft" line parsed as nothing at all."""
    meas = A._parse_roofr_pdf(_roofr_pdf([
        'Report summary', 'Total roof area 2500 sqft',
        'Total eaves 210ft', 'Total valleys 60ft',
    ]))['measurements']
    assert meas['eave_lf'] == 210.0
    assert meas['valley_lf'] == 60.0


def test_roofr_pitches_come_from_the_summary_not_the_last_structure(A):
    """Every other measurement is read from the first match at or after the
    "Report summary" heading; the pitch table was independently read from the
    LAST table in that same text. Those two rules cannot both be right, and on
    a report whose summary leads the structure detail they disagree — the
    detached garage decided low-slope, steep and predominant pitch for the whole
    house. Here the property is 4,000 sqft (3,000 at 4/12 + 1,000 at 8/12) and
    the garage is 600 sqft of 2/12: taking the last table billed 6 SQ of rolled
    roofing that isn't flat and dropped the 10 SQ steep charge entirely."""
    meas = A._parse_roofr_pdf(_roofr_pdf([
        'Report summary',
        'Total roof area 4,000 sqft',
        'Total eaves 400ft 0in',
        'Pitch 4/12 8/12',
        'Area (sqft) 3000 1000',
        'Structure 2 - Detached garage',
        'Total roof area 600 sqft',
        'Pitch 2/12',
        'Area (sqft) 600',
    ]))['measurements']
    assert meas['roof_squares'] == 40.0        # whole property, as before
    assert meas['steep_squares'] == 10.0       # the 8/12 area, not the garage's 0
    assert meas['low_slope_squares'] == 0.0    # the garage's 2/12 is not the house
    assert meas['predominant_pitch'] == 4      # largest area on the summary table


def test_roofr_pitches_still_take_the_last_table_without_a_summary(A):
    """With no "Report summary" to scope to there is no first-block rule to
    apply, so the older last-table behaviour has to stay exactly as it was."""
    meas = A._parse_roofr_pdf(_roofr_pdf([
        'Total roof area 4,000 sqft',
        'Pitch 2/12',
        'Area (sqft) 600',
        'Pitch 4/12 8/12',
        'Area (sqft) 3000 1000',
    ]))['measurements']
    assert meas['steep_squares'] == 10.0
    assert meas['low_slope_squares'] == 0.0


# ── Workstream D: the import refuses rather than applying gaps as zeros ──
# Two failures a rep actually hit. A genuine RoofR PDF was rejected as "not a
# PDF" because the check read the filename; and a parse that came back missing
# measurements applied anyway, because apply Object.assigns the keys present and
# leaves the rest at zero — which then prices as zero.

import io as _io


def test_a_pdf_without_a_pdf_filename_is_still_a_pdf(A, client):
    """The endpoint used to require a filename ending in '.pdf'. Any source that
    drops the extension — iOS Files, a share sheet, a messaging app handing over
    'document' — got told its RoofR report was not a PDF. The bytes decide."""
    pdf = _roofr_pdf(['Report summary', 'Total roof area 3000 sqft',
                      'Total eaves 200ft 0in'])
    r = client.post('/api/parse-roofr',
                    data={'file': (_io.BytesIO(pdf), 'document')},
                    content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['measurements']['roof_squares'] == 30.0


def test_a_file_that_is_not_a_pdf_is_refused(A, client):
    r = client.post('/api/parse-roofr',
                    data={'file': (_io.BytesIO(b'this is not a pdf'), 'report.pdf')},
                    content_type='multipart/form-data')
    assert r.status_code == 400
    assert 'not a PDF' in r.get_json()['error']


def test_import_refuses_when_a_core_measurement_did_not_parse(A, client):
    """A roof always has eaves. Coming back without them means the PARSE failed,
    and applying that leaves the estimate's eaves — and its gutters, drip edge
    and starter, all ordered off the eave run — at zero, priced and printed as
    if measured. Refuse, and say which figure is missing."""
    pdf = _roofr_pdf(['Report summary', 'Total roof area 3000 sqft',
                      'Total valleys 60ft 0in'])
    r = client.post('/api/parse-roofr',
                    data={'file': (_io.BytesIO(pdf), 'report.pdf')},
                    content_type='multipart/form-data')
    assert r.status_code == 422
    err = r.get_json()['error']
    assert 'eaves' in err
    assert 'Nothing was applied' in err


def test_import_names_the_measurements_the_report_did_not_carry(A, client):
    """Non-core gaps don't block — a simple gable really has no valleys — but
    they are named, because the alternative is a quiet '—' on a preview a rep
    is scanning for numbers, not for absences."""
    pdf = _roofr_pdf(['Report summary', 'Total roof area 3000 sqft',
                      'Total eaves 200ft 0in', 'Total rakes 90ft 0in'])
    r = client.post('/api/parse-roofr',
                    data={'file': (_io.BytesIO(pdf), 'report.pdf')},
                    content_type='multipart/form-data')
    assert r.status_code == 200
    unread = r.get_json()['unread']
    assert 'valleys' in unread and 'step flashing' in unread
    assert 'rakes' not in unread            # present in the report
    assert 'roof area' not in unread        # required keys never land here


def test_a_measured_zero_is_an_answer_not_a_gap(A, client):
    """A report that explicitly states 0ft 0in of valleys has ANSWERED the
    question. Flagging that as unread trains reps to ignore the banner."""
    pdf = _roofr_pdf(['Report summary', 'Total roof area 3000 sqft',
                      'Total eaves 200ft 0in', 'Total valleys 0ft 0in'])
    r = client.post('/api/parse-roofr',
                    data={'file': (_io.BytesIO(pdf), 'report.pdf')},
                    content_type='multipart/form-data')
    assert r.status_code == 200
    assert r.get_json()['measurements']['valley_lf'] == 0.0
    assert 'valleys' not in r.get_json()['unread']


def test_and_list_reads_like_a_sentence(A):
    assert A._and_list([]) == ''
    assert A._and_list(['eaves']) == 'eaves'
    assert A._and_list(['roof area', 'eaves']) == 'roof area and eaves'
    assert A._and_list(['roof area', 'eaves', 'valleys']) == 'roof area, eaves and valleys'
