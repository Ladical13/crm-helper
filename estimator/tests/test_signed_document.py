"""The signed contract must land in the Documents tab.

`_post_sign_pipeline` builds the signed PDF once and reuses it for three
things: the local Documents attachment, the CRM push, and the packets. Every
step of that pipeline is wrapped in try/except and only *prints* on failure —
which is right (a customer signing must never 500 because Base44 is down) but
means a broken signed-contract attachment is completely silent. The rep just
finds an empty Documents tab and has nowhere to look.

The sign route runs that pipeline on a daemon thread, so these tests join it
and assert in the foreground — a backgrounded assertion is not an assertion.
They used to call `_post_sign_pipeline` directly INSTEAD of joining, which left
the direct call racing the thread: two pipelines reading, modifying and saving
the same estimate at once. On CI that intermittently lost the attachment the
other had just written and the suite failed with 0 signed contracts, blocking
a deploy over a bug that was never in the app.
"""
import os
import threading

import pytest


def _signable(client, A, *, with_items=True):
    """A signed-ready estimate with real line items, plus its share token."""
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    doc = A.est_load(eid)
    doc['customer'] = {'name': 'Ada Lovelace', 'email': 'ada@example.com',
                       'phone': '9705550100',
                       'address': {'street': '12 Analytical Way', 'city': 'Loveland',
                                   'state': 'CO', 'zip': '80537'}}
    doc['estimate_type'] = 'retail'
    doc['selected_tier'] = 'better'
    doc.setdefault('trades', {})
    doc['trades']['roofing'] = {
        'enabled': True, 'mode': 'simple',
        'line_items': ([{'name': 'Architectural shingles', 'quantity': 32,
                         'unit': 'SQ', 'cost': 142,
                         'tiers': {t: {'included': True} for t in
                                   ('good', 'better', 'best')}},
                        {'name': 'Synthetic underlayment', 'quantity': 32,
                         'unit': 'SQ', 'cost': 18,
                         'tiers': {t: {'included': True} for t in
                                   ('good', 'better', 'best')}}]
                       if with_items else []),
        'tier_bundles': {'good': '', 'better': '', 'best': ''},
        'colors': {},
    }
    doc['shingle_selection'] = {'enabled': False, 'options': [], 'chosen': ''}
    doc['contract_text'] = 'Terms and conditions apply to this agreement.'
    token = 'signed-doc-tok-' + eid[:8]
    doc['share_token'] = token
    A.est_save(doc)
    return eid, token


def _sign(client, A, token):
    r = client.post(f'/sign/{token}', data={
        'sig_name': 'Ada Lovelace', 'sig_email': 'ada@example.com',
        'selected_tier': 'better', 'tier_roofing': 'better', 'agree': 'on',
    })
    assert r.status_code == 200, r.data[:400]
    _await_post_sign(A)


def _await_post_sign(A, timeout=60):
    """Wait for the pipeline the sign route spawned. The thread is started
    before the route returns, so by here it is either running or already done;
    either way nothing else may touch the estimate until it finishes."""
    for t in threading.enumerate():
        if t.name == A.POST_SIGN_THREAD:
            t.join(timeout)
            assert not t.is_alive(), 'the post-sign pipeline never finished'


def _signed_atts(client, eid):
    doc = client.get(f'/api/estimates/{eid}').get_json()
    return [a for a in (doc.get('attachments') or [])
            if a.get('doc_type') == 'signed_contract']


def test_signing_files_the_contract_in_documents(client, A):
    eid, token = _signable(client, A)
    _sign(client, A, token)

    atts = _signed_atts(client, eid)
    assert len(atts) == 1, 'the signed contract never reached the Documents tab'
    att = atts[0]
    assert att['server_generated'] is True
    # Internal copy: the customer already has this on their own page.
    assert att['show_in_estimate'] is False
    assert att['label']


def test_the_filed_pdf_exists_on_disk_and_is_readable(client, A):
    eid, token = _signable(client, A)
    _sign(client, A, token)

    att = _signed_atts(client, eid)[0]
    path = os.path.join(A.UPLOADS_DIR, *att['filename'].split('/'))
    assert os.path.exists(path), f'attachment row points at a missing file: {path}'
    assert os.path.getsize(path) > 5000, 'signed PDF is suspiciously small'
    with open(path, 'rb') as f:
        assert f.read(5) == b'%PDF-', 'filed document is not a PDF'


def test_the_filed_pdf_is_the_full_estimate_not_a_stub(client, A):
    """It is the whole document — cover, scope, pricing, terms, and the
    signature block — because that is what the rep needs to hand back to a
    customer who asks what they signed."""
    pymupdf = pytest.importorskip('pymupdf')
    eid, token = _signable(client, A)
    _sign(client, A, token)

    att = _signed_atts(client, eid)[0]
    path = os.path.join(A.UPLOADS_DIR, *att['filename'].split('/'))
    doc = pymupdf.open(path)
    text = '\n'.join(p.get_text() for p in doc)

    assert doc.page_count >= 2, 'a one-page signed contract means the body is missing'
    assert 'SIGNED CONTRACT' in text.upper()
    assert 'Ada Lovelace' in text            # who signed
    assert 'Architectural shingles' in text  # the scope they agreed to


def test_the_rep_can_open_it_through_the_uploads_route(client, A):
    """The Documents tab links straight at /uploads/<file>. A row that 404s is
    the same as no row at all."""
    eid, token = _signable(client, A)
    _sign(client, A, token)

    att = _signed_atts(client, eid)[0]
    r = client.get(f"/uploads/{att['filename']}")
    assert r.status_code == 200, f"Documents link is dead: {att['filename']}"
    assert r.data[:5] == b'%PDF-'


def test_resigning_swaps_rather_than_stacks(client, A):
    """One signed contract per estimate. Two rows means the rep has to guess
    which is authoritative."""
    eid, token = _signable(client, A)
    _sign(client, A, token)
    for _ in range(3):
        A._post_sign_pipeline(eid)
    assert len(_signed_atts(client, eid)) == 1
