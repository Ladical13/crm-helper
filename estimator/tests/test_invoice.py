"""GC invoice / quote: the plain, itemized document for general contractors.

What is worth a test rather than a read-through:

1. **The subtotal is the estimate's own total, to the cent.** invoice_rows()
   lists the rows _trade_subtotal prices. If the two ever disagree, a GC gets
   an invoice whose lines do not add up to the number the rep quoted.
2. **Every billed line is listed**, including customer_visible:false ones the
   homeowner PDF folds into the total. That was the whole ask: a GC checks the
   bill line by line.
3. **Supplements are listed and never totalled; only ACCEPTED change orders
   bill; payments reduce the balance.**
4. **A quote is a send; an invoice is a bill.** The margin floor gates the
   first and not the second.
5. **est['invoice'] is server-owned.** It holds payments received, so a stale
   whole-doc save must not roll it back.
"""
import pytest

import app as A
import demo_store
from portal import users as portal_users

PRICING = {'mode': 'margin', 'rate': 35,
           'tier_rates': {'good': 35, 'better': 35, 'best': 35}}
OTHER_REP = 'jacob'
if not portal_users.get(OTHER_REP):
    portal_users.create(OTHER_REP, password='test-only-password', role='rep',
                        full_name='Jacob')


@pytest.fixture(autouse=True)
def clean_slate():
    for eid in list(A.est_ids()):
        A.est_delete(eid)
    yield
    for eid in list(A.est_ids()):
        A.est_delete(eid)


def _item(name, qty, cost, section='', **extra):
    return dict({'name': name, 'quantity': qty, 'unit': 'SQ', 'section': section,
                 'tiers': {t: {'material_unit_cost': cost, 'labor_unit_cost': 0,
                               'description': ''}
                           for t in ('good', 'better', 'best')}}, **extra)


def _est(eid='inv-1', **over):
    doc = {
        'estimate_id': eid, 'salesperson': 'luke', 'estimate_type': 'retail',
        'customer': {'name': 'Summit Builders', 'email': 'ap@summit.example',
                     'address': {'street': '100 Main St', 'city': 'Loveland',
                                 'state': 'CO', 'zip': '80537'}},
        'pricing': PRICING, 'selected_tier': 'better',
        'trades': {
            'roofing': {'enabled': True, 'mode': 'gbb', 'sections': ['Supplements'],
                        'line_items': [
                            _item('Shingles', 30, 100),
                            _item('Install Labor', 30, 145, customer_visible=False),
                            _item('Skipped', 0, 999),
                            _item('Decking (per sheet)', 0, 65, 'Supplements'),
                        ]},
            'gutters': {'enabled': True, 'mode': 'simple', 'line_items': [
                {'name': 'Seamless 6"', 'quantity': 120, 'unit': 'LF', 'unit_price': 12.5},
            ]},
        },
    }
    doc.update(over)
    return doc


# ── 1-3. what it bills ─────────────────────────────────────────────────────

def test_subtotal_is_the_estimate_total_to_the_cent():
    est = _est()
    rows = A.invoice_rows(est)
    assert rows['subtotal'] == pytest.approx(A._estimate_total(est), abs=0.005)
    assert rows['subtotal'] == pytest.approx(
        30 * 100 / 0.65 + 30 * 145 / 0.65 + 120 * 12.5, abs=0.01)


def test_mix_and_match_tiers_follow_each_trades_pick():
    est = _est()
    est['trades']['roofing']['line_items'][0]['tiers']['best']['material_unit_cost'] = 200
    est['selected_tiers'] = {'roofing': 'best'}
    assert A.invoice_rows(est)['subtotal'] == pytest.approx(A._estimate_total(est), abs=0.005)


def test_insurance_bills_rcv_and_matches_the_claim_total():
    est = _est(estimate_type='insurance', trades={'insurance': {'sections': [
        {'name': 'Roof', 'items': [
            {'name': 'Tear off', 'quantity': 30, 'unit': 'SQ', 'acv': 1200, 'depreciation': 300},
            {'name': 'Drip edge', 'quantity': 200, 'unit': 'LF', 'acv': 500, 'depreciation': 0},
        ]}]}})
    rows = A.invoice_rows(est)
    assert rows['subtotal'] == pytest.approx(A._estimate_total(est)) == 2000
    assert rows['sections'][0]['rows'][0][3] == pytest.approx(50.0)   # 1500 / 30


def test_hidden_lines_are_listed_with_their_price():
    names = [r[0] for s in A.invoice_rows(_est())['sections'] for r in s['rows']]
    assert 'Install Labor' in names


