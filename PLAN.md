# Plan de resolución — Entrega 1 (TP Academia Novit 2026)

> Vigencia: solo cubre la **Entrega 1** (fecha límite 10/11, módulos 1.1 a 2.4). No incluye C, D, G
> del enunciado completo (borrador de orden, aprobación automática, cierre/certificación) — eso es
> Entrega 2+.

## Qué se entrega (resumen ejecutado)

- **Repositorio público en GitHub** llamado `josbert-rizzo` (no es un fork de `academia2026-tp`:
  es un repo nuevo, tuyo, que se levanta *al lado* de la planta y se conecta por red Docker y MQTT).
- En la raíz: `README.md`, `docker-compose.yml`, `tests/`. El resto de la estructura es libre
  (ver `docs/estructura-proyecto-python.md` de la planta como referencia orientativa).
- `README.md`: cómo levantar y probar el sistema, y justificación de qué corre en la PC de planta
  vs. en la nube.
- `INFORME.md`: modelos usados por tarea (local: nombre/proveedor/versión/parámetros/hardware de
  prueba; nube: nombre/proveedor/versión/página de referencia), consumo de tokens por tarea, costo
  aproximado, y una conclusión de por qué se eligieron esos modelos.
- `tests/`: tests automatizados de las tools + suite de evaluación (aciertos, costo, latencia).
- El link al repo se carga en el formulario de entregas (Google Forms, link en el enunciado).
- **No se entrega un Word ni un PDF.** Toda la documentación va en Markdown dentro del repo.

## Cómo se conectan los dos repos

```
TpAcademia-1/
├── academia2026-tp/   ← la planta simulada (ya corriendo: docker compose up -d --build)
└── josbert-rizzo/     ← tu solución (este repo nuevo)
```

Tu `docker-compose.yml` crea servicios que:
- van solo en la red `planta-lan` (externa, `internal: true`, sin salida directa a internet) con
  el label `academia.planta: pc`;
- en conjunto no superan 4 CPU / 8 GB;
- salen a internet solo por `HTTP(S)_PROXY=http://enlace:3128`, `NO_PROXY=broker,sim,localhost`;
- instalan `planta_contrato` con pip (no copian los modelos Pydantic);
- publican `sistema/estado` cada 30 s con `{sistema, enlace_ok, pendientes_sincronizar}`.

Ver `../academia2026-tp/docs/contrato.md` para el detalle de redes y tópicos.

## Pasos (orden sugerido)

### Fase 0 — Setup
- [ ] 0.1 Crear repo público en GitHub `josbert-rizzo`, conectar este folder como `origin`.
      **Pendiente de vos**: no tenés `gh` CLI instalado, así que lo creamos desde la web
      (ver sección "Cómo crear el repo en GitHub" más abajo). Git local ya está inicializado
      y con el primer commit hecho.
- [x] 0.2 Elegir stack: Python 3.11+, LangChain, Pydantic (`extra="forbid"` en tools),
      PostgreSQL 16 + pgvector. Ver `pyproject.toml`.
- [x] 0.3 Esqueleto de carpetas (ver `docs/estructura-proyecto-python.md` de la planta):
      `dominio/`, `procesamiento/`, `llm/`, `rag/`, `persistencia/`, `integraciones/`, `interfaces/`.
- [x] 0.4 `docker-compose.yml` inicial: servicio(s) propios en `planta-lan` (external) +
      `postgres:16` con `pgvector`. **Falta probar** `docker compose up` (Docker Desktop no
      estaba corriendo al hacer el setup) — levantalo vos cuando puedas y avisame si falla algo.
- [x] 0.5 Conectar al broker MQTT (`broker:1883` desde `planta-lan`) y confirmar que se reciben
      mensajes de `planta/#`. Implementado en `src/sistema/agente.py` (sin probar en vivo todavía).
- [x] 0.6 Latido: publicar `sistema/estado` cada 30 s. Implementado en `agente.py`.

### Fase 1 — Parte por voz (requerimiento A, completo)
- [x] 1.1 Suscribirse a `planta/recorridas`, bajar audio (`http://sim:8100{audio_url}`) y fotos.
      Probado en vivo: `docker compose up` (planta + esta solución) funcionando juntos,
      agente conectado al broker y latido publicando. Ver `src/sistema/procesamiento/recorridas.py`.
      **Corregido**: el `pip install` de `docs/contrato.md` apunta a un repo que no existe
      (`academia2026-planta`); el correcto es `academia2026-tp#subdirectory=contrato` (mismo repo
      de la planta). Ya ajustado en `pyproject.toml`.
