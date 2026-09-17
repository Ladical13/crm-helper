"""Optional upgrades — the extras a homeowner elects for themselves at signing.

The shape is a tick list on the /sign page: the rep offers priced extras, the
customer chooses, and what they chose joins the contract. Everything worth
pinning here is about the seam between "offered" and "bought", because that is
where money can move without anyone deciding it should:

  * an OFFERED upgrade must be worth exactly nothing — in the total, in the
    margin floor, in the funnel value the CRM drains;
  * an ELECTED one must be worth its price everywhere at once, or the signed
    contract, the invoice and the leaderboard disagree about one job;
  * the PRICE the customer was shown is the price they get, so a signature
    against a stale price is refused rather than silently repriced;
  * the ELECTION is the customer's, so nothing a rep's tab saves may undo it;
  * an elected upgrade with NO COST reports an unknown margin, never a
    perfect one.
"""
import json
import threading

import pytest

import app as A


def _await_post_sign(timeout=60):
    """Wait for the pipeline the sign route spawned. It writes the estimate
    (signed PDF, packet) in the background, so a test that reads or saves the
    doc while it is still running is racing it — which is exactly how this
    file's save test failed only when the whole file ran."""
    for t in threading.enumerate():
        if t.name == A.POST_SIGN_THREAD:
            t.join(timeout)
            assert not t.is_alive(), 'the post-sign pipeline never finished'


@pytest.fixture(autouse=True)
def clean_slate():
    for eid in list(A.est_ids()):
        A.est_delete(eid)
    yield
    _await_post_sign()
    for eid in list(A.est_ids()):
        A.est_delete(eid)


PKG = 12000.0


def _upg(uid, name, price, cost='', accepted=None):
    u = {'id': uid, 'name': name, 'price': price, 'cost': cost}
    if accepted is not None:
        u['accepted'] = accepted
    return u


def _seed(eid='e1', *, items=None, enabled=True, est_type='retail', signed=False):
    doc = {
        'estimate_id': eid,
        'salesperson': 'luke',
        'estimate_type': est_type,
        'share_token': 'tok-' + eid,
        'customer': {'name': 'Jon Smith', 'address': {'city': 'Loveland', 'state': 'CO'}},
        'pricing': {'mode': 'margin', 'global_rate': 35},
        'trades': {'roofing': {
            'enabled': True, 'mode': 'simple',
            'line_items': [{'name': 'Roof', 'quantity': 1,
                            'unit_price': PKG, 'unit_cost': 7000.0}],
        }},
        'upgrades': {'enabled': enabled, 'items': list(items or [])},
    }
    if est_type == 'insurance':
        doc['trades'] = {'insurance': {'enabled': True, 'line_items': [
            {'name': 'Remove shingles', 'acv': 6000.0, 'depreciation': 1500.0},
        ]}}
    if signed:
        doc['signature'] = {'name': 'Jon Smith', 'signed_at': '2026-09-16T00:00:00Z'}
        doc['status'] = 'accepted'
    A.est_save(doc)
    return doc


# ── Offered is not bought ──────────────────────────────────────────────────

def test_an_offered_upgrade_is_worth_nothing():
    """The whole point of the feature. If offering one moved the total, an
    upgrade would be a price rise the customer never agreed to."""
    est = _seed(items=[_upg('u_a', 'Gutter guards', 1450, 820),
                       _upg('u_b', 'Skylight', 2200, 1400)])
    assert A.upgrades_total(est) == 0
    assert A.calc_selected_total(est) == pytest.approx(PKG)
    assert A._estimate_total(est) == pytest.approx(PKG)
    # …and they ARE on the table, so the customer can see them.
    assert [u['id'] for u in A.upgrades_offered(est)] == ['u_a', 'u_b']


def test_an_elected_upgrade_is_worth_its_price_everywhere():
    est = _seed(items=[_upg('u_a', 'Gutter guards', 1450, 820, accepted=True),
                       _upg('u_b', 'Skylight', 2200, 1400)])
    assert A.upgrades_total(est) == pytest.approx(1450)
    assert A.calc_selected_total(est) == pytest.approx(PKG + 1450)
    assert A._estimate_total(est) == pytest.approx(PKG + 1450)
    assert A.invoice_rows(est)['total'] == pytest.approx(PKG + 1450)


