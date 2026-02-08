web: python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn config.asgi:application --bind 0.0.0.0:${PORT:-8000} -k uvicorn.workers.UvicornWorker --log-file -
worker: celery -A config worker --loglevel=info --pool=solo
beat: celery -A config beat --loglevel=info
