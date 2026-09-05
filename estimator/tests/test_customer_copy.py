"""The customer's own copy of the contract they just signed.

sig_email was collected on the signing form, written into the signature
certificate, repeated in the rep's notification — and never used to send the
customer anything. send_signature_notification resolved exactly one recipient,
`<salesperson>@projectoneroofing.com`, and the post-sign pipeline's other three
jobs (file the PDF, push to Base44, build the packets) are all internal. The
homeowner saw a confirmation screen, closed the tab, and had nothing.
"""
import pytest

import app as A


@pytest.fixture(autouse=True)
def clean_slate():
    for eid in list(A.est_ids()):
        A.est_delete(eid)
    yield
    for eid in list(A.est_ids()):
        A.est_delete(eid)


@pytest.fixture
def outbox(monkeypatch):
    sent = []

    def _fake(subject, html, to_addr, cc=None, attachments=None, bcc=None):
        sent.append({'subject': subject, 'html': html, 'to': to_addr,
                     'cc': cc, 'attachments': attachments})
        return True

    monkeypatch.setattr(A, '_send_email', _fake)
    return sent


def _est(sig_email='signer@example.com', cust_email='onfile@example.com'):
    return {
        'estimate_id': 'c1', 'salesperson': 'luke', 'estimate_type': 'retail',
        'share_token': 'tok-c1',
        'customer': {'name': 'Jon Smith', 'email': cust_email,
                     'address': {'city': 'Loveland', 'state': 'CO'}},
        'pricing': {'mode': 'margin'},
        'signature': {'name': 'Jon Smith', 'email': sig_email,
                      'signed_at': '2026-09-01T15:04:05Z'},
        'trades': {'roofing': {
            'enabled': True, 'mode': 'simple',
            'line_items': [{'name': 'Roof', 'quantity': 1,
                            'unit_price': 24000.0, 'unit_cost': 15000.0}],
        }},
    }


def test_the_customer_gets_their_contract(outbox):
    assert A.send_customer_signed_copy(_est(), pdf_bytes=b'%PDF-1.4 fake')
    assert outbox[0]['to'] == 'signer@example.com'


def test_the_pdf_is_attached(outbox):
    A.send_customer_signed_copy(_est(), pdf_bytes=b'%PDF-1.4 fake')
    name, blob = outbox[0]['attachments'][0]
    assert name.endswith('-signed-contract.pdf')
    assert blob == b'%PDF-1.4 fake'


def test_the_signing_address_wins_over_the_one_on_file(outbox):
    """They typed it at the moment of signing — that is the address they expect
    the copy at."""
    A.send_customer_signed_copy(_est(sig_email='typed@example.com'),
                                pdf_bytes=b'x')
    assert outbox[0]['to'] == 'typed@example.com'


def test_it_falls_back_to_the_customer_record(outbox):
    """The email field on the sign form is optional and always has been."""
    A.send_customer_signed_copy(_est(sig_email=''), pdf_bytes=b'x')
    assert outbox[0]['to'] == 'onfile@example.com'


def test_no_address_anywhere_is_a_skip_not_a_crash(outbox):
    assert A.send_customer_signed_copy(_est(sig_email='', cust_email='')) is False
    assert outbox == []


def test_the_rep_is_copied(outbox):
    """So the thread the customer might reply to has the rep on it."""
    A.send_customer_signed_copy(_est(), pdf_bytes=b'x')
    assert outbox[0]['cc'] == 'luke@projectoneroofing.com'


def test_an_oversized_pdf_links_instead_of_bouncing(outbox):
    """A signed PDF carrying photos can run large. Over the cap the mail links
    to the signing page rather than hitting the recipient's attachment limit
    and delivering nothing at all."""
    big = b'x' * ((A.CUSTOMER_COPY_MAX_MB + 1) * 1024 * 1024)
    A.send_customer_signed_copy(_est(), pdf_bytes=big)
    assert outbox[0]['attachments'] is None
    assert 'too large to attach' in outbox[0]['html']


def test_a_send_failure_never_escapes(monkeypatch):
    """It runs on the post-sign thread, by which point the signature is already
    stored and the funnel already notified. Nothing here may take that down."""
    def _boom(*a, **k):
        raise RuntimeError('SMTP is on fire')
    monkeypatch.setattr(A, '_send_email', _boom)
    assert A.send_customer_signed_copy(_est(), pdf_bytes=b'x') is False


def test_the_sign_form_says_what_the_email_is_for():
    """It read "optional, for your records" while going nowhere."""
    form = A._cv_sig_form('/sign/tok', agree_text='ok')
    assert 'we email your signed contract here' in form
