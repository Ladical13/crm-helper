"""The app-switcher bar's layout contract with the three host apps.

shell.js injects a fixed bar at the top of Canvass, Pipeline and Estimate, and
publishes `--p1-shell-h` so each app can push its own fixed header down out of
the way. That variable is the whole contract, and it is easy to get subtly
wrong: the bar pads itself down past the notch, and with box-sizing:content-box
that padding is height *on top of* its declared height.

It was wrong. `--p1-shell-h` was a flat 44px while on a notched iPhone the bar
rendered 44 + 47 = 91px, so 47px of every app's header sat underneath it â€” on
exactly the phones the reps carry, in installed-PWA mode where the inset is
non-zero. These tests pin the arithmetic so it cannot drift back.
"""
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(BASE, 'static')
REPO = os.path.dirname(BASE)


def _read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


SHELL_CSS = _read(os.path.join(STATIC, 'shell.css'))
SHELL_JS = _read(os.path.join(STATIC, 'shell.js'))


def test_shell_height_var_includes_the_safe_area_inset():
    """--p1-shell-h is what host apps offset by, so it must be the bar's TOTAL
    occupied height, inset included â€” not just its content box."""
    m = re.search(r'--p1-shell-h:\s*([^;]+);', SHELL_CSS)
    assert m, '--p1-shell-h is no longer declared in shell.css'
    value = m.group(1)
    assert 'safe-area-inset-top' in value, (
        f'--p1-shell-h is {value!r} â€” it omits the safe-area inset, so every '
        'app will offset its header too little on a notched phone.'
    )


def test_the_bar_sizes_itself_from_the_base_not_the_total():
    """If #p1-shell took its height from --p1-shell-h (which now includes the
    inset) *and* kept padding-top, the inset would be counted twice."""
    block = re.search(r'#p1-shell\s*\{(.*?)\}', SHELL_CSS, re.S)
    assert block, '#p1-shell rule not found'
    body = block.group(1)
    assert 'height: var(--p1-shell-base)' in body
    assert 'padding-top: env(safe-area-inset-top' in body


def test_shell_js_publishes_the_same_expression_as_the_stylesheet():
    """shell.js sets --p1-shell-h as an inline style on <html>, which beats the
    :root rule in shell.css. A bare pixel value there silently overrides the
    correct stylesheet default â€” that is the exact shape of the original bug."""
    m = re.search(r"setProperty\(\s*'--p1-shell-h',\s*([^)]*\)?[^;]*)\);", SHELL_JS)
    assert m, 'shell.js no longer sets --p1-shell-h'
    expr = m.group(1)
    assert 'safe-area-inset-top' in expr, (
        f'shell.js sets --p1-shell-h to {expr.strip()!r}, dropping the inset '
        'and overriding the correct value in shell.css.'
    )
    # Both sides must agree on the base, or the bar and the offset disagree.
    base_js = re.search(r'var SHELL_H\s*=\s*(\d+)', SHELL_JS)
    base_css = re.search(r'--p1-shell-base:\s*(\d+)px', SHELL_CSS)
    assert base_js and base_css
    assert base_js.group(1) == base_css.group(1), (
        f'shell.js SHELL_H={base_js.group(1)} but shell.css '
        f'--p1-shell-base={base_css.group(1)}px'
    )


def test_every_host_app_offsets_by_the_shell_variable():
    """Each app is responsible for making room. If one stops referencing the
    variable its header goes back under the bar."""
    for app, css in (('canvasser', 'canvasser/static/style.css'),
                     ('salescrm', 'salescrm/static/style.css'),
                     ('estimator', 'estimator/static/style.css')):
        text = _read(os.path.join(REPO, css))
        assert 'var(--p1-shell-h)' in text, f'{app} no longer offsets for the shell bar'
        assert '.p1-has-shell' in text, f'{app} offset is not scoped to .p1-has-shell'


def test_bar_links_meet_the_44px_touch_minimum():
    """The bar sits at the top of every screen on a phone and had the smallest
    tap targets in the system (25-27px tall). The visible pills stay small; a
    transparent ::after grows the hit area."""
    assert re.search(r'#p1-shell a::after\s*\{', SHELL_CSS), (
        'the touch-target expander on #p1-shell links is gone'
    )
    block = re.search(r'#p1-shell a::after\s*\{(.*?)\}', SHELL_CSS, re.S).group(1)
    assert 'height: 44px' in block


def test_print_collapses_the_offset():
    """Fixed headers repeat on every printed page; the estimator PDF once had
    the switcher bar banner across all of them. Hiding the bar is only half â€”
    the offset the apps add has to go too."""
    printed = re.search(r'@media print\s*\{(.*?)\n\}', SHELL_CSS, re.S)
    assert printed, 'the @media print block is gone'
    assert '#p1-shell { display: none !important; }' in printed.group(1)
    assert '--p1-shell-h: 0px !important' in printed.group(1)
