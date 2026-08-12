"""First-party question sources: CRM activity mining and field notes.

Reddit is gated by Reddit, so these replace it — and are better, because they
are our own records of real Colorado customers rather than strangers on a
forum. Nobody can revoke them either.

The tests that matter: the CRM connection must be read-only at the driver, and
a field note must not become a place to paste somebody's post.
"""
import os
import sqlite3

import pytest


# ── A throwaway salescrm.db ─────────────────────────────────────────────────

@pytest.fixture
def fake_crm(monkeypatch, tmp_path):
    """Build a minimal salescrm.db with realistic rep shorthand."""
    d = tmp_path / 'crm'
    d.mkdir()
    path = d / 'salescrm.db'
    conn = sqlite3.connect(path)
    conn.executescript('''
        CREATE TABLE leads (id TEXT PRIMARY KEY, city TEXT);
        CREATE TABLE activities (id TEXT PRIMARY KEY, lead_id TEXT, rep TEXT,
                                 kind TEXT, outcome TEXT, body TEXT,
                                 created_at TEXT);
    ''')
    conn.execute("INSERT INTO leads VALUES ('L1', 'Fort Collins')")
    conn.execute("INSERT INTO leads VALUES ('L2', 'Greeley')")
    conn.execute("INSERT INTO leads VALUES ('L3', 'Fort Collins')")
    notes = [
        ('A1', 'L1', "Homeowner asked about whether insurance covers a roof "
                     "that is 12 years old. Wants a call back."),
        ('A2', 'L2', "Does insurance cover an older roof? She asked twice."),
        ('A3', 'L3', "Asking about insurance covering an old roof, 15 years."),
        ('A4', 'L1', "Left voicemail."),
        ('A5', 'L2', "What happens to my deductible if I file a claim?"),
        ('A6', 'L3', "Knocked, nobody home."),
    ]
    for i, (aid, lead, body) in enumerate(notes):
        conn.execute("INSERT INTO activities VALUES (?, ?, 'luke', 'call', '', ?, "
                     "date('now', ?))", (aid, lead, body, f'-{i} day'))
    conn.commit()
    conn.close()
    monkeypatch.setenv('SALESCRM_DATA_DIR', str(d))
    return path


# ── CRM mining ───────────────────────────────────────────────────────────────

def test_crm_is_unavailable_without_a_database(monkeypatch, tmp_path):
    from agents.seo import crm_questions
    monkeypatch.setenv('SALESCRM_DATA_DIR', str(tmp_path / 'nope'))
    out = crm_questions.mine()
    assert out['available'] is False and out['questions'] == []


def test_the_crm_connection_is_read_only_at_the_driver(fake_crm):
    """Nimbus must never write to salescrm.db — that file holds the leads.
    mode=ro means a stray write fails in SQLite rather than relying on this
    module to behave."""
    from agents.seo import crm_questions
    conn = crm_questions._connect_readonly()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO leads VALUES ('X', 'Nowhere')")
    finally:
        conn.close()


def test_recurring_questions_are_clustered_and_counted(fake_crm):
    """Three reps wrote the same insurance question three different ways."""
    from agents.seo import crm_questions
    out = crm_questions.mine()
    assert out['available'] is True
    top = out['questions'][0]
    assert top['mentions'] >= 3
    assert 'insurance' in top['question'].lower()
    assert len(top['evidence']) >= 2
    assert all(e.startswith('crm:lead:') for e in top['evidence'])


def test_a_one_off_question_is_not_surfaced(fake_crm):
    """One caller is an anecdote. Building a page for it is how you end up
    with a site full of pages nobody searched for."""
    from agents.seo import crm_questions
    out = crm_questions.mine()
    assert not any('deductible' in q['question'].lower() for q in out['questions'])


def test_notes_with_no_question_are_ignored(fake_crm):
    from agents.seo import crm_questions
    out = crm_questions.mine()
    joined = ' '.join(q['question'].lower() for q in out['questions'])
    assert 'voicemail' not in joined and 'nobody home' not in joined


def test_the_dominant_city_travels_with_the_question(fake_crm):
    from agents.seo import crm_questions
    out = crm_questions.mine()
    assert out['questions'][0]['city'] == 'Fort Collins'


def test_extract_handles_rep_shorthand_without_a_question_mark():
    from agents.seo import crm_questions
    found = crm_questions.extract_questions('Asked about ice dams on the north side')
    assert found and 'ice dams' in found[0].lower()


def test_extract_never_raises_on_junk():
    from agents.seo import crm_questions
    for junk in ('', None, '???', 'x' * 5000):
        assert isinstance(crm_questions.extract_questions(junk), list)


def test_crm_findings_are_first_party_not_public_research(fake_crm):
    from agents.seo import crm_questions, honesty
    out = crm_questions.as_research()
    assert out['evidence_basis'] == honesty.FIRST_PARTY
    assert out['cost_usd'] == 0.0


# ── Field notes ──────────────────────────────────────────────────────────────

