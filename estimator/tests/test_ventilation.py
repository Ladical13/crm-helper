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
