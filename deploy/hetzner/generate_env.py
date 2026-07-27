"""Create the first Hetzner production environment file without printing secrets."""

from __future__ import annotations

import os
from pathlib import Path
import secrets


TARGET = Path(__file__).with_name("env.production")


def token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def main() -> None:
    if TARGET.exists():
        raise SystemExit(f"Refusing to overwrite existing {TARGET}")

    secret_key = token(48)
    postgres_password = token()
    redis_password = token()

    content = f"""\
APP_IMAGE=school-reports:20260727
ENV_FILE=deploy/hetzner/env.production
LOCAL_HTTP_PORT=18000

ENV=production
DEBUG=False
SECRET_KEY={secret_key}
SITE_URL=https://tawtheeq-ksa.com
ALLOWED_HOSTS=app.tawtheeq-ksa.com,tawtheeq-ksa.com,www.tawtheeq-ksa.com
CSRF_TRUSTED_ORIGINS=https://app.tawtheeq-ksa.com,https://tawtheeq-ksa.com,https://www.tawtheeq-ksa.com
WEBAUTHN_RP_ID=tawtheeq-ksa.com
PRODUCTION_STRICT_MODE=True

POSTGRES_DB=school_reports
POSTGRES_USER=school_reports
POSTGRES_PASSWORD={postgres_password}
DATABASE_URL=postgresql://school_reports:{postgres_password}@postgres:5432/school_reports
DB_SSL=False
CONN_MAX_AGE=600

REDIS_PASSWORD={redis_password}
REDIS_URL=redis://:{redis_password}@redis:6379/0
REDIS_MAXMEMORY=192mb

R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=
R2_ENDPOINT_URL=
AWS_S3_REGION_NAME=nbg1
MEDIA_PUBLIC_ACCESS_ENABLED=False
AWS_QUERYSTRING_AUTH=True

WEB_CONCURRENCY=1
GUNICORN_THREADS=2
GUNICORN_TIMEOUT=120
GUNICORN_KEEPALIVE=5
GUNICORN_MAX_REQUESTS=800
GUNICORN_MAX_REQUESTS_JITTER=80
CELERY_CORE_CONCURRENCY=1
CELERY_MEDIA_CONCURRENCY=1

NOTIFICATIONS_LOCAL_FALLBACK_ENABLED=False
PASSWORD_CHANGE_EMAIL_ENABLED=False
DAILY_MANAGER_REPORT_ENABLED=True
DAILY_MANAGER_REPORT_INAPP_ENABLED=True
DAILY_MANAGER_REPORT_EMAIL_ENABLED=False
DAILY_MANAGER_REPORT_WHATSAPP_ENABLED=False
DAILY_MANAGER_REPORT_HOUR=14
DAILY_MANAGER_REPORT_MINUTE=0

HEALTHZ_CHECK_CHANNELS=False
LOG_LEVEL=INFO
SECURITY_CONTACT_EMAIL=support@tawtheeq-ksa.com
"""

    fd = os.open(TARGET, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


if __name__ == "__main__":
    main()
