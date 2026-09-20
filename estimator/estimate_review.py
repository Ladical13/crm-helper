"""A second estimator reading the job over the rep's shoulder, before it goes.

Everything this checks, the tool already knows. The ventilation math prints
installed square inches against required. The margin report knows the worst
package on offer and which tiers have no cost behind them. The jurisdiction
profile knows which code year the city enforces. `valid_until` knows whether
the pricing is still good.

What did not exist was anything that read all of it AT ONCE, at the moment it
matters — the moment before a rep sends the thing to a homeowner. A two-rep
company cannot staff a senior estimator to look over every job; this is that
pass. It computes nothing: every number in `facts` was worked out by the
functions that already own it, and re-deriving one here would be a second
implementation of money math in a codebase that keeps exactly two on purpose
and holds them to the cent.

Two layers, deliberately independent:

- `deterministic_findings()` is rules over those numbers. Free, offline, no API
  key, and it is the layer that catches the things worth blocking on. It runs
  whether or not anything else does.
- `ai_findings()` is a reader for what no rule covers — the combinations. A
  rule can say "ventilation is short"; it takes judgement to notice that the
  scope decks over six box vents on a house whose attic area was never entered,
  so the shortfall is being measured against a guess.

Neither layer blocks a send. `_margin_floor_block` is the one thing in this
system that stops an estimate leaving, it has its own settings and its own
tests, and a review that could veto a send would be a second gate that
disagrees with the first. This informs; the rep decides.
"""
import json
import os

try:
    import anthropic
except ImportError:
    anthropic = None


MODEL = os.environ.get('ESTIMATE_REVIEW_MODEL', 'claude-opus-5')

SEVERITIES = ('high', 'medium', 'low')
_RANK = {s: i for i, s in enumerate(SEVERITIES)}


class ReviewError(Exception):
    """A review that could not be run. The message is shown to the rep."""


def available():
    return anthropic is not None and bool(os.environ.get('ANTHROPIC_API_KEY', '').strip())


def _finding(code, severity, what, fix=''):
    return {'code': code, 'severity': severity, 'what': what, 'fix': fix,
            'source': 'rule'}


def deterministic_findings(facts):
    """Everything a rule can decide. Runs with no API key and no network.

    Ordered by how much the thing costs when it goes out wrong, not by how
    easy it was to check.
    """
    out = []
    est_type = facts.get('estimate_type') or 'retail'

    # ── Money ──────────────────────────────────────────────────────────────
    worst = facts.get('margin_worst')
    warn  = facts.get('margin_warn_floor')
    if worst and warn and worst.get('margin_pct') is not None \
            and worst['margin_pct'] < warn:
        out.append(_finding(
            'margin_below_warn', 'high',
            f"The {worst['tier'].title()} package prices at "
            f"{worst['margin_pct']}% margin, under the {warn:g}% target.",
            'The customer picks the package, so the worst one on offer is the '
            'one to fix.'))

    unknown = facts.get('margin_unknown_tiers') or []
    if unknown:
        out.append(_finding(
            'margin_unknown', 'high',
            f"No cost behind {', '.join(t.title() for t in unknown)} — the "
            f"margin on {'that package is' if len(unknown) == 1 else 'those packages are'} "
            f"unknown, not healthy.",
            'A tier with no cost reports an unknown margin on purpose. Price '
            'the lines before this goes out.'))

    if facts.get('unpriced_lines'):
        out.append(_finding(
            'unpriced_lines', 'high',
            f"{len(facts['unpriced_lines'])} line(s) in this bid still cost $0.",
            'Placeholder costs make the margin a fiction in the flattering '
            'direction.'))

    if facts.get('upgrades_uncosted'):
        out.append(_finding(
            'upgrades_uncosted', 'medium',
            'An optional upgrade on offer has no cost entered: '
            + ', '.join(facts['upgrades_uncosted'][:4]) + '.',
            'An uncosted upgrade sits out of the margin entirely rather than '
            'inflating it, so the reported margin is missing that work.'))

    # ── Code and scope ─────────────────────────────────────────────────────
    vent = facts.get('vent') or {}
    if vent.get('exhaust_required') and \
            vent.get('exhaust_installed', 0) + 0.5 < vent['exhaust_required']:
        short = vent['exhaust_required'] - vent['exhaust_installed']
        out.append(_finding(
            'vent_exhaust_short', 'high',
            f"Exhaust ventilation is SHORT by {short:.0f} sq in "
            f"({vent['exhaust_installed']:.0f} installed against "
            f"{vent['exhaust_required']:.0f} required).",
            'This is what an inspector measures, and the crew builds what this '
            'scope says.'))
    if vent.get('intake_required') and \
            vent.get('intake_installed', 0) + 0.5 < vent['intake_required']:
        short = vent['intake_required'] - vent['intake_installed']
        out.append(_finding(
            'vent_intake_short', 'high',
            f"Intake ventilation is SHORT by {short:.0f} sq in.",
            'Exhaust without intake does not ventilate an attic; it pulls '
            'conditioned air out of the house.'))
    if vent.get('attic_area_assumed'):
        out.append(_finding(
            'attic_area_assumed', 'low',
            'Attic Area is blank, so ventilation is sized off roof squares.',
            'That is the sloped area, so it runs about 12% high on a 6/12 — it '
            'over-vents rather than under-vents, but it is a guess.'))

    # ── The claim, on an insurance job ─────────────────────────────────────
    if est_type == 'insurance' and not facts.get('has_measurements'):
        out.append(_finding(
            'no_measurements', 'high',
            'No measurement report imported.',
            'RoofR is the source of truth for quantities, and the Claim Check '
            'that finds a supplement compares against it. Without it the cost '
            'side is sized off nothing.'))
    if facts.get('carrier_review_lines'):
        out.append(_finding(
            'carrier_unclassified', 'medium',
            f"{facts['carrier_review_lines']} carrier line(s) are still "
            f"unclassified and counting toward the roof total.",
            'An unrecognised line counts as roof so the total never silently '
            'shrinks — confirm them before this drives a margin.'))

    # ── Can it even be sent, and will it read right ────────────────────────
    if facts.get('expired'):
        out.append(_finding(
            'expired', 'high',
            f"Pricing was only held until {facts.get('valid_until')}, so the "
            f"signature block is already withdrawn.",
            'Move the date before sending, or the customer opens a quote they '
            'cannot accept.'))
    elif facts.get('days_to_expiry') is not None and 0 <= facts['days_to_expiry'] <= 3:
        out.append(_finding(
            'expiring', 'medium',
            f"Pricing is only held for another {facts['days_to_expiry']} day(s).",
            'Homeowners take longer than that to decide.'))

    if not facts.get('customer_email'):
        out.append(_finding(
            'no_email', 'medium', 'No customer email on the estimate.',
            'The signed copy goes to that address, and without it the '
            'homeowner gets no receipt.'))

    if facts.get('company_content_missing'):
        out.append(_finding(
            'no_company_content', 'medium',
            'About Us, Warranty, Certifications and Reviews are empty.',
            'The proposal goes out with those sections blank — Settings fills '
            'them once for every estimate.'))

    jx = facts.get('jurisdiction') or {}
    if jx.get('name') and not jx.get('reviewed_at'):
        out.append(_finding(
            'jurisdiction_unapproved', 'low',
            f"{jx['name']}'s code profile has not been approved by a manager.",
            'It prints on the customer page as the code their city enforces, '
            'so a manager reads it before it ships.'))

    out.sort(key=lambda f: _RANK.get(f['severity'], 9))
    return out


