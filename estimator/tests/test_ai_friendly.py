"""AI-friendly customer document — the /sign page emits machine-readable
metadata (schema.org JSON-LD, meta description, OpenGraph) and a human-readable
'About This Estimate' block; the signed PDF gets the same summary as a
dedicated page BEFORE the T&C, plus PDF document metadata.

These are the invariants that make the estimate legible to Claude/ChatGPT
when the customer uploads it. If any of these regress, the AI reader falls
back to just the line items and boilerplate T&C — indistinguishable from a
generic competitor's estimate."""
import json
import re


def _retail_estimate():
    return {
        'estimate_id':   'retail-abc-xxxxxxxx',
        'estimate_date': '2026-08-01',
        'valid_until':   '2026-09-01',
        'salesperson':   'luke',
        'customer': {
            'name':    'Jane Doe',
            'address': {'street': '1 Test St', 'city': 'Loveland',
                        'state': 'CO', 'zip': '80537'},
        },
        'contract_text': 'TERMS AND CONDITIONS — sample body',
        'trades': {
            'roofing': {
                'enabled': True, 'mode': 'gbb',
                'tier_bundles': {'good': 'b_landmark', 'better': 'b_northgate',
                                 'best': 'b_standing_seam'},
                'line_items': [
                    {'name': 'Shingles', 'quantity': 30, 'unit': 'SQ',
                     'measure': 'squares_waste',
                     'tiers': {'good':   {'material_unit_cost': 142},
                               'better': {'material_unit_cost': 175},
                               'best':   {'material_unit_cost': 400}}},
                ],
            },
        },
        'measurements': {'attic_sqft': 1800, 'turtle_vents': 0},
    }


def _signed_estimate():
    est = _retail_estimate()
    est['signature'] = {
        'name': 'Jane Doe', 'email': 'jane@example.com',
        'signed_at': '2026-08-01T15:04:05Z',
        'ip_address': '127.0.0.1',
        'document_hash': 'a' * 64,
        'selected_tier': 'better', 'selected_tiers': {'roofing': 'better'},
        'shingle_color': 'Charcoal', 'siding_color': '',
        'initials': [],
    }
    est['selected_tier'] = 'better'
    est['selected_tiers'] = {'roofing': 'better'}
    return est


# ── /sign page (customer view) ───────────────────────────────────────────

def test_customer_view_emits_json_ld(A):
    html = A.build_customer_view(_retail_estimate(), token='tok-1')
    m = re.search(r'<script type="application/ld\+json">(\{.+?\})</script>', html, re.S)
    assert m, 'JSON-LD block missing from customer view'
    data = json.loads(m.group(1))
    assert data['@context'] == 'https://schema.org'
    assert data['@type'] == 'Offer'
    # Seller carries the credentials so a reader can cite them
    assert data['seller']['name'] == 'Project One Roofing'
    assert data['seller']['@type'] == 'RoofingContractor'
    assert data['seller'].get('hasCredential')
    # Item priced
    assert data['priceSpecification']['priceCurrency'] == 'USD'
    assert data['priceSpecification']['price'] > 0


def test_customer_view_has_meta_description_and_og(A):
    html = A.build_customer_view(_retail_estimate(), token='tok-1')
    assert '<meta name="description"' in html
    assert 'property="og:title"' in html
    assert 'property="og:description"' in html
    # And it's specific — Loveland, CO shows up in the summary
    assert 'Loveland' in html


def test_customer_view_has_estimate_details_block_before_tc(A):
    html = A.build_customer_view(_retail_estimate(), token='tok-1')
    # The details card (unique class + heading in a div, not the CSS comment)
    assert '<div class="cvdet">' in html
    # Precedes the T&C <details> block
    det_idx = html.find('<div class="cvdet">')
    tc_idx  = html.find('View Full Terms &amp; Conditions')
    assert det_idx > 0 and tc_idx > det_idx, \
        'estimate-details block must render BEFORE the T&C'


def test_details_block_names_manufacturer_and_code(A):
    html = A.build_customer_view(_retail_estimate(), token='tok-1')
    assert 'Northgate' in html               # manufacturer/model shown
    assert 'IRC' in html                     # code citation shown
    assert 'Class 4' in html                 # differentiator shown
    assert 'Lifetime' in html                # Best-tier workmanship visible


def test_details_block_respects_visibility_toggle(A):
    est = _retail_estimate()
    est['page_visibility'] = {'estimate_details': False}
    html = A.build_customer_view(est, token='tok-1')
    assert '<div class="cvdet">' not in html


