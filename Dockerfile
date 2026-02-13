FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

ARG INSTALL_DEV=false
ARG INSTALL_LOCAL_EMBEDDINGS=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY manage.py /app/
COPY config /app/config
COPY apps /app/apps
COPY tests /app/tests

RUN set -eux; \
    pip install --upgrade pip; \
    extras=""; \
    if [ "$INSTALL_DEV" = "true" ]; then extras="dev"; fi; \
    if [ "$INSTALL_LOCAL_EMBEDDINGS" = "true" ]; then extras="${extras}${extras:+,}local-embeddings"; fi; \
    if [ -n "$extras" ]; then \
      pip install -e ".[${extras}]"; \
    else \
      pip install -e "."; \
    fi

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
