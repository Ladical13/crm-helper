"""Researching leads already in the CRM.

The rule worth a test: research may FILL an empty contact field, only with a
cited answer, never overwrite, and never fill an opted-out email. A fabricated
pastor's email is worse than none.
"""
import importlib.util
import os
import sys
import tempfile

import pytest

from agents import perplexity
from agents.b2b import reenrich

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope='module')
def crm():
    d = tempfile.mkdtemp(prefix='reenrich_')
    saved = {k: os.environ.get(k) for k in ('SALESCRM_DATA_DIR', 'PORTAL_DATA_DIR')}
    os.environ['SALESCRM_DATA_DIR'] = d
    os.environ['PORTAL_DATA_DIR'] = d
    sys.path.insert(0, REPO)
    spec = importlib.util.spec_from_file_location(
        'reenrich_crm_app', os.path.join(REPO, 'salescrm', 'app.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules['reenrich_crm_app'] = mod
    spec.loader.exec_module(mod)
    yield mod
    for k, v in saved.items():            # the path is resolved at import; put it back
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _lead(crm, **kw):
    import uuid
    row = dict(id=str(uuid.uuid4()), lead_type='church', company='Grace Church',
               city='Loveland', rep='luke', stage='new', created_at=crm._now(),
               updated_at=crm._now())
    row.update(kw)
    with crm.get_db() as db:
        db.execute(f'INSERT INTO leads ({",".join(row)}) VALUES ({",".join("?" * len(row))})',
                   list(row.values()))
        return dict(db.execute('SELECT * FROM leads WHERE id=?', (row['id'],)).fetchone())


def _get(crm, lid):
    with crm.get_db() as db:
        return dict(db.execute('SELECT * FROM leads WHERE id=?', (lid,)).fetchone())


ANSWER = {'decision_maker': {'name': 'Rev. Dr. John A. Smith', 'title': 'Senior Pastor',
                             'email': 'John@GraceLoveland.org', 'phone': 'unknown'},
          'org_email': 'office@graceloveland.org', 'news': 'unknown',
          'summary': 'Grace Church has served Loveland since 1952.'}
CITES = ['https://graceloveland.org/staff']


@pytest.mark.parametrize('full,expect', [
    ('Rev. Dr. John A. Smith', ('John', 'A. Smith')),
    ('Pastor Maria Lopez', ('Maria', 'Lopez')),
    ('Madonna', ('', '')),
    ('unknown', ('', '')),
])
def test_split_name(full, expect):
    assert reenrich.split_name(reenrich._known(full)) == expect


def test_a_cited_answer_fills_empty_contact_fields(crm):
    lead = _lead(crm)
    reenrich.apply(crm, lead, dict(ANSWER), CITES)
    got = _get(crm, lead['id'])
    assert (got['first_name'], got['last_name']) == ('John', 'A. Smith')
    assert got['email'] == 'john@graceloveland.org'
    assert got['email_norm'] == 'john@graceloveland.org'
    assert got['enriched_at']


def test_an_uncited_answer_fills_nothing(crm):
    lead = _lead(crm)
    reenrich.apply(crm, lead, dict(ANSWER), [])
    got = _get(crm, lead['id'])
    assert got['first_name'] == '' and got['email'] == ''
    assert got['enriched_at']                    # still marked researched


def test_research_never_overwrites(crm):
    lead = _lead(crm, first_name='Pat', email='pat@example.com')
    reenrich.apply(crm, lead, dict(ANSWER), CITES)
    got = _get(crm, lead['id'])
    assert (got['first_name'], got['email']) == ('Pat', 'pat@example.com')


def test_an_opted_out_email_is_never_filled(crm):
    lead = _lead(crm)
    with crm.get_db() as db:
        db.execute("INSERT INTO suppressions (id, kind, value, created_by, created_at) "
                   "VALUES ('s1','email','john@graceloveland.org','luke',?)", (crm._now(),))
    reenrich.apply(crm, lead, dict(ANSWER), CITES)
    assert _get(crm, lead['id'])['email'] == ''


def test_the_rep_is_told_it_came_from_research(crm):
    lead = _lead(crm)
    reenrich.apply(crm, lead, dict(ANSWER), CITES)
    with crm.get_db() as db:
        body = db.execute("SELECT body FROM activities WHERE lead_id=? AND kind='system'",
                          (lead['id'],)).fetchone()[0]
    assert 'verify' in body


def test_dry_run_writes_nothing(crm):
    lead = _lead(crm)
    fill = reenrich.apply(crm, lead, dict(ANSWER), CITES, dry_run=True)
    assert fill['first_name'] == 'John'
    assert _get(crm, lead['id'])['enriched_at'] == ''


def test_research_can_be_aimed_at_one_reps_leads(crm):
    """A refill goes to whoever is short, not to whoever's leads score highest."""
    with crm.get_db() as db:
        db.execute('DELETE FROM leads')
    _lead(crm, company='Luke Church', rep='luke', icp_score=6)
    _lead(crm, company='Derik Church', rep='derik', icp_score=1)
    assert [l['company'] for l in reenrich.candidates(crm, 10, rep='derik')] == ['Derik Church']
    assert len(reenrich.candidates(crm, 10)) == 2


def test_a_run_stops_within_one_lookup_of_its_budget(crm, monkeypatch):
    """The weekly cap reaches a run as a budget. Three cents of budget at two
    cents a lookup is two lookups, not the five that were asked for."""
    with crm.get_db() as db:
        db.execute('DELETE FROM leads')
    for i in range(5):
        _lead(crm, company=f'Church {i}')
    monkeypatch.setattr(reenrich, 'research', lambda lead: (dict(ANSWER), CITES, 0.02))
    lines = []
    out = reenrich.run(crm, limit=5, log=lines.append, budget=0.03)
    assert round(out['spent'], 2) == 0.04 and out['names'] == 2
    assert any('Budget' in line for line in lines)
    with crm.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM leads WHERE enriched_at != ''").fetchone()[0] == 2


def test_the_week_is_the_last_seven_days_of_research_only(tmp_path, monkeypatch):
    """A rolling window, so Friday's run and Monday's are one week's money. And
    research alone: an events search must not use up the refill."""
    from datetime import datetime, timedelta
    from agents import config
    monkeypatch.setenv('AGENTS_DATA_DIR', str(tmp_path))
    stamp = lambda days: (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    with config.get_cache_db() as db:
        for when, reason, cost in ((stamp(1), reenrich.REASON, 4.0),
                                   (stamp(6), reenrich.REASON, 5.0),
                                   (stamp(8), reenrich.REASON, 7.0),
                                   (stamp(1), 'events', 9.0)):
            db.execute('INSERT INTO spend_ledger (occurred_at, source, reason, cost_usd) '
                       "VALUES (?, 'perplexity', ?, ?)", (when, reason, cost))
        db.commit()
    assert reenrich.week_spend_usd() == 9.0


def test_the_spend_cap_stops_the_run_cleanly(crm, monkeypatch):
    _lead(crm, company='Capped Church')
    def capped(*a, **k):
        raise perplexity.SpendCapReached('cap')
    monkeypatch.setattr(perplexity, 'search_json', capped)
    out = reenrich.run(crm, limit=5, log=lambda *_: None)
    assert out['names'] == out['emails'] == 0


def test_a_partner_is_asked_for_the_person_who_refers_not_the_one_who_fixes():
    """A brokerage's facilities contact is its office manager."""
    realtor = reenrich._prompt({'company': 'Park Realty', 'lead_type': 'realtor'})
    assert 'managing broker' in realtor and 'facilities' not in realtor
    assert 'real estate brokerage' in realtor
    church = reenrich._prompt({'company': 'Grace Church', 'lead_type': 'church'})
    assert 'facilities' in church and 'senior pastor' in church
