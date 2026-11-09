FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src

RUN pip install --no-cache-dir -e /app

ENV PYTHONPATH=/app/src
WORKDIR /app/src

CMD ["python", "-m", "sistema.agente"]
