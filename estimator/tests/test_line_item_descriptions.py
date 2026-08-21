"""Every Pricing tab gets the same multi-line line-item description box.

Reps asked for one shape across all trades: a line item with a description
underneath it, where Enter starts a new line instead of doing nothing. That is
only true if the editor is a <textarea> — an <input type="text"> swallows Enter,
and every trade had one except the Simple tabs.

Two halves have to hold together or the feature is a lie:

  1. The EDITORS are textareas (this file's first half). A regression to
     <input> is invisible until a rep types Enter and nothing happens.
  2. The OUTPUT preserves the newline (second half). HTML collapses whitespace
     and fpdf2's cell() writes a raw newline into the PDF text string as a junk
     glyph, so every rendering path needs its own deliberate handling. A rep who
     types three lines and gets one run-on line on the customer's estimate will
     stop using the box.
"""
import os
import re
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import app as A


@pytest.fixture(scope='module')
def js():
    with open(os.path.join(BASE, 'static', 'app.js'), encoding='utf-8') as fh:
        return fh.read()


@pytest.fixture(scope='module')
def css():
    with open(os.path.join(BASE, 'static', 'style.css'), encoding='utf-8') as fh:
        return fh.read()


# ── 1. Every editor is a textarea ────────────────────────────────────────────

# class -> which Pricing tab it serves, for a readable failure message.
DESC_EDITORS = {
    'simple-item-desc': 'the Simple tabs + the Other tab',
    'li-row-desc-input': 'the Good / Better / Best grid',
    'ins-desc-input': 'the Insurance tab',
}


@pytest.mark.parametrize('cls,where', sorted(DESC_EDITORS.items()))
def test_description_editor_is_a_textarea(js, cls, where):
    """<input> swallows Enter. Only a textarea gives the rep a new line."""
    assert f'<input class="{cls}' not in js, (
        f'{where}: .{cls} went back to an <input> — Enter no longer starts a '
        f'new line in the description box.'
    )
    assert f'<textarea class="{cls}' in js, (
        f'{where}: no <textarea class="{cls}"> found in app.js.'
    )


@pytest.mark.parametrize('cls,where', sorted(DESC_EDITORS.items()))
def test_description_editor_grows_to_fit(js, cls, where):
    """A textarea that doesn't grow hides everything past line one behind a
    scrollbar, which is barely better than the old single-line input."""
    block = re.search(r'<textarea class="' + re.escape(cls) + r'[^>]*?>', js, re.S)
    assert block, f'{where}: could not find the .{cls} textarea tag'
    tag = block.group(0)
    assert 'desc-ta' in tag, (
        f'{where}: .{cls} is missing the desc-ta class, so the autoGrowAll() '
        f'pass after render skips it and a saved multi-line description opens '
        f'collapsed to one row.'
    )
    assert 'autoGrow(this)' in tag, (
        f'{where}: .{cls} has no autoGrow(this) on input — it will not grow as '
        f'the rep types.'
    )


@pytest.mark.parametrize('cls', sorted(DESC_EDITORS))
def test_description_editor_css_suits_a_textarea(css, cls):
    """resize/overflow have to be off or the drag handle and the scrollbar both
    fight autoGrow()'s height."""
    m = re.search(r'^\.' + re.escape(cls) + r'\s*\{(.*?)\}', css, re.S | re.M)
    assert m, f'.{cls} rule not found in style.css'
    body = m.group(1)
    for prop in ('resize: none', 'overflow: hidden', 'font-family: inherit'):
        assert prop in body, f'.{cls} is missing `{prop}` — see autoGrow()'


def test_the_tab_renderer_sizes_boxes_after_it_paints(js):
    """renderTradeContent() replaces the whole tab's innerHTML, which throws
    away every height autoGrow set. Without the pass afterwards, switching tabs
    silently collapses saved descriptions."""
    m = re.search(r'function renderTradeContent\(\).*?\n\}', js, re.S)
    assert m, 'renderTradeContent not found'
    assert 'autoGrowAll(host)' in m.group(0), (
        'renderTradeContent no longer calls autoGrowAll after setting innerHTML'
    )


# ── 2. The Other tab's legacy per-tier note ──────────────────────────────────

def test_other_tab_note_input_is_gone(js, css):
    """The Other tab's one-line "Note" was the odd one out — and it only ever
    reached the printed estimate, never the customer view. It is now the same
    description field as everywhere else."""
    assert 'other-note-input' not in js
    assert 'other-note-input' not in css


