import json

import pytest

from agents import config, perplexity
from agents.content import posts, studio


def plan(platforms=None):
    cid = studio.create_campaign({'name': 'Helpful local answers', 'start_date': '2026-09-21',
                                  'platforms': platforms or ['facebook', 'instagram']})
    studio.research(cid)
    idea = studio.ideas(cid)[0]
    studio.select_idea(cid, idea['id'], {'status': 'selected'})
    return cid


def manual_post(platform='facebook'):
    cid = plan([platform])
    studio.generate(cid, manual=True)
    return cid, studio.list_posts(cid)[0]


def save(p, **updates):
    return studio.update_post(p['id'], {'revision': p['revision'], **updates}, 'luke')


def latest(cid):
    return studio.list_posts(cid)[0]


def test_library_is_cited_deduplicated_and_filtered_without_paid_access(monkeypatch):
    monkeypatch.setattr(perplexity, 'search_json', lambda *a, **k: pytest.fail('Unexpected paid call'))
    cid = studio.create_campaign({'name': 'Windows', 'services': ['windows']})
    first = studio.research(cid)
    second = studio.research(cid)
    assert first['added'] >= 1 and second['added'] == 0
    ideas = studio.ideas(cid)
    assert all(i['service'] == 'windows' and i['evidence'][0]['url'].startswith('https://') for i in ideas)
    assert 'not popularity' in first['note']
    assert ideas[0]['researched_at'].startswith('2026-09-16')


def test_web_research_requires_provider_citations_and_matching_audience(monkeypatch):
    candidate = {'question': 'What changes should owners ask about?', 'why_care': 'Avoid surprises.',
                 'angle': 'Prepare questions.', 'pillar': 'Planning', 'service': 'roofing', 'audience': 'homeowner',
                 'evidence': [{'title': 'Source', 'url': 'https://agency.example/report', 'finding': 'Ask questions.'}]}
    monkeypatch.setattr(perplexity, 'search_json', lambda *a, **k: {'data': {'ideas': [candidate]}, 'citations': []})
    cid = plan()
    assert studio.research(cid, live=True)['added'] == 0
    monkeypatch.setattr(perplexity, 'search_json', lambda *a, **k: {'data': {'ideas': [candidate]}, 'citations': ['https://agency.example/report']})
    assert studio.research(cid, live=True)['added'] == 1


@pytest.mark.parametrize('bad', [None, 'oops', {}, 4])
def test_malformed_research_keeps_useful_library(monkeypatch, bad):
    monkeypatch.setattr(perplexity, 'search_json', lambda *a, **k: {'data': {'ideas': bad}})
    cid = studio.create_campaign({'name': 'Test'})
    assert studio.research(cid, live=True)['added'] > 0


def test_missing_writer_is_reported_and_manual_drafting_still_works():
    cid = plan()
    result = studio.generate(cid)
    assert not result['ok'] and result['packages'][0]['rejected']
    assert studio.list_posts(cid) == []
    studio.generate(cid, manual=True)
    assert len(studio.list_posts(cid)) == 2
    studio.generate(cid, manual=True)
    assert len(studio.list_posts(cid)) == 2, 'Duplicate click created duplicate drafts'


def test_generated_content_receives_evidence_and_separates_creative(monkeypatch):
    prompts = []
    def writer(prompt, **kwargs):
        prompts.append(prompt)
        return {'data': {'body': 'Compare the scope before comparing the total.',
                         'image_prompt': 'A scope checklist', 'slides': ['Compare materials', 'Compare scope']},
                'cost_usd': .001}
    monkeypatch.setattr(perplexity, 'search_json', writer)
    cid = plan()
    result = studio.generate(cid)
    assert len(studio.list_posts(cid)) == 2 and result['cost_usd'] == .002
    assert 'consumer.ftc.gov' in prompts[0]
    p = latest(cid)
    assert 'A scope checklist' not in p['draft_text']
    assert p['creative']['image_prompt'] == 'A scope checklist'


def test_empty_or_nonobject_model_data_never_becomes_a_draft(monkeypatch):
    for data in ({}, {'raw': 'Not JSON'}, {'body': ''}, ['text'], {'call_to_action': 'Contact us'}):
        monkeypatch.setattr(perplexity, 'search_json', lambda *a, **k: {'data': data})
        pkg = posts.build_package({'topic': 'Question'}, platforms=['facebook'])
        assert not pkg['posts'] and pkg['rejected']


