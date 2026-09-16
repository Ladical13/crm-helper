"""Read a SCANNED carrier estimate -- one with no text layer -- with Claude.

The Xactimate and Symbility parsers in app.py read text, so a printed estimate
run back through a scanner gave them nothing at all. Reps get exactly that when
a homeowner hands over the paper copy. This module turns the page images into
the same shape those parsers return, so the review modal, the reconcile check
and the import all work unchanged.

A model reading pixels can misread a digit, so nothing here is trusted on its
own say-so. Three checks decide whether a read looks clean, and all three use
the carrier's own printed arithmetic rather than anything the model computed:

  - every line's RCV = ACV + depreciation      (app._carrier_reconcile)
  - every line's RCV = qty x unit price + tax + O&P   (`math_off`, below)
  - the lines add up to the carrier's printed total   (app._carrier_reconcile)

The model is told to transcribe, never to calculate, so a misread shows up as
a line that fails its own arithmetic instead of being quietly "corrected" to
agree. The second check is what catches a misread unit price or quantity,
which the first and third cannot see.
"""
import base64
import json
import os

try:
    import anthropic
except ImportError:          # the parsers still work; scans just say so
    anthropic = None

try:
    import fitz              # pymupdf -- already required for RoofR pages
except ImportError:
    fitz = None


MODEL = os.environ.get('CARRIER_SCAN_MODEL', 'claude-opus-5')
# A carrier estimate is a handful of pages; the cap bounds cost and latency for
# a file that turns out to be a 60-page policy packet.
MAX_PAGES = 30
# Long edge of each page image. Enough for 9pt figures on a letter page to read
# cleanly without paying for resolution the model will not use.
PAGE_LONG_EDGE = 2000
# A line's own arithmetic is allowed this much slack for cent rounding across a
# two-decimal quantity times a two-decimal price, and no more.
MATH_TOLERANCE_ABS = 1.00
MATH_TOLERANCE_PCT = 0.002


class ScanError(Exception):
    """A scan that could not be read. The message is shown to the rep."""


def available():
    return (anthropic is not None and fitz is not None
            and bool(os.environ.get('ANTHROPIC_API_KEY', '').strip()))


def render_pages(file_bytes):
    """JPEG bytes per page, grayscale, long edge PAGE_LONG_EDGE."""
    if fitz is None:
        raise ScanError('pymupdf is not installed, so a scanned PDF cannot be read.')
    doc = fitz.open(stream=file_bytes, filetype='pdf')
    out = []
    try:
        for page in list(doc)[:MAX_PAGES]:
            r = page.rect
            zoom = PAGE_LONG_EDGE / max(r.width, r.height)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom),
                                  colorspace=fitz.csGRAY, alpha=False)
            out.append(pix.tobytes('jpeg', jpg_quality=85))
    finally:
        doc.close()
    return out


# ── what the model is asked for ────────────────────────────────────────────

def _nullable(schema):
    return {'anyOf': [schema, {'type': 'null'}]}


def _obj(props):
    return {'type': 'object', 'properties': props,
            'required': list(props), 'additionalProperties': False}


_NUM = {'type': 'number'}
_STR = {'type': 'string'}
_TOTALS = _nullable(_obj({'rcv': _NUM, 'depreciation': _NUM, 'acv': _NUM}))

SCHEMA = _obj({
    'format': {'type': 'string', 'enum': ['xactimate', 'symbility', 'other']},
    'meta': _obj({k: _STR for k in (
        'carrier', 'claim_number', 'policy_number', 'insured', 'date_of_loss',
        'type_of_loss', 'price_list', 'adjuster')}),
    'address': _obj({k: _STR for k in ('street', 'city', 'state', 'zip')}),
    'sections': {'type': 'array', 'items': _obj({
        'name': _STR,
        'items': {'type': 'array', 'items': _obj({
            'line_no': {'type': 'integer'},
            'description': _STR,
            'qty': _NUM,
            'qty_calculated': _nullable(_NUM),
            'unit': _STR,
            'unit_price': _NUM,
            'tax': _NUM,
            'overhead_profit': _NUM,
            'rcv': _NUM,
            'depreciation': _NUM,
            'nonrecoverable': {'type': 'boolean'},
            'acv': _NUM,
        })},
        'subtotal': _TOTALS,
    })},
    'line_item_totals': _TOTALS,
    'summary': _obj({k: _nullable(_NUM) for k in (
        'line_item_total', 'material_sales_tax', 'rcv_total', 'acv_total',
        'deductible', 'net_claim', 'recoverable_depreciation',
        'net_claim_if_recovered', 'paid_when_incurred')}),
    'measurements': _obj({k: _nullable(_NUM) for k in (
        'roof_squares', 'eave_lf', 'ridge_lf')}),
    'unreadable': {'type': 'array', 'items': _STR},
    'missing_pages': {'type': 'array', 'items': _STR},
})

