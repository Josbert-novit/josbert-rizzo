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

    # Corte (4.1): si procesar una nota (descarga + transcripción + extracción + resolución)
    # tarda más que esto, se corta y se avisa igual en la tablet — más vale una respuesta
    # genérica a tiempo que quedarse mudo (sistema_mudo es eliminatorio).
    timeout_nota_s = float(os.getenv("TIMEOUT_NOTA_S", "90"))

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
