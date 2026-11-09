# Sistema de mantenimiento — TP Academia Novit 2026 (Entrega 1)

Sistema que se conecta a la planta simulada [academia2026-tp](../academia2026-tp) para recibir
notas de voz del operador, detectar anomalías y asistir al planificador en la aprobación de
órdenes de trabajo. Ver [PLAN.md](PLAN.md) para el detalle de requerimientos y avance por fase.

## Qué corre en la PC de planta vs. en la nube

- **PC de planta** (red `planta-lan`, sin internet salvo por el enlace): el agente que escucha
  MQTT y la transcripción de voz local (debe seguir funcionando con el enlace caído).
- **Nube** (opcional, solo si el enlace está arriba): llamadas a modelos remotos para tareas que
  no son críticas en tiempo real (p. ej. análisis más profundo, RAG). El detalle de qué modelo se
  usa en cada tarea y por qué está en [INFORME.md](INFORME.md).

## Cómo levantarlo

Requiere la planta corriendo primero:

```bash
cd ../academia2026-tp
docker compose up -d --build
```

Después, en esta carpeta:

```bash
cp .env.example .env
docker compose up -d --build
```

| Qué | Dónde |
| --- | --- |
| Central de mantenimiento | http://localhost:8200 |

## Cómo correr los tests

```bash
pip install -e ".[dev]"
pytest
```

## Estructura

Ver [docs/estructura-proyecto-python.md](../academia2026-tp/docs/estructura-proyecto-python.md)
de la planta (referencia orientativa).

```
src/sistema/
├── dominio/         # reglas de negocio, entidades y estados (testeable sin LLM)
├── procesamiento/    # pipeline de recepción y transformación de mensajes
├── llm/              # acceso a modelos (transcripción, extracción, analisis)
├── rag/               # indexado y recall sobre el corpus de documentos
├── persistencia/      # Postgres: estado de conversación, eventos, órdenes
├── integraciones/     # MQTT, HTTP de la planta, MCP
└── interfaces/        # Central de mantenimiento (FastAPI)
```
