"""Symbility (insurance carrier estimate) PDF import.

Guards the parser that turns a Symbility/Cotality export — Safeco, Liberty
Mutual, Farmers — into the same sections/metadata/summary shape the Xactimate
importer produces, so one review modal renders either format.

Symbility is a nine-column grid and is parsed in pypdf's LAYOUT mode, so the
fixture is built by drawing text at fixed x positions rather than line by line:
column geometry is the input this parser actually reads. It reproduces the
quirks observed in a real Safeco export: descriptions wrapping over two and
three lines, a bundle-rounded quantity ("11.87 (12.00)") where the rounded
figure is the one priced, bare integer quantities, ALL-CAPS adjuster comments
sitting at the same indent as real category headings, a wrapped adjuster
comment whose second line is short enough to pose as one, per-plan subtotal
checksums, a plan with no items, and a cover page carrying a full grid row that
must never be imported because it has no column header.
"""
import json
import os
import shutil
import subprocess

import pytest

from conftest import TEST_DATA_DIR  # noqa: F401  (forces DATA_DIR env setup)

# x positions (mm) of the nine columns. Chosen so no cell's text runs into the
# next one at 7pt Courier — layout extraction reads glyph positions, so an
# overlap here would corrupt the fixture rather than the parser. The last gap
# is wide because "Depreciation" is the longest heading and has to keep clear
# air before "ACV" for the column-header sniff to see two separate cells.
GRID = [8, 58, 82, 102, 114, 134, 154, 176, 200]
HEADER = ['Description', 'Quantity', 'Unit Price', 'Per', 'Total O&P',
          'Total Taxes', 'RC', 'Depreciation', 'ACV']

# Indents inside the description column. The parser tells a wrapped description
# from an adjuster's note by which of these it starts in, so the gap matters.
X_HEAD = 8      # plan + category headings, and the item number
X_CONT = 12     # continuation of an item's description
X_NOTE = 28     # free-text note hung under an item


def row(*vals):
    """One grid row: (x, text) pairs across the nine columns."""
    return [(x, v) for x, v in zip(GRID, vals) if v not in (None, '')]


def at(*pairs):
    """One free line: at((x, text), ...)."""
    return list(pairs)


def _pdf(pages):
    """Render pages of (x, text) lines, so the real _parse_symbility_pdf path
    (pypdf layout extraction) runs end to end."""
    import app as A
    pdf = A.FPDF(format='letter')
    pdf.set_auto_page_break(False)
    pdf.set_margins(4, 4, 4)
    for page in pages:
        pdf.add_page()
        pdf.set_font('Courier', '', 7)
        y = 12
        for line in page:
            for x, txt in line:
                pdf.text(x, y, str(txt))
            y += 4
    return bytes(pdf.output())


# ── fixture pages ──────────────────────────────────────────────────────────

PAGE_META = [
    at((X_HEAD, 'Fixture Mutual Insurance Company')),
    at((X_HEAD, 'PO Box 999')),
    at((X_HEAD, 'Scranton, PA 18505-5014')),
    at((X_HEAD, 'CLAIM NO.:'), (40, '778899001'),
       (110, 'INSURED:'), (140, 'Jane Fixture')),
    at((12, 'Date of Loss:'), (40, '04/03/2026'),
       (110, 'Address:'), (140, '99 MAILING RD')),
    at((12, 'Deductible:'), (40, '$2,500.00'), (140, 'GREELEY CO  80631')),
    at((12, 'Type of Claim:'), (40, 'Wind')),
    # The database name wraps — the second row must be folded back on.
    at((X_HEAD, 'Pricing Database:'), (40, 'Cotality Data Driven USDC - April')),
    at((40, '2026 (Colorado) (Fort Collins)')),
    at((X_HEAD, 'Claim Rep:'), (40, 'Pat Adjuster')),
    at((X_HEAD, 'Policy No.:'), (40, 'OY1234567')),
    at((X_HEAD, 'Policy Type:'), (40, "Homeowner's")),
    # Loss address is the risk location and must win over the mailing Address.
    at((X_HEAD, 'License:'), (110, 'Loss address:'), (140, '12 TEST LN')),
    at((140, 'WINDSOR CO  80550-2951')),
]

