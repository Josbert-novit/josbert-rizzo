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
- [ ] 0.2 Elegir stack: Python 3.11+, LangChain, Pydantic (`extra="forbid"` en tools),
      PostgreSQL 16 + pgvector.
- [ ] 0.3 Esqueleto de carpetas (ver `docs/estructura-proyecto-python.md` de la planta):
      `dominio/`, `procesamiento/`, `llm/`, `rag/`, `persistencia/`, `integraciones/`, `interfaces/`.
- [ ] 0.4 `docker-compose.yml` inicial: servicio(s) propios en `planta-lan` (external) +
      `postgres:16` con `pgvector`. Probar que `docker compose up` levanta sin tocar la planta.
- [ ] 0.5 Conectar al broker MQTT (`broker:1883` desde `planta-lan`) y confirmar que se reciben
      mensajes de `planta/#`.
- [ ] 0.6 Latido: publicar `sistema/estado` cada 30 s.

### Fase 1 — Parte por voz (requerimiento A, completo)
- [ ] 1.1 Suscribirse a `planta/recorridas`, bajar audio (`http://sim:8100{audio_url}`) y fotos.
- [ ] 1.2 Transcripción con modelo **local** de la lista cerrada (obligatorio desde ya: en
      `fuga-sin-enlace` el enlace cae a los 10 min).
- [ ] 1.3 Extracción a `FilaParte` con structured output (Pydantic): una fila por equipo+variable,
      `hora` = `ts` de la nota (no el reloj de la máquina), fotos asociadas a la fila que corresponde.
- [ ] 1.4 Resolver nombres informales contra `GET /api/planta` ("la dos", "el de la izquierda",
      "la de reserva"); si es ambiguo ("la bomba" a secas) preguntar por tablet.
- [ ] 1.5 Unidades: "kilos" = kg/cm²; B-101 en kg/cm², B-102 en psi, B-103 en bar; un diferencial
      dicho "dieciocho" en vez de "uno ocho" se pregunta.
- [ ] 1.6 Rango histórico vía `GET /api/historian/{tag}/{variable}` (45 días); fuera de rango pide
      confirmación antes de guardar.
- [ ] 1.7 Tablet: mandar borrador en `MensajeTablet.pantalla.filas` (con opciones si hay que elegir),
      procesar `planta/respuestas` en lenguaje de campo ("No, la uno estaba en ciento quince kilos"),
      reenviar borrador corregido.
- [ ] 1.8 Parte pasa `Borrador` → `Confirmado` solo tras confirmación del operador.
- [ ] 1.9 Manejar notas `VIVO-…` (sin parte esperado, pueden no traer nada que reportar).

### Fase 2 — Detección en planta (requerimiento B, en código, SIN LLM)
- [ ] 2.1 Por cada parte confirmado y cada desvío de `planta/sensores/#`, generar `EventoSistema`
      idempotente (`id` estable, `origen` parte/sensor, `nota_ids`).
- [ ] 2.2 Límites desde `datos/planta.yaml` o `GET /api/planta` (NO del manual Rev. C, que está
      vencido). Misma entrada → misma decisión, siempre.
- [ ] 2.3 Clasificar `Sin novedad` / `Escalado`. Escalar solo anomalía **sostenida**, no un pico
      aislado. "Parada, en reserva" no es anomalía.
- [ ] 2.4 El escalado lleva contexto: parte, fotos, tendencia de sensores de las últimas horas.
- [ ] 2.5 `Alerta de seguridad` (S1–S4: agua a presión, olor a gas, humo/fuego, persona lesionada):
      detector **local, sin nube**; avisar en tablet el procedimiento de emergencia + aviso por
      radio al supervisor (`espera_respuesta: false`); publicar `AlertaSeguridad` en <30 s reales;
      **cero falsas alarmas** (criterio eliminatorio).

### Fase 3 — Tools y MCP
- [ ] 3.1 Tools de solo lectura, Pydantic estricto (`extra="forbid"`):
      equipos · historial de sensores · historial de órdenes · inventario/stock/materiales ·
      catálogo de tareas · búsqueda en manuales.
- [ ] 3.2 Exponer al menos una tool por servidor **MCP**.
- [ ] 3.3 Manejo de errores, timeouts y reintentos en cada tool.
- [ ] 3.4 Tests automatizados de cada tool en `tests/`.

### Fase 4 — Control y evaluación
- [ ] 4.1 Condiciones de corte, presupuesto de pasos/tokens, timeout global por nota.
- [ ] 4.2 Mini spec del flujo del parte en el repo (Spec-Driven Development) — documentar el
      recorrido: nota de voz → transcripción → extracción → resolución → confirmación → evento.
- [ ] 4.3 Evaluación contra `GET /api/verdad`: aciertos por campo, y si preguntó solo cuando había
      ambigüedad real (ni de más ni de menos).

### Fase 5 — RAG, contexto y memoria
- [ ] 5.1 Indexar `datos/documentos` en pgvector con metadatos: equipo, fabricante, documento,
      sección, cita, vigente.
- [ ] 5.2 Manejar las trampas del corpus:
      - Rev. C de la bomba vencida (tiene otros límites) — no debe ganarle a la Rev. D.
      - Secciones de TX-250, TX-500, FC-24, FC-96, PIA-1, PIA-2 que **no aplican** a esta planta.
      - Traducir códigos de falla del fabricante a F01–F10 vía MBP-4 §6.
- [ ] 5.3 Medir `recall@k` con `evaluacion/retrieval.json` (71 preguntas) usando
      `corpus.evaluar.evaluar(buscar, suite)`. Comparar contra baseline BM25 (hit@5 0,85 /
      recall@5 0,66). Reportar cuántas veces aparece la Rev. C antes que la D (debería ser ~0).
- [ ] 5.4 Cada conclusión cita la sección del manual (`TX350-D §3.2`).
- [ ] 5.5 Justificar en el README/INFORME qué se consulta por RAG y qué por API/SQL: **límites y
      stock no salen del RAG**.
- [ ] 5.6 Diseñar el context builder (el análisis completo es Entrega 2, pero el diseño va ahora).
- [ ] 5.7 Memoria: persistir en Postgres el estado de conversación de cada nota (mensaje de tablet,
      nota, borrador, intento) + escribir la política de qué se recuerda y por cuánto tiempo.

### Fase 6 — Documentación y verificación final
- [ ] 6.1 `README.md`: requisitos, cómo levantar (`docker compose up`), cómo correr tests,
      direcciones de pantallas, ejemplo de recorrido completo de una nota.
- [ ] 6.2 `INFORME.md`: modelos por tarea, consumo de tokens, costo aproximado, conclusión.
- [ ] 6.3 Correr `corregir.py --escenarios martes-tranquilo fuga-sin-enlace` y revisar criterios
      A1 a A4b, B1, B2, F1, F2.
- [ ] 6.4 Verificar explícitamente las dos condiciones **eliminatorias**:
      - `sin_alerta_de_seguridad` → nunca fallar una S1–S4.
      - `sistema_mudo` → el sistema siempre tiene que responder/registrar algo, nunca quedarse callado.
- [ ] 6.5 Commit, push, cargar el link del repo en el formulario de entregas.

## Convenciones de este plan

- Cada fase se cierra con un commit propio.
- Avisame cuando quieras empezar una fase y seguimos paso a paso desde ahí.
- Este archivo se actualiza a medida que avanzamos (marco `[x]` lo hecho).
