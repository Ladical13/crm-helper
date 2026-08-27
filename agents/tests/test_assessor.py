"""County assessor records — the free source behind the `commercial` segment.

No network. Both counties are exercised against recorded slices of the real
payloads: Larimer's CSV extracts and Weld's ArcGIS FeatureServer JSON.

This replaced Perplexity guessing at "commercial building owners in Greeley".
The tests that matter are the ones about what the data does NOT say — Weld
publishes no roof or year-built at all, and Larimer's roof fields are only
partly filled, so the scoring must never read absent as old, flat, or small.
"""
import json

import pytest


_IMP_HEADER = ('PARCELNO,ACCOUNTNO,SCHEDULENUM,IMPACTUALVALUE,IMPNO,'
               'PROPERTYTYPE,OCCCODE,OCCDESCRIPTION,OCCPERCENT,OCCABSTRACT,'
               'BLTASCODE,BLTASDESCRIPTION,SF,CONDOIMPSF,BSMNTSF,BSMNTFINSF,'
               'GARSF,IMPPERIMETER,IMPCOMPLETEDPCT,IMPCONDITIONTYPE,'
               'IMPQUALITY,HVACTYPE,IMPEXTERIOR,IMPINTERIOR,IMPUNITTYPE,'
               'BLTASSTORIES,SPRINKLERSF,ROOFTYPE,ROOFCOVER,FLOORCOVER,'
               'BLTASFOUNDATION,ROOMCOUNT,BEDROOMCOUNT,BATHCOUNT,CLASSCODE,'
               'CLASSDESCRIPTION,BLTASYEARBUILT,YEARREMODELED,'
               'REMODELEDPERCENT,ADJUSTEDYEARBUILT,AGE,MHTITLENO,MHSERIALNO,'
               'MHLENGTH,MHWIDTH,MHMAKE,TAXYEAR,RUNDATE')

_OWN_HEADER = ('PARCELNO,ACCOUNTNO,SCHEDULENUM,NAME1,NAME2,MAILADDRESS1,'
               'MAILADDRESS2,MAILCITY,MAILSTATE,MAILZIPCODE,SITUSSTREETNO,'
               'SITUSPREDIRECTION,SITUSSTREETNAME,SITUSSTREETTYPE,'
               'SITUSPOSTDIRECTION,SITUSUNIT,SITUSADDRESS,SITUSCITY,'
               'SITUSZIPCODE,TAXYEAR,RUNDATE')


def _imp(acct, ptype='Commercial', occ='Office Building', sf='24000',
         rooftype='Flat', roofcover='', year='1994', cond='Average'):
    c = [''] * 48
    c[1], c[5], c[7], c[12] = acct, ptype, occ, sf
    c[19], c[27], c[28], c[36] = cond, rooftype, roofcover, year
    return ','.join(c)


def _own(acct, name1, situs, city='FORT COLLINS', mail='PO BOX 580',
         mailcity='FORT COLLINS', zipc='80525'):
    c = [''] * 21
    c[1], c[3], c[5] = acct, name1, mail
    c[7], c[8], c[9] = mailcity, 'CO', '80522'
    c[16], c[17], c[18] = situs, city, zipc
    return ','.join(c)


