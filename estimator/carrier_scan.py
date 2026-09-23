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
import re

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

def _obj(props):
    return {'type': 'object', 'properties': props,
            'required': list(props), 'additionalProperties': False}


# EVERY figure is a string, and that is not laziness. The first version of this
# schema typed figures as numbers and made the optional ones `anyOf [number,
# null]`; the API refused the whole request with "the compiled grammar is too
# large", so no scan could be read at all. One type per field and "" for a
# figure the page does not print keeps the grammar small, and the parsing was
# needed anyway -- a carrier prints "$1,234.56" and "(514.73)".
_STR = {'type': 'string'}

SCHEMA = _obj({
    'format': {'type': 'string', 'enum': ['xactimate', 'symbility', 'other']},
    'meta': _obj({k: _STR for k in (
        'carrier', 'claim_number', 'policy_number', 'insured', 'date_of_loss',
        'type_of_loss', 'price_list', 'adjuster')}),
    'address': _obj({k: _STR for k in ('street', 'city', 'state', 'zip')}),
    'sections': {'type': 'array', 'items': _obj({
        'name': _STR,
        'items': {'type': 'array', 'items': _obj({
            'line_no': _STR,
            'description': _STR,
            'qty': _STR,
            'qty_calculated': _STR,
            'unit': _STR,
            'unit_price': _STR,
            'tax': _STR,
            'overhead_profit': _STR,
            'rcv': _STR,
            'depreciation': _STR,
            'nonrecoverable': _STR,
            'acv': _STR,
        })},
        'subtotal_rcv': _STR,
        'subtotal_depreciation': _STR,
        'subtotal_acv': _STR,
    })},
    'total_rcv': _STR,
    'total_depreciation': _STR,
    'total_acv': _STR,
    'summary': _obj({k: _STR for k in (
        'line_item_total', 'material_sales_tax', 'rcv_total', 'acv_total',
        'deductible', 'net_claim', 'recoverable_depreciation',
        'net_claim_if_recovered', 'paid_when_incurred')}),
    'measurements': _obj({k: _STR for k in (
        'roof_squares', 'eave_lf', 'ridge_lf')}),
    'unreadable': {'type': 'array', 'items': _STR},
    'missing_pages': {'type': 'array', 'items': _STR},
})

SYSTEM = """\
You transcribe scanned insurance carrier estimates (Xactimate or Symbility/Cotality exports) for a roofing contractor. The transcription is checked line by line against the carrier's own printed arithmetic, so it must be what is printed on the page, not what the figures ought to be.

Transcribe; never calculate or correct. If a figure disagrees with the rest of its line, keep it as printed: a mismatch is how a misread gets caught downstream. If a character is hard to read, give your best reading and name the line and field in `unreadable`.

EVERY field is a string. A figure is its digits with no currency symbol and no thousands separators ("2323.32", "21.39", "4"). **A figure the page does not print is an empty string** — never a guess, never "0" unless a zero is printed.

Line items:
- Include every numbered line that carries figures, including lines printed with a strikethrough; the carrier's subtotals still count them. Skip numbered notes that carry no figures.
- `description` is the full description, with wrapped lines joined. Leave out notes printed under a line ("Includes 8% waste on quantity.").
- Symbility prints a bundle-rounded quantity as "21.39 (21.67)": `qty` is the figure in parentheses and `qty_calculated` the one before it. When there is only one quantity, `qty_calculated` is "".
- Xactimate REMOVE and REPLACE columns: `unit_price` is their sum.
- `nonrecoverable` is "yes" when the depreciation is printed in <angle brackets>, otherwise "".

Sections are the grouping level the carrier prints a subtotal for: an Xactimate room or area; a Symbility plan (for example "ROOFPLAN: DWELLING ROOF"). When a Symbility plan nests areas inside it, the section is the plan and `subtotal_rcv`/`subtotal_depreciation`/`subtotal_acv` come from the plan's subtotal row, not the areas'. All three are "" when no subtotal is printed for that section.

`total_rcv`/`total_depreciation`/`total_acv` are the carrier's printed total of all line items: Xactimate's "Line Item Totals" row, or the last Symbility "Subtotal" row across the columns. If Symbility prints no such row but a single plan covers the estimate, use that plan's subtotal. "" when nothing like it is printed.

`summary` comes from the claim-totals page, as positive magnitudes even where a deduction is printed in parentheses. `acv_total` only when a plain actual cash value line is printed; not a "net" figure that has also deducted costs payable when incurred.

`measurements`: roof squares, eave and ridge lengths as printed in a roof plan's measurement block, summed across roof plans that carry line items.

`meta` and `address`: empty strings for anything not printed. The address is the loss/property address, not the insured's mailing address.

`missing_pages`: if page footers ("Page 5 of 7") show pages absent from the scan, say which. Otherwise empty.

`format` is "symbility" for the nine-column Description/Quantity/Unit Price/Per/Total O&P/Total Taxes/RC/Depreciation/ACV grid, "xactimate" for an Xactimate grid, otherwise "other".
"""


def _json_text(text):
    """The JSON object in a reply. With the schema on, the reply IS the object;
    the no-schema fallback can wrap it in a code fence or a sentence."""
    s = (text or '').strip()
    fence = re.search(r'```(?:json)?\s*(.+?)```', s, re.S)
    if fence:
        s = fence.group(1).strip()
    start, end = s.find('{'), s.rfind('}')
    return s[start:end + 1] if 0 <= start < end else s


