"""The bundle editor's What's Included wording: editable in place, and honest.

Two faults, found together:
  * the list was read-only, so rewording one bullet meant leaving the bundle
    for the Products tab and finding the product's 💬 among ~50 rows;
  * the Price Book preview RESTATED the bullet rule instead of calling it, and
    had drifted — it dropped every hidden product, so a labor line's promise
    ("Installed by Project One crews") was on the customer's card and missing
    from the preview the manager was editing against.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(os.path.dirname(HERE), 'static')
RUNNER = os.path.join(HERE, 'bundle_runner.js')


def _app_js():
    return open(os.path.join(STATIC, 'app.js'), encoding='utf-8').read()


def _fn(src, name):
    m = re.search(r'^function ' + name + r'\s*\(.*?^\}', src, flags=re.S | re.M)
    assert m, f'{name} not found in app.js'
    return m.group(0)


def test_preview_and_estimate_share_one_rule():
    src = _app_js()
    assert 'featuresFromCatalog(' in _fn(src, 'bundleFeatures')
    assert 'featuresFromCatalog(' in _fn(src, 'pbBundleFeatures')
    for name in ('bundleFeatures', 'pbBundleFeatures'):
        assert 'customer_visible' not in _fn(src, name), (
            f'{name} restates the bullet rule again — it drifts')


def test_each_product_in_the_bundle_gets_a_wording_box():
    src = _app_js()
    row = _fn(src, 'pbRenderWordingRow')
    assert '<textarea' in row and 'pbSetProductBullets(' in row
    assert 'pbSetProductSilence(' in row
    assert 'pbRenderWordingRow(' in _fn(src, 'pbRenderBundleFeaturePreview')


def test_wording_list_has_no_scroll_box_of_its_own():
    css = open(os.path.join(STATIC, 'style.css'), encoding='utf-8').read()
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    bodies = [m.group(2) for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', css)
              if re.search(r'\.pb-(bundle-feat-preview|wording-list)(?![\w-])', m.group(1))]
    assert bodies
    for body in bodies:
        assert not re.search(r'max-height|overflow', body)


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_hidden_product_with_wording_still_makes_the_card(tmp_path):
    book = {
        'roofing_catalog': [
            {'id': 'm', 'name': 'Panels', 'bullets': ['26ga panels']},
            {'id': 'lab', 'name': 'Install Labor', 'customer_visible': False,
             'bullets': ['Installed by Project One crews']},
            {'id': 'fee', 'name': 'Overhead', 'customer_visible': False},
            {'id': 'dump', 'name': 'Dumpster', 'bullets': []},
            {'id': 'trim', 'name': 'Rake Trim'},
        ],
        'roofing_bundles': [{'id': 'b', 'name': 'PBR',
                             'product_ids': ['m', 'lab', 'fee', 'dump', 'trim'],
                             'extra_features': ['5-year warranty', '26ga panels']}],
    }
    scenario, out = tmp_path / 's.json', tmp_path / 'o.json'
    scenario.write_text(json.dumps({'priceBook': book, 'estimate': {'trades': {}},
                                    'ops': [{'op': 'features', 'trade': 'roofing', 'id': 'b'}]}),
                        encoding='utf-8')
    r = subprocess.run(['node', RUNNER, str(scenario), str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text(encoding='utf-8'))['_probe'] == [
        '26ga panels', 'Installed by Project One crews', 'Rake Trim', '5-year warranty']
