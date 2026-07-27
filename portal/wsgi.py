"""The WSGI entry point gunicorn serves. One process, four Flask apps.

    gunicorn portal.wsgi:application

`DispatcherMiddleware` routes by URL prefix before any app sees the request, so
each of the three apps keeps every one of its routes registered at its own
root. That is the whole reason this merge did not require renaming ~130 routes:
all three apps collide at `/`, `/static/*`, `/api/me`, `/api/users`,
`/api/config`, `/api/invites`, `/health` and more, and prefix mounting makes
every one of those collisions disappear without touching a single @app.route.

    /            portal    login, launcher, user admin, compat redirects
    /canvass/*   canvasser
    /crm/*       salescrm
    /estimate/*  estimator
"""
import importlib.util
import os
import sys

from werkzeug.middleware.dispatcher import DispatcherMiddleware

from portal import session as psession
from portal.app import app as portal_app
from portal.mounts import MOUNTS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_app(module_name, app_dir):
    """Import <app_dir>/app.py under a unique module name and return its `app`.

    Loaded by file path rather than `import app` because all three modules are
    literally named `app` and would collide in sys.modules. The app's own
    directory is appended to sys.path first so its sibling imports resolve —
    estimator/app.py imports permit_coords at module level.
    """
    directory = os.path.join(REPO_ROOT, app_dir)
    if directory not in sys.path:
        # Appended, not inserted: these directories must never shadow stdlib
        # or site-packages modules.
        sys.path.append(directory)

    spec = importlib.util.spec_from_file_location(
        module_name, os.path.join(directory, 'app.py'))
    module = importlib.util.module_from_spec(spec)
    # Registered before exec_module because Flask resolves an app's root_path
    # (and therefore its static/template folders) from sys.modules[__name__].
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.app


def build():
    """Return the composed WSGI application."""
    mounts = {}
    for m in MOUNTS:
        sub = load_app('p1_%s_app' % m['key'], m['dir'])
        # Re-apply the shared cookie config. The four apps share one cookie and
        # each re-saves it whenever it touches session, so a mismatched Secure
        # or SameSite flag in any one of them logs the rep out at random.
        psession.configure(sub, max_content_length=sub.config.get('MAX_CONTENT_LENGTH'))
        mounts[m['prefix']] = sub
    return DispatcherMiddleware(portal_app, mounts)


application = build()


if __name__ == '__main__':
    # Local dev: `python -m portal.wsgi`. `flask run` cannot serve this —
    # DispatcherMiddleware is a plain WSGI callable, not a Flask app.
    from werkzeug.serving import run_simple
    port = int(os.environ.get('PORT', 5010))
    run_simple('0.0.0.0', port, application,
               use_reloader=False, use_debugger=True, threaded=True)
