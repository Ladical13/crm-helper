"""Phase 2: which of OUR people are under the swath.

This is the module that justifies building any of this. A hail map is a
commodity — several vendors sell one, and we will not beat a weather company at
weather. What no vendor can sell us is this join: the swath against our Roof
Care Plan subscribers, our past customers, our open estimates and our dead
leads from two seasons ago. It needs our CRM, which is precisely why it cannot
be bought, and why it is the moat rather than the map.

**This module is deliberately dumb about what a record is**, the same way
`prospector/` is deliberately dumb about what is already in the CRM. It takes
whatever rows a caller hands it — leads, Base44 contacts, RCP subscribers,
estimates — resolves each to a coordinate, and reports which sit under hail and
how big. Deciding what those rows mean, who owns them, and who gets called
first is the caller's job. Teaching this module about lead stages would couple
the storm archive to the CRM's schema and guarantee it breaks the next time a
stage is renamed.

Ranking is by hail size, then by distance from the swath's peak — a rep working
down the list is walking from the worst-hit roof outward, which is both the
best use of the day and the easiest order to explain to them.
"""
import math

from hail import grid as hgrid


# Priority bands. Named here rather than in the caller because the ORDER is a
# business rule that must not drift between the map, the email drafts and the
# canvassing zones: an RCP subscriber is a contractual obligation and is
# contacted first, always, even when a past customer took bigger hail.
TIERS = ('rcp', 'past_customer', 'open_lead', 'lost_estimate', 'cold')
_TIER_RANK = {t: i for i, t in enumerate(TIERS)}


def _haversine_miles(lat1, lng1, lat2, lng2):
    r = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


def peak_cell(swath):
    """(lat, lng) of the worst-hit cell's centre, or None for an empty swath.

    Ties break on the cell key so the peak is stable across runs — a rep's list
    must not reorder itself because a dict iterated differently.
    """
    if not swath.cells:
        return None
    key = max(sorted(swath.cells), key=lambda k: swath.cells[k])
    return hgrid.cell_center(*key, cell_deg=swath.cell_deg)


def affected(swath, records, resolve=None, min_size=None):
    """Records sitting under the swath, worst-hit first.

    `records` is any iterable of dicts. Each needs a coordinate, supplied
    either as 'lat'/'lng' keys or by `resolve(record) -> (lat, lng) | None`;
    the usual resolver reads `portal.geo.lookup`, which is why the geocode
    backfill blocks this whole phase.

    A record with no coordinate is SKIPPED, not counted as a miss — and callers
    must report how many were skipped. Silently dropping un-geocoded customers
    is how a storm report says "we have 40 customers affected" when the true
    answer is 400 and the other 360 simply had no lat/lng. That failure looks
    exactly like a small storm.

    Returns (hits, skipped). Each hit is the original record plus `hail_size_in`,
    `miles_from_peak` and, where the record carried one, its `tier`.
    """
    floor = swath.threshold_in if min_size is None else min_size
    box = swath.bbox()
    if box is None:
        return [], 0
    south, west, north, east = box
    peak = peak_cell(swath)

    hits, skipped = [], 0
    for rec in records:
        lat, lng = rec.get('lat'), rec.get('lng')
        if (lat is None or lng is None) and resolve is not None:
            got = resolve(rec)
            if got:
                lat, lng = got
        if lat is None or lng is None:
            skipped += 1
            continue
        # Cheap box reject before the dict lookup — most of the customer list
        # is nowhere near any given storm.
        if not (south <= lat <= north and west <= lng <= east):
            continue
        size = swath.size_at(lat, lng)
        if size < floor:
            continue
        hit = dict(rec)
        hit['lat'], hit['lng'] = lat, lng
        hit['hail_size_in'] = size
        hit['miles_from_peak'] = (round(_haversine_miles(lat, lng, *peak), 2)
                                  if peak else 0.0)
        hits.append(hit)

    hits.sort(key=lambda h: (-h['hail_size_in'], h['miles_from_peak']))
    return hits, skipped


def by_tier(hits):
    """Group hits into the priority bands, each still worst-hit first.

    Order is TIERS, not hail size: the biggest hail on a cold address does not
    outrank a Roof Care Plan subscriber we have promised to look after.
    Anything with an unknown or missing tier lands in 'cold' rather than being
    dropped — an unclassified customer is still a customer.
    """
    out = {t: [] for t in TIERS}
    for h in hits:
        tier = h.get('tier')
        out[tier if tier in _TIER_RANK else 'cold'].append(h)
    return out


def summarize(swath, hits, skipped=0):
    """The numbers a storm brief leads with.

    `skipped` is carried through and reported rather than quietly dropped,
    because a large skip count means the answer is understated — and an
    understated storm brief is one nobody acts on.
    """
    tiers = by_tier(hits)
    return {
        'max_size_in': swath.max_size,
        'cell_count': len(swath),
        'affected': len(hits),
        'skipped_no_coordinate': skipped,
        'by_tier': {t: len(v) for t, v in tiers.items()},
        'peak': peak_cell(swath),
    }


def geo_resolver():
    """The usual resolver: the shared address→coordinate cache.

    Imported lazily so `hail/` stays importable — and testable — without the
    portal's database being present.
    """
    from portal import geo

    def resolve(rec):
        hit = geo.lookup(rec.get('address', ''), rec.get('city', ''),
                         rec.get('state', ''), rec.get('zip', ''))
        return (hit['lat'], hit['lng']) if hit else None

    return resolve
