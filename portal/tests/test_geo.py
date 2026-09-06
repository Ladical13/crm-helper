"""The address → coordinate layer that every hail join depends on.

A swath is a polygon and the only question worth asking of it — which of our
people are underneath — is point-in-polygon. That needs coordinates. These
tests hold down the four things that would make the join quietly wrong rather
than loudly broken.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from portal import geo  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_portal_db(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTAL_DATA_DIR', str(tmp_path))
    geo.reset_cache()
    yield
    geo.reset_cache()


# ── Normalization ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('a,b', [
    ('123 Main St', '123 MAIN STREET'),
    ('123 Main St.', '123 Main St'),
    ('  123   Main   St  ', '123 Main St'),
    ('123 North Main Street', '123 N Main St'),
    ('456 Oak Avenue', '456 OAK AVE'),
    ('789 Elm Boulevard', '789 elm blvd'),
])
def test_interchangeable_spellings_share_a_key(a, b):
    assert geo.norm_address(a) == geo.norm_address(b)


@pytest.mark.parametrize('a,b', [
    ('123 Main St', '123 Main St Apt 2'),
    ('123 Main St', '125 Main St'),
    ('123 Main St', '123 Main Ct'),
])
def test_genuinely_different_addresses_do_not_collide(a, b):
    """Conservative on purpose. Merging two households is how one person's
    roof ends up filed against another person's claim — a worse failure than
    paying to geocode the same street twice."""
    assert geo.norm_address(a) != geo.norm_address(b)


def test_an_empty_address_has_no_key():
    """A blank key would collide every address-less row in the database onto a
    single cache entry, and then hand all of them one arbitrary coordinate."""
    assert geo.norm_address('') == ''
    assert geo.norm_address(None, '', '   ') == ''
    assert geo.put('', lat=1, lng=2) == ''
    assert geo.counts() == {}


def test_parts_and_free_text_reach_the_same_key():
    assert (geo.norm_address('123 Main St', 'Fort Collins', 'CO', '80521')
            == geo.norm_address('123 Main St Fort Collins CO 80521'))


# ── The Census response ─────────────────────────────────────────────────────

MATCHED = (
    '"1","123 MAIN ST, FORT COLLINS, CO, 80521","Match","Exact",'
    '"123 MAIN ST, FORT COLLINS, CO, 80521","-105.0844,40.5853","12345","L"\n'
)


def test_census_coordinates_are_lon_lat():
    """The single most dangerous line in this module.

    Every other surface in this codebase writes lat first; the Census response
    writes lon first. Read it backwards and Fort Collins lands in the Indian
    Ocean — at which point the swath join matches nobody and reports zero
    affected customers, which reads exactly like a quiet storm.
    """
    parsed = geo.parse_census_csv(MATCHED)['1']
    assert parsed['status'] == 'ok'
    assert parsed['lat'] == pytest.approx(40.5853)   # Colorado, not the ocean
    assert parsed['lng'] == pytest.approx(-105.0844)
    assert 40 < parsed['lat'] < 41 and -106 < parsed['lng'] < -104


def test_a_tie_is_not_a_match():
    """A tie means several equally good candidates. Picking the first would put
    a real customer on a real, wrong roof."""
    csv_text = ('"7","400 Oak, Fort Collins, CO","Tie"\n')
    assert geo.parse_census_csv(csv_text)['7']['status'] == 'nomatch'


def test_a_no_match_is_recorded_not_dropped():
    csv_text = ('"3","Nowhere Rd, Fort Collins, CO","No_Match"\n')
    parsed = geo.parse_census_csv(csv_text)['3']
    assert parsed['status'] == 'nomatch'
    assert parsed['lat'] is None


def test_a_mangled_coordinate_pair_degrades_to_nomatch():
    csv_text = ('"4","123 Main St","Match","Exact","123 MAIN ST","garbage","1","L"\n')
    assert geo.parse_census_csv(csv_text)['4']['status'] == 'nomatch'


# ── Cache behaviour ─────────────────────────────────────────────────────────

def _fake_post(rows):
    """Stand-in transport: matches anything on 'MAIN', misses everything else."""
    out = []
    for row_id, street, city, state, zipcode in rows:
        if 'MAIN' in street.upper():
            out.append(f'"{row_id}","{street}","Match","Exact",'
                       f'"{street.upper()}","-105.0844,40.5853","1","L"')
        else:
            out.append(f'"{row_id}","{street}","No_Match"')
    return '\n'.join(out) + '\n'


def test_geocode_writes_hits_and_misses_and_lookup_reads_back():
    written = geo.geocode([
        ('123 Main St', 'Fort Collins', 'CO', '80521'),
        ('9 Nowhere Rd', 'Fort Collins', 'CO', '80521'),
    ], post=_fake_post)
    assert written == {'ok': 1, 'nomatch': 1}

    hit = geo.lookup('123 MAIN STREET', 'Fort Collins', 'CO', '80521')
    assert hit['lat'] == pytest.approx(40.5853)
    assert hit['source'] == 'census'

    # A miss is cached, but lookup still reports no coordinate — the caller
    # wanted a point, and "asked, no answer" is the backfill's business.
    assert geo.lookup('9 Nowhere Rd', 'Fort Collins', 'CO', '80521') is None
    assert geo.counts() == {'ok': 1, 'nomatch': 1}


def test_misses_are_cached_so_a_rerun_does_not_resend_them():
    """Re-sending every permanent failure on every run is how a batch job grows
    until it times out."""
    geo.geocode([('9 Nowhere Rd', 'Fort Collins', 'CO', '80521')], post=_fake_post)
    assert geo.stale_keys('nomatch') == geo.known_keys()

    calls = []

    def counting_post(rows):
        calls.append(len(rows))
        return _fake_post(rows)

    todo = [a for a in [('9 Nowhere Rd', 'Fort Collins', 'CO', '80521')]
            if geo.norm_address(*a) not in geo.known_keys()]
    assert todo == []
    geo.geocode(todo, post=counting_post)
    assert calls == []


def test_duplicate_addresses_are_sent_once():
    sent = []

    def counting_post(rows):
        sent.extend(rows)
        return _fake_post(rows)

    geo.geocode([
        ('123 Main St', 'Fort Collins', 'CO', '80521'),
        ('123 MAIN STREET', 'Fort Collins', 'CO', '80521'),
        ('123 main st.', 'Fort Collins', 'CO', '80521'),
    ], post=counting_post)
    assert len(sent) == 1, 'three spellings of one address should cost one lookup'


def test_a_pin_coordinate_outranks_nothing_but_is_kept_as_its_own_source():
    """Pins are seeded before the batch because a rep standing on the property
    beats a TIGER centerline interpolation — and costs nothing."""
    geo.put('123 Main St, Fort Collins, CO', lat=40.5, lng=-105.1,
            source='pin', status='ok')
    hit = geo.lookup('123 MAIN STREET, Fort Collins, CO')
    assert hit['source'] == 'pin'
    assert hit['lat'] == pytest.approx(40.5)


def test_batch_limit_is_the_documented_ceiling():
    """Send 10,001 and the endpoint rejects the whole batch rather than
    truncating it, so the chunking is not a nicety."""
    assert geo.BATCH_LIMIT == 10000
    chunks = list(geo._chunks(list(range(25000)), geo.BATCH_LIMIT))
    assert [len(c) for c in chunks] == [10000, 10000, 5000]


def test_we_do_not_geocode_through_nominatim():
    """OSM's policy is 1 req/sec and explicitly forbids bulk use. The canvasser
    already leans on it from one Railway IP; routing a 10k backfill through it
    would earn a ban on the tool reps use in driveways."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'geo.py'), encoding='utf-8').read()
    # The host, not the word — the module comment explains at length why we do
    # not use it, and a bare substring check would fail on its own reasoning.
    assert 'nominatim.openstreetmap.org' not in src.lower()
    assert 'geocoding.geo.census.gov' in src