SYSTEM = """\
You transcribe scanned insurance carrier estimates (Xactimate or Symbility/Cotality exports) for a roofing contractor. The transcription is checked line by line against the carrier's own printed arithmetic, so it must be what is printed on the page, not what the figures ought to be.

Transcribe; never calculate or correct. If a figure disagrees with the rest of its line, keep it as printed: a mismatch is how a misread gets caught downstream. If a character is hard to read, give your best reading and name the line and field in `unreadable`.

Line items:
- Include every numbered line that carries figures, including lines printed with a strikethrough; the carrier's subtotals still count them. Skip numbered notes that carry no figures.
- `description` is the full description, with wrapped lines joined. Leave out notes printed under a line ("Includes 8% waste on quantity.").
- Symbility prints a bundle-rounded quantity as "21.39 (21.67)": `qty` is the figure in parentheses and `qty_calculated` the one before it. Otherwise `qty_calculated` is null.
- Xactimate REMOVE and REPLACE columns: `unit_price` is their sum. Columns a layout does not print (tax, O&P, depreciation) are 0.
- Depreciation printed in <angle brackets> is non-recoverable: `nonrecoverable` true. Depreciation in (parentheses) is recoverable: false.
- Money is a positive number with no symbols or commas.

Sections are the grouping level the carrier prints a subtotal for: an Xactimate room or area; a Symbility plan (for example "ROOFPLAN: DWELLING ROOF"). When a Symbility plan nests areas inside it, the section is the plan and `subtotal` is the plan's subtotal row, not the areas'. `subtotal` is null when none is printed.

`line_item_totals` is the carrier's printed total of all line items: Xactimate's "Line Item Totals" row, or the last Symbility "Subtotal" row across the columns. If Symbility prints no such row but a single plan covers the estimate, use that plan's subtotal. Null when nothing like it is printed.

`summary` comes from the claim-totals page, as positive magnitudes even where a deduction is printed in parentheses. Null for any figure not printed. `acv_total` only when a plain actual cash value line is printed; not a "net" figure that has also deducted costs payable when incurred.

`measurements`: roof squares, eave and ridge lengths as printed in a roof plan's measurement block, summed across roof plans that carry line items. Null when not printed.

`meta` and `address`: empty strings for anything not printed. The address is the loss/property address, not the insured's mailing address.

`missing_pages`: if page footers ("Page 5 of 7") show pages absent from the scan, say which. Otherwise empty.

`format` is "symbility" for the nine-column Description/Quantity/Unit Price/Per/Total O&P/Total Taxes/RC/Depreciation/ACV grid, "xactimate" for an Xactimate grid, otherwise "other".
"""


def _request(pages):
    content = []
    for i, jpeg in enumerate(pages, 1):
        content.append({'type': 'text', 'text': f'Page {i} of {len(pages)} of the scan:'})
        content.append({'type': 'image', 'source': {
            'type': 'base64', 'media_type': 'image/jpeg',
            'data': base64.standard_b64encode(jpeg).decode('ascii')}})
    content.append({'type': 'text', 'text': 'Transcribe this carrier estimate.'})
    return [{'role': 'user', 'content': content}]


