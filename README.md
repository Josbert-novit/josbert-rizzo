# Sistema de mantenimiento — TP Academia Novit 2026 (Entregas 1 y 2)

Sistema que se conecta a la planta simulada [academia2026-tp](../academia2026-tp) para recibir
notas de voz del operador, detectar anomalías y asistir al planificador en la aprobación de
órdenes de trabajo. Ver [PLAN.md](PLAN.md) para el detalle de requerimientos y avance por fase.

## Requisitos

- Docker + Docker Compose.
- ~8 GB de RAM libres (Ollama corre dos modelos locales: `qwen2.5:1.5b` para extracción y
  `bge-m3` para embeddings — ver [INFORME.md](INFORME.md)) y unos 10 GB de disco para
  las imágenes con los modelos horneados.
- Para correr los tests fuera de Docker: Python ≥3.11.
- El repo de la planta [academia2026-tp](../academia2026-tp) clonado al lado de este (`../`).

## Qué corre en la PC de planta vs. en la nube

| Dónde | Qué | Por qué |
| --- | --- | --- |
| **PC de planta** (`planta-lan`, label `academia.planta: pc`, sale solo por `enlace:3128`) | `agente` (MQTT, transcripción Whisper, detección, alerta S1-S4, diálogo con la tablet, reglas de aprobación, órdenes, cierres) y `ollama` (qwen2.5:1.5b para extracción/análisis/cierre, bge-m3 para el RAG) | Todo lo que puntúa en tiempo real o protege a las personas tiene que andar con el enlace caído (`fuga-sin-enlace`) |
| **Red propia** (`backend`) | `postgres` (pgvector, diálogos, órdenes, cola de fotos) y `central` (interfaz HTTP del planificador, supervisor e inspector, puerto 8200) | Estado durable que sobrevive al corte de luz y una interfaz para las personas |
| **Nube** (solo con el enlace arriba) | Visión NVIDIA NIM (`nvidia/nemotron-3.5-super-vl-120b-a12b`) para las fotos | No hay un modelo de visión que corra en CPU sin GPU. Sin enlace, la foto queda en cola y se analiza cuando vuelve; la alerta y la orden nunca esperan a la visión |

**Por qué el agente está en dos redes y Postgres no lleva el label de planta.** El contrato
(`docs/contrato.md`) dice que lo que corre en la PC de planta va solo en `planta-lan`. El agente
está además en `backend` para hablar con Postgres. Es una decisión a propósito, y esto la acota:

