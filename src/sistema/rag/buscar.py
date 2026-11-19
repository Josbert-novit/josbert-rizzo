"""Búsqueda híbrida sobre el corpus indexado: palabras clave (full-text de Postgres) + semántica
(coseno en pgvector), fusionadas con RRF — módulo 2.2, Entrega 2 (hallazgo H1 de la Fase 15).

Por qué híbrida y no solo pgvector:
- La parte léxica está lista apenas se carga el texto (segundos, sin Ollama, ver `indexar.py`);
  los embeddings se completan en segundo plano. Mientras faltan, la búsqueda igual devuelve
  resultados útiles en vez de una tabla casi vacía.
- Los embeddings fallan con números, códigos e IDs exactos ("F-201B", "§6.1", "OT-25-0026"),
  justo lo que más abunda en manuales de límites; ahí gana la coincidencia exacta de palabras.
  En 5.3 medimos solo pgvector en hit@5 0.648 contra 0.85 del BM25 de la cátedra.
- RRF combina **posiciones**, no puntajes (los de `ts_rank_cd` y coseno no son comparables):
  `RRF(d) = Σ 1/(k + posición)`, con k=60.

Trampa de versiones (5.2): por default solo se buscan fragmentos `vigente=true`, en las dos
mitades. La Rev. C del manual de la bomba se indexa igual (por si hace falta consultarla a
propósito, con `incluir_no_vigente=True`), pero nunca compite con la Rev. D por default. Lo mismo
con las secciones de otros modelos u otras plantas (`aplica=false`, ver `indexar.py`).
`GET /api/planta` sigue siendo la fuente de verdad para límites de alarma (eso no sale del RAG,
ver 5.5); esto es para preguntas de texto libre contra los manuales/contrato/historial.
"""

import logging
import re
from dataclasses import dataclass

from pgvector import Vector

from sistema.rag.embeddings import Embeddings
from sistema.rag.esquema import conectar

log = logging.getLogger(__name__)

K_RRF = 60
# Candidatos que aporta cada mitad antes de fusionar: más que `k` para que un fragmento que una
# mitad pone 15º y la otra 2º pueda subir al top-k combinado.
CANDIDATOS_POR_MITAD = 50

_PALABRA = re.compile(r"[^\W_]+", re.UNICODE)  # sin "_": to_tsquery lo trata como separador


@dataclass
class Fragmento:
    cita: str  # id, p. ej. "TX350-D §3.2" o "historial OT-25-0026"
    doc: str
    titulo: str | None
    texto: str
    vigente: bool
    similitud: float  # puntaje RRF combinado (más alto = más relevante)


def consulta_full_text(pregunta: str) -> str | None:
    """Arma una consulta OR para `to_tsquery('spanish', ...)` a partir de la pregunta libre.

    OR y no AND (`plainto_tsquery`): una pregunta en lenguaje natural casi nunca tiene todas sus
    palabras en el mismo fragmento; el ranking (`ts_rank_cd`) ya premia a los que tienen más.
    Solo letras/dígitos, así un carácter especial de la pregunta nunca rompe la sintaxis de
    `to_tsquery`. Las stopwords las descarta el propio diccionario `spanish`."""
    palabras = [p.lower() for p in _PALABRA.findall(pregunta) if len(p) >= 2 or p.isdigit()]
    vistas = list(dict.fromkeys(palabras))
    return " | ".join(vistas) if vistas else None


