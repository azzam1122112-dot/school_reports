web: python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn config.asgi:application -k uvicorn.workers.UvicornWorker --log-file -
worker: celery -A config worker --loglevel=info --pool=solo
beat: celery -A config beat --loglevel=info