def test_zero_quantity_and_excluded_lines_are_not_billed():
    est = _est()
    est['trades']['roofing']['line_items'][0]['tiers']['better']['included'] = False
    names = [r[0] for s in A.invoice_rows(est)['sections'] for r in s['rows']]
    assert 'Skipped' not in names and 'Shingles' not in names
    assert A.invoice_rows(est)['subtotal'] == pytest.approx(A._estimate_total(est), abs=0.005)


def test_supplements_are_listed_but_not_totalled():
    rows = A.invoice_rows(_est())
    assert [r[0] for r in rows['supplements']] == ['Roofing: Decking (per sheet)']
    names = [r[0] for s in rows['sections'] for r in s['rows']]
    assert 'Decking (per sheet)' not in names


def test_only_accepted_change_orders_bill():
    def co(status, amount):
        return {'id': status, 'title': f'CO {status}', 'status': status, 'pricing': PRICING,
                'line_items': [{'name': 'Extra', 'quantity': 1, 'price_override': amount}]}
    est = _est(change_orders=[co('accepted', 800), co('pending', 5000), co('declined', 9000)])
    rows = A.invoice_rows(est)
    assert rows['co_total'] == 800
    assert rows['total'] == pytest.approx(rows['subtotal'] + 800)
    assert [c['title'] for c in rows['change_orders']] == ['CO accepted']


def test_payments_reduce_the_balance_due():
    est = _est(invoice={'payments': [{'date': '2026-09-01', 'amount': 2500, 'note': 'Deposit'}]})
    rows = A.invoice_rows(est)
    assert rows['payments_total'] == 2500
    assert rows['balance_due'] == pytest.approx(rows['total'] - 2500)


def test_sanitizer_drops_junk():
    out = A._sanitize_invoice({
        'kind': 'receipt', 'issue_date': 'next tuesday', 'due_date': '',
        'payments': [{'amount': 'abc'}, {'amount': 0}, {'amount': '150.555', 'date': 'x'}],
    })
    assert 'kind' not in out and 'issue_date' not in out
    assert out['due_date'] == ''
    assert out['payments'] == [{'date': '', 'amount': 150.56, 'note': ''}]


# ── the number ────────────────────────────────────────────────────────────

def test_default_number_is_stable_and_follows_the_kind(client):
    A.est_save(_est('abcd1234-0000'))
    base = client.get('/api/estimates/abcd1234-0000/invoice').get_json()['invoice']
    assert base['number'] == 'INV-ABCD1234'
    # Switching kind with the default number still in the box flips the prefix.
    d = client.put('/api/estimates/abcd1234-0000/invoice',
                   json={'kind': 'quote', 'number': 'INV-ABCD1234'}).get_json()
    assert d['invoice']['number'] == 'Q-ABCD1234'
    # A number the rep typed survives a switch back.
    client.put('/api/estimates/abcd1234-0000/invoice', json={'number': 'GC-7781'})
    d = client.put('/api/estimates/abcd1234-0000/invoice',
                   json={'kind': 'invoice', 'number': 'GC-7781'}).get_json()
    assert d['invoice']['number'] == 'GC-7781'


# ── 4. sending ─────────────────────────────────────────────────────────────

def _below_floor(monkeypatch, eid):
    monkeypatch.setattr(A, '_margin_floors', lambda: (35.0, 30.0))
    monkeypatch.setattr(A, '_is_manager_up', lambda *a, **k: False)
    est = _est(eid)
    est['pricing'] = {'mode': 'margin', 'rate': 10,
                      'tier_rates': {'good': 10, 'better': 10, 'best': 10}}
    A.est_save(est)


def test_a_quote_below_the_floor_is_blocked(client, monkeypatch):
    _below_floor(monkeypatch, 'inv-quote')
    sent = []
    monkeypatch.setattr(A, '_send_email', lambda *a, **k: sent.append(a) or True)
    client.put('/api/estimates/inv-quote/invoice', json={'kind': 'quote'})
    r = client.post('/api/estimates/inv-quote/invoice/send-email', json={})
    assert r.status_code == 403 and not sent


def test_an_invoice_is_not_gated_by_the_floor(client, monkeypatch):
    _below_floor(monkeypatch, 'inv-bill')
    sent = []
    monkeypatch.setattr(A, '_send_email',
                        lambda subject, html, to, **k: sent.append((subject, to, k)) or True)
    r = client.post('/api/estimates/inv-bill/invoice/send-email', json={'email': 'gc@x.example'})
    assert r.status_code == 200, r.get_json()
    subject, to, kw = sent[0]
    assert to == 'gc@x.example' and subject.startswith('Invoice INV-')
    fname, pdf = kw['attachments'][0]
    assert fname.endswith('.pdf') and pdf[:5] == b'%PDF-'
    doc = A.est_load('inv-bill')
    assert doc['invoice']['sent_to'] == 'gc@x.example'
    assert [a['doc_type'] for a in doc['attachments']] == ['invoice']


