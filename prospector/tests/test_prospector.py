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
