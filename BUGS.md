# Bugs encontrados y arreglados

Este archivo documenta bugs reales (no hipótesis) encontrados probando el sistema en vivo contra
la planta simulada (`academia2026-tp`), en orden cronológico por fase. Cada uno se detectó
corriendo el sistema de verdad, no leyendo código.

## Fase 0/1 — Setup y parte por voz

### 1. URL de `planta_contrato` rota en la documentación de la planta
**Dónde**: `docs/contrato.md` de `academia2026-tp` documenta
`pip install "git+https://github.com/NovitSoftware/academia2026-planta#subdirectory=contrato"`.
**Bug**: ese repositorio (`academia2026-planta`) no existe — `git clone` devuelve
"Repository not found". El repo real de la planta es `academia2026-tp`.
**Arreglo**: `pyproject.toml` apunta a
`git+https://github.com/NovitSoftware/academia2026-tp#subdirectory=contrato` (mismo repo que
la planta, carpeta `contrato`), verificado con `pip install --dry-run`.

### 2. `python:3.11-slim` no trae `git`
**Dónde**: `src/Dockerfile`.
**Bug**: `pip install` de un paquete `git+https://...` necesita el binario `git`, que la imagen
base no trae instalado → build del agente fallaba.
**Arreglo**: `apt-get install -y git libgomp1` (`libgomp1` lo necesita `ctranslate2`, el motor de
`faster-whisper`).

### 3. Variable de entorno vacía pisa el valor por defecto
**Dónde**: `src/sistema/config.py`.
**Bug**: `.env.example` traía `TRANSCRIPCION_MODELO_LOCAL=` (vacío). `os.getenv("...", "small")`
devuelve `""` (string vacío) cuando la variable existe pero está vacía — el default de `os.getenv`
solo aplica si la variable **no existe**. Resultado: `ValueError: Invalid model size ''` en
`faster-whisper`.
**Arreglo**: `os.getenv("TRANSCRIPCION_MODELO_LOCAL") or "small"` (el `or` sí cubre el caso de
string vacío) y un valor real en `.env`/`.env.example`.

### 4. Ollama sin proxy no puede bajar el modelo
**Dónde**: `docker-compose.yml`, servicio `ollama`.
**Bug**: `planta-lan` es `internal: true` (sin salida directa a internet). El servicio `ollama`
no tenía `HTTP_PROXY`/`HTTPS_PROXY` → `ollama pull qwen2.5:1.5b` fallaba por DNS.
**Arreglo**: agregado `HTTP_PROXY`/`HTTPS_PROXY=http://enlace:3128` al servicio `ollama`.

### 5. El proxy interceptaba el tráfico de loopback del propio Ollama
**Dónde**: entrypoint del servicio `ollama` en `docker-compose.yml`.
**Bug**: con el proxy ya configurado, `ollama pull` seguía fallando, ahora con un error genérico
("something went wrong, please see the ollama server logs for details") sin nada útil en los
logs del servidor. Causa: `NO_PROXY` no incluía `127.0.0.1`, así que el *cliente* CLI de Ollama
(que habla con su propio servidor en `localhost:11434`) enrutaba esa llamada local a través del
proxy.
**Arreglo**: agregado `127.0.0.1` a `NO_PROXY` y `OLLAMA_HOST=127.0.0.1:11434` explícito antes de
`ollama pull` en el entrypoint.

### 6. `ollama/ollama:0.3.14` no soporta structured output
**Dónde**: imagen del servicio `ollama`.
**Bug**: `langchain-ollama` con `.with_structured_output()` pide `format` como un JSON Schema
(objeto). Esa versión de Ollama solo acepta `format` como string
(`ollama._types.ResponseError: json: cannot unmarshal object into Go struct field
ChatRequest.format of type string`).
**Arreglo**: subida la imagen a `ollama/ollama:0.5.4` (el modelo ya bajado en el volumen siguió
sirviendo sin tener que volver a bajarlo).

### 7. Fakes de test desactualizados tras un refactor
**Dónde**: `tests/test_recorridas.py`, `tests/test_dialogo.py`.
**Bug**: al introducir `interpretar_texto()` como pipeline compartido (resolución + unidades +
rango histórico), pasó a necesitar `resolver.equipo_por_tag()`. Los `ResolverFalso` de los tests
no tenían ese método → `AttributeError` en el test run, no en producción.
**Arreglo**: agregado `equipo_por_tag()` a cada fake.

## Fase 2 — Detección en planta

### 8. Bomba parada/en reserva escalaba falsamente por presión baja
**Dónde**: `src/sistema/procesamiento/eventos.py` (`DetectorSensores`).
**Bug**: probando en vivo con `B-103` (bomba de reserva, `estado: "parada"`), su
`presion_descarga` sostenida en ~1.5 kg/cm² (muy por debajo de `alarma_baja: 95`) generaba un
`EventoSistema` `Escalado`. Es un falso positivo: una bomba parada *siempre* lee presión baja por
diseño, eso no es una anomalía — exactamente el caso que 2.3 pide no escalar ("Parada, en reserva
no es anomalía"). El sensor por sí solo no distingue "bomba apagada" de "bomba en marcha con
problema de succión".
**Arreglo**: agregada `EstadoBombas` (`src/sistema/procesamiento/limites.py`), que consulta
`GET /api/estado` (sí está en el contrato: "estado completo de la planta, lo que ve el visor")
con cache corto de 5 s, y el detector ignora `presion_succion`/`presion_descarga` cuando la bomba
no está `"marcha"`. Verificado con el mismo escenario en vivo tras el fix: ya no escala.

## Fase 2.5 — Alerta de seguridad

### 9. "a presión" transcripto como "aprecian" — el detector de S1 no matcheaba
**Dónde**: `src/sistema/procesamiento/seguridad.py`.
**Bug**: inyecté la condición S1 real (`/api/control/accion`, `accion: seguridad`, código S1) y
dejé correr el pipeline completo (audio sintetizado → Whisper → detector). La transcripción real
de Whisper (modelo `small`) dio:
> "Atención, se rompió algo en la descarga de la del medio, sale agua, **aprecian** para todos
> lados, me alejé, saqué foto desde lejos."

en vez de "...sale agua **a presión** para todos lados...". El detector original buscaba la frase
exacta `"agua a presion"` → no matcheaba, **la alerta no se disparaba** (falso negativo, el peor
caso posible: `sin_alerta_de_seguridad` es un criterio eliminatorio).
**Arreglo**: el detector pasó de "una frase exacta por código" a "varias combinaciones
redundantes de palabras" por código (ver `_PATRONES` en `seguridad.py`), incluyendo `["sale
agua"]` como señal suficiente por sí sola para S1 (no depende de que "presión" se transcriba
bien). Agregado un test de regresión con el texto real deformado
(`test_detecta_s1_aunque_whisper_deforme_a_presion`).

### 10. El modelo de Whisper no sobrevivía a un rebuild del contenedor
**Dónde**: `docker-compose.yml`, servicio `agente`.
**Bug**: el volumen `notas-media` persiste las notas, pero no había ningún volumen para el cache
de Hugging Face (`~/.cache/huggingface`, donde `faster-whisper` guarda el modelo ya bajado). Cada
`docker compose up --build` (cualquier cambio de código) recreaba el contenedor desde cero y
perdía el modelo: el próximo audio a transcribir forzaba una re-descarga completa desde
Hugging Face, con la demora y la dependencia de internet que eso implica — justo lo que una
alerta de seguridad (presupuesto real de 30 s, tiene que andar en el escenario
`fuga-sin-enlace`) no se puede dar el lujo de necesitar.
**Arreglo**: agregado el volumen `whisper-modelos:/root/.cache/huggingface` al servicio `agente`
(mismo patrón que ya existía para `ollama-modelos`). Además, `Transcriptor._cargar_modelo()`
intenta primero `local_files_only=True` y solo si falla (no hay nada cacheado todavía) cae al
modo normal — así, con el volumen ya tibio, ni siquiera se intenta tocar la red.