def test_a_failed_send_reports_502(client, monkeypatch):
    A.est_save(_est('inv-fail'))
    monkeypatch.setattr(A, '_send_email', lambda *a, **k: False)
    assert client.post('/api/estimates/inv-fail/invoice/send-email', json={}).status_code == 502


def test_the_pdf_renders_with_everything_on_it(client):
    A.est_save(_est('inv-pdf', invoice={'notes': 'Net 30', 'po_ref': 'PO-9',
                                        'payments': [{'amount': 100, 'date': '2026-09-01'}]},
                    change_orders=[{'id': 'c', 'title': 'Add vents', 'status': 'accepted',
                                    'pricing': PRICING, 'line_items': [
                                        {'name': 'Vent', 'quantity': 2, 'price_override': 90}]}]))
    r = client.get('/api/estimates/inv-pdf/invoice.pdf')
    assert r.status_code == 200 and r.data[:5] == b'%PDF-'


def test_filing_replaces_the_previous_copy(client):
    A.est_save(_est('inv-file'))
    client.post('/api/estimates/inv-file/invoice')
    client.post('/api/estimates/inv-file/invoice')
    atts = A.est_load('inv-file')['attachments']
    assert [a['doc_type'] for a in atts] == ['invoice']


# ── 5. ownership ───────────────────────────────────────────────────────────

def test_a_whole_doc_save_cannot_roll_back_payments(client):
    A.est_save(_est('inv-stale'))
    client.put('/api/estimates/inv-stale/invoice', json={'payments': [{'amount': 500}]})
    stale = client.get('/api/estimates/inv-stale').get_json()
    stale['invoice'] = {'payments': []}
    client.put('/api/estimates/inv-stale', json=stale)
    assert A.est_load('inv-stale')['invoice']['payments'][0]['amount'] == 500


def test_a_rep_cannot_reach_another_reps_invoice(app):
    A.est_save(_est('inv-theirs'))            # owned by luke
    c = app.test_client()
    with c.session_transaction() as s:
        s['user'] = OTHER_REP
    assert c.get('/api/estimates/inv-theirs/invoice').status_code == 403
    assert c.get('/api/estimates/inv-theirs/invoice.pdf').status_code == 403
    assert c.post('/api/estimates/inv-theirs/invoice/send-email', json={}).status_code == 403


def test_no_invoice_route_is_open_to_a_demo_guest():
    """Walks the URL map rather than naming the routes.

    The list used to be five hand-written endpoint names, so a route added
    later arrived unchecked — and `send_invoice_for_signature` mails a real
    address and mints a public token, which is exactly the shape of thing a
    guest must not reach. Derive the set, and a new invoice route is covered
    the moment it exists.
    """
    eps = {r.endpoint for r in A.app.url_map.iter_rules()
           if str(r).startswith('/api/estimates/') and '/invoice' in str(r)}
    assert len(eps) >= 6, f'the rule scan stopped matching: {eps}'
    for ep in eps:
        assert ep not in demo_store.ALLOWED_ENDPOINTS, ep
        assert ep not in A.PUBLIC_ENDPOINTS, ep


# ── signing it ─────────────────────────────────────────────────────────────

def _signable(client, eid='inv-sign', **over):
    """An estimate with a minted signing token, ready for the public page."""
    A.est_save(_est(eid, **over))
    est = A.est_load(eid)
    inv = dict(est.get('invoice') or {})
    inv['sign_token'] = 'tok-' + eid
    est['invoice'] = inv
    A.est_save(est)
    return eid, inv['sign_token']


def test_the_public_page_renders_the_figures_and_a_form():
    eid, tok = _signable(None)
    c = A.app.test_client()
    r = c.get(f'/sign-inv/{tok}')
    assert r.status_code == 200
    html = r.data.decode()
    assert 'Sign &amp; Approve' in html
    assert 'Summit Builders' in html
    # The amount on the page is the estimate's own total, not a figure the
    # invoice block stores separately.
    assert A.fc(A.invoice_rows(A.est_load(eid))['subtotal']) in html


def test_a_bad_token_is_a_404_not_a_login_redirect():
    assert A.app.test_client().get('/sign-inv/nope').status_code == 404


