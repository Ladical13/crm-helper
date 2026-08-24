"""What the training log must never get wrong.

Every test here is one of the ways a workout tracker quietly lies: a set that
did not get stored, volume that counts warm-ups, a PR that never happened, a
streak that resets because the clock is in UTC. None of it is visible on screen
— the number just looks plausible — which is the whole reason to pin it.
"""
from conftest import finish, log, start


# ── Logging a session ────────────────────────────────────────────────────────

def test_a_session_records_what_was_lifted(client, ex_id):
    w = start(client)
    log(client, w['id'], ex_id, 185, 5)
    log(client, w['id'], ex_id, 185, 5)
    log(client, w['id'], ex_id, 205, 3)

    detail = client.get(f"/api/workouts/{w['id']}").get_json()
    assert detail['sets'] == 3
    assert detail['exercises'] == 1
    assert detail['volume'] == 185 * 5 + 185 * 5 + 205 * 3
    assert [s['reps'] for s in detail['exercise_list'][0]['sets']] == [5, 5, 3]


def test_only_one_workout_can_be_open_at_a_time(client, ex_id):
    """A second 'start' returns the session already in progress.

    Two open workouts would split one gym visit across two records, and every
    total the app computes would then be wrong in both directions at once.
    """
    first = start(client)
    log(client, first['id'], ex_id, 135, 8)
    second = start(client)
    assert second['id'] == first['id']
    assert second['sets'] == 1


def test_active_returns_the_session_in_progress_and_nothing_after_finishing(client, ex_id):
    w = start(client)
    log(client, w['id'], ex_id, 135, 5)
    assert client.get('/api/workouts/active').get_json()['id'] == w['id']
    finish(client, w['id'])
    assert client.get('/api/workouts/active').get_json() is None


def test_warmups_are_logged_but_never_counted(client, ex_id):
    """Warm-ups exist so the next session can repeat the ramp. Counting them
    makes volume a measure of how much warming up happened."""
    w = start(client)
    log(client, w['id'], ex_id, 45, 10, set_type='warmup')
    log(client, w['id'], ex_id, 135, 5, set_type='warmup')
    log(client, w['id'], ex_id, 225, 5)

    detail = client.get(f"/api/workouts/{w['id']}").get_json()
    assert detail['volume'] == 225 * 5
    assert detail['sets'] == 1
    # ...but all three are still there to read.
    assert len(detail['exercise_list'][0]['sets']) == 3


def test_finishing_drops_the_skeleton_sets_a_routine_laid_down(client, ex_id):
    """An empty set is a movement that was planned and not worked. History must
    show the session that happened, not the one intended."""
    w = start(client)
    client.post(f"/api/workouts/{w['id']}/sets",
                json={'exercise_id': ex_id, 'weight': 0, 'reps': 0})
    log(client, w['id'], ex_id, 315, 1)

    done = finish(client, w['id'])
    assert len(done['exercise_list'][0]['sets']) == 1
    assert done['duration_min'] >= 0


def test_sets_can_be_corrected_and_removed(client, ex_id):
    w = start(client)
    s = log(client, w['id'], ex_id, 185, 5)
    fixed = client.patch(f"/api/sets/{s['id']}", json={'reps': 4}).get_json()
    assert fixed['reps'] == 4
    assert fixed['volume'] == 185 * 4
    client.delete(f"/api/sets/{s['id']}")
    assert client.get(f"/api/workouts/{w['id']}").get_json()['sets'] == 0


def test_a_set_needs_a_real_exercise(client):
    w = start(client)
    r = client.post(f"/api/workouts/{w['id']}/sets",
                    json={'exercise_id': 999999, 'weight': 100, 'reps': 5})
    assert r.status_code == 400


def test_junk_in_a_number_field_lands_on_zero_rather_than_a_500(client, ex_id):
    """These come off a numeric keypad on a phone with chalk on it."""
    w = start(client)
    s = log(client, w['id'], ex_id, 'e', '')
    assert s['weight'] == 0 and s['reps'] == 0
    assert client.get(f"/api/workouts/{w['id']}").get_json()['volume'] == 0


def test_scrapping_a_workout_takes_its_sets_with_it(client, ex_id):
    w = start(client)
    log(client, w['id'], ex_id, 185, 5)
    client.delete(f"/api/workouts/{w['id']}")
    assert client.get(f"/api/workouts/{w['id']}").status_code == 404
    with client.application.test_request_context():
        pass
    import app as appmod
    with appmod.get_db() as db:
        left = db.execute('SELECT COUNT(*) c FROM sets WHERE workout_id=?',
                          (w['id'],)).fetchone()['c']
    assert left == 0, 'deleting a workout left orphaned sets behind'


# ── What did I do last time ──────────────────────────────────────────────────

def test_last_time_skips_the_session_in_progress(client, ex_id):
    """The number under the bar has to be LAST week's, not the empty set just
    created — which is what `before` exists for."""
    old = start(client, local_date='2026-08-17')
    log(client, old['id'], ex_id, 225, 5)
    finish(client, old['id'])

    today = start(client, local_date='2026-08-24')
    client.post(f"/api/workouts/{today['id']}/sets",
                json={'exercise_id': ex_id, 'weight': 0, 'reps': 0})

    last = client.get(f"/api/exercises/{ex_id}/last?before={today['id']}").get_json()
    assert last['local_date'] == '2026-08-17'
    assert [s['weight'] for s in last['sets']] == [225]


