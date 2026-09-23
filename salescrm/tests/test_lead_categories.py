"""The Pipeline's category tabs and its grouped-by-category list."""
from conftest import signup, new_lead


def _seed(client):
    signup(client)
    ids = {}
    for ltype in ('church', 'realtor', 'church', 'homeowner', 'insurance_agent', 'realtor'):
        ids.setdefault(ltype, []).append(
            new_lead(client, lead_type=ltype, first_name=ltype.title(),
                     city='Loveland' if ltype != 'homeowner' else 'Greeley')['id'])
    return ids


def test_type_counts_cover_every_category_under_the_other_filters(client):
    """A tab's count must match the rows that tab shows, and picking one
    category must not zero the others — so `type` itself is ignored."""
    _seed(client)
    counts = client.get('/api/leads/type-counts').get_json()
    assert counts == {'church': 2, 'realtor': 2, 'homeowner': 1, 'insurance_agent': 1}
    assert client.get('/api/leads/type-counts?type=church').get_json() == counts
    # The other filters DO apply, the same way the list applies them.
    assert client.get('/api/leads/type-counts?q=loveland').get_json() == {
        'church': 2, 'realtor': 2, 'insurance_agent': 1}
    for key, n in counts.items():
        assert len(client.get(f'/api/leads?type={key}').get_json()) == n


def test_grouped_list_pages_in_category_order(client):
    """Sorted by category on the SERVER, so "Show more" continues the group it
    was in instead of scattering a category across every page."""
    _seed(client)
    order = [l['lead_type'] for l in client.get('/api/leads?sort=type').get_json()]
    assert order == ['homeowner', 'realtor', 'realtor', 'insurance_agent', 'church', 'church']
    first = client.get('/api/leads?sort=type&limit=3').get_json()
    rest = client.get('/api/leads?sort=type&limit=3&offset=3').get_json()
    assert [l['lead_type'] for l in first + rest] == order


def test_reps_only_count_their_own_leads(client):
    from conftest import logout
    _seed(client)
    logout(client)
    signup(client, username='rep2', code='')
    new_lead(client, lead_type='school')
    assert client.get('/api/leads/type-counts').get_json() == {'school': 1}
