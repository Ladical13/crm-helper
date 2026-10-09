"""Sourcing layer. Offline — the network is stubbed, so these stay fast.

The live end of this (that the datasets exist and the filters return what we
think) is checked with `python -m prospector segments --count`.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from prospector import normalize, socrata, sources          # noqa: E402
from prospector.sources import cdos, dora                   # noqa: E402


# ── SoQL construction ────────────────────────────────────────────────────────

def test_quote_escapes_apostrophes():
    """Doubling the quote is SoQL's only escape.

    Without it "Home Owner's Association" - the biggest free segment there is -
    is a syntax error rather than 8,536 HOAs.
    """
    assert socrata.quote("Home Owner's Association") == "'Home Owner''s Association'"
    assert socrata.quote('plain') == "'plain'"


def test_any_of_builds_an_in_clause():
    assert socrata.any_of('licensetype', ['A', 'B']) == "licensetype in ('A', 'B')"


def test_dora_where_filters_active_colorado():
    w = dora.where('hoa')
    assert "Home Owner''s Association" in w
    assert "licensestatus = 'Active'" in w
    assert "state = 'CO'" in w


def test_dora_where_can_drop_the_filters():
    w = dora.where('hoa', active_only=False, state=None)
    assert 'licensestatus' not in w and 'state =' not in w


def test_cdos_where_filters_good_standing_and_keywords():
    w = cdos.where('property_manager')
    assert "upper(entityname) like '%PROPERTY MANAGEMENT%'" in w
    assert "entitystatus = 'Good Standing'" in w
    assert "principalstate = 'CO'" in w


def test_every_segment_declares_a_valid_lead_type():
    """Segment lead_types must match salescrm's LEAD_TYPES or the import 400s."""
    valid = {'homeowner', 'realtor', 'hoa', 'insurance_agent', 'property_manager',
             'adjuster', 'commercial', 'referral_partner'}
    for name, _mod, meta in sources.all_segments():
        assert meta['lead_type'] in valid, name


def test_resolve_rejects_unknown_names():
    with pytest.raises(KeyError):
        sources.resolve('nope:hoa')
    with pytest.raises(KeyError):
        sources.resolve('dora:nope')
    with pytest.raises(KeyError):
        sources.resolve('dora')


def test_individual_brokers_are_off_by_default():
    """They carry no contact details, so they are the paid tier, not the free one."""
    assert dora.SEGMENTS['broker'].get('default') is False
    assert 'dora:broker' not in [n for n, _m, _x in sources.all_segments(defaults_only=True)]


# ── Normalization ────────────────────────────────────────────────────────────

def test_clean_entity_name_strips_status_suffixes():
    """Colorado stores delinquency inside the name; unstripped it lands on a card."""
    assert normalize.clean_entity_name(
        'ARG PROPERTY MANAGEMENT CORPORATION, Delinquent September 1, 2009'
    ) == 'ARG PROPERTY MANAGEMENT CORPORATION'
    assert normalize.clean_entity_name('Acme Realty LLC, Dissolved June 2020') == 'Acme Realty LLC'


def test_clean_entity_name_leaves_good_names_alone():
    for name in ('Highland Terrace Lofts Condominiums, Inc.',
                 "TOLLGATE CREEK TOWNHOMES HOMEOWNERS' ASSOCIATION",
                 'Espinoza Property Management LLC'):
        assert normalize.clean_entity_name(name) == name


def test_row_always_has_every_field():
    r = normalize.row(company='Acme')
    assert set(normalize.FIELDS) <= set(r)
    assert r['phone'] == '' and r['icp_score'] == 0


def test_score_rewards_reachability_and_service_area():
    assert normalize.score(city='Fort Collins', address='1 Main St', person='Jane') == 6
    assert normalize.score(city='Fort Collins') == 3          # in area, active
    assert normalize.score(city='Snowmass Village') == 1      # active only
    assert normalize.score(city='Fort Collins', active=False) == 2


# ── Row shaping (network stubbed) ────────────────────────────────────────────

def _stub(monkeypatch, rows):
    monkeypatch.setattr(socrata, 'fetch', lambda *a, **k: iter(rows))


