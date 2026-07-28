"""Commercial fastener calculator — JS <-> Python parity, plus the rules.

Fastener density on a low-slope roof varies by roof zone, so this math decides
how many screws hold a commercial roof down. It is implemented twice for the
same reason pricing is: the rep's browser recalculates as they type, and the
server has to produce the same schedule for the production packet without
trusting the client.

`attic_ventilation` is the cautionary tale — its constants are mirrored in both
files with NOTHING to catch drift. So every fixture here is priced by the real
`app.js` functions under node AND by `app.py`, and any disagreement fails. The
same runner also covers atticVentilation, closing that older gap.

The other half of this file is the safety contract: when the calculator cannot
know the answer it must return ZERO and say why, never a plausible number.
"""
import json
import math
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, 'fastening_runner.js')
TABLE_PATH = os.path.join(os.path.dirname(HERE), 'commercial_fastening.json')

pytestmark = pytest.mark.skipif(shutil.which('node') is None,
                                reason='node not installed — the calculator cannot be run')

with open(TABLE_PATH, encoding='utf-8') as _f:
    TABLE = json.load(_f)


def _t(**over):
    """The shipped table with top-level fields overridden."""
    t = json.loads(json.dumps(TABLE))
    for k, v in over.items():
        if k == 'zone_rule':
            t['zone_rule'] = {**t['zone_rule'], **v}
        else:
            t[k] = v
    return t


def _m(**kw):
    """A fully-specified mechanically-attached roof; override what a case needs."""
    base = {'comm_length_ft': 100, 'comm_width_ft': 50, 'comm_height_ft': 20,
            'comm_uplift': 90, 'comm_seam_attach': 1, 'comm_insul_attach': 1}
    base.update(kw)
    return base


FIXTURES = [
    # ── the baseline, both corner geometries ─────────────────────────────
    # ASCE 7-16 L-corners (12a^2) vs 7-10 square corners (4a^2): a 3x
    # difference in the zone that matters most, which is why it is a setting.
    ('baseline L-corner', _m(), TABLE),
    ('baseline square corner', _m(), _t(zone_rule={'corner_shape': 'square'})),

    # ── each of the four clamps on `a` ───────────────────────────────────
    ('a pinned by 0.4h (short building)', _m(comm_height_ft=5), TABLE),
    ('a pinned by 0.1*least (tall building)', _m(comm_height_ft=200), TABLE),
    ('a pinned by the 3 ft floor', _m(comm_length_ft=25, comm_width_ft=20, comm_height_ft=4), TABLE),
    ('a pinned by 0.04*least floor', _m(comm_length_ft=400, comm_width_ft=300, comm_height_ft=6), TABLE),
    ('a capped at least/2 (tiny roof)', _m(comm_length_ft=8, comm_width_ft=6, comm_height_ft=40), TABLE),

    # ── zone overrides ───────────────────────────────────────────────────
    ('one zone overridden', _m(comm_zone_corner_sf=500), TABLE),
    ('all three overridden', _m(comm_zone_field_sf=3000, comm_zone_perim_sf=1500,
                                comm_zone_corner_sf=500), TABLE),
    ('override with no dimensions at all', {'comm_uplift': 90, 'comm_seam_attach': 1,
                                            'comm_zone_field_sf': 3000,
                                            'comm_zone_perim_sf': 1000,
                                            'comm_zone_corner_sf': 300}, TABLE),
    ('override sum diverges from the roof', _m(comm_zone_field_sf=100), TABLE),

    # ── uplift rating lookup ─────────────────────────────────────────────
    ('rating exact match', _m(comm_uplift=75), TABLE),
    ('rating rounds UP to the next row', _m(comm_uplift=70), TABLE),
    ('rating above every row', _m(comm_uplift=150), TABLE),
    ('rating below every row', _m(comm_uplift=10), TABLE),
    # "105" sorts below "60" as a string — both sides must sort numerically.
    ('rating needs numeric key sort', _m(comm_uplift=100), TABLE),

    # ── insulation layers: missing is 1, explicit 0 is 0 ─────────────────
    ('layers missing means one', _m(), TABLE),
    ('layers explicit 0 means none', _m(comm_insul_layers=0), TABLE),
    ('layers 2', _m(comm_insul_layers=2), TABLE),

    # ── attachment profile ───────────────────────────────────────────────
    ('adhered: seam off, insulation on', _m(comm_seam_attach=0), TABLE),
    ('coating: both off', _m(comm_seam_attach=0, comm_insul_attach=0), TABLE),
    ('seam flag absent fails closed', {'comm_length_ft': 100, 'comm_width_ft': 50,
                                       'comm_height_ft': 20, 'comm_uplift': 90}, TABLE),

    # ── bail-out paths: zero, never a guess ──────────────────────────────
    ('no dimensions and no override', {'comm_uplift': 90, 'comm_seam_attach': 1}, TABLE),
    ('no uplift rating picked', _m(comm_uplift=0), TABLE),
    ('empty table', _m(), _t(ratings={})),
    ('height missing', _m(comm_height_ft=0), TABLE),

    # ── reconciliation + misc ────────────────────────────────────────────
    ('bounding box disagrees with measured area', _m(comm_squares=30), TABLE),
    ('bounding box agrees with measured area', _m(comm_squares=50), TABLE),
    ('multi-level roof warns', _m(comm_sections=3), TABLE),
    ('waste at a ceil boundary', _m(), _t(waste_pct=3.7)),
    ('zero waste', _m(), _t(waste_pct=0)),
    ('non-standard board size', _m(), _t(board_sf=50)),
]


