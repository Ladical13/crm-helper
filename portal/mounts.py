"""The three apps and where they hang off the origin.

Single source of truth for the mount prefixes: portal.wsgi builds the
DispatcherMiddleware map from this, the launcher renders cards from it, and
the app-switcher bar renders its pills from it. Change a prefix here and all
three follow.

Prefixes are also baked into each app's front end (fetch paths, service worker
scope, static hrefs), so changing one is not a one-file edit — grep the app's
static/ directory too.
"""

MOUNTS = [
    {
        'key':    'canvass',
        'prefix': '/canvass',
        'label':  'Canvass',
        'icon':   '📍',
        'blurb':  'Knock the door, drop the pin, check the hail.',
        'dir':    'canvasser',
        'accent': '#10B981',
    },
    {
        'key':    'crm',
        'prefix': '/crm',
        'label':  'Pipeline',
        'icon':   '📋',
        'blurb':  'Work the lead — stages, tasks, follow-ups.',
        'dir':    'salescrm',
        'accent': '#F97316',
    },
    {
        'key':    'estimate',
        'prefix': '/estimate',
        'label':  'Estimate',
        'icon':   '🏠',
        'blurb':  'Measure, price, present, sign.',
        'dir':    'estimator',
        'accent': '#00A8B5',
    },
]

BY_KEY = {m['key']: m for m in MOUNTS}
PREFIXES = [m['prefix'] for m in MOUNTS]
