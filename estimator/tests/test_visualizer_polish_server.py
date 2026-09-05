"""Server-side visualizer polish regressions.

These cover presentation details that are easy to miss in browser-only checks,
plus the concurrency boundary between the Design Studio's focused endpoints
and the estimator's whole-document autosave.
"""
import base64
import io

import pytest
from PIL import Image


class _PdfRecorder:
    def __init__(self):
        self.rects = []
        self.images = []

    def set_draw_color(self, *value):
        pass

    def set_fill_color(self, *value):
        pass

    def rect(self, *args, **kwargs):
        self.rects.append((args, kwargs))

    def image(self, path, **kwargs):
        self.images.append((path, kwargs))


@pytest.mark.parametrize(
    ('image_size', 'expected'),
    [
        ((100, 200), (31.0, 20.0, 18.0, 36.0)),     # portrait
        ((400, 300), (16.0, 20.0, 48.0, 36.0)),     # 4:3
        ((1600, 900), (10.0, 21.125, 60.0, 33.75)), # 16:9
    ],
)
def test_pdf_visualizer_image_is_centered_and_contained(A, tmp_path,
                                                         image_size, expected):
    path = tmp_path / f'{image_size[0]}x{image_size[1]}.png'
    Image.new('RGB', image_size, '#36506a').save(path)
    pdf = _PdfRecorder()

    assert A._pdf_contain_image(pdf, str(path), 10, 20, 60, 36) is True
    assert pdf.rects == [((10, 20, 60, 36), {'style': 'DF'})]
    assert len(pdf.images) == 1
    _, placement = pdf.images[0]
    assert (placement['x'], placement['y'], placement['w'], placement['h']) \
        == pytest.approx(expected)


def test_customer_design_review_uses_the_render_natural_aspect_ratio(A):
    html = A._design_review_page({
        'customer': {'name': 'Aspect Ratio Customer'},
        'visualizer': {'tier_renders': {'better': 'estimate/portrait.png'}},
    }, 'review-token')

    assert 'portrait.png' in html
    assert '.elevation img{display:block;width:100%;height:auto;object-fit:contain' in html
    assert 'aspect-ratio:5/3' not in html


def _jpeg_b64(size=(3, 2)):
    content = io.BytesIO()
    Image.new('RGB', size, '#774433').save(content, format='JPEG')
    return base64.b64encode(content.getvalue()).decode('ascii')


def test_stale_full_estimate_put_cannot_overwrite_visualizer_state_or_assets(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        # With no server visualizer yet, a full save may create the initial
        # document. This keeps first-use compatibility with the estimator UI.
        initial = client.get(f'/api/estimates/{eid}').get_json()
        initial['visualizer'] = {
            'scope': ['roof'],
            'selections': {'roofing': {'better': {'color_name': 'Old color'}}},
            'tier_renders': {'good': f'{eid}/vr_old.jpg'},
        }
        assert client.put(f'/api/estimates/{eid}', json=initial).status_code == 200
        stale_tab = client.get(f'/api/estimates/{eid}').get_json()
        assert stale_tab['visualizer']['selections']['roofing']['better']['color_name'] == 'Old color'

        uploaded = client.post(f'/api/estimates/{eid}/visualizer/asset', json={
            'kind': 'render', 'tier': 'better', 'elevation_id': 'front',
            'elevation_name': 'Front', 'ext': 'jpg', 'content_b64': _jpeg_b64(),
        })
        assert uploaded.status_code == 201, uploaded.get_data(as_text=True)
        new_render = uploaded.get_json()['filename']

        state = client.put(f'/api/estimates/{eid}/visualizer/state', json={
            'scope': ['roof', 'siding'],
            'favorite_tier': 'better',
            'concept_names': {'better': 'Updated concept'},
            'selections': {
                'roofing': {'better': {'color_name': 'New color'}},
                'siding': {'better': {'color_name': 'New siding'}},
            },
        })
        assert state.status_code == 200, state.get_data(as_text=True)

        # A normal estimate field still saves from the old tab; only the newer
        # server-owned Design Studio state is protected from the stale payload.
        stale_tab['notes_customer'] = 'Ordinary estimate edits still save.'
        assert client.put(f'/api/estimates/{eid}', json=stale_tab).status_code == 200

        fresh = client.get(f'/api/estimates/{eid}').get_json()
        vz = fresh['visualizer']
        assert fresh['notes_customer'] == 'Ordinary estimate edits still save.'
        assert vz['scope'] == ['roof', 'siding']
        assert vz['favorite_tier'] == 'better'
        assert vz['concept_names']['better'] == 'Updated concept'
        assert vz['selections']['roofing']['better']['color_name'] == 'New color'
        assert vz['tier_renders']['better'] == new_render
        assert vz['elevations']['front']['tier_renders']['better'] == new_render
    finally:
        client.delete(f'/api/estimates/{eid}')
