"""The Partners tab is a relationship book, not a dump of every record.

It returned every partner-type lead with no limit and no search — right for a
book of twenty realtors, and a way to hang a phone the moment prospecting
started importing HOAs and brokerages by the thousand, one DOM card per row.
The Pipeline board already learned this and moved its search to the server;
this tab was left rendering the whole table.
"""
from conftest import signup, login, new_lead
import app as appmod


def _book(client, **kw):
    qs = '&'.join(f'{k}={v}' for k, v in kw.items())
    return client.get('/api/partners' + (f'?{qs}' if qs else '')).get_json()


def _cold(client, n, **kw):
    """What a prospect import writes: never touched, no referrals."""
    now = appmod._now()
    with appmod.get_db() as db:
        db.executemany(
            "INSERT INTO leads (id, lead_type, service, stage, entry_stage, rep, "
            "created_at, updated_at, last_activity_at, import_batch, company) "
            "VALUES (?,'hoa','roofing','new','new','luke',?,?,'','batch1',?)",
            [(f'cold{i}', now, now, f'HOA {i}') for i in range(n)])


def test_a_touched_partner_is_in_the_book(client):
    signup(client)
    p = new_lead(client, lead_type='realtor', first_name='Sara')
    client.post(f'/api/leads/{p["id"]}/activities', json={'kind': 'call'})
    assert [x['id'] for x in _book(client)['partners']] == [p['id']]


def test_a_partner_who_has_referred_is_in_the_book(client):
    """A relationship, whether or not anyone remembered to log the call."""
    signup(client)
    p = new_lead(client, lead_type='realtor')
    new_lead(client, referred_by=p['id'])
    assert [x['id'] for x in _book(client)['partners']] == [p['id']]


def test_cold_imported_prospects_are_not_in_the_book(client):
    """A row nobody has ever called is a prospect, not a partner. Listing them
    here both drowns the real book and overstates it."""
    signup(client)
    _cold(client, 50)
    b = _book(client)
    assert b['partners'] == []
    assert b['cold_prospects'] == 50, 'counted, so they are visibly excluded'


def test_search_reaches_the_cold_prospects(client):
    """Excluded from the default view, never unreachable."""
    signup(client)
    _cold(client, 50)
    found = _book(client, q='HOA+7')['partners']
    assert [x['company'] for x in found] == ['HOA 7']


def test_search_treats_wildcards_literally(client):
    signup(client)
    _cold(client, 3)
    assert _book(client, q='%')['partners'] == []


def test_the_book_is_bounded(client):
    """The bug: no limit at all, one DOM card per row."""
    signup(client)
    now = appmod._now()
    with appmod.get_db() as db:
        db.executemany(
            "INSERT INTO leads (id, lead_type, service, stage, entry_stage, rep, "
            "created_at, updated_at, last_activity_at) "
            "VALUES (?,'hoa','roofing','new','new','luke',?,?,?)",
            [(f'warm{i}', now, now, now) for i in range(250)])
    b = _book(client)
    assert len(b['partners']) == appmod.PARTNER_PAGE
    assert b['total'] == 250, 'the full size is reported even when the page is not'


def test_the_best_partners_come_first(client):
    """The book is read to decide who to call, and the partner who has sent
    four jobs is not the one to scroll past."""
    signup(client)
    quiet = new_lead(client, lead_type='realtor', first_name='Quiet')
    client.post(f'/api/leads/{quiet["id"]}/activities', json={'kind': 'call'})
    busy = new_lead(client, lead_type='realtor', first_name='Busy')
    for _ in range(3):
        new_lead(client, referred_by=busy['id'])
    assert [x['id'] for x in _book(client)['partners']] == [busy['id'], quiet['id']]


def test_a_rep_sees_only_their_own_partners(client):
    signup(client, 'luke')                        # manager
    signup(client, 'casey')
    login(client, 'luke')
    p = new_lead(client, lead_type='realtor')
    client.post(f'/api/leads/{p["id"]}/activities', json={'kind': 'call'})
    login(client, 'casey')
    assert _book(client)['partners'] == []
    assert _book(client)['cold_prospects'] == 0


def test_the_book_ships_only_what_a_card_needs(client):
    """204 KB of research notes, citations and storm summaries to draw a name,
    a type and three numbers — over a phone connection."""
    signup(client)
    p = new_lead(client, lead_type='realtor', first_name='Sara')
    client.post(f'/api/leads/{p["id"]}/activities', json={'kind': 'call'})
    row = _book(client)['partners'][0]
    assert set(row) == {'id', 'name', 'lead_type', 'company', 'phone', 'email',
                        'city', 'stage', 'stage_label', 'stage_color',
                        'referrals_total', 'referrals_won'}
