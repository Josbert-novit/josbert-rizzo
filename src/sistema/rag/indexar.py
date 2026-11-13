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

from sistema.integraciones.planta_http import PlantaHTTP
from sistema.rag.embeddings import Embeddings
from sistema.rag.esquema import conectar, crear_esquema

log = logging.getLogger(__name__)

TAMANO_LOTE = 32

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


def indexar(http: PlantaHTTP, embeddings: Embeddings) -> int:
    documentos = http.documentos()["documentos"]
    equipos_por_doc = {d["id"]: d.get("equipos") or [] for d in documentos}
    tipo_por_doc = {d["id"]: d.get("tipo") for d in documentos}

    fragmentos = http.fragmentos()
    log.info("corpus: %d fragmentos a indexar", len(fragmentos))

    conn = conectar()
    crear_esquema(conn)

    total = 0
    for inicio in range(0, len(fragmentos), TAMANO_LOTE):
        lote = fragmentos[inicio : inicio + TAMANO_LOTE]
        vectores = embeddings.embed_documentos([f["texto"] for f in lote])
        filas = [
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
                vector,
            )
            for f, vector in zip(lote, vectores)
        ]
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO rag_fragmentos (id, doc, numero, titulo, clave, pagina, vigente, aplica, tipo, equipos, texto, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    doc = EXCLUDED.doc, numero = EXCLUDED.numero, titulo = EXCLUDED.titulo,
                    clave = EXCLUDED.clave, pagina = EXCLUDED.pagina, vigente = EXCLUDED.vigente, aplica = EXCLUDED.aplica,
                    tipo = EXCLUDED.tipo, equipos = EXCLUDED.equipos, texto = EXCLUDED.texto,
                    embedding = EXCLUDED.embedding
                """,
                filas,
            )
        total += len(filas)
        log.info("indexados %d/%d", total, len(fragmentos))

    conn.close()
    return total


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    total = indexar(PlantaHTTP(), Embeddings())
    log.info("listo: %d fragmentos indexados", total)


if __name__ == "__main__":
    main()