- [x] 1.2 Transcripción con modelo **local** (`faster-whisper`, modelo `small`, CPU/int8). No
      encontramos la "lista cerrada" oficial en los docs accesibles (el Enunciado la menciona
      pero no la incluye) — confirmaste que el modelo es libre, así que seguimos con este.
      Probado en vivo end-to-end: nota disparada por `/api/operador/nota`, bajada de audio,
      transcripción y guardado de `transcripcion.txt` en el volumen. Ver
      `src/sistema/llm/transcripcion.py`.
- [x] 1.3 Extracción a `FilaParte` con structured output (Pydantic): una fila por equipo+variable,
      `hora` = `ts` de la nota (no el reloj de la máquina). LLM local vía **Ollama** (`qwen2.5:1.5b`,
      CPU), `langchain-ollama` con `.with_structured_output()`. Probado en vivo: el LLM separó
      correctamente una nota con varios equipos en filas distintas, validadas contra el Pydantic
      del contrato (`extra="forbid"`). La precisión fina (unidades, números mal interpretados) se
      resuelve en 1.4/1.5/1.6, no acá.
      **Simplificación pendiente**: "fotos asociadas a la fila que corresponde" — por ahora todas
      las fotos de la nota se asocian a todas las filas (no hay lógica de asociación por fila
      todavía; requeriría razonamiento visual, posible candidato para un modelo NVIDIA en la nube
      con visión más adelante).
      **Troubleshooting real que encontramos y resolvimos**: Ollama en `planta-lan` necesita el
      proxy (`HTTP_PROXY`/`HTTPS_PROXY=http://enlace:3128`) para bajar modelos; el *cliente* CLI
      de Ollama además necesita `OLLAMA_HOST=127.0.0.1:11434` explícito al llamar `ollama pull`
      dentro del propio contenedor, si no el tráfico de loopback se enruta mal por el proxy. Y la
      imagen `ollama/ollama:0.3.14` es demasiado vieja para structured output con JSON Schema;
      subimos a `0.5.4`.
- [x] 1.4 Resolver nombres informales contra `GET /api/planta` ("la dos", "el de la izquierda",
      "la de reserva"). Ver `src/sistema/procesamiento/resolucion.py` (`ResolverNombres`):
      match exacto contra `tag` real, match exacto contra la lista de `nombres` informales de
      cada equipo, y si no matchea nada, fallback genérico por `tipo` (p. ej. "bomba") —
      resuelto si hay un solo equipo de ese tipo, ambiguo si hay varios.
      Lo que queda ambiguo o sin resolver **todavía no pregunta por tablet**: eso es 1.7
      (loop de confirmación), que es el siguiente paso lógico. Por ahora solo queda marcado
      en el `Parte` (`tag: null`, `ambiguo: true`, `candidatos: [...]`).
      Probado en vivo contra la API real: "la dos" → B-102, "la de reserva" → B-103, "la bomba"
      (genérico) → ambiguo entre las 3 bombas, y en una corrida real el LLM extrajo "el filtro de
      la izquierda" (frase distinta a la lista exacta) → quedó ambiguo entre F-201A y F-201B, que
      es el comportamiento correcto cuando no hay certeza.
- [x] 1.5 Unidades: "kilos" = kg/cm²; B-101 en kg/cm², B-102 en psi, B-103 en bar; un diferencial
      dicho "dieciocho" en vez de "uno ocho" se pregunta. Ver `src/sistema/procesamiento/unidades.py`
      (`resolver_unidad`): normaliza alias ("kilos"→"kg/cm2"), y una vez resuelto el tag de una
      bomba, la unidad real sale del `manometro_local` del equipo (`GET /api/planta`) — no de lo
      que diga el operador. Si lo dicho no coincide con el manómetro real, o si un diferencial de
      filtro viene ≥10 (probable "dieciocho" mal dicho en vez de "1.8"), la fila queda con
      `requiere_confirmacion: true` y un motivo legible.
      Probado contra datos reales de la planta: B-101/kg·cm², B-102/psi correctos; "la dos" +
      "kilos" dicho → detecta el desacuerdo con el manómetro real (psi); diferencial 18 → pide
      confirmar, 1.8 → pasa normal.
      Nota: esto todavía no dispara la pregunta real por tablet (eso sigue siendo 1.7); por ahora
      queda marcado en el `Parte`.