def test_signing_stores_the_signature_and_hashes_the_figures():
    eid, tok = _signable(None)
    c = A.app.test_client()
    r = c.post(f'/sign-inv/{tok}', data={'sig_name': 'Dana Ruiz',
                                         'sig_email': 'dana@summit.example'})
    assert r.status_code == 200
    sig = A.est_load(eid)['invoice']['signature']
    assert sig['name'] == 'Dana Ruiz'
    assert len(sig['document_hash']) == 64
    assert sig['token'] == tok


def test_a_nameless_signature_is_refused():
    _eid, tok = _signable(None)
    assert A.app.test_client().post(f'/sign-inv/{tok}',
                                    data={'sig_name': '  '}).status_code == 400


def test_signing_twice_does_not_overwrite_the_first_signature():
    eid, tok = _signable(None)
    c = A.app.test_client()
    c.post(f'/sign-inv/{tok}', data={'sig_name': 'First Signer'})
    c.post(f'/sign-inv/{tok}', data={'sig_name': 'Second Signer'})
    assert A.est_load(eid)['invoice']['signature']['name'] == 'First Signer'


def test_a_signed_invoice_can_no_longer_be_edited(client):
    """The signature covers a hash of these figures. Letting them move
    afterwards leaves a record attesting to an amount the document no longer
    shows — the same reason a signed estimate cannot change status."""
    eid, tok = _signable(None)
    A.app.test_client().post(f'/sign-inv/{tok}', data={'sig_name': 'Dana Ruiz'})
    r = client.put(f'/api/estimates/{eid}/invoice', json={'po_ref': 'changed'})
    assert r.status_code == 409
    assert 'signed' in r.get_json()['error'].lower()
    assert (A.est_load(eid)['invoice'].get('po_ref') or '') != 'changed'


def test_signing_an_invoice_does_not_push_a_job_to_the_den_or_move_the_funnel():
    """THE reason this is not the estimate's signing path.

    `/sign/<token>` runs `_post_sign_pipeline`: a Contact and a Project into
    The Den, packets filed against them, and the CRM funnel driven to `won`.
    An invoice bills work that was already sold, so reusing it would push the
    job to the back office a second time and re-win a lead won months ago —
    silently, because both are background threads.
    """
    import inspect
    src = inspect.getsource(A._post_invoice_sign_pipeline)
    for forbidden in ('_push_to_den', '_funnel_record', '_post_sign_pipeline',
                      'push_to_crm=True'):
        assert forbidden not in src, f'{forbidden} reached the invoice pipeline'


def test_the_signing_token_is_never_settable_by_a_client():
    """A client that could name the token could sign on the customer's behalf."""
    for field in ('sign_token', 'signature', 'sign_sent_at'):
        assert field not in A._sanitize_invoice({field: 'x'})


def test_a_whole_doc_save_cannot_strip_a_signature(client):
    eid, tok = _signable(None)
    A.app.test_client().post(f'/sign-inv/{tok}', data={'sig_name': 'Dana Ruiz'})
    client.put(f'/api/estimates/{eid}', json=_est(eid))
    assert A.est_load(eid)['invoice'].get('signature'), 'the signature was rolled back'


# ── homeowner mode: "List every line" unticked ─────────────────────────────

def test_unticking_list_every_line_folds_hidden_rows_but_not_the_money():
    itemized = A.invoice_rows(_est())
    folded = A.invoice_rows(_est(invoice={'itemize': False}))
    names = [r[0] for s in folded['sections'] for r in s['rows']]
    assert 'Install Labor' not in names and 'Shingles' in names
    roof = next(s for s in folded['sections'] if s['title'] == 'Roofing')
    assert roof['folded'] == 1
    assert folded['subtotal'] == itemized['subtotal'] == pytest.approx(
        A._estimate_total(_est()), abs=0.005)


def test_a_trade_whose_every_line_is_hidden_still_bills():
    est = _est(invoice={'itemize': False})
    for it in est['trades']['gutters']['line_items']:
        it['customer_visible'] = False
    rows = A.invoice_rows(est)
    gut = next(s for s in rows['sections'] if s['title'] == 'Gutters')
    assert gut['rows'] == [] and gut['subtotal'] == 1500
    assert rows['subtotal'] == pytest.approx(A._estimate_total(est), abs=0.005)


def test_itemize_defaults_on_and_only_takes_a_real_boolean():
    assert A.invoice_fields(_est())['itemize'] is True
    assert 'itemize' not in A._sanitize_invoice({'itemize': 'no'})
    assert A._sanitize_invoice({'itemize': False}) == {'itemize': False}