SYSTEM = """You are a senior roofing estimator reviewing a colleague's estimate
before it is sent to a homeowner. You are the second pair of eyes a small
company cannot otherwise afford.

You are given facts that have ALREADY been computed by the estimating tool, and
a list of problems its own rules already found. Do not recompute anything. Do
not restate a finding the rules already made — the rep will see those anyway.

Your job is the things a rule cannot express: combinations, omissions, and
scopes that do not match the house they describe. For example — a tear-off with
no dump or haul-off line; a steep-slope job with no underlayment; a job in a
2021 IRC city with no ice & water on the eaves; ventilation that is technically
sufficient but only because an attic area was guessed; a scope that decks over
existing vents without replacing their capacity; a quantity that does not fit
the measurements given.

Rules:
- Report ONLY things you are confident are wrong or missing. A rep who gets
  five real findings acts on them; a rep who gets twenty guesses stops reading.
- Never state a number the facts did not give you. You are reading, not
  estimating. If something looks off but you cannot tell from what you were
  given, say what you would check rather than asserting a figure.
- Never comment on whether a PRICE is right. What a roof should sell for is
  between this company and its market.
- Write for a rep standing in a truck: what is wrong, and what to do about it.
- An empty list is a good answer and a common one.

Severity: high means do not send this as it stands; medium means fix it before
the customer asks; low means worth knowing."""

SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['findings'],
    'properties': {
        'findings': {
            'type': 'array',
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'required': ['severity', 'what', 'fix'],
                'properties': {
                    'severity': {'type': 'string', 'enum': list(SEVERITIES)},
                    'what': {'type': 'string'},
                    'fix': {'type': 'string'},
                },
            },
        },
    },
}


def ai_findings(facts, rule_findings, client=None):
    """What no rule covers. Returns [] rather than raising when unavailable."""
    if not available():
        return []
    client = client or anthropic.Anthropic()
    payload = {
        'estimate': facts,
        'already_found_by_rules': [f['what'] for f in rule_findings],
    }
    try:
        with client.beta.messages.stream(
            model=MODEL, max_tokens=16000,
            betas=['server-side-fallback-2026-07-01'], fallbacks='default',
            system=SYSTEM,
            output_config={'format': {'type': 'json_schema', 'schema': SCHEMA}},
            messages=[{'role': 'user',
                       'content': json.dumps(payload, default=str)[:180000]}],
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
    for f in (data.get('findings') or [])[:20]:
        sev = str(f.get('severity') or '').strip().lower()
        what = str(f.get('what') or '').strip()
        if sev not in SEVERITIES or not what:
            continue
        out.append({'code': 'reviewer', 'severity': sev, 'what': what[:400],
                    'fix': str(f.get('fix') or '').strip()[:400],
                    'source': 'reviewer'})
    return out


def run(facts, client=None):
    """Both layers. The rules always run; the reader is best-effort.

    A reader that is down, rate-limited or unconfigured must never cost the rep
    the rule findings — those are the ones worth acting on, and they are free.
    `reviewer_error` says so rather than the absence being silent, because a
    review that quietly half-ran reads exactly like a clean estimate.
    """
    rules = deterministic_findings(facts)
    result = {'findings': rules, 'reviewer_ran': False, 'reviewer_error': ''}
    if not available():
        return result
    try:
        extra = ai_findings(facts, rules, client=client)
    except ReviewError as e:
        result['reviewer_error'] = str(e)
        return result
    result['reviewer_ran'] = True
    result['findings'] = sorted(rules + extra,
                                key=lambda f: _RANK.get(f['severity'], 9))
    return result