def test_last_time_is_none_for_a_movement_never_worked(client, ex_id):
    assert client.get(f'/api/exercises/{ex_id}/last').get_json() is None


def test_history_groups_by_session_newest_first(client, ex_id):
    for date, weight in (('2026-08-10', 205), ('2026-08-17', 215), ('2026-08-24', 225)):
        w = start(client, local_date=date)
        log(client, w['id'], ex_id, weight, 5)
        finish(client, w['id'])
    hist = client.get(f'/api/exercises/{ex_id}/history').get_json()
    assert [h['local_date'] for h in hist] == ['2026-08-24', '2026-08-17', '2026-08-10']
    assert hist[0]['volume'] == 225 * 5
    assert hist[0]['top_weight'] == 225


# ── Records ──────────────────────────────────────────────────────────────────

def test_e1rm_treats_a_single_as_its_own_max(app):
    """Epley inflates a true 1RM by 3.3%, which would make every heavy single
    read as a PR the moment it is logged."""
    assert app.e1rm(315, 1) == 315
    assert app.e1rm(225, 5) == round(225 * (1 + 5 / 30.0), 1)
    assert app.e1rm(225, 0) == 0
    assert app.e1rm(0, 5) == 0


def test_records_pick_the_best_of_each_kind(client, ex_id):
    w = start(client, local_date='2026-08-10')
    log(client, w['id'], ex_id, 315, 1)      # heaviest
    log(client, w['id'], ex_id, 275, 5)      # best e1RM (320.8) and best volume
    finish(client, w['id'])

    rec = client.get('/api/records').get_json()[0]
    assert rec['top_weight'] == 315
    assert rec['top_weight_reps'] == 1
    assert rec['best_e1rm'] == 320.8
    assert rec['best_set_volume'] == 275 * 5


def test_warmups_never_set_a_record(client, ex_id):
    w = start(client)
    log(client, w['id'], ex_id, 405, 5, set_type='warmup')   # mistyped ramp set
    log(client, w['id'], ex_id, 225, 5)
    finish(client, w['id'])
    rec = client.get('/api/records').get_json()[0]
    assert rec['top_weight'] == 225


def test_a_deleted_set_takes_its_record_with_it(client, ex_id):
    """Records are computed on read for exactly this reason: a stored PR
    survives the correction that disproves it."""
    w = start(client)
    log(client, w['id'], ex_id, 225, 5)
    fat_finger = log(client, w['id'], ex_id, 2255, 5)
    assert client.get('/api/records').get_json()[0]['top_weight'] == 2255
    client.delete(f"/api/sets/{fat_finger['id']}")
    assert client.get('/api/records').get_json()[0]['top_weight'] == 225


# ── Stats and streaks ────────────────────────────────────────────────────────

def test_the_week_is_the_lifters_week_not_utcs(client, ex_id):
    """A 7pm Sunday session in Colorado is Monday in UTC. Deriving the date
    from started_at would move half the evening workouts into next week."""
    w = start(client, local_date='2026-08-23')          # a Sunday
    log(client, w['id'], ex_id, 185, 5)
    finish(client, w['id'])
    # Asked on the Monday after: that Sunday belongs to the PREVIOUS week.
    stats = client.get('/api/stats?today=2026-08-24').get_json()
    assert stats['this_week']['workouts'] == 0
    assert stats['week_streak'] == 1, 'last week counted, so the streak stands'


def test_the_streak_counts_weeks_and_survives_a_rest_day(client, ex_id):
    """Weekly, not daily, on purpose: a daily streak makes a rest day look like
    a failure, which is a tracker arguing with the training plan."""
    for date in ('2026-08-10', '2026-08-19', '2026-08-24'):   # three straight weeks
        w = start(client, local_date=date)
        log(client, w['id'], ex_id, 185, 5)
        finish(client, w['id'])
    stats = client.get('/api/stats?today=2026-08-24').get_json()
    assert stats['week_streak'] == 3
    assert stats['this_week']['workouts'] == 1
    assert stats['total_workouts'] == 3


def test_an_empty_monday_does_not_read_as_a_broken_streak(client, ex_id):
    w = start(client, local_date='2026-08-19')
    log(client, w['id'], ex_id, 185, 5)
    finish(client, w['id'])
    # Nothing logged yet in the week of the 24th; last week still counts.
    assert client.get('/api/stats?today=2026-08-24').get_json()['week_streak'] == 1


def test_an_unfinished_session_is_not_counted_yet(client, ex_id):
    w = start(client, local_date='2026-08-24')
    log(client, w['id'], ex_id, 185, 5)
    stats = client.get('/api/stats?today=2026-08-24').get_json()
    assert stats['total_workouts'] == 0
    assert stats['this_week']['workouts'] == 0


# ── Exercises ────────────────────────────────────────────────────────────────

