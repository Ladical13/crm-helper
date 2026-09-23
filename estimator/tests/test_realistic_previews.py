"""Offline contract tests: never call a paid image provider."""
import io
import json
import base64
from pathlib import Path

import pytest
from PIL import Image
from estimator import exterior_rendering as R


@pytest.fixture
def design(client, A, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-not-a-key')
    monkeypatch.setenv('EXTERIOR_REALISTIC_PREVIEW', '1')
    with A.realistic_store.db() as db:
        db.execute('DELETE FROM jobs')
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    directory = Path(A.UPLOADS_DIR) / eid
    directory.mkdir(exist_ok=True)
    Image.new('RGB', (81, 144), 'gray').save(directory / 'original.png')
    doc = A.est_load(eid)
    doc['visualizer'] = {'scope': ['roof'], 'selections': {'roofing': {'good': {
        'product_name': 'Standing seam metal', 'color_name': 'Charcoal'}}},
        'elevations': {'front': {'base_image': f'{eid}/original.png', 'masks': {}, 'tier_renders': {}}}}
    A.est_save(doc)
    calls = []
    monkeypatch.setattr(A, 'start_realistic_preview', lambda *args: calls.append(args))
    yield eid, calls
    client.delete(f'/api/estimates/{eid}')


def start(client, eid, **fields):
    return client.post(f'/api/estimates/{eid}/realistic-previews', json={
        'confirm': True, 'elevation': 'front', 'tier': 'good',
        'nonce': 'unique-request-12345', **fields})


def ready(client, A, eid):
    jid = start(client, eid).get_json()['id']
    Image.new('RGB', (81, 144), 'blue').save(A.realistic_store.directory / (jid+'.png'))
    A.realistic_store.finish(jid, 'ready')
    return jid


def test_start_is_idempotent_and_never_publishes(client, A, design):
    eid, calls = design
    before = A.est_load(eid)['visualizer']
    first = start(client, eid)
    assert first.status_code == 202
    assert start(client, eid).get_json()['id'] == first.get_json()['id']
    assert len(calls) == 1
    assert A.est_load(eid)['visualizer'] == before
    images, size, prompt = calls[0][1]
    assert images[0][0] == 'original.png'
    assert size == (81, 144)
    assert 'fascia' in prompt and 'standing-seam' in prompt
    assert start(client, eid, nonce='another-request-12345').status_code == 400


def test_auth_and_explicit_enable(client, anon, A, monkeypatch, design):
    eid, calls = design
    assert start(anon, eid).status_code == 401
    monkeypatch.setattr(A, '_can_touch_estimate', lambda doc: False)
    assert start(client, eid).status_code == 403
    monkeypatch.setattr(A, '_can_touch_estimate', lambda doc: True)
    monkeypatch.delenv('OPENAI_API_KEY')
    assert start(client, eid).status_code == 503
    assert not calls


def test_review_required_and_accept_preserves_original(client, A, design):
    eid, _ = design
    jid = ready(client, A, eid)
    path = f'/api/estimates/{eid}/realistic-previews/{jid}'
    assert client.post(path+'/accept', json={}).status_code == 400
    response = client.post(path+'/accept', json={'reviewed': True})
    assert response.status_code == 200
    ev = response.get_json()['visualizer']['elevations']['front']
    assert ev['base_image'] == f'{eid}/original.png'
    assert ev['tier_renders'] == {'good': f'{eid}/vr_ai_{jid}.png'}
    assert client.get(path+'/image').headers['Cache-Control'] == 'private, no-store'


def test_changed_product_cannot_accept_old_result(client, A, design):
    eid, _ = design
    jid = ready(client, A, eid)
    doc = A.est_load(eid)
    doc['visualizer']['selections']['roofing']['good']['color_name'] = 'Red'
    A.est_save(doc)
    path = f'/api/estimates/{eid}/realistic-previews/{jid}'
    assert client.get(path).get_json()['stale'] is True
    assert client.post(path+'/accept', json={'reviewed': True}).status_code == 409
    assert not A.est_load(eid)['visualizer']['elevations']['front']['tier_renders']


def test_limits_include_failed_calls(client, A, monkeypatch, design):
    eid, _ = design
    monkeypatch.setenv('EXTERIOR_RENDER_USER_DAILY_LIMIT', '1')
    jid = start(client, eid).get_json()['id']
    A.realistic_store.finish(jid, 'failed', 'Interrupted')
    response = start(client, eid, nonce='different-request-12345')
    assert response.status_code == 400
    assert 'daily' in response.get_json()['error']


@pytest.mark.parametrize('ref', ['../../secret.png', 'https://example.com/photo.png', 'other-estimate/photo.png'])
def test_references_cannot_escape_estimate(client, A, design, ref):
    eid, calls = design
    doc = A.est_load(eid)
    doc['visualizer']['elevations']['front']['base_image'] = ref
    A.est_save(doc)
    assert start(client, eid).status_code == 400
    assert not calls


def test_provider_contract_preserves_portrait_and_keeps_key_server_side(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only-secret')
    output = io.BytesIO()
    Image.new('RGB', (864, 1536)).save(output, 'PNG')
    captured = []
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size):
            yield json.dumps({'data': [{'b64_json': base64.b64encode(output.getvalue()).decode()}]}).encode()
    def post(url, **kwargs):
        captured.append((url, kwargs))
        return Response()
    monkeypatch.setattr(R.requests, 'post', post)
    assert R.generate([('original.png', b'input')], (810, 1440), 'edit')
    assert captured[0][1]['data']['size'] == '864x1536'
    assert captured[0][1]['data']['n'] == '1'
    assert captured[0][1]['allow_redirects'] is False


def test_timeout_never_retries_or_leaks_key(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'private-value')
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('private-value')
    monkeypatch.setattr(R.requests, 'post', fail)
    with pytest.raises(R.RenderError) as exc:
        R.generate([], (810,1440), 'edit')
    assert 'private-value' not in str(exc.value)
    assert len(calls) == 1


def test_browser_workflow_contract():
    import subprocess
    result = subprocess.run(['node', str(Path(__file__).with_name('realistic_preview_runner.cjs'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_candidate_is_private_and_estimate_scoped(client, anon, A, design):
    eid, _ = design
    jid = ready(client, A, eid)
    assert anon.get(f'/api/estimates/{eid}/realistic-previews/{jid}/image').status_code == 401
    other = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        assert client.get(f'/api/estimates/{other}/realistic-previews/{jid}').status_code == 404
    finally:
        client.delete(f'/api/estimates/{other}')


def test_interrupted_job_is_not_restarted(client, A, design):
    eid, calls = design
    jid = start(client, eid).get_json()['id']
    with A.realistic_store.db() as db:
        db.execute('UPDATE jobs SET created=0 WHERE id=?', (jid,))
    assert client.get(f'/api/estimates/{eid}/realistic-previews/{jid}').get_json()['status'] == 'failed'
    assert start(client, eid).get_json()['id'] == jid
    assert len(calls) == 1


def test_invalid_scope_fails_without_provider_call(client, A, design):
    eid, calls = design
    doc = A.est_load(eid)
    doc['visualizer']['scope'] = [{}]
    A.est_save(doc)
    assert start(client, eid).status_code == 400
    assert not calls
