"""Of the doors we knocked, where do we lose people?

The dashboard's "Funnel" showed current stage counts, so a lead that went
new -> won appeared only under Won and the whole ladder above it read as empty.
The question the panel was named after had no answer in the tool -- while every
stage_change needed to compute it had been on the timeline the whole time.
"""
from conftest import signup, login, new_lead
import app as appmod


def _funnel(client, days=30, rep=None):
    qs = f'?days={days}' + (f'&rep={rep}' if rep else '')
    return client.get('/api/dashboard' + qs).get_json()['funnel']


def _reached(client, **kw):
    return {r['key']: r['reached'] for r in _funnel(client, **kw)['rungs']}


def _walk(client, lead, *stages):
    for st in stages:
        client.patch(f'/api/leads/{lead["id"]}/stage', json={'stage': st})


# ── Reaching a rung implies every rung below it ──────────────────────────────

def test_a_won_deal_counts_at_every_rung_it_passed(client):
    """The whole bug in one test: this lead used to appear only under Won."""
    signup(client)
    lead = new_lead(client)
    _walk(client, lead, 'contacted', 'appt_set', 'inspected',
          'estimate_presented', 'won')
    assert _reached(client) == {'new': 1, 'contacted': 1, 'appt_set': 1,
                                'inspected': 1, 'estimate_presented': 1, 'won': 1}


def test_a_skipped_stage_still_counts_the_ones_below(client):
    """Reps skip rungs constantly -- straight from contacted to the estimate.
    A funnel that only counted explicit visits would report a hole that is not
    there."""
    signup(client)
    lead = new_lead(client)
    _walk(client, lead, 'estimate_presented')
    r = _reached(client)
    assert r['contacted'] == 1 and r['appt_set'] == 1 and r['inspected'] == 1
    assert r['won'] == 0


def test_the_counts_only_ever_go_down(client):
    signup(client)
    for stages in ([], ['contacted'], ['contacted', 'appt_set'],
                   ['contacted', 'appt_set', 'inspected']):
        _walk(client, new_lead(client), *stages)
    counts = [r['reached'] for r in _funnel(client)['rungs']]
    assert counts == sorted(counts, reverse=True), counts


# ── Lost deals stay in the cohort ────────────────────────────────────────────

def test_a_lost_deal_keeps_the_rungs_it_reached(client):
    """Dropping them would flatter every conversion rate on the screen."""
    signup(client)
    lead = new_lead(client)
    _walk(client, lead, 'contacted', 'appt_set')
    client.patch(f'/api/leads/{lead["id"]}/stage',
                 json={'stage': 'lost', 'lost_reason': 'price'})
    r = _reached(client)
    assert r['appt_set'] == 1
    assert r['inspected'] == 0
    assert _funnel(client)['cohort'] == 1


def test_lost_never_outranks_won(client):
    """`lost` sits last in STAGES so the board reads left to right, which makes
    its raw index 7 -- above won's 6. Any rank comparison over STAGE_KEYS would
    score every dead deal as having got further than a signed one."""
    signup(client)
    lead = new_lead(client)
    client.patch(f'/api/leads/{lead["id"]}/stage', json={'stage': 'lost'})
    assert _reached(client)['won'] == 0
    assert 'lost' not in appmod.LADDER


# ── Where a lead entered ─────────────────────────────────────────────────────

def test_a_doorstep_lead_entered_above_new(client):
    """The canvasser hands appointments straight into `appt_set`. Without an
    entry stage those read as "never got past new", under-reporting conversion
    on exactly the leads door-knocking exists to produce."""
    signup(client)
    lead = new_lead(client, stage='appt_set')
    r = _reached(client)
    assert r['contacted'] == 1 and r['appt_set'] == 1
    assert r['inspected'] == 0


def test_an_immediately_lost_doorstep_lead_keeps_its_entry_rung(client):
    """It has no stage_change to a rung and its current stage is off the
    ladder, so the entry stage is the only record of how far it got."""
    signup(client)
    lead = new_lead(client, stage='appt_set')
    client.patch(f'/api/leads/{lead["id"]}/stage', json={'stage': 'lost'})
    assert _reached(client)['appt_set'] == 1


def test_entry_stage_is_backfilled_from_the_first_move(client):
    """Live rows predate the column. The earliest stage_change names the stage
    it moved OUT of, on the left of the arrow -- the one place that survives."""
    signup(client)
    lead = new_lead(client)
    _walk(client, lead, 'contacted', 'appt_set')
    with appmod.get_db() as db:
        db.execute("UPDATE leads SET entry_stage='' WHERE id=?", (lead['id'],))
        appmod._backfill_entry_stage(db)
        got = db.execute('SELECT entry_stage FROM leads WHERE id=?',
                         (lead['id'],)).fetchone()['entry_stage']
    assert got == 'new'


