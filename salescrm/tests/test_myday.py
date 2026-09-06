"""My Day's numbers are the pipeline's, not a page of it.

It fetched /api/leads?limit=1000 and counted the page in the browser. Two things
were wrong and both get worse as the business grows: "Open leads" and
"Pipeline $" silently capped at 1,000 — which a rep passes on their first
imported batch — and the first screen of the morning pulled a thousand rows over
a phone connection in a driveway to compute five numbers SQL can return.
"""
from conftest import signup, login, new_lead
import app as appmod


def _seed(n, stage='new', value=100.0, temperature='warm', last_activity=None):
    now = appmod._now()
    with appmod.get_db() as db:
        db.executemany(
            'INSERT INTO leads (id, lead_type, service, stage, entry_stage, rep, '
            'est_value, temperature, created_at, updated_at, last_activity_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            [(f'{stage}{temperature}{i}', 'homeowner', 'roofing', stage, stage, 'luke',
              value, temperature, now, now, last_activity if last_activity is not None else now)
             for i in range(n)])


def test_open_count_and_value_reach_past_the_old_page(client):
    signup(client)
    _seed(1200, value=1000.0)
    m = client.get('/api/myday').get_json()
    assert m['open_count'] == 1200, 'this reported 1000 — the size of the page'
    assert m['pipeline_value'] == 1200 * 1000.0


def test_terminal_leads_are_not_open_pipeline(client):
    signup(client)
    _seed(3, stage='won', value=5000.0)
    _seed(2, stage='contacted', value=1000.0)
    m = client.get('/api/myday').get_json()
    assert m['open_count'] == 2
    assert m['pipeline_value'] == 2000.0


def test_hot_is_counted_in_full_and_listed_in_part(client):
    """The count must be true; the list only has to be actionable."""
    signup(client)
    _seed(40, temperature='hot')
    m = client.get('/api/myday').get_json()
    assert m['hot_count'] == 40
    assert len(m['hot']) == 25


def test_the_oldest_stalled_lead_is_the_one_shown(client):
    """Stalled is a date rule, applied in SQL. Filtering a recency-ordered page
    drops the oldest first — which is exactly the lead a rep needs to see."""
    signup(client)
    old = appmod._iso(appmod._now_dt() - appmod.timedelta(days=90))
    _seed(30, stage='contacted', last_activity=old)
    m = client.get('/api/myday').get_json()
    assert m['stalled_count'] == 30
    assert len(m['stalled']) == 25
    assert all(x['stalled'] for x in m['stalled'])


def test_a_fresh_lead_is_not_stalled(client):
    signup(client)
    new_lead(client)
    assert client.get('/api/myday').get_json()['stalled_count'] == 0


def test_sidebar_counts_are_the_whole_pipeline(client):
    """They were tallied from the cached page, so a rep holding 36,000 leads
    saw "New 1,000" on the one panel that says how much work is in front of
    them."""
    signup(client)
    _seed(1100)
    _seed(5, stage='won')
    counts = client.get('/api/myday').get_json()['stage_counts']
    assert counts['new'] == 1100
    assert counts['won'] == 5
    assert counts['appt_set'] == 0, 'every stage is present, not just the seen ones'


def test_a_rep_sees_only_their_own_day(client):
    signup(client, 'luke')                        # manager
    signup(client, 'casey')
    login(client, 'luke')
    new_lead(client, est_value=9000)
    login(client, 'casey')
    m = client.get('/api/myday').get_json()
    assert m['open_count'] == 0 and m['pipeline_value'] == 0
    assert m['stage_counts']['new'] == 0