def _call(client, pages):
    with client.beta.messages.stream(
            model=MODEL,
            max_tokens=64000,
            betas=['server-side-fallback-2026-07-01'],
            fallbacks='default',
            system=SYSTEM,
            messages=_request(pages),
            output_config={'format': {'type': 'json_schema', 'schema': SCHEMA}},
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == 'refusal':
        raise ScanError('The scan reader declined this document. Enter the lines by hand.')
    if msg.stop_reason == 'max_tokens':
        raise ScanError('This scan is too long to read in one pass. Enter the lines by hand.')
    text = next((b.text for b in msg.content if getattr(b, 'type', '') == 'text'), '')
    try:
        raw = json.loads(text)
    except ValueError:
        raise ScanError('The scan reader returned something unreadable. Try again.')
    usage = getattr(msg, 'usage', None)
    if usage is not None:
        print(f'[carrier-scan] {len(pages)} pages, model={getattr(msg, "model", MODEL)} '
              f'in={getattr(usage, "input_tokens", "?")} out={getattr(usage, "output_tokens", "?")}')
    return raw


# ── into the parsers' shape ────────────────────────────────────────────────

def _round(v):
    return round(float(v or 0), 2)


def _line_math_off(item):
    """Whether a line fails qty x unit price + tax + O&P = RCV."""
    expect = item['qty'] * item['unit_price'] + item['tax'] + item['_op']
    slack = max(MATH_TOLERANCE_ABS, abs(item['rcv']) * MATH_TOLERANCE_PCT)
    return abs(expect - item['rcv']) > slack


def normalize(raw):
    """The model's transcription → the dict _parse_symbility_pdf and
    _parse_xactimate_pdf return, plus `scanned`."""
    fmt = 'symbility' if raw.get('format') == 'symbility' else 'xactimate'
    warnings = ['Read from a scanned copy: every figure was read off an image. '
                'Check each line against the paper before loading.']
    sections = []
    for sec in raw.get('sections') or []:
        items = []
        for it in sec.get('items') or []:
            item = {
                'line_no':      int(it.get('line_no') or 0),
                'description':  ' '.join(str(it.get('description') or '').split()),
                'qty':          float(it.get('qty') or 0),
                'unit':         str(it.get('unit') or '').strip(),
                'unit_price':   _round(it.get('unit_price')),
                'rcv':          _round(it.get('rcv')),
                'depreciation': _round(it.get('depreciation')),
                'acv':          _round(it.get('acv')),
                'tax':          _round(it.get('tax')),
                '_op':          _round(it.get('overhead_profit')),
            }
            if fmt == 'symbility':
                item['overhead_profit'] = item['_op']
                if it.get('qty_calculated') is not None:
                    item['qty_calculated'] = float(it['qty_calculated'])
            else:
                item.update({'op': item['_op'], 'age_life': '', 'dep_pct': '',
                             'nonrecoverable': bool(it.get('nonrecoverable'))})
            if _line_math_off(item):
                item['math_off'] = True
                warnings.append(
                    f"Line {item['line_no']}: {item['qty']:g} × ${item['unit_price']:,.2f} "
                    f"+ tax + O&P does not come to its RCV ${item['rcv']:,.2f}. A figure on "
                    f"this line may be misread. Check it against the paper.")
            del item['_op']
            items.append(item)
        if not items:
            continue
        sub = sec.get('subtotal')
        sections.append({
            'name': ' '.join(str(sec.get('name') or 'Estimate').split()) or 'Estimate',
            'items': items,
            'totals': ({'rcv': _round(sub['rcv']), 'dep': _round(sub['depreciation']),
                        'acv': _round(sub['acv'])} if sub else None),
        })

    summary = {k: _round(v) for k, v in (raw.get('summary') or {}).items()
               if v is not None}
    if 'recoverable_depreciation' in summary:
        summary.setdefault('depreciation_total', summary['recoverable_depreciation'])
    grand = raw.get('line_item_totals')
    if grand:
        grand = (_round(grand['rcv']), _round(grand['depreciation']), _round(grand['acv']))
    elif sections and all(s['totals'] for s in sections):
        # Same rule the Symbility parser follows: section subtotals stand in
        # for a missing grand total only when every section printed one.
        grand = tuple(round(sum(s['totals'][k] for s in sections), 2)
                      for k in ('rcv', 'dep', 'acv'))
    if grand:
        summary['line_items_rcv'], summary['line_items_depreciation'], \
            summary['line_items_acv'] = grand

    for u in raw.get('unreadable') or []:
        warnings.append(f'Hard to read on the scan: {u}')
    missing = [m for m in (raw.get('missing_pages') or []) if str(m).strip()]
    if missing:
        warnings.append('The page footers suggest pages missing from this scan: '
                        + '; '.join(map(str, missing)) + '.')

    meta = {k: str(v).strip() for k, v in (raw.get('meta') or {}).items()
            if str(v or '').strip()}
    addr = {k: str(v).strip() for k, v in (raw.get('address') or {}).items()}
    if addr.get('city'):
        addr['city'] = addr['city'].title()
    if not (addr.get('street') and addr.get('city')):
        addr = {}
    measurements = {k: _round(v) for k, v in (raw.get('measurements') or {}).items()
                    if v is not None}

    out = {'format': fmt, 'scanned': True, 'meta': meta, 'address': addr,
           'sections': sections, 'summary': summary, 'warnings': warnings,
           'layout': {'header': '', 'columns': [], 'unknown_headers': []}}
    if fmt == 'symbility':
        out['measurements'] = measurements
    return out


def read(file_bytes, client=None):
    """Scanned carrier PDF → parser-shaped dict. Raises ScanError."""
    pages = render_pages(file_bytes)
    if not pages:
        raise ScanError('This PDF has no pages to read.')
    if client is None:
        if not available():
            raise ScanError('Scanned estimates cannot be read on this server yet.')
        client = anthropic.Anthropic()
    try:
        raw = _call(client, pages)
    except ScanError:
        raise
    except Exception as e:
        if anthropic is not None and isinstance(e, anthropic.APIError):
            print(f'[carrier-scan] API error: {e}')
            raise ScanError('The scan reader is unavailable right now. Try again in a '
                            'few minutes, or enter the lines by hand.')
        raise
    return normalize(raw)
