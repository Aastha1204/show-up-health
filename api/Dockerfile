# ShowUp API. Build from the REPOSITORY ROOT (it needs /db and /templates too):
#   docker build -f api/Dockerfile -t showup-api .
# Layout inside the image mirrors the repo, because the app finds /templates and /db/migrations relative to itself.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PORT=8000
WORKDIR /srv

COPY api/requirements.txt api/requirements.txt
RUN pip install -r api/requirements.txt

COPY api/app api/app
COPY api/run.py api/run.py
COPY db db
COPY templates templates

RUN useradd --create-home --uid 10001 showup && chown -R showup /srv
USER showup
WORKDIR /srv/api

# Apply any new migrations (safe to repeat; concurrent starts queue), then serve.
# Run exactly ONE replica: the 30-second reminder scheduler lives inside this process.
CMD ["sh", "-c", "python -m app.migrate && python run.py --host 0.0.0.0 --port ${PORT}"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'), timeout=4)" || exit 1
