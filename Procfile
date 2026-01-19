web: python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn config.wsgi:application --log-file -
worker: celery -A config worker --loglevel=info --pool=solo
beat: celery -A config beat --loglevel=info
