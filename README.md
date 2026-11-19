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

## Aprobación, cierre y certificación de órdenes (Entrega 2, Fase 12)

API propia en la Central (el anexo acepta "CLI, API o web"; no hay UI gráfica):

```bash
# Ver qué necesita revisión del planificador (quedó PendienteAprobacion, Fase 9)
curl http://localhost:8200/api/ordenes/pendientes

# Aprobar o rechazar una orden pendiente
curl -X POST http://localhost:8200/api/ordenes/ORD-1/aprobar
curl -X POST http://localhost:8200/api/ordenes/ORD-1/rechazar \
    -H 'Content-Type: application/json' -d '{"motivo": "sin stock en yacimiento"}'

# Cerrar un trabajo propio ya aprobado (supervisor) — falla si la orden es de una contratista
curl -X POST http://localhost:8200/api/ordenes/ORD-1/cerrar

# Certificar u observar un trabajo de contratista ya aprobado (inspector) — falla si es propio.
# monto_habilita_usd NO se manda: lo calcula el sistema contra la lista de precios vigente
# (procesamiento/certificacion.py), nunca lo que declara el cierre (contrato CT-SVP-2025, 3.2).
curl -X POST http://localhost:8200/api/ordenes/ORD-1/certificar \
    -H 'Content-Type: application/json' -d '{"habilita": "HAB-2026-0001"}'
curl -X POST http://localhost:8200/api/ordenes/ORD-1/observar \
    -H 'Content-Type: application/json' -d '{"motivo": "movilización cobrada sin corresponder"}'
```

El cierre por voz (`planta/cierres`, Fase 14) mueve la orden a `Ejecutada` solo, y únicamente si
estaba `Aprobada`. Además marca cada diferencia con la orden en `motivo`: tareas pedidas que no se
hicieron, repuestos reservados que no se usaron o que se usaron de más, horas extra y prueba
final faltante o con pérdida. Los endpoints de cierre y certificación son el paso humano
siguiente, después de `Ejecutada`.

`GET /api/ordenes/{id}` devuelve además:
- `correlacion_id`: el evento raíz que abrió la orden. Une evento, orden y cierre, y aparece en los
  logs del agente como `[correlacion=...]`.
- `repuestos_detalle`: cada repuesto con su disponibilidad (`yacimiento`, `central` o `sin stock`)
  y su costo unitario y total.

Ninguno de los dos viaja por `sistema/ordenes`, porque el `OrdenSistema` del contrato no admite
campos extra.

Verificación en vivo de todo este flujo (orden sembrada, apagado de golpe, transiciones, cierre
por MQTT, certificación y cola de fotos sin enlace), con el stack arriba:
`.venv/Scripts/python -m evaluacion.verificar_entrega2`.

Cada endpoint solo cambia el `estado` de la orden — nunca re-decide `Aprobada`/`PendienteAprobacion`
(esa decisión es exclusiva de `procesamiento/reglas_aprobacion.py`, Fase 9). Se persiste en
Postgres (`persistencia/ordenes.py`) con `pendiente_publicar=true`; un hilo de reconciliación en
`agente.py` (cada 5 s) la publica en `sistema/ordenes` y limpia el flag — así una decisión humana
sobrevive un corte de luz aunque el agente se caiga justo después de guardarla (F3).

## Ejemplo de recorrido completo de una nota

1. El operador graba una nota de voz en la tablet del visor (o se inyecta con
   `POST http://localhost:8100/api/control/accion {"accion": "nota"}`); la planta la publica en
   `planta/recorridas`.