def test_other_tab_writes_description_to_every_tier(js):
    """Mirrors otherSetUnitCost: the tab shows one tier but writes all three,
    so the description prints on every package rather than only the one the rep
    happened to be looking at."""
    m = re.search(r'function otherSetDesc\(id, v\) \{.*?\n\}', js, re.S)
    assert m, 'otherSetDesc not found'
    body = m.group(0)
    assert 'TIERS.forEach' in body
    assert "item.tiers[tier].description = v" in body


def test_folding_a_legacy_note_never_drops_text(js):
    """The migration runs on estimates that already exist. Overwriting a
    non-empty description, or deleting the note without moving it, would lose
    something a rep typed."""
    m = re.search(r'function otherFoldNoteIntoDesc\(item\) \{.*?\n\}', js, re.S)
    assert m, 'otherFoldNoteIntoDesc not found'
    body = m.group(0)
    # empty description -> take the note; non-empty -> append, never clobber
    assert "t.description = note" in body
    assert "desc + '\\n' + note" in body


# ── 3. The newline survives to every output ──────────────────────────────────

def test_customer_view_preserves_description_newlines():
    """HTML collapses newlines. Both description cells on the customer/sign
    page need pre-wrap: .cvd is the trade tables, .cvc-desc the insurance one."""
    for cls in ('.cvd', '.cvc-desc'):
        m = re.search(re.escape(cls) + r'\{([^}]*)\}', A._CV_CSS)
        assert m, f'{cls} rule not found in _CV_CSS'
        assert 'white-space:pre-wrap' in m.group(1), (
            f'{cls} lost white-space:pre-wrap — multi-line line-item '
            f'descriptions render as one run-on line to the customer.'
        )


def test_browser_print_turns_newlines_into_breaks(js):
    """The print builder emits HTML, so it has to convert explicitly.

    Two of the three live in printTradeBody — the per-package trade table, which
    buildPrintContent calls once per offered tier. Both functions are checked so
    splitting the builder again can't quietly drop a conversion."""
    body = ''
    for name in ('buildPrintContent\\(\\)', 'printTradeBody\\(trade, tier, o\\)'):
        m = re.search(r'function ' + name + r'.*?\n\}', js, re.S)
        assert m, f'{name} not found'
        body += m.group(0)
    # trade line items (description + notes), and the insurance table's own
    # Description column
    assert body.count(r"replace(/\n/g,'<br>')") >= 3, (
        'a print path stopped converting description newlines to <br>'
    )


def test_pdf_cells_flatten_newlines():
    """fpdf2's cell() does not wrap — it writes the newline straight into the
    PDF text string, where it renders as a junk glyph. One space is the honest
    single-line rendering."""
    assert A._pdf_oneline('line one\nline two') == 'line one line two'
    assert A._pdf_oneline('a\r\n\r\nb') == 'a b'
    assert A._pdf_oneline('  padded \n  out  ') == 'padded out'
    assert A._pdf_oneline(None) == ''
    # still does _pdf_safe's job — core fonts are latin-1 only
    assert A._pdf_oneline('sixteen—inch') == 'sixteen-inch'


def test_multi_cell_paths_keep_their_newlines():
    """_pdf_oneline is for cell() only. Scope notes and the contract body go
    through multi_cell, which wraps properly and wants the breaks — so
    _pdf_safe must stay newline-preserving."""
    assert A._pdf_safe('para one\n\npara two') == 'para one\n\npara two'


def test_every_single_line_pdf_cell_uses_the_flattening_trunc():
    """The core-font PDF builders each define their own local trunc(). All of
    them feed pdf.cell(), so all of them must flatten — one that slips back to
    _pdf_safe reintroduces the junk glyph in that document only.

    build_signed_pdf is deliberately NOT in this set: it draws its line items
    with pdf.table(), which wraps long descriptions instead of clipping them at
    a character count, so it has no trunc() to guard. Adding one back there
    would be a regression, not a fix.

    The count tracks the internal builders that still hand-place cells: the
    work order, the material order and the change order."""
    with open(os.path.join(BASE, 'app.py'), encoding='utf-8') as fh:
        src = fh.read()
    truncs = re.findall(r'    def trunc\(s, n\):\n(.*?)\n\n', src, re.S)
    assert len(truncs) == 3, f'expected 3 local trunc() helpers, found {len(truncs)}'
    for body in truncs:
        # Either flattener is fine — _pdf_oneline_rich is the same collapse for
        # the documents that embed Unicode faces. What must not come back is a
        # bare _pdf_safe, which leaves the newline in place for pdf.cell().
        assert ('_pdf_oneline(s)' in body or '_pdf_oneline_rich(s)' in body), (
            'a local trunc() no longer flattens newlines before pdf.cell()'
        )