- [x] 1.6 Rango histórico vía `GET /api/historian/{tag}/{variable}` (45 días); fuera de rango pide
      confirmación antes de guardar. Ver `src/sistema/procesamiento/rango_historico.py`
      (`VerificadorRango`): trae el historial (una vez por tag+variable, cacheado), calcula
      min/max con margen del 15%, y compara. Para eso hizo falta mapear la descripción libre de
      la variable (la que da el LLM, p. ej. "presión de descarga") a la clave canónica del
      historiador (`presion_descarga`) — ver `src/sistema/procesamiento/variables.py`
      (`mapear_variable`). Las lecturas puramente locales sin sensor conectado (temperatura por
      IR, goteo, estado de correas) no tienen con qué compararse: no bloquean nada.
      **Detalle importante que encontramos**: el historiador guarda presiones en la unidad
      canónica del sensor (p. ej. kg/cm² para B-102), que puede ser distinta de la unidad del
      manómetro local (psi para B-102). Agregamos conversión (`a_kgcm2` en `unidades.py`) antes
      de comparar, si no un valor normal en psi disparaba un falso "fuera de rango".
      Probado en vivo contra el historiador real: valor normal pasa, valor absurdo (500) marca
      confirmar con el rango real impreso, y la conversión de unidades evita falsos positivos.
- [x] 1.7 Tablet: mandar borrador en `MensajeTablet.pantalla.filas` (con opciones si hay que elegir),
      procesar `planta/respuestas` en lenguaje de campo ("No, la uno estaba en ciento quince kilos"),
      reenviar borrador corregido. Ver `src/sistema/procesamiento/dialogo.py`: pregunta una fila
      pendiente a la vez (ambigua → opciones con los candidatos; con `requiere_confirmacion` →
      sí/no), reusa `interpretar_texto` (el mismo pipeline de extracción) para reinterpretar
      correcciones y las mergea en el `Parte` existente, y recién al final pregunta la
      confirmación global del parte completo.
      **Simplificación**: el estado del diálogo (`dialogos_pendientes`, indexado por el id del
      último `MensajeTablet`) vive en memoria del proceso del agente — se pierde si reinicia.
      Persistirlo en Postgres es la Fase 5.7 (memoria de conversación).
- [x] 1.8 Parte pasa `Borrador` → `Confirmado` solo tras confirmación del operador.
- [x] 1.9 Manejar notas `VIVO-…` (sin parte esperado, pueden no traer nada que reportar).
      Probado en vivo end-to-end los tres juntos (1.7, 1.8, 1.9): disparé una nota real con un
      valor fuera de rango y un nombre ambiguo, respondí por MQTT (`mosquitto_pub` a
      `planta/respuestas`) a cada pregunta en orden, y el parte terminó en `estado: "Confirmado"`
      con ambas filas resueltas correctamente.

### Fase 2 — Detección en planta (requerimiento B, en código, SIN LLM)
- [x] 2.1 Por cada parte confirmado y cada desvío de `planta/sensores/#`, generar `EventoSistema`
      idempotente (`id` estable, `origen` parte/sensor, `nota_ids`). Ver
      `src/sistema/procesamiento/eventos.py`: `evaluar_parte()` (origen `parte`, id
      `EVT-PARTE-{parte.id}`, se dispara una sola vez en `agente.py` cuando el diálogo de
      confirmación llega a `Confirmado`) y `DetectorSensores` (origen `sensor`, suscripto a
      `planta/sensores/#` vía el mismo `planta/#` de siempre; id atado al inicio de la racha
      sostenida, así nunca genera dos eventos distintos para la misma anomalía).
- [x] 2.2 Límites desde `GET /api/planta` (`sensores`, con `alarma_alta`/`alarma_baja`/`disparo`),
      NO del manual Rev. C vencido. Ver `src/sistema/procesamiento/limites.py`
      (`CatalogoLimites`/`nivel_anomalia`): misma entrada (tag, variable, valor) siempre da la
      misma clasificación (`None` / `"alarma"` / `"disparo"`, más grave).
- [x] 2.3 Clasificar `SinNovedad` / `Escalado`. Un pico aislado no escala: hace falta que la
      anomalía esté sostenida (`UMBRAL_SOSTENIDO_S` = 300 s simulados, o 60 s si es nivel
      `disparo`, más grave) — medido por el `ts` de cada `LecturaSensor`, no el reloj real, para
      que funcione igual a cualquier velocidad de simulación. "Parada, en reserva" no es
      anomalía: si la fila de un parte no trae `valor` (el operador dijo "parada, en reserva" sin
      número) se ignora, y para el stream de sensores se agregó `EstadoBombas` (consulta
      `GET /api/estado`, cacheada 5 s) para no marcar una presión de succión/descarga baja como
      anomalía cuando la bomba no está en marcha.
      **Bug real que encontramos probando en vivo**: sin este chequeo, B-103 (bomba de reserva,
      parada) escalaba falsamente por `presion_descarga` baja (1.5 vs. `alarma_baja` 95) — la
      presión de una bomba parada siempre lee bajo por diseño. Corregido con `EstadoBombas`.
