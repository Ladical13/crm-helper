"""One loss vocabulary across both halves of the funnel.

The estimator owned a controlled seven-value list; the CRM took whatever prose a
browser prompt() returned. So the two could never be added together -- and the
CRM holds the bigger half of the answer, because most deals die at the door or
on the phone, before anyone builds an estimate at all.
"""
from conftest import signup, new_lead
from portal import lost_reasons as plost


def test_config_serves_the_reasons_rather_than_the_ui_restating_them(client):
    """Served, so the picker and the validator that accepts its value cannot
    drift apart -- the same rule the estimator's own picker follows."""
    signup(client)
    served = dict(client.get('/api/config').get_json()['lost_reasons'])
    assert served == plost.REASONS


def test_a_reason_outside_the_list_is_refused(client):
    signup(client)
    lead = new_lead(client)
    r = client.patch(f'/api/leads/{lead["id"]}/stage',
                     json={'stage': 'lost', 'lost_reason': 'they were mean to me'})
    assert r.status_code == 400


def test_a_loss_with_no_reason_yet_is_allowed(client):
    """Refusing it would leave a dead deal parked in an open stage, which is
    worse than an unattributed loss."""
    signup(client)
    lead = new_lead(client)
    r = client.patch(f'/api/leads/{lead["id"]}/stage', json={'stage': 'lost'})
    assert r.status_code == 200
    assert r.get_json()['lost_reason'] == ''


def test_a_valid_reason_sticks(client):
    signup(client)
    lead = new_lead(client)
    got = client.patch(f'/api/leads/{lead["id"]}/stage',
                       json={'stage': 'lost', 'lost_reason': 'competitor'}).get_json()
    assert got['lost_reason'] == 'competitor'


def test_reopening_a_lost_deal_clears_the_reason(client):
    """Plenty get re-quoted. A job that closes in March must not carry "went
    with someone else" into the month it was won."""
    signup(client)
    lead = new_lead(client)
    client.patch(f'/api/leads/{lead["id"]}/stage',
                 json={'stage': 'lost', 'lost_reason': 'competitor'})
    got = client.patch(f'/api/leads/{lead["id"]}/stage',
                       json={'stage': 'follow_up'}).get_json()
    assert got['lost_reason'] == ''


def test_the_put_path_validates_too(client):
    """Two doors onto one field is how they end up disagreeing."""
    signup(client)
    lead = new_lead(client)
    assert client.put(f'/api/leads/{lead["id"]}',
                      json={'lost_reason': 'nonsense'}).status_code == 400
