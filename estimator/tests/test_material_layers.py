"""Reusable style/color workflow. No paid provider calls."""
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from estimator import exterior_rendering as R
from test_realistic_previews import design, start


@pytest.fixture
def material(client, A, design):
    eid, calls = design
    doc = A.est_load(eid)
    doc['visualizer']['selections']['roofing']['good']['color_hex'] = '#808080'
    mask = Path(A.UPLOADS_DIR) / eid / 'mask.png'
    Image.new('RGBA', (81, 144), (255, 255, 255, 255)).save(mask)
    doc['visualizer']['elevations']['front']['masks']['roof'] = f'{eid}/mask.png'
    A.est_save(doc)
    return eid, calls


def original(client, eid, **fields):
    return client.post(f'/api/estimates/{eid}/material-layers', json={
        'role': 'roof', 'tier': 'good', 'elevation': 'front', 'action': 'original',
        'reviewed': True, **fields})


def test_existing_style_needs_no_provider_or_key(client, A, monkeypatch, material):
    eid, calls = material
    monkeypatch.delenv('OPENAI_API_KEY')
    result = original(client, eid)
    assert result.status_code == 200
    ev = result.get_json()['visualizer']['elevations']['front']
    layer = ev['material_layers'][0]
    assert layer['image_ref'] == ev['base_image']
    assert layer['reference_luma'] == pytest.approx(.21586, abs=.0001)
    assert layer['source'] == 'original'
    assert not ev['tier_renders']
    assert not calls
    assert original(client, eid).status_code == 200
    assert len(A.est_load(eid)['visualizer']['elevations']['front']['material_layers']) == 1


def test_material_snapshot_ignores_colors_but_not_style_or_photo(client, A, material):
    eid, calls = material
    response = start(client, eid, material_role='roof')
    assert response.status_code == 202
    jid = response.get_json()['id']
    prompt = calls[0][1][2]
    assert 'REUSABLE MATERIAL BASE' in prompt and '#808080' in prompt
    doc = A.est_load(eid)
    row = doc['visualizer']['selections']['roofing']['good']
    row.update(color_name='Red', color_hex='#ba2020', texture_ref='_catalog/et_new.png')
    A.est_save(doc)
    path = f'/api/estimates/{eid}/realistic-previews/{jid}'
    assert client.get(path).get_json()['stale'] is False
    row['style_id'] = 'different-seam-width'
    A.est_save(doc)
    assert client.get(path).get_json()['stale'] is True


def test_reviewed_layer_is_not_published_as_whole_ai_image(client, A, material):
    eid, _ = material
    jid = start(client, eid, material_role='roof').get_json()['id']
    Image.new('RGB', (81, 144), '#808080').save(A.realistic_store.directory / (jid + '.png'))
    A.realistic_store.finish(jid, 'ready')
    path = f'/api/estimates/{eid}/realistic-previews/{jid}/accept'
    assert client.post(path, json={}).status_code == 400
    response = client.post(path, json={'reviewed': True})
    assert response.status_code == 200
    ev = response.get_json()['visualizer']['elevations']['front']
    assert ev['base_image'] == f'{eid}/original.png'
    assert not ev['tier_renders']
    assert ev['material_layers'][0]['job_id'] == jid
    # Whole-estimate saves cannot forge or drop the server-owned layer.
    client.put(f'/api/estimates/{eid}', json={'visualizer': {}})
    assert A.est_load(eid)['visualizer']['elevations']['front']['material_layers'] == ev['material_layers']


def test_protection_applies_even_when_trim_not_in_scope(client, A, material):
    eid, calls = material
    doc = A.est_load(eid)
    doc['visualizer']['elevations']['front']['masks']['trim'] = f'{eid}/mask.png'
    A.est_save(doc)
    response = start(client, eid, material_role='roof')
    assert response.status_code == 400
    assert 'empty after protecting' in response.get_json()['error']
    assert original(client, eid).status_code == 409
    assert not calls


def test_mask_changes_invalidate_pending_job_but_same_bytes_new_ref_do_not(client, A, material):
    eid, _ = material
    jid = start(client, eid, material_role='roof').get_json()['id']
    path = f'/api/estimates/{eid}/realistic-previews/{jid}'
    root = Path(A.UPLOADS_DIR) / eid
    (root / 'new-mask.png').write_bytes((root / 'mask.png').read_bytes())
    doc = A.est_load(eid)
    doc['visualizer']['elevations']['front']['masks']['roof'] = f'{eid}/new-mask.png'
    A.est_save(doc)
    assert client.get(path).get_json()['stale'] is False
    Image.new('RGBA', (81, 144), (255, 255, 255, 0)).save(root / 'new-mask.png')
    assert client.get(path).get_json()['stale'] is True


