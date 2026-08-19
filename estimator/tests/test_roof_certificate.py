"""Roof certificate — the realtor-facing labor-only warranty.

Three things here are worth a test rather than a read-through:

1. **The coverage window runs from the inspection, not from generation.**
   Re-issuing a certificate (fixing a typo, adding the buyer's name) must
   never quietly extend the warranty. That is a promise the company is on the
   hook for, and it is invisible when it breaks.
2. **Month arithmetic clamps.** An inspection on the 31st plus six months is
   not "the 31st of a month that has 30 days".
3. **The term is a closed set.** The PDF prints "N MONTHS, LABOR ONLY" from
   whatever is stored, so an 18 or a 240 that slipped through the API would
   print as a real promise.

The JS preview in static/app.js mirrors _add_months so the rep sees the same
expiration date the PDF will carry; test_expiry_preview_mirrors_server keeps
the two formulas honest about the clamp.
"""
import json
import os
import re
from datetime import date

import pytest

HERE   = os.path.dirname(os.path.abspath(__file__))
EST    = os.path.dirname(HERE)
APP_JS = os.path.join(EST, 'static', 'app.js')


def _mk(client, cert):
    """Create an estimate carrying `cert` and return its id."""
    est_id = client.post('/api/estimates', json={}).get_json()['estimate_id']
    doc = client.get(f'/api/estimates/{est_id}').get_json()
    doc['customer'] = {'name': 'Dana Whitfield',
                       'address': {'street': '1418 Sycamore Ct', 'city': 'Loveland',
                                   'state': 'CO', 'zip': '80537'}}
    client.put(f'/api/estimates/{est_id}', json=doc)
    if cert:
        client.put(f'/api/estimates/{est_id}/roof-certificate', json=cert)
    return est_id


# ── term arithmetic ────────────────────────────────────────────────────

def test_term_runs_from_inspection_date(A):
    start, end = A._roof_cert_dates({'inspection_date': '2026-08-14',
                                     'term_months': 12})
    assert start == date(2026, 8, 14)
    assert end   == date(2027, 8, 14)


@pytest.mark.parametrize('start,months,expected', [
    ('2026-01-31',  1, date(2026, 2, 28)),   # short month
    ('2026-08-31',  6, date(2027, 2, 28)),   # short month, across a year
    ('2028-02-29', 12, date(2029, 2, 28)),   # leap day -> non-leap year
    ('2026-12-15',  6, date(2027, 6, 15)),   # plain year rollover
    ('2026-06-30', 24, date(2028, 6, 30)),
])
def test_add_months_clamps_to_month_end(A, start, months, expected):
    assert A._add_months(date.fromisoformat(start), months) == expected


def test_no_term_yields_no_expiration(A):
    start, end = A._roof_cert_dates({'inspection_date': '2026-08-14'})
    assert start == date(2026, 8, 14)
    assert end is None


def test_unparseable_inspection_date_is_not_a_crash(A):
    assert A._roof_cert_dates({'inspection_date': 'sometime in August',
                               'term_months': 12}) == (None, None)


# ── the term is a closed set ───────────────────────────────────────────

@pytest.mark.parametrize('bad', [18, 240, 0, -12, 'twelve', 1.5, None])
def test_sanitizer_drops_terms_outside_the_offered_set(A, bad):
    assert 'term_months' not in A._sanitize_roof_cert({'term_months': bad})


@pytest.mark.parametrize('good', [6, 12, 24, '12'])
def test_sanitizer_keeps_offered_terms(A, good):
    assert A._sanitize_roof_cert({'term_months': good})['term_months'] == int(good)


def test_sanitizer_ignores_unknown_fields(A):
    out = A._sanitize_roof_cert({'condition': 'Good', 'is_admin': True,
                                 'signature': 'forged'})
    assert out == {'condition': 'Good'}


def test_sanitizer_caps_field_lengths(A):
    out = A._sanitize_roof_cert({'condition': 'x' * 5000, 'findings': 'y' * 9000})
    assert len(out['condition']) == 300     # one-liner
    assert len(out['findings'])  == 4000    # paragraph


# ── API ────────────────────────────────────────────────────────────────

def test_issue_requires_an_inspection_date(client):
    est_id = _mk(client, {'term_months': 12})
    r = client.post(f'/api/estimates/{est_id}/roof-certificate', json={})
    assert r.status_code == 400
    assert 'inspection date' in r.get_json()['error'].lower()


