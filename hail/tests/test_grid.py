"""The grid arithmetic. Everything downstream inherits whatever this gets wrong.

These are the failures that would produce plausible, confident, wrong answers
rather than errors — a swath in the wrong place, or every storm 25x too big.
"""
import math

import pytest

from hail import grid as g

# Fort Collins, and the reason the longitude tests exist at all.
FOCO = (40.5853, -105.0844)


# ── Quantization ────────────────────────────────────────────────────────────

def test_negative_longitude_floors_instead_of_truncating():
    """The bug this codebase would otherwise have shipped.

    int() truncates toward zero; floor goes down. Colorado is entirely west of
    the meridian, so the naive version puts every cell in the service area one
    index off — consistently, silently, and in output that looks entirely
    normal.

    -105.005 rather than a rounder number on purpose: at some values binary
    floating point happens to hide the difference, and a test that picks one of
    those passes against the broken implementation too.
    """
    lng = -105.005
    scaled = lng / g.CELL_DEG
    assert int(scaled) == -10500 and math.floor(scaled) == -10501, (
        'this value must actually expose the truncation difference')

    _, ci = g.cell_index(40.0, lng)
    assert ci == -10501, 'cell_index must floor, not truncate'


def test_a_point_lands_inside_the_bounds_of_its_own_cell():
    """Round-trip: quantize a point, and the cell it names must contain it.
    An off-by-one in either direction breaks this for half the plane."""
    # 151.2 and -105.05 sit exactly on cell edges, which is where binary
    # representation error puts a point outside the cell it quantized into.
    for lat, lng in [FOCO, (40.0, -105.0), (-33.9, 151.2), (0.0, 0.0),
                     (40.5853, -105.0), (39.999999, -104.999999),
                     (40.0, -105.05), (40.01, -105.0), (-33.87, 151.21)]:
        ri, ci = g.cell_index(lat, lng)
        south, west, north, east = g.cell_bounds(ri, ci)
        assert south <= lat < north, f'{lat} outside [{south}, {north})'
        assert west <= lng < east, f'{lng} outside [{west}, {east})'


def test_cells_are_anchored_to_the_global_lattice_not_to_a_clip_box():
    """A cell id must mean the same ground forever. Anchor to a bounding box
    and every stored id becomes meaningless the first time someone widens the
    service area."""
    assert g.cell_index(40.0, -105.0) == (4000, -10500)
    assert g.cell_index(0.0, 0.0) == (0, 0)


def test_neighbouring_points_across_a_cell_edge_are_different_cells():
    """Cell edges are multiples of 0.01°, so the straddle has to be around
    40.01 — not 40.005, which is interior and would pass on any implementation."""
    assert g.cell_index(40.0099, -105.0) != g.cell_index(40.0101, -105.0)
    assert g.cell_index(40.0091, -105.0) == g.cell_index(40.0099, -105.0)


# ── Units ───────────────────────────────────────────────────────────────────

def test_millimetres_convert_to_inches():
    """Three units live in this system: MRMS is mm, the SPC CSV is hundredths
    of an inch, and everything a human sees is inches. Getting this backwards
    is a 25.4x error — every storm either always or never clears 1 inch."""
    assert g.mm_to_inches(25.4) == pytest.approx(1.0)
    assert g.mm_to_inches(44.45) == pytest.approx(1.75)
    assert g.MM_PER_INCH == 25.4


def test_a_swath_built_from_millimetres_thresholds_in_inches():
    # 30mm is 1.18" (clears); 20mm is 0.79" (does not).
    swath = g.swath_from_points([(40.5, -105.0, 30.0), (40.6, -105.1, 20.0)],
                                units='mm')
    assert len(swath) == 1
    assert swath.max_size == pytest.approx(30 / 25.4)


def test_unknown_units_are_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        g.swath_from_points([(40.5, -105.0, 1.5)], units='cm')


# ── Building a swath ────────────────────────────────────────────────────────

def test_below_threshold_points_are_dropped():
    swath = g.swath_from_points([
        (40.5, -105.0, 1.75),
        (40.6, -105.1, 0.75),   # pea hail — damages nothing, sells nothing
    ])
    assert len(swath) == 1
    assert swath.max_size == pytest.approx(1.75)


def test_the_maximum_wins_when_a_cell_is_hit_twice():
    """MESH is already a maximum over its own window. Averaging overlapping
    reads would shave the peak off every storm — and the peak is the number
    that decides whether a neighbourhood is worth a day of knocking."""
    swath = g.swath_from_points([
        (40.5001, -105.0001, 1.25),
        (40.5002, -105.0002, 2.50),   # same cell
        (40.5003, -105.0003, 1.75),   # same cell
    ])
    assert len(swath) == 1
    assert swath.max_size == pytest.approx(2.50)