def test_blended_shingles_are_not_flat_recolor_layers(client, A, material):
    eid, calls = material
    doc = A.est_load(eid)
    doc['visualizer']['selections']['roofing']['good']['product_name'] = 'IKO Nordic'
    A.est_save(doc)
    assert original(client, eid).status_code == 409
    assert start(client, eid, material_role='roof').status_code == 400
    assert not calls


def test_remove_layers_and_auth(client, anon, A, material, monkeypatch):
    eid, _ = material
    assert original(anon, eid).status_code == 401
    assert original(client, eid, reviewed=False).status_code == 400
    assert original(client, eid).status_code == 200
    assert original(client, eid, action='remove').status_code == 200
    assert not A.est_load(eid)['visualizer']['elevations']['front']['material_layers']
    monkeypatch.setattr(A, '_can_touch_estimate', lambda doc: False)
    assert original(client, eid).status_code == 403


def test_reframed_candidate_cannot_become_layer(client, A, material):
    eid, _ = material
    jid = start(client, eid, material_role='roof').get_json()['id']
    Image.new('RGB', (144, 81), '#808080').save(A.realistic_store.directory / (jid + '.png'))
    A.realistic_store.finish(jid, 'ready')
    result = client.post(f'/api/estimates/{eid}/realistic-previews/{jid}/accept', json={'reviewed': True})
    assert result.status_code == 409
    assert 'framing' in result.get_json()['error']
    assert not A.est_load(eid)['visualizer']['elevations']['front'].get('material_layers')


def test_browser_recolor_contract():
    result = subprocess.run(['node', str(Path(__file__).with_name('material_layer_runner.cjs'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_reusable_styles_are_bounded_and_siding_is_independent(client, A, material):
    eid, _ = material
    for index in range(10):
        doc = A.est_load(eid)
        doc['visualizer']['selections']['roofing']['good']['style_id'] = f'seam-{index}'
        A.est_save(doc)
        assert original(client, eid).status_code == 200
    doc = A.est_load(eid)
    assert len(doc['visualizer']['elevations']['front']['material_layers']) == 8
    doc['visualizer']['scope'].append('siding')
    doc['visualizer']['selections']['siding'] = {'good': {
        'product_name': 'LP SmartSide', 'style_id': 'lap-6', 'color_hex': '#ccddee'}}
    doc['visualizer']['elevations']['front']['masks']['siding'] = f'{eid}/mask.png'
    A.est_save(doc)
    assert original(client, eid, role='siding').status_code == 200
    assert original(client, eid, action='remove').status_code == 200
    layers = A.est_load(eid)['visualizer']['elevations']['front']['material_layers']
    assert len(layers) == 1 and layers[0]['role'] == 'siding'


def test_invalid_surface_never_calls_provider(client, A, material):
    eid, calls = material
    for role in ('trim', [], {}, 'https://example.com'):
        assert start(client, eid, material_role=role).status_code == 400
        assert original(client, eid, role=role).status_code == 400
    assert not calls


def test_removing_unavailable_layer_does_not_require_successful_render_save():
    from test_visualizer import _run_visualizer_ui_node
    _run_visualizer_ui_node(r"""
_vzResetState();
S.visualizer={scope:['roof'],base_image:'estimate-a/original.png'};
_vzElevation().material_layers=[{role:'roof',image_ref:'estimate-a/missing.png'}];
vzState.dirty=true;
saveCurrentWork=async()=>{throw Error('Must not try to save missing material first');};
_vzRedrawAll=()=>{};
const posts=[];
fetch=async(url,options)=>{posts.push(JSON.parse(options.body));return {ok:true,json:async()=>({
  visualizer:{elevations:{front:{material_layers:[],tier_renders:{}}}}
})};};
await _vzMaterialUseOriginal('roof',true);
assert.equal(posts.length,1);assert.equal(posts[0].action,'remove');
assert.deepEqual(_vzElevation().material_layers,[]);
assert.equal(vzState.dirty,true,'Removing the layer must not discard unsaved canvas work');
assert.equal(vzState.saving,false);
assert.equal(alerts.length,0);
""")


def test_save_preserves_unchecked_protective_masks():
    from test_visualizer import _run_visualizer_ui_node
    _run_visualizer_ui_node(r"""
_vzResetState();S.visualizer={scope:['roof'],base_image:'estimate-a/original.png'};
vzState.photoImg={naturalWidth:400,naturalHeight:300};
vzState.roofMask={toDataURL:()=> 'data:image/png;base64,AAAA'};
vzState.trimMask={toDataURL:()=> 'data:image/png;base64,BBBB'};
_vzMaskHasContent=mask=>!!mask;_vzDetectionUI=()=>{};
_vzComposeInto=()=>{};_vzEnsureTier=()=>{};
const masks=[];
_vzPostAsset=async(eid,body)=>{if(body.kind==='mask')masks.push(body.role);return {filename:eid+'/asset.png'};};
fetch=async()=>({ok:true});
assert.equal(await _vzSaveAll(),true);
assert.deepEqual(masks,['roof','trim']);
""")
