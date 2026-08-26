"""The free open-data B2B sources: IRS BMF churches and NCES CCD schools.

No network. Both are exercised against recorded slices of the real payloads,
so the suite is deterministic and costs nothing.

These two exist because the b2b run was buying every lead from Perplexity: on
a live sample, four of five rows had no phone number and none had an email.
The IRS and NCES files are free, authoritative, and carry a federal identifier
that does not drift between runs the way a model's spelling of a name does.
"""
import json

import pytest


# ── IRS Exempt Organizations BMF ─────────────────────────────────────────────

# Real column order from https://www.irs.gov/pub/irs-soi/eo_co.csv.
_BMF_HEADER = ('EIN,NAME,ICO,STREET,CITY,STATE,ZIP,GROUP,SUBSECTION,AFFILIATION,'
               'CLASSIFICATION,RULING,DEDUCTIBILITY,FOUNDATION,ACTIVITY,'
               'ORGANIZATION,STATUS,TAX_PERIOD,ASSET_CD,INCOME_CD,FILING_REQ_CD,'
               'PF_FILING_REQ_CD,ACCT_PD,ASSET_AMT,INCOME_AMT,REVENUE_AMT,'
               'NTEE_CD,SORT_NAME')


def _bmf_row(ein, name, street, city='FORT COLLINS', ntee='X20', status='01'):
    cells = [''] * 28
    cells[0], cells[1], cells[3] = ein, name, street
    cells[4], cells[5], cells[6] = city, 'CO', '80525-7076'
    cells[16] = status
    cells[26] = ntee
    return ','.join(cells)


_BMF_CSV = '\n'.join([
    _BMF_HEADER,
    # Coded as a congregation.
    _bmf_row('010916479', 'FORT COLLINS CHRISTIAN CHURCH', '7003 EGYPTIAN DR'),
    # No NTEE code at all, but unmistakably a church by name. The union of the
    # two tests is the whole point: on the live Colorado file, NTEE alone
    # missed 1,830 of these and the name test alone missed 1,484 coded ones.
    _bmf_row('200109866', 'GRACE CHAPEL OF THE ROCKIES', '300 WHEDBEE ST', ntee=''),
    # A PO box cannot be roofed, measured, or knocked on.
    _bmf_row('111111111', 'MAILBOX BAPTIST CHURCH', 'PO BOX 8210'),
    # Revoked exemption — not somebody to go and see.
    _bmf_row('222222222', 'DEFUNCT COMMUNITY CHURCH', '1 REAL ST', status='25'),
    # Not a church at all.
    _bmf_row('333333333', 'LARIMER CYCLING CLUB', '9 BIKE LN', ntee='N60'),
    # Right shape, wrong town.
    _bmf_row('444444444', 'PUEBLO FIRST CHURCH', '5 SOUTH ST', city='PUEBLO'),
])


@pytest.fixture
def bmf(monkeypatch):
    from agents.b2b.sources import _common, irs_bmf
    monkeypatch.setattr(_common, 'fetch_cached', lambda *a, **k: _BMF_CSV)
    return irs_bmf


def test_churches_come_from_the_ntee_code_or_the_name(bmf):
    names = {r['company'] for r in bmf.churches(city='Fort Collins')}
    assert 'Fort Collins Christian Church' in names   # by NTEE
    assert 'Grace Chapel Of The Rockies' in names     # by name, no NTEE
    assert 'Larimer Cycling Club' not in names


def test_a_po_box_is_not_a_roof(bmf):
    names = {r['company'] for r in bmf.churches(city='Fort Collins')}
    assert 'Mailbox Baptist Church' not in names


def test_a_revoked_exemption_is_not_a_prospect(bmf):
    names = {r['company'] for r in bmf.churches(city='Fort Collins')}
    assert 'Defunct Community Church' not in names


def test_the_city_filter_actually_filters(bmf):
    names = {r['company'] for r in bmf.churches(city='Fort Collins')}
    assert 'Pueblo First Church' not in names
    assert bmf.churches(city='Pueblo')[0]['company'] == 'Pueblo First Church'


def test_the_ein_is_the_dedupe_key(bmf):
    """A federal identifier does not drift between runs; a model's spelling of
    an organization's name does. Re-running a pull must not create doubles."""
    row = bmf.churches(city='Fort Collins')[0]
    assert row['source_ref'] == 'irs_bmf:co:010916479'
    assert row['license_no'] == '010916479'
    assert bmf.churches(city='Fort Collins')[0]['source_ref'] == row['source_ref']


def test_rows_are_not_shouted_at_the_rep(bmf):
    row = bmf.churches(city='Fort Collins')[0]
    assert row['company'] == 'Fort Collins Christian Church'
    assert row['address'] == '7003 Egyptian Dr'
    assert row['city'] == 'Fort Collins'


