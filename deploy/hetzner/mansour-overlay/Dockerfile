ARG BASE_IMAGE
FROM ${BASE_IMAGE}

COPY reports/mansour_assistant.py /app/reports/mansour_assistant.py
COPY reports/mansour_knowledge.py /app/reports/mansour_knowledge.py
COPY reports/views/mansour.py /app/reports/views/mansour.py
COPY reports/templates/reports/partials/mansour_assistant.html /app/reports/templates/reports/partials/mansour_assistant.html
COPY static/css/mansour-assistant.css /app/static/css/mansour-assistant.css
COPY static/js/mansour-assistant.js /app/static/js/mansour-assistant.js
COPY static/img/mansour-avatar-v2.png /app/static/img/mansour-avatar-v2.png
COPY deploy/hetzner/mansour-overlay/apply_overlay.py /tmp/apply_mansour_overlay.py

RUN python /tmp/apply_mansour_overlay.py && rm /tmp/apply_mansour_overlay.py