def test_the_library_is_seeded_and_searchable(client):
    assert len(client.get('/api/exercises').get_json()) >= 40
    names = [e['name'] for e in client.get('/api/exercises?q=press').get_json()]
    assert 'Bench Press' in names
    assert 'Back Squat' not in names


def test_search_treats_wildcards_literally(client):
    """A typed % must not match everything — same escape the CRM's pipeline
    search needed."""
    assert client.get('/api/exercises?q=%25').get_json() == []


def test_a_movement_cannot_exist_twice_under_two_spellings(client):
    """Two spellings split one lift's history in half, which breaks the only
    number this app exists to show."""
    first = client.post('/api/exercises', json={'name': 'Zercher Squat'}).get_json()
    again = client.post('/api/exercises', json={'name': 'zercher squat'})
    assert again.status_code == 200
    assert again.get_json()['id'] == first['id']


def test_archiving_a_movement_keeps_its_history(client, ex_id):
    w = start(client)
    log(client, w['id'], ex_id, 185, 5)
    finish(client, w['id'])
    client.delete(f'/api/exercises/{ex_id}')
    assert ex_id not in [e['id'] for e in client.get('/api/exercises').get_json()]
    # The sets are still there, and still count.
    assert client.get(f'/api/exercises/{ex_id}/history').get_json()[0]['volume'] == 925


def test_re_adding_an_archived_movement_brings_its_history_back(client, ex_id):
    client.delete(f'/api/exercises/{ex_id}')
    again = client.post('/api/exercises', json={'name': 'Back Squat'}).get_json()
    assert again['id'] == ex_id
    assert ex_id in [e['id'] for e in client.get('/api/exercises').get_json()]


# ── Routines ─────────────────────────────────────────────────────────────────

def test_a_routine_can_be_saved_from_the_workout_you_just_did(client, ex_id):
    bench = client.get('/api/exercises?q=Bench Press').get_json()[0]['id']
    w = start(client)
    log(client, w['id'], ex_id, 225, 5)
    log(client, w['id'], bench, 185, 5)
    finish(client, w['id'])

    saved = client.post('/api/routines', json={'name': 'Lower A',
                                               'from_workout_id': w['id']}).get_json()
    assert saved['exercise_ids'] == [ex_id, bench]
    listed = client.get('/api/routines').get_json()
    assert [i['exercise_id'] for i in listed[0]['items']] == [ex_id, bench]


def test_starting_from_a_routine_seeds_the_movements_but_no_numbers(client, ex_id):
    """Pre-filled targets would put weights on screen that were never lifted.
    A log must never show a set you did not do."""
    routine = client.post('/api/routines', json={'name': 'Squat day',
                                                 'exercise_ids': [ex_id]}).get_json()
    w = start(client, routine_id=routine['id'])
    assert w['name'] == 'Squat day'
    assert w['exercise_list'][0]['exercise_id'] == ex_id
    assert w['volume'] == 0
    assert w['sets'] == 0, 'a seeded skeleton set must not count as work'


def test_a_routine_needs_at_least_one_movement(client):
    assert client.post('/api/routines', json={'name': 'Empty'}).status_code == 400
    assert client.post('/api/routines', json={'exercise_ids': [1]}).status_code == 400


# ── Settings ─────────────────────────────────────────────────────────────────

def test_the_unit_is_a_label_and_never_rewrites_what_was_lifted(client, ex_id):
    w = start(client)
    log(client, w['id'], ex_id, 225, 5)
    assert client.patch('/api/settings', json={'unit': 'kg'}).get_json()['unit'] == 'kg'
    assert client.get(f"/api/workouts/{w['id']}").get_json()['volume'] == 225 * 5
    assert client.patch('/api/settings', json={'unit': 'stone'}).status_code == 400


def test_the_weekly_series_shows_the_weeks_that_were_missed(client, ex_id):
    """A chart built only from weeks that have a workout draws two sessions
    three weeks apart as back-to-back bars — a continuous block of training
    that did not happen."""
    for date in ('2026-08-03', '2026-08-24'):
        w = start(client, local_date=date)
        log(client, w['id'], ex_id, 185, 5)
        finish(client, w['id'])
    weeks = client.get('/api/stats?today=2026-08-24').get_json()['weeks']
    assert [w['week'] for w in weeks] == ['2026-08-03', '2026-08-10',
                                          '2026-08-17', '2026-08-24']
    assert [w['workouts'] for w in weeks] == [1, 0, 0, 1]
    assert client.get('/api/stats?today=2026-08-24').get_json()['week_streak'] == 1


def test_the_series_always_reaches_the_current_week(client, ex_id):
    """An empty current week must appear as an empty bar, not fall off the end
    of the chart — otherwise a week with nothing in it looks like it has not
    started yet."""
    w = start(client, local_date='2026-08-17')
    log(client, w['id'], ex_id, 185, 5)
    finish(client, w['id'])
    weeks = client.get('/api/stats?today=2026-08-24').get_json()['weeks']
    assert weeks[-1] == {'week': '2026-08-24', 'workouts': 0, 'volume': 0.0}
