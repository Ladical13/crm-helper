"""Mid-roof inspections: what each building department requires, remembered.

Some Colorado building departments want an inspector to see the roof partway
through: underlayment on, new covering 25-75% installed. Others want only a
final, and some want a mid-roof for tile or battened roofs and not for asphalt.
A crew that shingles past the window has to strip roofing back off to pass the
final, so the crew lead needs to know this, not just the person pulling the
permit.

**The memory.** One record per jurisdiction id, kept in its own file on the
data volume (`jurisdiction_inspections.json`). A city is looked up once,
either by the AI at the first signing there or by the office typing it in, and
every later job in that city reads the record for free. It is a separate file
and not a field in `jurisdictions.json` for two reasons. ⚙ Settings saves
`jurisdictions.json` by PUTting the whole document from a copy loaded earlier,
so a record written in between would be silently overwritten. And
`_seed_data_dir()` copies `jurisdictions.json` to the volume only when it is
absent, so nothing learned there could ever come from the repo anyway.

**Who wins.** A record has a `status`: `ai` (a model found it) or `confirmed`
(a manager entered or checked it). The AI never overwrites a confirmed record.
That is the same idea as the manager gate on the customer-facing code profile,
moved to where it matters here.

**Which way it fails.** An AI answer of *required* is trusted as required,
because the worst outcome is one unnecessary phone call. An AI answer of *not
required* is not trusted on its own: it prints as "probably not required, office
to confirm" until someone confirms it, because the costly mistake is the
crew skipping an inspection the city wanted.

Pure functions plus a small file store. No Flask, no network. `app.py` does the
lookup and the printing.
"""
import json
import os
import re
import threading
from datetime import datetime, timedelta

try:
    import fcntl                      # Railway (Linux): lock across gunicorn workers
except ImportError:                   # pragma: no cover - Windows dev laptop
    fcntl = None


# The roof coverings an answer may name, and the words the office reads for
# them. The AI is told to choose ONLY from these keys, so a conditional rule
# can be matched against the job without parsing free text.
ROOF_KINDS = {
    'asphalt':      'asphalt shingle',
    'tile':         'tile (clay or concrete)',
    'slate':        'natural slate',
    'metal':        'metal panel / standing seam',
    'stone_coated': 'stone-coated steel',
    'wood_shake':   'wood shake',
    'synthetic':    'synthetic / rubber (slate or shake look)',
    'low_slope':    'low-slope (TPO, EPDM, modified bitumen, built-up)',
    'battens':      'any covering installed on battens',
}

# Coverings that normally go on battens, so a "battens" rule catches them.
# Erring toward "applies" is deliberate: a needless inspection call costs a
# phone call, while a missed one can cost a tear-off.
BATTENED = frozenset({'tile', 'stone_coated'})

MID_ROOF_VALUES = ('required', 'conditional', 'not_required', 'unknown')

# An AI lookup that found nothing official is remembered too, so every job in
# that city does not pay for the same empty search. Asked again after this.
UNKNOWN_RETRY_DAYS = 30


# ── Classifying the job's roof ────────────────────────────────────────────

# First match wins, so the ORDER is the logic. Material words that cannot
# mean asphalt go first; then asphalt brand names, which settle "Presidential
# Shake" and "Landmark" before the bare words "shake" or "shingle" can mislead;
# then the plain material words; then generic shingle wording, last.
_KIND_RULES = (
    ('stone_coated', r'stone[\s-]*coated|\bdecra\b'),
    ('synthetic',    r'synthetic|composite|davinci|\bbrava\b|enviroslate|'
                     r'polymer|\brubber\b|euroshield'),
    ('low_slope',    r'\btpo\b|\bepdm\b|\bpvc\b|mod(?:ified)?[\s-]*bit|'
                     r'built[\s-]*up|\btorch|roll(?:ed)?[\s-]*roof|membrane'),
    ('asphalt',      r'\basphalt\b|landmark|timberline|\bduration\b|oakridge|'
                     r'northgate|malarkey|presidential|grand\s+manor|'
                     r'\bdynasty\b|\bnordic\b|\bcambridge\b|\bvista\b|'
                     r'\bhighlander\b|\bcertainteed\b|\bgaf\b|\biko\b|'
                     r'owens\s+corning|\btamko\b|\batlas\b|'
                     r'\b3[\s-]*tab\b|laminate|architectural'),
    ('slate',        r'\bslate\b'),
    ('tile',         r'\btiles?\b|\bclay\b|concrete'),
    ('wood_shake',   r'\bshakes?\b|\bcedar\b|\bwood\b'),
    ('metal',        r'standing[\s-]*seam|\bmetal\b|\bsteel\b|\bpbr\b|'
                     r'\br[\s-]*panel\b|snap[\s-]*lock|corrugated|'
                     r'aluminum|copper'),
    ('asphalt',      r'\bshingles?\b|\bcomposition\b'),
)
_KIND_RES = tuple((k, re.compile(p, re.IGNORECASE)) for k, p in _KIND_RULES)


