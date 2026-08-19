"""Seven buildings on one contract, each with its own numbers.

An apartment complex is not one roof. Before structures existed the estimate
carried ONE measurements dict, so every line item on it priced off the same
square count however many buildings the rep typed in — a 42 SQ building and a
38 SQ building both billed 42.

A structure is a building: a name, the trade its work sits on, and its own
measurements in the same flat key namespace. The name IS its section name, and
sections already print headers with their own subtotals on the PDF and on the
page the customer signs, so a building is priced and printed by machinery that
already existed. These tests cover what is new: which numbers a row reads, and
what happens when a building is copied, renamed or removed.

The browser side runs under node against the REAL functions in static/app.js.
"""
import json
import os
import shutil
import subprocess
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, 'structures_runner.js')

pytestmark = pytest.mark.skipif(shutil.which('node') is None,
                                reason='node not installed — the real app.js cannot be run')


def _item(name, section=None, item_id=None, measure='comm_sq', unit='SQ',
          cost=200.0, price=300.0):
    it = {'id': item_id or ('i_' + name.lower().replace(' ', '_')), 'name': name,
          'unit': unit, 'quantity': 0, 'measure': measure, 'customer_visible': True,
          'unit_cost': cost, 'unit_price': price}
    if section:
        it['section'] = section
    return it


def _bld(sid, name, squares, **more):
    m = {'comm_squares': squares}
    m.update(more)
    return {'id': sid, 'name': name, 'trade': 'commercial', 'measurements': m}


def _est(structures=None, items=None, measurements=None):
    structures = structures if structures is not None else []
    return {
        'estimate_type': 'commercial',
        'pricing': {'mode': 'margin', 'global_rate': 35, 'tier_rates': {},
                    'trade_rates': {}, 'per_trade_overrides': {}},
        'measurements': measurements if measurements is not None else {'comm_squares': 10},
        'structures': structures,
        'trades': {'commercial': {'enabled': True, 'mode': 'simple',
                                  'sections': [s['name'] for s in structures],
                                  'line_items': items or []}},
    }


def _run(est, ops, rename_to='', fasten_table=None):
    d = tempfile.mkdtemp(prefix='structures-')
    fx, out = os.path.join(d, 'in.json'), os.path.join(d, 'out.json')
    payload = {'estimate': est, 'ops': ops, 'renameTo': rename_to,
               'fastenTable': fasten_table}
    with open(fx, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh)
    proc = subprocess.run(['node', RUNNER, fx, out], capture_output=True, text=True)
    assert proc.returncode == 0, 'structures_runner.js failed: ' + proc.stderr
    with open(out, encoding='utf-8') as fh:
        return json.load(fh)


def _by_section(res):
    return {i['section']: i for i in res['items']}


# ── The bug this exists to prevent ──────────────────────────────────────────

def test_each_building_prices_off_its_own_roof_area():
    """The whole point. Two buildings, two square counts, two quantities — the
    same product line on each, reading different numbers."""
    est = _est(
        structures=[_bld('st1', 'Building 1', 42), _bld('st2', 'Building 2', 38)],
        items=[_item('TPO Membrane', 'Building 1', 'i1'),
               _item('TPO Membrane', 'Building 2', 'i2')],
    )
    res = _run(est, [{'op': 'applyMeasurements'}])
    rows = _by_section(res)
    assert rows['Building 1']['quantity'] == 42
    assert rows['Building 2']['quantity'] == 38


def test_an_untagged_row_reads_the_estimates_own_numbers():
    """Mobilization, a dumpster, a permit — work that belongs to the job rather
    than to a roof. It has no building, so it falls back to the estimate's own
    measurements, which is exactly what a single-roof estimate is made of."""
    est = _est(
        structures=[_bld('st1', 'Building 1', 42)],
        items=[_item('TPO Membrane', 'Building 1', 'i1'), _item('Mobilization', None, 'i2')],
        measurements={'comm_squares': 7},
    )
    res = _run(est, [{'op': 'applyMeasurements'}])
    rows = _by_section(res)
    assert rows['Building 1']['quantity'] == 42
    assert rows['']['quantity'] == 7


def test_an_estimate_with_no_buildings_is_untouched():
    """Every estimate written before this, and every ordinary one-roof job after
    it. S.measurements stays what it was and stays the fallback — if this ever
    fails, the feature has reached estimates that never asked for it."""
    est = _est(structures=[], items=[_item('TPO Membrane')],
               measurements={'comm_squares': 26})
    res = _run(est, [{'op': 'applyMeasurements'}])
    assert res['items'][0]['quantity'] == 26
    assert res['estimate']['structures'] == []


