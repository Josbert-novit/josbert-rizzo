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
