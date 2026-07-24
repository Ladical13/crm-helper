"""Xactimate (insurance carrier estimate) PDF import.

Guards the parser that turns a carrier's Xactimate export into sections of
line items (ACV/depreciation; RCV derived downstream), claim metadata, and
the claim summary. The synthetic fixture reproduces every quirk observed in a
real Allstate export: wrapped descriptions (numeric tail first AND
description-first), NA depreciation, the [M] max-depreciation marker,
Options/waste/allowance noise, per-section Totals checksums, a page-break
mid-section, per-coverage summary blocks, a 3-number recap row that must not
double-count, and an instructional "sample guide" page with FAKE example
items (no running "Page: N" header) that must never be imported.
"""
from conftest import TEST_DATA_DIR  # noqa: F401  (forces DATA_DIR env setup)


def _pdf(pages):
    """Build a PDF whose text lines mimic a carrier Xactimate export, so the
    real _parse_xactimate_pdf path (pypdf text extraction) runs end to end."""
    import app as A
    pdf = A.FPDF()
    pdf.set_font('Helvetica', '', 9)
    for lines in pages:
        pdf.add_page()
        for line in lines:
            pdf.cell(0, 5, line, new_x='LMARGIN', new_y='NEXT')
    return bytes(pdf.output())


PAGE1 = [
    'Acme Insurance Company',
    'P.O. Box 1234',
    'Springfield, TX 75266',
    'Fax: 866-000-0000',
    'www.example.com',
    'FIXTURE2 7/21/2026 Page: 1',
    'Insured: JANE FIXTURE Home: (970) 555-0000',
    'Property: 12 TEST LN',
    'WINDSOR, CO 80550-2951',
    'Claim Number: 111222333 Policy Number: 000999888 Type of Loss: Windstorm and Hail',
    'Date of Loss: 6/25/2025 12:00 PM Date Received: 7/13/2026 11:55 AM',
    'Price List: COFC8X_JUL26',
    'Estimate: FIXTURE2',
]

# Instructional guide page: fake example items, restarts numbering, and has
# NO "Page: N" running header — the page filter must drop it entirely.
GUIDE_PAGE = [
    'Your guide to reading your adjuster summary.*',
    'Insured: John Smith',
    'Claim Number: 1234567890 Policy Number: 000000123456789',
    'DESCRIPTION QUANTITY UNIT RCV AGE/LIFE COND DEP % DEPREC. ACV',
    '1. Remove 3 tab-25 yr. composition 15.88SQ 53.97 857.04 0/25 yrs Avg. NA (0.00) 857.04',
    '2. 3 tab-25 yr.-comp. shingle roofing 18.33SQ 219.11 4,016.29 2/25 yrs Avg. 8% (165.16) 3,851.13',
    'Total: Roof 4,873.33 165.16 4,708.17',
    'Total Recoverable Depreciation 47.94',
    'Net Claim if Depreciation is Recovered $854.93',
]

PAGE2 = [
    'Acme Insurance Company',
    'FIXTURE2 7/21/2026 Page: 2',
    'Dwelling Roof',
    '2874.13 Surface Area',
    '28.74 Number of Squares',
    '125.03 Total Ridge Length',
    'DESCRIPTION QUANTITY UNIT RCV AGE/LIFE COND. DEP % DEPREC. ACV',
    'Roofing',
    # tail on first line, description continues on the next
    '1. Remove Laminated - Standard grd - comp. 28.74 SQ 75.56 2,171.59 23/30 yrs Avg. NA (0.00) 2,171.59',
    'shingle rfg. - w/ felt',
    # [M] marker
    '2. Roofing felt - 15 lb. 28.74 SQ 43.52 1,250.76 23/20 yrs Avg. 90% [M] (1,125.68) 125.08',
    '3. Laminated - Standard grd - comp. shingle rfg. 32.67 SQ 295.36 9,649.41 23/30 yrs Avg. 76.67% (7,397.88) 2,251.53',
    '- w/out felt',
    'Auto Calculated Waste: 13.7%, 3.93SQ',
    'Options: Valleys: Closed-cut (half laced), Include eave starter course: Yes',
    'This line item includes an allowance of $132.28 per unit, which reflects current market values.',
    # uppercase continuation allowed because the description dangles with '-'
    '4. Detach & Reset Roof vent - turtle type - 6.00 EA 74.95 449.70 0/35 yrs Avg. 0% (0.00) 449.70',
    'Metal',
    'Components',
    '5. Remove Flashing - pipe jack 3.00 EA 9.95 29.85 23/NA Avg. NA (0.00) 29.85',
    'Totals: Dwelling Roof 13,551.31 9,523.56 4,027.75',
    'FRONT ELEVATION',
    'DESCRIPTION QUANTITY UNIT RCV AGE/LIFE COND. DEP % DEPREC. ACV',
    '6. R&R Gutter / downspout - aluminum - up to 10.00 LF 11.56 115.60 23/25 yrs Avg. 90% [M] (97.65) 17.95',
    '5"',
    'The above line item relates to damaged downspouts on this elevation.',
    'Totals: FRONT ELEVATION 115.60 97.65 17.95',
    # Shed's name + measurements sit at the END of this page...
    'Shed',
    '108.94 Surface Area',
]

