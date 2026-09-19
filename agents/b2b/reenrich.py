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


def _prompt(row, hint=''):
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
        f'- org_phone: the organization\'s main published phone number, or "unknown"\n'
        f'- website: the organization\'s own official website URL (not a directory '
        f'or social media page), or "unknown"\n'
        f'- news: public news in the last 24 months about roof, storm, hail, '
        f'insurance claim, construction, capital campaign or bond\n'
        f'- summary: ONE plain-English sentence a rep can use in a cold email\n\n'
        f'Organization: {(row.get("company") or "").strip()}\n'
        f'Address: {row.get("address", "")}, {row.get("city", "")} '
        f'{row.get("state", "")} {row.get("zip", "")}\n'
        f'Website: {row.get("website") or "unknown"}\n\n'
        + (f'A sales rep who called them noted: "{hint}". Use it to find the right person.\n\n'
           if hint else '')
        + f'Return JSON with exactly these keys: '
        f'decision_maker, org_email, org_phone, website, news, summary, citations'
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


# Directories and social pages are not an organisation's website: reading a
# Facebook page or a Yelp listing for staff emails finds nothing and teaches
# the website reader nothing it can use.
_NOT_A_SITE = ('facebook.com', 'instagram.com', 'yelp.com', 'linkedin.com', 'twitter.com',
               'x.com', 'youtube.com', 'google.com', 'mapquest.com', 'yellowpages.com',
               'bbb.org', 'guidestar.org', 'churchfinder.com', 'greatschools.org',
               'niche.com', 'nextdoor.com', 'manta.com', 'bizapedia.com', 'opencorporates.com')


def _website(v):
    v = _known(v)
    if not v:
        return ''
    if not re.match(r'^https?://', v, re.I):
        v = 'https://' + v
    m = re.match(r'^https?://([^/\s]+)', v, re.I)
    host = (m.group(1).lower() if m else '').split(':')[0]
    if not host or '.' not in host or any(host == d or host.endswith('.' + d) for d in _NOT_A_SITE):
        return ''
    return v.split('#')[0].rstrip('/')


def _phone(v):
    digits = re.sub(r'\D', '', _known(v))
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return f'({digits[:3]}) {digits[3:6]}-{digits[6:]}' if len(digits) == 10 else ''


def contact_fields(data, citations):
    """The contact fields research may fill: first_name, last_name, email,
    phone, website.

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
    # A direct line beats the switchboard; the switchboard beats nothing.
    phone = _phone(dm.get('phone')) or _phone(data.get('org_phone'))
    if phone:
        out['phone'] = phone
    site = _website(data.get('website'))
    if site:
        out['website'] = site
    return out


def research(row, model=None, hint=''):
    """(data, citations, cost) for one lead. Raises SpendCapReached."""
    result = perplexity.search_json(_prompt(row, hint), system=_SYSTEM, model=model,
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
        supp = crm._suppression_index(db)
        if 'email' in fill and crm._suppressed_by(supp, '', crm._norm_email(fill['email']), ''):
            fill.pop('email')
        if 'phone' in fill and crm._suppressed_by(supp, crm._norm_phone(fill['phone']), '', ''):
            fill.pop('phone')
        if dry_run:
            return fill
        now = crm._now()
        sets = {'research_notes': json.dumps(data, sort_keys=True) if data else '',
                'research_citations': json.dumps(citations),
                'enriched_at': now, 'updated_at': now}
        sets.update(fill)
        if fill and 'contact_source' in lead:
            # Recorded so "wrong contact" can clear what research put there
            # without touching anything a rep typed.
            sets['contact_source'] = 'research'
        if 'email' in fill:
            sets['email_norm'] = crm._norm_email(fill['email'])
        if 'phone' in fill:
            sets['phone_norm'] = crm._norm_phone(fill['phone'])
        if isinstance(data, dict) and _looks_promising(data):
            sets['icp_score'] = int(lead.get('icp_score') or 0) + 1
        db.execute('UPDATE leads SET ' + ', '.join(f'{k}=?' for k in sets) + ' WHERE id=?',
                   list(sets.values()) + [lead['id']])
        if hasattr(crm, '_refresh_contact_quality'):
            crm._refresh_contact_quality(db, lead['id'])
        found = []
        if 'first_name' in fill:
            found.append(f"contact {fill['first_name']} {fill.get('last_name', '')}".strip())
        if 'email' in fill:
            found.append(f"email {fill['email']}")
        if 'phone' in fill:
            found.append(f"phone {fill['phone']}")
        if 'website' in fill:
            found.append(f"website {fill['website']}")
        crm._log_activity(
            db, lead['id'], 'system', rep=lead['rep'],
            body=('Researched: found ' + ', '.join(found) + '. From public sources - verify '
                  'before relying on it.') if found else
                 'Researched: no named contact or email published. Notes in Research.')
    return fill


# People change jobs: a researched contact older than this is looked up again.
STALE_DAYS = 365


def candidates(crm, limit, lead_type=None, mode='new'):
    """Leads to research.

    new      never researched
    missing  researched, but still no website or no way to reach anyone -
             for re-running leads researched before phone and website were asked for
    stale    researched more than STALE_DAYS ago and never confirmed by a rep
    """
    base = "SELECT * FROM leads WHERE dnc = 0 AND stage = 'new' AND company != '' AND "
    if mode == 'missing':
        q = base + ("enriched_at != '' AND (website = '' OR (phone = '' AND email = '')) "
                    "AND contact_verified_at = ''")
    elif mode == 'stale':
        from datetime import datetime, timedelta
        cutoff = (datetime.utcnow() - timedelta(days=STALE_DAYS)).strftime('%Y-%m-%dT%H:%M:%SZ')
        q = base + f"enriched_at != '' AND enriched_at < '{cutoff}' AND contact_verified_at = ''"
    else:
        q = base + "enriched_at = ''"
    params = []
    if lead_type:
        q += ' AND lead_type = ?'
        params.append(lead_type)
    q += ' ORDER BY icp_score DESC, created_at LIMIT ?'
    with crm.get_db() as db:
        return [dict(r) for r in db.execute(q, params + [limit])]


def run(crm, limit=50, lead_type=None, dry_run=False, log=print, mode='new'):
    rows = candidates(crm, limit, lead_type, mode=mode)
    log(f'{len(rows)} leads ({mode}); month spend so far ${perplexity.month_spend_usd():.2f}')
    names = emails = phones = sites = spent = 0
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
        phones += 'phone' in fill
        sites += 'website' in fill
        log(f'  [{i}/{len(rows)}] {lead.get("company")}: '
            + (', '.join(f'{k}={v}' for k, v in fill.items()) or 'no contact found'))
    log(f'Done: {names} names, {emails} emails, {phones} phones, {sites} websites filled; '
        f'${spent:.2f} this run' + (' (dry run - nothing written)' if dry_run else ''))
    return {'names': names, 'emails': emails, 'phones': phones, 'websites': sites,
            'spent': spent, 'seen': len(rows)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--limit', type=int, default=50)
    ap.add_argument('--type', dest='lead_type')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--mode', choices=('new', 'missing', 'stale'), default='new')
    args = ap.parse_args(argv)
    import portal.wsgi  # noqa: F401  — loads the CRM as p1_crm_app
    crm = sys.modules['p1_crm_app']
    run(crm, limit=args.limit, lead_type=args.lead_type, dry_run=args.dry_run, mode=args.mode)
    return 0


if __name__ == '__main__':
    sys.exit(main())
