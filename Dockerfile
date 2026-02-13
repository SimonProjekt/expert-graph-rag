FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY manage.py /app/
COPY config /app/config
COPY apps /app/apps
COPY tests /app/tests

RUN pip install --upgrade pip && pip install -e ".[dev]"

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