# ── Making buildings 2..7 ───────────────────────────────────────────────────

def test_duplicating_a_building_copies_its_whole_build_up():
    """The way a complex actually gets built: copy the roof, change the number.
    The copy carries every line item, and the ORIGINAL must not move."""
    est = _est(
        structures=[_bld('st1', 'Building 1', 42)],
        items=[_item('TPO Membrane', 'Building 1', 'i1'),
               _item('Iso Insulation', 'Building 1', 'i2')],
    )
    res = _run(est, [{'op': 'duplicate', 'id': 'st1'}])
    assert [s['name'] for s in res['estimate']['structures']] == ['Building 1', 'Building 2']
    b1 = [r for r in res['items'] if r['section'] == 'Building 1']
    b2 = [r for r in res['items'] if r['section'] == 'Building 2']
    assert sorted(r['name'] for r in b2) == ['Iso Insulation', 'TPO Membrane']
    assert len(b1) == 2, 'the original building kept its rows'
    assert {r['id'] for r in b1}.isdisjoint({r['id'] for r in b2}), (
        'cloned rows share ids with the original — editing Building 2 would '
        'silently edit Building 1'
    )
    assert res['estimate']['trades']['commercial']['sections'] == ['Building 1', 'Building 2']


def test_a_copy_starts_on_its_originals_measurements():
    """Copy first, retype the squares second — so the copy has to start with the
    original's numbers rather than an empty card."""
    est = _est(structures=[_bld('st1', 'Building 1', 42)],
               items=[_item('TPO Membrane', 'Building 1', 'i1')])
    res = _run(est, [{'op': 'duplicate', 'id': 'st1'}])
    sts = res['estimate']['structures']
    assert sts[1]['measurements']['comm_squares'] == 42


def test_the_first_building_adopts_the_roof_already_typed_in():
    """A rep measures a roof, then realises it is a complex. Clicking Add
    Building must not leave that roof orphaned beside an empty Building 1: the
    work already on the estimate becomes Building 1 and keeps its numbers."""
    est = _est(structures=[], items=[_item('TPO Membrane', None, 'i1')],
               measurements={'comm_squares': 42})
    res = _run(est, [{'op': 'addStructure', 'trade': 'commercial'},
                     {'op': 'applyMeasurements'}])
    assert [s['name'] for s in res['estimate']['structures']] == ['Building 1', 'Building 2']
    rows = _by_section(res)
    assert rows['Building 1']['quantity'] == 42, 'the measured roof became Building 1'
    assert res['estimate']['structures'][0]['measurements']['comm_squares'] == 42


# ── Renaming and removing ───────────────────────────────────────────────────

def test_renaming_moves_the_building_its_section_and_its_rows_together():
    """The name is the join between a building and its measurements. Move one
    without the other two and the roof's numbers are orphaned — its rows fall
    back to the estimate's."""
    est = _est(structures=[_bld('st1', 'Building 1', 42)],
               items=[_item('TPO Membrane', 'Building 1', 'i1')],
               measurements={'comm_squares': 1})
    res = _run(est, [{'op': 'rename', 'id': 'st1', 'name': 'Clubhouse'},
                     {'op': 'applyMeasurements'}])
    assert res['estimate']['structures'][0]['name'] == 'Clubhouse'
    assert res['estimate']['trades']['commercial']['sections'] == ['Clubhouse']
    row = res['items'][0]
    assert row['section'] == 'Clubhouse'
    assert row['quantity'] == 42, 'the renamed building still owns its measurements'


def test_removing_a_building_takes_its_work_with_it():
    """A section is a label and its items survive it. A building is priced work:
    leaving its rows behind would keep charging for a roof that is off the job."""
    est = _est(
        structures=[_bld('st1', 'Building 1', 42), _bld('st2', 'Building 2', 38)],
        items=[_item('TPO Membrane', 'Building 1', 'i1'),
               _item('TPO Membrane', 'Building 2', 'i2')],
    )
    res = _run(est, [{'op': 'remove', 'id': 'st2'}])
    assert [s['name'] for s in res['estimate']['structures']] == ['Building 1']
    assert [r['section'] for r in res['items']] == ['Building 1']
    assert res['estimate']['trades']['commercial']['sections'] == ['Building 1']


# ── Money and fasteners, per building ───────────────────────────────────────