- [x] 2.4 El escalado lleva contexto: `parte_id` + `nota_ids` (para que quien reciba el evento
      pueda ir a buscar el parte completo con sus fotos en `/data/notas/{nota_id}/`), y la
      tendencia de sensores de las últimas horas (`src/sistema/procesamiento/tendencia.py`,
      `tendencia_reciente()`: compara el promedio de la primera y segunda mitad de las últimas 4 h
      de `GET /api/historian/...`) se agrega al `resumen` tanto en escalados por parte como por
      sensor.
      Probado en vivo end-to-end: inyecté una falla real F10 (sensor con deriva) en
      `presion_descarga` de B-102 vía `/api/control/accion`, aceleré la simulación y vi en los
      logs del agente escalados reales sostenidos (`EVT-SENSOR-B-102-presion_descarga-...`,
      también `vibracion` y `presion_diferencial` de F-201A por la física normal de la planta a
      alta carga). Disparé además una nota de voz real ("la dos está en 140 kilos de descarga"),
      confirmé el diálogo por `mosquitto_pub` como en 1.9, y al quedar `Confirmado` se publicó
      `EVT-PARTE-PARTE-NOTA-00232 (Escalado)` (140 kg/cm2 excede el límite real de 125).
      19 tests nuevos en `tests/test_limites.py` y `tests/test_eventos.py` (52 tests en total).
- [x] 2.5 `Alerta de seguridad` (S1–S4: agua a presión, olor a gas, humo/fuego, persona lesionada):
      detector **local, sin nube**; avisar en tablet el procedimiento de emergencia
      (`espera_respuesta: false`); publicar `AlertaSeguridad` en <30 s reales; **cero falsas
      alarmas** (criterio eliminatorio).
      Las condiciones inseguras **no vienen marcadas en el contrato** (`NotaRecorrida` no tiene
      campo "seguridad", la planta no las anuncia a propósito) — la única señal es el texto de la
      nota de voz. Ver `src/sistema/procesamiento/seguridad.py`: `detectar()` corre un matcheo de
      palabras clave (regex, determinístico, nada de LLM ni de nube) sobre la transcripción de
      Whisper, con varias combinaciones redundantes por código para no depender de una frase
      exacta. Se ejecuta en `agente.py` apenas sale de Whisper, **antes** de la extracción con
      Ollama y de cualquier otra llamada de red (historiador, etc.) para entrar cómodo en los 30 s
      reales y para no depender del enlace a internet si está cortado (escenario
      `fuga-sin-enlace`). Si detecta algo, publica un `EventoSistema` (`tipo: AlertaSeguridad`,
      `id` estable `EVT-SEGURIDAD-{nota.id}-{codigo}`) en `sistema/eventos` y un `MensajeTablet`
      con el procedimiento de emergencia (`espera_respuesta: false`, no bloquea esperando al
      operador) en `sistema/tablet`.
      **Nota sobre "aviso por radio al supervisor"**: el contrato de esta entrega no tiene un
      tópico MQTT para eso (no aparece en `docs/contrato.md`); el `EventoSistema` con
      `tipo: AlertaSeguridad` en `sistema/eventos` es la señal que un sistema de notificación al
      supervisor (Entrega 2) consumiría.
      Probado en vivo con las 4 condiciones reales inyectadas por `/api/control/accion`
      (`accion: seguridad`, códigos S1 a S4, frases canónicas de `operador.py::nota_seguridad`):
      las 4 se detectaron y publicaron correctamente. En el camino encontré y arreglé 3 bugs
      reales — detalle completo en `BUGS.md`:
      1. La transcripción real de Whisper deformó "a presión" en "aprecian" (S1 no matcheaba con
         una frase exacta) → el detector ahora usa combinaciones de palabras redundantes, no una
         sola frase fija.
      2. El modelo de Whisper no tenía volumen propio: cualquier *rebuild* del contenedor del
         agente lo perdía y tenía que re-bajarlo de internet en el próximo arranque (mal si el
         enlace está cortado justo en ese momento) → agregado el volumen `whisper-modelos`.
      3. El backend de transferencia nuevo de `huggingface_hub` (`hf-xet`) se cuelga sin avisar
         bajando el modelo a través del proxy de planta → `HF_HUB_DISABLE_XET=1` en el agente.
      10 tests nuevos en `tests/test_seguridad.py` (61 tests en total).

