"""Fetch a day of MRMS MESH and turn it into a Swath.

This is the leg that was missing. `grid`, `storms` and `join` were written and
tested against synthetic points because the dev sandbox could not reach
`mrms.ncep.noaa.gov` or the Iowa State archive — both are refused by the egress
proxy as a policy denial, and they still are.

**NOAA mirrors MRMS to AWS Open Data, and S3 is reachable.** The
`noaa-mrms-pds` bucket carries the same operational products, so the whole
pipeline runs against `noaa-mrms-pds.s3.amazonaws.com` instead. Everything
below was verified end to end against a real file
(`20260612-233000`) rather than from documentation:

- Coverage runs **2020-10-14 to today**. Shallower than the Iowa State archive
  (2014), and still years past Colorado's one-year claim window (CRS
  10-4-110.8), so it is the depth that matters for a claim and most of the
  depth that matters for a retail sale.
- The grid is **0.01° regular lat/lon, 7000 x 3500**, which is the lattice
  `grid.CELL_DEG` was already built on. Confirmed, not assumed.
- Values are **millimetres**, which settles the warning that used to sit on
  `MM_PER_INCH`: the observed daily maximum was 125.5, which is 4.9 inches of
  hail — extreme but real. Read as inches it would be 125 inches, which is not
  a number weather produces.

**Section 7 is a PNG, not raw packed integers** (Data Representation Template
41). That is the fact that decides the dependency question: eccodes/cfgrib
would need system libraries on Railway, but a PNG decodes with **Pillow, which
this repo already depends on** for the estimator's proposals. So `hail/` gains
no new dependency, and the two-worker box gains no new system package.

**One file per day, not forty-eight.** The bucket publishes every 30 minutes,
but `MESH_Max_1440min` is a *rolling 24-hour maximum*, so the last file of the
day already contains that day's peak at every cell. Pulling all 48 would be 48x
the bytes for the same answer.
"""
import datetime as _dt
import gzip
import io
import struct

from hail import grid as hgrid

try:
    import requests as http
except ImportError:  # pragma: no cover - exercised by the import guard alone
    http = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

BUCKET = 'https://noaa-mrms-pds.s3.amazonaws.com'
PRODUCT = 'CONUS/MESH_Max_1440min_00.50'

# The last publication of the day. 1440min is a rolling maximum, so 23:30 holds
# the day's peak everywhere; taking 00:00 would report the *previous* day.
DAY_STAMP = '233000'

# The bucket's first day. Asking for anything earlier gets a 404, which is worth
# saying plainly rather than letting a backfill grind through years of misses.
EARLIEST = _dt.date(2020, 10, 14)

# Whole state, not just the service area — widening later must not mean
# re-ingesting years of history, and the clip is what makes the decode cheap.
COLORADO = (36.99, -109.06, 41.01, -102.04)

FETCH_TIMEOUT = 120

# GRIB2 Data Representation Template 41: PNG. The only one MRMS uses for this
# product, and the only one decoded here — anything else raises rather than
# being guessed at, because a silently misread grid is a confident wrong answer
# about someone's roof.
_DRT_PNG = 41


class IngestError(RuntimeError):
    """Anything that stops a day being turned into a swath."""


def day_url(date, stamp=DAY_STAMP, product=PRODUCT):
    d = date.strftime('%Y%m%d')
    return (f'{BUCKET}/{product}/{d}/'
            f'MRMS_{product.split("/")[-1]}_{d}-{stamp}.grib2.gz')


def fetch_day(date, stamp=DAY_STAMP, get=None):
    """Download one day's gzipped GRIB2. Returns bytes, or None if absent.

    A missing file is None rather than an exception: the bucket has genuine
    gaps, and a backfill has to be able to record "looked, nothing there" and
    move on. `storms.record()` stores an empty swath for exactly that reason.
    """
    if date < EARLIEST:
        raise IngestError(
            f'{date} is before the archive starts ({EARLIEST}). '
            'Earlier history needs the Iowa State MTArchive, which this '
            'environment cannot reach.')
    get = get or _get
    url = day_url(date, stamp)
    status, body = get(url)
    if status == 404:
        return None
    if status != 200:
        raise IngestError(f'{url} returned HTTP {status}')
    return body


def _get(url):
    if http is None:
        raise IngestError('requests is not available')
    resp = http.get(url, timeout=FETCH_TIMEOUT)
    return resp.status_code, resp.content


# ── GRIB2 ───────────────────────────────────────────────────────────────────

def _sections(data):
    """Walk a GRIB2 message, yielding (number, body). Body includes the header."""
    if data[:4] != b'GRIB':
        raise IngestError('not a GRIB message')
    if data[7] != 2:
        raise IngestError(f'GRIB edition {data[7]}, expected 2')
    pos = 16
    while pos < len(data) - 4:
        if data[pos:pos + 4] == b'7777':
            return
        length = struct.unpack('>I', data[pos:pos + 4])[0]
        if length <= 0 or pos + length > len(data):
            raise IngestError('truncated GRIB message')
        yield data[pos + 4], data[pos:pos + length]
        pos += length


