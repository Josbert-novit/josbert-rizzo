"""Clientes de Ollama con los mismos topes para todos (Lote 6, A3/A4/B5).

Antes cada módulo armaba su `ChatOllama` a mano y no todos tenían timeout: el del cierre no tenía
ninguno (un Ollama trabado dejaba el pool de órdenes bloqueado para siempre) y los embeddings
tampoco. Acá:

- todo pedido tiene timeout HTTP;
- `keep_alive` deja el modelo cargado (en `vision-nemotron` se vio una recarga de 6.6 s en medio
  de una nota);
- el trabajo de **baja prioridad** (la sugerencia del análisis con LLM, que nunca se publica) se
  registra como cancelable: cuando llega una nota, `cancelar_baja_prioridad()` cierra su conexión
  HTTP, Ollama corta la generación al perder el cliente, y la nota no espera detrás de una llamada
  de 90 s (en `vision-nemotron`, contratista, eso trabó 7 notas).
"""

import logging
import threading

from sistema.config import config

log = logging.getLogger(__name__)

_lock = threading.Lock()
_clientes_baja_prioridad: list = []
_generacion_cancelacion = 0


def _keep_alive() -> int | str:
    try:
        return int(config.ollama_keep_alive)
    except ValueError:
        return config.ollama_keep_alive


def chat_ollama(*, timeout_s: float, baja_prioridad: bool = False, **extra):
    from langchain_ollama import ChatOllama

    opciones = {
        "model": config.llm_modelo_extraccion,
        "base_url": config.ollama_url,
        "temperature": 0,
        "keep_alive": _keep_alive(),
        "client_kwargs": {"timeout": timeout_s},
        # Mismo contexto en todas las llamadas: si cambia, Ollama recarga el modelo (Lote 8).
        "num_ctx": config.ollama_num_ctx,
    }
    if config.ollama_hilos:
        opciones["num_thread"] = config.ollama_hilos
    opciones.update(extra)
    chat = ChatOllama(**opciones)
    if baja_prioridad:
        with _lock:
            _clientes_baja_prioridad.append(chat._client._client)
    return chat


def embeddings_ollama():
    from langchain_ollama import OllamaEmbeddings

    return OllamaEmbeddings(
        model=config.rag_modelo_embeddings,
        base_url=config.ollama_url,
        keep_alive=_keep_alive(),
        client_kwargs={"timeout": config.timeout_embeddings_s},
    )


def generacion_cancelacion() -> int:
    """Cambia cada vez que se cancela algo: si cambió durante un trabajo, ese trabajo fue cortado."""
    with _lock:
        return _generacion_cancelacion


def cancelar_baja_prioridad() -> int:
    """Corta los pedidos de baja prioridad en curso. Devuelve cuántos clientes cerró."""
    global _generacion_cancelacion
    with _lock:
        clientes = list(_clientes_baja_prioridad)
        _clientes_baja_prioridad.clear()
        if clientes:
            _generacion_cancelacion += 1
    for cliente in clientes:
        try:
            cliente.close()
        except Exception:
            log.exception("no se pudo cerrar un cliente de Ollama de baja prioridad")
    return len(clientes)


def soltar_baja_prioridad() -> None:
    """Al terminar una sugerencia: cierra sus clientes (cada sugerencia crea los suyos) sin
    contarlo como cancelación."""
    with _lock:
        clientes = list(_clientes_baja_prioridad)
        _clientes_baja_prioridad.clear()
    for cliente in clientes:
        try:
            cliente.close()
        except Exception:
            pass
