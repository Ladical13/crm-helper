import pytest

from agents import jobs
from agents.content import studio


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENTS_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('PERPLEXITY_API_KEY', raising=False)


def test_studio_stays_admin_only(rep):
    assert rep.get('/nimbus/api/studio/campaigns').status_code == 403
    assert rep.post('/nimbus/api/studio/campaigns', json={'name': 'No'}).status_code == 403


def test_campaign_validation_and_missing_ids(admin):
    assert admin.post('/nimbus/api/studio/campaigns', json=[]).status_code == 400
    assert admin.post('/nimbus/api/studio/campaigns', json={'name': ''}).status_code == 400
    assert admin.get('/nimbus/api/studio/campaigns/9999').status_code == 404


def test_full_manual_workflow_and_legacy_routes_cannot_bypass_review(admin, monkeypatch):
    def synchronous(kind, work, dry_run):
        job_id = jobs.claim(kind, dry_run)
        jobs.execute(job_id, work)
        return job_id
    monkeypatch.setattr(jobs, 'start', synchronous)
    cid = admin.post('/nimbus/api/studio/campaigns', json={'name': 'Organic plan', 'platforms': ['facebook']}).get_json()['id']
    prefix = f'/nimbus/api/studio/campaigns/{cid}'
    run = admin.post(prefix+'/research', json={'live': False})
    assert run.status_code == 202 and run.get_json()['job_id']
    idea = admin.get(prefix).get_json()['ideas'][0]
    assert admin.post(prefix+f'/ideas/{idea["id"]}', json={'status': 'selected'}).status_code == 200
    assert admin.post(prefix+'/generate', json={'manual': True}).status_code == 202
    p = admin.get(prefix).get_json()['posts'][0]
    legacy = admin.get('/nimbus/api/content/drafts').get_json()
    assert next(d for d in legacy if d['id'] == p['id'])['source'] == 'studio'
    for old in ('social', 'content'):
        assert admin.post(f'/nimbus/api/{old}/drafts/{p["id"]}', json={'status': 'posted'}).status_code == 409
    response = admin.post(f'/nimbus/api/studio/posts/{p["id"]}', json={'revision': 1, 'draft_text': 'Ask about materials and scope.'})
    assert response.status_code == 200 and response.get_json()['revision'] == 2
    assert admin.post(f'/nimbus/api/studio/posts/{p["id"]}', json={'revision': 1, 'status': 'approved'}).status_code == 409
    assert admin.get(prefix+'/export').mimetype == 'text/csv'
    assert admin.get(prefix+'/export?format=json').get_json()['posts'][0]['draft_text'] == 'Ask about materials and scope.'
    assert admin.get('/nimbus/assets/studio.js').status_code == 200
    assert admin.get('/nimbus/marketing/social').status_code == 200


def test_shared_job_lock_blocks_duplicate_research(admin):
    cid = studio.create_campaign({'name': 'Lock check'})
    job_id = jobs.claim('seo')
    result = admin.post(f'/nimbus/api/studio/campaigns/{cid}/research', json={'live': True})
    assert result.status_code == 409
    jobs.finish(job_id, {'ok': True})
