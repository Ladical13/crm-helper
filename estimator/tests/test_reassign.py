"""Reassigning an estimate to another rep.

Ownership is visibility here: a rep sees only their own estimates, so the
salesperson field decides whose dashboard a job lives on. It used to change
only as a side effect of a whole-estimate save, which meant anyone could move
it and a stale tab autosaving could quietly hand a reassigned job straight back.
"""
import os

import pytest

import app as A
import demo_store
from portal import users as portal_users

REP = 'casey'
OTHER = 'jacob'
for _u in (REP, OTHER):
    if not portal_users.get(_u):
        portal_users.create(_u, password='test-only-password', role='rep',
                            full_name=_u.title())


@pytest.fixture(autouse=True)
def clean_slate():
    for eid in list(A.est_ids()):
        A.est_delete(eid)
    yield
    for eid in list(A.est_ids()):
        A.est_delete(eid)


def _as(app, name):
    c = app.test_client()
    with c.session_transaction() as s:
        s['user'] = name
    return c


def _seed(eid, salesperson='luke'):
    doc = {'estimate_id': eid, 'salesperson': salesperson, 'estimate_type': 'retail',
           'customer': {'name': 'Reassign Test', 'address': {'city': 'Loveland'}}}
    A.est_save(doc)
    return doc


def _ids(c):
    return {e['estimate_id'] for e in c.get('/api/estimates').get_json()}


def _reassign(c, eid, rep):
    return c.patch(f'/api/estimates/{eid}/salesperson', json={'salesperson': rep})


def test_a_manager_moves_an_estimate_between_dashboards(app, client):
    _seed('r-move', OTHER)
    assert 'r-move' in _ids(_as(app, OTHER))

    r = _reassign(client, 'r-move', REP)
    assert r.status_code == 200
    assert A.est_load('r-move')['salesperson'] == REP
    assert 'r-move' not in _ids(_as(app, OTHER))
    assert 'r-move' in _ids(_as(app, REP))

    hist = A.est_load('r-move')['assignment_history']
    assert hist[-1]['from'] == OTHER and hist[-1]['to'] == REP and hist[-1]['by'] == 'luke'


def test_a_rep_cannot_hand_off_or_take_an_estimate(app):
    _seed('r-mine', REP)
    _seed('r-theirs', OTHER)
    rep = _as(app, REP)
    assert _reassign(rep, 'r-mine', OTHER).status_code == 403
    assert _reassign(rep, 'r-theirs', REP).status_code == 403
    assert A.est_load('r-mine')['salesperson'] == REP
    assert A.est_load('r-theirs')['salesperson'] == OTHER


def test_a_rep_may_claim_an_unassigned_estimate_only_for_themselves(app):
    _seed('r-open', '')
    rep = _as(app, REP)
    assert _reassign(rep, 'r-open', OTHER).status_code == 403
    assert _reassign(rep, 'r-open', REP).status_code == 200
    assert A.est_load('r-open')['salesperson'] == REP


def test_an_unreadable_owner_is_not_claimable(app):
    """Fails closed, the same as the list does."""
    A.est_save({'estimate_id': 'r-weird', 'salesperson': 12345,
                'customer': {'name': 'Mystery'}})
    assert _reassign(_as(app, REP), 'r-weird', REP).status_code == 403


def test_a_name_off_the_roster_is_refused(client):
    _seed('r-typo')
    r = _reassign(client, 'r-typo', 'nobody.here')
    assert r.status_code == 400
    assert A.est_load('r-typo')['salesperson'] == 'luke'


def test_a_stale_save_cannot_undo_a_reassignment(client):
    """The trap this closes: a tab opened before the reassignment autosaves."""
    _seed('r-stale')
    assert _reassign(client, 'r-stale', REP).status_code == 200
    stale = {'estimate_id': 'r-stale', 'salesperson': 'luke', 'estimate_type': 'retail',
             'customer': {'name': 'Reassign Test'}, 'assignment_history': []}
    assert client.put('/api/estimates/r-stale', json=stale).status_code == 200
    saved = A.est_load('r-stale')
    assert saved['salesperson'] == REP
    assert saved['assignment_history'], 'a stale save wiped the reassignment record'


def test_a_save_still_assigns_an_unassigned_estimate(client):
    _seed('r-claim', '')
    doc = {'estimate_id': 'r-claim', 'salesperson': REP, 'customer': {'name': 'X'}}
    assert client.put('/api/estimates/r-claim', json=doc).status_code == 200
    assert A.est_load('r-claim')['salesperson'] == REP


def test_reassigning_moves_the_funnel_rep_without_moving_its_state(client):
    A.pfunnel.record('r-funnel', 'sent', rep='luke')
    _seed('r-funnel')
    _seed('r-nofunnel')
    assert _reassign(client, 'r-funnel', REP).status_code == 200
    assert _reassign(client, 'r-nofunnel', REP).status_code == 200
    row = A.pfunnel.get('r-funnel')
    assert row['rep'] == REP and row['state'] == 'sent'
    assert A.pfunnel.get('r-nofunnel') is None, 'reassignment invented a funnel row'


def test_the_roster_is_readable_by_a_rep(app):
    r = _as(app, REP).get('/api/team')
    assert r.status_code == 200
    names = {m['username'] for m in r.get_json()}
    assert {REP, OTHER, 'luke'} <= names


def test_the_demo_cannot_reach_reassignment_or_the_roster():
    assert 'reassign_estimate' not in demo_store.ALLOWED_ENDPOINTS
    assert 'team_roster' not in demo_store.ALLOWED_ENDPOINTS


def test_the_sidebar_picker_reassigns_through_the_patch():
    """Not through bind(), which only marks the doc dirty for a whole save —
    and a whole save no longer moves ownership, so it would silently do nothing."""
    with open(os.path.join(os.path.dirname(A.__file__), 'static', 'app.js'),
              encoding='utf-8') as f:
        js = f.read()
    assert "bind('salesperson'" not in js
    assert '/salesperson`' in js