def test_review_requires_saved_copy_and_preserves_history():
    cid, p = manual_post()
    with pytest.raises(ValueError, match='empty'):
        save(p, status='approved')
    save(p, draft_text='Compare the scope before choosing a contractor.', status='approved')
    p = latest(cid)
    assert p['status'] == 'draft'
    save(p, status='approved')
    p = latest(cid)
    assert p['approved_by'] == 'luke'
    save(p, draft_text='Ask about materials and the work included.')
    assert latest(cid)['status'] == 'draft'
    assert not latest(cid)['approved_by']
    assert len(studio.history(p['id'])) == 3
    with pytest.raises(studio.Conflict):
        save(p, status='approved')


def test_asset_posting_and_historical_record_gates():
    cid, p = manual_post('instagram')
    save(p, draft_text='Compare materials and scope before choosing a contractor.')
    save(latest(cid), status='approved')
    with pytest.raises(ValueError, match='visual asset'):
        save(latest(cid), status='ready')
    save(latest(cid), asset_ready=True, asset_url='https://design.example/approved')
    save(latest(cid), status='ready')
    with pytest.raises(ValueError, match='published post link'):
        save(latest(cid), status='posted')
    save(latest(cid), status='posted', posted_url='https://www.instagram.com/p/test')
    with pytest.raises(ValueError, match='historical record'):
        save(latest(cid), draft_text='Silently rewritten history.')
    save(latest(cid), metrics={'reach': 120, 'saves': 3})
    result = studio.results(cid)
    assert result['totals']['reach'] == 120
    assert result['coverage']['inquiries'] == 0
    assert result['learning_candidates'][0]['metrics']['saves'] == 3


def test_creative_claims_are_checked_before_approval():
    cid, p = manual_post()
    creative = {**p['creative'], 'slides': ['We are the #1 roofer in Colorado.']}
    save(p, draft_text='Compare the written scope.', creative=creative)
    with pytest.raises(ValueError, match='claim'):
        save(latest(cid), status='approved')


def test_export_separates_assets_and_neutralises_spreadsheet_formulas():
    cid, p = manual_post()
    save(p, draft_text='=HYPERLINK("https://bad.example")', owner='@someone')
    exported = studio.export_csv(cid)
    assert "'=HYPERLINK" in exported and "'@someone" in exported
    assert 'Creative brief' in exported and 'Sources' in exported


@pytest.mark.parametrize('updates', [{'asset_url': 'javascript:alert(1)'}, {'asset_url': 'https://user:secret@example.com'},
                                     {'metrics': {'reach': -1}}, {'metrics': {'reach': True}},
                                     {'planned_date': 'tomorrow'}, {'asset_ready': 'yes'}])
def test_invalid_post_inputs_leave_revision_untouched(updates):
    cid, p = manual_post()
    with pytest.raises(ValueError):
        save(p, **updates)
    assert latest(cid)['revision'] == 1


def test_recent_topic_warning_requires_meaningful_overlap():
    cid = plan()
    question = studio.ideas(cid)[0]['question']
    with config.get_cache_db() as db:
        db.execute('INSERT INTO content_drafts(created_at,topic,platform,draft_text) VALUES (?,?,?,?)',
                   (config.now_iso(), question, 'facebook', 'copy'))
    another = plan()
    assert next(i for i in studio.ideas(another) if i['question'] == question)['recently_used']
    assert any(not i['recently_used'] for i in studio.ideas(another))


def test_balanced_week_uses_multiple_pillars_and_spreads_dates():
    cid = plan(['google_business', 'email'])
    assert studio.suggest_week(cid)['selected'] == 4
    selected = [i for i in studio.ideas(cid) if i['status'] == 'selected']
    assert len({i['pillar'] for i in selected}) >= 3
    studio.select_idea(cid, selected[0]['id'], {'format': 'reel'})
    studio.generate(cid, manual=True)
    drafts = studio.list_posts(cid)
    assert len({p['planned_date'] for p in drafts}) == 4
    assert max(p['planned_date'] for p in drafts) == '2026-09-27'
    assert all(p['creative']['format'] == ('text' if p['platform'] == 'email' else 'photo') for p in drafts)


def test_old_writer_uses_the_same_validation(monkeypatch):
    from agents.content import draft
    monkeypatch.setattr(perplexity, 'search_json', lambda *a, **k: {'data': {'body': 'We are the #1 roofer in Colorado.'}})
    result = draft.draft_topic({'topic': 'Roofing'}, platforms=['blog', 'facebook'])
    assert result['drafts'] == [] and len(result['rejected']) == 2
