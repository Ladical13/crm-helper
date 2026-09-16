"""Scanned carrier estimates: a PDF with no text layer, read off its images.

No test here calls the real API. A fake client stands in for Claude and hands
back a transcription; what is under test is everything around that -- the
page rendering, the shape the transcription is turned into, the checks that
catch a misread, and the background job the browser polls.

The transcription is the real Liberty Mutual scan that motivated this (the
59th Ave claim), figures as printed and the homeowner replaced. Its lines add
up to the carrier's $22,379.73, which is what a correct read must prove.
"""
import io
import json
from types import SimpleNamespace

import pytest

from conftest import TEST_DATA_DIR  # noqa: F401  (forces DATA_DIR env setup)

# line_no, description, qty, qty_calculated, unit, price, tax, rc, dep, acv
LM_LINES = [
    (1, 'Dumpster, 10 Yard', 1, None, 'EA', 476.78, 37.95, 514.73, 0.00, 514.73),
    (2, 'Remove - Window Screen, Aluminum, 3-5 SF', 3, None, 'EA', 5.42, 0.00, 16.26, 0.00, 16.26),
    (3, 'Replace - Window Screen, Aluminum, 3-5 SF', 3, None, 'EA', 30.55, 3.61, 95.26, 44.45, 50.81),
    (4, 'ITEL, Shingles, Laminated/Architectural, Tear Out', 19.81, None, 'SQ', 117.28, 0.00, 2323.32, 0.00, 2323.32),
    (5, 'ITEL, Shingles, Laminated/Architectural, Good, Supply', 21.67, 21.39, 'SQ', 139.55, 240.71, 3264.76, 761.67, 2503.09),
    (6, 'ITEL, Shingles, Laminated/Architectural, Good, Install', 21.39, None, 'SQ', 280.26, 4.34, 5999.10, 1399.59, 4599.51),
    (7, 'Replace - Shingles, Starter Row, Eave, Continuous', 198.39, None, 'LF', 3.53, 9.00, 709.31, 248.26, 461.05),
    (8, 'Replace - Ridge Cap Shingles, 3-Tab', 131.92, None, 'LF', 8.45, 11.96, 1126.69, 394.34, 732.35),
    (9, 'Replace - Felt, Single Layer, 15 lb.', 17.15, None, 'SQ', 60.73, 12.47, 1053.99, 368.89, 685.10),
    (10, 'Replace - Ice/Water Shield, Single Row, LF', 196.52, None, 'LF', 5.64, 26.43, 1134.80, 317.74, 817.06),
    (12, 'Replace - Drip Edge, Rake, Aluminum, Pre-Finished Color', 220.50, None, 'LF', 4.07, 29.83, 927.27, 324.53, 602.74),
    (13, 'Rem/Reset - Roof Vent, Static, Box/Turtle, Aluminum', 2, None, 'EA', 105.04, 0.01, 210.09, 0.00, 210.09),
    (14, 'Rem/Reset - Flashing, Pipe Jack, Aluminum', 6, None, 'EA', 86.62, 0.68, 520.40, 0.00, 520.40),
    (15, 'Remove - Gutter, K-Style, Aluminum, 5"', 210.53, None, 'LF', 3.12, 0.00, 656.85, 0.00, 656.85),
    (16, 'Replace - Gutter, K-Style, Aluminum, 5"', 221.06, None, 'LF', 15.18, 64.76, 3420.45, 1330.21, 2090.24),
    (17, 'Remove - Downspout, Aluminum, 2"x3"', 24.00, None, 'LF', 3.12, 0.00, 74.88, 0.00, 74.88),
    (18, 'Replace - Downspout, Aluminum, 2"x3"', 25.20, None, 'LF', 12.89, 6.74, 331.57, 116.04, 215.53),
]