@pytest.fixture
def larimer(monkeypatch, tmp_path):
    """Point the source at CSV files on disk, the way the real cache does."""
    from agents.b2b.sources import _common, assessor

    imp = tmp_path / 'imp.csv'
    imp.write_text('\n'.join([
        _IMP_HEADER,
        _imp('R001', sf='120000', rooftype='Flat', year='1985'),
        _imp('R002', sf='4000', rooftype='Gable', year='2019'),
        # Same account, two buildings: the bigger one decides the job.
        _imp('R003', sf='9000', occ='Office Building'),
        _imp('R003', sf='60000', occ='Storage Warehouse'),
        # Residential must never reach the commercial queue.
        _imp('R004', ptype='Residential', sf='2200'),
        # Roof and year unknown — the common case, and it must not score as
        # though the building were new or steep.
        _imp('R005', sf='30000', rooftype='', year='0'),
    ]), encoding='utf-8')

    own = tmp_path / 'own.csv'
    own.write_text('\n'.join([
        _OWN_HEADER,
        _own('R001', 'BIG FLAT HOLDINGS LLC', '100 INDUSTRIAL WAY'),
        _own('R002', 'SMALL SHOP INC', '2 TINY LN'),
        _own('R003', 'WAREHOUSE PARTNERS LP', '300 DEPOT ST'),
        _own('R004', 'A HOMEOWNER', '55 HOUSE RD'),
        _own('R005', 'UNKNOWN ROOF LLC', '7 MYSTERY AVE'),
        # Right shape, wrong town.
        _own('R001', 'BIG FLAT HOLDINGS LLC', '9 ELSEWHERE ST', city='PUEBLO'),
    ]), encoding='utf-8')

    paths = {'larimer_improvement.csv': str(imp),
             'larimer_owner_location.csv': str(own)}
    monkeypatch.setattr(_common, 'fetch_cached_path',
                        lambda name, url, **k: paths.get(name))
    # Weld off unless a test asks for it.
    monkeypatch.setattr(_common, 'fetch_cached', lambda *a, **k: None)
    return assessor


def test_only_commercial_property_reaches_the_commercial_queue(larimer):
    names = {r['company'] for r in larimer.commercial(city='Fort Collins')}
    assert 'A Homeowner' not in names
    assert 'Big Flat Holdings Llc' in names


def test_one_owner_one_card_keeping_the_biggest_building(larimer):
    """An account can carry a warehouse and its office. Pushing both puts the
    same owner in a rep's queue twice for one conversation."""
    rows = [r for r in larimer.commercial(city='Fort Collins')
            if r['company'] == 'Warehouse Partners Lp']
    assert len(rows) == 1
    assert '60,000 sq ft' in rows[0]['hook']
    assert 'Storage Warehouse' in rows[0]['hook']


def test_a_big_old_flat_roof_outranks_a_small_new_steep_one(larimer):
    rows = larimer.commercial(city='Fort Collins')
    order = [r['company'] for r in rows]
    assert order.index('Big Flat Holdings Llc') < order.index('Small Shop Inc')


def test_an_unknown_roof_is_not_scored_as_an_old_flat_one(larimer):
    """Weld publishes no roof or year data and Larimer's is 68% filled, so
    absent is the common case. Reading it as old-and-flat would put every
    unknown building at the top of the queue."""
    rows = {r['company']: r for r in larimer.commercial(city='Fort Collins')}
    unknown = rows['Unknown Roof Llc']
    known = rows['Big Flat Holdings Llc']
    assert unknown['icp_score'] < known['icp_score']
    assert 'roof' not in unknown['hook'].lower()
    assert 'built' not in unknown['hook'].lower()


def test_the_city_filter_uses_where_the_building_is(larimer):
    """Not where the owner gets their mail — the roof is the thing being sold,
    and plenty of Fort Collins buildings are owned from out of state."""
    names = {r['company'] for r in larimer.commercial(city='Pueblo')}
    assert names == {'Big Flat Holdings Llc'}
    assert '9 Elsewhere St' in {r['address']
                                for r in larimer.commercial(city='Pueblo')}


def test_the_owners_mailing_address_survives_into_the_hook(larimer):
    """The importer accepts a fixed field list and silently ignores anything
    else, so a `mailing_address` key would vanish without a word. For a
    commercial LLC the building has no mailbox and this is the way in."""
    row = next(r for r in larimer.commercial(city='Fort Collins')
               if r['company'] == 'Big Flat Holdings Llc')
    assert 'Owner mail:' in row['hook']
    assert 'mailing_address' not in row


