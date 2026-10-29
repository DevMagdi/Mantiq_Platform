# syntax=docker/dockerfile:1
# Build context is the repository root (so `infra/` and `backend/` are both visible).
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

FROM base AS deps
COPY backend/requirements.txt ./backend/requirements.txt
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
 && pip install --upgrade pip \
 && pip install -r backend/requirements.txt \
 && apt-get purge -y build-essential && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

FROM base AS runtime
# curl stays in runtime: the compose healthchecks use it
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl && rm -rf /var/lib/apt/lists/*
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
COPY backend/ ./
ENV PYTHONPATH=/srv
# The object-storage directory is created *in the image*, owned by the runtime user: a named volume
# mounted on a path that does not exist in the image is initialised root-owned, and the API (uid 10001)
# would fail every attachment write with a permission error that looks nothing like its cause.
RUN mkdir -p /srv/objects
RUN useradd -r -u 10001 broker && chown -R broker:broker /srv
USER broker
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
