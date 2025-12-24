web: python manage.py migrate --noinput && gunicorn config.wsgi:application --log-file -
worker: celery -A config worker --loglevel=info --pool=solo
