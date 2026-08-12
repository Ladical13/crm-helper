"""Field notes — a question somebody actually heard, typed in by hand.

This is how Reddit reading legitimately reaches the system. Browsing
r/FortCollins at eleven at night is fine; it is automated access Reddit gates,
not a person reading the site. So a person reads, a person decides it matters,
and a person types the question in their own words. Nothing is scraped and no
Redditor's text is stored.

That last part is enforced rather than hoped for: ``looks_copy_pasted`` rejects
anything long enough to be a lifted post body. The note is meant to be the
*question*, not the thread.

Also the right place for what reps say in the truck, what came up at an HOA
meeting, and anything the CRM miner will not catch because nobody logged it.
"""
import re

from . import honesty
from .. import config

# A paraphrased question is short. Anything past this is somebody pasting a
# post body, which is exactly what we are not doing.
MAX_QUESTION_CHARS = 300


class Rejected(ValueError):
    """The note cannot be stored as written."""


def looks_copy_pasted(text):
    """Heuristics for pasted content rather than a typed question."""
    t = (text or '').strip()
    reasons = []
    if len(t) > MAX_QUESTION_CHARS:
        reasons.append(f'{len(t)} characters — a question should be short. '
                       f'Write what they asked, not the post they wrote.')
    if t.count('\n') >= 3:
        reasons.append('multiple paragraphs — looks pasted rather than typed')
    if re.search(r'\b(u/|/u/|r/\w+\s+said|edit:|tl;dr)\b', t, re.IGNORECASE):
        reasons.append('contains Reddit formatting or a username — paraphrase '
                       'it instead, and never identify the person')
    return reasons


def add(question, heard_where='', city='', service='', username=''):
    """Store one field note. Raises Rejected on anything that looks pasted."""
    q = (question or '').strip()
    if len(q) < 8:
        raise Rejected('too short to be a question')
    problems = looks_copy_pasted(q)
    if problems:
        raise Rejected('; '.join(problems))

    banned = honesty.find_banned_phrases(q)
    if banned:
        raise Rejected(f'uses a banned phrase from the marketing profile: '
                       f'{", ".join(banned)}')

    with config.get_cache_db() as db:
        cur = db.execute(
            'INSERT INTO field_notes (created_at, created_by, question, '
            'heard_where, city, service) VALUES (?, ?, ?, ?, ?, ?)',
            (config.now_iso(), username, q, (heard_where or '').strip()[:80],
             (city or '').strip()[:60], (service or '').strip()[:40]))
        db.commit()
        return {'id': cur.lastrowid, 'question': q, 'heard_where': heard_where,
                'city': city, 'service': service, 'status': 'new'}


def listing(status='new', limit=100):
    where, params = ('WHERE status = ?', [status]) if status else ('', [])
    with config.get_cache_db() as db:
        rows = db.execute(
            f'SELECT * FROM field_notes {where} ORDER BY id DESC LIMIT ?',
            params + [limit]).fetchall()
    return [dict(r) for r in rows]


def set_status(note_id, status):
    if status not in ('new', 'used', 'archived'):
        raise ValueError(f'unknown status {status!r}')
    with config.get_cache_db() as db:
        cur = db.execute('UPDATE field_notes SET status = ? WHERE id = ?',
                         (status, note_id))
        db.commit()
        return bool(cur.rowcount)


def mark_used(note_ids, run_id):
    if not note_ids:
        return 0
    with config.get_cache_db() as db:
        cur = db.execute(
            f'UPDATE field_notes SET status = ?, used_run_id = ? WHERE id IN '
            f'({",".join("?" * len(note_ids))})',
            ['used', run_id] + list(note_ids))
        db.commit()
        return cur.rowcount


def as_research():
    """Shaped like ``research.customer_questions`` so the sources merge."""
    notes = listing(status='new')
    questions, citations = [], []
    for n in notes:
        where = n['heard_where'] or 'noted by the team'
        questions.append({
            'question': n['question'],
            'why_it_matters': f'Heard directly — {where}. Typed in by '
                              f'{n["created_by"] or "the team"}.',
            'source': 'field_note',
        })
        citations.append(f'field-note:{n["id"]}')
    return {
        'questions': questions,
        'citations': citations,
        'evidence_basis': honesty.FIRST_PARTY,
        'cost_usd': 0.0,
        'note': f'{len(questions)} field note(s) awaiting use',
        'note_ids': [n['id'] for n in notes],
    }