def fusionar_rrf(*rankings: list[str], k: int = K_RRF) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: cada ranking es una lista de ids en orden (el primero, el mejor).
    Devuelve (id, puntaje) ordenado de mayor a menor; empate → primero el que apareció antes."""
    puntajes: dict[str, float] = {}
    for ranking in rankings:
        for posicion, fid in enumerate(ranking, start=1):
            puntajes[fid] = puntajes.get(fid, 0.0) + 1.0 / (k + posicion)
    return sorted(puntajes.items(), key=lambda par: par[1], reverse=True)


def _filtros(equipo: str | None, incluir_no_vigente: bool, incluir_no_aplicables: bool) -> tuple[list[str], list]:
    condiciones: list[str] = []
    parametros: list = []
    if not incluir_no_vigente:
        condiciones.append("vigente = true")
    if not incluir_no_aplicables:
        condiciones.append("aplica = true")
    if equipo:
        condiciones.append("(cardinality(equipos) = 0 OR %s = ANY(equipos))")
        parametros.append(equipo)
    return condiciones, parametros


def _ranking_lexico(conn, pregunta: str, condiciones: list[str], parametros: list, limite: int) -> list[str]:
    consulta = consulta_full_text(pregunta)
    if consulta is None:
        return []
    where = " AND ".join(["tsv @@ q", *condiciones])
    filas = conn.execute(
        f"""
        SELECT id FROM rag_fragmentos, to_tsquery('spanish', %s) AS q
        WHERE {where}
        ORDER BY ts_rank_cd(tsv, q) DESC, id
        LIMIT %s
        """,
        [consulta, *parametros, limite],
    ).fetchall()
    return [fid for (fid,) in filas]


def _ranking_semantico(
    conn, embeddings: Embeddings, pregunta: str, condiciones: list[str], parametros: list, limite: int
) -> list[str]:
    """Best-effort: si Ollama no responde (caído, saturado), la búsqueda sigue solo con la parte
    léxica en vez de fallar — el análisis de una orden no se puede quedar sin manuales por eso."""
    try:
        vector = Vector(embeddings.embed_consulta(pregunta))
    except Exception:
        log.warning("búsqueda: sin embedding de la consulta, se usa solo full-text", exc_info=True)
        return []
    where = " AND ".join(["embedding IS NOT NULL", *condiciones])
    filas = conn.execute(
        f"""
        SELECT id FROM rag_fragmentos
        WHERE {where}
        ORDER BY embedding <=> %s
        LIMIT %s
        """,
        [*parametros, vector, limite],
    ).fetchall()
    return [fid for (fid,) in filas]


def buscar(
    embeddings: Embeddings | None,
    pregunta: str,
    k: int = 5,
    equipo: str | None = None,
    incluir_no_vigente: bool = False,
    incluir_no_aplicables: bool = False,
) -> list[Fragmento]:
    """`embeddings=None` → solo palabras clave (útil para medir cada mitad por separado)."""
    condiciones, parametros = _filtros(equipo, incluir_no_vigente, incluir_no_aplicables)
    limite = max(k, CANDIDATOS_POR_MITAD)

    conn = conectar()
    try:
        lexico = _ranking_lexico(conn, pregunta, condiciones, parametros, limite)
        semantico = (
            _ranking_semantico(conn, embeddings, pregunta, condiciones, parametros, limite)
            if embeddings is not None
            else []
        )
        elegidos = fusionar_rrf(lexico, semantico)[:k]
        if not elegidos:
            return []
        ids = [fid for fid, _ in elegidos]
        filas = conn.execute(
            "SELECT id, doc, titulo, texto, vigente FROM rag_fragmentos WHERE id = ANY(%s)", [ids]
        ).fetchall()
    finally:
        conn.close()

    por_id = {fila[0]: fila for fila in filas}
    return [
        Fragmento(cita=fid, doc=por_id[fid][1], titulo=por_id[fid][2], texto=por_id[fid][3],
                  vigente=por_id[fid][4], similitud=puntaje)
        for fid, puntaje in elegidos
        if fid in por_id
    ]


def citas(embeddings: Embeddings | None, pregunta: str, k: int = 20, **kwargs) -> list[str]:
    """Firma compatible con `corpus.evaluar.evaluar(buscar, suite)` de academia2026-tp (5.3):
    `buscar(pregunta, k) -> list[str]` de citas en orden."""
    return [f.cita for f in buscar(embeddings, pregunta, k=k, **kwargs)]
