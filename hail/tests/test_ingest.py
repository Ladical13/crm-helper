"""The GRIB2 decode, against a message this test builds itself.

No fixture file: a real MRMS message is a megabyte of PNG and would be the
largest thing in the repo by an order of magnitude. Instead each test builds a
tiny GRIB2 message with the same structure the real product uses — grid
template 0, data representation template 41, 16-bit PNG, west→east then
north→south — with values chosen so a misread is arithmetically obvious.

The structure being mirrored was read off a real file
(`MRMS_MESH_Max_1440min_00.50_20260612-233000.grib2.gz` from the
`noaa-mrms-pds` bucket), not from documentation. `test_the_live_product_still_
decodes` re-checks that against the bucket when the network allows.
"""
import datetime as dt
import io
import struct

import pytest

from hail import grid as g
from hail import ingest

PIL = pytest.importorskip('PIL.Image')


# ── Building a message ──────────────────────────────────────────────────────

def _png16(values, ni, nj):
    img = PIL.new('I;16', (ni, nj))
    img.putdata(values)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def _section(num, payload):
    return struct.pack('>IB', len(payload) + 5, num) + payload


def _message(values, ni=4, nj=3, la1=41.0, lo1=-109.0, d=0.01,
             ref=-30.0, binscale=0, decscale=1, gdt=0, drt=41, scan=0):
    """A minimal but structurally faithful MESH message."""
    # oct 6 source, 7-10 npoints, 11 optional-list octets, 12 interpretation,
    # 13-14 template number. Nine bytes: one B too many here shifts every field
    # after it and the grid reads as nonsense.
    gds = struct.pack('>BIBBH', 0, ni * nj, 0, 0, gdt)              # oct 6-14
    gds += b'\x00' * 16                                              # oct 15-30
    gds += struct.pack('>II', ni, nj)                                # oct 31-38
    gds += b'\x00' * 8                                               # oct 39-46
    gds += struct.pack('>ii', int(la1 * 1e6), int(lo1 * 1e6))        # oct 47-54
    gds += b'\x00'                                                   # oct 55
    gds += struct.pack('>ii', int((la1 - nj * d) * 1e6),
                       int((lo1 + ni * d) * 1e6))                    # oct 56-63
    gds += struct.pack('>II', int(d * 1e6), int(d * 1e6))            # oct 64-71
    gds += struct.pack('>B', scan)                                   # oct 72

    drs = struct.pack('>IH', ni * nj, drt)
    drs += struct.pack('>fhhB', ref, binscale, decscale, 16)
    drs += b'\x00'

    body = (_section(1, b'\x00' * 16)
            + _section(3, gds)
            + _section(5, drs)
            + _section(6, b'\xff')
            + _section(7, _png16(values, ni, nj)))
    header = b'GRIB' + b'\x00\x00' + bytes([209, 2])
    return header + struct.pack('>Q', 16 + len(body) + 4) + body + b'7777'


# raw 30 -> 0.0mm, raw 284 -> 25.4mm (1.00in), raw 480 -> 45.0mm (1.77in)
RAW_ZERO, RAW_1IN, RAW_BIG = 30, 284, 480


# ── Geometry ────────────────────────────────────────────────────────────────

def test_a_cell_decodes_at_the_coordinate_it_belongs_to():
    """Row 0 is the NORTH edge — the product scans north→south. Read it the
    other way and every swath is mirrored about the middle of the country,
    which still looks like a plausible map."""
    vals = [RAW_ZERO] * 12
    vals[0] = RAW_BIG                       # row 0, col 0 = NW corner
    pts = ingest.points_in(_message(vals), (40.0, -110.0, 42.0, -108.0))
    top = max(pts, key=lambda p: p[2])
    assert top[0] == pytest.approx(41.0), 'row 0 must be the north edge'
    assert top[1] == pytest.approx(-109.0), 'col 0 must be the west edge'


def test_longitude_past_180_is_brought_back_into_range():
    """MRMS states its west edge as 230.005°E. Left as-is that is in the
    Pacific and every Colorado lookup misses."""
    pts = ingest.points_in(_message([RAW_ZERO] * 12, lo1=230.005 - 360.0),
                           (-90, -180, 90, 180))
    assert all(-180 <= p[1] <= 0 for p in pts)


def test_the_clip_returns_only_cells_inside_the_box():
    pts = ingest.points_in(_message([RAW_ZERO] * 12), (40.985, -108.985, 41.0, -108.97))
    assert pts, 'a box inside the grid must return something'
    for lat, lng, _ in pts:
        assert 40.97 <= lat <= 41.01 and -109.0 <= lng <= -108.96


def test_a_box_outside_the_grid_is_empty_rather_than_an_error():
    assert ingest.points_in(_message([RAW_ZERO] * 12), (10.0, -80.0, 11.0, -79.0)) == []


# ── Values ──────────────────────────────────────────────────────────────────

def test_values_unpack_to_millimetres():
    """GRIB2: (R + X*2^E) / 10^D. With the product's own R=-30, E=0, D=1 that
    is (raw - 30) / 10, and 284 is exactly one inch."""
    vals = [RAW_ZERO] * 12
    vals[5] = RAW_1IN
    pts = ingest.points_in(_message(vals), (40.0, -110.0, 42.0, -108.0))
    assert max(p[2] for p in pts) == pytest.approx(25.4)
    assert max(p[2] for p in pts) / g.MM_PER_INCH == pytest.approx(1.0)