def test_the_account_number_is_the_dedupe_key(larimer):
    row = next(r for r in larimer.commercial(city='Fort Collins')
               if r['company'] == 'Big Flat Holdings Llc')
    assert row['source_ref'] == 'assessor:larimer:R001'
    assert row['license_no'] == 'R001'


# ── Weld ─────────────────────────────────────────────────────────────────────

_WELD = {'features': [
    {'attributes': {'ACCOUNTNO': 'W100', 'NAME': 'LEPRINO FOODS COMPANY',
                    'BUSINESSNAME': 'Leprino Foods Company',
                    'ADDRESS1': '1830 W 38TH AVE', 'CITY': 'DENVER',
                    'STATE': 'CO', 'ZIPCODE': '80211', 'STREETNO': '1302',
                    'STREETNAME': '1ST', 'STREETSUF': 'AVE', 'STREETDIR': None,
                    'LOCCITY': 'GREELEY', 'SQFT': 689580,
                    'ACCTTYPE': 'Commercial'}},
    {'attributes': {'ACCOUNTNO': 'W200', 'NAME': 'UNKNOWN',
                    'STREETNO': '5', 'STREETNAME': 'NOBODY',
                    'LOCCITY': 'GREELEY', 'SQFT': 1000,
                    'ACCTTYPE': 'Commercial'}},
]}


@pytest.fixture
def weld(monkeypatch):
    from agents.b2b.sources import _common, assessor
    monkeypatch.setattr(_common, 'fetch_cached_path', lambda *a, **k: None)
    monkeypatch.setattr(_common, 'fetch_cached',
                        lambda *a, **k: json.dumps(_WELD))
    return assessor


def test_weld_returns_owners_with_the_building_address(weld):
    rows = weld.commercial(city='Greeley')
    row = next(r for r in rows if r['company'] == 'Leprino Foods Company')
    assert row['address'] == '1302 1st Ave'
    assert row['city'] == 'Greeley'
    assert row['source_ref'] == 'assessor:weld:W100'
    assert '689,580 sq ft' in row['hook']


def test_weld_claims_no_roof_or_year_it_does_not_have(weld):
    """Weld's ownership layer carries neither. Inventing a default here would
    be a confident lie on a rep's card."""
    row = next(r for r in weld.commercial(city='Greeley')
               if r['company'] == 'Leprino Foods Company')
    assert 'roof' not in row['hook'].lower()
    assert 'built' not in row['hook'].lower()


def test_a_placeholder_owner_name_is_not_a_lead(weld):
    names = {r['company'] for r in weld.commercial(city='Greeley')}
    assert 'Unknown' not in names


def test_one_county_failing_does_not_lose_the_other(monkeypatch):
    """Larimer is a 46 MB download and Weld is a live API. Either can be
    having a bad day, and the other's rows are still good."""
    from agents.b2b.sources import _common, assessor

    def boom(*a, **k):
        raise RuntimeError('county is down')

    monkeypatch.setattr(_common, 'fetch_cached_path', boom)
    monkeypatch.setattr(_common, 'fetch_cached', lambda *a, **k: json.dumps(_WELD))
    rows = assessor.commercial(city='Greeley')
    assert [r['company'] for r in rows] == ['Leprino Foods Company']


def test_a_city_name_cannot_break_out_of_the_weld_where_clause(monkeypatch):
    """The ArcGIS query is built by string interpolation. A city name has no
    business carrying a quote, but that must not be how we find out."""
    from agents.b2b.sources import _common, assessor
    seen = {}

    def capture(name, url, **kw):
        seen['where'] = (kw.get('params') or {}).get('where', '')
        return None

    monkeypatch.setattr(_common, 'fetch_cached_path', lambda *a, **k: None)
    monkeypatch.setattr(_common, 'fetch_cached', capture)
    assessor.commercial(city="O'Brien")
    assert "O''BRIEN" in seen['where']