def test_an_unpriced_upgrade_is_not_an_offer():
    """A tickable $0.00 row is not an offer, it is a bug the customer sees."""
    est = _seed(items=[_upg('u_a', 'Ridge vent', 0, 0, accepted=True),
                       _upg('u_b', '', 900, 500, accepted=True)])
    assert A.upgrades_offered(est) == []
    assert A.upgrades_total(est) == 0
    assert A.calc_selected_total(est) == pytest.approx(PKG)


def test_switching_the_block_off_withdraws_the_election_too():
    """Otherwise a rep un-offering upgrades leaves a total nothing explains."""
    est = _seed(enabled=False,
                items=[_upg('u_a', 'Gutter guards', 1450, 820, accepted=True)])
    assert A.upgrades_offered(est) == []
    assert A.upgrades_total(est) == 0
    assert A.calc_selected_total(est) == pytest.approx(PKG)


# ── Cost, and the margin it is allowed to report ──────────────────────────

def test_a_zero_cost_upgrade_is_not_costed():
    """The rate chain honours an explicit 0 — selling at cost is a real
    decision. A cost box reading 0 on an upgrade has simply never been filled
    in, and calling that free reports a 100% margin nobody earned."""
    assert A.upgrade_cost({'cost': 0}) is None
    assert A.upgrade_cost({'cost': ''}) is None
    assert A.upgrade_cost({'cost': None}) is None
    assert A.upgrade_cost({'cost': 'junk'}) is None
    assert A.upgrade_cost({'cost': 820}) == 820


def test_an_uncosted_election_is_named_not_averaged_in():
    """It would add its whole price to sell and nothing to cost, raising the
    reported margin in exactly the flattering direction."""
    est = _seed(items=[_upg('u_a', 'Gutter guards', 1450, accepted=True)])
    cost, uncosted = A.upgrades_cost_total(est)
    assert cost == 0
    assert [u['id'] for u in uncosted] == ['u_a']

    rep = A.estimate_margin_report(est)
    assert rep['upgrades_uncosted'] == ['Gutter guards']
    assert rep['upgrades_sell'] == 0, 'an uncosted upgrade must sit out of the margin'
    assert rep['upgrades_cost'] == 0
    # The package's own margin is untouched by it.
    bare = A.estimate_margin_report(_seed('e2'))
    assert rep['tiers'][0]['margin_pct'] == bare['tiers'][0]['margin_pct']


def test_a_costed_election_joins_the_margin():
    est = _seed(items=[_upg('u_a', 'Gutter guards', 1450, 820, accepted=True)])
    rep = A.estimate_margin_report(est)
    assert rep['upgrades_uncosted'] == []
    assert rep['upgrades_sell'] == pytest.approx(1450)
    assert rep['upgrades_cost'] == pytest.approx(820)
    assert rep['tiers'][0]['sell'] == pytest.approx(PKG + 1450)
    assert rep['tiers'][0]['cost'] == pytest.approx(7000 + 820)


# ── The customer page ─────────────────────────────────────────────────────

def test_the_sign_page_offers_the_tick_list_with_the_price_it_shows(anon):
    _seed(items=[_upg('u_a', 'Gutter guards', 1450, 820)])
    html = anon.get('/sign/tok-e1').get_data(as_text=True)
    assert 'Optional Upgrades' in html
    assert 'name="upgrade_u_a"' in html
    # The price echo is what lets the POST refuse a signature against a price
    # the rep has since changed.
    assert 'name="upgrade_price_u_a"' in html
    assert 'value="1450.00"' in html


def test_no_upgrades_means_no_block_and_no_script(anon):
    _seed()
    html = anon.get('/sign/tok-e1').get_data(as_text=True)
    # The stylesheet is one shared inline string, so .cv-upg rules are always
    # there — what must be absent is the block itself and its script.
    assert 'id="cv-upg"' not in html
    assert '_cvUpgChange' not in html


def test_an_unpriced_upgrade_never_reaches_the_customer(anon):
    _seed(items=[_upg('u_a', 'Ridge vent', 0)])
    html = anon.get('/sign/tok-e1').get_data(as_text=True)
    assert 'name="upgrade_u_a"' not in html


# ── Signing ───────────────────────────────────────────────────────────────

def _sign(anon, **extra):
    form = {'sig_name': 'Jon Smith', 'sig_email': 'jon@example.com', 'agree': 'on'}
    form.update(extra)
    r = anon.post('/sign/tok-e1', data=form)
    if r.status_code == 200:
        _await_post_sign()
    return r


