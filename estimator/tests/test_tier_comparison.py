"""The Good/Better/Best comparison block in the customer's PDF.

The whole point of the block is that a homeowner can print one sheet and hold
it next to another contractor's bid. That makes two things load-bearing: every
offered package has to be on it with a real price, and it must NOT appear on a
signed contract, where the choice has already been made.

The prices here are not recomputed by these tests on purpose — _trade_subtotal
is already held to app.js by test_parity.py. What is tested is that the page
shows the tiers it should and hides the ones it should not.
"""
import io

import pytest

pytest.importorskip('fpdf')
PdfReader = pytest.importorskip('pypdf').PdfReader


def _li(name, qty, unit, g, b, bs):
    return {'name': name, 'quantity': qty, 'unit': unit,
            'tiers': {'good': {'material_unit_cost': g},
                      'better': {'material_unit_cost': b},
                      'best': {'material_unit_cost': bs}}}


def _two_trade_estimate():
    """Roofing + siding, both G/B/B, with curated tier bullets."""
    return {
        'estimate_id': 'cmp-test-0001',
        'estimate_date': '2026-09-01',
        'valid_until': '2026-10-01',
        'salesperson': 'luke',
        'customer': {'name': 'Jane Doe',
                     'address': {'street': '1 Test St', 'city': 'Loveland',
                                 'state': 'CO', 'zip': '80537'}},
        'contract_text': 'TERMS AND CONDITIONS — sample body',
        'trades': {
            'roofing': {
                'enabled': True, 'mode': 'gbb',
                'tier_descriptions': {'good': 'Code-compliant at the best price.',
                                      'better': 'The long-term value pick.',
                                      'best': 'Maximum impact resistance.'},
                'tier_features': {'good': ['3-Tab shingles, 25-year warranty'],
                                  'better': ['Architectural shingles, lifetime warranty'],
                                  'best': ['Class 4 impact-rated designer shingles']},
                'line_items': [_li('Shingles', 30, 'SQ', 142, 175, 400)],
            },
            'siding': {
                'enabled': True, 'mode': 'gbb',
                'tier_descriptions': {'good': 'Durable vinyl.',
                                      'better': 'Insulated vinyl.',
                                      'best': 'Fiber cement.'},
                'tier_features': {'good': ['Standard vinyl siding'],
                                  'better': ['Insulated vinyl siding'],
                                  'best': ['James Hardie fiber cement']},
                'line_items': [_li('Siding', 22, 'SQ', 210, 320, 640)],
            },
        },
        'measurements': {'attic_sqft': 1800, 'turtle_vents': 0},
    }


def _signed(est):
    est = dict(est)
    est['signature'] = {
        'name': 'Jane Doe', 'email': 'jane@example.com',
        'signed_at': '2026-09-01T15:04:05Z', 'ip_address': '127.0.0.1',
        'document_hash': 'a' * 64, 'selected_tier': 'better',
        'selected_tiers': {'roofing': 'better', 'siding': 'better'},
        'shingle_color': 'Charcoal', 'siding_color': '', 'initials': [],
    }
    return est


