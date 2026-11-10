from typing import Protocol

from sistema.config import config


class ModeloEmbeddings(Protocol):
    def embed_documents(self, textos: list[str]) -> list[list[float]]: ...
    def embed_query(self, texto: str) -> list[float]: ...


class Embeddings:
    """Embeddings locales (Ollama, `nomic-embed-text`) — mismo motivo que el LLM de extracción:
    no depender de internet/nube en el camino crítico."""

    def __init__(self, modelo: ModeloEmbeddings | None = None) -> None:
        self._modelo = modelo

    def _cargar(self) -> ModeloEmbeddings:
        if self._modelo is None:
            from langchain_ollama import OllamaEmbeddings

            self._modelo = OllamaEmbeddings(model=config.rag_modelo_embeddings, base_url=config.ollama_url)
        return self._modelo

    def embed_documentos(self, textos: list[str]) -> list[list[float]]:
        # nomic-embed-text necesita el prefijo de tarea para embeber bien — sin él, la
        # similaridad entre pregunta y sección cae mucho (medido en 5.3: hit@5 bajó de ~0.53
        # a bastante más bajo que el baseline BM25 del 0.85 sin este prefijo).
        return self._cargar().embed_documents([f"search_document: {t}" for t in textos])

    def embed_consulta(self, texto: str) -> list[float]:
        return self._cargar().embed_query(f"search_query: {texto}")