def roof_kind(covering_name):
    """'CertainTeed Landmark (Architectural Shingle)' -> 'asphalt'.

    '' when the name does not say what the roof is (a package called
    "Better"). The caller treats '' as "cannot tell", never as asphalt, because
    a guess here decides whether the crew stops for an inspector."""
    s = str(covering_name or '')
    for kind, rx in _KIND_RES:
        if rx.search(s):
            return kind
    return ''


# ── Cleaning an answer into the stored shape ─────────────────────────────

_MID_ALIASES = {
    'yes': 'required', 'always': 'required', 'true': 'required',
    'no': 'not_required', 'none': 'not_required', 'false': 'not_required',
    'notrequired': 'not_required', 'notneeded': 'not_required',
    'sometimes': 'conditional', 'depends': 'conditional',
}


def _short(v, n):
    s = ' '.join(str(v or '').split())
    if s.lower() in ('unknown', 'none', 'null', 'n/a', '-'):
        return ''
    if len(s) <= n:
        return s
    cut = s[:n].rstrip()
    sp = cut.rfind(' ')
    if sp > n // 2:
        cut = cut[:sp]
    return cut.rstrip(',;:') + '...'


def _url(v):
    s = str(v or '').strip()
    return s if re.match(r'^https?://[^\s/]+\.[^\s]+$', s, re.IGNORECASE) else ''


def _photos(v):
    if isinstance(v, bool):
        return v
    s = str(v or '').strip().lower()
    if s in ('true', 'yes', 'y', '1', 'accepted'):
        return True
    if s in ('false', 'no', 'n', '0', 'not accepted'):
        return False
    return None


def clean(raw):
    """Coerce an AI answer or a manager's form into the stored fields.

    Never raises. Anything it does not recognise becomes 'unknown' or empty,
    so a malformed answer can only make a record LESS certain, never more."""
    raw = raw if isinstance(raw, dict) else {}
    mid = re.sub(r'[\s-]+', '_', str(raw.get('mid_roof') or '').strip().lower())
    mid = _MID_ALIASES.get(mid.replace('_', ''), mid)
    if mid not in MID_ROOF_VALUES:
        mid = 'unknown'
    applies = []
    for k in raw.get('applies_to') or []:
        k = re.sub(r'[\s-]+', '_', str(k or '').strip().lower())
        if k in ROOF_KINDS and k not in applies:
            applies.append(k)
    if mid != 'conditional':
        applies = []
    return {
        'mid_roof':        mid,
        'applies_to':      applies,
        'timing':          _short(raw.get('timing'), 120),
        'photos_accepted': _photos(raw.get('photos_accepted')),
        'notes':           _short(raw.get('notes'), 240),
        'source_url':      _url(raw.get('source_url')),
    }


def _now():
    return datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'


def make_record(raw, status, by):
    """A stored record: the cleaned answer plus who said it and when."""
    rec = clean(raw)
    rec['status'] = 'confirmed' if status == 'confirmed' else 'ai'
    rec['updated_at'] = _now()
    rec['updated_by'] = str(by or '').strip() or ('ai' if rec['status'] == 'ai' else '')
    return rec


def needs_lookup(rec, now=None):
    """True when the AI should be asked (again) about this jurisdiction.

    Never for a confirmed record. Once for a city nobody has asked about. Again
    only when an earlier AI answer was 'unknown' and is older than
    UNKNOWN_RETRY_DAYS, since official pages do get published."""
    if not rec:
        return True
    if rec.get('status') == 'confirmed':
        return False
    if rec.get('mid_roof') != 'unknown':
        return False
    try:
        at = datetime.fromisoformat(str(rec.get('updated_at') or '').replace('Z', ''))
    except ValueError:
        return True
    return (now or datetime.utcnow()) - at > timedelta(days=UNKNOWN_RETRY_DAYS)


# ── The store ────────────────────────────────────────────────────────────

_THREAD_LOCK = threading.Lock()


