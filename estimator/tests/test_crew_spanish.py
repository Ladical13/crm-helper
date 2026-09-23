"""The work order, in Spanish beside the English.

The crews building these roofs are substantially Spanish-speaking and this
sheet has always been English-only. That matters most at exactly the place the
document is designed to catch an error: the ventilation block prints installed
square inches against required and says SHORT by N, a number put there so a
wrong calculation fails in front of whoever is on the roof rather than
silently in a test. In a language the crew does not read, it fails silently
anyway.

Three things these tests hold down:

- **The labels are a static table, not a model call.** Free, offline, instant,
  and identical on two printings of the same sheet.
- **The table cannot drift from the builder.** A label added to the work order
  and not to `LABELS` fails here, rather than printing in English forever with
  nobody noticing.
- **A figure never changes in translation.** The crew builds to these numbers.
  Nobody in this office reads Spanish well enough to catch a changed quantity,
  so the check is automated and a failure prints English only.
"""
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import app as A                  # noqa: E402
import crew_spanish as C         # noqa: E402

APP_SRC = open(os.path.join(HERE, '..', 'app.py'), encoding='utf-8').read()
WO_SRC = APP_SRC[APP_SRC.index('def build_work_order_pdf'):]
WO_SRC = WO_SRC[:WO_SRC.index('\ndef ', 10)]


# ── The table cannot drift from the document ────────────────────────────────

def test_every_label_the_work_order_asks_for_is_in_the_table():
    """The guard that makes a static table safe.

    Without it, a label added to the builder prints in English on every work
    order forever and the only way anyone finds out is a crew asking.
    """
    asked = set(re.findall(r"\bL\(\s*'([^']+)'\s*\)", WO_SRC))
    asked |= set(re.findall(r'\bL\(\s*"([^"]+)"\s*\)', WO_SRC))
    assert asked, 'no L() calls found — the work order stopped asking for labels'
    missing = sorted(a for a in asked if not C.has_label(a))
    assert not missing, f'work order labels with no Spanish: {missing}'


def test_every_job_details_row_label_is_in_the_table():
    """`sub_es` prints the Spanish under each of these, so a missing one is a
    row that is bilingual everywhere except the word that names it."""
    rows = set(re.findall(r"detail_rows\.append\(\(\s*'([^']+)'", WO_SRC))
    assert rows, 'detail_rows stopped being built the way this test reads it'
    missing = sorted(r for r in rows if not C.has_label(r))
    assert not missing, f'Job Details labels with no Spanish: {missing}'


def test_the_table_has_no_blank_translations():
    for en, es in C.LABELS.items():
        assert es and es.strip(), f'{en!r} has an empty translation'


# ── English stays first, and stays the authority ────────────────────────────

def test_english_comes_first_in_every_label():
    """It is what the contract, the inspector and the office speak, and a
    translation nobody in the office can check is one nobody should trust."""
    for en in C.LABELS:
        out = C.label(en)
        assert out.startswith(en), f'{en!r} rendered as {out!r}'


def test_a_word_that_is_the_same_in_both_languages_prints_once():
    """'NO / NO' is noise, and noise on a work order teaches a crew to skim."""
    assert C.label('NO') == 'NO'
    assert C.label('YES') == 'YES / SÍ'


def test_an_unknown_string_passes_through_untouched():
    """A missing translation costs that one line, never the document."""
    assert C.label('Some Brand New Row') == 'Some Brand New Row'


# ── The number is never translated ──────────────────────────────────────────

def test_the_ventilation_verdict_carries_its_figure_across_by_formatting():
    """The single most important sentence on the sheet. Built by formatting
    rather than translation, so no model is ever in a position to alter it."""
    out = C.state_line('SHORT by 420 sq in')
    assert out.startswith('SHORT by 420 sq in')
    assert '420' in out.split('/', 1)[1]


def test_a_decimal_shortfall_survives_too():
    assert '1,250' in C.state_line('SHORT by 1,250 sq in').split('/', 1)[1]


