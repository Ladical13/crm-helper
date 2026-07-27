"""Project One portal — one login and one origin for the three rep tools.

The Estimator, the Canvasser, and the Sales CRM stay three independent Flask
apps. This package adds the layer that makes them feel like one product:

    portal.session  one identical cookie config, shared by all four apps
    portal.users    the single user store (portal.db) all four read
    portal.app      the login page, the launcher, and user administration
    portal.wsgi     the DispatcherMiddleware mount map gunicorn serves

The trick that keeps the merge small: all four apps share SESSION_SECRET, and
Flask's session cookie path is '/' regardless of SCRIPT_NAME. On one origin
that means the four apps read the *same* session dict, so one login writes
every key name the three existing auth guards look for and none of them need
to change.
"""
