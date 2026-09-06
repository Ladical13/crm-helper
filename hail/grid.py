"""The MRMS grid, as arithmetic rather than as geometry.

MESH (Maximum Estimated Size of Hail) arrives as a gridded field: NOAA merges
the whole NEXRAD network into one continuous raster and estimates a hail size
for every cell, whether or not a human was standing there. That is the entire
reason to prefer it over SPC storm reports, which are phoned-in points.

**There is no polygon in here, and that is deliberate.** The question the
business actually asks — "what size hail did radar estimate over THIS roof on
THIS date" — is a cell lookup, not a point-in-polygon test. Contouring a raster
into rings would need shapely (and numpy to be tolerable), would introduce
interpolation error between the data and the answer we give a homeowner, and
would buy nothing: the honest answer is the value of the cell the roof sits in.
Polygons are a *rendering* concern, and `cell_rects()` serves that from the same
cells without pretending to a resolution the data does not have.

So this module has no third-party dependencies at all, which matters on a box
running two gunicorn workers.

Two traps live here.

**Units.** This system now speaks three. MRMS MESH is **millimetres**. The SPC
CSV the canvasser reads today is **hundredths of an inch**. Everything a rep or
a customer ever sees is **inches**. Conversion happens once, on the way in, and
`MM_PER_INCH` is the only place it is spelled.

**Negative longitude.** Colorado is around -105°, and `int(-105.05 / 0.01)`
truncates toward zero while `floor` goes down — so the naive version puts every
western cell one index off, consistently, in a way that still looks like
plausible output. Every quantization here goes through `math.floor`.
"""
import math

# MRMS ships MESH in millimetres.
#
# ⚠️ UNVERIFIED against a real GRIB2 file — the dev sandbox proxy blocks both
# mrms.ncep.noaa.gov and the Iowa State archive, so this is from the product
# documentation rather than from a message we have actually decoded. It is the
# single assumption most likely to be silently wrong, and being wrong by 25.4x
# would put every storm either far above or far below the 1" threshold. Confirm
# it on the first real ingest before anyone trusts a number in front of a
# customer.
MM_PER_INCH = 25.4

# MRMS CONUS is a 0.01° lattice. Cells are anchored to the GLOBAL lattice
# (multiples of 0.01° from 0, not from a clip corner) so a cell id means the
# same patch of ground in every storm, in every clip window, forever. Anchoring
# to a bounding box instead would make yesterday's cell ids meaningless the
# first time someone widened the service area.
CELL_DEG = 0.01

# Functional damage to an asphalt shingle roof starts around 1 inch. Below it
# a storm is not worth a door, and storing those cells would balloon the
# archive with hail nobody can sell.
DEFAULT_THRESHOLD_IN = 1.0


# Decimal places to settle binary representation error on before flooring.
# 1e-9 degrees is about 0.1mm — orders of magnitude finer than any coordinate
# we will ever hold — so this can only ever absorb float noise, never a real
# difference between two positions.
_QUANT_DP = 9


def cell_index(lat, lng, cell_deg=CELL_DEG):
    """(row, col) of the cell containing a point, on the global lattice.

    Two separate hazards, and both bite in Colorado.

    **floor, never int().** int() truncates toward zero, so at -105.005 it
    gives -10500 where floor gives -10501. Every cell in a western-hemisphere
    service area lands one index off — consistently, and in output that looks
    entirely normal.

    **Round before flooring.** 151.2 / 0.01 is 15119.999999999998 in binary
    floating point, so a point sitting exactly on a cell edge floors into the
    cell *below* the one it belongs to, and then `cell_bounds` reports a cell
    that does not contain it. Rare, unreproducible, and it would surface as a
    customer mysteriously absent from a swath they are standing in.
    """
    return (math.floor(round(lat / cell_deg, _QUANT_DP)),
            math.floor(round(lng / cell_deg, _QUANT_DP)))


def cell_bounds(ri, ci, cell_deg=CELL_DEG):
    """(south, west, north, east) of a cell, in degrees.

    Rounded for the same reason `cell_index` rounds, and it has to happen on
    both sides: 15120 * 0.01 is 151.20000000000002, so an unrounded west edge
    can sit just *above* the very point that quantized into it.
    """
    south = round(ri * cell_deg, _QUANT_DP)
    west = round(ci * cell_deg, _QUANT_DP)
    return (south, west,
            round(south + cell_deg, _QUANT_DP),
            round(west + cell_deg, _QUANT_DP))


