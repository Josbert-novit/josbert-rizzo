# Informe — modelos, consumo y costo (Entrega 1)

## Hardware de prueba

Todo corre **local, sin GPU**: Intel Core i5-11300H (4 núcleos / 8 hilos), 32 GB RAM, Windows +
Docker Desktop. Confirmado en los logs de Ollama ("no compatible GPUs were discovered") — toda la
inferencia es CPU.

## Modelos por tarea

| Tarea | Modelo | Proveedor | Local/nube | Parámetros | Por qué |
| --- | --- | --- | --- | --- | --- |
| Transcripción de audio (1.2) | `faster-whisper` modelo **small** (CTranslate2, int8) | Systran (reempaquetado de Whisper de OpenAI) | **Local** | ~244 M | Tiene que seguir andando con el enlace caído (`fuga-sin-enlace` corta el acceso a internet a los 10 min) — nada que dependa de una API externa puede estar en el camino de la transcripción ni de la detección de alerta de seguridad. |
| Extracción de filas del parte (1.3) | **qwen2.5:1.5b** | Alibaba / Ollama | **Local** | 1.5 B | Mismo motivo: la extracción alimenta la detección de seguridad indirectamente (el diálogo de confirmación) y no puede depender del enlace. Un modelo de 1.5 B es lo más grande que corre con latencia aceptable (~5-10 s por llamada) en esta CPU sin GPU — se probó porque es el piso razonable para `structured output` vía Ollama. |
| Embeddings para RAG (5.1) | **nomic-embed-text** | Nomic AI / Ollama | **Local** | ~137 M | Mismo criterio: sin dependencia de nube. Necesita los prefijos de tarea (`search_document:`/`search_query:`) para rendir bien — encontrado y arreglado en vivo (bug #19, `BUGS.md`): sin ellos, `hit@5` caía de 0.648 a 0.535 contra la suite de 71 preguntas. |

**No se usó ningún modelo de nube en esta entrega.** El enunciado permite nube para tareas no
críticas en tiempo real (p. ej. RAG), pero el corpus y el volumen de esta Entrega 1 no lo
justificaban — correr todo local simplifica la arquitectura (sin credenciales, sin
`HTTP_PROXY`/`HTTPS_PROXY` adicional por tarea) y evitó un modo de falla entero (indisponibilidad
de la nube) en el camino crítico de seguridad.

## Decisión de arquitectura: modelos horneados en la imagen, no bajados en el arranque

Los tres modelos se descargan **en el build de la imagen Docker** (`ollama/Dockerfile`,
`src/Dockerfile`), no la primera vez que arranca el contenedor. Encontrado corriendo la
corrección automática real (`academia2026-tp/correccion/corregir.py`) contra este repo: el
corrector borra los volúmenes entre escenario y escenario (`docker compose down -v`), así que
bajar `qwen2.5:1.5b` (986 MB) recién al arrancar tardó más de 1 minuto real — casi todo el tiempo
real que dura un escenario corto — y dejó al sistema sin publicar casi nada. Horneando los
modelos en el build, el contenedor arranca con todo listo, sin depender de la red en el camino
crítico de la corrección (ver bug #19 en `BUGS.md` para el detalle completo).

## Consumo de tokens (real, medido)

`src/sistema/llm/control.py::ConsumoTokens` cuenta tokens **reales** de la respuesta de Ollama
(`prompt_eval_count`/`eval_count` de `generation_info`, no una estimación) por cada llamada al LLM
de extracción, acumulados por nota a lo largo de todo el diálogo de confirmación (extracción
inicial + hasta 5 correcciones, `MAX_PASOS_LLM_POR_NOTA = 6`, ver 4.1).

Medido en una corrida real contra el corrector automático (escenarios `martes-tranquilo` y
`fuga-sin-enlace`, 12 partes confirmados):

| | Valor |
| --- | --- |
| Tokens totales por parte confirmado (prompt + respuesta) | 1019 – 2809, promedio ≈ **2 280** |
| Pasos de LLM por parte (extracción inicial + correcciones) | 3 – 6, la mayoría llega al tope de 6 |
| Tiempo real por llamada al LLM (CPU, sin GPU) | ~5 – 10 s |

La mayoría de los partes agota el presupuesto de 6 pasos — señal de que el diálogo de
confirmación necesita varias vueltas en la práctica (equipo ambiguo, unidad que no coincide,
valor fuera de rango), no que el límite esté mal calibrado para el caso fácil (una nota con un
solo dato claro confirma en 1-3 pasos).

## Costo aproximado

**USD 0** en inferencia: los tres modelos corren localmente vía Ollama/CTranslate2, sin llamadas a
ninguna API de pago. El costo real de esta solución es de cómputo propio (CPU del host) y
latencia (cada llamada al LLM de extracción tarda 5-10 s en esta CPU sin GPU), no de facturación
por token. Si se migrara a un modelo de nube equivalente (p. ej. un modelo chico de OpenAI o
Anthropic para la extracción), el costo aproximado por nota rondaría fracciones de centavo de
USD dado el volumen de tokens medido (~2 300 tokens/parte) — pero se decidió no hacerlo por la
dependencia de red que introduciría en el camino crítico de seguridad (ver sección anterior).

## Conclusión

Para esta entrega, el criterio de selección de modelos no fue "el más preciso disponible" sino
"el que pueda correr 100% local, con latencia aceptable en CPU, sin GPU y sin depender del
enlace a internet" — porque dos de los criterios eliminatorios (`sin_alerta_de_seguridad`,
`sistema_mudo`) y un escenario completo (`fuga-sin-enlace`) exigen que el sistema siga
funcionando con la red externa caída. Esto llevó a tres decisiones concretas, todas verificadas
en vivo contra la planta real y contra el corrector automático:

1. Transcripción y extracción 100% locales (Whisper `small` + `qwen2.5:1.5b`), nunca en la nube.
2. Un presupuesto explícito de pasos/tokens por nota (4.1), para que una extracción mal
   interpretada no genere un ida-y-vuelta indefinido con el operador.
3. Modelos horneados en la imagen Docker en vez de descargados en el arranque — la diferencia
   entre que el sistema funcione o no dentro de la ventana de tiempo real de un escenario de
   corrección fue, literalmente, esta decisión (nota de corrección: 5.8/100 sin hornear los
   modelos → 62.0/100, aprobado, con los modelos horneados y el resto de los arreglos de esta
   fase — ver `BUGS.md` #19 y la Fase 6 de `PLAN.md`).

Modelos más grandes (p. ej. `qwen2.5:7b`) probablemente extraerían con más precisión (la
exactitud de `valor` medida en 4.3 fue del 31%, el número más bajo de los tres campos — ver
`PLAN.md`), pero no se probaron en esta entrega: el tiempo de inferencia en CPU sin GPU ya es el
cuello de botella identificado (B1/A2/A3 bajos en la corrección por diálogos que no llegan a
confirmarse dentro de la ventana de tiempo real), y un modelo más grande empeoraría exactamente
eso. Queda como mejora concreta para la Entrega 2: evaluar `qwen2.5:3b` con GPU, o reducir la
cantidad de llamadas al LLM por nota en vez de agrandar el modelo.
