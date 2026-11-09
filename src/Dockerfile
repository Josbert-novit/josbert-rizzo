FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src

RUN pip install --no-cache-dir -e /app

ENV PYTHONPATH=/app/src
WORKDIR /app/src

CMD ["python", "-m", "sistema.agente"]
