"""An appointment set at a door knows when it is, and books itself.

Two gaps closed together, because separately neither is worth much. An
`appointment` pin mapped to the `appt_set` stage carrying no date at all, so
the pipeline said an appointment existed and nothing in the system knew when —
nothing could remind anyone, and a door-set no-show is the standard way a
canvassing day evaporates. And reaching the Pipeline at all was a SECOND button
the rep had to remember, on a panel they had already walked away from, which
only appeared if a name happened to have been typed.
"""
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

APP_JS = open(os.path.join(HERE, '..', 'static', 'app.js'), encoding='utf-8').read()
INDEX  = open(os.path.join(HERE, '..', 'static', 'index.html'), encoding='utf-8').read()


def _code(src):
    """The source with `//` line comments removed.

    A test that forbids a construct must not be satisfied — or broken — by the
    comment explaining why the construct is forbidden. Both of these functions
    name `toISOString` and `new Date(v)` in a warning directly above the line
    that avoids them, which is exactly the prose a reader needs and exactly
    what a naive substring check trips over.
    """
    return '\n'.join(re.sub(r'(^|\s)//.*$', '', line)
                     for line in src.splitlines())


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CANVASSER_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('PORTAL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SESSION_SECRET', 'test-secret')
    for mod in [m for m in list(sys.modules) if m in ('app', 'p1_canvass_app')]:
        del sys.modules[mod]
    import app as canvasser_app
    canvasser_app.app.config['TESTING'] = True
    from portal import users as pusers
    for username in ('aaron', 'bryan'):
        if not pusers.get(username):
            pusers.create(username, password='test-only', role='rep')
    with canvasser_app.app.test_client() as c:
        with c.session_transaction() as sess:
            sess['username'] = 'aaron'
            sess['is_admin'] = False
        yield c, canvasser_app


def _appt(**kw):
    body = {'lat': 40.585, 'lng': -105.084, 'pin_type': 'appointment',
            'address': '1420 Oak St', 'contact_name': 'Jennifer',
            'appointment_at': '2026-09-24T18:00'}
    body.update(kw)
    return body


# ── The time itself ─────────────────────────────────────────────────────────

def test_an_appointment_pin_stores_its_time(client):
    c, _ = client
    assert c.post('/api/pins', json=_appt()).get_json()['appointment_at'] == '2026-09-24T18:00'


def test_the_time_is_stored_as_LOCAL_wall_clock(client):
    """"Thursday at six" is six o'clock in that driveway.

    Converting to UTC on the way in is how a 6pm appointment becomes a Friday
    for the six hours a day Colorado is behind UTC — the exact trap
    `_company_today` exists to handle on the estimator side. The conversion to
    UTC happens once, in the browser, when the CRM task is written, because the
    browser is the only party that knows the rep's offset.
    """
    c, _ = client
    stored = c.post('/api/pins', json=_appt()).get_json()['appointment_at']
    assert not stored.endswith('Z')
    assert '+' not in stored
    assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d', stored)


def test_a_mistyped_time_costs_the_rep_the_time_not_the_door(client):
    """A pin save must never fail on a bad time. The door was knocked; the
    record of it is worth more than the field that was fumbled."""
    c, _ = client
    r = c.post('/api/pins', json=_appt(appointment_at='next tuesday-ish'))
    assert r.status_code == 201
    assert r.get_json()['appointment_at'] == ''


def test_a_pin_that_is_not_an_appointment_carries_no_time(client):
    """Otherwise a Not Home pin can hold an appointment nothing will show,
    and the two disagree about whether one exists."""
    c, _ = client
    r = c.post('/api/pins', json=_appt(pin_type='come_back'))
    assert r.get_json()['appointment_at'] == ''


def test_an_appointment_can_be_rescheduled(client):
    c, _ = client
    pin = c.post('/api/pins', json=_appt()).get_json()
    r = c.put(f"/api/pins/{pin['id']}", json={'appointment_at': '2026-09-25T09:30'})
    assert r.get_json()['appointment_at'] == '2026-09-25T09:30'


def test_a_reschedule_is_cleaned_the_same_way_a_create_is(client):
    """Two paths that disagree about what a time is would store two shapes."""
    c, _ = client
    pin = c.post('/api/pins', json=_appt()).get_json()
    r = c.put(f"/api/pins/{pin['id']}", json={'appointment_at': 'whenever'})
    assert r.get_json()['appointment_at'] == ''


def test_a_pin_from_before_this_existed_still_loads(client):
    c, mod = client
    pin = c.post('/api/pins', json=_appt(appointment_at='')).get_json()
    assert pin['appointment_at'] == ''
    assert c.get(f"/api/pins/{pin['id']}").status_code == 200