# ── ZIP handling ────────────────────────────────────────────────────────────

def test_a_missing_zip_does_not_fork_the_key():
    """The failure this was written for.

    A canvasser pin's reverse-geocoded address has no zip; the same house as a
    CRM lead has one. Key on the zip and that house is two rows: geocoded
    twice, and the pin's free coordinate is invisible to the lead's lookup. The
    join then reports zero customers under a swath, which reads exactly like a
    quiet storm rather than like a bug.
    """
    assert (geo.norm_address('123 Main St, Fort Collins, CO')
            == geo.norm_address('123 Main St', 'Fort Collins', 'CO', '80521'))


def test_zip_plus_four_is_dropped_too():
    assert (geo.norm_address('123 Main St Fort Collins CO 80521-1234')
            == geo.norm_address('123 Main St Fort Collins CO'))


def test_a_five_digit_house_number_is_not_mistaken_for_a_zip():
    """Strip it and every address on the street collapses onto one key."""
    assert geo.norm_address('12345 Main St', 'Greeley', 'CO', '80631') \
        != geo.norm_address('Main St', 'Greeley', 'CO', '80631')
    assert geo.norm_address('12345 Main St Greeley CO').startswith('12345')


def test_a_bare_zip_survives_as_its_own_key():
    """Nothing useful, but it must not normalize to empty and collide with
    every other address-less row."""
    assert geo.norm_address('80521') == '80521'