def transcription(lines=LM_LINES, subtotal=True, grand=None, **over):
    raw = {
        'format': 'symbility',
        'meta': {'carrier': 'Liberty Mutual Insurance', 'claim_number': '555000111',
                 'policy_number': 'OY0000000', 'insured': 'Sam Fixture',
                 'date_of_loss': '07/06/2025', 'type_of_loss': 'Hail',
                 'price_list': 'Cotality Data Driven USDC - August 2026 (Colorado) (Denver)',
                 'adjuster': 'Pat Adjuster'},
        'address': {'street': '12 TEST AVE', 'city': 'ARVADA', 'state': 'CO', 'zip': '80004'},
        'sections': [{
            'name': 'Hover XML 1 - 23788523',
            'items': [{'line_no': n, 'description': d, 'qty': q, 'qty_calculated': qc,
                       'unit': u, 'unit_price': p, 'tax': t, 'overhead_profit': 0,
                       'rcv': rc, 'depreciation': dep, 'nonrecoverable': False, 'acv': acv}
                      for n, d, q, qc, u, p, t, rc, dep, acv in lines],
            'subtotal': ({'rcv': 22379.73, 'depreciation': 5305.72, 'acv': 17074.01}
                         if subtotal else None),
        }],
        'line_item_totals': grand,
        'summary': {'line_item_total': 21931.24, 'material_sales_tax': 448.49,
                    'rcv_total': 22379.73, 'acv_total': None, 'deductible': 5000.00,
                    'net_claim': 11559.28, 'recoverable_depreciation': 5305.72,
                    'net_claim_if_recovered': 17379.73, 'paid_when_incurred': 514.73},
        'measurements': {'roof_squares': 22.6, 'eave_lf': 210.53, 'ridge_lf': 35.33},
        'unreadable': [],
        'missing_pages': [],
    }
    raw.update(over)
    return raw


class FakeClient:
    """Stands in for anthropic.Anthropic(): records the request, returns a
    canned final message from the streaming helper."""

    def __init__(self, raw=None, stop_reason='end_turn'):
        self.raw, self.stop_reason, self.calls = raw, stop_reason, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kw):
        self.calls.append(kw)
        msg = SimpleNamespace(
            stop_reason=self.stop_reason, model=kw['model'],
            content=[SimpleNamespace(type='text', text=json.dumps(self.raw))],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1))

        class _Ctx:
            def __enter__(self_inner):
                return SimpleNamespace(get_final_message=lambda: msg)

            def __exit__(self_inner, *a):
                return False
        return _Ctx()


def scanned_pdf(pages=2):
    import app as A
    from PIL import Image
    pdf = A.FPDF(format='letter')
    for _ in range(pages):
        buf = io.BytesIO()
        Image.new('RGB', (200, 260), 'white').save(buf, format='PNG')
        buf.seek(0)
        pdf.add_page()
        pdf.image(buf, x=0, y=0, w=215)
    return bytes(pdf.output())


# ── reading ────────────────────────────────────────────────────────────────

def test_every_page_is_sent_as_an_image_with_a_schema():
    import carrier_scan as cs
    fake = FakeClient(transcription())
    cs.read(scanned_pdf(pages=3), client=fake)
    kw = fake.calls[0]
    images = [b for b in kw['messages'][0]['content'] if b['type'] == 'image']
    assert len(images) == 3
    assert all(b['source']['media_type'] == 'image/jpeg' for b in images)
    assert kw['output_config']['format']['type'] == 'json_schema'
    assert kw['model'] == cs.MODEL


def test_a_correct_read_of_the_real_scan_reconciles():
    import app as A
    import carrier_scan as cs
    d = cs.read(scanned_pdf(), client=FakeClient(transcription()))
    assert d['scanned'] is True and d['format'] == 'symbility'
    items = d['sections'][0]['items']
    assert len(items) == 17 and not any(i.get('math_off') for i in items)
    rec = A._carrier_reconcile(d)
    assert rec['ok'], rec
    assert rec['carrier_rcv'] == 22379.73


