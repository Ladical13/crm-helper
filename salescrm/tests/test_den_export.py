"""The pull half: shaping Base44's two entities into one row per person.

The network call itself is not tested — it needs a token and a live API, which
is exactly why it is quarantined in `fetch()` and everything worth getting
wrong lives in `build()`.
"""
from salescrm import den_export


def test_projects_are_attached_to_their_contact():
    rows = den_export.build(
        [{'id': 'c1'}, {'id': 'c2'}],
        [{'id': 'p1', 'contact_id': 'c1'}, {'id': 'p2', 'contact_id': 'c1'},
         {'id': 'p3', 'contact_id': 'c2'}])
    by_id = {r['id']: r for r in rows}
    assert [p['id'] for p in by_id['c1']['projects']] == ['p1', 'p2']
    assert [p['id'] for p in by_id['c2']['projects']] == ['p3']


def test_a_contact_with_no_projects_still_comes_through():
    """They are not a past customer, but they are still a person we hold."""
    rows = den_export.build([{'id': 'c1'}], [])
    assert rows[0]['projects'] == []


def test_the_other_market_is_left_behind():
    """The Den holds Tyler/Longview as well. Importing those into a Northern
    Colorado pipeline would put every one of them under the next Front Range
    hail swath."""
    rows = den_export.build(
        [{'id': 'co', 'location_id': 'CO1'}, {'id': 'tx', 'location_id': 'TX1'}],
        [], location_id='CO1')
    assert [r['id'] for r in rows] == ['co']


def test_no_location_filter_takes_everything():
    rows = den_export.build([{'id': 'a', 'location_id': 'CO1'},
                             {'id': 'b', 'location_id': 'TX1'}], [])
    assert len(rows) == 2


def test_a_contact_with_no_location_is_kept():
    """Older Den rows predate the location field, and dropping them would
    silently lose the oldest customers -- the ones this import is for."""
    rows = den_export.build([{'id': 'old'}], [], location_id='CO1')
    assert [r['id'] for r in rows] == ['old']