PAGE3 = [
    # ...its items start after the page break, behind the carrier header block.
    'Acme Insurance Company',
    'P.O. Box 1234',
    'Springfield, TX 75266',
    'Fax: 866-000-0000',
    'www.example.com',
    'FIXTURE2 7/21/2026 Page: 3',
    'DESCRIPTION QUANTITY UNIT RCV AGE/LIFE COND. DEP % DEPREC. ACV',
    # description-first wrap: the numeric tail lands on the SECOND line
    '7. Remove 3 tab - 25 yr. - composition shingle',
    'roofing - incl. felt 1.09 SQ 73.89 80.54 10/25 yrs Avg. NA (0.00) 80.54',
    '8. Roofing felt - 15 lb. 1.09 SQ 43.52 47.44 10/20 yrs Avg. 50% (23.72) 23.72',
    'Totals: Shed 127.98 23.72 104.26',
    'Line Item Totals: FIXTURE2 13,794.89 9,644.93 4,149.96',
]

SUMMARY_PAGES = [
    [
        'Acme Insurance Company',
        'FIXTURE2 7/21/2026 Page: 4',
        'Summary for AA-Dwelling',
        'Line Item Total 13,666.91',
        'Material Sales Tax 320.80',
        'Replacement Cost Value $13,987.71',
        'Less Depreciation (9,621.21)',
        'Actual Cash Value $4,366.50',
        'Less Deductible (500.00)',
        'Net Claim $3,866.50',
        'Total Recoverable Depreciation 9,621.21',
        'Net Claim if Depreciation is Recovered $13,487.71',
    ],
    [
        'Acme Insurance Company',
        'FIXTURE2 7/21/2026 Page: 5',
        'Summary for BB-Other Structures',
        'Line Item Total 127.98',
        'Material Sales Tax 13.65',
        'Replacement Cost Value $141.63',
        'Less Depreciation (23.72)',
        'Actual Cash Value $117.91',
        'Net Claim $117.91',
        'Total Recoverable Depreciation 23.72',
        'Net Claim if Depreciation is Recovered $141.63',
        # Recap-style 3-number row: must NOT be added to the coverage sums
        'Material Sales Tax 334.45 255.73 83.44',
    ],
]

FULL_DOC = [PAGE1, GUIDE_PAGE, PAGE2, PAGE3] + SUMMARY_PAGES


def _parse(A, pages=None):
    return A._parse_xactimate_pdf(_pdf(pages or FULL_DOC))


# ── sections & items ───────────────────────────────────────────────────

def test_parses_sections_and_items(A):
    data = _parse(A)
    names = [s['name'] for s in data['sections']]
    assert names == ['Dwelling Roof', 'FRONT ELEVATION', 'Shed']
    roof = data['sections'][0]
    assert [it['line_no'] for it in roof['items']] == [1, 2, 3, 4, 5]
    it = roof['items'][2]
    assert it['qty'] == 32.67 and it['unit'] == 'SQ' and it['unit_price'] == 295.36
    assert it['rcv'] == 9649.41 and it['depreciation'] == 7397.88 and it['acv'] == 2251.53
    assert data['warnings'] == []


def test_wrapped_descriptions_stitched(A):
    data = _parse(A)
    roof, front, shed = data['sections']
    assert roof['items'][0]['description'] == \
        'Remove Laminated - Standard grd - comp. shingle rfg. - w/ felt'
    assert roof['items'][2]['description'].endswith('- w/out felt')
    # uppercase continuation accepted because the description dangles with '-'
    assert roof['items'][3]['description'] == 'Detach & Reset Roof vent - turtle type - Metal'
    assert front['items'][0]['description'].endswith('up to 5"')
    # description-first wrap (tail on the second line)
    assert shed['items'][0]['description'] == \
        'Remove 3 tab - 25 yr. - composition shingle roofing - incl. felt'


def test_na_dep_and_material_marker(A):
    roof = _parse(A)['sections'][0]
    assert roof['items'][0]['depreciation'] == 0.0 and roof['items'][0]['dep_pct'] == 'NA'
    assert roof['items'][1]['depreciation'] == 1125.68   # the 90% [M] line
    assert roof['items'][4]['age_life'] == '23/NA'


