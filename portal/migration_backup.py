"""Keep a consistent local snapshot before an additive schema upgrade."""
import os
import sqlite3
import tempfile


def before_upgrade(path, label):
    if not os.path.isfile(path) or not os.path.getsize(path):
        return
    folder = os.path.join(os.path.dirname(path), 'pre-migration')
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, f'{os.path.basename(path)}-{label}.db')
    if os.path.exists(target):
        return
    fd, temporary = tempfile.mkstemp(suffix='.db', dir=folder)
    os.close(fd)
    try:
        src = sqlite3.connect(path)
        dst = sqlite3.connect(temporary)
        try:
            src.backup(dst)
            dst.execute('PRAGMA journal_mode=DELETE')
        finally:
            dst.close()
            src.close()
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
