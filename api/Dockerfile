# Build context is the repository root so that the api, adapters and
# providers packages all land side by side under /srv.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/srv
WORKDIR /srv
# git: workspace inspection (branch, dirty files, diffs) in tests and inline runs
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY api/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt
COPY api/ /srv/api/
COPY adapters/ /srv/adapters/
COPY providers/ /srv/providers/
WORKDIR /srv/api
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