def test_a_typed_question_is_stored():
    from agents.seo import field_notes
    n = field_notes.add('Does insurance cover a 12 year old roof?',
                        heard_where='r/FortCollins', city='Fort Collins',
                        username='luke')
    assert n['id'] and n['status'] == 'new'
    assert len(field_notes.listing()) == 1


def test_a_pasted_reddit_post_is_refused():
    """The note is meant to be the question, not the thread. Storing somebody
    else's post defeats the entire reason we are doing this by hand."""
    from agents.seo import field_notes
    with pytest.raises(field_notes.Rejected, match='characters'):
        field_notes.add('So I got home yesterday and noticed ' + 'x' * 400)


def test_a_note_naming_a_redditor_is_refused():
    from agents.seo import field_notes
    with pytest.raises(field_notes.Rejected, match='paraphrase'):
        field_notes.add('u/someguy asked whether insurance covers hail')


def test_a_multi_paragraph_note_is_refused():
    from agents.seo import field_notes
    with pytest.raises(field_notes.Rejected, match='pasted'):
        field_notes.add('First line\nsecond line\nthird line\nfourth line')


def test_a_note_using_a_banned_phrase_is_refused():
    from agents.seo import field_notes
    with pytest.raises(field_notes.Rejected, match='banned phrase'):
        field_notes.add('Do they really offer a free inspection?')


def test_a_note_too_short_to_be_a_question_is_refused():
    from agents.seo import field_notes
    with pytest.raises(field_notes.Rejected):
        field_notes.add('roof?')


def test_field_notes_are_first_party_and_free():
    from agents.seo import field_notes, honesty
    field_notes.add('How long does a roof replacement take?', 'phone call')
    out = field_notes.as_research()
    assert out['evidence_basis'] == honesty.FIRST_PARTY
    assert out['cost_usd'] == 0.0
    assert out['citations'][0].startswith('field-note:')


def test_a_used_note_stops_reappearing():
    from agents.seo import field_notes
    n = field_notes.add('Do you work with my insurance company?', 'HOA meeting')
    field_notes.mark_used([n['id']], run_id=7)
    assert field_notes.listing(status='new') == []
    assert field_notes.listing(status='used')[0]['used_run_id'] == 7


# ── How they reach the strategist ────────────────────────────────────────────

def test_a_first_party_question_becomes_a_high_confidence_recommendation():
    """A question our own records show customers asking beats one a model
    inferred, and the card says so."""
    from agents.seo import honesty, recommend
    recs = recommend.from_research(
        {('Fort Collins', 'roofing', 'Roofing'): {
            'questions': [{'question': 'Does insurance cover a 12 year old roof?',
                           'why_it_matters': 'Mentioned in 4 logged conversations.',
                           'source': 'crm'}],
            'citations': ['crm:lead:L1', 'crm:lead:L2']}},
        {}, [])
    assert recs
    rec = recs[0]
    assert rec['evidence_basis'] == honesty.FIRST_PARTY
    assert rec['confidence'] == 'high'
    assert 'our own CRM notes' in rec['rationale']
    kept, dropped = honesty.filter_all(recs)
    assert kept and not dropped
    assert kept[0]['label'] == 'From our own records'


def test_public_research_questions_still_say_they_are_indirect():
    from agents.seo import honesty, recommend
    recs = recommend.from_research(
        {('Greeley', 'roofing', 'Roofing'): {
            'questions': [{'question': 'What is the best shingle for hail?',
                           'why_it_matters': '', 'source': ''}],
            'citations': ['https://example.org/1']}},
        {}, [])
    kept, _ = honesty.filter_all(recs)
    assert kept[0]['label'] == 'Public-research opportunity'
    assert kept[0]['evidence_basis'] == honesty.PUBLIC_RESEARCH


def test_owned_data_is_still_impossible():
    """FIRST_PARTY must not become a loophole for search metrics we cannot
    measure. Our CRM tells us what customers asked, never how we rank."""
    from agents.seo import honesty
    base = {'category': 'faq_or_content_brief', 'action': 'Write it',
            'rationale': 'x', 'evidence': ['crm:lead:1'], 'confidence': 'high',
            'review_notes': 'check it'}
    with pytest.raises(honesty.Rejected):
        honesty.check({**base, 'evidence_basis': 'owned_data'})
    with pytest.raises(honesty.Rejected, match='ranking'):
        honesty.check({**base, 'evidence_basis': honesty.FIRST_PARTY,
                       'rationale': 'Our CRM shows we rank #2 for this'})


def test_a_run_uses_first_party_sources_before_paid_ones(fake_crm, monkeypatch):
    from agents import perplexity
    from agents.seo import field_notes, run as seo

    field_notes.add('Do you handle the insurance paperwork?', 'r/Denver')

    def no_paid(*a, **kw):
        raise perplexity.SpendCapReached('cap reached')
    monkeypatch.setattr(perplexity, 'search_json', no_paid)

    questions, _competitors, note, cost = seo._gather_research(dry_run=True)
    assert cost == 0.0
    assert questions, 'first-party sources should report even on a dry run'
    asked = [q['question'] for payload in questions.values()
             for q in payload['questions']]
    assert any('insurance paperwork' in q.lower() for q in asked)
    assert 'crm' in note or 'field notes' in note