def test_dora_pull_shapes_an_hoa_row(monkeypatch):
    _stub(monkeypatch, [{'entityname': 'Centerra Marketplace Association',
                         'city': 'Loveland', 'state': 'CO', 'zipcode': '80538',
                         'licensenumber': '51739'}])
    r = list(dora.pull('hoa'))[0]
    assert r['company'] == 'Centerra Marketplace Association'
    assert r['license_no'] == '51739'
    assert r['source_ref'] == 'dora:4zse-6bnw:51739'
    assert r['icp_score'] == 3                                # Loveland is in area


def test_dora_pull_skips_rows_without_a_licence(monkeypatch):
    """license_no and source_ref are the only dedupe keys DORA rows have."""
    _stub(monkeypatch, [{'entityname': 'Nameless HOA', 'city': 'Denver'}])
    assert list(dora.pull('hoa')) == []


def test_dora_pull_drops_repeat_licences(monkeypatch):
    _stub(monkeypatch, [{'entityname': 'A', 'licensenumber': '1', 'city': 'Denver'},
                        {'entityname': 'A', 'licensenumber': '1', 'city': 'Denver'}])
    assert len(list(dora.pull('hoa'))) == 1


def test_cdos_pull_keeps_address_and_named_agent(monkeypatch):
    _stub(monkeypatch, [{'entityid': '19871290381',
                         'entityname': 'URBAN PROPERTY MANAGEMENT, INC.',
                         'principaladdress1': '5450 Greenwood Plaza Blvd Ste 200',
                         'principalcity': 'Greenwood Village', 'principalstate': 'CO',
                         'principalzipcode': '80111',
                         'agentfirstname': 'STEPHEN', 'agentlastname': 'SHRAIBERG'}])
    r = list(cdos.pull('property_manager'))[0]
    assert r['first_name'] == 'Stephen' and r['last_name'] == 'Shraiberg'
    assert r['address'] == '5450 Greenwood Plaza Blvd Ste 200'
    assert r['source_ref'] == 'cdos:4ykn-tg5h:19871290381'
    assert r['icp_score'] == 4                                # address + person + active


def test_cdos_pull_ignores_registered_agent_services(monkeypatch):
    """CSC is a filing address, not somebody who buys roofs."""
    _stub(monkeypatch, [{'entityid': '1', 'entityname': 'Acme Property Management',
                         'principaladdress1': '1 Main St', 'principalcity': 'Denver',
                         'agentorganizationname': 'CSC',
                         'agentfirstname': 'MICHAEL', 'agentlastname': 'HESSEL'}])
    r = list(cdos.pull('property_manager'))[0]
    assert r['first_name'] == '' and r['last_name'] == ''


def test_cdos_pull_truncates_zip_plus_four(monkeypatch):
    _stub(monkeypatch, [{'entityid': '1', 'entityname': 'Acme Property Management',
                         'principalzipcode': '805381234', 'principalcity': 'Loveland'}])
    assert list(cdos.pull('property_manager'))[0]['zip'] == '80538'


def test_cdos_pull_cleans_delinquent_names(monkeypatch):
    _stub(monkeypatch, [{'entityid': '2',
                         'entityname': 'ARG PROPERTY MANAGEMENT CORPORATION, '
                                       'Delinquent September 1, 2009',
                         'principalcity': 'Los Altos'}])
    assert list(cdos.pull('property_manager'))[0]['company'] == \
        'ARG PROPERTY MANAGEMENT CORPORATION'


# ── The Den: people who already know us ──────────────────────────────────────

from prospector.sources import den                          # noqa: E402

CO = den.CO_LOCATION_ID
_TODAY = __import__('datetime').datetime(2026, 6, 1, tzinfo=__import__('datetime').timezone.utc)


def _den(projects=(), contacts=(), partners=()):
    """A stand-in for the Den read, keyed by entity."""
    data = {'Project': list(projects), 'Contact': list(contacts),
            'ReferralPartner': list(partners)}
    return lambda entity, location=True: data[entity]


def _job(pid, name, status, email='', phone='', **kw):
    return dict({'id': pid, 'client_name': name, 'status': status, 'client_email': email,
                 'client_phone': phone, 'created_date': '2026-02-01T15:00:00.000000',
                 'updated_date': '2026-03-09T17:00:00.000000'}, **kw)