- `backend` es `internal: true`, sin salida a internet (BUGS #61). El agente sigue saliendo a
  internet solo por `enlace:3128`: la red extra no saltea el enlace.
- Postgres hace de **disco de la PC de planta**: diálogos en curso, órdenes y la cola de fotos
  tienen que sobrevivir a que el agente se apague de golpe. Por eso no lleva `academia.planta: pc`:
  en `sin-luz` el agente se corta, y al volver retoma desde Postgres sin perder ni duplicar
  órdenes (`evaluacion/verificar_entrega2.py`, apagado de golpe de agente y de Postgres).
- No suma CPU ni RAM al presupuesto de la planta: agente 2.5 CPU / 2 GB + Ollama 1.5 CPU / 6 GB
  = 4 CPU / 8 GB, lo que pide el contrato.
- Para respetar el contrato al pie de la letra habría que mover Postgres a `planta-lan` con el
  label, y darle CPU y memoria del mismo presupuesto (sacándoselas al agente o a Ollama). No se
  hizo para no empeorar la latencia de las notas, que es lo que se puntúa en tiempo real.

El detalle de qué modelo se usa en cada tarea, versión, consumo y costo está en
[INFORME.md](INFORME.md).

## Política de memoria

| Qué se recuerda | Dónde | Por cuánto tiempo |
| --- | --- | --- |
| Diálogo en curso con la tablet (parte en borrador, fila en pregunta, pasos y tokens consumidos, firmas de preguntas para detectar loops) | Postgres, `dialogos_pendientes` (`persistencia/memoria.py`) | Mientras el parte esté en `Borrador`; se borra cuando se publica el parte. Un diálogo que nunca se contesta queda en la tabla (limitación conocida, sin limpieza por tiempo) |
| Órdenes y su historia de estados (con `correlacion_id`) | Postgres, `ordenes` (`persistencia/ordenes.py`) | Siempre: es el registro de mantenimiento |
| Fotos pendientes de análisis | Postgres, `fotos_pendientes_analisis` (`persistencia/fotos.py`) | Hasta que se analizan con el enlace arriba |
| Parte confirmado y transcripción | Disco, volumen `notas-media` (`parte.json`, `transcripcion.txt`) | Siempre, como auditoría |
| Manuales para el RAG | Postgres + pgvector | Se reindexan al arrancar; solo las revisiones vigentes se citan |

Lo que **no** se recuerda: límites, stock y estado de los equipos. Se leen siempre de la planta
(`/api/planta`, historian, inventario) en el momento, para no decidir con un dato viejo.

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
(`BUGS.md` #33) la orden que se **publica** sale en segundos y **sin LLM**, y siempre la evalúan
las reglas:
- Si la evidencia es clara, se propone la tarea del catálogo con sus repuestos:
  - empaquetadura → `T-B-EMP`;
  - aceite bajo → `T-B-ACE`;
  - correas → `T-B-COR`;
  - diferencial de filtro en alarma sostenida → `T-F-CAR`;
  - **colmatación acelerada** de un filtro → `T-F-CAR` (Lote 9, `BUGS.md` #78). Cada 30 min de
    planta, `procesamiento/colmatacion.py` corrige la ΔP al caudal de diseño con FT-401
    (FC48-3 §7.1) y compara la pendiente de las últimas 4 h con la de los ciclos de cartuchos
    anteriores del historian. Si sube 3 veces más rápido (FC48-3 §4.2), escala aunque la ΔP todavía
    no llegue a la alarma. Es el caso de `sin-luz`: la orden queda pendiente por stock antes del
    corte de luz.

  Si pasa las 5 reglas, sale `Aprobada` sola, y la planta manda después su cierre (G1). La regla 1
  exige que **cada** tarea sea estándar para ese equipo: `NO-ESTANDAR` o una orden sin tareas nunca
  se aprueban solas (`BUGS.md` #77).
- Si la evidencia no alcanza, se propone una inspección: `I-VIB` para vibración, temperatura o
  ruido, e `I-VAL` para el resto. No son tareas estándar, así que quedan `PendienteAprobacion`.

Un equipo nombrado en una alerta de seguridad nunca se aprueba solo. El interruptor de este
comportamiento es `ORDENES_TAREA_ESTANDAR` (activado por defecto). El análisis con
LLM corre después, con menor prioridad, y queda solo como **sugerencia** para el planificador
(`GET /api/ordenes/{id}/sugerencia`): nunca se publica, así que nunca decide el estado de una
orden.

**Dónde está la sección C del anexo (análisis y borrador de orden).** Las causas en orden, cada una
con su evidencia, su cita del manual y lo que falta para confirmarla, más las fallas repetidas del
historial (acople en B-101, émbolo rayado en B-103, cartucho roto en F-201B, filtro primario en
B-102, retén del piñón) salen de `llm/analisis.py`. El revisor (`procesamiento/revisor.py`) comprueba
las citas y los repuestos. El planificador los ve en la sugerencia, junto a la orden pendiente. La
orden publicada no espera al LLM por dos razones medidas en el corrector:
- cada análisis tarda ~2 min en esta CPU y un escenario puede terminar antes (`BUGS.md` #33);
- la eliminatoria `obedecio_la_nota` mira **todas** las publicaciones de una orden, así que
  republicarla con lo que propuso el LLM podría meter un repuesto pedido por una nota maliciosa.

En la sugerencia, `detiene_equipo` y `ejecutor` también salen del catálogo y no del modelo.

```
EventoSistema(Escalado)
       │
       ├──────────────────────────────────────────────┐
       ▼                                              ▼ (después, sin apuro, pool propio)
┌─────────────────────┐   sin LLM, segundos       ┌─────────────────────┐ LLM local     ← llm/analisis.py
│ 1. Orden inmediata    │   ← procesamiento/        │ Sugerencia: análisis  │ (parte,       + procesamiento/
│    tarea clara o       │     ordenes.py           │ + revisor (citas,     │ historian,      revisor.py
│    inspección;         │     ::orden_inmediata    │ repuestos)            │ RAG)
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
   **por palabras** (columna `tsv_st` con `to_tsvector('es_sin_tildes', …)` = `spanish` +
   `unaccent`, índice GIN, título con más peso);
2. los embeddings (`bge-m3`): salen del cache precalculado del repo
   (`datos/embeddings_corpus.npz`); solo lo que no esté ahí (otro corpus u otro modelo) se embebe
   en vivo, de a uno, pausándose mientras haya una nota, una respuesta o un cierre en curso
   (`llm/control.py::trabajo_urgente`), para no quitarle el Ollama a las notas.

La búsqueda (`rag/buscar.py`) es **híbrida**: une el ranking por palabras (`ts_rank`) con el
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

Resultado (corpus completo de 701 fragmentos, 71 preguntas):

| Buscador | hit@1 | hit@5 | recall@5 | mrr | distractor@5 |
|---|---:|---:|---:|---:|---:|
| **Nuestra híbrida con bge-m3 — la que usa el sistema** | **0.634** | **0.901** | **0.725** | **0.747** | 0.014 |
| Nuestra solo full-text (`ts_rank`, sin tildes) | 0.549 | 0.831 | 0.707 | 0.657 | **0.000** |
| Híbrida con nomic-embed-text (hasta el Lote 6) | 0.451 | 0.746 | 0.624 | 0.587 | 0.014 |
| BM25 de la cátedra (línea de base) | 0.352 | 0.845 | 0.664 | 0.573 | 0.099 |

Cómo se llegó (Lote 7, cada paso medido con el evaluador oficial antes de aplicarlo):
- `ts_rank_cd` → `ts_rank` (bug #66): con una consulta OR, `ts_rank_cd` enterraba las órdenes
  cortas del historial. Híbrida: hit@5 0.690 → 0.746.
- Búsqueda sin tildes (bug #67): el historial está escrito a mano sin tildes ("valvulas",
  "caudalimetro") y el diccionario `spanish` no las quita. Configuración propia `es_sin_tildes`
  (`spanish` + `unaccent`). Full-text: hit@5 0.817 → 0.831, recall@5 0.688 → 0.707.
- `nomic-embed-text` → `bge-m3` (bug #68): con nomic la mitad semántica restaba (la híbrida quedaba
  debajo del full-text solo); con bge-m3, multilingüe y el que usan las demos de la cátedra, la
  híbrida le gana a BM25 en hit@5, recall@5 y mrr. El corpus va precalculado en el repo
  (`datos/embeddings_corpus.npz`); en vivo solo se embebe la consulta (0.45 s de mediana en CPU).
- Contextual chunking (módulo 2.2, mismo formato que `planta_kit.rag` de la cátedra): el vector de
  cada fragmento lleva "Documento › Sección" adelante (`indexar.texto_para_embeber`), así se
  entiende solo. recall@5 0.711 → 0.725 y distractor@3 0.014 → 0, el resto igual. La columna
  `embedding_clave` (modelo + texto embebido) hace que una base ya indexada se re-embeba sola si
  cambia el modelo o lo que se embebe.
- No se ajustaron los pesos del RRF, para no sobreajustar a las mismas 71 preguntas con que se evalúa.
- Distractores: la Rev. C (no vigente) no aparece en ninguna búsqueda, por el filtro duro. El 0.014
  que queda es una sección vigente del documento correcto pero equivocada.
- Lo más flojo sigue siendo `historial` (hit@5 0.64 contra 0.82 de BM25): las órdenes se escriben
  con abreviaturas propias ("B2", "bba", "fa") que ninguna de las dos mitades resuelve.

## Velocidad y fiabilidad (Lote 6)

La PC de planta tiene 4 CPU sin GPU, y la corrección corre en una máquina que no controlamos. El
sistema está armado para que una nota nunca espere a la CPU y para que una falla de broker,
Postgres u Ollama no pierda ni cuelgue nada:

| Qué | Cómo |
| --- | --- |
| Whisper | 2 hilos (no uno por núcleo del host: el cgroup lo frenaba), modelo precargado al arrancar. Elegido midiendo con `evaluacion/evaluar_whisper.py` |
| Embeddings del corpus | precalculados en `src/sistema/datos/embeddings_corpus.npz`; al arrancar no se embebe nada que ya esté |
| Ollama | modelos siempre cargados, un pedido a la vez; la sugerencia del LLM (no se publica) se corta cuando llega una nota |
| Nota en cola | acuse en la tablet a los 8 s si todavía no tuvo respuesta; el corte de 90 s cuenta desde que la nota arranca |
| Alerta de seguridad | audio → Whisper → alerta; las fotos se bajan después |
| MQTT | QoS 1 en todo lo publicado, reconexión automática, notas repetidas ignoradas por id; nada de lo que pase con un mensaje corta el loop de red |
| Órdenes | se marcan publicadas solo si el broker confirmó la recepción |
| Postgres | timeouts de conexión y de consulta, tablas creadas una vez por proceso, reintentos al guardar órdenes y cierres |
| Arranque y apagado | conecta y late enseguida (la preparación corre en paralelo), maneja SIGTERM (`init: true`) |
| Red | `backend` es interna: el agente sale a internet solo por `enlace:3128`, como pide el contrato |

Para ver dónde se fue el tiempo en una corrida del corrector:

```bash
python -m evaluacion.latencias <corrida>   # whisper, cola, primer mensaje, acuses y cortes por escenario
```

## Servidor MCP (consulta de la planta, solo lectura)

`src/sistema/integraciones/mcp_server.py` expone 7 tools de solo lectura: `equipos`,
`historial_sensor`, `historial_ordenes`, `inventario`, `catalogo_tareas`, `documentos` y
`buscar_manuales`. Son las mismas funciones de `integraciones/tools.py` que usa el agente. Corre por
stdio, así que lo arranca el cliente MCP (Claude Desktop, Cursor, el inspector de MCP) y no es un
servicio del compose. Con la planta y el sistema levantados:

```bash
# desde el host (usa la planta en localhost)
.venv/Scripts/python -m sistema.integraciones.mcp_server

# o adentro del contenedor del agente (red de la planta); -T porque el cliente habla por stdio
docker compose exec -T agente python -m sistema.integraciones.mcp_server
```

Ejemplo de configuración para un cliente MCP:

```json
{"mcpServers": {"planta": {"command": "docker",
  "args": ["compose", "-p", "josbert-rizzo", "exec", "-T", "agente",
           "python", "-m", "sistema.integraciones.mcp_server"]}}}
```

## Cómo correr los tests

```bash
pip install -e ".[dev]"
pytest
```

Los tests no necesitan Docker ni la planta levantada. Los que comparan contra casos reales de la
cátedra (`academia2026-tp/datos/documentos/contrato/cierres_ejemplo.json`) se saltean solos si
ese repo no está en la carpeta de al lado.

**CI** (`.github/workflows/ci.yml`): en cada push y cada PR, GitHub Actions corre los mismos
comandos con Python 3.11 (la versión de la imagen del agente), con `academia2026-tp` al lado para
que corran todos los tests. Las actions van fijadas por commit. `ruff check` corre como paso
informativo: muestra los avisos de estilo pero no frena la CI. El corrector oficial y
`evaluacion/verificar_entrega2.py` quedan afuera porque necesitan la planta simulada y 4 CPU.

## Resultados del corrector automático

Las corridas reales de `corregir.py` contra este repo están en [correccion/](correccion/README.md)
(Entrega 1: **63.4 / 100 · APRUEBA**).

Entrega 2, verificación final (Fase 15, ver `PLAN.md` y `BUGS.md` #24–#43), corridas en
`academia2026-tp/correccion/corridas/`: de **33.7** (`entrega2-completa-v6`) a **87.5 / 100 ·
APRUEBA** (`lote5c-completa`, 5 escenarios, sin eliminatorias, 0 notas cortadas a los 90 s).
Lote 6 (velocidad y fiabilidad, `BUGS.md` #53–#65): **89.7 / 100 · APRUEBA** (`lote6b`, los 7
escenarios de la rúbrica, sin eliminatorias).

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
