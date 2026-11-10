# Spec: parte de mantenimiento por nota de voz

Recorrido completo de una nota de voz del operador (`planta/recorridas`) hasta el evento de
sistema que termina publicándose (`sistema/eventos`), pasando por el diálogo de confirmación en
la tablet (`sistema/tablet` / `planta/respuestas`). Documenta el flujo tal como está implementado
hoy, con los límites y decisiones de diseño que importan para entenderlo — no es una introducción
genérica al problema.

## 0. Entrada: `on_message` → `_procesar_nota_con_corte`

`src/sistema/agente.py`

El agente está suscripto a `planta/#` por MQTT. Cuando llega un mensaje en `planta/recorridas`
(`NotaRecorrida`), se despacha a `_procesar_nota_con_corte`, que corre el procesamiento real
(`_procesar_nota`) en un `ThreadPoolExecutor(max_workers=1)` con un timeout global
(`config.timeout_nota_s`, default 90 s — 4.1).

Por qué: una nota colgada (un audio corrupto, Whisper que se traba, Ollama que no responde) no
puede bloquear el proceso para siempre — `sistema_mudo` (el sistema deja de contestar) es un
criterio eliminatorio. Si se cumple el timeout, se publica un evento `Escalado`
(`EVT-TIMEOUT-{nota.id}`) y un `MensajeTablet` de fallback ("Estamos procesando tu nota, puede
demorar") en vez de quedarse sin responder.

Las notas se procesan de a una, en el orden en que llegan (`max_workers=1`) — no hay
procesamiento paralelo de notas distintas.

## 1. Descarga y transcripción

`src/sistema/procesamiento/recorridas.py::descargar_y_transcribir` +
`src/sistema/llm/transcripcion.py::Transcriptor`

1. Si la nota trae `audio_url`, se descarga (`PlantaHTTP.descargar`) y se transcribe con
   `faster-whisper` (modelo local, `language="es"`). El texto se persiste en
   `{media_dir}/{nota.id}/transcripcion.txt`.
2. Las fotos de la nota (si hay) se descargan también, pero no se usan en la extracción todavía
   (ver limitación más abajo).

Por qué local: en el escenario `fuga-sin-enlace` el enlace a internet se corta, y la transcripción
tiene que seguir funcionando sin nube. `Transcriptor._cargar_modelo()` intenta primero
`local_files_only=True` (el modelo ya debería estar en el volumen `whisper-modelos` de un arranque
anterior) y solo si falla cae a la descarga normal — así un reinicio del agente durante un corte no
se queda esperando a Hugging Face (bugs #10 y #11 en `BUGS.md`).

La separación `descargar_y_transcribir` / `armar_parte` (en vez de un único paso) es deliberada:
permite que la detección de alerta de seguridad (paso 2) corra sobre el texto crudo apenas sale de
Whisper, sin esperar a la extracción con LLM ni a ninguna otra llamada de red — importa para el
presupuesto real de 30 s que pide la consigna de seguridad.

## 2. Alerta de seguridad (antes que nada más)

`src/sistema/procesamiento/seguridad.py::detectar` — llamado desde `agente.py::_procesar_nota`
inmediatamente después de la transcripción, antes de la extracción.

Matcheo de palabras clave determinístico (regex), **no LLM**: ni más lento ni probabilístico,
"cero falsas alarmas" es un criterio eliminatorio, y tiene que funcionar sin depender del enlace
(`fuga-sin-enlace`) ni de Ollama. Cada código (S1-S4) se reconoce por combinaciones redundantes de
palabras (no una frase exacta) — ver bug #9 en `BUGS.md`: la transcripción real de Whisper deformó
"a presión" en "aprecian", y una frase exacta no la hubiera detectado.

Si hay detección: se publica un `EventoSistema` (`Escalado`) y un `MensajeTablet` de aviso
(`espera_respuesta=False`, no bloquea el diálogo de confirmación del parte) — y el procesamiento
de la nota **sigue** con la extracción normal (la alerta no reemplaza al parte, lo complementa).

## 3. Extracción con LLM

`src/sistema/llm/extraccion.py::Extractor` — invocado desde
`src/sistema/procesamiento/interpretacion.py::interpretar_texto`.

Un LLM local (Ollama, `qwen2.5:1.5b` vía `langchain-ollama`, `temperature=0`,
`.with_structured_output(FilasExtraidas)`) convierte el texto libre en una lista de `FilaParte`
(contrato: `tag` tal como lo dijo el operador sin resolver, `variable`, `valor`, `unidad`,
`observacion`). El prompt (`PROMPT_SISTEMA`) pide explícitamente no resolver el nombre del equipo
contra ningún listado — eso es el paso siguiente — y no inventar datos que la nota no menciona.

Control de presupuesto (4.1, `src/sistema/llm/control.py`): cada llamada pasa un
`_CallbackConsumo` que lee `prompt_eval_count`/`eval_count` reales de la respuesta de Ollama (no
una estimación) y los acumula en un `ConsumoTokens` por nota. `MAX_PASOS_LLM_POR_NOTA = 6` (1
extracción inicial + hasta 5 correcciones durante el diálogo, paso 6 de este documento) —
`verificar_presupuesto()` corta el ciclo antes de que una nota mal interpretada genere un ida y
vuelta indefinido con el operador.

## 4. Resolución de nombres, unidades y rango histórico

`src/sistema/procesamiento/interpretacion.py::interpretar_texto` orquesta los tres pasos
siguientes para cada `FilaParte` extraída, y produce una `FilaParteInterna` (el tipo interno, con
más campos que el contrato: `tag_dicho`, `ambiguo`, `candidatos`, `requiere_confirmacion`,
`motivo_confirmacion`). Este mismo pipeline se reusa para reinterpretar una corrección del
operador durante el diálogo (paso 6) — no está duplicado.

### 4.1 Resolución de nombres — `src/sistema/procesamiento/resolucion.py::ResolverNombres`

Resuelve el nombre informal del operador ("la dos", "el de la izquierda") contra
`GET /api/planta` (`equipos`, cada uno con una lista de `nombres` coloquiales). Tres resultados
posibles: `resuelto` (un tag), `ambiguo` (varios candidatos del mismo tipo) o `no_encontrado`.

El matching no es por igualdad exacta: se busca, dentro de la frase completa, el nombre conocido
(o como último recurso el tipo de equipo) **más específico** que aparece como substring — el más
largo que matchea gana. Esto importa porque el operador (virtual o real) no repite el nombre
pelado al contestar una pregunta de desambiguación, sino con relleno ("la correcta es el filtro
A.") — ver bug #15 en `BUGS.md`: con igualdad exacta, esto caía siempre al fallback genérico por
tipo y quedaba ambiguo para siempre entre los dos equipos del mismo tipo.

### 4.2 Unidades — `src/sistema/procesamiento/unidades.py::resolver_unidad`

Para variables de presión en una bomba, la unidad la define el **manómetro local** del equipo
(`GET /api/planta`), no lo que diga el operador — si no coinciden, se marca
`requiere_confirmacion` con el motivo exacto. También detecta un caso específico: un diferencial
de filtro dicho como "dieciocho" en vez de "uno ocho" (1.8) — valor sospechosamente alto para ese
tipo de lectura.

### 4.3 Rango histórico — `src/sistema/procesamiento/rango_historico.py::VerificadorRango`

Compara el valor (convertido a la unidad canónica del historiador, `a_kgcm2` para presiones)
contra el rango de `GET /api/historian/{tag}/{variable}` de los últimos 45 días, con un margen del
15% sobre la amplitud. Fuera de ese rango, pide confirmación. Las lecturas puramente locales (sin
sensor conectado equivalente: temperatura por IR, goteo, estado de correas) no se verifican — no
hay con qué comparar.

## 5. Parte en borrador

`src/sistema/procesamiento/recorridas.py::armar_parte` arma el `Parte` (`EstadoParte.borrador`,
`id=f"PARTE-{nota.id}"`) con las filas ya resueltas y lo persiste
(`{media_dir}/{nota.id}/parte.json`, vía `guardar_parte`) — esto es lo que expone
`GET /api/partes` en `central` para la evaluación 4.3.

## 6. Diálogo de confirmación

`src/sistema/procesamiento/dialogo.py` — el núcleo del flujo interactivo.

`EstadoDialogo` guarda el `Parte` en construcción, qué fila está en pregunta (`fila_en_pregunta`)
y el `ConsumoTokens` acumulado de la nota. Vive en memoria, indexado por el id del último
`MensajeTablet` enviado (`agente.py::dialogos_pendientes`) — se persiste en Postgres recién en la
Fase 5.7 (memoria de conversación); hasta entonces, un reinicio del agente pierde los diálogos en
curso.

`construir_mensaje()` arma la siguiente pregunta según cuál es la primera fila "pendiente"
(`tag is None` o `requiere_confirmacion`), con tres formas posibles:
1. **Elegir entre candidatos** (equipo ambiguo): el mensaje trae `opciones` y **no** el borrador
   completo en `pantalla.filas` — mezclar las dos cosas confunde la pregunta en sí (bug #14): el
   operador virtual de la planta decide si la pregunta es "elegir una opción" o "revisar el
   borrador completo" mirando si `pantalla.filas` viene vacío, y cualquier cliente que siga la
   misma convención se comporta igual.
2. **Equipo no encontrado**: pide que lo repita.
3. **Confirmar un valor puntual** (fuera de rango, unidad no coincide) o **confirmar el parte
   completo** (no queda ninguna fila pendiente): acá sí se manda el borrador completo, para que lo
   revisen.

`procesar_respuesta()` interpreta la respuesta del operador según cuál de las tres preguntas
estaba pendiente:
- Si es una elección de candidato: se resuelve contra el catálogo (4.1) y se fija `tag`.
- Si el equipo no se encontró, o si hay que confirmar un valor puntual y la respuesta no es un
  "sí" literal: se reinterpreta la respuesta con el mismo pipeline del paso 3-4
  (`interpretar_texto`), consumiendo presupuesto.
- Si no queda ninguna fila pendiente (confirmación final) y la respuesta no es "sí": se
  reinterpreta como una corrección libre, que puede tocar más de una fila.

**Presupuesto agotado**: en cualquiera de las tres ramas, si `verificar_presupuesto()` ya no da
margen, no se vuelve a llamar al LLM — se acepta la fila (o el parte entero) tal como está, se
anota el motivo en la `observacion` ("revisar a mano") y se deja de preguntar. Esto cubre las
**tres** ramas por igual: la primera versión (bug #13) solo lo hacía en una, y las otras dos
quedaban preguntando lo mismo para siempre una vez agotado el presupuesto (bug #17) — confirmado
en vivo contra el escenario `martes-tranquilo` las dos veces.

Una corrección sobre una fila puntual **reemplaza** esa fila directamente (ya se sabe cuál es, por
el índice de la pregunta) en vez de fusionarla por texto contra todo el parte — fusionar por
`tag`/`tag_dicho` dejaba filas duplicadas cuando la nueva extracción frasea el mismo equipo
distinto a como lo dijo la nota original (bugs #16 y #18; #18 queda con una limitación conocida
documentada en `BUGS.md`, no resuelta del todo).

Cuando el parte queda `Confirmado`, `agente.py::_avanzar_dialogo` llama a `evaluar_parte` (paso 7)
y publica el evento.

## 7. Evento de sistema

`src/sistema/procesamiento/eventos.py::evaluar_parte`

Sin LLM, en código: compara cada fila del parte confirmado contra los límites de alarma de
`GET /api/planta` (no el manual Rev. C vencido), convertidos a unidad canónica. Un único evento
por parte, id estable (`EVT-PARTE-{parte.id}`): `SinNovedad` si ninguna lectura excede los
límites, `Escalado` si alguna sí.

El mismo módulo también expone `DetectorSensores`, que corre de forma independiente sobre
`planta/sensores/#` (streaming, no ligado a un parte) para detectar anomalías **sostenidas** en el
tiempo (simulado, no de reloj real — así funciona igual sin importar la velocidad del simulador),
usando `EstadoBombas` (`GET /api/estado`) para no escalar falsamente una bomba parada o en
reserva (bug #8).

## Limitaciones conocidas (honestas, no para la próxima fase)

- Las fotos de la nota se descargan pero no se usan en la extracción (no hay visión en el LLM de
  extracción) — quedan asociadas a todas las filas de la nota, no fila por fila.
- El diálogo vive en memoria: un reinicio del agente en medio de una conversación pendiente la
  pierde (se resuelve en 5.7).
- La exactitud de `valor` medida en 4.3 (31% sobre la muestra limpia de `martes-tranquilo`) es, en
  parte, extracción real del LLM local (modelo chico, sobre una transcripción con su propio
  ruido) — no todo lo que falla en el campo `valor` es un bug de este pipeline.
