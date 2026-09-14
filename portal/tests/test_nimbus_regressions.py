"""Nimbus integration regressions, using the mounted CRM and temporary data."""
import sys
from unittest.mock import Mock

import pytest

from agents import config, ingest, jobs
from agents.b2b import dispatcher
from agents.supervisor import chat, client as sup_client


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    (tmp_path / 'agents').mkdir()
    monkeypatch.setenv('AGENTS_DATA_DIR', str(tmp_path / 'agents'))
    monkeypatch.delenv('PERPLEXITY_API_KEY', raising=False)
    crm = sys.modules['p1_crm_app']
    with crm.get_db() as db:
        for table in ('activities', 'tasks', 'cadence_enrollments', 'leads', 'suppressions'):
            db.execute(f'DELETE FROM {table}')


def test_pipeline_aggregates_more_than_one_page(admin):
    crm = sys.modules['p1_crm_app']
    with crm.get_db() as db:
        db.executemany(
            'INSERT INTO leads (id,rep,stage,est_value,created_at,updated_at) VALUES (?,?,?,?,?,?)',
            [(f'lead-{i}', 'luke', 'new', 100, '2026-01-01', '2026-01-01') for i in range(1200)])
        db.execute("INSERT INTO leads (id,rep,stage,est_value,created_at,updated_at) "
                   "VALUES ('won-old','luke','won',5000,'2025-01-01','2025-01-01')")
    data = admin.get('/nimbus/api/pipeline').get_json()
    assert data['open_leads'] == 1200
    assert data['open_value'] == 120000
    assert data['won_this_period'] == 1
    assert data['won_value'] == 5000
    assert data['period'] == 'all_time'


@pytest.mark.parametrize('dry_run', [False, True])
def test_research_matches_imported_and_deduped_rows_not_list_order(admin, monkeypatch, dry_run):
    existing = admin.post('/crm/api/leads', json={'company': 'Existing', 'email': 'old@example.com'}).get_json()
    unrelated = admin.post('/crm/api/leads', json={'company': 'Unrelated'}).get_json()
    config.save_territories({'luke': {'cities': ['Fort Collins'], 'segments': ['gc'],
                                      'counties': ['Larimer'], 'enrich_top_n': 5}})
    admin.post('/crm/api/suppressions', json={'kind': 'email', 'value': 'blocked@example.com'})
    rows = [
        {'company': 'Existing', 'email': 'old@example.com', 'icp_score': 50},
        {'company': 'Blocked', 'email': 'blocked@example.com', 'icp_score': 40},
        {'company': 'Invalid', 'icp_score': 30},
        {'company': 'New', 'source_ref': 'test:new', 'icp_score': 20},
        {'company': 'Another', 'source_ref': 'test:another', 'icp_score': 10},
    ]
    monkeypatch.setattr(dispatcher, '_pull_segment_city', lambda *a, **kw: rows)
    monkeypatch.setattr(dispatcher.enrich_mod, 'enrich_one', lambda row, *a, **kw:
                        dict(row, research_notes='Research: ' + row['company']))
    # Multiple chunks exercise global row indices, including skipped records.
    monkeypatch.setattr(ingest, 'CHUNK', 2)
    result = dispatcher.run('luke', client=admin, dry_run=dry_run)
    assert result['errors'] == []
    crm = sys.modules['p1_crm_app']
    with crm.get_db() as db:
        saved = {r['company']: dict(r) for r in db.execute('SELECT * FROM leads')}
    assert saved['Unrelated']['id'] == unrelated['id']
    assert saved['Unrelated']['research_notes'] == ''
    assert saved['Existing']['id'] == existing['id']
    if dry_run:
        assert set(saved) == {'Existing', 'Unrelated'}
        assert saved['Existing']['research_notes'] == ''
    else:
        assert set(saved) == {'Existing', 'Unrelated', 'New', 'Another'}
        for company in ('Existing', 'New', 'Another'):
            assert saved[company]['research_notes'] == 'Research: ' + company


def test_job_requests_share_state_before_worker_starts(admin, monkeypatch):
    monkeypatch.setattr(jobs.threading, 'Thread', Mock())
    response = admin.post('/nimbus/api/seo/run', json={'dry_run': True})
    assert response.status_code == 202
    job_id = response.get_json()['job_id']
    assert admin.post('/nimbus/api/social/run', json={}).status_code == 409
    assert admin.get(f'/nimbus/api/seo/result?job_id={job_id}').get_json()['running'] is True
    jobs.finish(job_id, {'ok': True, 'pages_crawled': 2})
    assert admin.post('/nimbus/api/social/run', json={}).status_code == 202
    assert admin.get(f'/nimbus/api/seo/result?job_id={job_id}').get_json()['manifest']['pages_crawled'] == 2
    assert admin.get('/nimbus/api/seo/result?job_id=99999').status_code == 404


def test_supervisor_reserves_conversation_before_worker_starts(admin, monkeypatch):
    monkeypatch.setattr(sup_client, 'key_is_set', lambda: True)
    thread = Mock()
    monkeypatch.setattr(chat.threading, 'Thread', thread)
    tid = admin.post('/nimbus/api/supervisor/threads').get_json()['thread_id']
    url = f'/nimbus/api/supervisor/threads/{tid}/message'
    assert admin.post(url, json={'text': 'First'}).status_code == 202
    assert admin.get(f'/nimbus/api/supervisor/threads/{tid}').get_json()['status'] == 'running'
    assert admin.post(url, json={'text': 'Second'}).status_code == 409
    assert thread.call_count == 1
