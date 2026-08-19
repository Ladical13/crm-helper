"""The two free B2B sources: IRS BMF (churches) and NCES/CCD (schools).

No network — both are exercised against recorded response shapes.

These were placeholders returning ``[]``, which meant every church, school and
GC lead fell through to paid Perplexity. The tests that matter most are the
fail-safe ones: a source that raises must return ``[]`` so the dispatcher still
falls through, exactly as it did before. Wiring a free source must never be
able to break a run that used to work.
"""
import json

import pytest

from agents.b2b.sources import irs_bmf, nces


class FakeResp:
    def __init__(self, payload='', status=200):
        self._payload = payload
        self.status_code = status
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        if isinstance(self._payload, str):
            return json.loads(self._payload)
        return self._payload


class FakeSession:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp


# ── IRS BMF ──────────────────────────────────────────────────────────────────

_BMF = (
    'EIN,NAME,STREET,CITY,STATE,ZIP,NTEE_CD,ASSET_AMT\n'
    '840123456,FIRST BAPTIST CHURCH OF GREELEY,1200 8TH AVE,GREELEY,CO,80631-1234,X21,850000\n'
    '840222222,ST MARY CATHOLIC PARISH,300 MAIN ST,FORT COLLINS,CO,80521,X22,120000\n'
    '840333333,LITTLE FLOCK FELLOWSHIP,,FORT COLLINS,CO,80524,X20,0\n'
    # Not a congregation — an animal shelter. Must not be pulled in.
    '840444444,LARIMER HUMANE SOCIETY,3501 E 71ST ST,LOVELAND,CO,80538,D20,900000\n'
    # No EIN: unusable as a dedupe key, so it must be dropped rather than
    # imported to duplicate on every future run.
    ',NAMELESS CHAPEL,1 A ST,GREELEY,CO,80631,X20,1000\n'
)


def test_only_congregations_are_returned():
    rows = irs_bmf.churches(session=FakeSession(FakeResp(_BMF)))
    names = {r['company'] for r in rows}
    assert 'Larimer Humane Society' not in names
    assert len(rows) == 3


def test_a_row_without_an_ein_is_dropped():
    """source_ref is the only stable dedupe key for a BMF row — most carry no
    phone or email. Importing one without it duplicates on every re-run."""
    rows = irs_bmf.churches(session=FakeSession(FakeResp(_BMF)))
    assert all(r['source_ref'] and r['source_ref'] != 'irs:bmf:' for r in rows)
    assert 'Nameless Chapel' not in {r['company'] for r in rows}


def test_source_ref_is_the_ein_and_is_stable():
    rows = irs_bmf.churches(session=FakeSession(FakeResp(_BMF)))
    first = next(r for r in rows if r['company'].startswith('First Baptist'))
    assert first['source_ref'] == 'irs:bmf:840123456'


def test_shouted_names_and_addresses_are_title_cased():
    """BMF ships everything upper case. A rep reading 'FIRST BAPTIST CHURCH OF
    GREELEY' off a card is reading a database dump, not a lead."""
    rows = irs_bmf.churches(session=FakeSession(FakeResp(_BMF)))
    assert 'First Baptist Church Of Greeley' in {r['company'] for r in rows}


def test_zip_plus_four_is_trimmed():
    rows = irs_bmf.churches(session=FakeSession(FakeResp(_BMF)))
    first = next(r for r in rows if r['source_ref'] == 'irs:bmf:840123456')
    assert first['zip'] == '80631'


def test_city_filter_matches_exactly():
    rows = irs_bmf.churches(city='Fort Collins', session=FakeSession(FakeResp(_BMF)))
    assert {r['city'] for r in rows} == {'Fort Collins'}
    assert len(rows) == 2


def test_property_owning_congregations_rank_first():
    """Asset size is the proxy for 'owns the building', which is the only kind
    of congregation that can authorise a re-roof."""
    rows = irs_bmf.churches(session=FakeSession(FakeResp(_BMF)))
    assert rows[0]['company'].startswith('First Baptist')
    assert rows[0]['icp_score'] > rows[-1]['icp_score']


def test_limit_is_honoured():
    rows = irs_bmf.churches(limit=1, session=FakeSession(FakeResp(_BMF)))
    assert len(rows) == 1


@pytest.mark.parametrize('resp', [
    FakeResp('', status=404),
    FakeResp('', status=500),
    RuntimeError('connection reset'),
])
def test_a_failed_pull_falls_through_instead_of_raising(resp):
    """The dispatcher treats [] as 'try Perplexity'. Raising here would abort a
    whole rep's run over a free source that is a bonus, not a dependency."""
    assert irs_bmf.churches(session=FakeSession(resp)) == []


