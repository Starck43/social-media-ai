# Build context: repository root (docker-compose `build.context: ..`).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Moscow

WORKDIR /srv/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first for better layer caching
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code (migrations and alembic.ini are required for `alembic upgrade head`)
COPY alembic.ini ./
COPY app ./app
COPY cli ./cli
COPY migrations ./migrations
COPY scripts ./scripts
COPY pyproject.toml ./

RUN useradd --create-home appuser \
    && chown -R appuser:appuser /srv/app
USER appuser

EXPOSE 8000

# Default entrypoint: API (the agent runtime is started via
# `python -m app.runtime`, the worker via `python -m app.worker`)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

