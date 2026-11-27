# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    curl \
    postgresql-client \
    mariadb-client \
    util-linux \
    tar \
    gzip

COPY Personal_Api_Platform/requirements.txt ./
RUN --mount=type=cache,id=psm-pip-cache,target=/root/.cache/pip \
    pip install --prefer-binary -r requirements.txt

# Single Source of Truth: Copy PAP Core & PSM Control Plane
COPY Personal_Api_Platform/app ./app
COPY Personal_Server_Manager/apps/api ./apps/api
COPY Personal_Server_Manager/apps/agent ./apps/agent
COPY Personal_Api_Platform/migrations ./migrations
COPY Personal_Api_Platform/alembic.ini ./alembic.ini

# Ensure directories for backups and storage exist
RUN mkdir -p /app/backups /opt/psm/uploads /opt/psm/releases /opt/psm/pap_backups \
    && useradd --create-home appuser \
    && chown -R appuser:appuser /app /opt/psm

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
