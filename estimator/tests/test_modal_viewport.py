"""Every modal container sizes with a vh/dvh pair, dvh second.

The Safari toolbar overlaps the bottom of `vh`, and the bottom of a modal is
where its Save / Confirm / Delete button lives. CLAUDE.md has carried this rule
since the CRM's modal buttons, the canvasser's pin Save and the login card all
ended up below the fold.

The modals had the fix and it was in the wrong place: the pair lived only inside
`@media (max-width: 767px)`. A phone was covered; an iPad was not — and the iPad
is where estimates get written, on a table full of numeric inputs. That is the
same width-vs-pointer trap CLAUDE.md documents twice for touch targets and focus
zoom, arriving a third time through a height query. `dvh` costs nothing on a
desktop (it equals `vh` with no dynamic toolbar), so it belongs on the base rule.
"""
import os
import re

import pytest

CSS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'static', 'style.css')

# Selectors that size a modal's outer box. `.xact-modal-body` is here because
# `.xact-modal-box` sets no height at all, so the body is what decides how tall
# that modal gets.
_MODAL_SIZING_RE = re.compile(r'modal-box|xact-modal-body')
_RULE_RE = re.compile(r'([^{}]+)\{([^{}]*)\}')
_VH_RE = re.compile(r'(max-height|height)\s*:\s*[^;]*?\d+vh')
_DVH_RE = re.compile(r'(max-height|height)\s*:\s*[^;]*?\d+dvh')


def _modal_rules():
    """(selector, body) for every rule that sizes a modal container."""
    css = open(CSS, encoding='utf-8').read()
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)   # comments quote vh on purpose
    out = []
    for m in _RULE_RE.finditer(css):
        sel, body = m.group(1).strip(), m.group(2)
        if _MODAL_SIZING_RE.search(sel) and _VH_RE.search(body):
            out.append((sel, body))
    return out


def test_there_are_modal_rules_to_check():
    """A refactor that renames every *-modal-box would otherwise make this whole
    file pass by finding nothing."""
    assert len(_modal_rules()) >= 8


@pytest.mark.parametrize('sel,body', _modal_rules(),
                         ids=lambda v: v if isinstance(v, str) and '{' not in v else '')
def test_every_modal_that_sizes_in_vh_also_sizes_in_dvh(sel, body):
    assert _DVH_RE.search(body), (
        f'{sel} caps its height in vh with no dvh fallback — its bottom row of '
        f'buttons sits under the Safari toolbar')


@pytest.mark.parametrize('sel,body', _modal_rules(),
                         ids=lambda v: v if isinstance(v, str) and '{' not in v else '')
def test_dvh_comes_second_so_it_wins(sel, body):
    """Order is the whole mechanism: a browser without dvh keeps the vh line, a
    browser with it takes the later one. Reversed, the fix does nothing."""
    assert _VH_RE.search(body).start() < _DVH_RE.search(body).start(), \
        f'{sel} declares dvh before vh, so vh overrides it'


def test_the_base_modal_rule_is_not_gated_behind_a_width_query():
    """This is the actual bug: the pair existed, inside @media (max-width:767px),
    so every touch device wider than a phone was left on vh alone."""
    css = open(CSS, encoding='utf-8').read()
    i = css.index('.modal-box { background: #fff;')
    # Walk back and confirm no unclosed @media wraps the base rule.
    before = css[:i]
    assert before.count('{') - before.count('}') == 0, \
        '.modal-box base rule sits inside a media query'
    body = css[i:css.index('}', i)]
    assert '80dvh' in body, 'the base .modal-box rule has no dvh fallback'
