"""Every JSON file the estimator keeps on the volume is in the backup.

The volume is the only copy of these. Four were left out of the backup list,
company_content.json among them, which is the About Us, Warranty,
Certifications and Reviews copy on every proposal and is not in git either. A
rebuilt volume would have shipped proposals without them and lost every
customer note, with no backup to restore from.

The list is checked against the code rather than against itself: any new
`os.path.join(DATA_DIR, '<name>.json')` in app.py has to appear in
_BACKUP_DATA_FILES, so a file added next month is covered on arrival.
"""
import io
import json
import os
import re
import zipfile

import app as A

HERE = os.path.dirname(os.path.abspath(__file__))
APP_SRC = open(os.path.join(HERE, '..', 'app.py'), encoding='utf-8').read()


def test_every_data_file_the_estimator_writes_is_backed_up():
    on_volume = set(re.findall(r"os\.path\.join\(DATA_DIR,\s*'([^'/]+\.json)'\)", APP_SRC))
    assert on_volume, 'found no DATA_DIR json files; has the pattern changed?'
    missing = sorted(on_volume - set(A._BACKUP_DATA_FILES))
    assert not missing, f'on the volume but not in the backup: {missing}'


def test_the_proposal_copy_and_the_customer_notes_are_in_the_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(A, 'DATA_DIR', str(tmp_path))
    for name, body in (('company_content.json', {'about_us': 'Family owned'}),
                       ('customer_notes.json', {'ada lovelace': 'Gate code 1234'})):
        (tmp_path / name).write_text(json.dumps(body), encoding='utf-8')
    with zipfile.ZipFile(io.BytesIO(A._build_backup_zip(include_uploads=False))) as zf:
        names = set(zf.namelist())
        assert {'company_content.json', 'customer_notes.json'} <= names
        assert json.loads(zf.read('customer_notes.json'))['ada lovelace'] == 'Gate code 1234'
