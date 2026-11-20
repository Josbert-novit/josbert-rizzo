"""Indexa el corpus documental de la planta en pgvector (5.1).

Todo se trae por la API HTTP de la planta (`GET /api/documentos`, `/documentos/fragmentos.jsonl`)
— nada de acceso directo al filesystem de `academia2026-tp`: el corrector solo va a tener la
planta corriendo, no necesariamente ese repo al lado del nuestro. Se embebe con un modelo local
(Ollama) y se guarda con los metadatos que hacen falta para manejar las trampas del corpus (5.2):
`vigente` (Rev. C vs D), `aplica` (secciones de otros modelos o de otras plantas) y `equipos`.

Correr dentro del contenedor del agente (tiene red hacia `sim`, `ollama` y `postgres`):
    docker compose exec agente python -m sistema.rag.indexar
"""

import logging
import re
import time
from typing import Callable

from sistema.integraciones.planta_http import PlantaHTTP
from sistema.rag.embeddings import Embeddings
from sistema.rag.esquema import conectar, crear_esquema

log = logging.getLogger(__name__)

# Chico a propósito (no 32): Ollama (sin GPU) sirve un pedido a la vez, así que un lote grande
# bloquea la cola entera mientras indexa en segundo plano — confirmado en vivo contra el
# corrector real: un lote de 32 tardaba 15-85 s (más contra carga) y dejaba un análisis de orden
# esperando su turno todo ese tiempo. Lotes chicos reparten mejor la cola sin cambiar el total
# indexado, a costa de más pedidos HTTP (aceptable: esto corre en segundo plano, sin apuro).
# 20/11 (corrida `vision-nemotron`): con 4 CPU un lote de 4 tardaba ~11 s y una nota que llegaba
# en el medio esperaba ese lote entero antes de transcribir. De a 1, la pausa llega en ~3 s.
TAMANO_LOTE = 1

# Trampa del corpus (5.2): los manuales traen secciones que no son de esta planta ni de este
# modelo (límites de la TX-250/TX-500, límites de PIA-1/PIA-2, equipos de otras plantas). Se
# detectan por el título de la sección, que es lo que las declara como tales, y se marcan
# `aplica=false` para que no compitan en la búsqueda (igual que la Rev. C con `vigente`).
_NO_APLICA = re.compile(r"otros modelos|otras plantas|\bPIA-[12]\b", re.IGNORECASE)


def _aplica_a_esta_planta(fragmento: dict) -> bool:
    return not _NO_APLICA.search(fragmento.get("titulo") or "")


def _equipos_de_fragmento(fragmento: dict, equipos_por_doc: dict[str, list[str]]) -> list[str]:
    if fragmento["doc"] == "historial":
        # El tag del equipo está en el título que arma el generador ("OT-24-0001 B-101 T-B-VAL").
        partes = (fragmento.get("titulo") or "").split()
        return [partes[1]] if len(partes) > 1 else []
    return equipos_por_doc.get(fragmento["doc"], [])


def filas_de_texto(fragmentos: list[dict], documentos: list[dict]) -> list[tuple]:
    """Pasada 1: las filas a insertar, sin embedding. Pura (sin red ni DB), para testearla."""
    equipos_por_doc = {d["id"]: d.get("equipos") or [] for d in documentos}
    tipo_por_doc = {d["id"]: d.get("tipo") for d in documentos}
    return [
        (
            f["id"],
            f["doc"],
            f.get("numero"),
            f.get("titulo"),
            f.get("clave"),
            f.get("pagina"),
            bool(f.get("vigente", True)),
            _aplica_a_esta_planta(f),
            tipo_por_doc.get(f["doc"]),
            _equipos_de_fragmento(f, equipos_por_doc),
            f["texto"],
        )
        for f in fragmentos
    ]


def indexar(
    http: PlantaHTTP, embeddings: Embeddings, *, debe_pausar: Callable[[], bool] | None = None
) -> int:
    """Dos pasadas (Entrega 2, hallazgo H1 de la Fase 15):

    1. **Texto** de todos los fragmentos, sin embedding. Tarda segundos y no usa Ollama: la
       columna `tsv` (full-text) se genera sola, así que la parte léxica de la búsqueda híbrida
       (`buscar.py`) anda desde el arranque. Antes, con una sola pasada que embebía todo, el
       corrector (que borra volúmenes entre escenarios) nunca pasaba de ~100/701 fragmentos y el
       RAG quedaba casi vacío durante todo el escenario.
    2. **Embeddings** solo de las filas con `embedding IS NULL`, en lotes chicos. Reanudable: si
       el proceso se reinicia, sigue donde quedó. Si el texto de un fragmento no cambió, el
       embedding ya calculado se conserva (ver el `CASE` del UPSERT).

    `debe_pausar`: mientras devuelva True, no se manda el próximo lote a Ollama — deja el
    servidor (y la CPU) libre para cualquier trabajo con apuro: una nota, una respuesta del
    operador, un análisis de orden o un cierre (ver `llm/control.py::trabajo_urgente`)."""
    documentos = http.documentos()["documentos"]
    fragmentos = http.fragmentos()
    log.info("corpus: %d fragmentos a indexar", len(fragmentos))

    conn = conectar()
    try:
        crear_esquema(conn)
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO rag_fragmentos (id, doc, numero, titulo, clave, pagina, vigente, aplica, tipo, equipos, texto)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    doc = EXCLUDED.doc, numero = EXCLUDED.numero, titulo = EXCLUDED.titulo,
                    clave = EXCLUDED.clave, pagina = EXCLUDED.pagina, vigente = EXCLUDED.vigente, aplica = EXCLUDED.aplica,
                    tipo = EXCLUDED.tipo, equipos = EXCLUDED.equipos, texto = EXCLUDED.texto,
                    embedding = CASE WHEN rag_fragmentos.texto = EXCLUDED.texto
                                     THEN rag_fragmentos.embedding ELSE NULL END
                """,
                filas_de_texto(fragmentos, documentos),
            )
        log.info("texto de %d fragmentos cargado (búsqueda por palabras lista)", len(fragmentos))

        pendientes = conn.execute(
            "SELECT id, texto FROM rag_fragmentos WHERE embedding IS NULL ORDER BY id"
        ).fetchall()
        log.info("embeddings pendientes: %d", len(pendientes))
        total = 0
        for inicio in range(0, len(pendientes), TAMANO_LOTE):
            while debe_pausar is not None and debe_pausar():
                time.sleep(0.5)
            lote = pendientes[inicio : inicio + TAMANO_LOTE]
            vectores = embeddings.embed_documentos([texto for _, texto in lote])
            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE rag_fragmentos SET embedding = %s WHERE id = %s",
                    [(vector, fid) for (fid, _), vector in zip(lote, vectores)],
                )
            total += len(lote)
            log.info("embeddings %d/%d", total, len(pendientes))
    finally:
        conn.close()
    return len(fragmentos)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    total = indexar(PlantaHTTP(), Embeddings())
    log.info("listo: %d fragmentos indexados", total)


if __name__ == "__main__":
    main()
