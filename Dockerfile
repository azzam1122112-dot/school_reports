# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Environment
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    SERVICE_TYPE=web

# Set work directory
WORKDIR /app

# Install system dependencies required for WeasyPrint and image handling
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    python3-pip \
    python3-setuptools \
    python3-wheel \
    python3-cffi \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-xlib-2.0-0 \
    libffi-dev \
    shared-mime-info \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . /app/

# Expose port (Render sets PORT automatically)
EXPOSE 10000

# Run service based on SERVICE_TYPE or START_CMD override
CMD ["sh", "-c", "\
set -e; \
if [ -n \"$START_CMD\" ]; then \
    echo \"[boot] Using START_CMD override\"; \
    exec sh -c \"$START_CMD\"; \
elif [ \"$SERVICE_TYPE\" = \"worker_default\" ]; then \
    echo \"[boot] Starting Celery worker: default\"; \
    exec celery -A config worker -Q default --concurrency=${CELERY_DEFAULT_CONCURRENCY:-2}; \
elif [ \"$SERVICE_TYPE\" = \"worker_notifications\" ]; then \
    echo \"[boot] Starting Celery worker: notifications\"; \
    exec celery -A config worker -Q notifications --concurrency=${CELERY_NOTIFICATIONS_CONCURRENCY:-2}; \
elif [ \"$SERVICE_TYPE\" = \"worker_images\" ]; then \
    echo \"[boot] Starting Celery worker: images\"; \
    exec celery -A config worker -Q images --concurrency=${CELERY_IMAGES_CONCURRENCY:-1}; \
elif [ \"$SERVICE_TYPE\" = \"worker_periodic\" ]; then \
    echo \"[boot] Starting Celery worker: periodic\"; \
    exec celery -A config worker -Q periodic --concurrency=${CELERY_PERIODIC_CONCURRENCY:-1}; \
elif [ \"$SERVICE_TYPE\" = \"beat\" ]; then \
    echo \"[boot] Starting Celery beat\"; \
    exec celery -A config beat; \
else \
    echo \"[boot] Starting web service\"; \
    python manage.py migrate --noinput; \
    python manage.py collectstatic --noinput; \
    exec gunicorn config.asgi:application \
        --bind 0.0.0.0:${PORT:-10000} \
        -k uvicorn.workers.UvicornWorker \
        --workers ${WEB_CONCURRENCY:-3} \
        --threads ${GUNICORN_THREADS:-2} \
        --timeout ${GUNICORN_TIMEOUT:-120} \
        --keep-alive ${GUNICORN_KEEPALIVE:-5} \
        --max-requests ${GUNICORN_MAX_REQUESTS:-2000} \
        --max-requests-jitter ${GUNICORN_MAX_REQUESTS_JITTER:-200}; \
fi"]