def test_issue_requires_a_term(client):
    est_id = _mk(client, {'inspection_date': '2026-08-14'})
    r = client.post(f'/api/estimates/{est_id}/roof-certificate', json={})
    assert r.status_code == 400
    assert 'term' in r.get_json()['error'].lower()


def test_issue_attaches_a_certificate(client):
    est_id = _mk(client, {'inspection_date': '2026-08-14', 'term_months': 12,
                          'inspector': 'Luke Durnbaugh', 'condition': 'Good'})
    r = client.post(f'/api/estimates/{est_id}/roof-certificate', json={})
    assert r.status_code == 200
    att = r.get_json()['attachment']
    assert att['doc_type'] == 'roof_certificate'
    assert att['server_generated'] is True
    # Internal document: never shown on the customer's estimate page.
    assert att['show_in_estimate'] is False


def test_reissue_replaces_rather_than_accumulates(client):
    """A certificate is a single authoritative document. Two live copies in the
    job file is how a realtor ends up holding the wrong one."""
    est_id = _mk(client, {'inspection_date': '2026-08-14', 'term_months': 12})
    for _ in range(3):
        assert client.post(f'/api/estimates/{est_id}/roof-certificate',
                           json={}).status_code == 200
    est = client.get(f'/api/estimates/{est_id}').get_json()
    certs = [a for a in est['attachments'] if a.get('doc_type') == 'roof_certificate']
    assert len(certs) == 1


def test_reissue_does_not_extend_coverage(client):
    """The whole point of anchoring on inspection_date."""
    est_id = _mk(client, {'inspection_date': '2026-08-14', 'term_months': 12})
    client.post(f'/api/estimates/{est_id}/roof-certificate', json={})
    est = client.get(f'/api/estimates/{est_id}').get_json()
    before = est['roof_certificate']['inspection_date']
    # Edit something unrelated and re-issue.
    client.put(f'/api/estimates/{est_id}/roof-certificate',
               json={'buyer_name': 'Tomas Reyes'})
    client.post(f'/api/estimates/{est_id}/roof-certificate', json={})
    est = client.get(f'/api/estimates/{est_id}').get_json()
    assert est['roof_certificate']['inspection_date'] == before


def test_certificate_is_not_gated_on_a_signature(client):
    """Unlike the production and permit packets: there is no contract here."""
    est_id = _mk(client, {'inspection_date': '2026-08-14', 'term_months': 12})
    est = client.get(f'/api/estimates/{est_id}').get_json()
    assert not est.get('signature')
    assert client.post(f'/api/estimates/{est_id}/roof-certificate',
                       json={}).status_code == 200


def test_save_merges_rather_than_replaces(client):
    est_id = _mk(client, {'inspection_date': '2026-08-14', 'term_months': 12,
                          'inspector': 'Luke Durnbaugh'})
    client.put(f'/api/estimates/{est_id}/roof-certificate',
               json={'buyer_name': 'Tomas Reyes'})
    rc = client.get(f'/api/estimates/{est_id}').get_json()['roof_certificate']
    assert rc['inspector']   == 'Luke Durnbaugh'   # survived
    assert rc['buyer_name']  == 'Tomas Reyes'
    assert rc['term_months'] == 12


def test_defaults_endpoint_serves_the_exclusions(client):
    d = client.get('/api/roof-certificate-defaults').get_json()
    assert d['terms'] == [6, 12, 24]
    body = d['exclusions'].lower()
    assert 'labor only' in body
    assert 'hail' in body                       # storm damage is excluded
    assert 'not a manufacturer warranty' in body


def test_certificate_endpoints_require_login(anon):
    assert anon.put('/api/estimates/x/roof-certificate', json={}).status_code == 401
    assert anon.post('/api/estimates/x/roof-certificate', json={}).status_code == 401


# ── PDF ────────────────────────────────────────────────────────────────

def _cert_text(A, cert, customer=None):
    import pymupdf
    est = {'estimate_id': '7f3a91c2-0000-4000-8000-000000000001',
           'salesperson': 'luke',
           'customer': customer or {'name': 'Dana Whitfield', 'address': {}},
           'roof_certificate': cert}
    doc = pymupdf.open(stream=A.build_roof_certificate_pdf(est), filetype='pdf')
    return doc.page_count, '\n'.join(p.get_text() for p in doc)


