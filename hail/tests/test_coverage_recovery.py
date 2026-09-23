import datetime as dt
from unittest.mock import patch
from hail import backfill, storms, ingest, grid


def test_missing_day_is_not_recorded_and_failed_refetch_preserves_good_data():
    day = dt.date(2026, 6, 12)
    with patch.object(ingest, '_get', return_value=(404, b'')):
        assert backfill.run([day])['failures'] == 1
    assert not storms.ingested_dates()
    storms.record(day.isoformat(), grid.swath_from_points([(40.5, -105, 2)]))
    with patch.object(ingest, '_get', return_value=(404, b'')):
        assert backfill.run([day])['failures'] == 1
    assert storms.history_at(40.5, -105)[0]['size_in'] == 2


def test_legacy_day_does_not_prove_coverage_and_texas_is_outside():
    storms.record('2026-06-12', grid.Swath())
    cov = storms.coverage('2026-06-12', '2026-06-12', 40.5, -105)
    assert cov['days_verified'] == 0 and cov['unverified_days'] == 1
    assert storms.coverage('2026-06-12', '2026-06-12', 32.3, -95.3)['outside_area']
    assert not storms.verified_dates()


def test_coverage_distinguishes_valid_zero_and_no_radar_cell():
    swath = grid.Swath()
    swath.coverage = dict(bounds=list(ingest.COLORADO), valid_runs=[[4050, -10500, -10500]])
    storms.record('2026-06-12', swath)
    valid = storms.coverage('2026-06-12', '2026-06-12', 40.505, -104.995)
    unknown = storms.coverage('2026-06-12', '2026-06-12', 40.505, -104.985)
    assert valid['status'] == 'complete' and valid['threshold_in'] == 1
    assert unknown['days_verified'] == 0 and unknown['status'] == 'partial'


def test_backfill_can_restart_after_completion_and_expired_worker():
    assert storms.backfill_claim('first', 'manager', 3)
    assert not storms.backfill_claim('overlap', 'manager', 3)
    storms.backfill_finish('done')
    assert storms.backfill_claim('second season', 'manager', 3)
    with storms.get_db() as db:
        db.execute("UPDATE backfill_job SET heartbeat_at='2000-01-01T00:00:00Z'")
    assert storms.backfill_state()['job']['status'] == 'interrupted'
    assert storms.backfill_claim('resume', 'manager', 3)
