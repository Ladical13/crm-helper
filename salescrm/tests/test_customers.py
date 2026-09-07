"""The PERSON, as distinct from the deal.

`leads` is one row per deal on purpose — the cross-sell Pitch button creates a
second lead for the same homeowner deliberately. That is right at the deal level
and it left nothing at the person level, so a homeowner with a roof in spring
and siding in autumn was two unrelated rows, their documents split across both,
and "what has this customer ever had from us" had no answer anywhere.
"""
import io

from conftest import signup, login, new_lead
import app as appmod


def _cust(client, lead):
    return client.get(f'/api/leads/{lead["id"]}').get_json()['customer']


def _upload(client, lead_id, name='claim.pdf'):
    return client.post(f'/api/leads/{lead_id}/documents',
                       data={'file': (io.BytesIO(b'%PDF-1.4'), name)},
                       content_type='multipart/form-data')


# ── Matching: on contact details, never on a name ────────────────────────────

def test_two_deals_for_one_phone_number_are_one_person(client):
    signup(client)
    roof = new_lead(client, first_name='Dana', last_name='Reed', phone='(970) 555-1212')
    siding = new_lead(client, first_name='Dana', last_name='Reed',
                      phone='970-555-1212', service='window_cleaning')
    assert _cust(client, roof)['id'] == _cust(client, siding)['id']
    assert _cust(client, roof)['other_deals'] == 1


def test_two_jon_smiths_are_two_people(client):
    """A name-only key merges their files — which in this business means one
    homeowner's signed contract filed under another's."""
    signup(client)
    a = new_lead(client, first_name='Jon', last_name='Smith', phone='9705551111')
    b = new_lead(client, first_name='Jon', last_name='Smith', phone='9705552222')
    assert _cust(client, a)['id'] != _cust(client, b)['id']


def test_email_matches_when_there_is_no_phone(client):
    signup(client)
    a = new_lead(client, last_name='Reed', email='Dana@Example.com')
    b = new_lead(client, last_name='Reed', email='dana@example.com')
    assert _cust(client, a)['id'] == _cust(client, b)['id']


def test_an_address_matches_only_with_a_surname(client):
    """A roof outlives its owner. Address alone would merge whoever we sold to
    in 2019 with whoever lives there now."""
    signup(client)
    old = new_lead(client, last_name='Reed', address='12 Elm St', city='Fort Collins')
    same = new_lead(client, last_name='Reed', address='12 Elm  Street',
                    city='Fort Collins')
    new_owner = new_lead(client, last_name='Okafor', address='12 Elm St',
                         city='Fort Collins')
    assert _cust(client, old)['id'] == _cust(client, same)['id']
    assert _cust(client, new_owner)['id'] != _cust(client, old)['id']


def test_a_row_that_identifies_nobody_gets_no_customer(client):
    """An open-data row with a company name and a city is not a person. Minting
    one each would put tens of thousands of rows in the table naming nobody."""
    signup(client)
    lead = new_lead(client, first_name='', last_name='', company='Some HOA',
                    phone='', email='', address='')
    assert _cust(client, lead) is None
    with appmod.get_db() as db:
        assert db.execute('SELECT COUNT(*) c FROM customers').fetchone()['c'] == 0


def test_later_deals_fill_in_what_the_first_did_not_know(client):
    """A doorstep lead with only an address, then a phone number three days
    later. Blanks fill; values already there are not overwritten, because the
    newest typing is not automatically the most correct."""
    signup(client)
    first = new_lead(client, first_name='', last_name='Reed',
                     address='12 Elm St', city='Fort Collins')
    cid = _cust(client, first)['id']
    new_lead(client, last_name='Reed', address='12 Elm St', city='Fort Collins',
             first_name='Dana', phone='9705551212')
    with appmod.get_db() as db:
        got = db.execute('SELECT * FROM customers WHERE id=?', (cid,)).fetchone()
    assert got['first_name'] == 'Dana'          # was blank, so it filled
    assert got['last_name'] == 'Reed'           # was set, so it stood
    assert got['phone_norm'] == '9705551212'


def test_correcting_a_phone_number_identifies_an_anonymous_lead(client):
    signup(client)
    lead = new_lead(client, first_name='', last_name='', company='Unknown',
                    phone='', email='', address='')
    assert _cust(client, lead) is None
    client.put(f'/api/leads/{lead["id"]}', json={'phone': '9705551212'})
    assert _cust(client, lead) is not None


def test_a_deal_is_not_re_pointed_at_a_different_person(client):
    """Re-pointing on an edit is how a customer's history splits in two."""
    signup(client)
    lead = new_lead(client, last_name='Reed', phone='9705551212')
    cid = _cust(client, lead)['id']
    client.put(f'/api/leads/{lead["id"]}', json={'phone': '9705559999'})
    assert _cust(client, lead)['id'] == cid


# ── The file itself ──────────────────────────────────────────────────────────