def test_pdf_states_the_term_and_both_dates(A):
    pages, text = _cert_text(A, {'inspection_date': '2026-08-14',
                                 'term_months': 12, 'condition': 'Good'})
    assert '12 MONTHS, LABOR ONLY' in text
    assert 'August 14, 2026' in text
    assert 'August 14, 2027' in text


def test_pdf_carries_the_standard_exclusions_when_blank(A):
    _, text = _cert_text(A, {'inspection_date': '2026-08-14', 'term_months': 6})
    assert 'hail' in text.lower()
    assert 'not a manufacturer warranty' in text.lower()


def test_pdf_prefers_a_custom_exclusion_block(A):
    _, text = _cert_text(A, {'inspection_date': '2026-08-14', 'term_months': 6,
                             'exclusions': 'Chimney cricket excluded.'})
    assert 'Chimney cricket excluded.' in text
    assert 'hail' not in text.lower()


def test_pdf_signature_block_names_the_inspector_and_the_date(A):
    _, text = _cert_text(A, {'inspection_date': '2026-08-14', 'term_months': 12,
                             'inspector': 'Luke Durnbaugh'})
    assert 'Luke Durnbaugh' in text
    assert 'Authorized Signature' in text
    assert 'Inspection Date' in text


def test_pdf_falls_back_to_the_salesperson_for_the_signature(A):
    _, text = _cert_text(A, {'inspection_date': '2026-08-14', 'term_months': 12})
    assert 'Luke' in text     # est['salesperson'] == 'luke', title-cased


def test_a_typical_certificate_is_one_page(A):
    """It gets handed to a realtor and dropped in a transaction file. A
    signature stranded on page 2 reads as an unsigned document."""
    pages, _ = _cert_text(A, {
        'inspection_date': '2026-08-14', 'term_months': 12,
        'inspector': 'Luke Durnbaugh', 'condition': 'Good',
        'roof_material': 'CertainTeed Landmark architectural asphalt shingle',
        'roof_age': 'Approximately 11 years',
        'remaining_life': '8-12 years under normal weather',
        'findings': 'Field shingles are intact with good granule retention and no '
                    'evidence of thermal splitting. Ridge cap is sound. Two pipe-jack '
                    'boots showed UV cracking at the collar and were replaced. Attic '
                    'inspected from the hall access: no active moisture staining.',
        'repairs_made': 'Replaced (2) pipe-jack boots and resealed exposed fasteners.',
        'realtor_name': 'Marcy Ellison', 'realtor_brokerage': 'Front Range Realty Group',
        'realtor_phone': '970-555-0199', 'realtor_email': 'marcy@frontrangerealty.example',
        'buyer_name': 'Tomas Reyes', 'seller_name': 'Dana Whitfield',
        'closing_date': 'September 12, 2026',
    }, customer={'name': 'Dana Whitfield',
                 'address': {'street': '1418 Sycamore Ct', 'city': 'Loveland',
                             'state': 'CO', 'zip': '80537'}})
    assert pages == 1


def test_certificate_number_is_stable(A):
    est = {'estimate_id': '7f3a91c2-0000-4000-8000-000000000001'}
    assert A._roof_cert_number(est) == 'RC-7F3A91C2'
    assert A._roof_cert_number(est) == A._roof_cert_number(dict(est))


# ── client/server parity ───────────────────────────────────────────────

def test_expiry_preview_mirrors_server(A):
    """The rep reads the coverage window off the form before issuing. If the
    JS and the PDF disagree about the month-end clamp, the rep promises one
    date and the document carries another."""
    with open(APP_JS, encoding='utf-8') as f:
        js = f.read()
    m = re.search(r'function rcAddMonths\(iso, months\) \{([\s\S]*?)\n\}', js)
    assert m, 'rcAddMonths not found in app.js'
    body = m.group(1)
    # The clamp is the part that is easy to drop in a rewrite.
    assert 'Math.min(d, last)' in body
    # And the offered terms must match the server's closed set.
    terms = re.search(r'const RC_TERMS\s*=\s*\[([\d,\s]+)\]', js)
    assert terms, 'RC_TERMS not found in app.js'
    assert [int(x) for x in terms.group(1).split(',')] == list(A._ROOF_CERT_TERMS)