def test_a_finished_job_becomes_a_won_customer():
    fetch = _den(
        projects=[_job('p1', 'Pat Ng', 'paid_and_closed', 'Pat@Example.com', '(970) 555-0101',
                       roof_installation_completed_date='2026-03-05')],
        contacts=[{'id': 'c1', 'name': 'Pat Ng', 'email': 'pat@example.com',
                   'city': 'loveland', 'street_address': '1 Elm St', 'zip_code': '80537-1234'}])
    [row] = list(den.pull('customers', fetch=fetch))
    assert (row['first_name'], row['last_name'], row['email']) == ('Pat', 'Ng', 'pat@example.com')
    assert (row['stage'], row['won_at']) == ('won', '2026-03-05')
    assert (row['city'], row['zip'], row['address']) == ('Loveland', '80537', '1 Elm St')
    # Keyed on the contact, so a second job for Pat is the same lead.
    assert row['source_ref'] == 'den:contact:c1'


def test_nobody_with_a_job_in_production_is_contacted():
    """A sales text in the middle of someone's install."""
    fetch = _den(projects=[
        _job('p1', 'Pat Ng', 'paid_and_closed', 'pat@example.com'),
        _job('p2', 'Pat Ng', 'scheduled', 'pat@example.com'),
        _job('p3', 'Sam Lee', 'follow_up', phone='970-555-0102'),
        _job('p4', 'Sam Lee', 'collect_final_payment', phone='9705550102'),
    ])
    assert list(den.pull('customers', fetch=fetch)) == []
    assert list(den.pull('open_jobs', fetch=fetch)) == []


def test_red_flag_customers_are_left_out():
    fetch = _den(projects=[_job('p1', 'Pat Ng', 'paid_and_closed', 'pat@example.com')],
                 contacts=[{'id': 'c1', 'email': 'pat@example.com',
                            'is_red_flag_customer': True}])
    assert list(den.pull('customers', fetch=fetch)) == []


def test_one_row_per_person_and_a_customer_is_never_an_open_job():
    fetch = _den(projects=[
        _job('p1', 'Pat Ng', 'paid_and_closed', 'pat@example.com'),
        _job('p2', 'Pat Ng', 'paid_and_closed', 'pat@example.com'),
        _job('p3', 'Pat Ng', 'ready_to_close', 'pat@example.com'),      # a second project
        _job('p4', 'Sam Lee', 'holding', phone='970-555-0102'),
        _job('p5', 'Lou Roe', 'lost_or_cancelled', 'lou@example.com'),  # not ours to nudge
    ])
    assert [r['first_name'] for r in den.pull('customers', fetch=fetch)] == ['Pat']
    opens = list(den.pull('open_jobs', fetch=fetch, today=_TODAY))
    assert [(r['first_name'], r['stage'], r['won_at']) for r in opens] == [('Sam', 'follow_up', '')]


def test_a_job_somebody_touched_last_week_is_not_an_open_job_yet():
    """Asked for an inspection on Friday; "I never heard where it landed" on Monday."""
    fetch = _den(projects=[
        _job('p1', 'New Lead', 'new_lead', 'new@example.com',
             updated_date='2026-05-28T12:00:00.000000'),
        _job('p2', 'Old Lead', 'ready_to_close', 'old@example.com',
             updated_date='2026-05-01T12:00:00.000000'),
        # Old estimate, but the same person called again this week.
        _job('p3', 'Back Again', 'follow_up', 'back@example.com',
             updated_date='2026-04-01T12:00:00.000000'),
        _job('p4', 'Back Again', 'new_lead', 'back@example.com',
             updated_date='2026-05-30T12:00:00.000000'),
    ])
    assert [r['first_name'] for r in den.pull('open_jobs', fetch=fetch, today=_TODAY)] == ['Old']


