import os
from pathlib import Path


class Config:
    mqtt_host = os.getenv("MQTT_HOST", "broker")
    mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
    historian_url = os.getenv("HISTORIAN_URL", "http://sim:8100")
    media_dir = Path(os.getenv("MEDIA_DIR", "/data/notas"))

    transcripcion_modelo = os.getenv("TRANSCRIPCION_MODELO_LOCAL") or "small"

    ollama_url = os.getenv("OLLAMA_URL", "http://ollama:11434")
    llm_modelo_extraccion = os.getenv("LLM_MODELO_EXTRACCION") or "qwen2.5:1.5b"

    # RAG (Fase 5): embeddings locales, mismo motivo que el LLM de extracción (sin depender de
    # internet/nube). Lote 7: bge-m3 (el de las demos de la cátedra, multilingüe) en lugar de
    # nomic-embed-text: con el evaluador oficial la híbrida pasó de hit@5 0.746 a 0.901 (BUGS #68).
    # Se puede volver a nomic con la variable + reindexar; la dimensión sale de `rag/esquema.py`.
    rag_modelo_embeddings = os.getenv("RAG_MODELO_EMBEDDINGS") or "bge-m3"
    # Context builder (5.6, `rag/contexto.py`): tope de caracteres de las citas que entran al
    # prompt del análisis. Con `num_ctx=4096` y el resto de la evidencia, 4000 caracteres (~1000
    # tokens) deja holgura; un fragmento que no entra se descarta entero.
    rag_max_caracteres_contexto = int(os.getenv("RAG_MAX_CARACTERES_CONTEXTO", "4000"))

    # Visión (Entrega 2, Fase 13): análisis de fotos de cierres vía NVIDIA NIM. A diferencia de
    # transcripción/extracción/embeddings, acá no hay un modelo local liviano equivalente para
    # visión multimodal — se usa la nube (requiere NVIDIA_API_KEY) y se encola cuando no hay
    # enlace (ver `llm/vision.py` y `persistencia/ordenes.py`).
    nvidia_api_key = os.getenv("NVIDIA_API_KEY") or ""
    nvidia_modelo_vision = os.getenv("NVIDIA_MODELO_VISION") or "nvidia/nemotron-3.5-super-vl-120b-a12b"

    # Corte (4.1): si procesar una nota (descarga + transcripción + extracción + resolución)
    # tarda más que esto, se corta y se avisa igual en la tablet — más vale una respuesta
    # genérica a tiempo que quedarse mudo (sistema_mudo es eliminatorio).
    timeout_nota_s = float(os.getenv("TIMEOUT_NOTA_S", "90"))

    # El análisis de una orden (Entrega 2) corre en su propio pool (`_pool_ordenes`), nunca
    # compite por el worker de las notas — pero sí compite por CPU/Ollama con la indexación RAG
    # de fondo (701 fragmentos) en entornos sin GPU, lo que lo hace bastante más lento que una
    # nota (confirmado en vivo: cortes a los 90 s mientras la indexación corría en paralelo).
    # Como no bloquea nada más, puede esperar más tiempo antes de cortar.
    timeout_orden_s = float(os.getenv("TIMEOUT_ORDEN_S", "280"))

    # G1 / Anexo C: con evidencia clara (empaquetadura, aceite, correas dichas por el operador, o
    # diferencial de filtro en alarma sostenida) la orden propone la tarea del catálogo en vez de
    # una inspección, y `evaluar_reglas` decide si se aprueba. Medido offline antes de activarlo
    # (`evaluacion/evaluar_tarea_estandar.py`): 0 aprobaciones que el corrector considere indebidas.
    ordenes_tarea_estandar = os.getenv("ORDENES_TAREA_ESTANDAR", "1").lower() not in ("0", "false", "no")

    # Lote 6 (A1): Whisper con hilos fijos. Con `cpu_threads=0` CTranslate2 toma todos los núcleos
    # del host aunque el contenedor tenga 2.5 CPU, y el cgroup lo frena (throttling). Valores
    # elegidos con `evaluacion/evaluar_whisper.py` (ver PLAN.md, Lote 6).
    whisper_hilos = int(os.getenv("WHISPER_HILOS", "2"))
    whisper_beam = int(os.getenv("WHISPER_BEAM", "5"))
    whisper_vad = os.getenv("WHISPER_VAD", "0").lower() in ("1", "true", "si", "sí")

    # Lote 6 (A4/B5): Ollama. `keep_alive=-1` deja los modelos cargados (en una corrida se vio una
    # recarga de 6.6 s en medio de una nota) y todo pedido tiene timeout: uno colgado no puede
    # trabar para siempre el pool que lo llamó.
    ollama_keep_alive = os.getenv("OLLAMA_KEEP_ALIVE_CLIENTE", "-1")
    ollama_hilos = int(os.getenv("OLLAMA_HILOS", "0")) or None
    # Lote 8 (velocidad): un solo contexto para todas las llamadas a qwen. Ollama 0.5.4 recarga el
    # modelo cada vez que cambia `num_ctx`: el precalentado, la extracción y el cierre iban con el
    # default (2048) y el análisis con 4096, y en `lote6b` tormenta-perfecta qwen se cargó 4 veces
    # (1.3-2.5 s de CPU cada una, encima de las notas). 4096 es lo que necesita el análisis.
    ollama_num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "4096"))
    # Lote 8: 25 → 10 s. En lote6-velocidad, lote6b, lote7-rag y lote8-velocidad fueron 14 notas al
    # LLM de respaldo: las 3 que terminaron tardaron 3-6 s y las otras 11 se cortaron a los 25 s y
    # usaron igual el borrador del parser. Esos 25 s eran espera pura antes de contestar la tablet.
    timeout_extraccion_s = float(os.getenv("TIMEOUT_EXTRACCION_S", "10"))
    timeout_cierre_llm_s = float(os.getenv("TIMEOUT_CIERRE_LLM_S", "45"))
    timeout_embeddings_s = float(os.getenv("TIMEOUT_EMBEDDINGS_S", "30"))

    # Lote 6 (A6): si a los `acuse_nota_s` de llegar una nota todavía no salió nada a la tablet
    # (cola ocupada, CPU lenta), se manda un acuse ("recibí tu nota") sin esperar respuesta: el
    # operador nunca queda sin contestación (E1 cuenta cualquier mensaje durante un corte de enlace).
    acuse_nota_s = float(os.getenv("ACUSE_NOTA_S", "8"))
    # Lote 6 (B7): el agente se conecta y late enseguida; las notas que lleguen mientras termina de
    # prepararse (parser, Whisper) esperan hasta esto antes de procesarse igual.
    espera_preparacion_s = float(os.getenv("ESPERA_PREPARACION_S", "60"))
    # Lote 8: el primer latido espera el arranque completo (Whisper, Ollama precalentado, corpus
    # indexado) hasta este tope. El corrector espera hasta 180 s el primer latido para arrancar el
    # reloj, y F2 tolera 60 s entre latidos (sin contar los cortes de luz).
    espera_primer_latido_s = float(os.getenv("ESPERA_PRIMER_LATIDO_S", "45"))
    espera_cierre_s = float(os.getenv("ESPERA_CIERRE_S", "120"))
    espera_maxima_sugerencia_s = float(os.getenv("ESPERA_MAXIMA_SUGERENCIA_S", "600"))
    # Lote 8 (velocidad): la sugerencia del LLM (nunca se publica) arranca recién después de
    # `quietud_sugerencia_s` sin notas ni respuestas en curso. Antes arrancaba apenas la nota mandaba
    # su pregunta a la tablet, en el hueco antes de que contestara el operador, y la cortaba la
    # respuesta: en `lote6b`, las notas con una sugerencia en curso tuvieron Whisper de 4.3 s de
    # mediana contra 2.9 s. `SUGERENCIA_LLM=0` la apaga del todo.
    sugerencia_llm = os.getenv("SUGERENCIA_LLM", "1").lower() not in ("0", "false", "no")
    quietud_sugerencia_s = float(os.getenv("QUIETUD_SUGERENCIA_S", "15"))
    intervalo_latido_s = float(os.getenv("INTERVALO_LATIDO_S", "30"))
    intervalo_sondeo_estado_s = float(os.getenv("INTERVALO_SONDEO_ESTADO_S", "2"))
    intervalo_reconciliacion_s = float(os.getenv("INTERVALO_RECONCILIACION_S", "5"))

    # Lote 6 (B5): Postgres nunca puede colgar un hilo: tope para conectar y para cada consulta.
    postgres_connect_timeout_s = int(os.getenv("POSTGRES_CONNECT_TIMEOUT_S", "5"))
    postgres_statement_timeout_ms = int(os.getenv("POSTGRES_STATEMENT_TIMEOUT_MS", "30000"))

    postgres_host = os.getenv("POSTGRES_HOST", "postgres")
    postgres_port = int(os.getenv("POSTGRES_PORT", "5432"))
    postgres_db = os.getenv("POSTGRES_DB", "sistema")
    postgres_user = os.getenv("POSTGRES_USER", "sistema")
    postgres_password = os.getenv("POSTGRES_PASSWORD", "sistema")

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


config = Config()
