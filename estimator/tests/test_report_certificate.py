"""The roof certificate as the condition report's optional last page.

A realtor's closing file loses the second of two PDFs, so the certificate can
ride inside the report. What that must never do:

1. **Say something different from the standalone certificate.** Both come out
   of _roof_cert_render(), and these tests read the text back to prove it —
   same number, same term, same dates.
2. **Carry a blank certificate.** A report with the certificate switched on and
   no inspection date or term is refused, by the same check the certificate's
   own POST uses, rather than issued with a certificate nobody can read.
3. **Extend coverage on re-issue.** The term runs from the inspection date, so
   issuing the report again next month must not move the expiry.
4. **Turn itself on.** Appending a warranty to a report is a legal promise, so
   the switch is a literal True and nothing else.
"""
import io
import re

import pytest

from test_condition_document import _mk, _pc


def _pages(raw):
    try:
        from pypdf import PdfReader
    except ImportError:
        pytest.skip('pypdf not installed')
    return [p.extract_text() or '' for p in PdfReader(io.BytesIO(raw)).pages]


CERT = {'inspection_date': '2026-09-10', 'term_months': 12,
        'inspected_by': 'Luke', 'condition': 'Good'}


def _issue(client, est_id):
    r = client.post(f'/api/estimates/{est_id}/condition-report', json={})
    assert r.status_code == 200, r.get_json()
    return client.get(f'/uploads/{r.get_json()["attachment"]["filename"]}').data


def _cert_bits(text):
    num = re.search(r'RC-[0-9A-F]+', text)
    return (num and num.group(0), '12 MONTHS' in text.upper(),
            'September 10, 2026' in text, 'September 10, 2027' in text)


def test_off_by_default(client):
    est_id = _mk(client, _pc())
    client.put(f'/api/estimates/{est_id}/roof-certificate', json=CERT)
    pages = _pages(_issue(client, est_id))
    assert not any('RC-' in p for p in pages)


@pytest.mark.parametrize('val', ['true', 1, 'yes'])
def test_only_a_literal_true_turns_it_on(client, val):
    est_id = _mk(client, _pc())
    client.put(f'/api/estimates/{est_id}/condition-report',
               json={'include_certificate': val})
    doc = client.get(f'/api/estimates/{est_id}').get_json()
    assert doc['condition_report']['include_certificate'] is False


def test_on_appends_the_same_certificate_as_the_standalone(client, A):
    est_id = _mk(client, _pc())
    client.put(f'/api/estimates/{est_id}/roof-certificate', json=CERT)
    client.put(f'/api/estimates/{est_id}/condition-report',
               json={'include_certificate': True})
    pages = _pages(_issue(client, est_id))
    est = client.get(f'/api/estimates/{est_id}').get_json()
    standalone = '\n'.join(_pages(A.build_roof_certificate_pdf(est)))

    # The certificate is the LAST page and only the last page.
    assert 'RC-' in pages[-1]
    assert not any('RC-' in p for p in pages[:-1])
    assert _cert_bits(pages[-1]) == _cert_bits(standalone)
    assert _cert_bits(pages[-1])[0] == A._roof_cert_number(est)


def test_the_certificate_page_carries_no_report_chrome(client):
    """The report's running header/footer stops before the certificate, which
    draws its own letterhead — and the report's own last page keeps its footer."""
    est_id = _mk(client, _pc())
    client.put(f'/api/estimates/{est_id}/roof-certificate', json=CERT)
    client.put(f'/api/estimates/{est_id}/condition-report',
               json={'include_certificate': True})
    pages = _pages(_issue(client, est_id))
    assert re.search(r'Page \d+ of \d+', pages[-2])
    assert not re.search(r'Page \d+ of \d+', pages[-1])


@pytest.mark.parametrize('cert', [
    {'term_months': 12},                          # no inspection date
    {'inspection_date': '2026-09-10'},            # no term
])
def test_a_blank_certificate_refuses_the_report_and_files_nothing(client, cert):
    est_id = _mk(client, _pc())
    client.put(f'/api/estimates/{est_id}/roof-certificate', json=cert)
    client.put(f'/api/estimates/{est_id}/condition-report',
               json={'include_certificate': True})
    r = client.post(f'/api/estimates/{est_id}/condition-report', json={})
    assert r.status_code == 400
    doc = client.get(f'/api/estimates/{est_id}').get_json()
    assert not [a for a in doc.get('attachments') or []
                if a.get('doc_type') == 'condition_report']


def test_both_posts_refuse_for_the_same_reason(client):
    est_id = _mk(client, _pc())
    client.put(f'/api/estimates/{est_id}/roof-certificate', json={'term_months': 12})
    client.put(f'/api/estimates/{est_id}/condition-report',
               json={'include_certificate': True})
    a = client.post(f'/api/estimates/{est_id}/roof-certificate', json={}).get_json()['error']
    b = client.post(f'/api/estimates/{est_id}/condition-report', json={}).get_json()['error']
    assert a in b


def test_reissuing_the_report_does_not_move_the_expiry(client):
    est_id = _mk(client, _pc())
    client.put(f'/api/estimates/{est_id}/roof-certificate', json=CERT)
    client.put(f'/api/estimates/{est_id}/condition-report',
               json={'include_certificate': True})
    first = _cert_bits(_pages(_issue(client, est_id))[-1])
    second = _cert_bits(_pages(_issue(client, est_id))[-1])
    assert first == second
    assert first[3]          # still September 10, 2027


def test_pull_from_report_offers_the_findings_as_text(client):
    est_id = _mk(client, _pc())
    d = client.get(f'/api/estimates/{est_id}/condition-report').get_json()
    assert 'Hail bruising, 6 per test square' in d['findings_text']
    assert 'Granule loss on the south slope.' in d['findings_text']