def test_only_colorado_partners_and_each_keeps_its_own_type():
    fetch = _den(partners=[
        {'id': 'r1', 'name': 'Kim Park', 'company': 'Park Realty', 'partner_type': 'realtor',
         'email': 'kim@parkrealty.com', 'location_id': CO, 'status': 'active'},
        {'id': 'r2', 'name': 'Tex Ray', 'partner_type': 'realtor', 'location_id': 'texas'},
        {'id': 'r3', 'name': 'Jo Fox', 'partner_type': 'plumber', 'location_id': CO},
    ])
    rows = list(den.pull('partners', fetch=fetch))
    assert [(r['first_name'], r['lead_type']) for r in rows] == [
        ('Kim', 'realtor'), ('Jo', 'referral_partner')]
    assert rows[0]['source_ref'] == 'den:partner:r1'


def test_a_den_row_says_who_sold_the_job():
    """As a username, so the importer can let a rep keep their own customers.
    The job's salesperson wins over whoever the contact was first assigned to."""
    fetch = _den(
        projects=[_job('p1', 'Pat Ng', 'paid_and_closed', 'pat@example.com',
                       assigned_salesperson='Derik@ProjectOneRoofing.com'),
                  _job('p2', 'Sam Lee', 'paid_and_closed', 'sam@example.com'),
                  _job('p3', 'Ada Roy', 'paid_and_closed', 'ada@example.com')],
        contacts=[{'id': 'c1', 'name': 'Pat Ng', 'email': 'pat@example.com',
                   'assigned_to': 'luke@projectoneroofing.com'},
                  {'id': 'c2', 'name': 'Sam Lee', 'email': 'sam@example.com',
                   'assigned_to': 'luke@projectoneroofing.com'}],
        partners=[{'id': 'r1', 'name': 'Kim Park', 'partner_type': 'realtor',
                   'location_id': CO, 'assigned_to': 'derik@projectoneroofing.com'}])
    owners = {r['first_name']: r['owner'] for r in den.pull('customers', fetch=fetch)}
    assert owners == {'Pat': 'derik', 'Sam': 'luke', 'Ada': ''}
    assert [r['owner'] for r in den.pull('partners', fetch=fetch)] == ['derik']


def test_every_den_segment_says_how_it_must_be_imported():
    """Imported cold, a customer is offered a free hail inspection."""
    for name, meta in den.SEGMENTS.items():
        assert meta['import']['lead_source'] in ('existing_customer', 'referral'), name
        assert meta['import']['cadence'], name


class _DenAnswer:
    def __init__(self, status, rows=()):
        self.status_code, self._rows = status, list(rows)

    def raise_for_status(self):
        pass

    def json(self):
        return self._rows


def _den_http(monkeypatch, answer):
    """Stand in for the Den over HTTP; returns the headers each call sent."""
    sent = []

    def get(url, params=None, timeout=None, headers=None):
        sent.append(headers)
        return answer

    monkeypatch.setattr(den.requests, 'get', get)
    return sent


def test_den_pull_needs_the_token(monkeypatch):
    monkeypatch.delenv('BASE44_TOKEN', raising=False)
    _den_http(monkeypatch, _DenAnswer(403))
    with pytest.raises(KeyError, match='not set'):
        list(den.pull('customers'))


def test_a_token_the_environment_adds_itself_is_not_sent_twice(monkeypatch):
    """In a Claude cloud environment the token is a network secret: the header
    is added on the way out and the session never holds it. So no variable is
    not yet an error, and nothing is sent in its place."""
    monkeypatch.delenv('BASE44_TOKEN', raising=False)
    sent = _den_http(monkeypatch, _DenAnswer(200, [_job('p1', 'Pat Ng', 'paid_and_closed',
                                                        'pat@example.com')]))
    assert [r['first_name'] for r in den.pull('customers')] == ['Pat']
    assert sent and all(h == {} for h in sent)


def test_an_empty_den_with_no_token_sent_is_a_refusal_not_a_quiet_week(monkeypatch):
    monkeypatch.delenv('BASE44_TOKEN', raising=False)
    _den_http(monkeypatch, _DenAnswer(200, []))
    with pytest.raises(KeyError, match='no token was sent'):
        list(den.pull('customers'))


def test_city_filter():
    assert normalize.city_filter('') is None
    assert normalize.city_filter('Fort Collins, loveland ') == {'fort collins', 'loveland'}
    assert 'greeley' in normalize.city_filter('noco') and 'denver' not in normalize.city_filter('noco')
