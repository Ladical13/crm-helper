"""Mine the sales CRM for the questions customers actually ask.

Better than Reddit, and nobody can revoke it. Reddit would have given us
strangers in Denver; this is every note a rep wrote about a real conversation
with a real Colorado customer — our market, our price point, our objections.

**Read-only, and enforced.** The connection is opened with SQLite's `mode=ro`
URI flag, so a stray UPDATE fails at the driver rather than relying on this
module to behave. Nimbus must never write to salescrm.db: that file holds the
leads, and a marketing tool has no business touching them.

Counts here are real. "Mentioned in 7 logged conversations" is a fact about
records we wrote ourselves, which is why these findings carry
``evidence_basis: first_party`` rather than the ``public_research`` everything
else uses. What stays impossible is anything about *search* — no ranking, no
volume, no traffic, regardless of source.
"""
import os
import re
import sqlite3
from collections import Counter

from .honesty import FIRST_PARTY

# Notes are shorthand written between calls. These are the shapes a question
# takes in that register, including the ones with no question mark.
_QUESTION_RE = re.compile(
    r'((?:^|[.!?;]\s*)'
    r'(?:how|what|why|when|where|which|who|does|do|did|can|could|should|is|are|'
    r'was|were|will|would|any|anyone|wants? to know|asked about|asking about|'
    r'wondering|concerned about|worried about)'
    r'\b[^.!?]{6,160}[?.!]?)', re.IGNORECASE)

# Phrases reps write that introduce a customer question without punctuating it.
_ASK_MARKERS = ('asked about', 'asking about', 'wants to know', 'wanted to know',
                'wondering', 'concerned about', 'worried about', 'question about',
                'questions about')

_STOPWORDS = {
    'the', 'a', 'an', 'and', 'or', 'to', 'of', 'in', 'on', 'at', 'is', 'are',
    'was', 'were', 'be', 'they', 'their', 'them', 'he', 'she', 'his', 'her',
    'we', 'our', 'us', 'you', 'your', 'it', 'its', 'this', 'that', 'have',
    'has', 'had', 'will', 'would', 'can', 'could', 'should', 'do', 'does',
    'did', 'about', 'with', 'for', 'from', 'not', 'but', 'said', 'says',
    'told', 'call', 'called', 'customer', 'homeowner', 'wants', 'want',
    'know', 'asked', 'asking', 'left', 'voicemail', 'spoke', 'talked',
}

# Only surface a theme once it has come up more than once — a single note is
# an anecdote, and building a page for one caller is how you get a site full
# of pages nobody searched for.
MIN_MENTIONS = 2


def db_path():
    data_dir = (os.environ.get('SALESCRM_DATA_DIR')
                or os.environ.get('DATA_DIR') or '')
    return os.path.join(data_dir, 'salescrm.db') if data_dir else ''


def available():
    p = db_path()
    return bool(p and os.path.exists(p))


def _connect_readonly():
    """Open salescrm.db read-only at the driver level.

    `mode=ro` means a write fails with an sqlite3 error rather than depending
    on this module never attempting one. Nimbus has no business writing to the
    file that holds the leads.
    """
    path = db_path()
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f'salescrm.db not found at {path or "(unset)"}')
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _terms(text):
    words = re.findall(r'[a-z0-9]+', (text or '').lower())
    return {w for w in words if len(w) > 3 and w not in _STOPWORDS}


def _clean(fragment):
    """Tidy a raw note fragment into something readable as a question."""
    s = re.sub(r'^[.!?;\s]+', '', (fragment or '')).strip()
    s = re.sub(r'\s+', ' ', s)
    if not s:
        return ''
    s = s[0].upper() + s[1:]
    if not s.endswith(('?', '.', '!')):
        s += '?'
    return s.rstrip('.') if s.endswith('.') else s


def extract_questions(text):
    """Pull question-shaped fragments out of one note. Never raises."""
    out = []
    body = (text or '').strip()
    if not body:
        return out
    for m in _QUESTION_RE.finditer(body):
        cleaned = _clean(m.group(1))
        if 12 <= len(cleaned) <= 200:
            out.append(cleaned)
    # Catch "asked about ice dams" style notes the regex above may split oddly.
    low = body.lower()
    for marker in _ASK_MARKERS:
        idx = low.find(marker)
        if idx >= 0:
            tail = body[idx + len(marker):].strip(' :-–')
            tail = re.split(r'[.!?;\n]', tail)[0].strip()
            if 4 <= len(tail) <= 120:
                out.append(_clean(f'{marker} {tail}'))
    return list(dict.fromkeys(out))


