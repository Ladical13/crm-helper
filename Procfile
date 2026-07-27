# One service serves all three tools. portal/wsgi.py mounts the canvasser, the
# sales CRM, and the estimator behind one login. The old --chdir estimator form
# served the estimator alone at the root.
web: gunicorn portal.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 60