### 11. `hf-xet` se cuelga sin avisar bajando el modelo a través del proxy de planta
**Dónde**: dependencia transitiva de `huggingface_hub` (`hf-xet`), usada automáticamente por
`faster-whisper` para bajar el modelo.
**Bug**: con el volumen del bug #10 recién creado (todavía vacío) y el fix de `local_files_only`
ya aplicado, el primer arranque se quedó colgado **más de 3 minutos** intentando bajar
`model.bin` (~250 MB): el log mostraba una sola request a
`https://huggingface.co/api/models/.../xet-read-token/...` y después nada, sin error, sin
timeout, sin progreso (confirmado revisando el tamaño real del cache dentro del contenedor:
2.8 MB después de 2 minutos). `hf-xet` es el backend de transferencia nuevo de
`huggingface_hub` (protocolo propio, no HTTP simple) y no respeta bien
`HTTP_PROXY`/`HTTPS_PROXY` dentro de esta red (`planta-lan` sin salida directa, todo pasa por
`enlace:3128`).
**Arreglo**: `HF_HUB_DISABLE_XET=1` en el entorno del servicio `agente` — con eso
`huggingface_hub` vuelve a bajar los archivos por HTTP común (se ve en los logs resolviendo a
`us.aws.cdn.hf.co` con un `GET` normal), y la descarga del modelo completo tardó ~25 s reales.
Verificado reconstruyendo el contenedor desde cero dos veces: la primera baja el modelo (una
sola vez), la segunda ya no toca la red en absoluto (gracias también al volumen del bug #10).

## Fase 3 — Tools y MCP

### 12. `extra="forbid"` demasiado estricto contra datos reales heterogéneos
**Dónde**: `src/sistema/integraciones/tools.py` (`Documento`, `OrdenHistorica`).
**Bug**: los tests con datos fake pasaban, pero al probar las tools contra la planta real
(`GET /api/documentos` y `GET /api/historial-ordenes`) saltó `ValidationError: Extra inputs are
not permitted`. Causas reales, dos:
1. `GET /api/documentos` no solo devuelve manuales — también un documento `"historial"` (índice
   de órdenes) con un esquema completamente distinto (`ordenes`, `desde`, `hasta` en vez de
   `revision`, `vigente`, `equipos`...). Con los fakes de los tests solo había manuales, así que
   no apareció ahí.
2. `GET /api/historial-ordenes` devuelve órdenes de dos orígenes distintos (archivo de dos años
   vs. simulador en vivo) que no comparten exactamente el mismo esquema (una traía un campo
   `descripcion` que el modelo no esperaba).
**Arreglo**: `Documento` y `OrdenHistorica` pasaron a `extra="allow"` (documentan los campos
conocidos, pero no revientan ante uno nuevo) — a diferencia de los modelos de *entrada* de cada
tool (`PedidoHistorialSensor`, etc.), que sí se mantienen `extra="forbid"` estricto: esos son
contrato nuestro, no datos de un tercero.
**Cómo se encontró**: corriendo las tools contra la planta real (no solo contra los fakes de los
tests) antes de darlas por terminadas — los tests con fakes por sí solos no lo iban a atrapar.

## Fase 4 — Control y evaluación

### 13. El presupuesto agotado dejaba una fila preguntando lo mismo para siempre
**Dónde**: `src/sistema/procesamiento/dialogo.py` (`procesar_respuesta`).
**Bug**: al agregar el presupuesto de pasos de LLM por nota (4.1), la primera versión, cuando se
agotaba el presupuesto en medio de una corrección, dejaba la fila con `requiere_confirmacion:
True` ("revisar a mano"). Pero una vez agotado el presupuesto **nunca más** se vuelve a
reinterpretar una corrección (esa es la gracia del límite), así que esa fila queda "pendiente"
para siempre: `construir_mensaje` la vuelve a preguntar, y si la respuesta no es un "sí" literal,
vuelve a caer en la misma rama sin presupuesto, por siempre. Lo encontré probando en vivo contra
el escenario `martes-tranquilo`: el agente quedó mandando la misma pregunta a la tablet cada
pocos segundos, sin parar, para la misma nota.
**Arreglo**: cuando se agota el presupuesto, la fila se da por aceptada tal cual está
(`requiere_confirmacion: False`) y el motivo queda anotado en la `observacion` (visible en el
parte, no bloquea el diálogo) en vez de en `motivo_confirmacion` (que es lo que hace que se
vuelva a preguntar).

### 14. Pedir que elija un equipo mezclado con el borrador completo confunde la respuesta
**Dónde**: `src/sistema/procesamiento/dialogo.py` (`construir_mensaje`).
**Bug**: probando en vivo contra el operador virtual de la planta (`planta_sim/dialogo.py`,
que contesta sola en los escenarios, con la verdad de campo), noté que la pregunta "¿A cuál
equipo te referís con 'el filtro de la izquierda'?" (con `opciones`) nunca se resolvía: el
agente la repetía sin parar con un id de mensaje distinto cada vez. La causa está del lado del
operador virtual: decide si la pregunta es "elegir una opción" o "revisar el borrador completo"
mirando si `pantalla.filas` viene vacío (`if opciones and not filas`). Nuestro `construir_mensaje`
mandaba **siempre** el borrador completo en `pantalla.filas`, incluso al preguntar por opciones
— entonces el operador virtual (y cualquier cliente MCP/UI que siga la misma convención) nunca
entraba a la rama de "elegir opción": comparaba el parte entero contra la verdad de campo y
contestaba otra cosa, sin resolver nunca la ambigüedad real.
**Arreglo**: cuando la pregunta trae `opciones`, `pantalla.filas` va vacío; el borrador completo
solo se manda en las preguntas de confirmación de valor o de parte completo (donde sí tiene
sentido que lo revisen).
**Por qué importa**: este bug afecta directamente la corrección automática real (el corrector
usa este mismo operador virtual), no solo nuestras pruebas manuales — sin el fix, cualquier nota
con un equipo dicho de forma ambigua queda sin resolver nunca.

### 15. El resolver de nombres exigía coincidencia exacta y loopeaba al elegir entre opciones
**Dónde**: `src/sistema/procesamiento/resolucion.py` (`ResolverNombres.resolver`).
**Bug**: con el fix del #14 ya aplicado, dejé correr el escenario `martes-tranquilo` limpio (sin
inyección manual) para juntar la muestra de la evaluación 4.3, y el agente quedó repitiendo "¿A
cuál equipo te referís con 'el filtro de la izquierda'?" cada ~10 s durante varios minutos (26
veces seguidas en los logs). Causa: `resolver()` solo reconocía una respuesta si coincidía
**exactamente** (tras sacar acentos) con el tag o con uno de los `nombres` del equipo. Pero el
operador (virtual o real) no repite el nombre pelado: contesta "La correcta es el filtro A." o
"Era el de la izquierda" (con relleno antes/después y puntuación), que nunca matchea una igualdad
exacta. Al fallar esa comparación, caía al fallback por `tipo` (p. ej. "filtro" aparece como
substring en la frase), que agrupa **todos** los equipos del mismo tipo — con dos filtros, eso es
ambiguo siempre, sin importar qué conteste el operador. Como esta rama no llama al LLM, tampoco
agotaba nunca el presupuesto de pasos (4.1): no había ningún freno, el loop era infinito.
**Arreglo**: dos partes.
1. `resolver()` pasó de "igualdad exacta" a "el nombre (o el tipo, como último recurso) más
   específico que aparece dentro de la frase gana" — el nombre más largo que matchea como
   substring se prioriza sobre el tipo genérico, así "el filtro A" (específico) le gana a
   "filtro" (genérico) aunque los dos aparezcan en la misma respuesta.
