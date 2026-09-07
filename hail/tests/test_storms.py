"""The storm archive — idempotence, and the difference between 'no hail' and
'never looked'."""
import pytest

from hail import grid as g
from hail import storms


def _swath(*points, threshold=1.0):
    return g.swath_from_points(points, threshold_in=threshold)


FOCO = (40.5853, -105.0844)
LOVELAND = (40.3978, -105.0750)


def test_a_recorded_storm_round_trips():
    swath = _swath((*FOCO, 1.75), (*LOVELAND, 1.25))
    event = storms.record('2026-06-12', swath)

    assert event['event_date'] == '2026-06-12'
    assert event['cell_count'] == 2
    assert event['max_size_in'] == pytest.approx(1.75)

    back = storms.load_swath(event['event_id'])
    assert len(back) == 2
    assert back.size_at(*FOCO) == pytest.approx(1.75)
    assert back.threshold_in == 1.0


def test_reingesting_a_date_replaces_rather_than_merges():
    """The normal case, not an edge case. A day's MESH is finalized hours after
    the fact and the real-time product is a rolling maximum, so the same date
    gets pulled while it happens, again that night, and again from the archive
    months later.

    Merging would let a partial early read leave phantom cells behind after the
    fuller one landed — hail on a street that, in the final data, never got any.
    A rep knocks it and finds nothing.
    """
    storms.record('2026-06-12', _swath((*FOCO, 1.25), (*LOVELAND, 1.10)))
    event = storms.record('2026-06-12', _swath((*FOCO, 2.00)))

    assert event['cell_count'] == 1, 'the Loveland cell must be gone, not merged'
    back = storms.load_swath(event['event_id'])
    assert back.size_at(*FOCO) == pytest.approx(2.00)
    assert back.size_at(*LOVELAND) == 0.0
    assert len(storms.events()) == 1, 're-ingest must not duplicate the event'


def test_a_quiet_day_is_recorded_so_a_gap_means_something():
    """Without this, 'no qualifying hail on the 13th' and 'the ingest never ran
    on the 13th' are the same absence — and a backfill can never tell which
    days it still owes."""
    event = storms.record('2026-06-13', _swath((*FOCO, 0.50)))
    assert event['cell_count'] == 0
    assert event['max_size_in'] == 0
    assert '2026-06-13' in storms.ingested_dates()


def test_ingested_dates_is_what_a_backfill_skips():
    storms.record('2026-06-12', _swath((*FOCO, 1.5)))
    storms.record('2026-06-13', _swath())
    assert storms.ingested_dates() == {'2026-06-12', '2026-06-13'}


def test_events_filter_by_date_and_size():
    storms.record('2026-05-01', _swath((*FOCO, 1.10)))
    storms.record('2026-06-12', _swath((*FOCO, 2.00)))
    storms.record('2026-07-04', _swath((*FOCO, 1.50)))

    assert [e['event_date'] for e in storms.events()] == \
        ['2026-07-04', '2026-06-12', '2026-05-01']
    assert [e['event_date'] for e in storms.events(since='2026-06-01')] == \
        ['2026-07-04', '2026-06-12']
    assert [e['event_date'] for e in storms.events(min_size=1.5)] == \
        ['2026-07-04', '2026-06-12']


def test_history_at_answers_for_the_roof_not_the_neighbourhood():
    """This is what replaces 'Hail by Address', which today scans five years of
    daily NOAA CSVs, can take a minute, and still only reports that somebody
    called in hail a few miles away."""
    storms.record('2026-05-01', _swath((*FOCO, 1.10)))
    storms.record('2026-06-12', _swath((*FOCO, 2.00), (*LOVELAND, 1.75)))
    storms.record('2026-07-04', _swath((*LOVELAND, 1.25)))

    hist = storms.history_at(*FOCO)
    assert [h['event_date'] for h in hist] == ['2026-06-12', '2026-05-01']
    assert hist[0]['size_in'] == pytest.approx(2.00)

    assert [h['event_date'] for h in storms.history_at(*LOVELAND)] == \
        ['2026-07-04', '2026-06-12']


def test_history_at_a_place_that_was_never_hit_is_empty_not_an_error():
    storms.record('2026-06-12', _swath((*FOCO, 2.00)))
    assert storms.history_at(39.7392, -104.9903) == []   # Denver


def test_history_can_be_windowed_to_the_claim_period():
    """Colorado gives a homeowner one year from date of loss to notify the
    carrier (CRS 10-4-110.8). Older storms are still worth knowing — they are
    retail sales evidence — but they are not claims, and the tool has to be
    able to tell them apart."""
    storms.record('2024-06-12', _swath((*FOCO, 2.00)))
    storms.record('2026-06-12', _swath((*FOCO, 1.50)))

    recent = storms.history_at(*FOCO, since='2025-09-06')
    assert [h['event_date'] for h in recent] == ['2026-06-12']


def test_load_swath_of_an_unknown_event_is_none():
    assert storms.load_swath('mrms_mesh:1999-01-01') is None


def test_event_ids_are_derived_so_two_pulls_are_one_event():
    assert storms.event_id('2026-06-12') == storms.event_id('2026-06-12')
    assert storms.event_id('2026-06-12') != storms.event_id('2026-06-13')


def test_history_can_span_every_source():
    """A radar estimate over this cell and a spotter's phone call from down the
    road are different claims about the same roof. Both are worth holding; what
    must never happen is flattening them into one number, so every row carries
    the source that produced it."""
    lat, lng = FOCO
    storms.record('2026-06-12', _swath((lat, lng, 1.75)), source='mrms_mesh')
    storms.record('2026-06-12', _swath((lat, lng, 1.00)), source='spc_reports')

    assert [h['source'] for h in storms.history_at(lat, lng)] == ['mrms_mesh']
    both = storms.history_at(lat, lng, source=None)
    assert sorted(h['source'] for h in both) == ['mrms_mesh', 'spc_reports']
