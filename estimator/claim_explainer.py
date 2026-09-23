"""The homeowner's own claim, explained back to them in plain English.

We parse every line of an Xactimate or Symbility estimate — RCV, ACV,
depreciation split recoverable from non-recoverable, deductible, O&P, tax per
authority. After an import we understand that homeowner's claim in more detail
than the homeowner does, and we have never once told them any of it.

The question this answers is the one every insurance customer asks and almost
nobody answers well: **"why is the check smaller than the estimate?"** The
answer is recoverable depreciation, and a homeowner who does not understand it
concludes either that their carrier is cheating them or that we are.

One rule governs everything here:

**Explain, never recalculate.** Every figure on the sheet is one the carrier
already wrote, copied across unchanged. The narrative is allowed to *name* a
number and say what it means; it is not allowed to produce one. A homeowner may
repeat any of these to their adjuster, so a figure this document invented would
be a figure we invented — and `_insurance_rcv_total` exists precisely because
the claim total is the carrier's own and must never be inflated by anything we
added. The arithmetic that does appear (ACV plus depreciation equals RCV) is
the carrier's own identity, checked rather than performed: `reconciles()` says
whether their numbers add up, and when they do not the sheet says so instead of
quietly papering over it.

Without `ANTHROPIC_API_KEY` the sheet still builds. `fallback_narrative()` is a
plain template over the same figures — less warm, equally correct — because a
homeowner waiting on an explanation should not be held up by an API key.
"""
import os

try:
    import anthropic
except ImportError:
    anthropic = None


MODEL = os.environ.get('CLAIM_EXPLAINER_MODEL', 'claude-opus-5')


class ExplainError(Exception):
    """A narrative that could not be written. The template prints instead."""


def available():
    return anthropic is not None and bool(os.environ.get('ANTHROPIC_API_KEY', '').strip())


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def claim_facts(est):
    """The carrier's own figures, copied across. None where they did not say.

    A missing figure stays None rather than becoming 0: "your carrier withheld
    $0.00 of depreciation" is a sentence about a claim nobody imported, and a
    homeowner would read it as a fact about theirs.
    """
    claim = est.get('insurance_claim') or {}
    td = ((est.get('trades') or {}).get('insurance') or {})
    return {
        'carrier':      (td.get('carrier') or '').strip(),
        'claim_number': (td.get('claim_number') or '').strip(),
        'date_of_loss': (claim.get('date_of_loss') or '').strip(),
        'type_of_loss': (claim.get('type_of_loss') or '').strip(),
        'rcv_total':    _num(claim.get('rcv_total')),
        'acv_total':    _num(claim.get('acv_total')),
        'depreciation': _num(claim.get('recoverable_depreciation')),
        'deductible':   _num(claim.get('deductible')),
        'net_claim':    _num(claim.get('net_claim')),
        'net_claim_if_recovered': _num(claim.get('net_claim_if_recovered')),
        'paid_when_incurred':     _num(claim.get('paid_when_incurred')),
    }


def has_enough(facts):
    """Whether there is a claim here worth explaining.

    RCV plus one of depreciation or deductible is the minimum that supports the
    only sentence that matters. Below that the sheet would be a logo and a
    paragraph of generalities, which is worse than not offering it.
    """
    return facts.get('rcv_total') is not None and (
        facts.get('depreciation') is not None or facts.get('deductible') is not None)


def reconciles(facts, tolerance=1.00):
    """Whether ACV + depreciation = RCV, by the carrier's own numbers.

    Returns None when a figure is missing. False is not an error and does not
    stop the sheet — carriers legitimately carry non-recoverable depreciation
    and pay-when-incurred lines that this identity does not see. It means the
    document must not claim the three figures tie out, because a homeowner who
    checks the subtraction and finds it wrong stops believing the rest.
    """
    rcv, acv, dep = facts.get('rcv_total'), facts.get('acv_total'), facts.get('depreciation')
    if rcv is None or acv is None or dep is None:
        return None
    return abs(rcv - (acv + dep)) <= tolerance