# No column header, but a full grid row: only the header check keeps this out.
PAGE_COVER = [
    at((X_HEAD, 'If you have questions about this estimate, please contact us.')),
    row('1  FAKE GUIDE EXAMPLE', '99.99', '$1.00', 'SF',
        '$0.00', '$0.00', '$99.99', '$0.00', '$99.99'),
]

PAGE_EXTERIOR = [
    row(*HEADER),
    at((X_HEAD, 'ESTIMATE: Structure'), (140, 'Claim #778899001, Jane Fixture')),
    at((12, 'Completed')),
    at((X_HEAD, 'EXTERIOR PLAN: Exterior Plan')),
    at((16, 'Exterior Plan')),
    at((16, 'Exterior:  2,296.32 SF')),
    at((16, 'Building perimeter (ground):  53.34 LF')),
    at((X_HEAD, 'HOUSEWRAP / INSULATION')),
    row('1  Siding, Insulation, EPS', '352.34', '$1.52', 'SF',
        '$0.00', '$21.06', '$556.61', '$27.84', '$528.77'),
    at((X_CONT, 'Backer - Replace')),
    at((X_NOTE, 'Includes 10% waste on quantity.')),
    at((X_HEAD, 'SIDING EXTRAS')),
    row('2  Gable Vent, Vinyl -', '1', '$29.81', 'EA',
        '$0.00', '$0.01', '$29.82', '$0.00', '$29.82'),
    at((X_CONT, 'Rem/Reset')),
    row('Exterior Plan - Subtotal (2 items)', None, None, None,
        '$0.00', '$21.07', '$586.43', '$27.84', '$558.59'),
]

PAGE_ROOF = [
    row(*HEADER),
    at((X_HEAD, 'ESTIMATE: Structure'), (140, 'Claim #778899001, Jane Fixture')),
    at((X_HEAD, 'ROOFPLAN: DWELLING ROOF')),
    at((16, 'Roof')),
    at((16, 'Roof area:  2,678.53 SF     Squares:  26.8 SQ')),
    # ALL-CAPS adjuster comment left at heading indent: too long to be a heading.
    at((X_HEAD, 'BACK LOWER T-LOCK SECTION PAID FOR ON PRIOR CLAIM 056541748')),
    at((X_HEAD, 'DWELLING BACK AND RIGHT')),
    row('3  ITEL, Shingles,', '11.87 (12.00)', '$135.59', 'SQ',
        '$0.00', '$135.05', '$1,762.13', '$293.74', '$1,468.39'),
    at((X_CONT, 'Laminated/Architectu...')),
    at((X_CONT, 'Good, Supply')),
    at((X_NOTE, 'Includes 8% waste on quantity.')),
    at((X_NOTE, 'Materials quantity bundle rounding applied.')),
    at((X_NOTE, 'ACCOUNTS FOR BACK ELEVATION REPLACEMENT AS THERE IS NO WIND')),
    # Short, ALL-CAPS, and the tail of the sentence above — not a heading.
    at((X_NOTE, 'DAMAGE TO THE LEFT ELEVATION')),
    at((X_HEAD, 'VENTS AND FLASHINGS')),
    row('4  Roof Vent, Static,', '6', '$70.11', 'EA',
        '$0.00', '$0.05', '$420.71', '$0.00', '$420.71'),
    at((X_CONT, 'Box/Turtle, Galvanized')),
    at((X_CONT, '- Rem/Reset')),
    row('Roof - Subtotal (2 items)', None, None, None,
        '$0.00', '$135.10', '$2,182.84', '$293.74', '$1,889.10'),
    row('DWELLING ROOF - Subtotal (2 items)', None, None, None,
        '$0.00', '$135.10', '$2,182.84', '$293.74', '$1,889.10'),
    # A plan the adjuster zeroed out: no items, must not reach the estimate —
    # and its squares must not reach the roof total either.
    at((X_HEAD, 'ROOFPLAN: SHED ROOF')),
    at((16, 'Roof')),
    at((16, 'Roof area:  362.94 SF     Squares:  3.6 SQ')),
    at((X_HEAD, 'NO COVERAGE AS REPLACEMENT WAS PAID ON PRIOR CLAIM 056541748')),
    row('Roof - Subtotal (1 item)', None, None, None,
        '$0.00', '$0.00', '$0.00', '$0.00', '$0.00'),
    row('Subtotal', None, None, None,
        '$0.00', '$156.17', '$2,769.27', '$321.58', '$2,447.69'),
]