def test_malformed_csv_keeps_whatever_parsed():
    rows = irs_bmf.churches(session=FakeSession(FakeResp('NOT,A,BMF\n1,2,3\n')))
    assert rows == []


# ── NCES / CCD ───────────────────────────────────────────────────────────────

_CCD = {'results': [
    {'ncessch': '080001000123', 'school_name': 'Fort Collins High School',
     'street_mailing': '3400 Lambkin Way', 'city_mailing': 'Fort Collins',
     'state_mailing': 'CO', 'zip_mailing': '80525', 'phone': '9704885000',
     'county_name': 'Larimer', 'enrollment': 1800},
    {'ncessch': '080001000456', 'school_name': 'Bauder Elementary',
     'street_mailing': '2345 W Prospect Rd', 'city_mailing': 'Fort Collins',
     'state_mailing': 'CO', 'zip_mailing': '80526', 'phone': '9704887400',
     'county_name': 'Larimer', 'enrollment': 420},
    {'ncessch': '080002000789', 'school_name': 'Greeley West High School',
     'street_mailing': '2401 35th Ave', 'city_mailing': 'Greeley',
     'state_mailing': 'CO', 'zip_mailing': '80634', 'phone': '9703488200',
     'county_name': 'Weld', 'enrollment': 1500},
    # No id — unusable as a dedupe key.
    {'ncessch': '', 'school_name': 'Ghost Academy', 'city_mailing': 'Greeley'},
], 'next': None}


def test_schools_are_normalised_into_importer_shape():
    rows = nces.schools(session=FakeSession(FakeResp(_CCD)))
    assert len(rows) == 3
    row = next(r for r in rows if r['company'] == 'Bauder Elementary')
    assert row['city'] == 'Fort Collins'
    assert row['state'] == 'CO'
    assert row['source_ref'] == 'nces:ccd:080001000456'


def test_a_school_without_an_id_is_dropped():
    rows = nces.schools(session=FakeSession(FakeResp(_CCD)))
    assert 'Ghost Academy' not in {r['company'] for r in rows}


def test_bigger_schools_rank_first():
    """Enrollment is the honest proxy for roof area and facilities budget."""
    rows = nces.schools(session=FakeSession(FakeResp(_CCD)))
    assert rows[0]['company'] == 'Fort Collins High School'


def test_county_filter_applies():
    rows = nces.schools(county='Weld', session=FakeSession(FakeResp(_CCD)))
    assert {r['company'] for r in rows} == {'Greeley West High School'}


def test_city_filter_applies():
    rows = nces.schools(city='Greeley', session=FakeSession(FakeResp(_CCD)))
    assert {r['company'] for r in rows} == {'Greeley West High School'}


def test_colorado_fips_is_sent():
    sess = FakeSession(FakeResp(_CCD))
    nces.schools(session=sess)
    assert sess.calls[0][1]['params']['fips'] == 8


def test_an_unknown_state_returns_nothing_rather_than_guessing():
    assert nces.schools(state='ZZ', session=FakeSession(FakeResp(_CCD))) == []


@pytest.mark.parametrize('resp', [
    FakeResp({}, status=503),
    RuntimeError('timeout'),
])
def test_a_failed_school_pull_falls_through(resp):
    assert nces.schools(session=FakeSession(resp)) == []


def test_pagination_stops_rather_than_looping_forever():
    """A `next` that points at itself must not spin the run forever."""
    class Looping(FakeSession):
        def get(self, url, **kw):
            self.calls.append((url, kw))
            return FakeResp({'results': [], 'next': 'https://x.example/next'})
    sess = Looping(None)
    assert nces.schools(session=sess) == []
    assert len(sess.calls) <= nces.MAX_PAGES


# ── Wiring: the free source must be tried BEFORE the paid one ────────────────

def test_free_sources_are_ordered_ahead_of_perplexity():
    """The dispatcher stops at the first puller that returns rows. If a free
    source is not first, it can never save a cent no matter how well it works.
    """
    from agents.b2b import sources

    for segment, free in (('church', 'irs_bmf'), ('school', 'nces')):
        pullers = sources.pullers_for(segment)
        assert pullers, segment
        assert pullers[0].__module__.endswith(free), segment
        assert pullers[-1].__module__.endswith('perplexity_gap'), segment


def test_an_unknown_segment_still_reaches_perplexity():
    from agents.b2b import sources
    pullers = sources.pullers_for('llamas')
    assert [p.__module__.endswith('perplexity_gap') for p in pullers] == [True]