def test_numbers_survived_catches_a_changed_quantity():
    en = 'Tear off 2 layers. 30 SQ total. Ridge 48 LF.'
    assert C.numbers_survived(en, 'Remover 2 capas. 30 SQ en total. Cumbrera 48 LF.')
    assert not C.numbers_survived(en, 'Remover 2 capas. 3 SQ en total. Cumbrera 48 LF.')
    assert not C.numbers_survived(en, 'Remover 2 capas. 30 SQ en total.')


def test_numbers_survived_counts_repeats():
    """A note saying 2 layers on 2 slopes must not come back saying it once."""
    assert not C.numbers_survived('2 layers, 2 slopes', 'dos: 2')
    assert C.numbers_survived('2 layers, 2 slopes', '2 capas, 2 aguas')


def test_trailing_punctuation_is_not_a_changed_number():
    assert C.numbers_survived('Ridge is 48 LF.', 'La cumbrera es 48 LF')


def test_the_prompt_forbids_changing_a_figure():
    src = open(os.path.join(HERE, '..', 'crew_spanish.py'), encoding='utf-8').read()
    assert 'NEVER change a number' in src
    assert 'brand name' in src, 'product names must survive translation too'


# ── Nothing about this can break a work order ───────────────────────────────

def test_a_work_order_builds_with_no_api_key(monkeypatch, tmp_path):
    """Most of the sheet is labels, so the bulk of the value costs nothing and
    works offline. A translation outage must never cost the crew the sheet."""
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    pdf = A.build_work_order_pdf(_signed_est())
    assert pdf and len(pdf) > 1000


def test_a_failed_translation_returns_empty_rather_than_raising(monkeypatch):
    monkeypatch.setattr(C, 'translate_notes',
                        lambda *a, **k: (_ for _ in ()).throw(C.TranslateError('down')))
    assert A._work_order_notes_es('Tear off 2 layers.') == ''


def test_a_translation_that_changed_a_figure_is_refused(monkeypatch):
    """Printing English only is the correct outcome. A crew builds to whatever
    the sheet says, and a wrong number is worse than no Spanish."""
    monkeypatch.setattr(C, 'translate_notes', lambda *a, **k: 'Remover 3 capas.')
    assert A._work_order_notes_es('Tear off 2 layers.') == ''


def test_a_good_translation_is_kept(monkeypatch):
    monkeypatch.setattr(C, 'translate_notes', lambda *a, **k: 'Remover 2 capas.')
    A._WO_NOTES_ES.clear()
    assert A._work_order_notes_es('Tear off 2 layers.') == 'Remover 2 capas.'


def test_the_same_note_is_not_translated_twice(monkeypatch):
    """Two copies of one work order must not carry two different Spanish
    paragraphs — and paying twice for that would be the lesser problem."""
    calls = []
    def once(text, client=None):
        calls.append(text)
        return 'Remover 2 capas.'
    monkeypatch.setattr(C, 'translate_notes', once)
    A._WO_NOTES_ES.clear()
    A._work_order_notes_es('Tear off 2 layers.')
    A._work_order_notes_es('Tear off 2 layers.')
    assert len(calls) == 1


def test_an_edited_note_is_translated_again(monkeypatch):
    calls = []
    monkeypatch.setattr(C, 'translate_notes',
                        lambda t, client=None: (calls.append(t), 'es')[1])
    A._WO_NOTES_ES.clear()
    A._work_order_notes_es('Tear off 2 layers.')
    A._work_order_notes_es('Tear off 3 layers.')
    assert len(calls) == 2


def test_an_empty_note_never_reaches_the_model(monkeypatch):
    monkeypatch.setattr(C, 'translate_notes',
                        lambda *a, **k: pytest.fail('called for an empty note'))
    assert A._work_order_notes_es('') == ''
    assert A._work_order_notes_es('   ') == ''


# ── The setting ─────────────────────────────────────────────────────────────