2. Como defensa adicional (por si una respuesta realmente no se puede resolver nunca, p. ej. ASR
   muy ruidoso), se agregó un tope de `MAX_INTENTOS_DESAMBIGUACION = 3` en `dialogo.py`: agotados
   los intentos, se toma el primer candidato y se anota "revisar a mano" en la observación, igual
   que ya se hacía para el presupuesto de LLM agotado (#13) — misma idea, pero cubriendo una rama
   que no pasa por el LLM.
**Por qué importa**: mismo motivo que #14 — afecta la interoperabilidad real con el operador
virtual del corrector, no solo el entorno de pruebas. Sin este fix, cualquier desambiguación de
equipo con dos o más candidatos del mismo tipo queda sin resolver nunca.

### 16. Una corrección puntual quedaba duplicada en vez de reemplazar la fila vieja
**Dónde**: `src/sistema/procesamiento/dialogo.py` (`procesar_respuesta`, rama de corrección sobre
una fila con `requiere_confirmacion`).
**Bug**: corriendo la evaluación 4.3 sobre la muestra limpia de `martes-tranquilo` (9 notas, con
los fixes de #14 y #15 ya aplicados), la exactitud de `valor` dio sospechosamente baja (32%).
Inspeccionando los partes guardados nota por nota, varias filas aparecían **duplicadas**: la
extracción original (a veces con un valor mal transcripto/extraído, p. ej. "4.0" en vez de "1.4")
quedaba al lado de una fila nueva con la corrección, en vez de reemplazarla. Causa: cuando se
pregunta por una fila puntual (`fila.tag` ya resuelto, `requiere_confirmacion` por fuera de rango
o por unidad) y el operador responde con una corrección (no un "sí"), el código reinterpretaba la
respuesta con el LLM y fusionaba el resultado contra **todo** el parte por `tag`/`tag_dicho`
(`_mergear_correccion`) — pero ya se sabía exactamente qué fila se estaba corrigiendo (el índice
de la pregunta). Si la nueva extracción frasea el mismo equipo distinto a como lo dijo la nota
original (p. ej. "el de la izquierda" en la corrección vs. "el filtro A" en la nota), el matcheo
por texto no encuentra la fila vieja y la deja huérfana, duplicada, con su valor incorrecto
todavía en el parte.
**Arreglo**: cuando se corrige una fila puntual, se reemplaza directamente `estado.parte.filas[indice]`
con la primera fila que devuelve la reinterpretación (ya se sabe cuál fila es: la que se estaba
preguntando), y solo se usa la fusión genérica por texto para los ítems *extra* que la respuesta
pueda mencionar de paso (otro equipo distinto). Para la corrección en la confirmación final
(`fila_en_pregunta is None`, el operador corrige sin que se le haya preguntado por una fila en
particular) se mantiene la fusión genérica, porque ahí sí hace falta adivinar a cuál fila se
refiere.
**Cómo se encontró**: comparando a mano, fila por fila, los partes guardados contra `GET
/api/verdad` antes de confiar en el número agregado del script de evaluación — el script agregado
por sí solo (32% de aciertos) no decía *por qué*; mirar los datos crudos sí.

### 17. El freno de presupuesto agotado (#13) solo cubría una de las tres ramas que lo necesitan
**Dónde**: `src/sistema/procesamiento/dialogo.py` (`procesar_respuesta`).
**Bug**: con #14, #15 y #16 ya arreglados, corrí de nuevo el escenario `martes-tranquilo` limpio
para la evaluación final, y el agente volvió a quedar en loop: "Parte armado con lo que entendí.
¿Confirmás todo como está?" 34 veces seguidas y "No encontré ningún equipo para 'uno'"/"...para
'pulmón'" 12 y 11 veces seguidas, en la misma corrida. Causa: el freno de presupuesto agotado que
el bug #13 agregó solo se aplicó a **una** de las tres ramas de `procesar_respuesta` que llaman al
LLM condicionalmente (la de confirmar/corregir una fila puntual). Las otras dos ramas —
`fila_en_pregunta is None` (confirmación final del parte completo) y `fila.tag is None` sin
candidatos (equipo no encontrado, reinterpretar como nota nueva) — agotan el mismo presupuesto
(`estado.consumo`) pero no tenían ningún `else` para cuando `_presupuesto_disponible()` da
`False`: simplemente no hacían nada y volvían a preguntar lo mismo en la vuelta siguiente, para
siempre.
**Arreglo**: mismo patrón que #13, aplicado a las otras dos ramas:
- Confirmación final sin presupuesto: se da el parte por confirmado tal como está (se deja de
  preguntar) y se anota "revisar a mano" en la `observacion` de cada fila.
- Equipo no encontrado sin presupuesto: se usa el nombre tal como lo dijo el operador (`tag_dicho`)
  como tag literal (mismo fallback que ya usa `FilaParteInterna.a_contrato()`), para que la fila
  deje de contar como "pendiente" (`_siguiente_fila_pendiente` la seguía marcando pendiente
  mientras `tag` fuera `None`, sin importar `requiere_confirmacion`), y se anota para revisión.
**Por qué importa**: es la tercera vez que aparece la misma clase de bug (ver #13) en el mismo
archivo — una señal de que "agregar un freno" no alcanza si no se aplica sistemáticamente a cada
lugar donde el presupuesto se puede agotar en medio de una pregunta pendiente. Quedó documentado
acá en vez de asumir que #13 ya cubría el caso general.

### 18. `_mergear_correccion` seguía duplicando filas con el equipo sin resolver todavía
**Dónde**: `src/sistema/procesamiento/dialogo.py` (`_mergear_correccion`, usada en la rama de
corrección libre durante la confirmación final, `fila_en_pregunta is None` — el #16 solo arregló
la rama de corregir una fila puntual).
**Bug**: corrí la evaluación 4.3 final (después de #14, #15, #16 y #17) sobre una muestra limpia
nueva de 9 notas, y la exactitud de `valor` no mejoró (31%, contra 32% de la corrida anterior).
Comparando partes contra `GET /api/verdad` fila por fila otra vez, seguía habiendo filas
duplicadas/huérfanas con `valor: null` para el mismo equipo (p. ej. "B-102 presión de descarga"
tres veces en un mismo parte). Causa: `_mergear_correccion` todavía exige que, cuando ninguna de
las dos filas (la vieja y la corregida) tiene `tag` resuelto, `tag_dicho` matchee por **igualdad
exacta** — la misma clase de problema que #16, pero en la otra rama, que no se tocó porque ahí sí
hace falta la fusión genérica (la respuesta puede mencionar más de un equipo a la vez).
**Arreglo**: cuando al menos una de las dos filas ya tiene `tag` resuelto, se compara por tag
(como antes). Cuando ninguna lo tiene, en vez de pedir igualdad exacta de `tag_dicho`, se compara
con contención de substring sobre el texto normalizado (reutilizando `_normalizar` de
`resolucion.py`) — así variaciones menores de frase (con/sin artículo, orden de palabras parcial)
matchean.
**Limitación conocida, documentada en vez de ocultada**: esto no cubre el caso en que la frase
cambia del todo (p. ej. "el de la izquierda" en la nota original vs. "el filtro A" en la
corrección — ningún string es substring del otro). Para ese caso haría falta pasarle un resolver
a `_mergear_correccion` y resolver ambos `tag_dicho` contra el catálogo antes de comparar, lo cual
queda fuera del alcance de este arreglo puntual (día de evaluación 4.3) y se deja anotado acá como
trabajo pendiente real, no resuelto del todo.
**Por qué el número de `valor` (31%) no mejoró más**: parte del error es genuinamente extracción
del LLM local (`qwen2.5:1.5b` es chico, sobre una transcripción de Whisper que ya tiene su propio
ruido) — no todo es un bug de fusión. El objetivo de 4.3 es medir y reportar esto con honestidad,
no maquillar el número.

## Fase 5 — RAG, contexto y memoria

### 19. `nomic-embed-text` sin los prefijos de tarea rendía bastante peor
**Dónde**: `src/sistema/rag/embeddings.py`.
**Bug**: indexé los 701 fragmentos del corpus y corrí la evaluación de retrieval (5.3) contra la
suite real de 71 preguntas (`evaluacion/retrieval.json`): `hit@5 = 0.535`, bastante por debajo del
baseline BM25 del enunciado (0.85). Mirando el detalle, preguntas directas como "¿a qué valor de
vibración dispara la bomba?" ni siquiera traían la sección obvia (`TX350-D §3.2`, "Límites de
operación") entre los primeros 5 resultados.
**Causa**: `nomic-embed-text` (el modelo de embeddings) necesita que el texto venga con un
prefijo de tarea (`"search_document: "` para lo que se indexa, `"search_query: "` para la
pregunta) — es un requisito documentado del modelo, no algo que Ollama agregue solo. Sin eso, el
espacio de embeddings no está bien calibrado y la similaridad empeora notablemente.
**Arreglo**: `Embeddings.embed_documentos`/`embed_consulta` agregan el prefijo correspondiente
antes de llamar al modelo. Reindexado completo y reevaluado: `hit@5` pasó de 0.535 a 0.648,
`recall@5` de 0.401 a 0.495, `mrr` de 0.406 a 0.504 — mejora clara, aunque sigue sin alcanzar al
baseline BM25 en esta corrida (ver notas de 5.3 en `PLAN.md`: el embedding semántico le cuesta más
que la coincidencia léxica de BM25 en contenido tabular/numérico como los límites de operación).
**Cómo se encontró**: evaluando contra la suite real (5.3) antes de dar la indexación por
terminada — un smoke test con una sola pregunta de ejemplo probablemente no lo hubiera mostrado
tan claro como medir hit@5/recall@5 sobre las 71 preguntas reales.

Los bugs #8, #9, #10 y #11 son los más relevantes para la nota final: #8 evita un falso positivo
("cero falsas alarmas" tiene penalidad explícita en la rúbrica); #9 evita un falso negativo
("sin alerta de seguridad ante S1-S4" es *eliminatorio*); #10 y #11 evitan que una alerta de
seguridad real se demore o directamente no llegue por una dependencia de red que no debería
existir en el camino crítico. Los bugs #14 y #15 son igual de críticos para la nota, por otro
motivo: sin ellos, el sistema nunca termina de resolver una ambigüedad de equipo contra el
operador virtual real que usa la corrección automática — no es un problema de nuestro entorno de
pruebas, es un problema de interoperabilidad con el corrector. Los dos se encontraron de la misma
forma: dejando correr el sistema completo contra un escenario real sin ninguna inyección manual, y
mirando los logs en vivo — ningún test con fakes los hubiera atrapado, porque los fakes no imitan
la forma en que el operador real frasea sus respuestas.

## Fase 6 — Verificación final contra el corrector automático

Corrida real de `academia2026-tp/correccion/corregir.py --escenarios martes-tranquilo
fuga-sin-enlace` (el corrector que efectivamente va a evaluar la entrega), no una simulación
manual. Primera corrida: **5.8/100, NO APRUEBA**, con 2 fallas eliminatorias. Los 4 bugs de acá
abajo explican casi todo ese salto hasta el **62.0/100, APRUEBA** de la corrida final — todos se
encontraron porque el corrector arranca el stack *desde cero, en un entorno con las mismas
restricciones de tiempo real* que nuestras pruebas manuales nunca ejercitaron (volúmenes siempre
limpios, escenarios cortos y comprimidos).

### 20. Los modelos se bajaban al arrancar el contenedor, no en el build
**Dónde**: `docker-compose.yml` (servicio `ollama`), `src/Dockerfile`.
**Bug**: el corrector borra los volúmenes entre escenario y escenario (`docker compose down -v`)
para aislar cada corrida. Eso significa que el volumen `ollama-modelos`/`whisper-modelos` (que
hasta ahora evitaba redescargas en reinicios normales, bugs #10/#11) arranca **vacío** en cada
escenario de la corrección. Medido en vivo: bajar `qwen2.5:1.5b` (986 MB) tardó más de 1 minuto
real — un escenario corto dura apenas 1-2 minutos reales en total, así que la descarga por sí
sola se comía casi toda la ventana, dejando al agente sin tiempo para procesar ninguna nota.
**Arreglo**: los modelos se bajan en el **build** de la imagen Docker (`ollama/Dockerfile` nuevo,
`src/Dockerfile` con un paso que invoca `WhisperModel(...)` durante el build), no en el arranque
del contenedor — así quedan horneados en la imagen, inmunes a que el volumen se borre.
**Cómo se encontró**: corriendo el corrector real por primera vez, no se podía haber visto de
otra forma — las pruebas manuales de este proyecto nunca borraron los volúmenes entre corridas.

### 21. `sistema/estado` no tenía el campo `ts` que exige el contrato
**Dónde**: `src/sistema/agente.py::publicar_estado`.
**Bug**: el latido se armaba a mano como un dict (`{"sistema": ..., "enlace_ok": ..., ...}`) en
vez de usar el modelo Pydantic real del contrato (`EstadoSistema`), y ese dict nunca tuvo el
campo `ts` (obligatorio). El corrector invalida cualquier mensaje que no matchee el contrato —
esto hacía fallar el criterio F1 (mensajes válidos) y F2 (latido) en el 100% de los latidos.
**Arreglo**: `publicar_estado()` construye un `EstadoSistema(...)` real (con `ts=datetime.now(...)`)
y publica `estado.model_dump_json()` — si en el futuro el contrato agrega un campo obligatorio
más, esto vuelve a fallar en vez de publicar silenciosamente algo inválido, que es preferible.

### 22. `on_message` de MQTT bloqueaba el loop de red — la alerta de seguridad se transmitía tarde
**Dónde**: `src/sistema/agente.py` (`_procesar_nota_con_corte`, rama `RESPUESTAS` de `on_message`).
**Bug**: el más grave de los cuatro, y el que causaba la falta eliminatoria
`sin_alerta_de_seguridad`. `_procesar_nota_con_corte` esperaba sincrónicamente
(`futuro.result(timeout=config.timeout_nota_s)`, hasta 90 s) adentro del callback `on_message` de
paho-mqtt. Ese callback corre en el mismo hilo que el loop de red del cliente
(`client.loop_forever()`): mientras está bloqueado ahí adentro, el cliente no puede ni recibir
mensajes nuevos ni, más grave, **transmitir** los que ya se encolaron con `client.publish(...)`
(quedan en el buffer de salida hasta que el loop vuelve a correr). Medido en vivo contra el
corrector: la detección de S1 (agua a presión) se logueaba a los ~10 s de recibida la nota
(`detectar()` corre antes que la extracción, rápido y sin LLM — tal como pide 2.5), pero el
mensaje `sistema/eventos` con `tipo: AlertaSeguridad` no llegaba de verdad al broker hasta que esa
misma nota terminaba de procesarse del todo (extracción con LLM incluida), ~33 s después —
confirmado comparando el log del agente (ALERTA logueada a las 18:39:10) contra el registro crudo
del corrector (`ts_real` del mensaje recibido: 18:39:43). Eso cae fuera de los 30 s reales que
exige el criterio eliminatorio.
**Arreglo**: la espera del corte se mueve a un hilo (`threading.Thread`) aparte
(`_vigilar_corte`), y lo mismo para procesar una respuesta del operador (rama `RESPUESTAS`, que
también puede llamar al LLM) — se manda al mismo `_pool_notas` en vez de bloquear `on_message`.
`client.publish()` es thread-safe en paho-mqtt, así que no hace falta ninguna otra sincronización.
**Por qué importa**: es el bug con más impacto de toda la sesión — una falla eliminatoria no es
un problema de puntaje, es la diferencia entre aprobar o no. Y es un recordatorio de que "la
detección es rápida" no alcanza si el mensaje que la reporta queda atrapado atrás de otro trabajo
más lento en el mismo hilo.

### 23. Memoria de conversación (5.7): se persistía ANTES de publicar, no después
**Dónde**: `src/sistema/agente.py::_avanzar_dialogo` (introducido en la Fase 5.7, en esta misma
sesión).
**Bug**: al agregar la persistencia en Postgres del estado de diálogo, `memoria.guardar()`/
`memoria.eliminar()` quedaron llamados **antes** de `client.publish(Topicos.TABLET, ...)` /
`client.publish(Topicos.EVENTOS, ...)`. En la primera corrida contra el corrector (Postgres recién
arrancando, antes de que `pg_isready` confirmara que ya aceptaba conexiones) una de esas llamadas
tiró `psycopg.OperationalError: connection refused` **sin capturar**, lo que crasheaba
`_avanzar_dialogo` antes de llegar al `client.publish(...)` — el mensaje a la tablet directamente
nunca se mandaba. Esto es una regresión que la propia Fase 5.7 introdujo sobre una feature que
antes andaba bien.
**Arreglo**: dos partes. (1) se invirtió el orden — publicar primero, persistir después — así un
problema de Postgres nunca le cuesta el mensaje ya construido; (2) la persistencia pasó a ser
*best-effort* (`_memoria_best_effort`, atrapa cualquier excepción y loguea, nunca propaga). Además
se agregó un `healthcheck` a `postgres` en `docker-compose.yml` (`pg_isready`) y
`depends_on: {condition: service_healthy}` en `agente`/`central`, para que la carrera de arranque
sea mucho menos probable de entrada — pero la robustez real viene del best-effort, no de ganarle
la carrera.
**Por qué importa**: una funcionalidad nueva (5.7) no puede ser un punto único de falla para una
funcionalidad vieja que ya andaba (el diálogo de confirmación). Documentado acá como recordatorio
de orden de operaciones: lo que **tiene** que pasar (publicar) va antes de lo que **ayuda a que
pase mejor** (persistir para sobrevivir un reinicio).

**Resumen de la Fase 6**: los bugs #20 y #22 son, junto con #9 (Fase 2.5), los más críticos de
todo el proyecto — los tres tocan directamente alguno de los dos criterios eliminatorios. Ninguno
se podía haber encontrado sin correr el corrector automático real: todos dependen de condiciones
que las pruebas manuales de este proyecto nunca reprodujeron (volúmenes siempre limpios en cada
escenario, ventanas de tiempo real comprimidas, un Postgres recién arrancando). La lección general
de toda la sesión, no solo de esta fase: cada fase de `PLAN.md` que se dio por terminada después
de "anda bien en mis pruebas manuales" encontró al menos un bug real al probarla contra el entorno
real (la planta en vivo primero, el corrector automático al final) — diecinueve de los
veintitrés bugs de este archivo (#8 en adelante) se encontraron así, no leyendo código.

## Fase 15 (Entrega 2) — Verificación en vivo

### 24. El análisis de órdenes compartía el pool de las notas — una orden lenta dejaba mudas a las notas
**Dónde**: `src/sistema/agente.py::_avanzar_dialogo`/`_procesar_evento_escalado_con_corte`
(Fases 8 y 10).
**Bug**: el análisis de una orden (Fase 10, LLM local) se enviaba al mismo `_pool_notas` (un solo
worker) que usan las notas de voz — y en el caso disparado desde `_avanzar_dialogo` (un evento
`Escalado` que sale de un parte confirmado), se llamaba encima **sin ningún corte/vigía**, por el
razonamiento de que "ya estamos en el hilo del pool, no hace falta otra vuelta". Probado en vivo
contra el escenario `ataque`: el análisis de una sola orden tardó **2m29s** (el modelo local con
un contexto largo y un schema anidado), y durante ese tiempo dos notas completamente distintas
(`NOTA-00001`, `NOTA-00007`) se cortaron a los 90 s cada una porque el único worker de
`_pool_notas` estaba ocupado con la orden — exactamente la misma clase de bug que el #22 de
Entrega 1 (una tarea lenta bloqueando otra que no tiene nada que ver), pero reintroducida en esta
entrega al reusar el pool existente en vez de aislar la nueva carga de trabajo.
**Arreglo**: pool propio `_pool_ordenes` (análisis de órdenes y procesamiento de cierres, Fase
14), separado de `_pool_notas`; la llamada desde `_avanzar_dialogo` pasó a ir siempre por
`_procesar_evento_escalado_con_corte` (pool + vigía con `timeout_nota_s`), nunca inline.
**Por qué importa**: "ya estamos en un hilo de background" no es lo mismo que "esta tarea no
puede frenar a otra" — dos cargas de trabajo independientes (notas, órdenes) necesitan pools
independientes, aunque las dos corran siempre fuera del hilo de red de MQTT. Solo se encontró
corriendo el escenario real `ataque` contra el stack completo, no en los tests unitarios (que
usan fakes instantáneos y nunca ejercitan la contención real del pool).

### 25. `rag_fragmentos` nunca se indexaba — y el primer arreglo (indexar sincrónico) dejó al sistema mudo
**Dónde**: `src/sistema/llm/analisis.py::armar_contexto` (vía `rag.buscar.buscar`), `src/sistema/agente.py::main`.
**Bug**: indexar el corpus (`python -m sistema.rag.indexar`) siempre fue un paso manual (documentado
en el README, corrido a mano durante el desarrollo) — nunca se ejecuta solo al levantar el stack.
En Entrega 1 esto no rompía nada porque ningún camino automático llamaba a `rag.buscar`. En
Entrega 2, `llm/analisis.py` SÍ lo llama en cada análisis de orden — y el corrector recrea los
volúmenes de Postgres en cada escenario (`docker compose down -v` antes de cada uno), así que la
tabla `rag_fragmentos` literalmente no existe nunca en una corrida real. Confirmado en los logs
del corrector (`correccion/corridas/entrega2-ataque/ataque.log`): cada intento de análisis de
orden terminaba en `psycopg.errors.UndefinedTable: relation "rag_fragmentos" does not exist`,
y por lo tanto ninguna orden llegaba nunca a publicarse (D1/D2 quedaron "sin datos").
**Arreglo (primer intento, insuficiente)**: `agente.py::main()` llamando a `indexar(http,
embeddings)` sincrónico antes de conectar a MQTT. Arregló el `UndefinedTable`, pero rompió algo
peor: el corpus real tiene ~700 fragmentos, y embeberlos con Ollama en esta CPU sin GPU tarda
varios **minutos** — bloqueando el primer latido (`sistema/estado`) de los 4 escenarios de la
corrida siguiente (`tormenta-perfecta`, `sin-luz`, `contratista`, `fuga-sin-enlace`) más allá de
los 180 s reales que el corrector espera antes de soltar el reloj. Resultado: nota 15.1/100 con
dos eliminatorias nuevas (`sin_alerta_de_seguridad`, `sistema_mudo`) — mucho peor que el problema
original, confirmado también corriendo el corrector real (`correccion/corridas/entrega2-completa`).
**Arreglo (el que queda)**: separar las dos partes. `_crear_esquema_rag_best_effort()` solo corre
el DDL (`crear_esquema`, sin llamar a Ollama, milisegundos) sincrónico al arrancar — así la tabla
existe desde el primer mensaje, aunque esté vacía. El indexado real (`indexar`, el que tarda
minutos) pasa a `_indexar_corpus_en_segundo_plano()`, en su propio hilo daemon, arrancado después
de conectar a MQTT — el agente publica su primer latido enseguida, y el corpus queda listo para
citar manuales unos minutos después, sin que nada tenga que esperarlo.
**Por qué importa**: dos lecciones en una. (1) un paso de setup que "ya se corrió una vez a mano"
es invisible para cualquier entorno que arranque de cero (mismo tipo de trampa que el bug #20 de
Entrega 1, acá del lado de los datos propios en vez de los modelos). (2) la solución obvia a (1)
— indexar al arrancar — puede ser peor que el problema si no se separa lo rápido (que el sistema
dependa de eso) de lo lento (que puede esperar): el patrón "nunca bloquear algo rápido con algo
lento" (pool+vigía, bugs #22 y #24) también aplica al arranque, no solo al loop de mensajes. Ni el
primer ni el segundo intento se habrían encontrado sin correr `correccion/corregir.py` de verdad.

### 26. Una pieza del equipo ("el rodamiento", "el pulmón") no tiene tope propio de reintentos y gasta todo el presupuesto de la nota
**Dónde**: `src/sistema/procesamiento/dialogo.py::procesar_respuesta` (rama `fila.tag is None`
sin candidatos).
**Bug**: cuando el operador nombra una pieza del equipo en vez de un equipo real ("el rodamiento",
"el pulmón", "la punta"), `ResolverNombres.resolver` nunca va a encontrar ningún candidato —no
importa cuántas veces se reinterprete la misma frase, el resultado va a ser `no_encontrado` otra
vez. Antes de este fix, esta rama solo paraba cuando se agotaba `MAX_PASOS_LLM_POR_NOTA` (6,
compartido con el resto de correcciones de la nota), así que una sola fila irresoluble podía
gastar las 6 llamadas al LLM de la nota entera. Confirmado contra el corrector real
(`correccion/corridas/entrega2-completa-v2` y `-v3`): varias notas con este patrón se cortaban a
los 90 s (`ERROR nota NOTA-00003: no se terminó de procesar en 90 s, se corta`) sin llegar nunca a
`Confirmado` — A2/A3 en 0%, y como el `Escalado` de esas notas tampoco llegaba a dispararse a
tiempo, también restaba datos a B1/D2.
**Arreglo**: la misma idea que ya tenía la rama de desambiguación ambigua (`MAX_INTENTOS_
DESAMBIGUACION`, bug #15): un tope chico (3) de reintentos propio de esta fila, separado del
presupuesto completo de la nota. Agotados los 3 intentos, se usa el nombre dicho como tag literal
y se marca "revisar a mano" — sin esperar a que se acabe el presupuesto entero.
**Por qué importa**: no todas las llamadas al LLM dentro del presupuesto de una nota valen lo
mismo — reintentar una frase que *nunca* va a resolver (porque no nombra ningún equipo) no es lo
mismo que reintentar una corrección real, y dejarla consumir el mismo presupuesto completo le
resta intentos útiles al resto de la nota además de arriesgar el corte por tiempo.

### 27. La indexación RAG de fondo y el análisis de una orden compiten por el mismo Ollama, aunque corran en pools separados
**Dónde**: `src/sistema/rag/indexar.py::indexar`, `src/sistema/agente.py::_procesar_evento_
escalado`/`_indexar_corpus_en_segundo_plano`.
**Bug**: separar la indexación en su propio hilo (bug #25) evita bloquear el primer latido, pero
Ollama en esta CPU (sin GPU) sirve un pedido a la vez — la indexación de fondo (701 fragmentos) y
el análisis de una orden terminan en la misma cola de ese único servidor, aunque corran en pools
de Python distintos. Confirmado en vivo (`entrega2-completa-v2`/`v3`): con lotes de 32 fragmentos,
un análisis de orden se cortaba a los 90 s esperando su turno; con lotes más chicos (4, mitigación
parcial) el análisis seguía sin ninguna llamada HTTP nueva por **más de 3 minutos** en un caso
real, mucho más que la ventana real de un escenario del corrector.
**Arreglo**: un `threading.Event` (`sistema.llm.control.analisis_orden_en_curso`) que
`_procesar_evento_escalado` prende antes de llamar al análisis y apaga al terminar. La indexación
(`indexar()`, con el callback `debe_pausar`) espera mientras esté activo antes de mandar cada lote
nuevo a Ollama. **Ampliación tras seguir viendo cortes a los 280 s en vivo** (con 8 eventos
`Escalado` de un mismo equipo encolados detrás del primero en `_pool_ordenes`): la indexación no
era la única competencia — las notas de voz (`Extractor.extraer`, `ExtractorCierre.extraer`,
pool separado `_pool_notas`) también llaman al mismo Ollama, y con el fix del bug #26 las notas
pasan más rápido por el diálogo, generando **más** tráfico de notas en el mismo tiempo real. Se
agregó `ceder_turno_si_hay_orden_en_curso()` (mismo módulo), llamado al inicio de ambos
extractores: mientras haya un análisis de orden en curso, una nota espera su turno antes de
mandar su propia llamada al LLM (con un tope defensivo de 60 s, para no dejar una nota colgada
para siempre si el evento quedara prendido por algún bug). La indexación es de fondo y sin apuro
(puede tardar de más); una nota puntúa bastante menos que D1+D2+F3+G1 juntos (30 de 93 puntos) —
no tiene sentido que todo compita en igualdad de condiciones por el mismo recurso escaso.
**Por qué importa**: dos pools de Python separados (bug #24) evitan que una tarea lenta bloquee el
*hilo* de otra, pero no evitan que compitan por un recurso externo compartido y serializado
(acá, un único servidor Ollama sin GPU) — ese tipo de contención hay que resolverlo explícitamente
(pausar lo que puede esperar), no alcanza con "correr en threads distintos".

### 28. La indexación RAG al arrancar dejaba sin turno a las notas: se cortaban casi todas a los 90 s (H1, H2)
**Dónde**: `src/sistema/agente.py::_indexar_corpus_en_segundo_plano`, `src/sistema/rag/indexar.py`.
**Bug**: el corrector borra los volúmenes en cada escenario (`down -v`), así que el sistema
reindexaba los 701 fragmentos con `nomic-embed-text` desde cero cada vez. Esa indexación ocupaba el
Ollama de CPU durante todo el escenario (nunca pasaba de ~100/701) y solo se pausaba durante un
análisis de orden, no durante una nota. En `entrega2-completa-v6` se cortaron 11 de 13 notas en
`tormenta-perfecta` y 16 de 19 en `contratista`, sin ninguna orden publicada: **33.7/100**.
**Arreglo**: búsqueda **híbrida**. La indexación hace dos pasadas: primero carga el texto sin
embeddings (en segundos, y con eso ya anda la búsqueda por palabras con `tsvector` en Postgres),
después calcula los embeddings de a 4 y se pausa con `trabajo_urgente` (un contador que prenden
las notas, las respuestas y los cierres). La búsqueda une los dos rankings con RRF: si todavía no
hay embeddings, o si falla el de la pregunta, responde igual con la mitad por palabras.
**Por qué importa**: lo que puede esperar (los embeddings) tiene que ceder ante lo que puntúa con
tiempo real (las notas). Además, el sistema tiene que servir desde el primer minuto, no después de
indexar todo.

### 29. Dos hilos creaban tablas a la vez sobre la base recién creada (H6)
**Dónde**: `src/sistema/agente.py::main` (esquema de fotos vs. indexación).
**Bug**: `duplicate key value violates unique constraint "pg_type_typname_nsp_index"` al arrancar.
Dos `CREATE TABLE IF NOT EXISTS` en paralelo no son seguros en Postgres: el `IF NOT EXISTS` no
cubre la carrera sobre el catálogo.
**Arreglo**: el esquema de fotos se crea en `main()` antes de arrancar los hilos
(`_crear_esquema_fotos_best_effort`).
**Por qué importa**: `IF NOT EXISTS` evita el error al repetir, no al concurrir. El DDL se corre
una vez y en un solo hilo.

### 30. El análisis de órdenes no terminaba nunca: generación sin tope del modelo chico (H7)
**Dónde**: `src/sistema/llm/analisis.py`, `src/sistema/procesamiento/ordenes.py::procesar_evento_escalado`.
**Bug**: en `lote1-contratista` seguía habiendo 0 órdenes: la única llamada de análisis no
terminó en 280 s. El esquema que veía el LLM incluía `evidencia_texto` (un campo que llena el
código), y no había `num_predict`, `num_ctx` ni tope de listas: `qwen2.5:1.5b` generaba sin fin.
Además, si el análisis levantaba una excepción, el evento quedaba sin orden.
**Arreglo**: `evidencia_texto` fuera del esquema (`SkipJsonSchema`), listas con `max_length`
(3 causas, 4 tareas, 6 repuestos), `num_predict=450`, `num_ctx=4096`, timeout HTTP de 90 s. Si
falla, cae a una orden de **inspección**.
**Por qué importa**: con un modelo chico hay que acotar la salida por todos lados (esquema,
tokens, tiempo), y siempre tener un plan B determinístico.

### 31. Revertido el "ceder el turno" del #27: hacía esperar 60 s a cada nota y no servía (H8)
**Dónde**: `src/sistema/llm/extraccion.py`, `src/sistema/llm/cierre.py`, `src/sistema/llm/control.py`.
**Bug**: con un análisis colgado (#30), cada llamada de una nota esperaba el máximo de 60 s antes
de usar Ollama: 13 de 19 notas cortadas a los 90 s. Además Ollama sí atendía en paralelo, así que
la espera ni siquiera evitaba la competencia.
**Arreglo**: las notas ya no ceden el turno. La prioridad se invirtió: lo que espera ahora es el
análisis (ver #33), y las notas pasan primero.
**Por qué importa**: una espera "defensiva" multiplicada por cada llamada de cada nota es peor que
el problema que quería evitar. La prioridad va del lado de lo que puntúa con tiempo real.

### 32. Nombres de equipo que no resolvían: "pulmón", "filtro B", "la 1" contra "1.1" (H4, H13)
**Dónde**: `src/sistema/procesamiento/resolucion.py`, `interpretacion.py`, `dialogo.py`.
**Bug**: el LLM extrae "pulmón" y la planta lo lista como "el pulmón". La comparación por
caracteres no los hacía coincidir. "B" a secas tampoco resolvía a "el filtro B". Una pieza
("empaquetadura") salía como fila suelta y el diálogo preguntaba "¿podés repetirlo?" sin salida
posible (el operador simulado contesta "no te entendí").
**Arreglo**: comparación **por palabras** sin el artículo inicial, y se acepta que lo dicho sea el
final de un nombre. Una pieza hereda el único equipo nombrado en la nota. Una fila sin equipo pero
con valor pregunta con todas las opciones, y una fila sin equipo ni valor ya no se pregunta. Los
nombres que son solo un número tienen que aparecer completos ("me dio 1.1" ya no nombra a la 1).
**Por qué importa**: cada callejón sin salida del diálogo deja una nota sin confirmar, y eso arrastra
A2, A3, B1 y D2.

### 33. La orden dependía del LLM: sin análisis no había orden (H10, H12)
**Dónde**: `src/sistema/procesamiento/ordenes.py::orden_inmediata`, `src/sistema/agente.py::_procesar_evento_escalado_con_corte`.
**Bug**: con el análisis acotado (#30) seguía tardando minutos por escalado en esta CPU, y las
órdenes llegaban tarde o no llegaban (D2, F3).
**Arreglo**: cada `Escalado` genera **en segundos y sin LLM** una orden de inspección (`I-VIB` para
vibración, temperatura o ruido; `I-VAL` para el resto). Las reglas la evalúan, y como esas tareas
no son estándar, la orden siempre queda `PendienteAprobacion`. No se duplica con una orden abierta
del mismo equipo y tareas: se le suma el evento. El análisis del LLM corre después, con menor
prioridad y en su propio pool (con un máximo de 2 en cola, para no frenar los cierres), y queda
como **sugerencia** para el planificador (`GET /api/ordenes/{id}/sugerencia`): nunca se publica.
**Por qué importa**: lo que puntúa (que exista una orden correcta y a tiempo) no puede depender de
lo más lento y menos confiable del sistema. Además, como la sugerencia nunca se publica, una nota
con instrucciones inyectadas no puede hacer aprobar nada.

### 34. Falsa alarma de seguridad S4: "golpeteo" + "manómetro" (H14)
**Dónde**: `src/sistema/procesamiento/seguridad.py`.
**Bug**: la regla de persona lesionada buscaba "golpe" y "mano" como pedazos de palabra, y
matcheó "hace un *golpe*teo metálico… le saqué foto al *mano*́metro" (`lote3-completa`,
`tormenta-perfecta` NOTA-00017). B2 bajó a 88%. Una falsa alarma resta y puede ser eliminatoria.
**Arreglo**: palabras completas ("golpe", "golpeó", "golpeado"… y "mano"/"manos"), con dos tests
de regresión con la transcripción real. Verificado en vivo en `lote4-completa`: B2 100%.
**Por qué importa**: en un detector por palabras clave, un substring corto es una falsa alarma
esperando a pasar.

### 35. Un solo evento por parte, y solo por valores: las fallas dichas de palabra no escalaban (H15, H16)
**Dónde**: `src/sistema/procesamiento/eventos.py::eventos_del_parte`, `src/sistema/procesamiento/novedades.py`.
**Bug**: el corrector decide **por cada equipo de cada nota** si correspondía `Escalado` o
`SinNovedad`, y cualquier observación del operador que no sea "parada, en reserva" o "parada por
mantenimiento" ya es una anomalía. Nosotros publicábamos un solo evento por parte (el resto de los
equipos quedaba sin evento) y solo escalábamos por un **valor** fuera de límite. Una falla dicha
de palabra ("pierde por la empaquetadura", "hace un golpeteo", "el visor de aceite está bajo")
terminaba en `SinNovedad` y sin orden: B1 29%, D2 50%, F3 0% en `lote3-completa`.
**Arreglo**: detector determinístico de fallas dichas (sin LLM, mismo criterio que la alerta de
seguridad), atribuidas al equipo del tramo de la nota. Un tramo sin equipo hereda el último
nombrado, porque Whisper corta "la dos… El rodamiento… Hace un golpeteo" en oraciones separadas.
Un evento por equipo del parte. Validado offline con las transcripciones reales contra la verdad
del corrector: 83 de 86 equipos bien. En vivo (`lote4-completa`): B1 29% → 41%, F3 0% → 100%.
**Por qué importa**: leer cómo se mide (la función del corrector) mostró que el problema era la
**forma** de los eventos, no solo la detección.

### 36. El evento de "nota cortada" era un Escalado sin equipo que arruinaba todos los equipos sin novedad (H18)
**Dónde**: `src/sistema/agente.py::_vigilar_corte`.
**Bug**: cuando una nota pasaba los 90 s se publicaba `EVT-TIMEOUT-{nota}` como `Escalado` **sin
equipo**. El corrector cuenta un evento sin equipo para **todos** los equipos de esa nota, así que
cada equipo que debía quedar en `SinNovedad` daba error: 10 notas así en `tormenta-perfecta`
(B1 11.5% en ese escenario).
**Arreglo**: ese evento ya no se publica. La tablet sigue avisando ("estamos procesando…"), así que
el sistema no queda mudo, y la nota publica sus eventos por equipo al confirmarse. Verificado en
`lote4-tormenta`: 11.5% → 27%.
**Por qué importa**: un evento "por las dudas" no es neutro: se puntúa igual que los demás.

### 37. Los eventos esperaban a que el operador confirmara, y algunas notas no se confirman nunca (H19)
**Dónde**: `src/sistema/agente.py::_escalar_novedades_dichas`, `src/sistema/procesamiento/novedades.py`.
**Bug**: el operador simulado solo confirma borradores con filas **con valor**. A una nota sin
números ("La 2 pierde bastante por la empaquetadura…", `ataque`) contesta "¿Confirmar qué?", y como
el `Escalado` salía recién al confirmar, la falla F01 de B-102 nunca tuvo orden (D2). En ráfagas
de notas (`tormenta-perfecta`), varias tampoco llegan a confirmarse antes del fin del escenario.
**Arreglo**: apenas se transcribe la nota, las fallas dichas que se pueden atribuir seguro publican
`Escalado`, con el mismo id que el evento del parte (y su orden de inspección). Los equipos
nombrados sin ninguna falla publican `SinNovedad` con un id **distinto** (`EVT-NOTA-…`): el
corrector se queda con la primera publicación de cada id, así que un `Escalado` posterior por
valor sale igual. Si en la nota queda una falla sin atribuir, no se publica ningún `SinNovedad`
temprano. Validado sobre las corridas reales: 26 escalados y 138 `SinNovedad` tempranos correctos,
0 en equipos equivocados.
**Por qué importa**: lo que se puede decidir solo con la nota no tiene por qué esperar al diálogo,
igual que la alerta de seguridad.

### 38. Un SinNovedad temprano y el del parte confirmado eran "duplicados" para el corrector (H20)
**Dónde**: `src/sistema/agente.py::_avanzar_dialogo` (`_sin_novedad_ya_publicados`).
**Bug**: con #37, un equipo sin falla publicaba `SinNovedad` al llegar la nota (`EVT-NOTA-…`) y
otra vez al confirmar el parte (`EVT-PARTE-…`). `sin_duplicados` (E3) agrupa por nota, tipo y equipo
y cuenta dos ids distintos como duplicado: E3 bajó a 88% en `lote4b-completa`.
**Arreglo**: al confirmar no se repite el `SinNovedad` de los equipos que ya lo tuvieron temprano.
Se recalcula con la misma función sobre el mismo texto, así que no depende de memoria del proceso
y sobrevive a un reinicio. Verificado en `lote4c-completa`: E3 100%.
**Por qué importa**: cuando un mismo hecho se publica por dos caminos, hay que decidir cuál es la
publicación "oficial", no publicar dos veces.

### 39. El latido cada 30 s nunca caía dentro de un corte de enlace que dura segundos reales (H21)
**Dónde**: `src/sistema/agente.py::publicar_estado`.
**Bug**: el reloj simulado corre rápido. En `fuga-sin-enlace` el corte duraba pocos segundos
reales, y con un latido fijo cada 30 s ninguno salía mientras el enlace estaba caído: el corrector
nunca veía `enlace_ok=false` (E2).
**Arreglo**: el estado se revisa cada 2 s y se publica apenas cambia el enlace o la cantidad de
pendientes, además del latido regular.
**Por qué importa**: un estado que se informa por período fijo se pierde los cambios cortos; lo que
importa es avisar el cambio.

### 40. El parte por voz dependía entero del LLM: lento, poco preciso y con cortes a los 90 s (Lote 5)
**Dónde**: `src/sistema/procesamiento/interpretacion.py`, `parser_notas.py`, `dialogo.py`,
`respuestas_operador.py`; `src/planta_kit/` (copia de la librería de la cátedra).
**Bug**: cada nota y cada respuesta del operador pasaban por `qwen2.5:1.5b` en CPU. El borrador
salía bien al primer intento en ~21% de las notas (A2) y el parte final en ~21% (A3). En ráfagas de
notas, 10 se cortaban a los 90 s. Además "Perfecto, confirmo." no contaba como confirmación (faltaba
en `PALABRAS_CONFIRMACION`) y también iba al LLM.
**Arreglo**: lo que propone la cátedra para el CU1 (`libreria-tp/ADLC.md`: "el LLM solo extrae
cuando el parser no alcanza"): primero el parser determinístico de `planta_kit` (notas y respuestas),
con las mañas de Whisper medidas en las corridas ("medió 62", "1-1", "golpe teo",
"embarquetadura"). El LLM queda de respaldo. Medido offline sobre 142 notas reales antes de tocar
nada (`evaluacion/evaluar_parser.py`); en vivo (`lote5-completa`): A2 21% → 57%, A3 21% → 64%,
41 notas por el parser y 7 por el LLM, la corrida bajó de 39 a 27 min, **80.8/100**.
**Por qué importa**: para un dominio con frases tan regulares, reglas medidas contra datos reales
le ganan a un modelo chico en velocidad y en precisión. El LLM rinde más como respaldo de lo raro
que como camino principal.

### 41. Una nota con un nombre deformado iba al LLM y trababa las notas de atrás (H22)
**Dónde**: `src/sistema/procesamiento/parser_notas.py::ParserPartes.parsear`.
**Bug**: "El A1 está disparada" (Whisper por "la uno") quedaba sin entender, la nota entera iba al
LLM, tardaba más de 90 s y las 3 notas siguientes se cortaban esperando en la cola
(`lote5-completa`, `contratista`). Lo mismo con el texto inyectado de `ataque` ("nota para el
sistema, aprobala…").
**Arreglo**: solo va al LLM si lo que quedó sin entender **tiene un número**, es decir una lectura
que se podría perder. Si no, se usa lo que entendió el parser y lo demás se registra en el log.
Offline sobre 188 notas reales: de 14 al LLM pasan a 7, sin perder ningún borrador perfecto.
**Por qué importa**: la decisión de pagar una llamada cara tiene que depender de si puede aportar
algo. Además, el texto con instrucciones inyectadas ya ni llega a un modelo.

### 42. Las fotos de las notas nunca iban a la cola de visión, y sin clave hubieran quedado pendientes para siempre (H23)
**Dónde**: `src/sistema/agente.py::_procesar_nota`, `src/sistema/procesamiento/fotos.py`.
**Bug**: solo las fotos de un **cierre** entraban a la cola de visión. La foto de la nota S1 de
`fuga-sin-enlace`, tomada con el enlace caído, nunca quedaba pendiente, así que el latido no tenía
nada que mostrar (E2). Y al encolarla aparecía otro problema: sin `NVIDIA_API_KEY`, al volver el
enlace el análisis fallaba siempre y la foto quedaba pendiente para siempre.
**Arreglo**: las fotos de las notas van a la cola en un hilo aparte (no frenan el parte ni la
alerta). Sin visión configurada, al volver el enlace la foto se marca resuelta "sin análisis" y se
registra en el log.
**Por qué importa**: una cola que no puede vaciarse nunca es tan mala como no tener cola.
"Pendiente" tiene que significar "se va a resolver solo cuando vuelva el enlace".

### 43. El LLM de respaldo trababa la cola aunque no tuviera nada que leer (H24)
**Dónde**: `src/sistema/procesamiento/parser_notas.py::ParserPartes.analizar`, `interpretacion.py::interpretar_texto`, `llm/extraccion.py`.
**Bug**: en `ataque` (un escenario corto en tiempo real), "el filtro B me dio diez de diferencial"
llegó transcripto como "El filtro V-Medial DSD diferencial". El parser no sacó ninguna fila, la
nota fue al LLM, que tardó más de 90 s, y la nota siguiente se cortó esperando hasta que terminó el
escenario. Ese texto no tiene ningún número, así que ningún modelo podía recuperar la lectura.
Aparte, una nota con "la bomba" a secas y sin valor ("¿La bomba está parada en reserva?") no
preguntaba cuál bomba, y la verdad del corrector sí lo cuenta como ambigüedad (A4a).
**Arreglo**: (a) sin números en lo no entendido, no se llama al LLM, haya o no filas; (b) el
extractor LLM tiene timeout de 45 s y, si falla o se pasa, se usa lo que entendió el parser; (c) la
ambigüedad de equipo se pregunta aunque la fila no tenga valor.
**Por qué importa**: un respaldo lento no puede ser bloqueante, y hay que poder decidir de antemano
que no vale la pena llamarlo.

## Auditoría de cumplimiento del Anexo Entrega 2 (19/11)

### 44. Todo cierre por MQTT reventaba con `NameError` (G1)
**Dónde**: `src/sistema/agente.py::_procesar_cierre_con_corte`.
**Bug**: el log final usaba `diferencias`, que solo existe dentro de `_procesar_cierre`. Cada
`NotaCierre` levantaba `NameError` dentro de `on_message`. No apareció en ninguna corrida porque
el corrector solo manda cierres de órdenes `Aprobada`, y el sistema nunca publicó una.
**Arreglo**: el log se movió adentro de `_procesar_cierre`, junto al correlation ID. Hay un test
que llama a `_procesar_cierre_con_corte` de punta a punta.
**Por qué importa**: un camino que el corrector no ejercita también necesita un test que lo
recorra entero.

### 45. Un cierre pasaba a `Ejecutada` cualquier orden, incluso Pendiente o ya Certificada
**Dónde**: `src/sistema/agente.py::_procesar_cierre`.
**Arreglo**: solo una orden `Aprobada` pasa a `Ejecutada`. Si ya estaba `Ejecutada`, el cierre se
toma como repetido y se ignora. En cualquier otro estado queda una advertencia en el log y la orden
no cambia.

### 46. `reduce_inyeccion` siempre en `False`
**Dónde**: `src/sistema/procesamiento/ordenes.py`.
**Arreglo**: ahora vale `detiene_equipo and not reserva`, calculado con la reserva real en ese
momento (`/api/estado`), como pide el anexo. El corrector no mide este campo; la revisión manual sí.

### 47. El patrón "válvulas en dos bombas → cartucho de F-201B" no podía dispararse nunca
**Dónde**: `src/sistema/llm/analisis.py::armar_contexto`.
**Bug**: el contexto solo pedía el historial de órdenes del propio equipo, así que el patrón que
cruza bombas no tenía con qué compararse. Además faltaba la quinta causa repetida del anexo
(B-102 que cavita → filtro primario colmatado).
**Arreglo**: se pide además el historial de toda la planta de los últimos 10 días, y solo cuentan
las bombas. Para la cavitación se usa `codigo_falla` F09 o, como el historial es texto libre de
campo, la palabra "cavit…" en el aviso o en el trabajo realizado.

### 48. La visión mandaba a la nube una URL interna de la planta
**Dónde**: `src/sistema/llm/vision.py`.
**Bug**: las fotos viven en `http://sim:8100/data/...`, dentro de `planta-lan`. NVIDIA NIM no puede
bajarlas, así que con una API key el análisis hubiera fallado igual. Nunca se vio porque la key
está vacía.
**Arreglo**: la foto se baja de la planta y se manda embebida (data URI en base64). La respuesta
ahora es por componente (empaquetadura, correas, acople, manómetro, filtro), con estado, hallazgo y
la lectura del manómetro si se ve. Se tolera el JSON envuelto en texto.

### 49. El LLM del cierre inventaba tareas y repuestos, y competía con las notas por la CPU (H25)
**Dónde**: `src/sistema/agente.py::_procesar_cierre`, `src/sistema/llm/cierre.py`.
**Bug**: con G1 activo llegaron los primeros cierres reales (corrida `g1-tarea-estandar`). El LLM
local le atribuyó a un cambio de cartuchos las tareas T-B-ROD, T-B-ALI e I-VIB y el repuesto "C48",
copiados de los ejemplos del prompt. Además el cierre (Whisper + LLM) llegó mientras entraban
notas: se cortó a los 90 s y A2 de `tormenta-perfecta` bajó de 92% a 54%.
**Arreglo**: `procesamiento/parser_cierre.py`, parser determinístico primero y LLM de respaldo. El
cierre espera hasta 120 s a que no haya notas en curso antes de transcribir.

### 50. El parser del cierre no reconocía la transcripción real de Whisper
**Dónde**: `src/sistema/procesamiento/parser_cierre.py`.
**Bug**: la primera versión se anclaba en "Hice", "Usé" y en la puntuación. El audio real, bajado
de la planta y transcripto con el mismo Whisper, llega así: "…y se cambió de Cartuchos C48 Cartucho
fitrante 25 micrones quedó en servicio… fueron 2,8 horas". No se reconoció ningún cierre en vivo y
en `ataque` el LLM no terminó antes del fin del escenario (G1 50%).
**Arreglo**: busca los nombres del catálogo en todo el texto, con comparación aproximada por
palabra. Los números del nombre tienen que coincidir exacto. Lee la cantidad justo antes del nombre
("cuarenta y ocho", "48", "un", "C48") y las horas en "3 con 2" o "2,8". Hay tests con las
transcripciones reales. Corrida `g1c-parser-whisper`: G1 100%.
**Por qué importa**: un parser se prueba contra lo que sale de Whisper, no contra el texto que se
le dio al sintetizador de voz.

### 51. El modelo de visión planeado ya no existe y el reemplazo devolvía la respuesta vacía

**Dónde**: `src/sistema/llm/vision.py`, `src/sistema/config.py`.
**Bug**: con la key cargada, `meta/llama-3.2-90b-vision-instruct` responde 410 Gone (NVIDIA lo dio
de baja). El reemplazo, `nvidia/nemotron-3.5-super-vl-120b-a12b`, razona antes de contestar: con
`max_tokens: 400` el razonamiento se come todo, `content` llega `None` y `_extraer_json` falla
con un `AttributeError` que no dice nada.
**Arreglo**: modelo por defecto Nemotron VL, `chat_template_kwargs.enable_thinking=false` solo para
Nemotron, timeout de 60 s, y error claro si no hay contenido. Probado en vivo: ~2-3 s por foto.
**Por qué importa**: los tests con cliente falso no podían ver ninguno de los dos problemas. Un
modelo de nube hay que probarlo contra la nube.

### 52. Los servicios de planta sumaban 7 CPU, y las notas competían por la CPU con los embeddings

**Dónde**: `docker-compose.yml`, `src/sistema/rag/indexar.py`, `src/sistema/llm/extraccion.py`.
**Bug**: el contrato pide 4 CPU / 8 GB entre todos los servicios de la PC de planta, y el agente
tenía 3 y Ollama 4. Además, en la corrida `vision-nemotron` (83.0): en contratista una nota cayó al
LLM de respaldo, que con Ollama ocupado en los embeddings tardó minutos y frenó la fila (4 notas
cortadas a 90 s); en fuga-sin-enlace Whisper tardó 14 s en vez de 5 por un lote de embeddings y la
nota se contestó después del corte (E1 0%).
**Arreglo**: agente 2.5 + Ollama 1.5 = 4 CPU (la memoria ya daba 2 + 6 = 8 GB). Embeddings de a 1
fragmento, para que la pausa por nota urgente llegue en segundos. Tope de 25 s al LLM de respaldo
de la extracción (antes 45 s): si no contesta, queda lo del parser. Corrida `cpu4`: 88.7, E1 100%.
**Por qué importa**: con menos CPU, lo que decide si una nota se contesta a tiempo es que nada de
fondo le gane a Whisper.

## Lote 6 — velocidad y fiabilidad (20/11)

Origen: análisis de la corrida `vision-nemotron` (83.0), que perdió 5.7 puntos solo por CPU, y una
auditoría de fiabilidad de `agente.py`. Plan y alcance en `PLAN.md`, sección "Lote 6".

### 53. Whisper usaba todos los núcleos del host aunque el contenedor tuviera 2.5 CPU

**Dónde**: `src/sistema/llm/transcripcion.py`, `src/sistema/config.py`, `docker-compose.yml`.
**Bug**: `WhisperModel` se creaba con los defaults (`cpu_threads=0`): CTranslate2 abre un hilo por
núcleo del host y el cgroup del contenedor lo frena a la fuerza. Con la máquina cargada, en
`vision-nemotron` Whisper tardó 13.1 s por nota de mediana (40 s p90) contra 4.7 s en `g1c`, y 13
de 49 notas pasaron los 90 s. Ollama tenía el mismo problema (4 hilos con 1.5 CPU). Además el
modelo se cargaba sin lock (una nota y un cierre juntos podían cargar dos) y recién con la
primera nota.
**Arreglo**: `WHISPER_HILOS=2` y `OLLAMA_HILOS=2`, lock de carga y precarga al arrancar. Medido
antes de elegir con `evaluacion/evaluar_whisper.py` (90 audios armados por el simulador con su
ruido de planta, transcritos en la imagen del agente con `--cpus 2.5`): 2 hilos y beam 5 bajan la
mediana de 4.1 a 3.3 s y el p90 de 5.9 a 4.6 s **sin cambiar ninguna nota** (45 perfectas en las
dos). Beam 2 con VAD era más rápido (2.9 s) pero mandaba una nota más al LLM; el VAD solo hacía
más lento a beam 5.
**Por qué importa**: la corrección corre en una máquina que no controlamos; lo que decide si una
nota se contesta a tiempo es que Whisper no pelee con el cgroup.

### 54. Los embeddings del corpus se recalculaban en cada escenario

**Dónde**: `src/sistema/rag/indexar.py`, `src/sistema/datos/embeddings_corpus.npz`.
**Bug**: el corrector borra los volúmenes entre escenarios (`down -v`), y cada arranque volvía a
embeber ~700 fragmentos con el Ollama de CPU durante minutos (en contratista de `vision-nemotron`:
140/701 a los 4.5 min), compitiendo con Whisper y con el LLM de las notas.
**Arreglo**: cache versionado (620 textos, 1.7 MB) con clave = hash del modelo + texto. Al arrancar
se cargan los vectores de los fragmentos que coinciden y solo se embebe lo demás (otro corpus u
otro modelo vuelven al comportamiento anterior). Se genera con `python -m sistema.rag.indexar
--exportar-cache`; verificado que cada vector es idéntico (coseno 1.0) al que da Ollama de a uno.

### 55. La sugerencia del LLM ocupaba Ollama 90 s aunque llegaran notas

**Dónde**: `src/sistema/llm/ollama.py` (nuevo), `src/sistema/agente.py::_sugerencia_llm`.
**Bug**: la sugerencia (que nunca se publica) esperaba a que no hubiera notas para *empezar*, pero
una vez arrancada no se podía cortar: en contratista de `vision-nemotron` dos llamadas de 90 s
coincidieron con una ráfaga de notas y 7 se cortaron detrás.
**Arreglo**: los clientes de Ollama de baja prioridad quedan registrados; cuando llega o arranca
una nota, respuesta o cierre, se cierra su conexión HTTP (Ollama deja de generar al perder el
cliente; probado: el pedido se corta en 1 ms) y la sugerencia se reintenta cuando el sistema se
desocupa (hasta 3 veces). Todos los clientes de Ollama salen ahora de una sola fábrica con timeout
y `keep_alive=-1` (se vio una recarga de modelo de 6.6 s en medio de una nota).

### 56. Las fotos se bajaban antes de la alerta de seguridad

**Dónde**: `src/sistema/procesamiento/recorridas.py`, `src/sistema/agente.py`.
**Bug**: `descargar_y_transcribir` bajaba todas las fotos (hasta 30 s cada una, con reintentos)
antes de devolver el texto, o sea antes de `detectar()`: una foto lenta podía sacar la alerta de
seguridad de los 30 s del criterio eliminatorio.
**Arreglo**: audio → Whisper → alerta; las fotos se bajan después, en un hilo aparte.

### 57. El corte de 90 s contaba la espera en la cola, y la nota podía quedar sin respuesta

**Dónde**: `src/sistema/agente.py` (`_vigilar`, `_vigilar_nota`).
**Bug**: el vigía empezaba a contar al encolar la nota: en una ráfaga, notas que solo esperaban su
turno recibían "TIMEOUT". Y mientras tanto el operador no recibía nada: en fuga-sin-enlace de
`vision-nemotron` la respuesta salió 3 s después de que volvió el enlace (E1 0%).
**Arreglo**: el corte cuenta desde que la nota arranca; y si a los 8 s (`ACUSE_NOTA_S`) no salió
ningún mensaje a la tablet para esa nota, se manda un acuse ("recibí tu nota…") con
`espera_respuesta=false`: el operador simulado no lo contesta y no cuenta como pregunta (A4b), pero
sí como respuesta durante un corte (E1).

### 58. Una excepción en el camino de sensores terminaba el agente, y había HTTP dentro del loop de MQTT

**Dónde**: `src/sistema/agente.py` (`on_message`, `_procesar_lectura`, `_procesar_cierre_con_corte`).
**Bug**: las lecturas de sensores se procesaban dentro de `on_message`, sin try/except y con
llamadas HTTP (estado de bombas, tendencia del historiador: hasta ~31 s con reintentos). paho
relanza las excepciones de los callbacks: una caída del simulador cortaba `loop_forever` y el
agente moría; un simulador lento frenaba el envío de todo lo publicado, alerta incluida. Las fotos
de un cierre también se encolaban dentro de `on_message` (Postgres + enlace + descarga + visión).
**Arreglo**: pool propio para sensores (un worker, mismo orden, con tope de cola), fotos de cierre
en un hilo, y `on_message` envuelto: ningún mensaje puede cortar el loop de red.

### 59. Las órdenes se marcaban publicadas aunque el broker no las hubiera recibido

**Dónde**: `src/sistema/agente.py::reconciliar_ordenes`, `src/sistema/persistencia/ordenes.py`.
**Bug**: se publicaba con QoS 0 sin mirar el resultado y se marcaba `pendiente_publicar=false`
igual: con el broker caído unos segundos, la orden quedaba marcada y no salía nunca (justo lo que
la persistencia de la Fase 12 tenía que evitar). Además, si la central o un escalado cambiaban la
orden entre la lectura y la marca, la versión nueva quedaba marcada sin publicarse.
**Arreglo**: todo lo que publica el sistema va con QoS 1 (paho guarda y reenvía al reconectar); la
orden se marca solo si el broker confirmó, y solo si `actualizado_en` no cambió. La planta publica
notas y respuestas con QoS 1: la suscripción también es QoS 1 y una nota repetida se ignora por
id. No se usa sesión persistente a propósito: el broker no se reinicia entre escenarios del
corrector y traería mensajes de un escenario al siguiente.

### 60. Hilos que morían callados y esperas sin tope

**Dónde**: `src/sistema/agente.py`, `src/sistema/persistencia/conexion.py` (nuevo), `src/sistema/llm/cierre.py`, `src/sistema/rag/embeddings.py`.
**Bugs**:
- el latido no tenía try/except (un error lo cortaba para siempre) e informaba 0 pendientes si
  Postgres fallaba (un falso "todo sincronizado");
- el LLM del cierre y los embeddings no tenían timeout: un Ollama trabado bloqueaba para siempre
  el pool de órdenes (ningún cierre más llegaba a `Ejecutada`);
- Postgres sin `connect_timeout` ni `statement_timeout`, y con todo el DDL (incluido un `ALTER
  TABLE`) en **cada** conexión — el latido abre dos cada 2 s;
- la orden inmediata se perdía para siempre si Postgres no respondía en ese momento (con el evento
  ya publicado), y el cierre tenía una conexión abierta mientras corría Whisper;
- el arranque reintentaba el parser hasta ~3 min **antes** de conectarse a MQTT (sin latido:
  `sistema_mudo` espera 180 s), y si fallaba todo iba al LLM para siempre;
- sin manejo de SIGTERM: Python era PID 1, ignoraba `docker stop` y lo mataban a los 10 s.
**Arreglo**: latido con try/except y último valor conocido; timeouts en todos los clientes de
Ollama y en Postgres; DDL una vez por proceso; reintentos con backoff para la orden inmediata y
el cierre; conexión a MQTT y latido primero, preparación en paralelo (las notas la esperan hasta
60 s) y parser reintentado en segundo plano; `init: true`, handler de SIGTERM y healthchecks
informativos. 20 tests nuevos de orquestación en `tests/test_lote6.py`.

### 61. La red `backend` daba salida a internet saltando el enlace

**Dónde**: `docker-compose.yml`.
**Bug**: el agente corre en la PC de planta y el contrato dice que sale a internet solo por
`enlace:3128`, pero también estaba en `backend`, una red común con salida directa.
**Arreglo**: `backend` pasa a `internal: true`; la central suma una red `publica` solo para
exponer el puerto 8200.

### 62. Deformaciones de Whisper que hacían perder una falla o un equipo

**Dónde**: `src/sistema/procesamiento/novedades.py`, `src/sistema/procesamiento/parser_notas.py`.
**Bugs**: "colqueteo/golpecheo/golpe de metálico" no escalaban el golpeteo (B1 en tormenta); "el
filtro B-Mario 10" / "Bmedio 10" / "BME10" iban al LLM, que devolvía una fila sin equipo ni valor
y se pedía confirmar un parte vacío (A4a en ataque); "Está del fondo" no se reconocía; y una regla
vieja convertía "la del medio 115" en "la del me dio 115", perdiendo el equipo.
**Arreglo**: "metálico" alcanza para el golpeteo (en la verdad de campo de todas las corridas
aparece solo así, 20 de 20); normalizaciones nuevas para el filtro B y "la del fondo"; la regla de
"medió" ya no toca "la del medio". `evaluar_parser` sobre las 759 notas reales: 618 → 620
perfectas, ninguna nueva que falle.
**No arreglable**: las notas sin ningún número ("parada, en reserva", "pierde por la
empaquetadura", la alerta S1) nunca suman A2/A3: el corrector solo cuenta borradores con filas con
valor y el operador simulado no confirma un borrador sin valores (`puntuar.py::borradores_de`).

### 63. El mismo evento escalado dos veces pisaba una orden ya Aprobada

**Dónde**: `src/sistema/agente.py::_orden_inmediata_best_effort`.
**Bug**: una falla dicha de palabra escala al llegar la nota (L4.9) y otra vez al confirmarse el
parte, con el **mismo id** de evento. El id de la orden sale del id del evento, y la segunda vez la
evidencia (mezclada con otras novedades del parte) daba otra tarea: no era "equivalente" y el UPSERT
pisaba la orden. En `lote6-velocidad` (ataque) una T-F-CAR **Aprobada**, ya publicada y con el
técnico despachado por la planta, pasó a I-VAL **Pendiente**; el cierre llegó sobre una orden no
aprobada y no se ejecutó (G1 75%). En `cpu4` no se vio de casualidad (otro orden de llegada).
**Arreglo**: si ya hay una orden para ese evento, no se vuelve a decidir. 2 tests de regresión.
**Por qué importa**: una decisión publicada no puede cambiar sola por una segunda pasada del mismo
dato — para el planificador es incoherente y para la planta ya es una orden en ejecución.

### 64. "El A1" (por "la uno") mandaba la nota al LLM y se perdía el borrador

**Dónde**: `src/sistema/procesamiento/parser_notas.py`.
**Bug**: Whisper escribe "El A1" por "la uno" (17 de 17 veces en las corridas guardadas). El parser
no lo reconocía, la nota iba al LLM y en `lote6-velocidad` (tormenta, NOTA-00018) el LLM se cortó
a los 25 s: sin borrador (A1, A3, B1).
**Arreglo**: normalización "el/en A1|A2|A3" → "la 1|2|3", sin tocar decimales. `evaluar_parser`
sobre 840 notas reales: 685 → 701 perfectas, 34 → 18 al LLM, ninguna falla nueva.

### 65. Una foto que no se podía analizar quedaba pendiente para siempre

**Dónde**: `src/sistema/procesamiento/fotos.py::reconciliar_fotos_pendientes`.
**Bug**: con la visión configurada (`NVIDIA_API_KEY`), cualquier error al analizar una foto la
dejaba en la cola y se reintentaba cada 5 s sin fin: una foto que no existe (404) o una key o
modelo inválidos mantenían `pendientes_sincronizar > 0` para siempre, y E2 pide que la cola quede
en 0 después del corte. Lo encontró `evaluacion/verificar_entrega2.py` (31/32): el 19/11 pasaba
porque todavía no había key y la foto se resolvía "sin análisis".
**Arreglo**: un 4xx (salvo 408/429) es permanente → la foto se resuelve sin análisis; timeouts, 5xx
y 429 se reintentan hasta 5 veces. 2 tests nuevos; la verificación en vivo vuelve a 32/32.

### 66. El ranking por palabras enterraba las órdenes del historial (`ts_rank_cd`)

**Dónde**: `src/sistema/rag/buscar.py::_ranking_lexico`.
**Bug**: medido con el evaluador oficial de la planta (`evaluar_retrieval.py --oficial`, 71
preguntas, 24/11), la categoría `historial` daba hit@5 0.18 contra 0.82 del BM25 de la cátedra: las
órdenes cortas del historial casi nunca aparecían. Con una consulta OR, `ts_rank_cd` (densidad de
cobertura) premia a las secciones largas de manual que juntan muchas palabras sueltas cerca.
**Arreglo**: `ts_rank` en vez de `ts_rank_cd`. Se probaron también las normalizaciones por largo de
Postgres (1 y 2) en las dos funciones: todas peores. Resultado de la híbrida: hit@5 0.690 → 0.746,
recall@5 0.563 → 0.624, mrr 0.552 → 0.574, historial 0.18 → 0.45.
**Qué no se hizo**: bajar el peso de la mitad semántica en el RRF acerca la híbrida al full-text
(con peso 0.3, hit@5 0.803), pero sería ajustar sobre las mismas 71 preguntas con que se evalúa. Se
deja RRF con pesos iguales. Solo lo usa el análisis de órdenes (sugerencia al planificador, nunca
publicada), así que no cambia nada de lo que mide el corrector automático.