def test_the_commercial_segment_now_tries_the_assessor_before_paying():
    from agents.b2b import sources
    pullers = sources.pullers_for('commercial')
    assert pullers[0].__module__.endswith('assessor')
    assert pullers[-1].__module__.endswith('perplexity_gap')


# ── Secretary of State: putting a name to the LLC ────────────────────────────

_SOS = [
    {'entityname': 'EJS Holdings, LLC', 'entitystatus': 'Good Standing',
     'agentfirstname': 'ELIZABETH', 'agentlastname': 'SAMPSON'},
    # The registry never deletes, and a dead shell often shares the name.
    {'entityname': 'Big Flat Holdings LLC, Dissolved June 30, 2021',
     'entitystatus': 'Voluntarily Dissolved',
     'agentfirstname': 'OLD', 'agentlastname': 'OWNER'},
    {'entityname': 'BIG FLAT HOLDINGS LLC', 'entitystatus': 'Good Standing',
     'agentfirstname': 'CURRENT', 'agentlastname': 'OWNER'},
    # A prefix LIKE on "HARMONY ROAD" really does return all of these.
    {'entityname': 'Harmony Road Veterinary Clinic, PC',
     'entitystatus': 'Good Standing',
     'agentfirstname': 'WRONG', 'agentlastname': 'VET'},
    # A corporate agent service is a vendor's name, not the owner's.
    {'entityname': 'JDM II SF National, LLC', 'entitystatus': 'Good Standing',
     'agentfirstname': 'Registered Agents', 'agentlastname': 'Inc'},
]


@pytest.fixture
def registry(monkeypatch):
    from agents.b2b.sources import _common, sos
    monkeypatch.setattr(_common, 'fetch_cached', lambda *a, **k: json.dumps(_SOS))
    return sos


def test_an_llc_resolves_to_the_person_who_signs(registry):
    out = registry.principals_for(['EJS HOLDINGS LLC'])
    assert out['EJS HOLDINGS LLC'] == {'first_name': 'Elizabeth',
                                       'last_name': 'Sampson'}


def test_punctuation_and_legal_suffix_do_not_break_the_match(registry):
    """The assessor writes "EJS HOLDINGS LLC"; the registry writes
    "EJS Holdings, LLC". Exact matching finds neither."""
    for spelling in ('EJS HOLDINGS LLC', 'EJS Holdings, L.L.C.', 'Ejs Holdings'):
        assert registry.principals_for([spelling]).get(spelling, {}).get(
            'last_name') == 'Sampson'


def test_a_dissolved_shell_never_outranks_the_live_entity(registry):
    out = registry.principals_for(['BIG FLAT HOLDINGS LLC'])
    assert out['BIG FLAT HOLDINGS LLC']['first_name'] == 'Current'


def test_a_prefix_match_is_not_a_match(registry):
    """Searching "HARMONY ROAD" returns a vet clinic, a music academy and a
    massage therapist. Handing a rep any of them as the building's owner is
    worse than handing them nothing."""
    assert registry.principals_for(['HARMONY ROAD LLC']) == {}


def test_a_corporate_agent_service_is_not_a_person_to_ask_for(registry):
    """A rep asking the front desk for "Registered Agents Inc" gets a blank
    look. No name beats a vendor's name."""
    assert registry.principals_for(['JDM II SF NATIONAL LLC']) == {}


def test_a_registry_outage_leaves_the_lead_intact(monkeypatch, larimer):
    """An unresolved entity is still a perfectly good lead — the assessor
    already told us who owns the building and where it is."""
    from agents.b2b.sources import sos

    def boom(*a, **k):
        raise RuntimeError('socrata is down')

    monkeypatch.setattr(sos, 'principals_for', boom)
    rows = larimer.commercial(city='Fort Collins')
    assert rows
    assert all(r['first_name'] == '' for r in rows)