def decode(data):
    """Decode a MESH GRIB2 message into (geometry, values).

    Returns (geometry, unpack, image).

    `geometry` is (la1, lo1, dy, dx, ni, nj) with la1/lo1 the NORTH-WEST corner,
    because this product scans west→east then north→south. `unpack` is the
    (reference, binary_scale, decimal_scale) triple, handed back rather than
    applied: the full grid is 24.5 million cells, callers only ever want
    Colorado, and converting all of it would cost far more than the crop.
    """
    if Image is None:
        raise IngestError('Pillow is not available')
    if data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)

    geom = ref = binscale = decscale = payload = None
    for num, body in _sections(data):
        if num == 3:
            gdt = struct.unpack('>H', body[12:14])[0]
            if gdt != 0:
                raise IngestError(f'grid template {gdt}, expected 0 (lat/lon)')
            ni, nj = struct.unpack('>II', body[30:38])
            la1, lo1 = struct.unpack('>ii', body[46:54])
            di, dj = struct.unpack('>II', body[63:71])
            scan = body[71]
            # Read the scan flags rather than trusting the corner names. A file
            # that ever ships south-to-north would otherwise be decoded upside
            # down and still look entirely plausible.
            if scan & 0x80:
                raise IngestError('east-to-west scanning is not handled')
            if scan & 0x40:
                raise IngestError('south-to-north scanning is not handled')
            if scan & 0x20:
                raise IngestError('column-major scanning is not handled')
            lo = lo1 / 1e6
            geom = (la1 / 1e6, lo - 360.0 if lo > 180 else lo,
                    dj / 1e6, di / 1e6, ni, nj)
        elif num == 5:
            drt = struct.unpack('>H', body[9:11])[0]
            if drt != _DRT_PNG:
                raise IngestError(
                    f'data representation template {drt}, expected {_DRT_PNG} '
                    '(PNG). Decoding another packing as PNG would produce a '
                    'plausible wrong grid.')
            ref = struct.unpack('>f', body[11:15])[0]
            binscale = struct.unpack('>h', body[15:17])[0]
            decscale = struct.unpack('>h', body[17:19])[0]
        elif num == 7:
            payload = body[5:]

    if geom is None or payload is None or ref is None:
        raise IngestError('GRIB message is missing a required section')

    img = Image.open(io.BytesIO(payload))
    img.load()
    if img.size != (geom[4], geom[5]):
        raise IngestError(f'PNG {img.size} does not match grid '
                          f'{(geom[4], geom[5])}')

    # GRIB2 unpacking: value = (R + X * 2^E) / 10^D.
    unpack = (ref, 2.0 ** binscale, 10.0 ** decscale)
    return geom, unpack, img


def points_in(data, bounds=COLORADO):
    """[(lat, lng, mm)] for every cell inside `bounds`.

    Clipping here rather than downstream is what keeps this cheap: CONUS is 24.5
    million cells and Colorado is about 160 thousand, so the crop happens before
    a single value is read.

    Flag values are left in and filtered by the threshold rather than special-
    cased. MRMS encodes "no coverage" and "no hail" as negatives (-3.0 and -1.0
    in the file this was built against), and any threshold a roofer cares about
    is far above zero — so `swath_from_points` drops them for free, and a new
    flag value cannot sneak past a list this module has to keep up to date.
    """
    (la1, lo1, dy, dx, ni, nj), (ref, scale_b, scale_d), img = decode(data)
    south, west, north, east = bounds

    r0 = max(0, int((la1 - north) / dy))
    r1 = min(nj, int((la1 - south) / dy) + 1)
    c0 = max(0, int((west - lo1) / dx))
    c1 = min(ni, int((east - lo1) / dx) + 1)
    if r0 >= r1 or c0 >= c1:
        return []

    crop = img.crop((c0, r0, c1, r1))
    width = c1 - c0
    out = []
    for i, raw in enumerate(crop.getdata()):
        row, col = r0 + i // width, c0 + i % width
        out.append((round(la1 - row * dy, 6),
                    round(lo1 + col * dx, 6),
                    (ref + raw * scale_b) / scale_d))
    return out


def swath_for(date, bounds=COLORADO, threshold_in=hgrid.DEFAULT_THRESHOLD_IN,
              get=None):
    """Fetch one day and return its Swath. An absent file yields an empty one.

    Empty is a real answer — `storms.record()` keeps it so that "no qualifying
    hail" and "the ingest never ran" stay distinguishable.
    """
    body = fetch_day(date, get=get)
    if body is None:
        return hgrid.Swath({}, threshold_in=threshold_in)
    return hgrid.swath_from_points(points_in(body, bounds),
                                   threshold_in=threshold_in, units='mm')