def test_signing_records_what_was_ticked_and_nothing_else(anon):
    _seed(items=[_upg('u_a', 'Gutter guards', 1450, 820),
                 _upg('u_b', 'Skylight', 2200, 1400)])
    r = _sign(anon, upgrade_u_a='1', upgrade_price_u_a='1450.00',
              upgrade_price_u_b='2200.00')
    assert r.status_code == 200

    est = A.est_load('e1')
    items = {u['id']: u for u in est['upgrades']['items']}
    assert items['u_a']['accepted'] is True
    assert items['u_b']['accepted'] is False
    sig = est['signature']
    assert [e['id'] for e in sig['upgrades']] == ['u_a']
    assert sig['upgrades_total'] == pytest.approx(1450)
    assert A._estimate_total(est) == pytest.approx(PKG + 1450)


def test_a_price_that_moved_refuses_the_signature(anon):
    """A signature is a price agreement. Ticking $1,450 of gutter guards must
    never become an $1,850 contract because the rep edited the row."""
    _seed(items=[_upg('u_a', 'Gutter guards', 1850, 820)])
    r = _sign(anon, upgrade_u_a='1', upgrade_price_u_a='1450.00')
    assert r.status_code == 409
    assert 'refresh' in r.get_data(as_text=True).lower()
    assert A.est_load('e1').get('signature') is None


def test_a_tick_with_no_price_echo_is_refused(anon):
    """Fail closed: a form that ticked an upgrade without echoing back a price
    is not a page this app served, and there is nothing to agree against."""
    _seed(items=[_upg('u_a', 'Gutter guards', 1450, 820)])
    assert _sign(anon, upgrade_u_a='1').status_code == 409


def test_ticking_a_withdrawn_upgrade_is_refused_not_dropped(anon):
    """The customer believed they were buying it. Silently signing them up for
    the job without it is the one outcome nobody would notice."""
    _seed(items=[_upg('u_a', 'Gutter guards', 1450, 820)], enabled=False)
    r = _sign(anon, upgrade_u_a='1', upgrade_price_u_a='1450.00')
    assert r.status_code == 409
    assert A.est_load('e1').get('signature') is None


