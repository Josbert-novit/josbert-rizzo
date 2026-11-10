FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src

RUN pip install --no-cache-dir -e /app

# El modelo de Whisper queda horneado en la imagen (no solo en el volumen `whisper-modelos`): el
# corrector borra los volúmenes entre escenarios (`down -v`), y bajarlo recién en el arranque le
# come tiempo real al escenario justo cuando la alerta de seguridad (criterio eliminatorio) tiene
# que salir rápido. `HF_HUB_DISABLE_XET=1` porque el backend de transferencia nuevo de
# huggingface_hub no se baja bien acá (ver BUGS.md).
ARG TRANSCRIPCION_MODELO_LOCAL=small
ENV HF_HUB_DISABLE_XET=1
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${TRANSCRIPCION_MODELO_LOCAL}', device='cpu', compute_type='int8')"

ENV PYTHONPATH=/app/src
WORKDIR /app/src

CMD ["python", "-m", "sistema.agente"]
