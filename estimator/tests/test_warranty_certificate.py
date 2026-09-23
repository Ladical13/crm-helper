"""The post-job warranty certificate.

The workmanship term is already stated in five places (see
test_warranty_consistency.py). This document is the one a homeowner keeps for
years and hands a buyer, so it must say exactly what the sale promised and
nothing it did not:

1. **Retail reads the signed tier.** Best is Lifetime, Good and Better are
   5-year — off warranty_by_tier, never a literal of its own.
2. **Insurance is flat and names no package.** warranty_by_tier is empty there
   on purpose; the certificate reads WARRANTY_INSURANCE_FLAT.
3. **It refuses rather than guesses.** No signature, no completion date, no
   product, or a job whose warranty cannot be read → 400, nothing filed.
4. **The term runs from completion**, and a lifetime warranty prints no expiry.
"""
import inspect
import io
import re
from datetime import date

import pytest


def _text(raw):
    try:
        from pypdf import PdfReader
    except ImportError:
        pytest.skip('pypdf not installed')
    return '\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(raw)).pages)


def _signed(tier='better', est_type='retail', **over):
    est = {
        'estimate_id': 'beef0001-0000-0000-0000-000000000000',
        'estimate_type': est_type,
        'selected_tier': tier,
        'customer': {'name': 'Dana Whitfield',
                     'address': {'street': '1418 Sycamore Ct', 'city': 'Loveland',
                                 'state': 'CO', 'zip': '80537'}},
        'signature': {'signed_at': '2026-08-01T10:00:00Z', 'selected_tier': tier},
        'trades': {'roofing': {'enabled': True, 'mode': 'simple',
                               'line_items': [{'name': 'Shingles', 'quantity': 30,
                                               'unit': 'SQ', 'cost': 142}]}},
        'warranty_certificate': {'completion_date': '2026-09-02',
                                 'product_installed': 'CertainTeed Landmark Pro'},
    }
    if est_type == 'insurance':
        est['trades'] = {'insurance': {'enabled': True, 'line_items': [
            {'name': 'Remove & replace comp shingle', 'quantity': 30,
             'unit': 'SQ', 'unit_price': 400}]}}
    est.update(over)
    return est


# ── what it promises ───────────────────────────────────────────────────

@pytest.mark.parametrize('tier,expect', [('good', '5-year'), ('better', '5-year'),
                                         ('best', 'Lifetime')])
def test_retail_reads_the_signed_tier(A, tier, expect):
    term, _p, basis = A._warranty_manifest(_signed(tier))
    assert basis == 'tier'
    assert term == A._WARRANTY_BY_TIER[tier]
    assert expect in _text(A.build_warranty_certificate_pdf(_signed(tier)))


def test_five_years_runs_from_completion(A):
    text = _text(A.build_warranty_certificate_pdf(_signed('better')))
    assert 'September 02, 2031' in text


def test_a_lifetime_warranty_prints_no_expiration_date(A):
    text = _text(A.build_warranty_certificate_pdf(_signed('best')))
    assert 'as long as you own the home' in text
    assert not re.search(r'20[4-9]\d', text)


def test_insurance_is_flat_and_names_no_package(A):
    est = _signed(est_type='insurance')
    term, _p, basis = A._warranty_manifest(est)
    assert basis == 'insurance'
    assert term == A.WARRANTY_INSURANCE_FLAT
    text = _text(A.build_warranty_certificate_pdf(est)).lower()
    assert '5-year' in text
    for pkg in ('good package', 'better package', 'best package', 'lifetime'):
        assert pkg not in text


def test_the_builder_states_no_term_of_its_own(A):
    """A seventh spelling of the warranty is what this test exists to stop."""
    for fn in (A.build_warranty_certificate_pdf, A._warranty_manifest,
               A._warranty_cert_expiry):
        src = re.sub(r'"""[\s\S]*?"""', '', inspect.getsource(fn))   # code, not docs
        assert not re.search(r"'[^']*(5-year|5 year|Lifetime)[^']*'", src), fn.__name__


# ── refusals ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('over', [
    {'signature': None},
    {'warranty_certificate': {'product_installed': 'X'}},          # no completion
    {'warranty_certificate': {'completion_date': '2026-09-02'}},   # no product
    {'warranty_certificate': {'completion_date': 'soon',
                              'product_installed': 'X'}},
])
def test_it_refuses_rather_than_guesses(A, over):
    est = _signed(**over)
    assert A._warranty_cert_problem(est)
    with pytest.raises(ValueError):
        A.build_warranty_certificate_pdf(est)


def test_a_job_whose_warranty_cannot_be_read_is_refused(A):
    est = _signed(trades={})
    assert A._warranty_manifest(est)[2] == 'unknown'
    assert 'Could not tell' in A._warranty_cert_problem(est)


def test_the_completion_date_is_never_defaulted(client):
    """The contract date is offered as a fill, never stored as the answer."""
    est_id = client.post('/api/estimates', json={}).get_json()['estimate_id']
    d = client.get(f'/api/estimates/{est_id}/warranty-certificate').get_json()
    assert not d['warranty_certificate'].get('completion_date')
    assert d['problem']


def test_an_unsigned_estimate_files_nothing(client):
    est_id = client.post('/api/estimates', json={}).get_json()['estimate_id']
    client.put(f'/api/estimates/{est_id}/warranty-certificate',
               json={'completion_date': '2026-09-02', 'product_installed': 'X'})
    r = client.post(f'/api/estimates/{est_id}/warranty-certificate', json={})
    assert r.status_code == 400
    doc = client.get(f'/api/estimates/{est_id}').get_json()
    assert not [a for a in doc.get('attachments') or []
                if a.get('doc_type') == 'warranty_certificate']


def test_expiry_arithmetic(A):
    assert A._warranty_cert_expiry('5-year x', date(2026, 8, 31)) == (date(2031, 8, 31), False)
    assert A._warranty_cert_expiry('Lifetime x', date(2026, 8, 31)) == (None, True)
    assert A._warranty_cert_expiry('Workmanship guaranteed', date(2026, 8, 31)) == (None, False)