SYSTEM = """You are writing a short plain-English explanation of a homeowner's
own insurance claim, to be printed on a single page and handed to them by their
roofing contractor.

You are given figures the CARRIER produced. Your job is to explain what they
mean, warmly and without condescension, to someone who has never read an
insurance estimate before.

Absolute rules:

1. NEVER produce a number that was not given to you. Do not add, subtract,
   total or estimate anything. You may name a figure you were given and say
   what it is for. A homeowner may repeat these numbers to their adjuster.
2. If a figure is missing, do not mention it and do not guess at it.
3. Never promise what the carrier will do, never give legal or tax advice, and
   never suggest the carrier has treated them unfairly. You are explaining a
   document, not advocating.
4. Do not mention the contractor's pricing, margin, or what the job costs to
   build. This page is about their claim.

The single most important thing to explain, when there is recoverable
depreciation, is why the first check is smaller than the total: the carrier
holds part of the money back until the work is actually completed, and releases
it afterwards. Homeowners routinely believe this is a shortfall or a mistake.
Say plainly that it is neither and that it is normal.

Write 3 to 5 short paragraphs. No headings, no bullet points, no greeting, no
sign-off — the page supplies those. Second person. Warm, calm, specific."""


def narrative(facts, client=None):
    """The plain-English explanation. Raises; callers fall back to template."""
    if not available():
        raise ExplainError('Claim narrative needs ANTHROPIC_API_KEY.')
    import json
    given = {k: v for k, v in facts.items() if v not in (None, '')}
    client = client or anthropic.Anthropic()
    try:
        with client.beta.messages.stream(
            model=MODEL, max_tokens=4000,
            betas=['server-side-fallback-2026-07-01'], fallbacks='default',
            system=SYSTEM,
            messages=[{'role': 'user', 'content': json.dumps(given)}],
        ) as stream:
            msg = stream.get_final_message()
    except Exception as e:
        raise ExplainError(f'Could not write the explanation: {e}')
    if msg.stop_reason == 'refusal':
        raise ExplainError('The explanation was declined.')
    out = ''.join(b.text for b in msg.content if b.type == 'text').strip()
    if not out:
        raise ExplainError('The explanation came back empty.')
    return out


def _money(v):
    return f'${v:,.2f}' if v is not None else ''


def fallback_narrative(facts):
    """The same explanation as a template. Correct, just less warm.

    A homeowner waiting to understand their claim should not be held up by an
    API key, and this is the paragraph that answers their actual question.
    """
    paras = []
    rcv, acv = facts.get('rcv_total'), facts.get('acv_total')
    dep, ded = facts.get('depreciation'), facts.get('deductible')
    carrier = facts.get('carrier') or 'Your carrier'

    if rcv is not None:
        paras.append(
            f'{carrier} has approved a total of {_money(rcv)} to repair the '
            f'damage to your home. That figure is called the Replacement Cost '
            f'Value — what it costs to put your roof back the way it was.')
    if dep:
        first = _money(acv) if acv is not None else 'the first payment'
        paras.append(
            f'Your first check will be smaller than that, and that is normal. '
            f'Insurance companies hold back part of the money until the work is '
            f'actually finished — {_money(dep)} in your case. That held-back '
            f'amount is called recoverable depreciation, and you get it. It is '
            f'released after the roof is built and your carrier receives the '
            f'final invoice, which we send them.'
            + (f' The first payment is {first}.' if acv is not None else ''))
    if ded is not None:
        paras.append(
            f'Your deductible is {_money(ded)}. That is the part of the repair '
            f'you are responsible for under your policy, and it is the main '
            f'thing you pay out of pocket. By Colorado law we cannot waive it, '
            f'absorb it, or pay it for you — any contractor offering to do that '
            f'is offering to break the law on your behalf.')
    paras.append(
        'We handle the paperwork with your carrier from here, including the '
        'final invoice that releases the rest of your money. If anything on '
        'this page does not match what you were sent, tell us and we will '
        'look at it with you.')
    return '\n\n'.join(paras)


def explanation(facts, client=None):
    """(text, source). Never raises — the template is always available."""
    try:
        return narrative(facts, client=client), 'written'
    except ExplainError as e:
        print(f'[claim-explainer] using the template: {e}')
        return fallback_narrative(facts), 'template'
