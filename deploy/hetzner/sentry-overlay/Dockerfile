ARG BASE_IMAGE=school-reports:9a7da9b1
FROM ${BASE_IMAGE}

USER root

RUN pip install --no-cache-dir "sentry-sdk[django]==2.66.1"

COPY sentry_runtime.py /app/config/sentry_runtime.py
RUN printf '\nfrom config.sentry_runtime import configure_sentry\nconfigure_sentry()\n' >> /app/config/settings.py