def cell_center(ri, ci, cell_deg=CELL_DEG):
    south, west, north, east = cell_bounds(ri, ci, cell_deg)
    return ((south + north) / 2.0, (west + east) / 2.0)


def mm_to_inches(mm):
    return mm / MM_PER_INCH


class Swath:
    """The cells of one storm that exceeded the threshold.

    Sparse on purpose. A CONUS MESH grid is millions of cells and almost all of
    them are zero; a real Colorado hail day is thousands. Storing only what
    cleared the threshold is what keeps a decade of archive small enough to sit
    beside the other databases.
    """

    def __init__(self, cells=None, cell_deg=CELL_DEG, threshold_in=DEFAULT_THRESHOLD_IN):
        self.cells = dict(cells or {})       # {(ri, ci): size_inches}
        self.cell_deg = cell_deg
        self.threshold_in = threshold_in

    def __len__(self):
        return len(self.cells)

    def __bool__(self):
        """A swath with no cells is falsy — 'no qualifying hail', not 'no data'.
        Callers distinguish the two by whether an ingest ran at all."""
        return bool(self.cells)

    @property
    def max_size(self):
        return max(self.cells.values(), default=0.0)

    def size_at(self, lat, lng):
        """Estimated hail size over a point, in inches. 0.0 outside the swath.

        This is the number that ends up in front of a homeowner, so it is the
        cell's own value — not an average of neighbours, not an interpolation.
        We report what the radar estimated over that ground.
        """
        return self.cells.get(cell_index(lat, lng, self.cell_deg), 0.0)

    def covers(self, lat, lng):
        return self.size_at(lat, lng) > 0.0

    def bbox(self):
        """(south, west, north, east) enclosing every cell, or None if empty.

        Cheap pre-filter: a caller joining thousands of addresses tests the box
        before paying for a dict lookup per address.
        """
        if not self.cells:
            return None
        rows = [ri for ri, _ in self.cells]
        cols = [ci for _, ci in self.cells]
        return (min(rows) * self.cell_deg, min(cols) * self.cell_deg,
                (max(rows) + 1) * self.cell_deg, (max(cols) + 1) * self.cell_deg)

    def cell_rects(self):
        """[(south, west, north, east, size_in)] — for drawing.

        Rectangles at the data's real resolution. The canvasser's current hail
        overlay draws `max(500, size * 800)` metre circles around report points,
        a damage footprint that exists nowhere in the data; these are the actual
        cells and claim nothing beyond them.
        """
        return [cell_bounds(ri, ci, self.cell_deg) + (size,)
                for (ri, ci), size in sorted(self.cells.items())]


def swath_from_points(points, threshold_in=DEFAULT_THRESHOLD_IN,
                      cell_deg=CELL_DEG, units='in'):
    """Build a Swath from (lat, lng, size) triples.

    The decoder's output shape, kept deliberately dumb so that whatever reads
    GRIB2 — and that choice is still open — only has to yield points. Where a
    cell is hit more than once (a finer source grid, or overlapping sweeps) the
    **maximum** wins: MESH is already a maximum over its own window, and taking
    a mean would quietly shave the peak off every storm, which is exactly the
    number that decides whether a neighbourhood is worth knocking.
    """
    if units not in ('in', 'mm'):
        raise ValueError(f'unknown units: {units!r}')
    cells = {}
    for lat, lng, size in points:
        size_in = mm_to_inches(size) if units == 'mm' else size
        if size_in < threshold_in:
            continue
        key = cell_index(lat, lng, cell_deg)
        if size_in > cells.get(key, 0.0):
            cells[key] = size_in
    return Swath(cells, cell_deg=cell_deg, threshold_in=threshold_in)


def clip(points, bounds):
    """Filter (lat, lng, ...) tuples to a (south, west, north, east) box.

    Applied before anything else in the ingest: CONUS is two orders of
    magnitude more grid than Northern Colorado, and everything downstream —
    threshold, dedupe, storage — is cheaper for having dropped it first.
    """
    south, west, north, east = bounds
    return [p for p in points
            if south <= p[0] <= north and west <= p[1] <= east]
