#!/bin/sh
set -e
umask 027

mkdir -p /app/media /app/staticfiles
chown -R bamapp:bamapp /app/media /app/staticfiles

python manage.py migrate --noinput
python manage.py seed_bam
python manage.py ensure_supervisor
python manage.py protect_sensitive_data
python manage.py collectstatic --noinput

chown -R bamapp:bamapp /app/media /app/staticfiles
exec gosu bamapp gunicorn bam_supervise.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120