def test_noise_never_becomes_items_or_descriptions(A):
    data = _parse(A)
    all_text = ' '.join(it['description'] for s in data['sections'] for it in s['items'])
    for frag in ('Auto Calculated', 'Options', 'allowance', 'above line item', 'Components'):
        assert frag not in all_text
    # "Components" category label did not glue onto item 4's description
    assert data['sections'][0]['items'][3]['description'].endswith('Metal')


def test_section_survives_page_break(A):
    shed = _parse(A)['sections'][2]
    assert shed['name'] == 'Shed'
    assert [it['line_no'] for it in shed['items']] == [7, 8]
    assert shed['totals'] == {'rcv': 127.98, 'dep': 23.72, 'acv': 104.26}


def test_sample_guide_page_rejected(A):
    data = _parse(A)
    descs = ' '.join(it['description'] for s in data['sections'] for it in s['items'])
    assert '3 tab-25 yr' not in descs          # guide items absent
    total = sum(it['rcv'] for s in data['sections'] for it in s['items'])
    assert abs(total - 13794.89) < 0.05        # matches Line Item Totals checksum


def test_guide_run_rejected_even_when_page_filter_passes(A):
    # Same guide content but WITH a running header: the page filter keeps it,
    # so the line-number-run + checksum defense has to reject it instead.
    guide = ['FIXTURE2 7/21/2026 Page: 9' if 'guide to reading' in ln else ln
             for ln in GUIDE_PAGE]
    guide[0] = 'FIXTURE2 7/21/2026 Page: 9'
    data = _parse(A, [PAGE1, guide, PAGE2, PAGE3] + SUMMARY_PAGES)
    descs = ' '.join(it['description'] for s in data['sections'] for it in s['items'])
    assert '3 tab-25 yr.-comp' not in descs
    total = sum(it['rcv'] for s in data['sections'] for it in s['items'])
    assert abs(total - 13794.89) < 0.05


# ── metadata & summary ─────────────────────────────────────────────────

def test_meta_and_address(A):
    data = _parse(A)
    m = data['meta']
    assert m['carrier'] == 'Acme Insurance Company'
    assert m['claim_number'] == '111222333'
    assert m['policy_number'] == '000999888'
    assert m['type_of_loss'] == 'Windstorm and Hail'
    assert m['price_list'] == 'COFC8X_JUL26'
    assert m['date_of_loss'] == '6/25/2025'
    assert m['insured'] == 'JANE FIXTURE'
    assert data['address'] == {'street': '12 TEST LN', 'city': 'Windsor',
                               'state': 'CO', 'zip': '80550'}


def test_summary_sums_coverages(A):
    s = _parse(A)['summary']
    assert s['deductible'] == 500.00
    assert s['rcv_total'] == round(13987.71 + 141.63, 2)
    assert s['acv_total'] == round(4366.50 + 117.91, 2)
    assert s['net_claim'] == round(3866.50 + 117.91, 2)
    assert s['recoverable_depreciation'] == round(9621.21 + 23.72, 2)
    assert s['net_claim_if_recovered'] == round(13487.71 + 141.63, 2)
    # 3-number recap row skipped — coverages only
    assert s['material_sales_tax'] == round(320.80 + 13.65, 2)
    assert s['line_items_rcv'] == 13794.89
    assert s['line_items_depreciation'] == 9644.93
    assert s['line_items_acv'] == 4149.96


def test_rcv_mismatch_warns_and_keeps_acv_dep(A):
    page = list(PAGE2[:9])   # up through the column header + 'Roofing'
    page.append('1. Widget thing 1.00 EA 100.00 999.99 0/10 yrs Avg. 10% (10.00) 90.00')
    page.append('Line Item Totals: FIXTURE2 999.99 10.00 90.00')
    data = _parse(A, [PAGE1, page])
    assert any('RCV' in w for w in data['warnings'])
    it = data['sections'][0]['items'][0]
    assert it['acv'] == 90.00 and it['depreciation'] == 10.00


# ── endpoint ───────────────────────────────────────────────────────────

def _post(client, data, name='est.pdf'):
    import io
    return client.post('/api/parse-xactimate',
                       data={'file': (io.BytesIO(data), name)},
                       content_type='multipart/form-data')


def test_endpoint_happy_path(client):
    r = _post(client, _pdf(FULL_DOC))
    assert r.status_code == 200
    body = r.get_json()
    assert body['meta']['claim_number'] == '111222333'
    assert len(body['sections']) == 3


def test_endpoint_rejects_non_pdf(client):
    r = _post(client, b'not a pdf', name='est.txt')
    assert r.status_code == 400


def test_endpoint_422_when_no_items(client):
    r = _post(client, _pdf([['FIXTURE2 Page: 1', 'Just some text', 'No items here']]))
    assert r.status_code == 422


def test_endpoint_requires_auth(anon):
    r = _post(anon, _pdf(FULL_DOC))
    assert r.status_code in (302, 401, 403)