def test_the_folded_pdf_renders(client):
    A.est_save(_est('inv-folded', invoice={'itemize': False}))
    r = client.get('/api/estimates/inv-folded/invoice.pdf')
    assert r.status_code == 200 and r.data[:5] == b'%PDF-'


# ── the ways in ────────────────────────────────────────────────────────────

def test_invoice_is_reachable_from_the_header_and_the_more_menu():
    import os
    html = open(os.path.join(os.path.dirname(A.__file__), 'static', 'index.html'),
                encoding='utf-8').read()
    css = open(os.path.join(os.path.dirname(A.__file__), 'static', 'style.css'),
               encoding='utf-8').read()
    assert 'class="btn-invoice"' in html and 'onclick="openInvoice()"' in html
    assert 'openInvoice();closeMoreMenu()' in html
    # The laptop breakpoint hides every header button not named here, so the
    # invoice button has to be on the keep list or it vanishes below 1600px.
    assert ':not(.btn-invoice)' in css


def test_the_pdf_can_be_saved_and_not_only_previewed():
    """`?download=1` sets Content-Disposition: attachment, and had done since
    the endpoint was written — with nothing in the UI ever passing it.

    Preview opens the PDF inline. On a laptop the browser's own save button
    covers the gap; on the phone a rep is holding in a driveway it is a dead
    end, and handing a GC a document is most of what this panel is for.
    """
    import os
    js = open(os.path.join(os.path.dirname(A.__file__), 'static', 'app.js'),
              encoding='utf-8').read()
    assert 'onclick="invDownload()"' in js, 'no button calls it'
    assert 'function invDownload' in js
    body = js[js.index('async function invDownload'):]
    body = body[:body.index('async function invFile')]
    assert 'download=1' in body, (
        'invDownload opens the same inline preview — the flag is what makes it '
        'a save')


def test_the_download_flag_actually_changes_the_disposition(client):
    """The button is only worth having if the flag does something."""
    A.est_save(_est('inv-dl'))
    inline = client.get('/api/estimates/inv-dl/invoice.pdf')
    attach = client.get('/api/estimates/inv-dl/invoice.pdf?download=1')
    assert inline.status_code == attach.status_code == 200
    assert inline.data[:5] == attach.data[:5] == b'%PDF-'
    assert 'attachment' not in (inline.headers.get('Content-Disposition') or '')
    assert 'attachment' in attach.headers.get('Content-Disposition', '')
    assert '.pdf' in attach.headers['Content-Disposition']


def test_the_rep_is_told_when_it_is_signed(monkeypatch):
    """The signature lands in a background thread on a public route, so
    without this the rep finds out by opening the estimate and noticing.

    This caught a real one: the notification was referenced by the pipeline and
    never defined, and because the call sits in a try/except the only sign was
    a line in a log nobody reads.
    """
    sent = []
    monkeypatch.setattr(A, '_send_email',
                        lambda subj, html, to, **kw: sent.append((subj, html, to)) or True)
    eid, tok = _signable(None)
    A.app.test_client().post(f'/sign-inv/{tok}', data={'sig_name': 'Dana Ruiz'})
    assert A.send_invoice_signature_notification(A.est_load(eid)) is True
    subj, html, _to = sent[-1]
    assert 'signed' in subj.lower()
    assert 'Dana Ruiz' in html
    assert 'nothing in the pipeline has' in html, (
        'the rep should be told explicitly that this did NOT move the job')


def test_the_pipeline_calls_a_notification_that_actually_exists():
    """A name resolved at call time, inside a try/except, is a silent no-op."""
    import inspect
    src = inspect.getsource(A._post_invoice_sign_pipeline)
    for name in ('send_invoice_signature_notification', 'generate_invoice'):
        assert name in src
        assert hasattr(A, name), f'{name} is called by the pipeline and does not exist'


def test_the_panel_offers_the_signing_link_and_shows_when_it_is_signed():
    import os
    js = open(os.path.join(os.path.dirname(A.__file__), 'static', 'app.js'),
              encoding='utf-8').read()
    assert 'onclick="invSendForSignature()"' in js
    assert 'function invSendForSignature' in js
    body = js[js.index('async function invSendForSignature'):]
    body = body[:body.index('async function invFile')]
    assert 'invoice/send-signature' in body
    # A link that exists but was not emailed is still worth having — a rep
    # pastes it into the thread the GC is already on.
    assert 'clipboard' in body, 'no fallback when the mail does not go'
    assert 'rc-signed-chip' in js, 'nothing shows that it came back signed'
