"""Colorado Secretary of State business registry — puts a name to an LLC.

The assessor tells us "EJS HOLDINGS LLC" owns the building. That is who signs,
but it is not somebody a rep can ask for. This looks the entity up in the
state's business registry and returns its **registered agent** — for a small
local LLC, almost always the principal.

Free Socrata dataset, no key: 3.1 million Colorado entities, 2.2 million of
them (71%) carrying an agent name.

Three things this gets wrong if written naively, all found against live data:

  * **A prefix LIKE over-matches wildly.** Searching "HARMONY ROAD" returns a
    music academy, a veterinary clinic, a massage therapist and a mobile home
    park. Every candidate is matched back on a normalised core and anything
    that is not an exact match is thrown away.
  * **The registry never deletes.** Dissolved and delinquent entities come
    back alongside live ones, often with the *same* name — so status is part
    of the ranking, not an afterthought.
  * **The name carries its own epitaph.** A dead entity is stored as
    "Harmony Road Group LLC, Dissolved June 30, 2021", so the status clause
    has to come off before any comparison.

**A registered agent is not always the owner.** Large companies use corporate
agent services or their law firm, and the biggest entities here (Leprino,
Avago) simply have no agent name published at all. That is why this fills in
a contact name when it can and never invents one when it cannot.
"""
import json
import re

from . import _common

API = 'https://data.colorado.gov/resource/4ykn-tg5h.json'

# How many entity names go into one WHERE clause. Ten ran in 0.6s against the
# live dataset; this is kept modest because each is a leading-anchored LIKE.
BATCH = 12

_STATUS_SUFFIX = re.compile(
    r',\s*(dissolved|delinquent|withdrawn|revoked|expired|merged|converted)\b.*$',
    re.I)

_LEGAL_SUFFIX = re.compile(
    r'\b(L\.?L\.?C|L\.?L\.?L\.?P|L\.?L\.?P|L\.?P|INC(ORPORATED)?|CORP(ORATION)?|'
    r'COMPANY|CO|LTD|LIMITED|PC|PLLC|TRUST|ASSOCIATION|ASSN)\b', re.I)

# Commercial registered-agent services and law firms. When one of these is the
# agent, the name is a vendor's, not the owner's — worse than no name, because
# a rep would ask for them by name and be told nobody knows who that is.
_AGENT_SERVICES = re.compile(
    r'(registered agent|agents? inc|corporation service|ct corporation|'
    r'incorp|cogency|northwest registered|legalzoom|harbor compliance|'
    r'national registered|paracorp|vcorp|inc\.? plan|resident agent)', re.I)

# Live entities first; a dissolved shell with the same name is not who owns
# the building today.
_STATUS_RANK = {'good standing': 0, 'active': 1, 'delinquent': 2}


def _core(name):
    """The comparable heart of an entity name.

    Drops the status epitaph, punctuation and the legal-form suffix, so
    "EJS Holdings, LLC" and "EJS HOLDINGS LLC" land on the same string.
    """
    s = _STATUS_SUFFIX.sub('', _common.clean(name) or '')
    s = _LEGAL_SUFFIX.sub(' ', s)
    s = re.sub(r'[^A-Za-z0-9 ]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip().upper()


def _rank(row):
    status = (row.get('entitystatus') or '').strip().lower()
    return _STATUS_RANK.get(status, 3)


def _agent(row):
    first = _common.clean(row.get('agentfirstname'))
    last = _common.clean(row.get('agentlastname'))
    if not (first or last):
        return None
    whole = f'{first} {last}'.strip()
    if _AGENT_SERVICES.search(whole):
        return None            # a vendor's name is worse than none
    return {'first_name': _common.title(first), 'last_name': _common.title(last)}


def principals_for(names, state='CO'):
    """``{original name: {first_name, last_name}}`` for the entities we can
    resolve. Names that cannot be resolved are simply absent.

    Never raises — a failed lookup means the lead keeps the entity name it
    already had, which is still a workable lead.
    """
    if (state or 'CO').upper()[:2] != 'CO':
        return {}
    wanted = {}
    for n in names:
        c = _core(n)
        if len(c) >= 4:              # "ABC" alone would match half the state
            wanted.setdefault(c, n)
    if not wanted:
        return {}

    out = {}
    cores = list(wanted)
    for i in range(0, len(cores), BATCH):
        chunk = cores[i:i + BATCH]
        clause = ' OR '.join(
            "upper(entityname) like '{}%'".format(c.replace("'", "''"))
            for c in chunk)
        body = _common.fetch_cached(
            'sos_' + _cache_key(chunk) + '.json', API,
            ttl_days=30,
            params={'$where': clause, '$limit': 400})
        if not body:
            continue
        try:
            rows = json.loads(body)
        except ValueError:
            continue
        if not isinstance(rows, list):
            continue

        # Keep the best-standing exact core match per name.
        best = {}
        for row in rows:
            c = _core(row.get('entityname'))
            if c not in wanted:
                continue           # the over-match this whole function guards
            prev = best.get(c)
            if prev is None or _rank(row) < _rank(prev):
                best[c] = row
        for c, row in best.items():
            agent = _agent(row)
            if agent:
                out[wanted[c]] = agent
    return out


def _cache_key(chunk):
    import hashlib
    return hashlib.sha1('|'.join(sorted(chunk)).encode()).hexdigest()[:16]