def test_an_empty_swath_is_falsy_but_still_a_real_object():
    """'We looked and there was no qualifying hail' must be representable —
    otherwise it is indistinguishable from an ingest that never ran."""
    swath = g.swath_from_points([(40.5, -105.0, 0.5)])
    assert not swath
    assert len(swath) == 0
    assert swath.max_size == 0.0
    assert swath.bbox() is None


# ── Lookup ──────────────────────────────────────────────────────────────────

def test_size_at_reports_the_cell_the_roof_sits_in():
    swath = g.swath_from_points([(40.5853, -105.0844, 1.75)])
    assert swath.size_at(40.5853, -105.0844) == pytest.approx(1.75)
    assert swath.covers(40.5853, -105.0844)


def test_outside_the_swath_is_zero_not_an_error():
    swath = g.swath_from_points([(40.5853, -105.0844, 1.75)])
    assert swath.size_at(39.0, -104.0) == 0.0
    assert not swath.covers(39.0, -104.0)


def test_bbox_encloses_every_cell():
    swath = g.swath_from_points([
        (40.50, -105.10, 1.5),
        (40.60, -105.00, 2.0),
    ])
    south, west, north, east = swath.bbox()
    assert south <= 40.50 and north >= 40.60
    assert west <= -105.10 and east >= -105.00


def test_cell_rects_claim_only_the_data_resolution():
    """The canvasser's current overlay draws max(500, size*800) metre circles
    around report points — a damage footprint that exists nowhere in the data.
    These are the real cells and assert nothing wider."""
    swath = g.swath_from_points([(40.5, -105.0, 1.5)])
    (south, west, north, east, size), = swath.cell_rects()
    assert size == pytest.approx(1.5)
    assert north - south == pytest.approx(g.CELL_DEG)
    assert east - west == pytest.approx(g.CELL_DEG)


# ── Clipping ────────────────────────────────────────────────────────────────

def test_clip_keeps_only_points_in_the_box():
    """CONUS is two orders of magnitude more grid than Northern Colorado, and
    everything downstream is cheaper for having dropped it first."""
    noco = (40.0, -105.6, 41.0, -104.6)
    kept = g.clip([(40.5, -105.0, 1.5),   # Fort Collins
                   (32.8, -96.8, 2.0),    # Dallas
                   (41.5, -105.0, 1.5)],  # Wyoming
                  noco)
    assert len(kept) == 1
    assert kept[0][0] == 40.5


# ── How far a MESH number can be repeated ──────────────────────────────

def test_a_reading_above_the_us_record_is_called_impossible():
    """The 2026 Colorado season holds two cells at 8.85 and 8.56 inches. The
    largest hailstone ever recovered in the US was 8.0 (Vivian, SD, 2010), so
    a rep repeating 8.85 to a homeowner has said something that cannot be true
    — and that is the overclaim this whole package exists to prevent, arriving
    from the other direction."""
    assert g.size_caveat(8.85) == 'impossible'
    assert g.size_caveat(8.56) == 'impossible'
    assert 'do not quote' in g.size_note(8.85)


def test_rare_but_real_hail_is_flagged_to_verify_not_refused():
    """4 inches is a once-in-years stone for a town, not a physical
    impossibility. Calling it impossible would cry wolf on the storms that
    matter most."""
    assert g.size_caveat(4.0) == 'verify'
    assert g.size_caveat(5.0) == 'verify'
    assert 'confirm on' in g.size_note(4.0)


def test_ordinary_hail_carries_no_hedge():
    """Almost every storm is here. A caveat on all of them is one nobody
    reads by the second week."""
    for size in (0.75, 1.0, 1.75, 2.5, 3.99):
        assert g.size_caveat(size) == ''
        assert g.size_note(size) == ''


def test_the_caveat_never_changes_the_value():
    """Capping would misreport NOAA's own product — claiming the radar said 4
    when it said 8.85 — and the archive would then disagree with the source it
    was built from. The number survives; only what is said about it changes."""
    import inspect
    src = inspect.getsource(g.size_caveat) + inspect.getsource(g.size_note)
    assert 'min(' not in src and 'max(' not in src, 'this must not clamp'


def test_a_missing_or_unparseable_size_is_not_an_alarm():
    for bad in (None, '', 'x', object()):
        assert g.size_caveat(bad) == ''