def _round(v):
    """Recursively round floats so 1e-15 representation noise doesn't fail parity."""
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, dict):
        return {k: _round(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_round(x) for x in v]
    return v


@pytest.fixture(scope='module')
def js_results(tmp_path_factory):
    """Run every fixture through the real app.js functions under node."""
    d = tmp_path_factory.mktemp('fastening')
    fx, out = d / 'fixtures.json', d / 'js.json'
    fx.write_text(json.dumps([{'name': n, 'm': m, 'table': t}
                              for n, m, t in FIXTURES]), encoding='utf-8')
    proc = subprocess.run(['node', RUNNER, str(fx), str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f'fastening_runner.js failed:\n{proc.stderr}'
    return {r['name']: r for r in json.loads(out.read_text(encoding='utf-8'))}


@pytest.mark.parametrize('name,m,table', FIXTURES, ids=[f[0] for f in FIXTURES])
def test_js_and_py_agree(js_results, name, m, table, A):
    """The whole point: one roof, two implementations, identical schedule."""
    py = _round(A.commercial_fastening(m, table))
    js = _round(js_results[name]['fasten'])
    # Warning TEXT is prose and formats differently per language; the COUNT of
    # warnings is behaviour and must match.
    assert len(py.pop('warnings')) == len(js.pop('warnings')), f'{name}: warning count'
    py.pop('rating_note'), js.pop('rating_note')
    assert py == js, name


@pytest.mark.parametrize('name,m,table', FIXTURES, ids=[f[0] for f in FIXTURES])
def test_attic_ventilation_js_and_py_agree(js_results, name, m, table, A):
    """Closes a long-standing gap: attic_ventilation's constants are mirrored in
    both files and nothing checked them until now."""
    assert _round(A.attic_ventilation(m)) == _round(js_results[name]['vent']), name


# ── the safety contract ────────────────────────────────────────────────

@pytest.mark.parametrize('m,reason', [
    ({'comm_uplift': 90}, 'missing_dimensions'),
    ({'comm_length_ft': 100, 'comm_width_ft': 50, 'comm_height_ft': 20}, 'no_uplift_rating'),
    ({'comm_length_ft': 100, 'comm_width_ft': 50, 'comm_uplift': 90}, 'missing_dimensions'),
])
def test_unknown_inputs_produce_zero_and_a_reason(A, m, reason):
    """It must never guess. Zero plus a named reason, so the panel and the packet
    can say WHAT is missing instead of showing a plausible count."""
    r = A.commercial_fastening(m, TABLE)
    assert r['ok'] is False
    assert r['reason'] == reason
    assert r['insul']['total'] == 0 and r['seam']['total'] == 0


def test_rating_never_rounds_down(A):
    """Rounding down to a lighter fastening schedule under-fastens the roof."""
    for asked, expect in [(60, 60), (61, 75), (75, 75), (90, 90), (91, 105)]:
        assert A.commercial_fastening(_m(comm_uplift=asked), TABLE)['rating'] == expect


def test_rating_above_every_row_uses_the_top_and_warns(A):
    r = A.commercial_fastening(_m(comm_uplift=500), TABLE)
    assert r['rating'] == 105
    assert r['rating_note']
    assert any('highest available' in w for w in r['warnings'])


def test_corner_shape_changes_the_corner_zone_threefold(A):
    """4a^2 (ASCE 7-10) vs 12a^2 (7-16) is the single biggest lever in here."""
    sq = A.commercial_fastening(_m(), _t(zone_rule={'corner_shape': 'square'}))
    ell = A.commercial_fastening(_m(), TABLE)
    assert ell['zones']['corner']['sf'] == pytest.approx(3 * sq['zones']['corner']['sf'])
    assert ell['insul']['total'] > sq['insul']['total']


def test_zone_areas_always_sum_to_the_roof(A):
    r = A.commercial_fastening(_m(), TABLE)
    total = sum(r['zones'][z]['sf'] for z in ('field', 'perimeter', 'corner'))
    assert total == pytest.approx(100 * 50)


def test_tiny_roof_is_all_corner_not_negative_perimeter(A):
    """12a^2 can exceed the edge band on a small roof; the clamp keeps perimeter
    at zero instead of going negative and cancelling out real fasteners."""
    r = A.commercial_fastening(_m(comm_length_ft=8, comm_width_ft=6, comm_height_ft=40), TABLE)
    assert r['zones']['perimeter']['sf'] >= 0
    assert r['zones']['corner']['sf'] > 0
    total = sum(r['zones'][z]['sf'] for z in ('field', 'perimeter', 'corner'))
    assert total == pytest.approx(48)


def test_insulation_layers_missing_is_one_but_explicit_zero_is_zero(A):
    """A cleared field stores an explicit 0 via parseFloat(v)||0, and 0 is a real
    answer — a recover with no new insulation."""
    assert A.commercial_fastening(_m(), TABLE)['insul']['total'] > 0
    assert A.commercial_fastening(_m(comm_insul_layers=0), TABLE)['insul']['total'] == 0
    one = A.commercial_fastening(_m(comm_insul_layers=1), TABLE)['insul']['raw']
    two = A.commercial_fastening(_m(comm_insul_layers=2), TABLE)['insul']['raw']
    assert two == pytest.approx(2 * one)


def test_seam_fails_closed_when_attachment_is_unknown(A):
    """An adhered system has no seam fasteners. Guessing 'yes' would put
    thousands of phantom screws on a bid."""
    m = {'comm_length_ft': 100, 'comm_width_ft': 50, 'comm_height_ft': 20, 'comm_uplift': 90}
    r = A.commercial_fastening(m, TABLE)
    assert r['seam']['total'] == 0 and r['seam']['applies'] is False
    assert r['insul']['total'] > 0, 'insulation still applies when unspecified'


def test_coating_zeroes_both_layers(A):
    """A restoration coating is a no-tear-off recover — no boards, no membrane.
    A single 'is mechanically attached' boolean would get this wrong."""
    r = A.commercial_fastening(_m(comm_seam_attach=0, comm_insul_attach=0), TABLE)
    assert r['insul']['total'] == 0 and r['seam']['total'] == 0


def test_bounding_box_mismatch_warns_instead_of_scaling(A):
    """An L-shaped roof's bounding box is bigger AND has more corners, so
    auto-scaling would be both hidden and unconservative."""
    r = A.commercial_fastening(_m(comm_squares=30), TABLE)   # 3000 SF vs 5000 SF bbox
    assert r['area_check']['warn'] is True
    assert any('not a rectangle' in w for w in r['warnings'])
    assert A.commercial_fastening(_m(comm_squares=50), TABLE)['area_check']['warn'] is False


def test_override_is_reported_per_zone(A):
    r = A.commercial_fastening(_m(comm_zone_corner_sf=500), TABLE)
    assert r['zones']['corner']['source'] == 'override'
    assert r['zones']['field']['source'] == 'computed'
    assert r['zone_source'] == 'mixed'


def test_multi_level_roof_is_flagged(A):
    """v1 computes zones for ONE rectangle; a stepped roof needs manual areas."""
    assert any('Multiple roof levels' in w
               for w in A.commercial_fastening(_m(comm_sections=3), TABLE)['warnings'])


def test_waste_rounds_up_to_whole_fasteners(A):
    r = A.commercial_fastening(_m(), _t(waste_pct=5))
    assert r['insul']['total'] == math.ceil(r['insul']['raw'] * 1.05)
    assert r['insul']['total'] == int(r['insul']['total'])


# ── the shipped table itself ───────────────────────────────────────────

def test_shipped_table_is_complete(A):
    """Every rating row needs all three zones for both layers, or a roof silently
    gets zero fasteners in one zone."""
    t = A._load_commercial_fastening()
    assert t['ratings'], 'no ratings shipped'
    for name, row in t['ratings'].items():
        assert str(name).isdigit(), f'rating key {name!r} must be the psf number'
        for z in ('field', 'perimeter', 'corner'):
            assert row['insul_per_board'].get(z), f'{name}/{z} insulation density missing'
            seam = row['seam'][z]
            assert seam.get('sheet_width_ft') and seam.get('spacing_in'), f'{name}/{z} seam spec missing'


def test_shipped_table_densities_increase_toward_the_corner(A):
    """Corners see the most uplift. A table where the corner is looser than the
    field is a data-entry error that would under-fasten exactly where it matters."""
    for name, row in A._load_commercial_fastening()['ratings'].items():
        d = row['insul_per_board']
        assert d['field'] <= d['perimeter'] <= d['corner'], f'{name}: densities not increasing'


def test_table_carries_its_disclaimer(A):
    """The seeded densities are generic and invented. That has to travel with the
    data, not just live in a code comment."""
    note = A._load_commercial_fastening().get('source_note', '')
    assert 'GENERIC' in note.upper()


def test_api_serves_and_manager_can_save(client, anon):
    got = client.get('/api/commercial-fastening')
    assert got.status_code == 200 and got.get_json()['ratings']
    edited = got.get_json()
    edited['board_sf'] = 48
    assert client.put('/api/commercial-fastening', json=edited).status_code == 200
    assert client.get('/api/commercial-fastening').get_json()['board_sf'] == 48


def test_saved_table_keeps_fields_added_later(A, client):
    """A table saved under v1 must gain v2 zone_rule fields rather than losing
    them to the merge."""
    client.put('/api/commercial-fastening',
               json={'ratings': TABLE['ratings'], 'zone_rule': {'corner_shape': 'square'}})
    t = A._load_commercial_fastening()
    assert t['zone_rule']['corner_shape'] == 'square'
    assert t['zone_rule']['a_pct_least'] == 0.10, 'lost a field the saved file did not have'
    assert t.get('source_note')
