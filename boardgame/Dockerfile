FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Cloud Run injects $PORT (default 8080); bind 0.0.0.0 so it is reachable.
ENV PORT=8080
EXPOSE 8080

# A single gunicorn worker keeps the SQLite catalog in one process. The
# bundled games.json is auto-seeded on first request via create_app().
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 120 app:app"]
