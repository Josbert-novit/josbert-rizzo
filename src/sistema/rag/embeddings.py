from typing import Protocol



class ModeloEmbeddings(Protocol):
    def embed_documents(self, textos: list[str]) -> list[list[float]]: ...
    def embed_query(self, texto: str) -> list[float]: ...


def usa_prefijos(nombre_modelo: str) -> bool:
    """nomic-embed-text necesita prefijos de tarea (bug #19); bge-m3 no los usa."""
    return nombre_modelo.startswith("nomic")


class Embeddings:
    """Embeddings locales (Ollama, `config.rag_modelo_embeddings`, bge-m3 por defecto) — mismo
    motivo que el LLM de extracción: no depender de internet/nube en el camino crítico."""

    def __init__(self, modelo: ModeloEmbeddings | None = None, nombre_modelo: str | None = None) -> None:
        from sistema.config import config

        self._modelo = modelo
        self._prefijos = usa_prefijos(nombre_modelo or config.rag_modelo_embeddings)

    def _cargar(self) -> ModeloEmbeddings:
        if self._modelo is None:
            # Lote 6 (B5): con timeout (antes un pedido colgado trababa la indexación y las sugerencias).
            from sistema.llm.ollama import embeddings_ollama

            self._modelo = embeddings_ollama()
        return self._modelo

    def embed_documentos(self, textos: list[str]) -> list[list[float]]:
        # nomic-embed-text necesita el prefijo de tarea para embeber bien — sin él, la
        # similaridad entre pregunta y sección cae mucho (medido en 5.3: hit@5 bajó de ~0.53
        # a bastante más bajo que el baseline BM25 del 0.85 sin este prefijo).
        if self._prefijos:
            textos = [f"search_document: {t}" for t in textos]
        return self._cargar().embed_documents(textos)

    def embed_consulta(self, texto: str) -> list[float]:
        return self._cargar().embed_query(f"search_query: {texto}" if self._prefijos else texto)