def test_bilingual_is_on_by_default(monkeypatch):
    monkeypatch.setattr(A, '_app_settings', lambda: {})
    assert A._bilingual_work_order() is True


def test_bilingual_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(A, '_app_settings', lambda: {'work_order_bilingual': False})
    assert A._bilingual_work_order() is False


def test_switching_it_off_leaves_the_english_document_unchanged(monkeypatch):
    """The English is the document. Spanish is additive, and turning it off has
    to give back exactly what the crew got before this existed."""
    est = _signed_est()
    monkeypatch.setattr(A, '_app_settings', lambda: {'work_order_bilingual': False})
    off = _pdf_text(A.build_work_order_pdf(est))
    assert 'Detalles del Trabajo' not in off
    assert 'Job Details' in off

    monkeypatch.setattr(A, '_app_settings', lambda: {})
    on = _pdf_text(A.build_work_order_pdf(est))
    assert 'Job Details / Detalles del Trabajo' in on


# ── Helpers ─────────────────────────────────────────────────────────────────

def _signed_est():
    return {
        'estimate_id': 'wo-test', 'estimate_type': 'retail', 'salesperson': 'luke',
        'customer': {'name': 'Jennifer Ruiz', 'phone': '970-555-0134',
                     'address': {'street': '1420 Oak St', 'city': 'Loveland',
                                 'state': 'CO', 'zip': '80537'}},
        'signature': {'name': 'J Ruiz', 'signed_at': '2026-09-19T12:00:00Z',
                      'selected_tier': 'better'},
        'pricing': {'mode': 'margin'},
        'measurements': {'roof_squares': 30, 'attic_sqft': 3000,
                         'predominant_pitch': 6, 'turtle_vents': 6},
        'notes_internal': 'Tear off 2 layers. Watch the power line.',
        'trades': {'roofing': {
            'enabled': True, 'mode': 'simple',
            'line_items': [{'name': 'Roof', 'quantity': 1,
                            'unit_price': 20000.0, 'unit_cost': 12000.0}],
        }},
    }


def _pdf_text(raw):
    fitz = pytest.importorskip('fitz')
    import io
    doc = fitz.open(stream=io.BytesIO(raw), filetype='pdf')
    return '\n'.join(p.get_text() for p in doc)


# ── RoofR sits beside the carrier import ────────────────────────────────────
#
# Not a translation matter, but the same shape of problem: an insurance job
# needs two documents and they lived in different places. The carrier PDF had a
# button on the Insurance tab; the measurement report was three levels into the
# ⋮ menu. Without the measurements the cost side is sized off nothing, the
# margin is unknowable, and the Claim Check that finds a supplement has nothing
# to compare against — and Xactimate exports carry no measurements at all.

APP_JS = open(os.path.join(HERE, '..', 'static', 'app.js'), encoding='utf-8').read()


def test_roofr_import_sits_beside_every_carrier_import_button():
    """Both halves of one task, in one place, on both screens that offer it."""
    sites = [m.start() for m in re.finditer(r"xact-pdf-input'\)\.click\(\)", APP_JS)]
    assert len(sites) >= 2, 'the carrier import buttons moved'
    for i in sites:
        window = APP_JS[i:i + 400]
        assert 'roofrImportBtn' in window, \
            'a carrier import button with no RoofR button beside it'


def test_one_builder_serves_both_sites():
    """Two copies would drift, and the one that drifted would be the one on the
    screen a rep actually uses."""
    assert APP_JS.count('function roofrImportBtn') == 1


def test_the_button_says_whether_the_report_is_already_in():
    """"Do I still need to do this?" is the only question a rep has when they
    look at it."""
    fn = APP_JS[APP_JS.index('function roofrImportBtn'):]
    fn = fn[:fn.index('/* ── The $0 guard')]
    assert 'roof_squares' in fn
    assert 'SQ measured' in fn
    assert 'Import RoofR Measurements' in fn
