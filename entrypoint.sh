#!/bin/sh
set -e
python manage.py migrate --noinput
python manage.py seed_bam
python manage.py ensure_supervisor
python manage.py collectstatic --noinput
exec gunicorn bam_supervise.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120
