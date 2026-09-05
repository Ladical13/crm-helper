"""Estimate expiry — the promise the tool printed and never kept.

valid_until defaults to 30 days out and prints on the customer page, the PDF
and the signed contract as "Pricing held until <date>". Nothing checked it:
customer_sign validated the name, the initials, the shingle colour, the siding
colour and whether a package was still offered, and never looked at the date.
A homeowner could open a link from six months ago and sign at six-month-old
shingle and OSB pricing, and the tool would file the contract, build the packet
and hand the job to production as an ordinary win.
"""
from datetime import date, datetime, timedelta

import pytest

import app as A


@pytest.fixture(autouse=True)
def clean_slate():
    for eid in list(A.est_ids()):
        A.est_delete(eid)
    yield
    for eid in list(A.est_ids()):
        A.est_delete(eid)


def _days(n):
    return (date.today() + timedelta(days=n)).isoformat()


def _seed(eid, valid_until, *, signed=False):
    doc = {
        'estimate_id': eid, 'salesperson': 'luke', 'estimate_type': 'retail',
        'valid_until': valid_until,
        'share_token': 'tok-' + eid,
        'customer': {'name': 'Test', 'email': 'test@example.com',
                     'address': {'city': 'Loveland'}},
        'pricing': {'mode': 'margin'},
        'contract_initials': [],
        'trades': {'roofing': {
            'enabled': True, 'mode': 'simple',
            'line_items': [{'name': 'Roof', 'quantity': 1,
                            'unit_price': 12000.0, 'unit_cost': 7000.0}],
        }},
    }
    if signed:
        doc['signature'] = {'name': 'Test', 'signed_at': '2026-01-01T00:00:00Z'}
        doc['status'] = 'accepted'
    A.est_save(doc)
    return doc


def test_a_lapsed_estimate_is_expired():
    assert A._est_expired(_seed('e1', _days(-1)))


def test_todays_expiry_still_signs():
    """"Held until the 14th" means through the 14th, not up to it."""
    assert not A._est_expired(_seed('e2', _days(0)))


def test_a_future_date_is_not_expired():
    assert not A._est_expired(_seed('e3', _days(30)))


def test_no_date_means_no_expiry():
    assert not A._est_expired(_seed('e4', ''))


def test_an_unparseable_date_means_no_expiry():
    """A typo in that box must not lock a customer out of a good estimate."""
    assert not A._est_expired(_seed('e5', 'next tuesday'))


def test_a_signed_estimate_is_never_expired():
    """The price was locked by the signature, not by the date — and the signed
    confirmation has to keep rendering forever."""
    assert not A._est_expired(_seed('e6', _days(-400), signed=True))


def test_the_customer_page_withdraws_the_signature_form(anon):
    _seed('e7', _days(-5))
    html = anon.get('/sign/tok-e7').get_data(as_text=True)
    assert 'This pricing has expired' in html
    # The shared page JS references the input by name to drive the signature
    # preview; what must be gone is the input itself.
    assert 'class="cvinput" name="sig_name"' not in html, \
        'the sign form must be withdrawn'


def test_the_estimate_itself_still_renders(anon):
    """They came back to look, which makes them a lead, not a dead end."""
    _seed('e8', _days(-5))
    html = anon.get('/sign/tok-e8').get_data(as_text=True)
    assert 'Call ' in html and A.COMPANY_PHONE_DISPLAY in html


def test_a_live_estimate_still_shows_the_form(anon):
    _seed('e9', _days(10))
    html = anon.get('/sign/tok-e9').get_data(as_text=True)
    assert 'class="cvinput" name="sig_name"' in html
    assert 'This pricing has expired' not in html


def test_a_stale_tab_cannot_post_a_signature(anon):
    """The rendered page withdrew the form, so reaching the POST means a tab
    opened before it expired — or a replay."""
    _seed('e10', _days(-5))
    r = anon.post('/sign/tok-e10', data={'sig_name': 'Jon Smith', 'agree': 'on'})
    assert r.status_code == 410
    assert A.est_load('e10').get('signature') is None


def test_the_rep_is_told_once_that_someone_came_back(anon, monkeypatch):
    """An expired-link click is a warm lead the tool used to throw away — and a
    customer refreshing five times is one lead, not five."""
    sent = []
    monkeypatch.setattr(A, '_notify_expired_view', lambda est: sent.append(est))
    _seed('e11', _days(-5))
    for _ in range(3):
        anon.get('/sign/tok-e11')
    # The notification runs on a thread; the flag it guards is the durable part.
    assert A.est_load('e11').get('expired_notified_at')


def test_change_orders_are_unaffected():
    """A customer approving an add-on is not re-buying the original job, and
    change orders carry their own dates."""
    form = A._cv_sig_form('/sign-co/tok', agree_text='ok')
    assert 'name="sig_name"' in form


# ── The public routes a customer actually needs ───────────────────────────

def test_the_customer_can_download_their_pdf_without_logging_in(anon):
    """The "save a copy before you decide" card sits on every /sign variant and
    links to /sign/<token>/download.pdf — but that endpoint was not on the
    default-deny allowlist, so the button a CUSTOMER sees bounced them to a
    login page. Same token, same estimate, no way through for the one person it
    is for."""
    _seed('e12', _days(10))
    r = anon.get('/sign/tok-e12/download.pdf')
    assert r.status_code == 200, 'a customer must not be asked to log in'
    assert r.data[:4] == b'%PDF'


def test_a_bad_token_still_404s_rather_than_redirecting(anon):
    """Public does not mean unguarded — the token is the whole protection."""
    assert anon.get('/sign/not-a-real-token/download.pdf').status_code == 404
