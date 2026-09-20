"""A second reader for the price book's material/labor split.

Every catalog product carries ONE cost. Some of that money buys *things*
(shingles, drip edge, ice & water) and some of it buys *people* (the crew
tearing off and installing). Nothing in the data says which, so `cost_class`
is the stored decision and `_guess_cost_class` writes the first draft of it.

That guesser is a keyword match, and keyword matching is wrong in specific,
funny, expensive ways. "Pancake Sc**rew**s" contains the word *crew*, which is
why `crew` is deliberately absent from the labor words. "Metal Delivery &
Rollformer Set-Up" reads like a crew setting something up and is a $368
supplier charge, which is why the exclusion list runs first. Every one of those
carve-outs is a fault somebody already hit, written down after the fact — the
list is a record of the traps that have been found, not of the ones that exist.

This module is the same job done by something that reads a product name the way
a person would. Four things keep it safe, and they are the house rules, not new
ones:

- **It only ever PROPOSES.** Nothing here writes. `review()` returns a diff a
  manager reads, and `apply()` takes only the ids that manager approved back.
  Same contract as `classifyCarrierItem` and as the jurisdiction verifier: the
  guess is a starting point, the stored decision is the answer.
- **It is never in the request path.** `_ensure_bundle_catalogs()` runs on
  every price-book GET, so a model call there would put latency and money on a
  screen a rep opens all day. This runs when a manager asks it to, once, over
  the whole book.
- **`_guess_cost_class` stays and stays first.** It is free, instant, needs no
  API key and no network, and it classifies every new product the moment it is
  created. This reviews its work; it does not replace it.
- **It can only ever move the SPLIT.** `cost_class` is forbidden from touching
  a total, a sell price, a margin floor or a quantity — `tests/test_cost_split.py`
  pins that — so the worst a wrong proposal can do, if a manager approves it
  without looking, is misreport which column an already-correct cost sits in.

Without `ANTHROPIC_API_KEY` this reports unavailable and the price book behaves
exactly as it did before the module existed.
"""
import json
import os

try:
    import anthropic
except ImportError:          # the keyword guesser is unaffected
    anthropic = None


MODEL = os.environ.get('COST_CLASS_MODEL', 'claude-opus-5')
# One call for the whole book. A product line is an id, a name and a class —
# about fifteen tokens — so even a 780-product catalog is a small request, and
# splitting it would cost more in repeated instructions than it saved.
MAX_PRODUCTS = 2000

CLASSES = ('material', 'labor')


class ReviewError(Exception):
    """A review that could not be run. The message is shown to the manager."""


def available():
    return anthropic is not None and bool(os.environ.get('ANTHROPIC_API_KEY', '').strip())


SYSTEM = """You are reviewing a roofing and exterior contractor's price book.

Every product in it carries a single cost, and that cost is filed into one of
two buckets:

  material - a thing the company BUYS. Shingles, underlayment, drip edge,
             ridge cap, fasteners, coil, sealant, vents, gutters, siding.
             Also anything a supplier or a third party invoices for that is
             not our crew's time: delivery, freight, rollformer set-up, crane
             hire, dumpsters, permits, inspections, moisture surveys.

  labor    - our own crew's TIME. Tear-off, install, detach and reset,
             demolition, haul-off, clean-up of our own work.

You are given the products with the bucket currently stored against each one.
Report ONLY the ones you believe are filed in the wrong bucket.

Rules:
- Judge from the product NAME. The id is a hint at best.
- A name that mixes both (a product whose price covers supply AND fit) stays
  as it is. Report only a clear mis-filing, not a debatable one.
- A supplier charge is material even when it describes an activity. "Delivery",
  "Set-Up", "Freight" and "Crane" are somebody else's invoice, not our crew.
- Watch for names where a labor word appears inside another word. "Screws"
  contains "crew". "Installation Kit" is a box of parts, not a crew.
- When you are unsure, leave it alone. A manager reads every line you return,
  and a list of maybes is a list nobody finishes.

Return the proposals only. An empty list is a good answer."""

SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['proposals'],
    'properties': {
        'proposals': {
            'type': 'array',
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'required': ['product_id', 'proposed', 'reason'],
                'properties': {
                    'product_id': {'type': 'string'},
                    'proposed': {'type': 'string', 'enum': list(CLASSES)},
                    'reason': {'type': 'string',
                               'description': 'One short sentence a manager can '
                                              'judge without opening the product.'},
                },
            },
        },
    },
}


def products_for_review(pb, cost_class_of):
    """[(trade, id, name, current_class)] for every catalog product in a book.

    Flat and trade-tagged rather than nested: the model is asked one question
    per row, and the trade only exists so the manager can see where a proposal
    landed.
    """
    rows = []
    for key in sorted(k for k in pb if k.endswith('_catalog')):
        trade = key[:-len('_catalog')]
        for p in (pb.get(key) or []):
            pid = str(p.get('id') or '').strip()
            if not pid:
                continue
            rows.append((trade, pid, str(p.get('name') or ''), cost_class_of(p)))
    return rows


def _payload(rows):
    return '\n'.join(f'{pid}\t{current}\t{name}' for _t, pid, name, current in rows)


def review(rows, client=None):
    """Proposed reclassifications for `rows`, as a list of dicts.

    Every proposal is checked back against the rows that were sent: a product
    id the book does not have, a class that is not one of the two, or a
    "change" to the class already stored are all dropped here rather than shown
    to a manager. The model is a second reader, not a second source of ids.
    """
    if not available():
        raise ReviewError('Cost-class review needs ANTHROPIC_API_KEY.')
    rows = list(rows)[:MAX_PRODUCTS]
    if not rows:
        return []
    by_id = {pid: (trade, name, current) for trade, pid, name, current in rows}

    client = client or anthropic.Anthropic()
    try:
        with client.beta.messages.stream(
            model=MODEL, max_tokens=32000,
            betas=['server-side-fallback-2026-07-01'], fallbacks='default',
            system=SYSTEM,
            output_config={'format': {'type': 'json_schema', 'schema': SCHEMA}},
            messages=[{'role': 'user', 'content':
                       'product_id\tcurrent\tname\n' + _payload(rows)}],
        ) as stream:
            msg = stream.get_final_message()
    except Exception as e:
        raise ReviewError(f'Could not run the review: {e}')

    if msg.stop_reason == 'refusal':
        raise ReviewError('The review was declined.')
    text = ''.join(b.text for b in msg.content if b.type == 'text')
    try:
        data = json.loads(text)
    except Exception:
        raise ReviewError('The review came back unreadable.')

    out = []
    for prop in (data.get('proposals') or []):
        pid = str(prop.get('product_id') or '').strip()
        proposed = str(prop.get('proposed') or '').strip().lower()
        if pid not in by_id or proposed not in CLASSES:
            continue
        trade, name, current = by_id[pid]
        if proposed == current:
            continue
        out.append({'trade': trade, 'product_id': pid, 'name': name,
                    'current': current, 'proposed': proposed,
                    'reason': str(prop.get('reason') or '').strip()[:300]})
    out.sort(key=lambda r: (r['trade'], r['name'].lower()))
    return out


def apply(pb, approved):
    """Write approved classes into `pb` in place. Returns what changed.

    `approved` is [{product_id, cost_class}] — the ids a manager ticked, not
    the ids the model returned. A proposal nobody approved never reaches this
    function, which is what keeps the model a reviewer rather than an editor.
    """
    wanted = {}
    for item in (approved or []):
        pid = str((item or {}).get('product_id') or '').strip()
        klass = str((item or {}).get('cost_class') or '').strip().lower()
        if pid and klass in CLASSES:
            wanted[pid] = klass
    if not wanted:
        return []

    changed = []
    for key in (k for k in pb if k.endswith('_catalog')):
        for p in (pb.get(key) or []):
            pid = str(p.get('id') or '').strip()
            if pid in wanted and p.get('cost_class') != wanted[pid]:
                changed.append({'trade': key[:-len('_catalog')], 'product_id': pid,
                                'name': str(p.get('name') or ''),
                                'cost_class': wanted[pid]})
                p['cost_class'] = wanted[pid]
    return changed