def _request(pages):
    content = []
    for i, jpeg in enumerate(pages, 1):
        content.append({'type': 'text', 'text': f'Page {i} of {len(pages)} of the scan:'})
        content.append({'type': 'image', 'source': {
            'type': 'base64', 'media_type': 'image/jpeg',
            'data': base64.standard_b64encode(jpeg).decode('ascii')}})
    content.append({'type': 'text', 'text': 'Transcribe this carrier estimate.'})
    return [{'role': 'user', 'content': content}]


_JSON_ONLY = ('\n\nReply with one JSON object of exactly the fields described '
              'above and nothing else: no prose, no code fence.')


def _stream(client, pages, schema=True):
    kw = dict(model=MODEL, max_tokens=64000,
              betas=['server-side-fallback-2026-07-01'], fallbacks='default',
              system=SYSTEM if schema else SYSTEM + _JSON_ONLY,
              messages=_request(pages))
    if schema:
        kw['output_config'] = {'format': {'type': 'json_schema', 'schema': SCHEMA}}
    with client.beta.messages.stream(**kw) as stream:
        return stream.get_final_message()


def _call(client, pages):
    # The schema is what guarantees a parseable answer, but it is also a thing
    # the API can refuse on its own terms -- it rejected the first version of
    # this one outright ("the compiled grammar is too large"), and every scan
    # failed with it. So a 400 falls back to asking for the same JSON in the
    # prompt: a worse guarantee, and far better than no import at all.
    try:
        msg = _stream(client, pages)
    except Exception as e:
        # Keyed on the status, not an SDK class: 400 is the API saying this
        # request is malformed, and the schema is the only part of it that can
        # be. Anything else (401, 429, a network drop) is not ours to retry.
        if getattr(e, 'status_code', None) != 400:
            raise
        print(f'[carrier-scan] schema refused, retrying without it: {e}')
        msg = _stream(client, pages, schema=False)
    if msg.stop_reason == 'refusal':
        raise ScanError('The scan reader declined this document. Enter the lines by hand.')
    if msg.stop_reason == 'max_tokens':
        raise ScanError('This scan is too long to read in one pass. Enter the lines by hand.')
    text = next((b.text for b in msg.content if getattr(b, 'type', '') == 'text'), '')
    try:
        raw = json.loads(_json_text(text))
    except ValueError:
        raise ScanError('The scan reader returned something unreadable. Try again.')
    usage = getattr(msg, 'usage', None)
    if usage is not None:
        print(f'[carrier-scan] {len(pages)} pages, model={getattr(msg, "model", MODEL)} '
              f'in={getattr(usage, "input_tokens", "?")} out={getattr(usage, "output_tokens", "?")}')
    return raw


# ── into the parsers' shape ────────────────────────────────────────────────

def _num(v):
    """A transcribed figure → float, or None for one the page did not print.

    Tolerant on purpose: the schema asks for bare digits, but a carrier prints
    "$1,234.56" and "(514.73)", and a stray symbol must not lose a real figure.
    """
    s = str(v if v is not None else '').strip()
    if not s:
        return None
    neg = s.startswith('-') or s.lstrip('$').startswith('(')
    digits = re.sub(r'[^\d.]', '', s)
    if not digits or digits == '.':
        return None
    try:
        n = float(digits)
    except ValueError:
        return None
    return -n if neg else n


def _round(v, default=0.0):
    n = _num(v)
    return default if n is None else round(n, 2)


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
                'line_no':      int(_round(it.get('line_no'))),
                'description':  ' '.join(str(it.get('description') or '').split()),
                'qty':          _round(it.get('qty')),
                'unit':         str(it.get('unit') or '').strip(),
                'unit_price':   _round(it.get('unit_price')),
                'rcv':          _round(it.get('rcv')),
                'depreciation': _round(it.get('depreciation')),
                'acv':          _round(it.get('acv')),
                'tax':          _round(it.get('tax')),
                '_op':          _round(it.get('overhead_profit')),
            }
            nonrec = str(it.get('nonrecoverable') or '').strip().lower()
            if fmt == 'symbility':
                item['overhead_profit'] = item['_op']
                calc = _num(it.get('qty_calculated'))
                if calc is not None:
                    item['qty_calculated'] = calc
            else:
                item.update({'op': item['_op'], 'age_life': '', 'dep_pct': '',
                             'nonrecoverable': nonrec in ('yes', 'true', '1')})
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
        sub = [_num(sec.get('subtotal_' + k)) for k in ('rcv', 'depreciation', 'acv')]
        sections.append({
            'name': ' '.join(str(sec.get('name') or 'Estimate').split()) or 'Estimate',
            'items': items,
            # A subtotal needs its RCV; depreciation and ACV blank read as zero,
            # which is what a section with nothing depreciated prints.
            'totals': ({'rcv': round(sub[0], 2), 'dep': round(sub[1] or 0, 2),
                        'acv': round(sub[2] if sub[2] is not None else sub[0], 2)}
                       if sub[0] is not None else None),
        })

    summary = {}
    for k, v in (raw.get('summary') or {}).items():
        n = _num(v)
        if n is not None:
            summary[k] = round(n, 2)
    if 'recoverable_depreciation' in summary:
        summary.setdefault('depreciation_total', summary['recoverable_depreciation'])
    total = [_num(raw.get('total_' + k)) for k in ('rcv', 'depreciation', 'acv')]
    grand = None
    if total[0] is not None:
        grand = (round(total[0], 2), round(total[1] or 0, 2),
                 round(total[2] if total[2] is not None else total[0], 2))
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
    measurements = {}
    for k, v in (raw.get('measurements') or {}).items():
        n = _num(v)
        if n is not None:
            measurements[k] = round(n, 2)

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