def test_the_election_is_inside_the_signed_document_hash(anon):
    """The hash is what the certificate promises. An upgrade added to the
    contract after the fact has to break it."""
    _seed(items=[_upg('u_a', 'Gutter guards', 1450, 820)])
    _sign(anon, upgrade_u_a='1', upgrade_price_u_a='1450.00')
    est = A.est_load('e1')
    stored = est['signature']['document_hash']

    doc = json.loads(json.dumps(est))
    doc.pop('signature')
    for u in doc['upgrades']['items']:
        u['accepted'] = False
    rehash = A.hashlib.sha256(json.dumps(
        doc, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    assert rehash != stored


def test_the_signed_page_shows_what_they_elected_and_a_contract_total(anon):
    _seed(items=[_upg('u_a', 'Gutter guards', 1450, 820)])
    _sign(anon, upgrade_u_a='1', upgrade_price_u_a='1450.00')
    html = anon.get('/sign/tok-e1').get_data(as_text=True)
    assert 'Optional Upgrades You Selected' in html
    assert 'Contract Total' in html
    assert A.fc(PKG + 1450) in html


# ── The election belongs to the customer ──────────────────────────────────

def test_a_reps_save_cannot_undo_the_election(client, anon):
    """A whole-doc save is a snapshot from whenever that tab loaded. A rep who
    had the estimate open before the signature landed would otherwise autosave
    the election straight back off the contract, and the total with it."""
    _seed(items=[_upg('u_a', 'Gutter guards', 1450, 820)])
    _sign(anon, upgrade_u_a='1', upgrade_price_u_a='1450.00')

    stale = A.est_load('e1')
    stale['upgrades']['items'][0].pop('accepted', None)   # what an old tab holds
    stale.pop('signature', None)
    client.put('/api/estimates/e1', json=stale)

    est = A.est_load('e1')
    assert est['upgrades']['items'][0]['accepted'] is True
    assert A._estimate_total(est) == pytest.approx(PKG + 1450)


def test_a_rep_may_still_add_and_edit_rows(client, anon):
    _seed(items=[_upg('u_a', 'Gutter guards', 1450, 820)])
    doc = A.est_load('e1')
    doc['upgrades']['items'].append(_upg('u_b', 'Skylight', 2200, 1400))
    doc['upgrades']['items'][0]['price'] = 1550
    client.put('/api/estimates/e1', json=doc)

    est = A.est_load('e1')
    assert [u['id'] for u in est['upgrades']['items']] == ['u_a', 'u_b']
    assert A.upgrade_price(est['upgrades']['items'][0]) == 1550


def test_a_row_saved_without_an_id_gets_one(client):
    """The id is the /sign form's field name, so a row without one is a priced
    offer the customer cannot select — and it fails silently."""
    _seed()
    doc = A.est_load('e1')
    doc['upgrades']['items'] = [{'name': 'Gutter guards', 'price': 1450}]
    client.put('/api/estimates/e1', json=doc)
    uid = A.est_load('e1')['upgrades']['items'][0]['id']
    assert uid.startswith('u_') and len(uid) > 2


# ── Insurance ─────────────────────────────────────────────────────────────

def test_the_claim_total_is_never_inflated_by_an_upgrade():
    """The claim is the carrier's number and a customer may repeat it to their
    adjuster. A non-covered upgrade is the homeowner's own out-of-pocket."""
    est = _seed(est_type='insurance',
                items=[_upg('u_a', 'Gutter guards', 1450, 820, accepted=True)])
    assert A._insurance_rcv_total(est) == pytest.approx(7500)
    assert A.insurance_cost_report(est)['claim_total'] == pytest.approx(7500)
    # …while the contract is worth the claim plus what they elected.
    assert A._estimate_total(est) == pytest.approx(7500 + 1450)


def test_an_insurance_upgrade_is_revenue_with_a_cost():
    est = _seed(est_type='insurance',
                items=[_upg('u_a', 'Gutter guards', 1450, 820, accepted=True)])
    rep = A.insurance_cost_report(est)
    assert rep['upgrades'] == pytest.approx(1450)
    assert rep['upgrades_cost'] == pytest.approx(820)


def test_an_uncosted_insurance_upgrade_lands_on_the_unpriced_list():
    est = _seed(est_type='insurance',
                items=[_upg('u_a', 'Gutter guards', 1450, accepted=True)])
    assert 'Gutter guards' in A.insurance_cost_report(est)['unpriced']


# ── Documents ─────────────────────────────────────────────────────────────

def test_the_invoice_bills_the_election_as_its_own_section():
    est = _seed(items=[_upg('u_a', 'Gutter guards', 1450, 820, accepted=True),
                       _upg('u_b', 'Skylight', 2200, 1400)])
    rows = A.invoice_rows(est)
    upg = [s for s in rows['sections'] if s['title'] == 'Optional Upgrades']
    assert len(upg) == 1
    assert [r[0] for r in upg[0]['rows']] == ['Gutter guards']
    assert upg[0]['subtotal'] == pytest.approx(1450)
    # Inside the subtotal, not bolted on beside it like a change order — this
    # was one agreement, signed once.
    assert rows['co_total'] == 0
    assert rows['subtotal'] == pytest.approx(PKG + 1450)


def test_the_packet_cost_rows_still_add_up_to_its_own_total():
    """The packet's TOTAL line prints _estimate_total as Contract Value, and a
    jurisdiction may fee on the Cost Total beside it. Leaving the elected
    upgrades out left the rows failing to sum to the table's own footer."""
    est = _seed(items=[_upg('u_a', 'Gutter guards', 1450, 820, accepted=True)])
    rows = A._packet_cost_rows(est)
    assert sum(r['sell'] for r in rows) == pytest.approx(A._estimate_total(est))
    upg = [r for r in rows if r['trade'] == 'upgrades']
    assert len(upg) == 1
    # Cost files as material, for the same reason job extras do: the packet
    # prints Cost Total = materials + labor.
    assert upg[0]['materials_cost'] == pytest.approx(820)
    assert upg[0]['labor_cost'] == 0


def test_the_packet_says_when_an_elected_upgrade_has_no_cost():
    est = _seed(items=[_upg('u_a', 'Gutter guards', 1450, accepted=True)])
    note = A._packet_upgrade_note(est)
    assert 'Gutter guards' in note and 'no cost' in note
    assert A._packet_upgrade_note(_seed('e2')) == ''


def test_the_cost_split_stays_about_trades():
    """_cost_split_by_trade's every row is walked back into est['trades'] by
    test_cost_split.py. The upgrades row belongs to the packet, not to it."""
    est = _seed(items=[_upg('u_a', 'Gutter guards', 1450, 820, accepted=True)])
    assert all(r['trade'] in A.GBB_TRADES for r in A._cost_split_by_trade(est))
