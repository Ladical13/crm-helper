"""A storm alert goes to the REP, never to the customer.

The obvious version mails the homeowner — "hail hit your street, book an
inspection". Deliberately not built. Northern Colorado gets a lot of qualifying
hail, and a list that hears from you on every swath stops being a list by the
third season; the people it burns first are the ones who already chose you.
A human decides who is worth a call. That is slower, and it is the point.
"""
import pytest
from conftest import signup, login, new_lead

import app as appmod
from hail import grid as hgrid, storms as hstorms
from portal import geo as pgeo, mail as pmail

FOCO = (40.5853, -105.0844)


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(appmod.pmail, 'configured', lambda: True)
    monkeypatch.setattr(appmod.pmail, 'send',
                        lambda subject, html, to, **kw: (
                            sent.append({'subject': subject, 'html': html, 'to': to}) or True))
    return sent


@pytest.fixture(autouse=True)
def storm_db(tmp_path, monkeypatch):
    monkeypatch.setenv('HAIL_DATA_DIR', str(tmp_path))
    hstorms.reset_cache()
    pgeo.reset_cache()
    yield
    hstorms.reset_cache()
    pgeo.reset_cache()


def _storm(*points, date='2026-06-12', source='mrms_mesh'):
    return hstorms.record(date, hgrid.swath_from_points(points, threshold_in=1.0),
                          source=source)['event_id']


def _customer(client, address, at=FOCO, stage='won', **kw):
    lead = new_lead(client, address=address, city='Fort Collins', state='CO', **kw)
    pgeo.put(lead['address'], lat=at[0], lng=at[1], matched=lead['address'],
             source='census', status='ok',
             key=pgeo.norm_address(lead['address'], lead['city'],
                                   lead['state'], lead['zip']))
    if stage:
        client.patch(f'/api/leads/{lead["id"]}/stage', json={'stage': stage})
    return lead


def test_the_rep_is_the_recipient_not_the_customer(client, outbox):
    signup(client)
    _customer(client, '12 Elm St', email='homeowner@example.com')
    _storm((*FOCO, 1.75))
    assert appmod._check_storm_alerts() == 1
    assert [m['to'] for m in outbox] == ['luke@projectoneroofing.com']
    assert 'homeowner@example.com' not in outbox[0]['to']


def test_a_rep_is_told_only_about_their_own_customers(client, outbox):
    signup(client, 'luke')                          # manager
    signup(client, 'casey')
    login(client, 'casey')
    _customer(client, '12 Elm St')
    login(client, 'luke')
    _storm((*FOCO, 1.75))
    appmod._check_storm_alerts()
    assert [m['to'] for m in outbox] == ['casey@projectoneroofing.com']


def test_the_alert_is_sent_once_per_storm(client, outbox):
    signup(client)
    _customer(client, '12 Elm St')
    _storm((*FOCO, 1.75))
    appmod._check_storm_alerts()
    assert appmod._check_storm_alerts() == 0
    assert len(outbox) == 1


def test_a_second_storm_alerts_again(client, outbox):
    signup(client)
    _customer(client, '12 Elm St')
    _storm((*FOCO, 1.75), date='2026-06-12')
    appmod._check_storm_alerts()
    _storm((*FOCO, 2.00), date='2026-07-04')
    assert appmod._check_storm_alerts() == 1


def test_plan_members_lead_the_list(client, outbox):
    """We owe them the call regardless of who took bigger hail."""
    signup(client)
    _customer(client, '1 Plan St', plan='roof_care', billing='annual')
    _customer(client, '2 Past St')
    _storm((*FOCO, 1.75))
    appmod._check_storm_alerts()
    html = outbox[0]['html']
    assert html.index('Roof Care Plan members') < html.index('Past customers')


def test_cold_imported_addresses_are_not_in_the_email(client, outbox):
    """A cold address is a canvassing lead, not somebody to phone. It belongs
    on the map, not in an inbox."""
    signup(client)
    cold = _customer(client, '4 Cold Ct', stage=None)
    with appmod.get_db() as db:
        db.execute("UPDATE leads SET import_batch='b1', last_activity_at='' WHERE id=?",
                   (cold['id'],))
    _storm((*FOCO, 1.75))
    assert appmod._check_storm_alerts() == 0
    assert outbox == []


def test_hail_below_the_threshold_does_not_alert(client, outbox):
    signup(client)
    _customer(client, '12 Elm St')
    _storm((*FOCO, 1.10))
    monkey = appmod.STORM_ALERT_MIN_IN
    appmod.STORM_ALERT_MIN_IN = 1.5
    try:
        assert appmod._check_storm_alerts() == 0
    finally:
        appmod.STORM_ALERT_MIN_IN = monkey


def test_the_email_says_where_the_number_came_from(client, outbox):
    """Radar over the roof and a spotter's phone call from down the road are
    different facts, and only one survives a customer asking how we know."""
    signup(client)
    _customer(client, '12 Elm St')
    _storm((*FOCO, 1.75), source='spc_reports')
    appmod._check_storm_alerts()
    assert 'nearby, not measured at the roof' in outbox[0]['html']


def test_radar_is_described_as_radar(client, outbox):
    signup(client)
    _customer(client, '12 Elm St')
    _storm((*FOCO, 1.75), source='mrms_mesh')
    appmod._check_storm_alerts()
    assert 'Radar-estimated' in outbox[0]['html']


def test_a_failed_send_is_retried_next_pass(client, monkeypatch):
    """A rep who never got the list must not be recorded as having had it."""
    signup(client)
    _customer(client, '12 Elm St')
    _storm((*FOCO, 1.75))
    monkeypatch.setattr(appmod.pmail, 'configured', lambda: True)
    monkeypatch.setattr(appmod.pmail, 'send', lambda *a, **k: False)
    assert appmod._check_storm_alerts() == 0
    with appmod.get_db() as db:
        assert db.execute('SELECT COUNT(*) c FROM storm_notices').fetchone()['c'] == 0


def test_nothing_is_sent_when_mail_is_unconfigured(client, monkeypatch):
    signup(client)
    _customer(client, '12 Elm St')
    _storm((*FOCO, 1.75))
    monkeypatch.setattr(appmod.pmail, 'configured', lambda: False)
    assert appmod._check_storm_alerts() == 0


def test_an_empty_archive_is_not_an_error(client, outbox):
    signup(client)
    assert appmod._check_storm_alerts() == 0