PAGE_TOTALS = [
    at((X_HEAD, 'CLAIM TOTALS')),
    at((X_HEAD, 'Subtotal:'), (120, '$2,613.10')),
    at((X_HEAD, 'Total taxes:'), (120, '$156.17')),
    at((X_HEAD, 'Replacement cost value:'), (120, '$2,769.27')),
    at((X_HEAD, 'Less costs payable when incurred:'), (120, '$(678.24)')),
    at((X_HEAD, 'Less Recoverable depreciation (including taxes):'), (120, '$(321.58)')),
    at((X_HEAD, 'Actual cash value:'), (120, '$2,447.69')),
    at((X_HEAD, 'Applied deductible:'), (120, '$(2,500.00)')),
    at((X_HEAD, 'Net actual cash value:'), (120, '$0.00')),
    at((X_HEAD, 'Amount payable if depreciation is recovered and costs are '
                'incurred:'), (120, '$321.58')),
]

# Same totals page with the longest label wrapped, stranding its colon on a
# second line — page widths differ between carriers, so the figure has to be
# found without leaning on the colon.
PAGE_TOTALS_WRAPPED = PAGE_TOTALS[:-1] + [
    at((X_HEAD, 'Amount payable if depreciation is recovered and costs are'),
       (120, '$321.58')),
    at((X_HEAD, 'incurred:')),
]

PAGES = [PAGE_META, PAGE_COVER, PAGE_EXTERIOR, PAGE_ROOF, PAGE_TOTALS]


def parsed():
    import app as A
    return A._parse_symbility_pdf(_pdf(PAGES))


# ── format detection ───────────────────────────────────────────────────────

def test_symbility_export_is_detected():
    import app as A
    assert A._detect_carrier_format(_pdf(PAGES)) == 'symbility'


def test_xactimate_export_is_still_detected():
    """The nine-column sniff must not claim Xactimate's four-column grid."""
    import app as A
    pdf = A.FPDF()
    pdf.set_font('Helvetica', '', 9)
    pdf.add_page()
    for line in ('Acme Insurance Company',
                 'FIXTURE 7/21/2026 Page: 1',
                 'DESCRIPTION QUANTITY UNIT RCV AGE/LIFE COND DEP % DEPREC. ACV',
                 '1. Remove 3 tab-25 yr. composition 15.88 SQ 53.97 857.04 '
                 '0/25 yrs Avg. NA (0.00) 857.04'):
        pdf.cell(0, 5, line, new_x='LMARGIN', new_y='NEXT')
    assert A._detect_carrier_format(bytes(pdf.output())) == 'xactimate'


# ── line items ─────────────────────────────────────────────────────────────

def test_every_line_item_is_read_off_the_grid():
    d = parsed()
    items = [i for s in d['sections'] for i in s['items']]
    assert [i['line_no'] for i in items] == [1, 2, 3, 4]
    first = items[0]
    assert first['qty'] == 352.34
    assert first['unit'] == 'SF'
    assert first['unit_price'] == 1.52
    assert first['rcv'] == 556.61
    assert first['depreciation'] == 27.84
    assert first['acv'] == 528.77
    assert first['tax'] == 21.06
    assert first['overhead_profit'] == 0.0
    assert not d['warnings'], d['warnings']


def test_bundle_rounded_quantity_prices_the_rounded_figure():
    """"11.87 (12.00)" means 11.87 was rounded up to whole bundles and 12.00 is
    what the carrier paid for: 12.00 x 135.59 + 135.05 tax = 1,762.13."""
    it = next(i for s in parsed()['sections'] for i in s['items']
              if i['line_no'] == 3)
    assert it['qty'] == 12.00
    assert it['qty_calculated'] == 11.87
    assert abs(it['qty'] * it['unit_price'] + it['tax'] - it['rcv']) < 0.02


def test_bare_integer_quantity_parses():
    it = next(i for s in parsed()['sections'] for i in s['items']
              if i['line_no'] == 4)
    assert it['qty'] == 6
    assert it['unit'] == 'EA'


