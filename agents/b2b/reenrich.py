"""Research leads that are already in the CRM but were never researched.

    python -m agents.b2b.reenrich --limit 50 --dry-run
    python -m agents.b2b.reenrich --limit 503

The dispatcher researches leads while it imports them, capped at the top N
per run, so a batch imported without research stays unresearched forever:
every script opens on "Hi there" to a front desk, and every email template is
useless because nobody has an email. This fills that in for existing rows.

Per lead, one Perplexity call (~$0.02, cached, under the monthly spend cap).
The storm join is deliberately skipped: it geocodes through Nominatim, which
CLAUDE.md already flags as used against its policy, and the hail archive does
storms properly.

What it writes, and the rules that keep it honest:

* research_notes / research_citations / enriched_at — always, as the
  dispatcher's own write-back does.
* first_name / last_name / email — ONLY when the lead's field is empty, the
  answer is not "unknown", and the answer arrived with at least one citation.
  A fabricated pastor's email is worse than none: it bounces, or worse, lands.
* An email that is on the suppression list is never filled in.
* A system note on the lead's timeline says what was found and that it came
  from research, so the rep verifies before relying on it.
"""
import argparse
import json
import re
import sys

from .. import perplexity
from .enrich import _SYSTEM, _kind_for, _looks_promising

_EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
_HONORIFICS = {'rev', 'rev.', 'reverend', 'pastor', 'fr', 'fr.', 'father', 'dr', 'dr.',
               'mr', 'mr.', 'mrs', 'mrs.', 'ms', 'ms.', 'sister', 'brother', 'bishop',
               'elder', 'deacon', 'msgr', 'msgr.'}


def _prompt(row):
    kind = _kind_for(row.get('lead_type') or '')
    return (
        f'Research this {kind} for a first call from a local roofing contractor. '
        f'Find the person who handles the building, facilities or maintenance '
        f'(for a church: business administrator, facilities or trustees chair, '
        f'else the senior pastor; for a school district: facilities director; '
        f'for an HOA: the management company contact or board president).\n'
        f'Report:\n'
        f'- decision_maker: {{name, title, email, phone}} — email and phone only '
        f'if published on a public page; otherwise "unknown"\n'
        f'- org_email: a general contact email published by the organization, or "unknown"\n'
        f'- news: public news in the last 24 months about roof, storm, hail, '
        f'insurance claim, construction, capital campaign or bond\n'
        f'- summary: ONE plain-English sentence a rep can use in a cold email\n\n'
        f'Organization: {(row.get("company") or "").strip()}\n'
        f'Address: {row.get("address", "")}, {row.get("city", "")} '
        f'{row.get("state", "")} {row.get("zip", "")}\n'
        f'Website: {row.get("website") or "unknown"}\n\n'
        f'Return JSON with exactly these keys: '
        f'decision_maker, org_email, news, summary, citations'
    )


def _known(v):
    v = (v or '').strip() if isinstance(v, str) else ''
    return '' if not v or v.lower() in ('unknown', 'n/a', 'none', 'null') else v


def split_name(full):
    """'Rev. Dr. John A. Smith' -> ('John', 'A. Smith'). ('', '') if unusable."""
    parts = [p for p in (full or '').replace(',', ' ').split() if p]
    while parts and parts[0].lower() in _HONORIFICS:
        parts.pop(0)
    if len(parts) < 2 or not parts[0][0].isalpha():
        return '', ''
    return parts[0], ' '.join(parts[1:])


def contact_fields(data, citations):
    """The contact fields research may fill: {'first_name','last_name','email'}.

    Empty unless the answer came with at least one citation."""
    if not citations or not isinstance(data, dict):
        return {}
    out = {}
    dm = data.get('decision_maker') if isinstance(data.get('decision_maker'), dict) else {}
    first, last = split_name(_known(dm.get('name')))
    if first:
        out['first_name'], out['last_name'] = first, last
    email = _known(dm.get('email')) or _known(data.get('org_email'))
    if email and _EMAIL_RE.match(email):
        out['email'] = email.lower()
    return out


