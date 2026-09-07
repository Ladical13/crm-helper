"""Importing the history that lives in The Den.

"Past customer" meant "past customer of THIS CRM" — a fraction of the real
history, because anyone who bought before this tool existed has no `won` lead
here. Every consumer of that idea was quietly understating: the storm alert,
lifetime value, past-customer mining.
"""
from conftest import signup, login, new_lead
import app as appmod


def _contact(cid='c1', **kw):
    row = {'id': cid, 'first_name': 'Dana', 'last_name': 'Reed',
           'phone': '(970) 555-1212', 'email': 'dana@example.com',
           'street_address': '12 Elm St', 'city': 'Fort Collins',
           'state': 'CO', 'zip_code': '80521'}
    row.update(kw)
    return row


def _run(client, contacts, **kw):
    return client.post('/api/customers/import',
                       json=dict(contacts=contacts, **kw)).get_json()


def test_a_completed_job_becomes_a_past_customer(client):
    signup(client)
    got = _run(client, [_contact(projects=[
        {'id': 'p1', 'status': 'completed', 'contract_value': 18500,
         'completed_date': '2021-05-04T00:00:00Z'}])])
    assert got['created'] == 1 and got['jobs'] == 1
    leads = client.get('/api/leads').get_json()
    assert [l['stage'] for l in leads] == ['won']
    assert leads[0]['est_value'] == 18500
    assert leads[0]['won_at'].startswith('2021-05-04')


def test_three_roofs_over_nine_years_are_three_deals_one_person(client):
    """The history reads as what it was, not as one row."""
    signup(client)
    _run(client, [_contact(projects=[
        {'id': 'p1', 'status': 'completed', 'contract_value': 9000,
         'completed_date': '2016-04-01T00:00:00Z'},
        {'id': 'p2', 'status': 'completed', 'contract_value': 12000,
         'completed_date': '2021-06-01T00:00:00Z'},
        {'id': 'p3', 'status': 'paid', 'contract_value': 22000,
         'completed_date': '2025-08-01T00:00:00Z'}])])
    leads = client.get('/api/leads').get_json()
    assert len(leads) == 3
    cid = client.get(f'/api/leads/{leads[0]["id"]}').get_json()['customer']['id']
    cust = client.get(f'/api/customers/{cid}').get_json()
    assert cust['won_count'] == 3
    assert cust['lifetime_value'] == 43000


def test_a_deal_that_never_became_a_job_is_not_imported_as_won(client):
    """Importing those as `won` would inflate every close rate and revenue
    figure on the board."""
    signup(client)
    got = _run(client, [_contact(projects=[{'id': 'p1', 'status': 'lead'}])])
    assert got['jobs'] == 0
    assert client.get('/api/leads').get_json() == []
    with appmod.get_db() as db:            # the person is still worth holding
        assert db.execute('SELECT COUNT(*) c FROM customers').fetchone()['c'] == 1


def test_re_running_the_import_changes_nothing(client):
    """A run that died halfway has to be safe to repeat."""
    signup(client)
    rows = [_contact(projects=[{'id': 'p1', 'status': 'completed',
                                'contract_value': 1000}])]
    _run(client, rows)
    again = _run(client, rows)
    assert again['created'] == 0 and again['skipped_already_here'] == 1
    assert len(client.get('/api/leads').get_json()) == 1


def test_dedupe_is_on_the_den_id_not_the_phone_number(client):
    """It survives a customer changing their number, which contact-detail
    matching would read as a different person."""
    signup(client)
    _run(client, [_contact('c1', projects=[{'id': 'p1', 'status': 'completed'}])])
    _run(client, [_contact('c1', phone='9705559999',
                           projects=[{'id': 'p1', 'status': 'completed'}])])
    assert len(client.get('/api/leads').get_json()) == 1


def test_a_red_flag_in_the_den_arrives_as_a_caution(client):
    """Not as do_not_serve. Those are different decisions and only one is
    recorded in Base44 — promoting it would make a call nobody made."""
    signup(client)
    got = _run(client, [_contact(is_red_flag_customer=True,
                                 projects=[{'id': 'p1', 'status': 'completed'}])])
    assert got['flagged'] == 1
    lead = client.get('/api/leads').get_json()[0]
    c = client.get(f'/api/leads/{lead["id"]}').get_json()['customer']
    assert c['flag'] == 'caution'
    assert c['flag_by'] == 'import'


def test_a_row_that_identifies_nobody_is_reported_not_silently_dropped(client):
    signup(client)
    got = _run(client, [{'id': 'c9', 'company': '', 'city': 'Fort Collins'}])
    assert got['created'] == 0
    assert any('c9' in p for p in got['problems'])


def test_a_contact_with_no_id_is_reported(client):
    signup(client)
    got = _run(client, [_contact(id='')])
    assert got['created'] == 0 and got['problems']


def test_a_dry_run_writes_nothing(client):
    signup(client)
    got = _run(client, [_contact(projects=[{'id': 'p1', 'status': 'completed'}])],
               dry_run=True)
    assert got['created'] == 1 and got['dry_run'] is True
    assert client.get('/api/leads').get_json() == []


def test_only_an_admin_can_import(client):
    signup(client, 'luke')                    # first user is admin
    signup(client, 'casey')
    login(client, 'casey')
    assert client.post('/api/customers/import',
                       json={'contacts': [_contact()]}).status_code == 403


def test_a_name_only_field_is_split(client):
    """Base44 carries both `name` and the split pair; older rows have only the
    one."""
    signup(client)
    _run(client, [{'id': 'c2', 'name': 'Sam Okafor', 'phone': '9705553333',
                   'projects': [{'id': 'p1', 'status': 'completed'}]}])
    lead = client.get('/api/leads').get_json()[0]
    assert (lead['first_name'], lead['last_name']) == ('Sam', 'Okafor')


def test_imported_customers_reach_the_storm_tiers(client):
    """The whole point: without this, "past customer" means "past customer of
    this CRM", which is a fraction of the real history."""
    signup(client)
    _run(client, [_contact(projects=[{'id': 'p1', 'status': 'completed'}])])
    lead = client.get('/api/leads').get_json()[0]
    assert appmod._lead_tier(lead) == 'past_customer'