def test_wrapped_description_is_rejoined():
    items = {i['line_no']: i['description']
             for s in parsed()['sections'] for i in s['items']}
    assert items[1] == 'Siding, Insulation, EPS Backer - Replace'
    assert items[3] == 'ITEL, Shingles, Laminated/Architectu... Good, Supply'
    assert items[4] == 'Roof Vent, Static, Box/Turtle, Galvanized - Rem/Reset'


def test_notes_hung_under_an_item_never_join_its_description():
    for desc in (i['description'] for s in parsed()['sections'] for i in s['items']):
        assert 'waste on quantity' not in desc
        assert 'bundle rounding' not in desc
        assert 'ACCOUNTS FOR' not in desc


def test_all_caps_adjuster_comments_do_not_become_categories():
    """Comments are typed in caps like the headings are. A long one left at the
    heading indent, and the short tail of a wrapped one, both had to be
    rejected — only the real headings may label an item."""
    cats = {i.get('category') for s in parsed()['sections'] for i in s['items']}
    assert cats == {'HOUSEWRAP / INSULATION', 'SIDING EXTRAS',
                    'DWELLING BACK AND RIGHT', 'VENTS AND FLASHINGS'}


def test_grid_row_on_a_page_without_the_column_header_is_ignored():
    d = parsed()
    assert not any('FAKE GUIDE' in i['description']
                   for s in d['sections'] for i in s['items'])


# ── sections ───────────────────────────────────────────────────────────────

def test_items_group_under_their_plan_with_subtotal_checksums():
    d = parsed()
    assert [s['name'] for s in d['sections']] == ['Exterior Plan', 'Dwelling Roof']
    ext, roof = d['sections']
    assert ext['totals'] == {'rcv': 586.43, 'dep': 27.84, 'acv': 558.59}
    assert roof['totals'] == {'rcv': 2182.84, 'dep': 293.74, 'acv': 1889.10}
    for sec in d['sections']:
        assert abs(sum(i['rcv'] for i in sec['items']) - sec['totals']['rcv']) < 0.02


def test_shouty_plan_names_are_title_cased_for_the_contract():
    """Section names print on the customer's contract, so DWELLING ROOF should
    not. Plans already in mixed case are left alone."""
    names = [s['name'] for s in parsed()['sections']]
    assert 'Dwelling Roof' in names
    assert 'Exterior Plan' in names


def test_plan_with_no_items_is_dropped():
    assert not any(s['name'].lower().startswith('shed')
                   for s in parsed()['sections'])


def test_plan_measurements_are_captured():
    d = parsed()
    ext = d['sections'][0]['measurements']
    roof = d['sections'][1]['measurements']
    assert ext['Exterior']['value'] == 2296.32
    assert ext['Exterior']['unit'] == 'SF'
    assert ext['Building perimeter (ground)']['value'] == 53.34
    assert roof['Roof area']['value'] == 2678.53
    assert roof['Squares']['value'] == 26.8


# ── metadata, address, summary ─────────────────────────────────────────────

def test_claim_metadata_comes_off_the_two_column_form():
    m = parsed()['meta']
    assert m['carrier'] == 'Fixture Mutual Insurance Company'
    assert m['claim_number'] == '778899001'
    assert m['insured'] == 'Jane Fixture'
    assert m['date_of_loss'] == '04/03/2026'
    assert m['policy_number'] == 'OY1234567'
    assert m['type_of_loss'] == 'Wind'
    assert m['adjuster'] == 'Pat Adjuster'
    # the wrapped second row is folded back on
    assert m['price_list'] == 'Cotality Data Driven USDC - April 2026 (Colorado) (Fort Collins)'


def test_loss_address_wins_over_the_mailing_address():
    assert parsed()['address'] == {
        'street': '12 TEST LN', 'city': 'Windsor', 'state': 'CO', 'zip': '80550'}


def test_claim_summary_reads_the_totals_page():
    s = parsed()['summary']
    assert s['line_item_total'] == 2613.10
    assert s['material_sales_tax'] == 156.17
    assert s['rcv_total'] == 2769.27
    assert s['acv_total'] == 2447.69
    assert s['net_claim'] == 0.0
    assert s['net_claim_if_recovered'] == 321.58


