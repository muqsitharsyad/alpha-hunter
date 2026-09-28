# AlphaHunter AI: single process, SQLite file, no Node toolchain.
# Alternatives considered and rejected for the constrained home server:
#   Postgres + Redis       -> unnecessary additional services for one paper user.
#   Next.js frontend       -> needs Node, a build step and a second process.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/templates ./templates
COPY backend/static ./static
COPY backend/run.py ./run.py

# non-root: the app never needs write access outside its data volume
RUN useradd --create-home --uid 10001 alphahunter \
 && mkdir -p /data \
 && chown -R alphahunter:alphahunter /app /data
USER alphahunter

ENV ALPHAHUNTER_DATABASE_URL=sqlite:////data/alphahunter.db \
    HOST=0.0.0.0 \
    PORT=8010 \
    TRADING_MODE=paper \
    ENABLE_LIVE_EXECUTION=false

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/login', timeout=3).close()" || exit 1

CMD ["python", "run.py"]