# ── The handoff ─────────────────────────────────────────────────────────────

def test_an_appointment_pin_requires_a_name():
    """The one required field in the app, and only on this one pin type.

    An appointment with no name cannot become a lead — no cadence, no task, no
    reminder, no leaderboard credit. The rep did the hardest work of the day
    and the system recorded a coloured dot.
    """
    save = APP_JS[APP_JS.index("$('save-pin-btn')"):]
    save = save[:save.index('async function getGPS')]
    assert re.search(r"selectedPinType === 'appointment' && !payload\.contact_name", save)
    assert 'return;' in save


def test_saving_hands_off_without_a_second_button():
    save = APP_JS[APP_JS.index("$('save-pin-btn')"):]
    save = save[:save.index('// ── Offline outbox')]
    assert 'autoHandoff(pin)' in save


def test_a_pin_that_synced_from_the_outbox_still_reaches_the_pipeline():
    """There was no network when the rep tapped Save, and the CRM — not this
    app — owns what a lead is, so the lead cannot be queued beside the pin."""
    flush = APP_JS[APP_JS.index('async function flushOutbox'):]
    flush = flush[:flush.index('function replacePendingPin')]
    assert 'autoHandoff' in flush


def test_the_handoff_cannot_write_two_leads_for_one_door():
    """`handoffToPipeline` records the lead id back onto the pin, and that is
    what every retry path checks before running again."""
    guard = APP_JS[APP_JS.index('function canHandoff'):]
    guard = guard[:guard.index('async function autoHandoff')]
    assert '!pin.crm_lead_id' in guard
    assert 'pin.pending' in guard, 'a queued pin has no server id to attach a lead to'


def test_a_failed_handoff_never_costs_the_pin():
    auto = APP_JS[APP_JS.index('async function autoHandoff'):]
    auto = auto[:auto.index('async function addToPipeline')]
    assert 'catch' in auto
    assert 'Add to Pipeline' in auto, 'the rep needs to be told the manual route still exists'


def test_the_appointment_becomes_a_task_the_rep_will_see():
    hand = APP_JS[APP_JS.index('async function handoffToPipeline'):]
    hand = hand[:hand.index('function canHandoff')]
    assert "/handoff" in hand
    assert "crmPost" not in hand, "the server must commit the entire handoff atomically"


def test_the_task_due_date_matches_the_crms_own_spelling():
    """'...:00.000Z' sorts before '...:00Z' as text, so a task stored with
    milliseconds would read as due fractionally before every other task."""
    fn = APP_JS[APP_JS.index('function apptToUtc'):]
    fn = fn[:fn.index('async function crmPost')]
    assert 'slice(0, 19)' in fn and "+ 'Z'" in fn


def test_one_handoff_builder_serves_the_button_and_the_automatic_path():
    """Two builders would drift into producing different leads from one door."""
    manual = APP_JS[APP_JS.index('async function addToPipeline'):]
    manual = manual[:manual.index('// ── Edit pin')]
    assert 'handoffToPipeline' in manual
    assert 'crmPost(\'/api/leads\'' not in manual, 'the manual button rebuilt the lead itself'


def test_the_local_time_is_never_parsed_with_bare_new_Date():
    """Safari and Chrome disagree about whether a bare 'YYYY-MM-DDTHH:MM' is
    local or UTC, and the wrong branch moves the appointment by the offset."""
    for fn_name, stop in (('function apptToUtc', 'async function crmPost'),
                          ('function prettyAppt', '// ── Map')):
        body = APP_JS[APP_JS.index(fn_name):]
        end = body.index(stop) if stop in body else 600
        body = _code(body[:end])
        assert 'new Date(v)' not in body and 'new Date(local)' not in body


def test_the_field_is_only_offered_on_an_appointment():
    assert 'id="appointment-fields"' in INDEX
    assert 'id="pin-appointment-at"' in INDEX
    vis = APP_JS[APP_JS.index('function updateContactFieldsVisibility'):]
    vis = vis[:vis.index('function defaultApptLocal')]
    assert "selectedPinType === 'appointment'" in vis


def test_the_default_time_is_built_from_local_parts_not_an_iso_string():
    """toISOString() would hand a Colorado rep a time six hours off their own
    field, in a control that only speaks local wall clock."""
    fn = _code(APP_JS[APP_JS.index('function defaultApptLocal'):
                      APP_JS.index('function prettyAppt')])
    assert 'toISOString' not in fn
    assert 'getFullYear' in fn