### Fase 3 — Tools y MCP
- [x] 3.1 Tools de solo lectura: equipos · historial de sensores · historial de órdenes ·
      inventario/stock/materiales · catálogo de tareas · búsqueda en manuales. Ver
      `src/sistema/integraciones/tools.py`. Los modelos de **entrada** de cada tool
      (`PedidoHistorialSensor`, `PedidoHistorialOrdenes`, `PedidoInventario`,
      `PedidoCatalogoTareas`, `PedidoBuscarManuales`) son Pydantic estrictos (`extra="forbid"`):
      es nuestro propio contrato. Los de **salida** que envuelven datos de la planta
      (`Documento`, `OrdenHistorica`, `Equipo`) usan `extra="allow"` a propósito — ver bug #12 en
      `BUGS.md`: `GET /api/documentos` mezcla manuales con un documento "historial" de esquema
      distinto, y `GET /api/historial-ordenes` mezcla órdenes del archivo con órdenes en vivo del
      simulador que no comparten exactamente el mismo esquema.
      Inventario/stock y catálogo de tareas no tienen endpoint en la planta (no están en
      `docs/contrato.md`): es dato propio del sistema de mantenimiento, copiado de
      `datos/materiales.yaml` a `src/sistema/datos/materiales.yaml` (ver
      `src/sistema/integraciones/materiales.py`).
      "Búsqueda en manuales" (`tool_buscar_manuales`) es un matcheo de palabras clave sobre
      `GET /api/documentos/{id}/secciones`, no el RAG semántico de la Fase 5 (ese va con
      pgvector) — alcanza para una consulta puntual de solo lectura, y por default excluye
      revisiones no vigentes (la Rev. C de la bomba).
- [x] 3.2 Servidor **MCP** en `src/sistema/integraciones/mcp_server.py`
      (`python -m sistema.integraciones.mcp_server`, por stdio), expone las 7 tools (todas las de
      3.1 más `documentos`). Probado llamando `mcp.call_tool(...)` en proceso contra la planta
      real: `equipos` y `buscar_manuales` devolvieron resultados correctos y tipados.
- [x] 3.3 Manejo de errores, timeouts y reintentos: `src/sistema/integraciones/planta_http.py`
      (`PlantaHTTP._get`) reintenta 2 veces con backoff ante timeout/caída de conexión/5xx, pero
      **no** reintenta un 4xx (no lo arregla reintentar la misma pregunta mal hecha). Si se
      agotan los reintentos, levanta `PlantaHTTPError`, que cada tool atrapa para devolver un
      `error` tipado en la respuesta en vez de una excepción sin manejar.
- [x] 3.4 Tests en `tests/test_tools.py`, `tests/test_planta_http.py` (reintentos, agotamiento,
      no reintentar 4xx) y `tests/test_materiales_datos.py`. Además, probadas en vivo contra la
      planta real (no solo contra fakes) antes de cerrar la fase — así se encontró el bug #12.
      25 tests nuevos (86 en total).

### Fase 4 — Control y evaluación
- [x] 4.1 Condiciones de corte, presupuesto de pasos/tokens, timeout global por nota.
      `src/sistema/llm/control.py`: `ConsumoTokens` cuenta pasos y tokens **reales** (de
      `generation_info` de Ollama vía callback de LangChain, no una estimación);
      `MAX_PASOS_LLM_POR_NOTA = 6` (1 extracción inicial + hasta 5 correcciones).
      `src/sistema/agente.py`: timeout global por nota con `ThreadPoolExecutor` +
      `future.result(timeout=config.timeout_nota_s)` (default 90 s) — si una nota se cuelga, no
      bloquea el proceso para siempre: se publica un evento `Escalado` (`EVT-TIMEOUT-{nota.id}`) y
      un mensaje de fallback a la tablet (nunca quedarse mudo, `sistema_mudo` es eliminatorio).
      Probado en vivo contra el escenario `martes-tranquilo` repetidas veces (ver bugs #13, #17):
      agotar el presupuesto de pasos en medio de un diálogo encontró **dos** bugs reales de "qué
      hacer cuando se agota" (no alcanza con un solo freno, hacen falta en las tres ramas de
      `procesar_respuesta`) — las tres ramas quedan cubiertas ahora: aceptar la fila/el parte tal
      como está y anotar "revisar a mano", nunca seguir preguntando sin fin.
- [x] 4.2 Mini spec del flujo del parte en el repo (Spec-Driven Development) — documentar el
      recorrido: nota de voz → transcripción → extracción → resolución → confirmación → evento.
      Ver `docs/spec-parte-por-voz.md`.