def test_summary_label_wrapped_onto_a_second_line_still_finds_its_figure():
    import app as A
    pages = [PAGE_META, PAGE_COVER, PAGE_EXTERIOR, PAGE_ROOF, PAGE_TOTALS_WRAPPED]
    assert A._parse_symbility_pdf(_pdf(pages))['summary']['net_claim_if_recovered'] == 321.58


def test_money_coming_off_the_claim_is_reported_as_a_magnitude():
    """The carrier prints these as negatives; the claim card and the Xactimate
    importer both deal in positives."""
    s = parsed()['summary']
    assert s['deductible'] == 2500.00
    assert s['recoverable_depreciation'] == 321.58
    assert s['depreciation_total'] == 321.58
    assert s['paid_when_incurred'] == 678.24


def test_estimate_subtotal_row_becomes_the_line_item_checksum():
    s = parsed()['summary']
    assert s['line_items_rcv'] == 2769.27
    assert s['line_items_depreciation'] == 321.58
    assert s['line_items_acv'] == 2447.69


# ── warnings ───────────────────────────────────────────────────────────────

def test_subtotal_that_disagrees_with_its_lines_warns():
    import app as A
    pages = [PAGE_META, PAGE_COVER,
             PAGE_EXTERIOR[:-1] + [row('Exterior Plan - Subtotal (2 items)',
                                       None, None, None, '$0.00', '$21.07',
                                       '$999.99', '$27.84', '$558.59')]]
    warns = A._parse_symbility_pdf(_pdf(pages))['warnings']
    assert any('review carefully' in w for w in warns), warns


def test_line_whose_acv_and_depreciation_miss_its_rc_warns():
    import app as A
    bad = [row(*HEADER),
           at((X_HEAD, 'EXTERIOR PLAN: Exterior Plan')),
           row('1  Siding, Insulation, EPS', '352.34', '$1.52', 'SF',
               '$0.00', '$21.06', '$556.61', '$27.84', '$1.00')]
    warns = A._parse_symbility_pdf(_pdf([PAGE_META, bad]))['warnings']
    assert any('ACV + depreciation' in w for w in warns), warns


# ── endpoint ───────────────────────────────────────────────────────────────

def test_endpoint_routes_a_symbility_upload(client):
    import io as _io
    r = client.post('/api/parse-xactimate',
                    data={'file': (_io.BytesIO(_pdf(PAGES)), 'estimate.pdf')},
                    content_type='multipart/form-data')
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body['format'] == 'symbility'
    assert sum(len(s['items']) for s in body['sections']) == 4
    assert body['meta']['claim_number'] == '778899001'


# ── review modal ───────────────────────────────────────────────────────────
# The modal is shared by both formats and only its labels differ, so these run
# the real openXactModal lifted out of static/app.js rather than asserting on
# the payload and hoping the browser agrees.

RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      'xact_modal_runner.js')
needs_node = pytest.mark.skipif(shutil.which('node') is None,
                                reason='node not installed — modal cannot be rendered')


def _render(payload, tmp_path, measurements=None):
    src = tmp_path / 'payload.json'
    out = tmp_path / 'modal.json'
    src.write_text(json.dumps(payload), encoding='utf-8')
    proc = subprocess.run(
        ['node', RUNNER, str(src), str(out), json.dumps(measurements or {})],
        capture_output=True, text=True)
    assert proc.returncode == 0, f'xact_modal_runner.js failed:\n{proc.stderr}'
    return json.loads(out.read_text(encoding='utf-8'))


@needs_node
def test_modal_labels_a_symbility_import(tmp_path):
    r = _render(parsed(), tmp_path)
    assert '>Symbility<' in r['modal']
    assert 'Pricing Database' in r['modal']
    assert 'Price List' not in r['modal']
    assert 'Austin' not in r['modal']          # fixture adjuster, not the real one
    assert 'Pat Adjuster' in r['modal']
    assert 'Jane Fixture' in r['modal']
    # section names and every line reach the review table
    assert 'value="Exterior Plan"' in r['modal']
    assert 'value="Dwelling Roof"' in r['modal']
    assert '4 of 4 lines' in r['totals']