def test_the_rep_is_always_told_it_came_off_a_scan():
    import carrier_scan as cs
    d = cs.read(scanned_pdf(), client=FakeClient(transcription()))
    assert any('scanned copy' in w for w in d['warnings'])


def test_a_misread_unit_price_fails_the_import_even_when_totals_agree():
    """$280.26 read as $230.26 leaves RCV, ACV and depreciation untouched, so
    neither of the carrier-total checks can see it -- only the line's own
    qty x price arithmetic does. It must not pass green."""
    import app as A
    import carrier_scan as cs
    lines = [l if l[0] != 6 else l[:5] + (230.26,) + l[6:] for l in LM_LINES]
    d = cs.read(scanned_pdf(), client=FakeClient(transcription(lines)))
    rec = A._carrier_reconcile(d)
    assert not rec['ok'] and 6 in rec['lines_off']
    assert any(w.startswith('Line 6:') for w in d['warnings'])


def test_a_misread_rcv_fails_on_the_carrier_total():
    import app as A
    import carrier_scan as cs
    lines = [l if l[0] != 16 else l[:7] + (3470.45,) + l[8:] for l in LM_LINES]
    d = cs.read(scanned_pdf(), client=FakeClient(transcription(lines)))
    assert not A._carrier_reconcile(d)['ok']


def test_plan_subtotal_stands_in_for_a_missing_grand_total_only_when_every_plan_has_one():
    import carrier_scan as cs
    d = cs.read(scanned_pdf(), client=FakeClient(transcription()))
    assert d['summary']['line_items_rcv'] == 22379.73
    d = cs.read(scanned_pdf(), client=FakeClient(transcription(subtotal=False)))
    assert 'line_items_rcv' not in d['summary']


def test_printed_grand_total_wins_over_section_subtotals():
    import carrier_scan as cs
    raw = transcription(grand={'rcv': 22379.73, 'depreciation': 5305.72, 'acv': 17074.01})
    raw['sections'][0]['subtotal'] = {'rcv': 1.0, 'depreciation': 0, 'acv': 1.0}
    d = cs.read(scanned_pdf(), client=FakeClient(raw))
    assert d['summary']['line_items_rcv'] == 22379.73


def test_shape_matches_what_the_symbility_parser_returns():
    import carrier_scan as cs
    d = cs.read(scanned_pdf(), client=FakeClient(transcription()))
    line5 = next(i for i in d['sections'][0]['items'] if i['line_no'] == 5)
    assert line5['qty'] == 21.67 and line5['qty_calculated'] == 21.39
    assert 'overhead_profit' in line5 and 'op' not in line5
    assert d['sections'][0]['totals'] == {'rcv': 22379.73, 'dep': 5305.72, 'acv': 17074.01}
    assert d['measurements'] == {'roof_squares': 22.6, 'eave_lf': 210.53, 'ridge_lf': 35.33}
    assert d['address'] == {'street': '12 TEST AVE', 'city': 'Arvada', 'state': 'CO',
                            'zip': '80004'}
    s = d['summary']
    assert s['deductible'] == 5000.0 and s['net_claim'] == 11559.28
    assert 'acv_total' not in s                      # null is dropped, not zeroed


def test_xactimate_scan_carries_no_measurements():
    """RoofR is the source of truth for an Xactimate job; its parser returns no
    measurements and a scan of one must not start supplying them."""
    import carrier_scan as cs
    d = cs.read(scanned_pdf(), client=FakeClient(transcription(format='xactimate')))
    assert 'measurements' not in d
    assert 'op' in d['sections'][0]['items'][0]


def test_unreadable_figures_and_missing_pages_are_named():
    import carrier_scan as cs
    raw = transcription(unreadable=['line 9 quantity'], missing_pages=['3 and 4 of 7'])
    d = cs.read(scanned_pdf(), client=FakeClient(raw))
    assert any('line 9 quantity' in w for w in d['warnings'])
    assert any('3 and 4 of 7' in w for w in d['warnings'])


