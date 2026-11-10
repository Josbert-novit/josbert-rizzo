# Sistema de mantenimiento — TP Academia Novit 2026 (Entrega 1)

Sistema que se conecta a la planta simulada [academia2026-tp](../academia2026-tp) para recibir
notas de voz del operador, detectar anomalías y asistir al planificador en la aprobación de
órdenes de trabajo. Ver [PLAN.md](PLAN.md) para el detalle de requerimientos y avance por fase.

## Requisitos

- Docker + Docker Compose.
- ~8 GB de RAM libres (Ollama corre dos modelos locales: `qwen2.5:1.5b` para extracción y
  `nomic-embed-text` para embeddings — ver [INFORME.md](INFORME.md)) y unos 10 GB de disco para
  las imágenes con los modelos horneados.
- Para correr los tests fuera de Docker: Python ≥3.11.
- El repo de la planta [academia2026-tp](../academia2026-tp) clonado al lado de este (`../`).

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

La primera vez tarda varios minutos: Docker hornea los modelos de Ollama y de Whisper **en el
build de la imagen** (no al arrancar el contenedor) — una decisión deliberada, no un detalle de
implementación: medido en vivo contra el corrector automático, bajar los modelos recién al
arrancar le comía casi todo el tiempo real disponible por escenario y hacía fallar el sistema
casi por completo (ver bug #19 y la nota de la Fase 6 en `PLAN.md`).

| Qué | Dónde |
| --- | --- |
| Central de mantenimiento (nuestro sistema: partes, eventos) | http://localhost:8200 |
| Visor de la planta (tablet simulada, pantallas) | http://localhost:8080 |
| Plano de la planta | http://localhost:8081 |
| API de la planta | http://localhost:8100 |

## Ejemplo de recorrido completo de una nota

1. El operador graba una nota de voz en la tablet del visor (o se inyecta con
   `POST http://localhost:8100/api/control/accion {"accion": "nota"}`); la planta la publica en
   `planta/recorridas`.
2. El agente la descarga y transcribe con Whisper local (`llm/transcripcion.py`) — antes que nada
   más, corre el detector de alerta de seguridad (`procesamiento/seguridad.py`) sobre el texto
   crudo, sin esperar al LLM.
3. El texto se extrae a filas estructuradas con el LLM local (`llm/extraccion.py`), se resuelve
   el nombre del equipo contra el catálogo (`procesamiento/resolucion.py`), se valida la unidad
   (`procesamiento/unidades.py`) y el rango histórico (`procesamiento/rango_historico.py`).
4. El borrador se manda a la tablet (`sistema/tablet`) para que el operador confirme o corrija
   (`procesamiento/dialogo.py`) — el estado de esa conversación se guarda en Postgres
   (`persistencia/memoria.py`) por si el agente se reinicia a mitad de camino.
5. Confirmado el parte, se evalúa contra los límites de alarma de la planta
   (`procesamiento/eventos.py`) y se publica un `EventoSistema` (`SinNovedad` o `Escalado`) en
   `sistema/eventos`.

Spec completa, con los bugs reales encontrados en cada paso: [docs/spec-parte-por-voz.md](docs/spec-parte-por-voz.md).

## RAG: qué sale de ahí y qué no (5.5)

El corpus (`src/sistema/rag/`) se indexa en pgvector trayendo todo por la API HTTP de la planta
(`GET /api/documentos`, `GET /documentos/fragmentos.jsonl`) — no hay ningún acceso directo al
filesystem de `academia2026-tp`, para no depender de tener los dos repos uno al lado del otro:

```bash
docker compose exec agente python -m sistema.rag.indexar
```

Evaluación de retrieval (5.3), contra la suite de 71 preguntas (`evaluacion/retrieval.json`, se
trae también por API):

```bash
docker compose exec agente python -m evaluacion.evaluar_retrieval --detalle
```

**El RAG es solo para texto narrativo** (manuales, contrato, historial de órdenes) — para lo que
no hay un endpoint o archivo estructurado propio. Lo que **no** sale del RAG, porque ya tiene una
fuente exacta y más barata:

| Dato | Fuente |
| --- | --- |
| Límites de alarma/disparo | `GET /api/planta` (no el manual — la Rev. C tiene otros valores) |
| Estado en vivo de la planta | `GET /api/estado` |
| Stock, catálogo de tareas | `src/sistema/integraciones/materiales.py` (`datos/materiales.yaml`) |
| Código de falla ↔ fabricante | `src/sistema/rag/fallas.py` (parsea MBP-4 §6.1, no RAG semántico:
  es una tabla de correspondencia exacta, no hace falta similaridad para eso) |

Mezclar esto con el RAG agregaría un paso de más (el LLM "leyendo" un número que ya se podía
consultar exacto) sin ninguna ganancia.

**Trampas del corpus (5.2)**: la Rev. C del manual (`vigente: false`) se indexa pero se excluye
por default de la búsqueda (`vigente=true` es un filtro duro, no una preferencia de ranking) —
confirmado en 5.3: ninguna de las 71 preguntas recupera una cita no vigente. Las secciones de
otros modelos/plantas (TX-250, TX-500, PIA-1, PIA-2) no se filtran aparte: quedan sujetas a la
calidad del embedding semántico, que los resultados de 5.3 miden directamente.

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