@needs_node
def test_modal_still_labels_an_xactimate_import(tmp_path):
    payload = {
        'format': 'xactimate',
        'meta': {'carrier': 'Acme Insurance', 'claim_number': '111222333',
                 'price_list': 'COFC8X_JUL26'},
        'address': {}, 'warnings': [], 'summary': {},
        'sections': [{'name': 'Roof', 'items': [
            {'description': 'Remove 3 tab-25 yr. composition', 'qty': 15.88,
             'unit': 'SQ', 'unit_price': 53.97, 'rcv': 857.04,
             'depreciation': 0.0, 'acv': 857.04}]}],
    }
    r = _render(payload, tmp_path)
    assert '>Xactimate<' in r['modal']
    assert 'Price List' in r['modal']
    assert 'Pricing Database' not in r['modal']


# ── roof measurements ──────────────────────────────────────────────────────
# Symbility prints a measurement block per plan. Those figures are offered to
# the estimate only as a fallback: a RoofR report is a measured take-off and
# these are a few numbers off the adjuster's diagram, so an estimate that
# already has measurements keeps them. The rule itself lives in
# applyXactImport; these cover the figures it is handed.

def test_roof_measurements_map_to_estimator_fields():
    assert parsed()['measurements'] == {'roof_squares': 26.8}


def test_only_mappable_labels_come_across():
    """Soffit, Footprint, Subtractions and the exterior wall areas have no
    unambiguous counterpart, so they are dropped rather than guessed at."""
    assert set(parsed()['measurements']) <= {'roof_squares', 'eave_lf', 'ridge_lf'}


def test_zeroed_out_plan_does_not_inflate_the_roof_total():
    """The fixture's SHED ROOF is a plan the adjuster gave no coverage. Its
    squares must not be added to the dwelling's — the estimate would order
    material for a roof nobody is paying to replace."""
    assert parsed()['measurements']['roof_squares'] == 26.8


def test_roof_area_stands_in_when_a_plan_omits_its_squares():
    import app as A
    page = [row(*HEADER),
            at((X_HEAD, 'ROOFPLAN: DWELLING ROOF')),
            at((16, 'Roof area:  2,678.53 SF')),
            at((X_HEAD, 'VENTS AND FLASHINGS')),
            row('1  Roof Vent, Static,', '6', '$70.11', 'EA',
                '$0.00', '$0.05', '$420.71', '$0.00', '$420.71')]
    m = A._parse_symbility_pdf(_pdf([PAGE_META, page]))['measurements']
    assert m['roof_squares'] == 26.79      # 2,678.53 SF / 100


def test_two_covered_roof_plans_are_summed():
    import app as A
    page = [row(*HEADER),
            at((X_HEAD, 'ROOFPLAN: MAIN')),
            at((16, 'Squares:  20.0 SQ')), at((16, 'Eaves:  100.00 LF')),
            row('1  Roof Vent, Static,', '6', '$70.11', 'EA',
                '$0.00', '$0.05', '$420.71', '$0.00', '$420.71'),
            at((X_HEAD, 'ROOFPLAN: DETACHED GARAGE')),
            at((16, 'Squares:  6.5 SQ')), at((16, 'Eaves:  40.00 LF')),
            row('2  Roof Vent, Static,', '2', '$70.11', 'EA',
                '$0.00', '$0.02', '$140.24', '$0.00', '$140.24')]
    m = A._parse_symbility_pdf(_pdf([PAGE_META, page]))['measurements']
    assert m['roof_squares'] == 26.5
    assert m['eave_lf'] == 140.0


@needs_node
def test_modal_says_measurements_will_be_used_when_the_estimate_is_empty(tmp_path):
    r = _render(parsed(), tmp_path, measurements={})
    assert 'Roof measurements in this PDF' in r['modal']
    assert 'currently empty' in r['modal']


@needs_node
def test_modal_says_measurements_are_held_back_when_a_report_exists(tmp_path):
    r = _render(parsed(), tmp_path, measurements={'roof_squares': 31.2})
    assert 'left out' in r['modal']
    assert 'already has measurements' in r['modal']


@needs_node
def test_modal_says_nothing_about_measurements_for_xactimate(tmp_path):
    payload = {'format': 'xactimate', 'meta': {}, 'address': {}, 'warnings': [],
               'summary': {}, 'sections': [{'name': 'Roof', 'items': []}]}
    assert 'Roof measurements in this PDF' not in _render(payload, tmp_path)['modal']
