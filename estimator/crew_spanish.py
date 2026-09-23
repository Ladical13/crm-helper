"""The work order, in Spanish, beside the English.

The crews that build these roofs in Northern Colorado are substantially
Spanish-speaking and every document they work from is English-only. That is not
a comfort problem. `_vent_nfa_report` prints installed square inches against
required, per side, and says SHORT by N — a number put on the sheet
specifically so that a wrong calculation fails in front of whoever is on the
roof rather than silently in a test. If the sentence around that number is in a
language the crew does not read, it fails silently after all.

**Both languages print, English first.** English stays the authority: it is
what the contract, the inspector and the office speak, and a translation that
nobody can check is a translation nobody should trust. Side by side, a bad line
is visible to anyone who glances at the sheet.

Two kinds of text on that document, handled two completely different ways:

- **Fixed labels and headings** — "Job Details", "Shingle Color", "Exhaust
  NFA", "meets code", "AS INSTALLED - FILL IN ON SITE". A finite, closed set
  that changes only when someone edits the PDF builder. These live in `LABELS`
  below: a static table, free, offline, instant, reviewable in a diff, and
  incapable of drifting between two printings of the same sheet. Asking a model
  to translate the word "Customer" on every PDF build would be paying for
  latency and variance to get a worse answer.
- **The rep's free-text crew notes** — arbitrary, per job, and the only part of
  the sheet that cannot be known in advance. That is where a model earns its
  place, and it is the only thing in here that makes a network call.

Two rules on the translated notes, and the first one is the important one:

**Numbers, dimensions and product names pass through untouched.** A translation
that changed a quantity would be the worst failure this document can have —
worse than no translation, because the crew would build to it. The labels can
never touch a number by construction (they are labels). The notes prompt
forbids it, and `test_crew_spanish.py` checks a note full of figures comes back
carrying every one of them.

**No API key means English-only notes, and the labels still translate.** Most of
the sheet is labels, so the bulk of the value costs nothing and works offline.
"""
import os
import re

try:
    import anthropic
except ImportError:
    anthropic = None


MODEL = os.environ.get('CREW_SPANISH_MODEL', 'claude-opus-5')


class TranslateError(Exception):
    """A note that could not be translated. Never fatal — the English prints."""


def available():
    return anthropic is not None and bool(os.environ.get('ANTHROPIC_API_KEY', '').strip())


# Every fixed string the work order prints. Add a row here when you add a
# label to build_work_order_pdf; `test_crew_spanish.py` fails on a label the
# builder prints that this table has never heard of, so the two cannot drift.
LABELS = {
    # Headings
    'Work Order':            'Orden de Trabajo',
    'Job Details':           'Detalles del Trabajo',
    'Notes':                 'Notas',
    'Ventilation':           'Ventilación',
    'Ventilation Layout':    'Diseño de Ventilación',

    # Job card
    'Customer':              'Cliente',
    'Phone':                 'Teléfono',
    'Job Address':           'Dirección del Trabajo',
    'Job #':                 'Trabajo N.º',
    'Estimate #':            'Estimado N.º',
    'Salesperson':           'Vendedor',
    'Signed':                'Firmado',
    'Package':               'Paquete',
    'Shingle Color':         'Color de Teja',
    'Siding Color':          'Color de Revestimiento',

    # Job details — these are the exact strings build_work_order_pdf appends to
    # detail_rows. `test_crew_spanish.py` reads them out of app.py and fails on
    # any this table has never heard of, so the two cannot drift apart.
    'Squares to Install':    'Cuadros a Instalar',
    'Scheduled Date':        'Fecha Programada',
    'Tear-off Layers':       'Capas a Remover',
    'Steep Charge':          'Cargo por Inclinación',
    'Height / Access':       'Altura / Acceso',
    'Hand Load':             'Carga a Mano',
    'Satellite Dish':        'Antena Parabólica',
    'Ridge Vent':            'Ventilación de Cumbrera',
    'YES - see the Ventilation Layout page':
        'SÍ - ver la página de Diseño de Ventilación',
    'Intake Vent':           'Ventilación de Entrada',
    'Ridge vent':            'Ventilación de Cumbrera',
    'Intake vent':           'Ventilación de Entrada',
    'Cut in for code':       'Corte según el Código',
    'Intake for code':       'Entrada según el Código',
    'Exhaust NFA':           'NFA de Salida',
    'Intake NFA':            'NFA de Entrada',

    # Values and states
    'YES':                   'SÍ',
    'NO':                    'NO',
    'None - walkable':       'Ninguno - transitable',
    'meets code':            'cumple con el código',
    'NOT on this job':       'NO en este trabajo',
    'NOT CALCULATED':        'NO CALCULADO',
    'installed':             'instalado',
    'required':              'requerido',
    'sq in':                 'pulg²',

    # Stamps
    'CREW ONLY - INTERNAL':  'SOLO PARA LA CUADRILLA - INTERNO',
    'AS INSTALLED - FILL IN ON SITE':
        'TAL COMO SE INSTALÓ - LLENAR EN EL SITIO',
}