@pytest.mark.parametrize('stop', ['refusal', 'max_tokens'])
def test_an_incomplete_read_raises_rather_than_returning_partial_lines(stop):
    import carrier_scan as cs
    with pytest.raises(cs.ScanError):
        cs.read(scanned_pdf(), client=FakeClient(transcription(), stop_reason=stop))


# ── the endpoint and its job ───────────────────────────────────────────────

@pytest.fixture
def inline_scan(monkeypatch):
    """Scans available, read by a fake client, on the request thread."""
    import app as A
    import carrier_scan as cs
    state = {'raw': transcription()}
    monkeypatch.setattr(cs, 'available', lambda: True)
    real_read = cs.read
    monkeypatch.setattr(cs, 'read',
                        lambda raw, client=None: real_read(raw, client=FakeClient(state['raw'])))
    monkeypatch.setattr(A, '_spawn', lambda target, *args: target(*args))
    return state


def _upload(c, pdf):
    return c.post('/api/parse-xactimate', data={'file': (io.BytesIO(pdf), 'scan.pdf')},
                  content_type='multipart/form-data')


def test_a_scan_upload_starts_a_job_and_the_poll_collects_it_once(client, inline_scan):
    r = _upload(client, scanned_pdf())
    assert r.status_code == 202
    job = r.get_json()['scan_job']
    r = client.get(f'/api/parse-xactimate/scan/{job}')
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['reconcile']['ok'] and body['scanned']
    # the claim is deleted from the volume the moment it is collected
    assert client.get(f'/api/parse-xactimate/scan/{job}').status_code == 404


def test_a_scan_that_does_not_reconcile_is_kept_for_an_admin(client, inline_scan):
    import app as A
    inline_scan['raw'] = transcription(
        [l if l[0] != 16 else l[:7] + (3470.45,) + l[8:] for l in LM_LINES])
    before = len(A._carrier_failure_names())
    job = _upload(client, scanned_pdf(pages=3)).get_json()['scan_job']
    body = client.get(f'/api/parse-xactimate/scan/{job}').get_json()
    assert not body['reconcile']['ok'] and body['reconcile']['kept']
    assert len(A._carrier_failure_names()) == before + 1


def test_another_user_cannot_collect_someone_elses_scan(app, client, inline_scan):
    from portal import users as portal_users
    if not portal_users.get('scanrep'):
        portal_users.create('scanrep', password='test-only-password', role='rep')
    job = _upload(client, scanned_pdf()).get_json()['scan_job']
    other = app.test_client()
    with other.session_transaction() as s:
        s['user'] = 'scanrep'
    assert other.get(f'/api/parse-xactimate/scan/{job}').status_code == 404
    assert client.get(f'/api/parse-xactimate/scan/{job}').status_code == 200


def test_a_read_that_errors_ends_the_poll_with_a_message(client, inline_scan, monkeypatch):
    import carrier_scan as cs

    def boom(raw, client=None):
        raise cs.ScanError('The scan reader is unavailable right now.')
    monkeypatch.setattr(cs, 'read', boom)
    job = _upload(client, scanned_pdf()).get_json()['scan_job']
    r = client.get(f'/api/parse-xactimate/scan/{job}')
    assert r.status_code == 422 and 'unavailable' in r.get_json()['error']


def test_without_a_key_a_scan_still_gets_the_ask_for_the_real_pdf_message(client, monkeypatch):
    import carrier_scan as cs
    monkeypatch.setattr(cs, 'available', lambda: False)
    r = _upload(client, scanned_pdf())
    assert r.status_code == 422 and r.get_json()['scanned'] is True


def test_the_browser_waits_on_a_scan_job_rather_than_opening_an_empty_modal():
    import os
    js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'static', 'app.js'), encoding='utf-8').read()
    start = js.index('async function importXactPdf')
    body = js[start:js.index('\n}\n', start)]
    assert 'r.status === 202 && data.scan_job' in body
    assert 'waitForCarrierScan(' in body
    assert '/api/parse-xactimate/scan/' in js
