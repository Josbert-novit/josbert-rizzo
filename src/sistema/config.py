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
    # internet/nube). nomic-embed-text es chico (~274 MB) y anda bien en CPU.
    rag_modelo_embeddings = os.getenv("RAG_MODELO_EMBEDDINGS") or "nomic-embed-text"

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
