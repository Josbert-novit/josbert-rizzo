"""Esquema de pgvector para el corpus documental (5.1).

Una fila por fragmento (sección de manual/contrato, u orden del historial), con los metadatos
necesarios para filtrar las trampas del corpus (5.2) sin depender del LLM para eso: `vigente`
(Rev. C vs D) y `equipos` (para no confundir una TX-350 con una TX-250 mencionada de paso).
"""

import psycopg
from pgvector.psycopg import register_vector

from sistema.config import config

DIMENSION_EMBEDDING = 768  # nomic-embed-text

DDL_EXTENSION = "CREATE EXTENSION IF NOT EXISTS vector;"

DDL = f"""
CREATE TABLE IF NOT EXISTS rag_fragmentos (
    id TEXT PRIMARY KEY,              -- cita: "TX350-D §3.2" o "historial OT-25-0026"
    doc TEXT NOT NULL,                -- "TX350-D", "MBP-4", "historial", ...
    numero TEXT,
    titulo TEXT,
    clave TEXT,
    pagina INTEGER,
    vigente BOOLEAN NOT NULL DEFAULT true,
    tipo TEXT,                        -- manual_fabricante, manual_operadora, contrato, historial
    equipos TEXT[] NOT NULL DEFAULT '{{}}',
    texto TEXT NOT NULL,
    embedding vector({DIMENSION_EMBEDDING})
);

CREATE INDEX IF NOT EXISTS rag_fragmentos_embedding_idx
    ON rag_fragmentos USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS rag_fragmentos_vigente_idx ON rag_fragmentos (vigente);
"""


def conectar() -> psycopg.Connection:
    """La extensión `vector` tiene que existir en la base ANTES de registrar el adaptador de
    pgvector del lado de psycopg (si no, `register_vector` no encuentra el tipo en `pg_type` y
    falla) — por eso el orden acá no es arbitrario."""
    conn = psycopg.connect(config.postgres_dsn, autocommit=True)
    conn.execute(DDL_EXTENSION)
    register_vector(conn)
    return conn


def crear_esquema(conn: psycopg.Connection) -> None:
    conn.execute(DDL)
