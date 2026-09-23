"""The Price Book bundle picker must not scroll on its own.

It was a scroll box (max-height: 46vh; overflow-y: auto) inside the modal body,
which is itself the scroller. On a tall monitor 46vh came out a row short of the
chips it held: the last row sat clipped beneath a box that looked finished, its
scrollbar thumb was nearly full length, and the wheel scrolled the modal instead
— so the products at the end of the catalog could not be reached or ticked.
"""
import os
import re

CSS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'static', 'style.css')


def _picker_bodies():
    css = re.sub(r'/\*.*?\*/', '', open(CSS, encoding='utf-8').read(), flags=re.S)
    return [m.group(2) for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', css)
            if re.search(r'\.pb-bundle-picker(?![\w-])', m.group(1))]


def test_there_is_a_picker_rule_to_check():
    assert _picker_bodies()


def test_picker_has_no_scroll_box_of_its_own():
    for body in _picker_bodies():
        assert not re.search(r'max-height|overflow', body), (
            '.pb-bundle-picker scrolls inside the scrolling modal body again — '
            'the last row of products ends up clipped out of reach')