- [x] 4.3 Evaluación contra `GET /api/verdad`: aciertos por campo, y si preguntó solo cuando había
      ambigüedad real (ni de más ni de menos). Script: `evaluacion/evaluar_parte.py` (tests en
      `tests/test_evaluar_parte.py`).
      **Resultado real** (escenario `martes-tranquilo` corrido limpio de punta a punta sin
      inyección manual — el escenario completo tiene 9 notas guionadas en total, es su techo; no
      hay más de donde sacar una muestra más grande para este escenario puntual):
      - 8 de 9 notas con `items` evaluables en la verdad de campo, 16 ítems esperados en total.
      - tag correcto: 69% (11/16) · variable correcta: 44% (7/16) · valor correcto: 31% (5/16).
      - Ambigüedad de equipo: 2 notas con ambigüedad real — preguntó cuando correspondía (TP) 1,
        no preguntó debiendo (FN) 1, preguntó de más (FP) 2.
      **Esta corrida en vivo encontró y arregló 4 bugs reales de diálogo** (ver `BUGS.md` #14 a
      #18 — son 5 entradas, #15 agrupa el bug del resolver y su freno en la misma entrada):
      preguntas de desambiguación que nunca se podían resolver contra el operador real (#14, #15),
      el presupuesto de pasos agotado dejando la misma pregunta repitiéndose para siempre en tres
      ramas distintas del diálogo (#13, #17), y filas duplicadas en el parte cuando una corrección
      frasea el mismo equipo distinto a la nota original (#16, #18 — este último documentado como
      parcialmente resuelto: cubre variaciones menores de frase, no una reformulación completa).
      La exactitud de `valor` (31%) sigue siendo el número más bajo de los tres campos; parte de
      ese error es extracción real del LLM local (`qwen2.5:1.5b`, un modelo chico, sobre una
      transcripción de Whisper que ya tiene su propio ruido) y no un bug de nuestro código — 4.3
      es justamente para medir y reportar esto con honestidad, no para maquillarlo. El bug #18
      quedó arreglado y reconstruido en los contenedores pero no se volvió a correr una muestra en
      vivo completa después (hubiera significado otro ciclo de ~10 minutos de espera real); con
      más partes acumulados orgánicamente durante la Fase 5 se puede volver a evaluar.

### Fase 5 — RAG, contexto y memoria
- [x] 5.1 Indexar el corpus en pgvector con metadatos: equipo, fabricante, documento, sección,
      cita, vigente. `src/sistema/rag/esquema.py` (tabla `rag_fragmentos`, extensión `vector`,
      índice `hnsw` con distancia coseno) + `src/sistema/rag/indexar.py`. Todo se trae por la API
      HTTP de la planta (`GET /api/documentos`, `GET /documentos/fragmentos.jsonl`) — **no** por
      acceso directo al filesystem de `academia2026-tp`: el corrector solo tiene la planta
      corriendo, no necesariamente este repo al lado del nuestro. Embeddings locales
      (`nomic-embed-text` vía Ollama, mismo motivo que el LLM de extracción: no depender de
      internet en el camino crítico). 701 fragmentos indexados (593 vigentes, 108 de la Rev. C
      no vigente, el resto historial). Correr: `docker compose exec agente python -m
      sistema.rag.indexar`.
- [x] 5.2 Trampas del corpus:
      - **Rev. C vencida**: `src/sistema/rag/buscar.py` filtra `vigente=true` **por default** —
        no es una preferencia de ranking, es un filtro duro: la Rev. C ni siquiera compite.
        Medido en 5.3: aparece en el top-5 en 2 de 71 preguntas (con `incluir_no_vigente=False`
        por default, debería tender a 0 — ver limitación abajo).
      - **Secciones que no aplican** (TX-250/500, FC-24/96, PIA-1/2): no se filtran aparte, quedan
        sujetas a la calidad del embedding semántico — los números de 5.3 miden esto directamente,
        no se oculta con una regla hardcodeada.
      - **Códigos de falla del fabricante**: `src/sistema/rag/fallas.py::TraductorFallas`, parsea
        la tabla de MBP-4 §6.1 en vivo (no hardcodeada a mano, para seguir sirviendo si se
        regenera el corpus con otra semilla). Tests en `tests/test_fallas.py`.
- [x] 5.3 Evaluación de retrieval contra `evaluacion/retrieval.json` (71 preguntas, traído por API
      — no por filesystem). Cálculo de hit@k/recall@k/mrr **propio**
      (`evaluacion/evaluar_retrieval.py`), no se usa `corpus.evaluar` de `academia2026-tp` (no es
      instalable y acoplarnos a ese checkout sería frágil si el corrector no tiene ambos repos
      uno al lado del otro). Tests de la métrica en `tests/test_evaluar_retrieval.py` (sin DB).
      **Resultado real** (corpus completo, 701 fragmentos, `k=20` recuperadas por pregunta):
      - hit@k: {1: 0.408, 3: 0.549, 5: 0.648, 10: 0.732} · recall@k: {1: 0.272, 3: 0.413,
        5: 0.495, 10: 0.592} · mrr: 0.504.
      - Baseline BM25 del enunciado: hit@5 0.85 / recall@5 0.66 — no lo alcanzamos. Encontrado y
        documentado en vivo (bug #19, `BUGS.md`): sin los prefijos de tarea que pide
        `nomic-embed-text` (`search_document:`/`search_query:`), el resultado era bastante peor
        (hit@5 0.535); con el fix mejoró a 0.648 pero sigue por debajo de BM25. Hipótesis (no
        comprobada más a fondo por tiempo): en contenido tabular/numérico como los límites de
        operación (la categoría más floja, hit@5 0.364), la coincidencia léxica exacta de BM25 le
        gana al embedding semántico de un modelo chico — las categorías de texto narrativo
        (`procedimiento` 0.875, `certificacion` 0.8, `operacion` 0.818) sí superan el baseline.
      - Distractor en el top-5: 2/71 (`lim-11`, `con-05`) — revisado a mano: en los dos casos el
        "distractor" es una sección **vigente** del documento correcto pero equivocada (no la
        trampa de versiones), p. ej. `TX350-D §2.1` en vez de `§3.1`. Confirmado aparte que
        ninguna de las 71 preguntas trae una cita `vigente=false` entre las 20 recuperadas: el
        filtro duro de la Rev. C funciona al 100%, el campo "distractores" de la suite incluye
        también confusiones de sección dentro del mismo documento, no solo el trap de versiones.
- [x] 5.4 Cada resultado de `buscar()` es un `Fragmento` cuya `cita` **es** el id de sección
      (`TX350-D §3.2`) — no hay forma de devolver un resultado sin cita, viene de la clave
      primaria de `rag_fragmentos`.
- [x] 5.5 Qué sale del RAG y qué no: documentado en `README.md` (sección "RAG: qué sale de ahí y
      qué no"). Límites de alarma (`GET /api/planta`), estado en vivo (`GET /api/estado`), stock y
      catálogo de tareas (`materiales.py`/`materiales.yaml`) y traducción de código de falla
      (`fallas.py`, tabla exacta, no semántica) **no** pasan por RAG.
- [x] 5.6 Context builder: diseño en `src/sistema/rag/contexto.py` (docstring con las 5
      decisiones: fragmentos con cita antepuesta, tope por caracteres no por tokens, descartar
      fragmento entero en vez de truncar, deduplicar por cita, qué datos quedan afuera). La
      implementación completa (función real + prompt) es Entrega 2, como pide el enunciado.
- [x] 5.7 Memoria: `src/sistema/persistencia/memoria.py` — tabla `dialogos_pendientes` (clave
      estable `nota_id`, no `mensaje_id` porque este último cambia en cada vuelta del diálogo),
      guarda el `EstadoDialogo` completo (parte en borrador, fila en pregunta, consumo de
      presupuesto, intentos de desambiguación) como JSONB. Política (docstring del módulo): se
      guarda en cada mensaje enviado, se borra apenas el parte queda `Confirmado` — no hay
      limpieza por tiempo absoluto de diálogos que nunca se contestan, limitación conocida y
      documentada, no resuelta por alcance. `agente.py` carga los diálogos pendientes al arrancar
      (`_cargar_dialogos_pendientes`). Tests de serialización en `tests/test_memoria.py` (sin DB).
      **Verificado en vivo**: con 2 diálogos abiertos de verdad (escenario `martes-tranquilo`),
      reinicié el contenedor del agente y el log mostró
      `recuperados 2 diálogos pendientes de Postgres tras el arranque` — las dos filas
      sobrevivieron intactas al reinicio, confirmando que una conversación en curso ya no se
      pierde si el proceso se cae a mitad de camino.

### Fase 6 — Documentación y verificación final
- [x] 6.1 `README.md`: requisitos, cómo levantar, cómo correr tests, direcciones de pantallas,
      ejemplo de recorrido completo de una nota (con link a `docs/spec-parte-por-voz.md`).
- [x] 6.2 `INFORME.md`: modelos por tarea (los tres 100% locales vía Ollama/CTranslate2, sin nube),
      consumo de tokens real (medido con `ConsumoTokens`: ~2 280 tokens/parte confirmado en
      promedio sobre 12 partes reales), costo (USD 0, todo local), conclusión.
- [x] 6.3 Corrida real contra `corregir.py --escenarios martes-tranquilo fuga-sin-enlace` (el
      corrector automático de `academia2026-tp`, no una simulación): **primera corrida real,
      5.8/100, NO APRUEBA**, con 2 fallas eliminatorias. Se encontraron y arreglaron 3 bugs
      reales de infraestructura/arquitectura en el camino (documentados en `BUGS.md` #19 y en
      esta sección):
      1. Los modelos de Ollama/Whisper se bajaban recién al arrancar el contenedor — el corrector
         borra los volúmenes entre escenarios (`down -v`), así que la descarga (>1 min para
         `qwen2.5:1.5b`, 986 MB) consumía casi todo el tiempo real del escenario. Arreglado
         horneando los modelos en el **build** de la imagen (`ollama/Dockerfile`, `src/Dockerfile`).
      2. `sistema/estado` (el latido, F2) no incluía el campo `ts` que exige el contrato
         (`EstadoSistema`) — se armaba a mano como dict en vez de usar el modelo Pydantic real.
      3. **El más grave**: `on_message` de MQTT (paho-mqtt) esperaba sincrónicamente
         (`futuro.result(timeout=...)`) a que cada nota terminara de procesarse (hasta 90 s) antes
         de volver — eso bloqueaba el loop de red del cliente MQTT, y con él la *transmisión* de
         cualquier mensaje ya encolado con `client.publish(...)`. Medido en vivo: la alerta de
         seguridad se detectaba y encolaba en ~10 s, pero no se transmitía de verdad hasta que la
         nota terminaba de procesarse del todo, ~33 s después — fuera de la ventana de 30 s
         reales del criterio eliminatorio. Arreglado moviendo la espera del corte (y el
         procesamiento de respuestas del operador, mismo problema) a hilos aparte, para que
         `on_message` nunca bloquee.
      También se encontró y arregló una regresión propia de la Fase 5.7 (memoria de conversación):
      `memoria.guardar()`/`eliminar()` corrían **antes** de publicar el mensaje a la tablet —
      cualquier hiccup de Postgres bloqueaba la publicación entera. Se invirtió el orden (publicar
      primero, persistir después) y se hizo la persistencia *best-effort* (nunca bloqueante).
      **Corrida final, con todos los arreglos: 62.0/100, APRUEBA** (mínimo 60). Sin fallas
      eliminatorias. B2 (alerta de seguridad) y F1 (mensajes válidos) al 100%, F2 (latido) al
      96-98%. A2/A3 (confirmar el parte) y B1 (Sin novedad/Escalado) quedan como el punto más
      débil (11-44%): el diálogo de confirmación con el LLM local (CPU, sin GPU, ~5-10 s por
      llamada) no siempre llega a confirmarse dentro de la ventana de tiempo real comprimida de
      un escenario de corrección corto — ver la conclusión de `INFORME.md` para el detalle y las
      mejoras propuestas para la Entrega 2.
- [x] 6.4 Condiciones **eliminatorias**, verificadas explícitamente contra el corrector real:
      - `sin_alerta_de_seguridad`: la corrida inicial SÍ la violó (`fuga-sin-enlace` sin
        `AlertaSeguridad`) — causa raíz y arreglo en 6.3 arriba. La corrida final no tiene
        ninguna falta eliminatoria listada.
      - `sistema_mudo`: la corrida inicial también mostró "el sistema no publicó nada válido" en
        `fuga-sin-enlace` — mismo arreglo (modelos horneados + `on_message` no bloqueante)
        lo resolvió; la corrida final confirma F1 (mensajes válidos) al 100%.
- [ ] 6.5 Commit, push, cargar el link del repo en el formulario de entregas.

## Cómo crear el repo en GitHub (pendiente, lo hacés vos)

1. Andá a https://github.com/new
2. Nombre: `josbert-rizzo`, visibilidad **Public**, no inicialices con README/gitignore (ya los
   tenemos localmente).
3. Copiá la URL que te da (`https://github.com/<tu-usuario>/josbert-rizzo.git`) y pasámela, o
   corré vos mismo:
   ```bash
   git remote add origin https://github.com/<tu-usuario>/josbert-rizzo.git
   git push -u origin master
   ```
4. Avisame cuando esté y seguimos con la Fase 1.

## Convenciones de este plan

- Cada fase se cierra con un commit propio.
- Avisame cuando quieras empezar una fase y seguimos paso a paso desde ahí.
- Este archivo se actualiza a medida que avanzamos (marco `[x]` lo hecho).
