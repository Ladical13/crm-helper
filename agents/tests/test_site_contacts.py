"""Reading an organisation's own website, and the free Secretary of State names.

The website reader's rules are the whole point: own-domain emails only, the
named contact's address before a shared inbox, and NEVER some other staff
member's personal address. It only fills empty fields, and honours robots.txt.
"""
import importlib.util
import os
import sys
import tempfile
import uuid

import pytest

from agents.b2b import reenrich, site_contacts as sc, sos_backfill

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope='module')
def crm():
    d = tempfile.mkdtemp(prefix='sitecontacts_')
    saved = {k: os.environ.get(k) for k in ('SALESCRM_DATA_DIR', 'PORTAL_DATA_DIR')}
    os.environ['SALESCRM_DATA_DIR'] = d
    os.environ['PORTAL_DATA_DIR'] = d
    sys.path.insert(0, REPO)
    spec = importlib.util.spec_from_file_location(
        'sitecontacts_crm_app', os.path.join(REPO, 'salescrm', 'app.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules['sitecontacts_crm_app'] = mod
    spec.loader.exec_module(mod)
    yield mod
    for k, v in saved.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _lead(crm, **kw):
    row = dict(id=str(uuid.uuid4()), lead_type='church', company='Grace Church', city='Loveland',
               rep='luke', stage='new', website='https://grace.org', created_at=crm._now(),
               updated_at=crm._now())
    row.update(kw)
    with crm.get_db() as db:
        db.execute(f'INSERT INTO leads ({",".join(row)}) VALUES ({",".join("?" * len(row))})',
                   list(row.values()))
        return dict(db.execute('SELECT * FROM leads WHERE id=?', (row['id'],)).fetchone())


def _get(crm, lid):
    with crm.get_db() as db:
        return dict(db.execute('SELECT * FROM leads WHERE id=?', (lid,)).fetchone())


HOME = '''<html><body><a href="/about-us">About</a> <a href="/staff">Our Staff</a>
<a href="https://facebook.com/grace">fb</a> <a href="/sermons">Sermons</a>
<p>Call us: (970) 555-0100 &middot; 1418 Sycamore Ct, Loveland</p></body></html>'''
STAFF = '''<html><body>
<p>Pastor John Smith - <a href="mailto:jsmith@grace.org">email</a></p>
<p>Youth: Kyle Brown kbrown@grace.org</p>
<p>Office: office [at] grace [dot] org, <a href="tel:+19705550199">call</a></p>
<p>Our web host: support@wixpress.com  logo@2x.png</p></body></html>'''
PAGES = {'https://grace.org': HOME, 'https://grace.org/staff': STAFF,
         'https://grace.org/about-us': '<p>Since 1952.</p>'}


def _fetch(url):
    return PAGES.get(url)


def test_parse_finds_obfuscated_emails_phones_and_only_useful_same_site_links():
    emails, phones, links = sc.parse(STAFF, 'https://grace.org/staff')
    assert 'office@grace.org' in emails and 'jsmith@grace.org' in emails
    assert not any('wixpress' in e or '.png' in e for e in emails)
    assert '(970) 555-0199' in phones
    _, _, home_links = sc.parse(HOME, 'https://grace.org')
    assert home_links == ['https://grace.org/about-us', 'https://grace.org/staff']


def test_the_named_contacts_own_address_wins():
    emails = ['kbrown@grace.org', 'jsmith@grace.org', 'office@grace.org']
    assert sc.pick_email(emails, 'grace.org', 'John', 'Smith') == 'jsmith@grace.org'


def test_without_a_name_only_a_shared_inbox_is_used_never_a_staffers_own():
    assert sc.pick_email(['kbrown@grace.org', 'office@grace.org'], 'grace.org') == 'office@grace.org'
    assert sc.pick_email(['kbrown@grace.org'], 'grace.org') == ''


def test_another_domains_address_is_never_used():
    assert sc.pick_email(['jsmith@gmail.com'], 'grace.org', 'John', 'Smith') == ''


def test_reading_a_site_fills_only_empty_fields(crm):
    lead = _lead(crm, first_name='John', last_name='Smith')
    found = sc.read_site('https://grace.org', fetch=_fetch, sleep=lambda *_: None)
    fill = sc.apply(crm, lead, found)
    got = _get(crm, lead['id'])
    assert fill == {'email': 'jsmith@grace.org', 'phone': '(970) 555-0100'}
    assert got['email'] == 'jsmith@grace.org' and got['phone_norm'] == '9705550100'
    assert got['site_checked_at'] and got['contact_quality'] == 2

    kept = _lead(crm, email='pastor@grace.org', phone='970-555-1111')
    assert sc.apply(crm, kept, found) == {}
    assert _get(crm, kept['id'])['email'] == 'pastor@grace.org'


def test_a_site_read_this_quarter_is_not_read_again(crm):
    lead = _lead(crm, company='Once Church')
    sc.apply(crm, lead, {'emails': [], 'phones': [], 'pages': []})
    assert lead['id'] not in [c['id'] for c in sc.candidates(crm, 500)]


def test_robots_txt_is_honoured(monkeypatch):
    calls = []

    class R:
        def __init__(self, text, status=200, ctype='text/html'):
            self.text, self.status_code, self.headers = text, status, {'content-type': ctype}

    def get(url, **kw):
        calls.append(url)
        if url.endswith('/robots.txt'):
            return R('User-agent: *\nDisallow: /', ctype='text/plain')
        return R(HOME)
    import requests
    monkeypatch.setattr(requests, 'get', get)
    assert sc._fetcher('https://grace.org')('https://grace.org/staff') is None
    assert calls == ['https://grace.org/robots.txt']


def test_sos_names_commercial_leads_that_are_still_just_an_llc(crm):
    a = _lead(crm, lead_type='commercial', company='JAX PROPERTIES LLC')
    b = _lead(crm, lead_type='commercial', company='Named Co', first_name='Pat')
    r = sos_backfill.run(crm, log=lambda *_: None,
                         lookup=lambda names: {'JAX PROPERTIES LLC': {'first_name': 'Jane',
                                                                      'last_name': 'Jax'}})
    assert r['named'] == 1
    assert (_get(crm, a['id'])['first_name'], _get(crm, a['id'])['contact_source']) == ('Jane', 'research')
    assert _get(crm, b['id'])['first_name'] == 'Pat'


# ── Research now asks for the phone and the website ─────────────────────────

def test_research_fills_phone_and_website_when_cited():
    out = reenrich.contact_fields(
        {'decision_maker': {'name': 'unknown'}, 'org_phone': '+1 970-555-0100',
         'website': 'grace.org/'}, ['https://grace.org'])
    assert out == {'phone': '(970) 555-0100', 'website': 'https://grace.org'}


@pytest.mark.parametrize('site', ['https://www.facebook.com/grace', 'yelp.com/biz/grace', 'unknown'])
def test_a_directory_or_social_page_is_not_a_website(site):
    assert reenrich._website(site) == ''


def test_missing_mode_revisits_researched_leads_without_a_way_in(crm):
    lead = _lead(crm, company='Revisit Church', website='', enriched_at='2026-08-01T00:00:00Z')
    done = _lead(crm, company='Done Church', enriched_at='2026-08-01T00:00:00Z',
                 phone='970', email='a@done.org')
    ids = [c['id'] for c in reenrich.candidates(crm, 500, mode='missing')]
    assert lead['id'] in ids and done['id'] not in ids


def test_a_same_named_organisation_in_another_state_is_not_used(crm):
    """Shepherd of the Hills, Austin TX - not the Fort Collins church research
    was looking for. Nothing is taken, and the rep is told to check."""
    texas = {'https://grace.org': '<p>Grace Church, Austin, Texas. (512) 327-3370 '
                                  '<a href="mailto:office@grace.org">x</a></p>'}
    lead = _lead(crm, company='Texas Grace')
    found = sc.read_site('https://grace.org', fetch=texas.get, sleep=lambda *_: None)
    assert sc.apply(crm, lead, found) == {}
    got = _get(crm, lead['id'])
    assert got['email'] == '' and got['phone'] == '' and got['site_checked_at']
    with crm.get_db() as db:
        note = db.execute("SELECT body FROM activities WHERE lead_id=?", (lead['id'],)).fetchone()[0]
    assert 'different organisation' in note


def test_an_out_of_state_number_is_never_filled(crm):
    page = {'https://grace.org': '<p>Loveland, Colorado. Call (512) 327-3370</p>'}
    lead = _lead(crm, company='Area Code Church')
    found = sc.read_site('https://grace.org', fetch=page.get, sleep=lambda *_: None)
    assert 'phone' not in sc.apply(crm, lead, found)


def test_research_ignores_an_out_of_state_phone():
    out = reenrich.contact_fields({'org_phone': '(512) 327-3370'}, ['https://x.org'])
    assert 'phone' not in out