def research(row, model=None):
    """(data, citations, cost) for one lead. Raises SpendCapReached."""
    result = perplexity.search_json(_prompt(row), system=_SYSTEM, model=model,
                                    max_tokens=1500, reason='b2b-reenrich')
    data = result.get('data') or {}
    citations = (data.get('citations') if isinstance(data, dict) else None) \
        or result.get('citations') or []
    if isinstance(data, dict):
        data['citations'] = citations
    return data, [c for c in citations if isinstance(c, str) and c.startswith('http')], \
        float(result.get('cost_usd') or 0.0)


def apply(crm, lead, data, citations, dry_run=False):
    """Write one lead's research. Returns the contact fields actually filled."""
    fill = contact_fields(data, citations)
    # Never overwrite what a rep or the import already put there.
    fill = {k: v for k, v in fill.items() if not (lead.get(k) or '').strip()}
    if 'first_name' in fill and (lead.get('last_name') or '').strip():
        fill.pop('last_name', None)
    with crm.get_db() as db:
        if 'email' in fill:
            supp = crm._suppression_index(db)
            if crm._suppressed_by(supp, '', crm._norm_email(fill['email']), ''):
                fill.pop('email')
        if dry_run:
            return fill
        now = crm._now()
        sets = {'research_notes': json.dumps(data, sort_keys=True) if data else '',
                'research_citations': json.dumps(citations),
                'enriched_at': now, 'updated_at': now}
        sets.update(fill)
        if 'email' in fill:
            sets['email_norm'] = crm._norm_email(fill['email'])
        if isinstance(data, dict) and _looks_promising(data):
            sets['icp_score'] = int(lead.get('icp_score') or 0) + 1
        db.execute('UPDATE leads SET ' + ', '.join(f'{k}=?' for k in sets) + ' WHERE id=?',
                   list(sets.values()) + [lead['id']])
        found = []
        if 'first_name' in fill:
            found.append(f"contact {fill['first_name']} {fill.get('last_name', '')}".strip())
        if 'email' in fill:
            found.append(f"email {fill['email']}")
        crm._log_activity(
            db, lead['id'], 'system', rep=lead['rep'],
            body=('Researched: found ' + ', '.join(found) + '. From public sources - verify '
                  'before relying on it.') if found else
                 'Researched: no named contact or email published. Notes in Research.')
    return fill


def candidates(crm, limit, lead_type=None):
    q = ("SELECT * FROM leads WHERE enriched_at = '' AND dnc = 0 AND stage = 'new' "
         "AND company != ''")
    params = []
    if lead_type:
        q += ' AND lead_type = ?'
        params.append(lead_type)
    q += ' ORDER BY icp_score DESC, created_at LIMIT ?'
    with crm.get_db() as db:
        return [dict(r) for r in db.execute(q, params + [limit])]


def run(crm, limit=50, lead_type=None, dry_run=False, log=print):
    rows = candidates(crm, limit, lead_type)
    log(f'{len(rows)} unresearched leads; month spend so far ${perplexity.month_spend_usd():.2f}')
    names = emails = spent = 0
    for i, lead in enumerate(rows, 1):
        try:
            data, cites, cost = research(lead)
        except perplexity.SpendCapReached:
            log('Monthly spend cap reached - stopping. Re-run next month or raise the cap.')
            break
        except Exception as e:                       # one bad answer must not stop 500
            log(f'  [{i}] {lead.get("company")}: research failed ({e})')
            continue
        spent += cost
        fill = apply(crm, lead, data, cites, dry_run=dry_run)
        names += 'first_name' in fill
        emails += 'email' in fill
        log(f'  [{i}/{len(rows)}] {lead.get("company")}: '
            + (', '.join(f'{k}={v}' for k, v in fill.items()) or 'no contact found'))
    log(f'Done: {names} names, {emails} emails filled; ${spent:.2f} this run'
        + (' (dry run - nothing written)' if dry_run else ''))
    return {'names': names, 'emails': emails, 'spent': spent, 'seen': len(rows)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--limit', type=int, default=50)
    ap.add_argument('--type', dest='lead_type')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    import portal.wsgi  # noqa: F401  — loads the CRM as p1_crm_app
    crm = sys.modules['p1_crm_app']
    run(crm, limit=args.limit, lead_type=args.lead_type, dry_run=args.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