def test_each_building_carries_its_own_subtotal():
    """What the customer reads per building, and what the card on the Scope page
    shows. 42 SQ at $300 and 38 SQ at $300 are not the same money."""
    est = _est(
        structures=[_bld('st1', 'Building 1', 42), _bld('st2', 'Building 2', 38)],
        items=[_item('TPO Membrane', 'Building 1', 'i1'),
               _item('TPO Membrane', 'Building 2', 'i2')],
    )
    res = _run(est, [{'op': 'applyMeasurements'}])
    totals = {t['name']: t['total'] for t in res['totals']}
    assert totals['Building 1'] == pytest.approx(42 * 300)
    assert totals['Building 2'] == pytest.approx(38 * 300)


def _fasten_table():
    """The shipped table, not a hand-rolled one — a fixture with the wrong shape
    would return zeros and this test would pass by agreeing with nothing."""
    with open(os.path.join(HERE, '..', 'commercial_fastening.json'), encoding='utf-8') as fh:
        return json.load(fh)


def test_fastener_counts_are_calculated_per_building():
    """Zone geometry is a fact about ONE building — its length, width and
    height. A crew lays out corner spacing from these, so the big building and
    the small one cannot share an answer."""
    est = _est(structures=[
        _bld('st1', 'Building 1', 42, comm_length_ft=200, comm_width_ft=100,
             comm_height_ft=20, comm_uplift=60, comm_insul_layers=1),
        _bld('st2', 'Building 2', 12, comm_length_ft=60, comm_width_ft=40,
             comm_height_ft=14, comm_uplift=60, comm_insul_layers=1),
    ])
    res = _run(est, [], fasten_table=_fasten_table())
    got = {f['name']: f for f in res['fastening']}
    assert got['Building 1']['ok'] and got['Building 2']['ok']
    assert got['Building 1']['insul'] > got['Building 2']['insul'], (
        'both buildings got the same fastener count — the schedule is not '
        'reading per-building dimensions'
    )


# ── The server side: the crew packet ────────────────────────────────────────
#
# Pricing needs none of this - quantities are resolved in the browser and stored
# on the items, so the customer page and every total already read them. The
# server recalculates from measurements in exactly one place, the production
# packet, and that is what these cover.

def _packet_est():
    est = _est(
        structures=[
            _bld('st1', 'Building 1', 42, comm_perimeter_lf=520, comm_length_ft=200,
                 comm_width_ft=100, comm_height_ft=20, comm_uplift=60, comm_work_type=0),
            _bld('st2', 'Building 2', 38, comm_perimeter_lf=460, comm_length_ft=160,
                 comm_width_ft=90, comm_height_ft=20, comm_uplift=60, comm_work_type=1),
        ],
        items=[_item('TPO Membrane', 'Building 1', 'i1'),
               _item('TPO Membrane', 'Building 2', 'i2')],
    )
    est['customer'] = {'name': 'Gap Road Apartments', 'email': 'pm@example.com',
                       'phone': '3035550101',
                       'address': {'street': '1 Gap Rd', 'city': 'Golden', 'state': 'CO'}}
    est['signature'] = {'name': 'Dana Reyes', 'email': 'pm@example.com',
                        'signed_at': '2026-08-19T12:00:00Z', 'selected_tier': 'better',
                        'data': 'data:image/png;base64,AAA'}
    return est


def test_measurement_sets_is_one_per_building(A):
    sets = A._measurement_sets(_packet_est())
    assert [name for name, _m in sets] == ['Building 1', 'Building 2']
    assert [m['comm_squares'] for _n, m in sets] == [42, 38]


def test_measurement_sets_falls_back_to_the_estimates_own(A):
    """A single-roof job has no structures and must report exactly as it did."""
    est = _est(structures=[], measurements={'comm_squares': 26})
    assert A._measurement_sets(est) == [('', {'comm_squares': 26})]


def _pdf_text(raw):
    try:
        from pypdf import PdfReader
    except ImportError:
        pytest.skip('pypdf not installed')
    import io as _io
    r = PdfReader(_io.BytesIO(raw))
    return chr(10).join(p.extract_text() or '' for p in r.pages)


def test_the_packet_names_every_building(A):
    """A crew lays out corner fastener spacing from this sheet. Printing the
    first building's schedule once, unlabelled, is how the wrong roof gets
    fastened to the wrong number."""
    packet = A.build_production_packet_pdf(_packet_est())
    assert packet[:4] == b'%PDF'
    text = _pdf_text(packet)
    assert 'Building 1' in text and 'Building 2' in text


def test_the_packet_reports_each_buildings_job_type(A):
    """Re-roof on one building and new construction on another is an ordinary
    complex. One job type printed for all seven sends a tear-off crew to a
    building that never needed one."""
    text = _pdf_text(A.build_production_packet_pdf(_packet_est()))
    assert 'RE-ROOF' in text and 'NEW CONSTRUCTION' in text
