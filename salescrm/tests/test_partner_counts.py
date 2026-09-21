"""Partner counts, published for Nimbus.

Nimbus scores a networking event on whether the room is full of the segment
the pipeline is THIN on — the fortieth realtor contact is not worth an evening
and the second insurance agent is. It reaches the CRM through this API and
never through `salescrm.db`, which is the boundary `agents/__init__.py` states,
so the number it scores on has to be one this app publishes on purpose.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import app as A  # noqa: E402

from conftest import signup  # noqa: E402


@pytest.fixture(autouse=True)
def _signed_in(client):
    """Manager scope, which is what Nimbus forwards: the gap it ranks on is
    the whole company's pipeline, not one rep's slice of it."""
    signup(client)
    return client


def _lead(client, **kw):
    body = {'first_name': 'A', 'last_name': 'Partner', 'lead_type': 'realtor'}
    body.update(kw)
    r = client.post('/api/leads', json=body)
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()


def test_every_partner_type_is_reported_even_at_zero(client):
    """A type the caller cannot see is a type it would have to guess about,
    and a guess of zero invents a gap that may not exist."""
    counts = client.get('/api/partners/counts').get_json()['partner_counts']
    assert set(counts) == set(A.PARTNER_TYPES)
    assert all(v['active'] == 0 and v['total'] == 0 for v in counts.values())


def test_homeowners_are_not_partners(client):
    """An event full of homeowners is not a networking opportunity, and
    counting them would drown every real segment."""
    assert 'homeowner' not in client.get('/api/partners/counts').get_json()['partner_counts']


def test_counts_split_active_from_total(client):
    """A won realtor is a relationship that already paid off; an open one is
    still work. The gap Nimbus scores on is about live relationships."""
    _lead(client, lead_type='realtor')
    b = _lead(client, lead_type='realtor')
    # Set directly: this is a test of what the endpoint counts, not of the
    # stage machinery that gets a lead to won.
    with A.get_db() as db:
        db.execute("UPDATE leads SET stage='won' WHERE id=?", (b['id'],))

    counts = client.get('/api/partners/counts').get_json()['partner_counts']
    assert counts['realtor']['total'] == 2
    assert counts['realtor']['active'] == 1


def test_a_thin_segment_reads_as_thin(client):
    for _ in range(3):
        _lead(client, lead_type='realtor')
    _lead(client, lead_type='insurance_agent')
    counts = client.get('/api/partners/counts').get_json()['partner_counts']
    assert counts['realtor']['active'] == 3
    assert counts['insurance_agent']['active'] == 1


def test_a_dnc_partner_is_not_counted(client):
    """Suppression beats everything here as everywhere else: a partner we may
    not contact is not a partner the event ranking should bank on."""
    lead = _lead(client, lead_type='insurance_agent')
    with A.get_db() as db:
        db.execute('UPDATE leads SET dnc=1 WHERE id=?', (lead['id'],))
    counts = client.get('/api/partners/counts').get_json()['partner_counts']
    assert counts['insurance_agent']['total'] == 0


def test_every_type_carries_a_label(client):
    """Nimbus prints these back to a human; a bare key reads as a bug."""
    counts = client.get('/api/partners/counts').get_json()['partner_counts']
    assert all(v['label'] and v['label'] != k for k, v in counts.items())