def test_the_customer_carries_every_deal_and_its_whole_timeline(client):
    """"We quoted them in March, lost it on price, and they called back in
    October" is one story that lived in two places."""
    signup(client)
    roof = new_lead(client, last_name='Reed', phone='9705551212', est_value=20000)
    siding = new_lead(client, last_name='Reed', phone='9705551212',
                      service='window_cleaning', est_value=3000)
    client.patch(f'/api/leads/{roof["id"]}/stage', json={'stage': 'won'})
    cid = _cust(client, roof)['id']

    got = client.get(f'/api/customers/{cid}').get_json()
    assert {l['id'] for l in got['leads']} == {roof['id'], siding['id']}
    assert got['won_count'] == 1 and got['open_count'] == 1
    assert got['lifetime_value'] == 20000
    assert any(a['lead_id'] == siding['id'] for a in got['activities'])


def test_a_document_belongs_to_the_person_not_just_the_deal(client):
    """An insurance letter uploaded on the roof lead is the same customer's
    letter when they come back for siding."""
    signup(client)
    roof = new_lead(client, last_name='Reed', phone='9705551212')
    _upload(client, roof['id'], name='adjuster.pdf')
    cid = _cust(client, roof)['id']
    got = client.get(f'/api/customers/{cid}').get_json()
    assert [d['orig_name'] for d in got['documents']] == ['adjuster.pdf']


def test_a_rep_cannot_read_a_customer_they_have_no_deal_with(client):
    """Otherwise the record is a way to read another rep's pipeline sideways."""
    signup(client, 'luke')                       # manager
    signup(client, 'casey')
    login(client, 'casey')
    lead = new_lead(client, last_name='Reed', phone='9705551212')
    cid = _cust(client, lead)['id']
    signup(client, 'dana')
    login(client, 'dana')
    assert client.get(f'/api/customers/{cid}').status_code == 404


def test_a_rep_sees_only_their_own_deals_on_a_shared_customer(client):
    signup(client, 'luke')
    signup(client, 'casey')
    login(client, 'luke')
    mine = new_lead(client, last_name='Reed', phone='9705551212')
    cid = _cust(client, mine)['id']
    login(client, 'casey')
    theirs = new_lead(client, last_name='Reed', phone='9705551212')
    got = client.get(f'/api/customers/{cid}').get_json()
    assert [l['id'] for l in got['leads']] == [theirs['id']]


def test_customers_can_be_searched(client):
    signup(client)
    new_lead(client, first_name='Dana', last_name='Reed', phone='9705551212')
    new_lead(client, first_name='Sam', last_name='Okafor', phone='9705559999')
    found = client.get('/api/customers?q=okafor').get_json()
    assert [c['name'] for c in found] == ['Sam Okafor']


def test_search_treats_wildcards_literally(client):
    signup(client)
    new_lead(client, last_name='Reed', phone='9705551212')
    assert client.get('/api/customers?q=%').get_json() == []


def test_correcting_the_person_updates_their_keys(client):
    signup(client)
    lead = new_lead(client, last_name='Reed', phone='9705551212')
    cid = _cust(client, lead)['id']
    client.put(f'/api/customers/{cid}', json={'phone': '(970) 555-3434'})
    with appmod.get_db() as db:
        assert db.execute('SELECT phone_norm FROM customers WHERE id=?',
                          (cid,)).fetchone()['phone_norm'] == '9705553434'


# ── Existing data ────────────────────────────────────────────────────────────

def test_leads_already_in_the_table_are_grouped_on_migration(client):
    signup(client)
    a = new_lead(client, last_name='Reed', phone='9705551212')
    b = new_lead(client, last_name='Reed', phone='9705551212')
    with appmod.get_db() as db:                  # rewind to before the feature
        db.execute("UPDATE leads SET customer_id=''")
        db.execute('DELETE FROM customers')
        appmod._backfill_customers(db)
    assert _cust(client, a)['id'] == _cust(client, b)['id']


def test_the_backfill_is_idempotent(client):
    signup(client)
    new_lead(client, last_name='Reed', phone='9705551212')
    with appmod.get_db() as db:
        appmod._backfill_customers(db)
        appmod._backfill_customers(db)
        assert db.execute('SELECT COUNT(*) c FROM customers').fetchone()['c'] == 1


def test_the_backfill_does_not_re_examine_rows_that_name_nobody(client, monkeypatch):
    """This runs at import, on every gunicorn boot. A prospecting table is
    mostly rows that will never identify anybody and they keep customer_id=''
    forever, so a bare scan re-runs the address normalizer over all 36,000 of
    them on every deploy — 650ms a boot at 40k leads, growing with each import.
    """
    signup(client)
    new_lead(client, first_name='', last_name='', company='Some HOA',
             phone='', email='', address='')
    new_lead(client, last_name='Reed', phone='9705551212')

    seen = []
    real = appmod._link_customer
    monkeypatch.setattr(appmod, '_link_customer',
                        lambda db, lid, row: (seen.append(lid), real(db, lid, row))[1])
    with appmod.get_db() as db:
        db.execute("UPDATE leads SET customer_id=''")
        db.execute('DELETE FROM customers')
        appmod._backfill_customers(db)
        assert len(seen) == 1, 'the anonymous row must not be considered at all'
        seen.clear()
        appmod._backfill_customers(db)
    assert seen == [], 'and nothing is reconsidered on the next boot'