class _FileLock:
    """Serialise read-modify-write across threads AND gunicorn workers.

    Without the cross-process half, two workers signing two jobs in the same
    new city at once could each write a record and lose one."""

    def __init__(self, path):
        self.path = path + '.lock'
        self.fh = None

    def __enter__(self):
        _THREAD_LOCK.acquire()
        if fcntl is not None:
            try:
                os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
                self.fh = open(self.path, 'a')
                fcntl.flock(self.fh, fcntl.LOCK_EX)
            except OSError:
                self.fh = None
        return self

    def __exit__(self, *exc):
        if self.fh is not None:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_UN)
            finally:
                self.fh.close()
                self.fh = None
        _THREAD_LOCK.release()
        return False


def load(path):
    """Every record, keyed by jurisdiction id. {} when there is no file yet."""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    recs = data.get('jurisdictions') if isinstance(data, dict) else None
    return recs if isinstance(recs, dict) else {}


def get(path, jid):
    rec = load(path).get(str(jid or ''))
    return rec if isinstance(rec, dict) else None


def _write(path, recs):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'version': 1, 'jurisdictions': recs}, f, indent=2,
                  ensure_ascii=False, sort_keys=True)
    os.replace(tmp, path)             # a reader never sees half a file


def put(path, jid, rec):
    """Store one record and return what is stored.

    An AI record never replaces a confirmed one, checked inside the lock, so
    a lookup that started before a manager confirmed cannot land after it."""
    jid = str(jid or '').strip()
    if not jid:
        raise ValueError('jurisdiction id required')
    with _FileLock(path):
        recs = load(path)
        old = recs.get(jid)
        if (isinstance(old, dict) and old.get('status') == 'confirmed'
                and rec.get('status') != 'confirmed'):
            return old
        recs[jid] = rec
        _write(path, recs)
    return rec


def delete(path, jid):
    with _FileLock(path):
        recs = load(path)
        if recs.pop(str(jid or ''), None) is not None:
            _write(path, recs)


# ── What it means for one job ────────────────────────────────────────────

def _kinds_text(kinds):
    return ', '.join(ROOF_KINDS.get(k, k) for k in kinds)


def resolve(rec, kind=''):
    """Turn a jurisdiction's record into the verdict for ONE roof.

    Returns state 'required', 'not_required' or 'confirm' (nobody knows yet,
    or only the AI thinks it is not required), plus the lines the documents
    print. ASCII hyphens only, because the same text lands in a PDF."""
    out = {'state': 'confirm', 'confirmed': False, 'line': '', 'timing': '',
           'photos_accepted': None, 'notes': '', 'source_url': '', 'basis': '',
           'roof_kind': kind or ''}
    if not rec:
        out['line'] = 'NOT CONFIRMED - ask the building department before scheduling the crew'
        out['basis'] = 'Not looked up yet'
        return out

    confirmed = rec.get('status') == 'confirmed'
    day = str(rec.get('updated_at') or '')[:10]
    out.update({
        'confirmed':       confirmed,
        'timing':          rec.get('timing') or '',
        'photos_accepted': rec.get('photos_accepted'),
        'notes':           rec.get('notes') or '',
        'source_url':      rec.get('source_url') or '',
        'basis':           (f'Confirmed by {rec.get("updated_by") or "the office"} {day}'
                            if confirmed else
                            f'AI lookup {day} - not yet confirmed by the office'),
    })
    mid = rec.get('mid_roof')
    applies = rec.get('applies_to') or []

    if mid == 'conditional':
        if not applies:
            mid = 'unknown'
        elif not kind:
            out['line'] = (f'Required only for {_kinds_text(applies)} - '
                           'confirm what this roof is')
            return out
        elif kind in applies or ('battens' in applies and kind in BATTENED):
            mid = 'required'
        else:
            mid = 'not_required'

    if mid == 'required':
        out['state'] = 'required'
        what = f' for {ROOF_KINDS.get(kind, kind)}' if rec.get('mid_roof') == 'conditional' else ''
        out['line'] = f'REQUIRED{what}' + (f' - {out["timing"]}' if out['timing'] else '')
        return out

    if mid == 'not_required':
        scope = (f' for {ROOF_KINDS.get(kind, kind)} (only for {_kinds_text(applies)})'
                 if rec.get('mid_roof') == 'conditional' else '')
        if confirmed:
            out['state'] = 'not_required'
            out['line'] = f'Not required{scope}'
        else:
            out['line'] = f'Probably not required{scope} - office to confirm'
        return out

    out['line'] = 'NOT CONFIRMED - ask the building department before scheduling the crew'
    if not confirmed:
        out['basis'] = f'AI lookup {day} found no official answer'
    return out