def _cluster(found, threshold=0.5):
    """Group near-identical questions so one theme is one row."""
    clusters = []
    for item in found:
        terms = _terms(item['question'])
        if not terms:
            continue
        for c in clusters:
            overlap = len(terms & c['terms']) / min(len(terms), len(c['terms']))
            if overlap >= threshold:
                c['mentions'] += 1
                c['evidence'].append(item['ref'])
                c['cities'].append(item.get('city', ''))
                # Keep the longest phrasing — usually the most complete one.
                if len(item['question']) > len(c['question']):
                    c['question'] = item['question']
                break
        else:
            clusters.append({'question': item['question'], 'terms': terms,
                             'mentions': 1, 'evidence': [item['ref']],
                             'cities': [item.get('city', '')]})
    return clusters


def mine(days=180, limit_notes=4000):
    """Recurring customer questions from CRM activity. Returns a dict.

    Never raises: no CRM file, an unreadable one, or an empty one all mean one
    fewer input, not a failed run.
    """
    if not available():
        return {'available': False, 'questions': [], 'notes_scanned': 0,
                'note': f'salescrm.db not found (SALESCRM_DATA_DIR={os.environ.get("SALESCRM_DATA_DIR", "unset")})'}
    try:
        conn = _connect_readonly()
    except Exception as e:                                       # noqa: BLE001
        return {'available': False, 'questions': [], 'notes_scanned': 0,
                'note': f'could not open salescrm.db read-only ({type(e).__name__})'}

    found, scanned = [], 0
    try:
        rows = conn.execute(
            "SELECT a.id, a.lead_id, a.body, a.created_at, "
            "       COALESCE(l.city, '') AS city "
            "FROM activities a LEFT JOIN leads l ON l.id = a.lead_id "
            "WHERE a.body != '' AND a.created_at >= date('now', ?) "
            "ORDER BY a.created_at DESC LIMIT ?",
            (f'-{int(days)} day', int(limit_notes))).fetchall()
        for r in rows:
            scanned += 1
            for q in extract_questions(r['body']):
                found.append({'question': q, 'ref': f'crm:lead:{r["lead_id"]}',
                              'city': r['city']})
    except sqlite3.Error as e:
        conn.close()
        return {'available': False, 'questions': [], 'notes_scanned': 0,
                'note': f'CRM read failed: {e}'}
    finally:
        try:
            conn.close()
        except Exception:                                        # noqa: BLE001
            pass

    clusters = [c for c in _cluster(found) if c['mentions'] >= MIN_MENTIONS]
    clusters.sort(key=lambda c: -c['mentions'])

    questions = []
    for c in clusters:
        cities = [x for x in c['cities'] if x]
        top_city = Counter(cities).most_common(1)[0][0] if cities else ''
        questions.append({
            'question': c['question'],
            'mentions': c['mentions'],
            'city': top_city,
            # Deduped lead refs, capped — the point is provenance, not a list.
            'evidence': list(dict.fromkeys(c['evidence']))[:8],
            'why_it_matters': f'Mentioned in {c["mentions"]} logged conversations'
                              + (f', most often in {top_city}' if top_city else '')
                              + '. This is our own record of what customers ask.',
            'source': 'crm',
        })

    note = (f'{len(questions)} recurring question(s) from {scanned} note(s)'
            if questions else
            f'{scanned} note(s) scanned, nothing recurring yet '
            f'(a theme needs {MIN_MENTIONS}+ mentions)')
    return {'available': True, 'questions': questions,
            'notes_scanned': scanned, 'note': note}


def as_research(days=180):
    """Shaped like ``research.customer_questions`` so the two merge cleanly."""
    out = mine(days=days)
    return {
        'questions': [{'question': q['question'],
                       'why_it_matters': q['why_it_matters'],
                       'source': 'crm'} for q in out['questions']],
        'citations': [ref for q in out['questions'] for ref in q['evidence']],
        'evidence_basis': FIRST_PARTY,
        'cost_usd': 0.0,
        'note': out['note'],
    }