def _text(pdf_bytes):
    return '\n'.join(p.extract_text() or ''
                     for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


def _money(v):
    return f'{v:,.2f}'


def test_unsigned_pdf_compares_every_offered_package(A):
    """One block per G/B/B trade, each carrying all three real subtotals."""
    est = _two_trade_estimate()
    text = _text(A.build_signed_pdf(est, signed=False))

    assert text.count('Compare Packages') == 2, 'one block per G/B/B trade'
    for trade in ('Roofing', 'Siding'):
        assert f'{trade} — Compare Packages' in text
    for trade in ('roofing', 'siding'):
        for tier in ('good', 'better', 'best'):
            sub = A._trade_subtotal(est, trade, tier)
            assert _money(sub) in text, f'{trade} {tier} subtotal missing'


def test_comparison_totals_the_whole_job_per_package(A):
    """With two trades on the bid, the per-trade columns are what a customer
    adds up wrong — the all-trades strip is the number they actually compare."""
    est = _two_trade_estimate()
    text = _text(A.build_signed_pdf(est, signed=False))
    assert 'COMPLETE PROJECT' in text
    for tier in ('good', 'better', 'best'):
        assert _money(A.calc_tier_total(est, tier)) in text


def test_single_trade_estimate_skips_the_all_trades_strip(A):
    """One trade means the trade block already IS the whole job — repeating it
    underneath reads as a second, different number."""
    est = _two_trade_estimate()
    del est['trades']['siding']
    text = _text(A.build_signed_pdf(est, signed=False))
    assert 'Compare Packages' in text
    assert 'COMPLETE PROJECT' not in text


def test_signed_contract_has_no_comparison(A):
    """A signature is a decision. Showing the two packages they turned down
    invites renegotiating a document that is already executed."""
    text = _text(A.build_signed_pdf(_signed(_two_trade_estimate()), signed=True))
    assert 'Compare Packages' not in text
    assert 'COMPLETE PROJECT' not in text
    assert 'SIGNED CONTRACT' in text


def test_comparison_offers_only_the_enabled_tiers(A):
    """tiers_enabled is how a rep takes a package off the table. The PDF must
    not put back a price the /sign page refuses to show."""
    est = _two_trade_estimate()
    est['tiers_enabled'] = {'good': False}
    text = _text(A.build_signed_pdf(est, signed=False))

    assert 'Compare Packages' in text
    assert _money(A._trade_subtotal(est, 'roofing', 'better')) in text
    assert _money(A._trade_subtotal(est, 'roofing', 'good')) not in text


def test_one_enabled_tier_drops_the_block_entirely(A):
    """Nothing to compare — a lone 'comparison' column is just noise above the
    line items that already say the same thing."""
    est = _two_trade_estimate()
    est['tiers_enabled'] = {'good': False, 'best': False}
    assert 'Compare Packages' not in _text(A.build_signed_pdf(est, signed=False))


def test_insurance_estimates_have_no_packages_to_compare(A):
    """Insurance is priced off the carrier's scope, not off G/B/B."""
    est = {
        'estimate_id': 'cmp-ins-0001', 'estimate_type': 'insurance',
        'estimate_date': '2026-09-01', 'salesperson': 'luke',
        'customer': {'name': 'Jane Doe',
                     'address': {'street': '1 Test St', 'city': 'Loveland',
                                 'state': 'CO', 'zip': '80537'}},
        'contract_text': 'TERMS AND CONDITIONS — sample body',
        'trades': {'insurance': {'enabled': True, 'carrier': 'State Farm',
                                 'claim_number': 'CLM-1',
                                 'line_items': [{'name': 'Roof replacement',
                                                 'acv': 900, 'depreciation': 100}]}},
    }
    assert 'Compare Packages' not in _text(A.build_signed_pdf(est, signed=False))


def test_simple_mode_trades_are_not_offered_as_packages(A):
    """A fixed-price trade has one price, not three. It keeps its normal line
    items and must not grow three identical columns."""
    est = _two_trade_estimate()
    est['trades']['siding']['mode'] = 'simple'
    est['trades']['siding']['line_items'] = [
        {'name': 'Siding', 'quantity': 22, 'unit': 'SQ', 'unit_price': 400}]
    text = _text(A.build_signed_pdf(est, signed=False))
    assert text.count('Compare Packages') == 1
    assert 'Roofing — Compare Packages' in text
    assert 'Siding — Compare Packages' not in text


def test_a_commercial_bid_is_offered_as_three_packages(A):
    """Commercial sells coating / overlay / full replacement now, so it earns
    the comparison block like any other G/B/B trade — and a building owner
    comparing three flat-roof approaches on one sheet is exactly who it is for.

    It reaches the block through _trade_mode, so this is also the customer-side
    proof that the default actually flipped."""
    est = _two_trade_estimate()
    est['trades'] = {'commercial': {
        'enabled': True,          # no explicit mode: the DEFAULT must carry it
        'tier_descriptions': {'good': 'Restore the roof you have.',
                              'better': 'Cover it without a tear-off.',
                              'best': 'Full tear-off and replacement.'},
        'tier_features': {'good': ['Silicone restoration coating'],
                          'better': ['1/4" cover board and new 60-mil TPO'],
                          'best': ['Tear-off to the deck, new polyiso and TPO']},
        'line_items': [_li('Membrane', 44, 'SQ', 60, 100, 130)],
    }}
    est['estimate_type'] = 'commercial'
    text = _text(A.build_signed_pdf(est, signed=False))
    assert 'Commercial — Compare Packages' in text
    assert 'Restore the roof you have.' in text
    assert 'Full tear-off and replacement.' in text

    # ...and a rep who chose to sell one system gets one price, not three.
    est['trades']['commercial']['mode'] = 'simple'
    est['trades']['commercial']['line_items'] = [
        {'name': 'TPO system', 'quantity': 44, 'unit': 'SQ', 'unit_price': 140.85}]
    assert 'Compare Packages' not in _text(A.build_signed_pdf(est, signed=False))


def test_bullets_are_capped_with_an_overflow_line(A):
    """A column that runs past the cap says so, rather than silently implying
    the package stops there."""
    est = _two_trade_estimate()
    est['trades']['roofing']['tier_features']['better'] = [
        f'Included item number {i}' for i in range(1, 12)]
    text = _text(A.build_signed_pdf(est, signed=False))
    assert 'Included item number 7' in text
    assert 'Included item number 8' not in text
    assert f'+ {11 - A._CMP_MAX_BULLETS} more included' in text


def test_comparison_matches_the_customer_pages_own_numbers(A, client):
    """The printed comparison and the /sign page are two renderings of one
    set of helpers. If they ever disagree, the customer caught us, not a test."""
    est = _two_trade_estimate()
    est['share_token'] = 'cmp-parity-token'
    A.est_save(est)

    text = _text(A.build_signed_pdf(est, signed=False))
    page = client.get('/sign/cmp-parity-token').get_data(as_text=True)

    for trade in ('roofing', 'siding'):
        for tier in ('good', 'better', 'best'):
            money = _money(A._trade_subtotal(est, trade, tier))
            assert money in text, f'{trade} {tier} missing from the PDF'
            assert money in page, f'{trade} {tier} missing from the sign page'


# ── The emailed copy ───────────────────────────────────────────────────────
# Sending used to be link-only: the customer's inbox held a URL and the PDF sat
# two clicks away behind it, which is why reps heard "it downloads as a link".

def test_send_email_attaches_the_estimate_pdf(A, client, monkeypatch):
    est = _two_trade_estimate()
    est['estimate_id'] = 'cmp-email-0001'
    est['customer']['email'] = 'jane@example.com'
    A.est_save(est)

    sent = {}

    def _fake_send(subject, html_body, to_addr, cc=None, attachments=None, bcc=None):
        sent.update(subject=subject, body=html_body, to=to_addr,
                    attachments=attachments)
        return True

    monkeypatch.setattr(A, '_send_email', _fake_send)
    monkeypatch.setattr(A, 'get_public_url', lambda: 'https://example.test')

    r = client.post('/api/estimates/cmp-email-0001/send-email', json={})
    assert r.status_code == 200
    assert r.get_json()['pdf_attached'] is True

    atts = sent['attachments']
    assert atts and len(atts) == 1
    fname, data = atts[0]
    assert fname.endswith('.pdf') and 'Estimate' in fname
    assert data[:4] == b'%PDF'
    # It is the unsigned variant, so it carries the comparison the link does
    assert 'Compare Packages' in _text(data)
    # ...and the link is still the call to action, because signing needs it
    assert 'https://example.test/sign/' in sent['body']


def test_send_email_still_goes_out_when_the_pdf_cannot_be_built(A, client, monkeypatch):
    """A broken render must cost the customer the attachment, never the email."""
    est = _two_trade_estimate()
    est['estimate_id'] = 'cmp-email-0002'
    est['customer']['email'] = 'jane@example.com'
    A.est_save(est)

    sent = {}

    def _fake_send(subject, html_body, to_addr, cc=None, attachments=None, bcc=None):
        sent.update(attachments=attachments)
        return True

    def _boom(est, signed=None):
        raise RuntimeError('font blew up')

    monkeypatch.setattr(A, '_send_email', _fake_send)
    monkeypatch.setattr(A, 'build_signed_pdf', _boom)
    monkeypatch.setattr(A, 'get_public_url', lambda: 'https://example.test')

    r = client.post('/api/estimates/cmp-email-0002/send-email', json={})
    assert r.status_code == 200
    assert r.get_json()['pdf_attached'] is False
    assert sent['attachments'] is None
