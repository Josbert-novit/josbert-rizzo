"""Indexa el corpus documental de la planta en pgvector (5.1).

Todo se trae por la API HTTP de la planta (`GET /api/documentos`, `/documentos/fragmentos.jsonl`)
— nada de acceso directo al filesystem de `academia2026-tp`: el corrector solo va a tener la
planta corriendo, no necesariamente ese repo al lado del nuestro. Se embebe con un modelo local
(Ollama) y se guarda con los metadatos que hacen falta para manejar las trampas del corpus (5.2):
`vigente` (Rev. C vs D), `aplica` (secciones de otros modelos o de otras plantas) y `equipos`.

Correr dentro del contenedor del agente (tiene red hacia `sim`, `ollama` y `postgres`):
    docker compose exec agente python -m sistema.rag.indexar
"""

import argparse
import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Callable

from sistema.config import config
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


# Lote 6 (A2): embeddings precalculados del corpus, versionados en el repo. Cada escenario del
# corrector arranca con la base vacía (`down -v`) y antes había que volver a embeber ~700
# fragmentos con el Ollama de CPU, compitiendo con las notas por varios minutos. La clave es un
# hash del modelo + el texto exacto que se embebe: si el corpus de la planta cambia (otra semilla)
# o se cambia de modelo, esos fragmentos no coinciden y se embeben como antes.
CACHE_EMBEDDINGS = Path(__file__).resolve().parents[1] / "datos" / "embeddings_corpus.npz"


def clave_cache(texto: str, modelo: str | None = None) -> str:
    modelo = modelo or config.rag_modelo_embeddings
    return hashlib.sha1(f"{modelo}|{texto}".encode("utf-8")).hexdigest()


def cargar_cache(ruta: Path = CACHE_EMBEDDINGS) -> dict[str, list[float]]:
    if not ruta.exists():
        return {}
    import numpy as np

    datos = np.load(ruta)
    return {str(k): v for k, v in zip(datos["claves"], datos["vectores"])}


def exportar_cache(http: PlantaHTTP, embeddings: Embeddings, ruta: Path = CACHE_EMBEDDINGS, lote: int = 16) -> int:
    """Embebe el corpus completo una vez (fuera de la corrida) y lo guarda en `ruta`."""
    import numpy as np

    fragmentos = http.fragmentos()
    textos = sorted({f["texto"] for f in fragmentos})
    claves, vectores = [], []
    for inicio in range(0, len(textos), lote):
        trozo = textos[inicio : inicio + lote]
        vectores.extend(embeddings.embed_documentos(trozo))
        claves.extend(clave_cache(t) for t in trozo)
        log.info("cache de embeddings: %d/%d", len(claves), len(textos))
    ruta.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(ruta, claves=np.array(claves), vectores=np.array(vectores, dtype=np.float32))
    return len(claves)


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
        try:
            cache = cargar_cache()
        except Exception:
            log.exception("no se pudo leer el cache de embeddings; se embebe todo")
            cache = {}
        if cache:
            desde_cache = [(cache[k], fid) for fid, texto in pendientes if (k := clave_cache(texto)) in cache]
            with conn.cursor() as cur:
                cur.executemany("UPDATE rag_fragmentos SET embedding = %s WHERE id = %s", desde_cache)
            ids_cache = {fid for _, fid in desde_cache}
            pendientes = [(fid, texto) for fid, texto in pendientes if fid not in ids_cache]
            log.info("embeddings desde el cache precalculado: %d", len(desde_cache))
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--exportar-cache", action="store_true",
                    help=f"embeber el corpus y guardarlo en {CACHE_EMBEDDINGS} (no toca Postgres)")
    args = ap.parse_args()
    if args.exportar_cache:
        log.info("listo: %d embeddings en %s", exportar_cache(PlantaHTTP(), Embeddings()), CACHE_EMBEDDINGS)
        return
    total = indexar(PlantaHTTP(), Embeddings())
    log.info("listo: %d fragmentos indexados", total)


if __name__ == "__main__":
    main()
