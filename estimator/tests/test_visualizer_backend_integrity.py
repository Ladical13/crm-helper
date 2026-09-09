"""Design assets and customer approvals must refer to valid, current work."""
import base64
import io
import os
import re

import pytest
from PIL import Image


def _image_b64(size=(12, 8), color=(50, 70, 90, 128)):
    output = io.BytesIO()
    Image.new('RGBA', size, color).save(output, 'PNG')
    return base64.b64encode(output.getvalue()).decode('ascii')


@pytest.fixture
def design_id(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    yield eid
    client.delete(f'/api/estimates/{eid}')


def _upload(client, eid, kind, **fields):
    return client.post(f'/api/estimates/{eid}/visualizer/asset', json={
        'kind': kind, 'ext': 'png', 'content_b64': _image_b64(), **fields,
    })


@pytest.mark.parametrize('kind,fields', [
    ('base', {}), ('mask', {'role': 'trim'}), ('render', {'tier': 'better'}),
])
def test_photo_mask_and_render_reject_non_image_bytes(client, A, design_id,
                                                     kind, fields):
    before = client.get(f'/api/estimates/{design_id}').get_json()
    result = _upload(client, design_id, kind, **fields,
                     content_b64=base64.b64encode(b'<html>not an image</html>').decode())
    assert result.status_code == 400
    after = client.get(f'/api/estimates/{design_id}').get_json()
    assert after.get('visualizer') == before.get('visualizer')
    folder = os.path.join(A.UPLOADS_DIR, design_id)
    assert not os.path.isdir(folder) or not os.listdir(folder)


@pytest.mark.parametrize('payload', [
    ['base'], {'kind': 12}, {'kind': 'base', 'ext': ['png']},
    {'kind': 'base', 'ext': 'png', 'content_b64': {'bad': 'value'}},
])
def test_malformed_asset_requests_return_validation_errors(client, design_id, payload):
    response = client.post(f'/api/estimates/{design_id}/visualizer/asset', json=payload)
    assert response.status_code == 400
    assert response.get_json()['error']


def test_image_dimensions_are_bounded_before_pixel_decode(client, design_id):
    result = _upload(client, design_id, 'base', content_b64=_image_b64((6001, 1)))
    assert result.status_code == 400
    assert '6000' in result.get_json()['error']


def test_stored_mask_keeps_alpha_and_uses_declared_format(client, A, design_id):
    result = _upload(client, design_id, 'mask', role='trim')
    assert result.status_code == 201
    with Image.open(os.path.join(A.UPLOADS_DIR, result.get_json()['filename'])) as mask:
        assert mask.format == 'PNG'
        assert mask.getpixel((3, 3))[3] == 128

    # Mislabeled older clients are normalized, rather than saving PNG bytes
    # behind a JPEG extension that later confuses PDF generation.
    result = _upload(client, design_id, 'render', tier='better', ext='jpg')
    assert result.status_code == 201
    with Image.open(os.path.join(A.UPLOADS_DIR, result.get_json()['filename'])) as render:
        assert render.format == 'JPEG'
        assert render.size == (12, 8)


def test_replacing_mask_invalidates_only_its_elevation_renders(client, design_id):
    front = _upload(client, design_id, 'render', tier='better').get_json()['filename']
    rear = _upload(client, design_id, 'render', tier='better',
                   elevation_id='rear').get_json()['filename']
    assert front != rear
    assert _upload(client, design_id, 'mask', role='trim').status_code == 201
    vz = client.get(f'/api/estimates/{design_id}').get_json()['visualizer']
    assert vz['tier_renders'] == {}
    assert vz['elevations']['front']['tier_renders'] == {}
    assert vz['elevations']['rear']['tier_renders']['better'] == rear


def test_deleting_front_does_not_resurrect_legacy_photo_or_renders(client, design_id):
    assert _upload(client, design_id, 'base').status_code == 201
    assert _upload(client, design_id, 'render', tier='better').status_code == 201
    rear = _upload(client, design_id, 'base', elevation_id='rear').get_json()['filename']
    response = client.put(f'/api/estimates/{design_id}/visualizer/state', json={
        'delete_elevation_id': 'front', 'active_elevation_id': 'rear',
    })
    assert response.status_code == 200
    vz = response.get_json()['visualizer']
    assert set(vz['elevations']) == {'rear'}
    assert vz['elevations']['rear']['base_image'] == rear
    assert vz['elevation_order'] == ['rear']
    assert vz['active_elevation_id'] == 'rear'
    assert vz['base_image'] is None
    assert vz['tier_renders'] == {}
    # A later metadata save normalizes again and must not recreate Front.
    vz = client.put(f'/api/estimates/{design_id}/visualizer/state', json={
        'favorite_tier': 'better',
    }).get_json()['visualizer']
    assert set(vz['elevations']) == {'rear'}


def test_provia_upload_keeps_current_elevation_and_its_name(client, design_id):
    assert _upload(client, design_id, 'base', elevation_id='rear',
                   elevation_name='Back patio').status_code == 201
    before = client.get(f'/api/estimates/{design_id}').get_json()['visualizer']
    result = _upload(client, design_id, 'provia', tier='better')
    assert result.status_code == 201
    after = client.get(f'/api/estimates/{design_id}').get_json()['visualizer']
    assert after['active_elevation_id'] == 'rear'
    assert after['elevations'] == before['elevations']
    assert after['elevation_order'] == before['elevation_order']
    assert after['provia_specs']['better']['configured_image'] == result.get_json()['filename']


def _review_hash(anon, token):
    page = anon.get(f'/design/{token}')
    assert page.status_code == 200
    return re.search(r'name="design_hash_better" value="([0-9a-f]{64})"',
                     page.get_data(as_text=True)).group(1)


@pytest.mark.parametrize('change', ['image', 'selection', 'name'])
def test_customer_cannot_approve_a_revision_they_have_not_seen(client, anon, design_id, change):
    assert _upload(client, design_id, 'render', tier='better').status_code == 201
    token = client.post(f'/api/estimates/{design_id}/visualizer/share').get_json()['token']
    old_hash = _review_hash(anon, token)
    if change == 'image':
        assert _upload(client, design_id, 'render', tier='better').status_code == 201
    else:
        update = ({'selections': {'roofing': {'better': {'color_name': 'Black'}}}}
                  if change == 'selection' else {'concept_names': {'better': 'Revised'}})
        assert client.put(f'/api/estimates/{design_id}/visualizer/state', json=update).status_code == 200
    form = {'approved_tier': 'better', 'approver_name': 'Customer',
            'agree': 'yes', 'design_hash_better': old_hash}
    rejected = anon.post(f'/design/{token}', data=form)
    assert rejected.status_code == 409
    assert 'Review the current images' in rejected.get_data(as_text=True)
    assert not client.get(f'/api/estimates/{design_id}').get_json().get('design_approval')

    form['design_hash_better'] = _review_hash(anon, token)
    assert form['design_hash_better'] != old_hash
    assert anon.post(f'/design/{token}', data=form).status_code == 200
    approval = client.get(f'/api/estimates/{design_id}').get_json()['design_approval']
    assert approval['snapshot_hash'] == form['design_hash_better']


def test_old_approval_forms_without_snapshot_require_refresh(client, anon, design_id):
    assert _upload(client, design_id, 'render', tier='better').status_code == 201
    token = client.post(f'/api/estimates/{design_id}/visualizer/share').get_json()['token']
    response = anon.post(f'/design/{token}', data={
        'approved_tier': 'better', 'approver_name': 'Customer', 'agree': 'yes',
    })
    assert response.status_code == 409
    assert not client.get(f'/api/estimates/{design_id}').get_json().get('design_approval')


def test_malformed_detection_role_never_contacts_provider(client, design_id, monkeypatch):
    from estimator import exterior_detection
    monkeypatch.setattr(exterior_detection, 'configured', lambda: True)
    monkeypatch.setattr(exterior_detection, 'submit',
                        lambda *args: pytest.fail('invalid request reached provider'))
    response = client.post(f'/api/estimates/{design_id}/visualizer/detection', json={
        'role': ['trim'], 'photo_key': 'front',
    })
    assert response.status_code == 400