2. El agente la descarga y transcribe con Whisper local (`llm/transcripcion.py`) — antes que nada
   más, corre el detector de alerta de seguridad (`procesamiento/seguridad.py`) sobre el texto
   crudo, sin esperar al LLM. Con el mismo criterio (palabras clave, sin LLM) se detectan las
   **fallas dichas de palabra** (`procesamiento/novedades.py`: "pierde por la empaquetadura",
   "hace un golpeteo", "el visor de aceite está bajo", "disparada, no arranca"…): las que se
   pueden atribuir seguro a un equipo publican `Escalado` en ese momento, y los equipos nombrados
   sin ninguna falla, `SinNovedad` (`BUGS.md` #37). No esperan a que el operador confirme el
   parte, porque una nota sin números no se confirma nunca.
3. El texto se convierte en filas estructuradas **primero con un parser determinístico**: el de
   la librería de la cátedra (`src/planta_kit/notas.py`, copiado sin cambios) más las mañas de
   Whisper medidas en las corridas (`procesamiento/parser_notas.py`: "medió 62" → "me dio 62",
   "1-1" → "1.1"…). Es el diseño de la cátedra para el CU1 (`libreria-tp/ADLC.md`: "el LLM solo
   extrae cuando el parser no alcanza"). Solo si queda texto **con números** sin entender, la nota
   va al LLM local (`llm/extraccion.py`), con resolución de nombres
   (`procesamiento/resolucion.py`), unidad (`procesamiento/unidades.py`) y rango histórico
   (`procesamiento/rango_historico.py`). Medido sobre 188 notas reales del corrector
   (`python -m evaluacion.evaluar_parser`): 161 borradores perfectos, 16 con una pregunta que
   corresponde, 7 al LLM.
4. El borrador se manda a la tablet (`sistema/tablet`) para que el operador confirme o corrija
   (`procesamiento/dialogo.py`). Las respuestas también se entienden primero sin LLM
   (`planta_kit/respuestas.py` + `procesamiento/respuestas_operador.py`: "No. La uno estaba en
   ciento quince kilos, no en ciento doce.", "Era la dos.", "Te faltó…"); el LLM queda para lo que
   no se entiende — el estado de esa conversación se guarda en Postgres
   (`persistencia/memoria.py`) por si el agente se reinicia a mitad de camino.
5. Confirmado el parte, se publica **un `EventoSistema` por equipo** en `sistema/eventos`
   (`procesamiento/eventos.py::eventos_del_parte`): `Escalado` si alguna lectura supera los
   límites de alarma de la planta o si el operador dijo una falla, `SinNovedad` si no
   (`BUGS.md` #35).

Spec completa, con los bugs reales encontrados en cada paso: [docs/spec-parte-por-voz.md](docs/spec-parte-por-voz.md).

## Órdenes de trabajo (Entrega 2): qué es workflow fijo y qué usa LLM

Un evento `Escalado` (de los pasos 2 o 5 de arriba, o directo de un sensor sostenido) dispara un
**router de pasos fijo**, no un agente libre. Desde el Lote 3 de la verificación final
(`BUGS.md` #33) la orden que se **publica** sale en segundos y **sin LLM**: una orden de
inspección (`I-VIB` para vibración, temperatura o ruido; `I-VAL` para el resto), evaluada por las
reglas. Como esas tareas no son estándar, siempre queda `PendienteAprobacion`. El análisis con
LLM corre después, con menor prioridad, y queda solo como **sugerencia** para el planificador
(`GET /api/ordenes/{id}/sugerencia`): nunca se publica, así que nunca decide el estado de una
orden.

```
EventoSistema(Escalado)
       │
       ├──────────────────────────────────────────────┐
       ▼                                              ▼ (después, sin apuro, pool propio)
┌─────────────────────┐   sin LLM, segundos       ┌─────────────────────┐ LLM local     ← llm/analisis.py
│ 1. Orden inmediata    │   ← procesamiento/        │ Sugerencia: análisis  │ (parte,       + procesamiento/
│    de inspección       │     ordenes.py           │ + revisor (citas,     │ historian,      revisor.py
│    (I-VIB / I-VAL);    │     ::orden_inmediata    │ repuestos)            │ RAG)
│    no duplica una      │                          │ → se guarda, NUNCA    │
│    orden abierta igual │                          │   se publica          │
└──────────┬───────────┘                          └─────────────────────┘
           ▼
┌─────────────────────┐   sin LLM, función pura — ÚNICA           ← procesamiento/reglas_aprobacion.py
│ 2. Reglas de          │   que escribe Aprobada/Pendiente
│    aprobación          │   (nunca el LLM: por diseño, no hay
│    (decide el estado)  │   forma de que un prompt manipulado
│                        │   apruebe algo él solo)
└──────────┬───────────┘
           ▼
┌─────────────────────┐   sin LLM, sobrevive reinicios            ← persistencia/ordenes.py
│ 3. Persistencia +     │   (F3): Postgres es la fuente de
│    publicación         │   verdad, no la memoria del proceso
│    (reconciliación     │   ni el broker.
│    cada 5 s)           │
└──────────┬───────────┘
           ▼
   sistema/ordenes (Aprobada | PendienteAprobacion)
           │
           ▼ (si Pendiente) aprobación humana vía `central.py` (API propia, :8200)
           │
           ▼
   Ejecutada (al llegar el cierre por `planta/cierres`) → Cerrada | Certificada | Observada
```

Nada de esto corre sincrónicamente dentro de `on_message` (`BUGS.md` #22, el bug más caro de la
Entrega 1): la orden inmediata va en un hilo propio, y la sugerencia en su propio pool de 1
worker con hilo vigía, que espera a que no haya notas en curso antes de usar Ollama y acepta como
máximo 2 en cola (si hay más, esa sugerencia se descarta; la orden sale igual).

## RAG: qué sale de ahí y qué no (5.5)

El corpus (`src/sistema/rag/`) se indexa en pgvector trayendo todo por la API HTTP de la planta
(`GET /api/documentos`, `GET /documentos/fragmentos.jsonl`) — no hay ningún acceso directo al
filesystem de `academia2026-tp`, para no depender de tener los dos repos uno al lado del otro.

Desde la Entrega 2, `agente.py` lo indexa solo al arrancar — necesario porque el análisis de
órdenes (Fase 10) ya depende de `rag_fragmentos` para citar manuales, y el corrector recrea los
volúmenes en cada escenario (`docker compose down -v`), así que un paso manual nunca llega a
correr ahí. Corre en su propio hilo (`_indexar_corpus_en_segundo_plano`), **después** de que el
agente ya está respondiendo (bug #25), y en **dos pasadas** (`BUGS.md` #28):

1. el texto de los ~700 fragmentos, sin embeddings: tarda segundos y con eso ya anda la búsqueda
   **por palabras** (columna `tsv` con `to_tsvector('spanish', …)`, índice GIN, título con más peso);
2. los embeddings (`nomic-embed-text`), de a 4, pausándose mientras haya una nota, una respuesta
   o un cierre en curso (`llm/control.py::trabajo_urgente`): en esta CPU sin GPU tardan varios
   minutos y no pueden quitarle el Ollama a las notas.

La búsqueda (`rag/buscar.py`) es **híbrida**: une el ranking por palabras (`ts_rank_cd`) con el
semántico (distancia coseno en pgvector) por *Reciprocal Rank Fusion* (k=60). Si todavía no hay
embeddings, o si falla el de la pregunta, responde igual con la mitad por palabras. Para reindexar a mano igual (por ejemplo, después de regenerar el
corpus con otra semilla):

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
confirmado en 5.3: ninguna de las 71 preguntas recupera una cita no vigente, así que la revisión
vieja queda por encima de la vigente en 0 de 71 casos. Las secciones de otros modelos u otras plantas
(límites de TX-250/TX-500, de PIA-1/PIA-2, equipos de otras plantas) se marcan `aplica=false` al
indexar, según su título (`rag/indexar.py`), y se excluyen igual que la Rev. C.

Evaluación con el evaluador oficial de la planta (`corpus.evaluar.evaluar`) y comparación contra
la línea de base BM25 sobre los mismos fragmentos:

```bash
docker compose run --rm -v ../academia2026-tp/planta:/academia/planta:ro agente \
    python -m evaluacion.evaluar_retrieval --oficial
```

## Cómo correr los tests

```bash
pip install -e ".[dev]"
pytest
```

## Resultados del corrector automático

Las corridas reales de `corregir.py` contra este repo están en [correccion/](correccion/README.md)
(Entrega 1: **63.4 / 100 · APRUEBA**).

Entrega 2, verificación final (Fase 15, ver `PLAN.md` y `BUGS.md` #24–#43), corridas en
`academia2026-tp/correccion/corridas/`: de **33.7** (`entrega2-completa-v6`) a **87.5 / 100 ·
APRUEBA** (`lote5c-completa`, 5 escenarios, sin eliminatorias, 0 notas cortadas a los 90 s).

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