def test_the_decimal_scale_is_honoured_rather_than_assumed():
    """Hardcode /10 and a file that ever ships D=2 reads 10x high — every storm
    would clear every threshold."""
    vals = [RAW_ZERO] * 12
    vals[0] = 1030
    a = ingest.points_in(_message(vals, decscale=1), (40.0, -110.0, 42.0, -108.0))
    b = ingest.points_in(_message(vals, decscale=2), (40.0, -110.0, 42.0, -108.0))
    assert max(p[2] for p in a) == pytest.approx(100.0)
    assert max(p[2] for p in b) == pytest.approx(10.0)


def test_negative_flag_values_are_dropped_by_the_threshold_not_by_a_list():
    """MRMS encodes 'no coverage' and 'no hail' as negatives. Nothing here
    enumerates them — any threshold a roofer cares about is far above zero, so
    a new flag value cannot sneak past a list this module forgot to update."""
    vals = [0] * 12          # raw 0 -> -3.0mm
    vals[7] = RAW_BIG
    pts = ingest.points_in(_message(vals), (40.0, -110.0, 42.0, -108.0))
    assert min(p[2] for p in pts) < 0, 'flags survive into points_in'
    swath = g.swath_from_points(pts, units='mm')
    assert len(swath) == 1, 'and are dropped by the threshold'
    assert swath.max_size == pytest.approx(45.0 / 25.4, abs=0.01)


# ── Refusing to guess ───────────────────────────────────────────────────────

@pytest.mark.parametrize('kwargs,fragment', [
    ({'drt': 40}, 'template 40'),
    ({'gdt': 30}, 'grid template 30'),
    ({'scan': 0x40}, 'south-to-north'),
    ({'scan': 0x80}, 'east-to-west'),
    ({'scan': 0x20}, 'column-major'),
])
def test_an_unexpected_encoding_raises_rather_than_being_guessed(kwargs, fragment):
    """Every one of these would otherwise produce a complete, plausible,
    wrongly-placed grid — the failure mode that reaches a customer."""
    with pytest.raises(ingest.IngestError) as e:
        ingest.points_in(_message([RAW_ZERO] * 12, **kwargs), ingest.COLORADO)
    assert fragment in str(e.value)


def test_a_non_grib_payload_is_refused():
    with pytest.raises(ingest.IngestError):
        ingest.points_in(b'this is not a GRIB file at all', ingest.COLORADO)


# ── Fetching ────────────────────────────────────────────────────────────────

def test_the_url_matches_the_bucket_layout():
    url = ingest.day_url(dt.date(2026, 6, 12))
    assert url == ('https://noaa-mrms-pds.s3.amazonaws.com/'
                   'CONUS/MESH_Max_1440min_00.50/20260612/'
                   'MRMS_MESH_Max_1440min_00.50_20260612-233000.grib2.gz')


def test_a_missing_day_is_none_rather_than_an_exception():
    """The bucket has genuine gaps, and a backfill has to record 'looked,
    nothing there' and keep going."""
    assert ingest.fetch_day(dt.date(2026, 6, 12), get=lambda u: (404, b'')) is None


def test_a_server_error_is_not_mistaken_for_an_empty_day():
    with pytest.raises(ingest.IngestError):
        ingest.fetch_day(dt.date(2026, 6, 12), get=lambda u: (503, b''))


def test_a_date_before_the_archive_says_so_plainly():
    """Rather than letting a backfill grind through years of 404s."""
    with pytest.raises(ingest.IngestError) as e:
        ingest.fetch_day(dt.date(2014, 6, 12), get=lambda u: (404, b''))
    assert '2020-10-14' in str(e.value)


def test_a_missing_day_yields_an_empty_swath_not_a_crash():
    swath = ingest.swath_for(dt.date(2026, 6, 12), get=lambda u: (404, b''))
    assert not swath and len(swath) == 0


# ── Against the real bucket ─────────────────────────────────────────────────

@pytest.mark.live
def test_the_live_product_still_decodes():
    """Everything above is built on a structure read off one real file. This
    catches the day NOAA changes the packing, the grid or the units — at which
    point every number this system reports about a customer's roof is wrong and
    still looks completely normal.

    Deselected by default (`-m 'not live'` in pytest.ini) so the suite stays
    offline and deterministic; run it deliberately with `-m live`.
    """
    body = ingest.fetch_day(dt.date(2026, 6, 12))
    assert body is not None
    (la1, lo1, dy, dx, ni, nj), (ref, sb, sd), _img = ingest.decode(body)
    assert (ni, nj) == (7000, 3500)
    assert dx == pytest.approx(g.CELL_DEG) and dy == pytest.approx(g.CELL_DEG)
    assert la1 == pytest.approx(54.995) and lo1 == pytest.approx(-129.995)
    assert (ref, sb, sd) == (pytest.approx(-30.0), 1.0, 10.0)

    pts = ingest.points_in(body, ingest.COLORADO)
    assert len(pts) > 100_000
    peak_mm = max(p[2] for p in pts)
    # Sanity on the units. Anything over ~8in of hail in Colorado would mean
    # the scale is being read wrong, not that the weather was remarkable.
    assert 0 < peak_mm / g.MM_PER_INCH < 8