def test_a_dead_download_yields_no_rows_rather_than_an_exception(monkeypatch):
    """The dispatcher's contract with every free source: returning nothing
    costs the run one input and falls through to Perplexity. Raising would
    lose the whole segment."""
    from agents.b2b.sources import _common, irs_bmf, nces
    monkeypatch.setattr(_common, 'fetch_cached', lambda *a, **k: None)
    assert irs_bmf.churches(city='Fort Collins') == []
    assert nces.schools(city='Fort Collins') == []


# ── NCES Common Core of Data ─────────────────────────────────────────────────

_CCD = {'results': [
    {'ncessch': '080399000545', 'school_name': 'ROCKY MOUNTAIN HIGH SCHOOL',
     'lea_name': 'Poudre School District R-1', 'phone': '(970)488-7023',
     'street_location': '1300 WEST SWALLOW ROAD', 'city_location': 'FORT COLLINS',
     'zip_location': '80526', 'state_location': 'CO',
     'enrollment': 2069, 'school_status': 1},
    {'ncessch': '080399000999', 'school_name': 'TINY CHARTER ACADEMY',
     'lea_name': 'Poudre School District R-1', 'phone': '(970)555-0000',
     'street_location': '12 SMALL ST', 'city_location': 'FORT COLLINS',
     'zip_location': '80525', 'state_location': 'CO',
     'enrollment': 60, 'school_status': 1},
    # Closed: no live roof to sell.
    {'ncessch': '080399000111', 'school_name': 'SHUTTERED ELEMENTARY',
     'lea_name': 'Poudre School District R-1', 'phone': '(970)555-1111',
     'street_location': '9 GONE AVE', 'city_location': 'FORT COLLINS',
     'zip_location': '80525', 'state_location': 'CO',
     'enrollment': 0, 'school_status': 2},
    # Only a mailing PO box — nothing a rep can visit.
    {'ncessch': '080399000222', 'school_name': 'POBOX SCHOOL',
     'lea_name': 'Somewhere', 'phone': '(970)555-2222',
     'street_mailing': 'P O BOX 577', 'city_mailing': 'FORT COLLINS',
     'zip_mailing': '80525', 'state_location': 'CO',
     'enrollment': 110, 'school_status': 1},
]}


@pytest.fixture
def ccd(monkeypatch):
    from agents.b2b.sources import _common, nces
    monkeypatch.setattr(_common, 'fetch_cached',
                        lambda *a, **k: json.dumps(_CCD))
    return nces


def test_schools_arrive_with_a_phone_number(ccd):
    """The reason this source is worth more than the Perplexity call it
    replaces: CCD carries phones, and Perplexity mostly did not."""
    rows = ccd.schools(city='Fort Collins')
    assert rows
    assert all(r['phone'] for r in rows)


def test_a_closed_school_is_not_a_prospect(ccd):
    names = {r['company'] for r in ccd.schools(city='Fort Collins')}
    assert 'Shuttered Elementary' not in names


def test_a_mailing_po_box_is_dropped_like_any_other(ccd):
    names = {r['company'] for r in ccd.schools(city='Fort Collins')}
    assert 'Pobox School' not in names


def test_the_bigger_roof_ranks_first(ccd):
    """Pupil count is the one size signal available before paying to enrich,
    and on a roof it is a decent proxy for square footage."""
    rows = ccd.schools(city='Fort Collins')
    assert rows[0]['company'] == 'Rocky Mountain High School'
    assert rows[0]['icp_score'] > rows[-1]['icp_score']
    assert '2069 students' in rows[0]['hook']


def test_the_ncessch_id_is_the_dedupe_key(ccd):
    row = ccd.schools(city='Fort Collins')[0]
    assert row['source_ref'] == 'nces:080399000545'
    assert row['license_no'] == '080399000545'


# ── Shared helpers ───────────────────────────────────────────────────────────

def test_open_data_abbreviations_still_match_a_real_city_name():
    """The BMF writes Colorado Springs as 'COLORADO SPGS'. A rep filtering on
    the real name would otherwise see none of that city's rows."""
    from agents.b2b.sources import _common
    assert _common.matches_city('COLORADO SPGS', 'Colorado Springs')
    assert _common.matches_city('FORT COLLINS', 'Fort Collins')
    assert not _common.matches_city('PUEBLO', 'Fort Collins')
    assert not _common.matches_city('LOVELAND', 'Longmont')


def test_every_source_scores_reachability_the_same_way():
    """A free row and a Perplexity row land in one queue and get sorted
    against each other, so they must be scored by one function."""
    from agents.b2b.sources import _common, perplexity_gap
    assert perplexity_gap._base_icp_score is _common.base_icp_score
    assert perplexity_gap._clean is _common.clean


def test_already_cased_names_are_left_alone():
    """CCD names its schools properly. Re-casing them would turn
    "McKinley Elementary" into "Mckinley Elementary" — breaking correct data
    to fix a problem it does not have."""
    from agents.b2b.sources import _common
    assert _common.title('McKinley Elementary') == 'McKinley Elementary'
    assert _common.title("St. Mary's Academy") == "St. Mary's Academy"
    # Shouting still gets quietened.
    assert _common.title('ROCKY MOUNTAIN HIGH SCHOOL') == 'Rocky Mountain High School'
