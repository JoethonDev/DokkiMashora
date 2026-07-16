# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.prod.txt ./
RUN pip install --no-cache-dir -r requirements.prod.txt

RUN addgroup --system app \
    && adduser --system --ingroup app app

COPY --chown=app:app . .

RUN install -d -o app -g app /app/media /app/staticfiles

USER app

EXPOSE 10000

CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py collectstatic --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-10000} --workers ${GUNICORN_WORKERS:-3} --threads ${GUNICORN_THREADS:-2} --timeout ${GUNICORN_TIMEOUT:-120} --access-logfile - --error-logfile -"]