def label(en):
    """'English / Español' for a known label, or the English unchanged.

    An unknown string comes back untouched rather than raising: a missing
    translation should cost the reader that one line, never the document. The
    drift test is what stops a label going missing quietly.

    A word that is the same in both languages prints once. 'NO / NO' is noise,
    and noise on a work order is what teaches a crew to skim it.
    """
    es = LABELS.get(en)
    if not es or es == en:
        return en
    return f'{en} / {es}'


def has_label(en):
    return en in LABELS


_SHORT_RE = re.compile(r'^SHORT by ([\d.,]+) sq in$')


def state_line(en):
    """The ventilation verdict, which is a label with a number wedged in it.

    'SHORT by 420 sq in' is generated, not fixed, so it cannot live in LABELS —
    and it is the single most important sentence on the sheet. Built by
    formatting rather than by translation so the figure is carried across
    literally, by construction, and no model is ever in a position to alter it.
    """
    m = _SHORT_RE.match(en.strip())
    if m:
        return f'{en} / FALTAN {m.group(1)} pulg²'
    return label(en)


SYSTEM = """You translate short internal notes on a roofing work order from
English into Mexican Spanish, for the crew who will build the roof.

Absolute rules:

1. NEVER change a number, a dimension, a measurement, a date, a phone number
   or a street address. Copy every figure exactly as written, including its
   units. A crew builds to these numbers. Changing one is worse than not
   translating at all.
2. NEVER translate a product or brand name. "Landmark", "IKO Nordic",
   "StormGuard", "Ice & Water", "CertainTeed" stay exactly as written.
3. Keep roofing trade vocabulary the crews actually use in Colorado. Prefer
   the plain working word over the formal one.
4. Keep the structure. One line in, one line out. A bulleted list stays a
   bulleted list.
5. Translate only. Do not add advice, warnings, pleasantries or safety notes
   that were not in the original. The rep wrote what they meant.

Return the Spanish translation and nothing else — no preamble, no quotes, no
explanation."""


def translate_notes(text, client=None):
    """Spanish for one free-text note. Raises TranslateError; never returns ''.

    Callers print the English regardless and treat a failure as "no Spanish on
    this one note", because a work order that refuses to build over a
    translation is a crew standing on a roof with no sheet at all.
    """
    text = (text or '').strip()
    if not text:
        return ''
    if not available():
        raise TranslateError('Spanish notes need ANTHROPIC_API_KEY.')
    client = client or anthropic.Anthropic()
    try:
        with client.beta.messages.stream(
            model=MODEL, max_tokens=4000,
            betas=['server-side-fallback-2026-07-01'], fallbacks='default',
            system=SYSTEM,
            messages=[{'role': 'user', 'content': text[:8000]}],
        ) as stream:
            msg = stream.get_final_message()
    except Exception as e:
        raise TranslateError(f'Could not translate: {e}')
    if msg.stop_reason == 'refusal':
        raise TranslateError('The translation was declined.')
    out = ''.join(b.text for b in msg.content if b.type == 'text').strip()
    if not out:
        raise TranslateError('The translation came back empty.')
    return out


_NUM_RE = re.compile(r'\d[\d,.]*')


def numbers_in(text):
    """Every numeric token, normalized for comparison.

    Trailing punctuation is stripped because a figure at the end of a sentence
    picks up a period in one language and not the other, and that is not a
    changed number.
    """
    return [n.rstrip('.,') for n in _NUM_RE.findall(text or '')]


def numbers_survived(english, spanish):
    """True when the translation carries every figure the English had.

    The one automated check on a translation nobody in the office can read.
    Counted as a multiset, so a figure that appears twice has to appear twice.
    """
    from collections import Counter
    return not (Counter(numbers_in(english)) - Counter(numbers_in(spanish)))
