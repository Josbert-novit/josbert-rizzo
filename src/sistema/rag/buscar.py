"""Búsqueda semántica sobre el corpus indexado (5.1) — similaridad coseno en pgvector.

Trampa de versiones (5.2): por default solo se buscan fragmentos `vigente=true`. La Rev. C del
manual de la bomba (`vigente=false`) se indexa igual (por si alguna vez hace falta consultarla a
propósito, con `incluir_no_vigente=True`), pero nunca compite con la Rev. D por default — así no
hace falta que el ranking "le gane" a la revisión vieja, directamente no está en la carrera.
`GET /api/planta` sigue siendo la fuente de verdad para límites de alarma (eso no sale del RAG,
ver 5.5); esto es para preguntas de texto libre contra los manuales/contrato/historial.
"""

from dataclasses import dataclass

from pgvector import Vector

from sistema.rag.embeddings import Embeddings
from sistema.rag.esquema import conectar


@dataclass
class Fragmento:
    cita: str  # id, p. ej. "TX350-D §3.2" o "historial OT-25-0026"
    doc: str
    titulo: str | None
    texto: str
    vigente: bool
    similitud: float


def buscar(
    embeddings: Embeddings,
    pregunta: str,
    k: int = 5,
    equipo: str | None = None,
    incluir_no_vigente: bool = False,
) -> list[Fragmento]:
    vector = Vector(embeddings.embed_consulta(pregunta))

    condiciones = []
    parametros_condicion: list = []
    if not incluir_no_vigente:
        condiciones.append("vigente = true")
    if equipo:
        condiciones.append("(cardinality(equipos) = 0 OR %s = ANY(equipos))")
        parametros_condicion.append(equipo)
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    conn = conectar()
    try:
        filas = conn.execute(
            f"""
            SELECT id, doc, titulo, texto, vigente, 1 - (embedding <=> %s) AS similitud
            FROM rag_fragmentos
            {where}
            ORDER BY embedding <=> %s
            LIMIT %s
            """,
            [vector, *parametros_condicion, vector, k],
        ).fetchall()
    finally:
        conn.close()

    return [
        Fragmento(cita=fid, doc=doc, titulo=titulo, texto=texto, vigente=vigente, similitud=similitud)
        for fid, doc, titulo, texto, vigente, similitud in filas
    ]


def citas(embeddings: Embeddings, pregunta: str, k: int = 20, **kwargs) -> list[str]:
    """Firma compatible con `corpus.evaluar.evaluar(buscar, suite)` de academia2026-tp (5.3):
    `buscar(pregunta, k) -> list[str]` de citas en orden."""
    return [f.cita for f in buscar(embeddings, pregunta, k=k, **kwargs)]