def test_an_unmoved_lead_backfills_to_where_it_sits(client):
    signup(client)
    lead = new_lead(client, stage='contacted')
    with appmod.get_db() as db:
        db.execute("UPDATE leads SET entry_stage='' WHERE id=?", (lead['id'],))
        appmod._backfill_entry_stage(db)
        got = db.execute('SELECT entry_stage FROM leads WHERE id=?',
                         (lead['id'],)).fetchone()['entry_stage']
    assert got == 'contacted'


# ── Cohort, window and scope ─────────────────────────────────────────────────

def test_the_cohort_is_leads_created_in_the_window(client):
    signup(client)
    old = new_lead(client)
    new_lead(client)
    with appmod.get_db() as db:
        db.execute("UPDATE leads SET created_at='2020-01-01T00:00:00Z' WHERE id=?",
                   (old['id'],))
    assert _funnel(client, days=7)['cohort'] == 1


def test_conversion_is_reported_against_the_previous_rung(client):
    """Share of the whole cohort tells you the shape; share of the rung before
    is where a leak actually shows up."""
    signup(client)
    for _ in range(4):
        new_lead(client)
    _walk(client, new_lead(client), 'contacted')
    rungs = {r['key']: r for r in _funnel(client)['rungs']}
    assert rungs['new']['pct_of_prev'] is None, 'nothing precedes the top rung'
    assert rungs['contacted']['reached'] == 1
    assert rungs['contacted']['pct_of_prev'] == 20.0


def test_a_rep_sees_only_their_own_funnel(client):
    signup(client, 'luke')                       # manager
    signup(client, 'casey')
    login(client, 'luke')
    _walk(client, new_lead(client), 'contacted')
    login(client, 'casey')
    assert _funnel(client)['cohort'] == 0


def test_an_empty_cohort_reports_zero_not_a_crash(client):
    signup(client)
    f = _funnel(client)
    assert f['cohort'] == 0
    assert all(r['reached'] == 0 and r['pct_of_cohort'] == 0.0 for r in f['rungs'])


def test_the_snapshot_and_the_flow_are_both_served_and_named_apart(client):
    """One number was doing both jobs badly. `stage_counts` is where the board
    stands; `funnel` is how the cohort moved."""
    signup(client)
    _walk(client, new_lead(client), 'contacted', 'won')
    d = client.get('/api/dashboard').get_json()
    assert d['stage_counts']['won'] == 1 and d['stage_counts']['contacted'] == 0
    assert {r['key']: r['reached'] for r in d['funnel']['rungs']}['contacted'] == 1


# ── follow_up is a holding state, not a rung ─────────────────────────────────

def test_follow_up_is_not_a_rung(client):
    """It sat between "quoted" and "won", so every deal that closed straight off
    the estimate was credited with a follow-up that never happened and the row
    became "whichever is larger"."""
    signup(client)
    _walk(client, new_lead(client), 'estimate_presented', 'won')
    assert 'follow_up' not in _reached(client)
    assert appmod.LADDER == ['new', 'contacted', 'appt_set', 'inspected',
                             'estimate_presented', 'won']


def test_a_lead_in_follow_up_counts_as_quoted(client):
    """Being in follow-up means they HAVE been quoted -- that is the true
    statement about how far it got, and dropping it off the ladder entirely
    would lose the rung it earned."""
    signup(client)
    _walk(client, new_lead(client), 'follow_up')
    r = _reached(client)
    assert r['estimate_presented'] == 1
    assert r['won'] == 0


# ── The cohort is leads somebody sourced ─────────────────────────────────────

def test_bulk_imported_prospects_are_not_in_the_cohort(client):
    """One open-data pull adds tens of thousands of rows nobody sourced. Mixed
    in, they drown the few hundred real leads and the panel reports on the size
    of the last import instead of on the sales process."""
    signup(client)
    _walk(client, new_lead(client), 'contacted')
    with appmod.get_db() as db:                    # what an import writes
        db.execute("INSERT INTO leads (id, lead_type, service, stage, entry_stage, "
                   "rep, created_at, updated_at, import_batch) "
                   "VALUES ('b1','hoa','roofing','new','new','luke',?,?,'batch1')",
                   (appmod._now(), appmod._now()))
    f = _funnel(client)
    assert f['cohort'] == 1
    assert f['bulk_imported'] == 1, 'counted, not silently dropped'
    assert {r['key']: r['pct_of_prev'] for r in f['rungs']}['contacted'] == 100.0
