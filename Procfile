# One service serves all three tools. portal/wsgi.py mounts the canvasser, the
# sales CRM, and the estimator behind one login. The old --chdir estimator form
# served the estimator alone at the root.
#
# The migration runs first so the very first boot after the cutover has the
# team's accounts. --if-empty makes it a no-op on every later restart (it must
# not resurrect a user an admin deleted), and `;` rather than `&&` so a failed
# migration can never keep the site down — worst case nobody is enrolled yet and
# PORTAL_SIGNUP_CODE bootstraps the first admin.
web: python -m portal.migrate_users --apply --if-empty; gunicorn portal.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 60
