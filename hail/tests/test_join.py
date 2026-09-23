"""Phase 2 — the join that cannot be bought.

The failure mode these guard against is not a crash. It is a storm brief that
confidently reports a smaller number than the truth, which reads as a small
storm and gets nobody out of bed.
"""
import pytest

from hail import grid as g
from hail import join

FOCO = (40.5853, -105.0844)
LOVELAND = (40.3978, -105.0750)
DENVER = (39.7392, -104.9903)


def _swath(*points):
    return g.swath_from_points(points)


def test_only_records_under_the_swath_come_back():
    swath = _swath((*FOCO, 1.75))
    hits, skipped = join.affected(swath, [
        {'name': 'In it',  'lat': FOCO[0],   'lng': FOCO[1]},
        {'name': 'Denver', 'lat': DENVER[0], 'lng': DENVER[1]},
    ])
    assert [h['name'] for h in hits] == ['In it']
    assert hits[0]['hail_size_in'] == pytest.approx(1.75)
    assert skipped == 0


def test_records_without_a_coordinate_are_counted_not_silently_dropped():
    """The most dangerous failure in this module.

    Drop them quietly and a storm brief says '1 customer affected' when the
    honest answer is '1 affected, 1 we could not place'. That reads as a small
    storm rather than as a gap in the geocoding, and nobody investigates a
    small storm.
    """
    swath = _swath((*FOCO, 1.75))
    hits, skipped = join.affected(swath, [
        {'name': 'Placed',   'lat': FOCO[0], 'lng': FOCO[1]},
        {'name': 'Unplaced', 'address': '123 Nowhere'},
    ])
    assert len(hits) == 1
    assert skipped == 1
    assert join.summarize(swath, hits, skipped)['skipped_no_coordinate'] == 1


def test_a_resolver_supplies_coordinates_the_record_lacks():
    swath = _swath((*FOCO, 1.75))
    known = {'123 Main St': FOCO}
    hits, skipped = join.affected(
        swath,
        [{'name': 'Lead', 'address': '123 Main St'}],
        resolve=lambda r: known.get(r.get('address')))
    assert len(hits) == 1 and skipped == 0
    assert hits[0]['lat'] == pytest.approx(FOCO[0])


def test_hits_are_ordered_worst_hit_first_then_nearest_the_peak():
    swath = _swath((*FOCO, 2.50), (*LOVELAND, 1.25))
    hits, _ = join.affected(swath, [
        {'name': 'Loveland', 'lat': LOVELAND[0], 'lng': LOVELAND[1]},
        {'name': 'FoCo',     'lat': FOCO[0],     'lng': FOCO[1]},
    ])
    assert [h['name'] for h in hits] == ['FoCo', 'Loveland']
    assert hits[0]['miles_from_peak'] == pytest.approx(0.0, abs=0.5)
    assert hits[1]['miles_from_peak'] > 5


def test_the_peak_is_stable_across_runs():
    """A rep's list must not reorder itself between two loads because a dict
    iterated differently."""
    swath = _swath((*FOCO, 2.0), (*LOVELAND, 2.0))
    assert join.peak_cell(swath) == join.peak_cell(swath)


def test_tier_order_beats_hail_size():
    """An RCP subscriber is a contractual obligation. The biggest hail on a
    cold address does not outrank someone we promised to look after — and that
    order has to be the same in the map, the drafts and the canvassing zones,
    which is why it lives here and not in each caller."""
    swath = _swath((*FOCO, 2.50), (*LOVELAND, 1.10))
    hits, _ = join.affected(swath, [
        {'name': 'Cold big hail', 'lat': FOCO[0], 'lng': FOCO[1], 'tier': 'cold'},
        {'name': 'RCP member', 'lat': LOVELAND[0], 'lng': LOVELAND[1], 'tier': 'rcp'},
    ])
    # Raw order is by size...
    assert [h['name'] for h in hits] == ['Cold big hail', 'RCP member']
    # ...but the tiering is what a rep actually works.
    tiers = join.by_tier(hits)
    assert [h['name'] for h in tiers['rcp']] == ['RCP member']
    assert [h['name'] for h in tiers['cold']] == ['Cold big hail']
    assert list(tiers) == list(join.TIERS)


def test_an_unknown_tier_lands_in_cold_rather_than_vanishing():
    """An unclassified customer is still a customer."""
    swath = _swath((*FOCO, 1.75))
    hits, _ = join.affected(swath, [
        {'name': 'Mystery', 'lat': FOCO[0], 'lng': FOCO[1], 'tier': 'not_a_tier'},
        {'name': 'Untagged', 'lat': FOCO[0], 'lng': FOCO[1]},
    ])
    tiers = join.by_tier(hits)
    assert len(tiers['cold']) == 2
    assert sum(len(v) for v in tiers.values()) == len(hits)


def test_min_size_can_be_raised_above_the_stored_threshold():
    """The archive stores everything over 1 inch, but a full deployment is
    worth calling only for the bigger events."""
    swath = _swath((*FOCO, 2.50), (*LOVELAND, 1.10))
    hits, _ = join.affected(swath, [
        {'name': 'Big',   'lat': FOCO[0],     'lng': FOCO[1]},
        {'name': 'Small', 'lat': LOVELAND[0], 'lng': LOVELAND[1]},
    ], min_size=1.5)
    assert [h['name'] for h in hits] == ['Big']


def test_an_empty_swath_affects_nobody_without_erroring():
    hits, skipped = join.affected(g.swath_from_points([]), [
        {'name': 'Anyone', 'lat': FOCO[0], 'lng': FOCO[1]},
    ])
    assert hits == [] and skipped == 0


def test_the_original_record_survives_the_join():
    """Callers need their own ids back to act on a hit — the join adds fields,
    it does not replace the row."""
    swath = _swath((*FOCO, 1.75))
    hits, _ = join.affected(swath, [
        {'lead_id': 'abc123', 'name': 'Jon Smith',
         'lat': FOCO[0], 'lng': FOCO[1], 'tier': 'past_customer'},
    ])
    assert hits[0]['lead_id'] == 'abc123'
    assert hits[0]['name'] == 'Jon Smith'
    assert hits[0]['tier'] == 'past_customer'


def test_summarize_reports_what_a_storm_brief_leads_with():
    swath = _swath((*FOCO, 2.50), (*LOVELAND, 1.25))
    hits, skipped = join.affected(swath, [
        {'lat': FOCO[0], 'lng': FOCO[1], 'tier': 'rcp'},
        {'lat': LOVELAND[0], 'lng': LOVELAND[1], 'tier': 'past_customer'},
        {'address': 'unplaceable'},
    ])
    s = join.summarize(swath, hits, skipped)
    assert s['max_size_in'] == pytest.approx(2.50)
    assert s['affected'] == 2
    assert s['skipped_no_coordinate'] == 1
    assert s['by_tier']['rcp'] == 1
    assert s['by_tier']['past_customer'] == 1
    assert s['cell_count'] == 2