def test_insurance_view_still_has_json_ld(A):
    est = _retail_estimate()
    est['estimate_type'] = 'insurance'
    est['trades']['insurance'] = {'enabled': True, 'carrier': 'State Farm',
                                  'claim_number': 'X-1',
                                  'sections': [{'name': 'Roof',
                                                'items': [{'name': 'Shingles',
                                                           'acv': 8000, 'depreciation': 2000}]}]}
    html = A.build_customer_view(est, token='tok-2')
    assert '<script type="application/ld+json">' in html


# ── Signed PDF ───────────────────────────────────────────────────────────

def _pdf_text(bytes_):
    """Extract text from the emitted PDF; skip test if pypdf unavailable."""
    import pytest
    try:
        from pypdf import PdfReader
    except ImportError:
        pytest.skip('pypdf not installed')
    from io import BytesIO
    r = PdfReader(BytesIO(bytes_))
    return '\n'.join(p.extract_text() or '' for p in r.pages)


def test_signed_pdf_includes_about_estimate_page(A):
    pdf = A.build_signed_pdf(_signed_estimate())
    assert isinstance(pdf, (bytes, bytearray)) and len(pdf) > 5000
    text = _pdf_text(pdf)
    assert 'About This Estimate' in text
    # Materials with the specific brand name land in the PDF text
    assert 'Northgate' in text
    # Code + ventilation are cited
    assert 'IRC' in text
    assert 'Attic Ventilation' in text or 'ventilation' in text.lower()
    # Tiered warranty
    assert 'Lifetime' in text
    # Company profile
    assert 'Project One Roofing' in text


def test_signed_pdf_details_page_comes_before_terms(A):
    pdf = A.build_signed_pdf(_signed_estimate())
    text = _pdf_text(pdf)
    a_idx = text.find('About This Estimate')
    t_idx = text.find('Terms & Conditions')
    assert a_idx >= 0 and t_idx > a_idx, \
        'the About This Estimate page must precede the T&C page'


def test_unsigned_pdf_uses_estimate_title_and_skips_signature(A):
    """When a customer downloads the PDF before signing, the title bar reads
    ESTIMATE (not SIGNED CONTRACT) and the tail carries a preview notice
    instead of the E-SIGN certificate. The About-This-Estimate page and T&C
    still ship — those are the reason to download it."""
    pdf = A.build_signed_pdf(_retail_estimate(), signed=False)
    assert isinstance(pdf, (bytes, bytearray)) and len(pdf) > 5000
    text = _pdf_text(pdf)
    assert 'ESTIMATE' in text and 'SIGNED CONTRACT' not in text
    assert 'UNSIGNED PREVIEW' in text
    assert 'ELECTRONICALLY SIGNED' not in text
    # Substantive content still lands
    assert 'About This Estimate' in text
    assert 'Northgate' in text


def test_download_route_returns_pdf_attachment(client, A):
    est = _retail_estimate()
    est['share_token'] = 'download-test-token'
    A.est_save(est)
    r = client.get('/sign/download-test-token/download.pdf')
    assert r.status_code == 200
    assert r.headers['Content-Type'] == 'application/pdf'
    dispo = r.headers.get('Content-Disposition', '')
    assert 'attachment' in dispo
    assert 'Estimate' in dispo and '.pdf' in dispo
    assert r.data[:4] == b'%PDF'


def test_download_route_404s_on_bad_token(client):
    r = client.get('/sign/no-such-token/download.pdf')
    assert r.status_code == 404


def test_download_route_returns_signed_contract_when_already_signed(client, A):
    est = _signed_estimate()
    est['share_token'] = 'download-signed-token'
    A.est_save(est)
    r = client.get('/sign/download-signed-token/download.pdf')
    assert r.status_code == 200
    # It should be the signed version — title bar reads SIGNED CONTRACT.
    import pytest
    try:
        from pypdf import PdfReader
    except ImportError:
        pytest.skip('pypdf not installed')
    from io import BytesIO
    text = '\n'.join(p.extract_text() or ''
                    for p in PdfReader(BytesIO(r.data)).pages)
    assert 'SIGNED CONTRACT' in text
    assert 'ELECTRONICALLY SIGNED' in text


def test_signed_pdf_sets_document_metadata(A):
    import pytest
    try:
        from pypdf import PdfReader
    except ImportError:
        pytest.skip('pypdf not installed')
    from io import BytesIO
    pdf = A.build_signed_pdf(_signed_estimate())
    r = PdfReader(BytesIO(pdf))
    meta = r.metadata or {}
    assert 'Project One Roofing' in (meta.get('/Title') or '')
    assert 'Project One Roofing' == (meta.get('/Author') or '')
    kw = meta.get('/Keywords') or ''
    assert 'roof' in kw.lower()
    assert 'warranty' in kw.lower()
