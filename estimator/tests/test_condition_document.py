"""The roof health report as its own document.

It used to exist only as a PAGE of the proposal, so an inspection-only client
or a realtor had to be sent an estimate to receive a report. Now it files like
the roof certificate does. What is worth a test rather than a read-through:

1. **One set of decisions, two renderers.** The /sign block and the PDF both
   lay out condition_report_view(). If the PDF ever computes its own totals,
   the document a realtor holds and the page a homeowner reads can disagree
   about what the roof needs.
2. **The proposal's print chip does not reach the document.** Unticking Roof
   Health means "leave it out of the estimate", not "this report may not be
   issued" — a rep who does that and then clicks the card must not get a
   blank PDF or a refusal.
3. **Nothing to report means no document**, never an empty PDF with a logo.
"""
import io

import pytest


def _pdf_text(raw):
    try:
        from pypdf import PdfReader
    except ImportError:
        pytest.skip('pypdf not installed')
    r = PdfReader(io.BytesIO(raw))
    return '\n'.join(p.extract_text() or '' for p in r.pages)


def _pc(audience='homeowner', cost='$1,500', **roof):
    sec = {'enabled': True, 'grade': 'C', 'summary': 'Granule loss on the south slope.',
           'material_type': 'Asphalt', 'age_years': '14', 'pitch': '6/12',
           'findings': [{'id': 'f1', 'area': 'South slope', 'severity': 'medium',
                         'description': 'Hail bruising, 6 per test square'}],
           'recommendations': [{'id': 'r1', 'priority': 'soon',
                                'description': 'Replace damaged shingles',
                                'cost_range': cost}]}
    sec.update(roof)
    return {'audience': audience, 'inspection_date': '2026-09-10',
            'property_name': '', 'executive_notes': 'Serviceable, with repairs.',
            'report_photo_ids': [], 'sections': {'roof': sec}}


def _est(**over):
    est = {'estimate_id': 'abcd1234-0000-0000-0000-000000000000',
           'customer': {'name': 'Dana Whitfield',
                        'address': {'street': '1418 Sycamore Ct', 'city': 'Loveland',
                                    'state': 'CO', 'zip': '80537'}},
           'property_condition': _pc()}
    est.update(over)
    return est


def _mk(client, pc, **extra):
    est_id = client.post('/api/estimates', json={}).get_json()['estimate_id']
    doc = client.get(f'/api/estimates/{est_id}').get_json()
    doc['customer'] = {'name': 'Dana Whitfield',
                       'address': {'street': '1418 Sycamore Ct', 'city': 'Loveland',
                                   'state': 'CO', 'zip': '80537'}}
    doc['property_condition'] = pc
    doc.update(extra)
    client.put(f'/api/estimates/{est_id}', json=doc)
    return est_id


# ── one view, two renderers ─────────────────────────────────────────────

def test_the_pdf_and_the_sign_page_print_the_same_total(A):
    est = _est()
    html = A._cv_condition_block(est)
    text = _pdf_text(A.build_condition_report_pdf(est))
    assert '$1,500.00' in html
    assert '$1,500.00' in text


def test_a_legacy_range_keeps_its_plus_in_the_pdf_too(A):
    est = _est(property_condition=_pc(cost='$8,000 – $12,000'))
    assert A.condition_report_view(est)['costs']['plus'] == '+'
    assert '$8,000.00+' in _pdf_text(A.build_condition_report_pdf(est))


def test_a_single_price_totals_exactly(A):
    est = _est()
    assert A.condition_report_view(est)['costs']['plus'] == ''
    assert '$1,500.00+' not in _pdf_text(A.build_condition_report_pdf(est))


def test_hoa_wording_reaches_the_pdf(A):
    est = _est(property_condition=_pc(audience='hoa'))
    text = _pdf_text(A.build_condition_report_pdf(est))
    assert 'Property Condition Report' in text
    assert 'Estimated Repair Investment' in text


def test_a_legacy_roof_health_estimate_still_reports(A):
    """Estimates saved before property_condition existed hold roof_health."""
    est = {'customer': {'name': 'X'},
           'roof_health': {'condition': 'fair', 'summary': 'Old report',
                           'findings': [], 'recommendations': []}}
    v = A.condition_report_view(est)
    assert v and v['sections'][0]['grade'] == 'C'
    assert A.build_condition_report_pdf(est)


# ── the print chip belongs to the proposal ─────────────────────────────

def test_unticking_roof_health_hides_it_from_the_proposal_only(A):
    est = _est(page_visibility={'report': False})
    assert A._cv_condition_block(est) == ''
    assert A.condition_report_view(est) is not None
    assert 'Home Condition Report' in _pdf_text(A.build_condition_report_pdf(est))


# ── nothing to report ──────────────────────────────────────────────────

def test_an_ungraded_report_is_not_a_document(A):
    est = _est(property_condition=_pc(grade=''))
    assert A.condition_report_view(est) is None
    with pytest.raises(ValueError):
        A.build_condition_report_pdf(est)


def test_the_post_refuses_an_empty_report_and_writes_nothing(client):
    est_id = _mk(client, _pc(grade=''))
    r = client.post(f'/api/estimates/{est_id}/condition-report', json={})
    assert r.status_code == 400
    doc = client.get(f'/api/estimates/{est_id}').get_json()
    assert not [a for a in doc.get('attachments') or []
                if a.get('doc_type') == 'condition_report']


# ── filing ─────────────────────────────────────────────────────────────

def test_issuing_files_one_report_and_reissuing_replaces_it(client):
    est_id = _mk(client, _pc())
    for _ in range(2):
        r = client.post(f'/api/estimates/{est_id}/condition-report', json={})
        assert r.status_code == 200, r.get_json()
    doc = client.get(f'/api/estimates/{est_id}').get_json()
    reports = [a for a in doc['attachments'] if a.get('doc_type') == 'condition_report']
    assert len(reports) == 1
    assert reports[0]['server_generated'] is True
    assert reports[0]['show_in_estimate'] is False


def test_put_saves_fields_and_never_builds(client):
    est_id = _mk(client, _pc())
    r = client.put(f'/api/estimates/{est_id}/condition-report',
                   json={'prepared_for': 'Sandy Ruiz, Coldwell Banker',
                         'cover_note': 'x' * 9000, 'junk': 'dropped'})
    assert r.status_code == 200
    doc = client.get(f'/api/estimates/{est_id}').get_json()
    cr = doc['condition_report']
    assert cr['prepared_for'] == 'Sandy Ruiz, Coldwell Banker'
    assert len(cr['cover_note']) == 4000
    assert 'junk' not in cr
    assert not [a for a in doc.get('attachments') or []
                if a.get('doc_type') == 'condition_report']


def test_prepared_for_prints_on_the_report(client):
    est_id = _mk(client, _pc())
    client.put(f'/api/estimates/{est_id}/condition-report',
               json={'prepared_for': 'Sandy Ruiz, Coldwell Banker'})
    client.post(f'/api/estimates/{est_id}/condition-report', json={})
    doc = client.get(f'/api/estimates/{est_id}').get_json()
    att = next(a for a in doc['attachments'] if a['doc_type'] == 'condition_report')
    raw = client.get(f'/uploads/{att["filename"]}').data
    assert 'Sandy Ruiz, Coldwell Banker' in _pdf_text(raw)


def test_get_summarizes_without_the_body(client):
    est_id = _mk(client, _pc())
    d = client.get(f'/api/estimates/{est_id}/condition-report').get_json()
    assert d['summary']['sections'][0]['grade'] == 'C'
    assert d['summary']['cost_total'] == 1500
