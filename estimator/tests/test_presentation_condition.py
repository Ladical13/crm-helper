"""Customer presentations must show a report, never serialized form data."""
import pytest


def _render(A, **fields):
    est = {'customer': {'name': 'Sample Customer', 'address': {}},
           'trades': {}, **fields}
    with A.app.test_request_context('/'):
        return A.build_presentation_view(est, 'test-presentation')


@pytest.mark.parametrize('fields', [
    {},
    {'roof_health': {'findings': [], 'recommendations': [], 'report_photo_ids': []}},
    {'property_condition': {'sections': {'roof': {'enabled': True, 'grade': ''}},
                            'report_photo_ids': ['private-photo-id']}},
])
def test_empty_condition_reports_have_no_slide(A, fields):
    html = _render(A, **fields)
    assert '<div class="ps-cond">' not in html
    assert 'private-photo-id' not in html


def _report():
    return {'audience': 'hoa', 'report_photo_ids': ['private-photo-id'],
            'sections': {'roof': {
                'enabled': True, 'grade': 'D', 'summary': 'Wear at the edges',
                'findings': [{'area': 'North edge', 'severity': 'high',
                              'description': '<script>alert(1)</script> & lifting'}],
                'recommendations': [{'priority': 'immediate',
                                     'description': 'Repair flashing', 'cost_range': '$1,500'}],
            }}}


def test_structured_condition_report_is_readable_and_escaped(A):
    html = _render(A, property_condition=_report())
    assert '<div class="ps-cond">' in html
    for text in ('Property Condition Report', 'Grade D', 'Poor', 'North edge',
                 'High', 'Repair flashing', '$1,500', 'Estimated Total'):
        assert text in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt; &amp; lifting' in html
    assert '<script>alert(1)</script>' not in html
    assert 'private-photo-id' not in html
    assert "'enabled': True" not in html


def test_hidden_report_is_also_hidden_in_presentation(A):
    html = _render(A, property_condition=_report(), page_visibility={'report': False})
    assert '<div class="ps-cond">' not in html
    assert 'Repair flashing' not in html


def test_legacy_roof_report_uses_the_same_readable_format(A):
    html = _render(A, roof_health={
        'condition': 'poor', 'summary': 'Aging shingles',
        'findings': [{'area': 'Ridge', 'severity': 'high', 'description': 'Missing caps'}],
        'recommendations': [], 'report_photo_ids': ['private-photo-id'],
    })
    assert '<div class="ps-cond">' in html
    assert 'Grade D' in html
    assert 'Missing caps' in html
    assert 'private-photo-id' not in html
