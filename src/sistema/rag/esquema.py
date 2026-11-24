"""Esquema de pgvector para el corpus documental (5.1).

Una fila por fragmento (sección de manual/contrato, u orden del historial), con los metadatos
necesarios para filtrar las trampas del corpus (5.2) sin depender del LLM para eso: `vigente`
(Rev. C vs D), `aplica` (secciones de otros modelos de la familia TX o de otras plantas, que no
corresponden a PIA-3) y `equipos`.
"""

import psycopg
from pgvector.psycopg import register_vector

from sistema.persistencia import conexion

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
    aplica BOOLEAN NOT NULL DEFAULT true,  -- false: sección de otro modelo/planta (trampa 5.2)
    tipo TEXT,                        -- manual_fabricante, manual_operadora, contrato, historial
    equipos TEXT[] NOT NULL DEFAULT '{{}}',
    texto TEXT NOT NULL,
    embedding vector({DIMENSION_EMBEDDING})
);

CREATE INDEX IF NOT EXISTS rag_fragmentos_embedding_idx
    ON rag_fragmentos USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS rag_fragmentos_vigente_idx ON rag_fragmentos (vigente);
-- Bases indexadas antes de agregar `aplica`: la columna se suma sin reindexar desde cero.
ALTER TABLE rag_fragmentos ADD COLUMN IF NOT EXISTS aplica BOOLEAN NOT NULL DEFAULT true;

-- Búsqueda híbrida (Entrega 2, hallazgo H1): parte léxica en full-text de Postgres. Columna
-- generada, así está lista apenas se inserta el texto — sin esperar a Ollama. El título pesa más
-- (`A`) que el cuerpo (`B`), igual que la línea de base BM25 de la cátedra (título ×3).
ALTER TABLE rag_fragmentos ADD COLUMN IF NOT EXISTS tsv tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('spanish', coalesce(titulo, '')), 'A') ||
        setweight(to_tsvector('spanish', coalesce(doc, '') || ' ' || texto), 'B')
    ) STORED;
CREATE INDEX IF NOT EXISTS rag_fragmentos_tsv_idx ON rag_fragmentos USING gin (tsv);
"""


def conectar() -> psycopg.Connection:
    """La extensión `vector` tiene que existir en la base ANTES de registrar el adaptador de
    pgvector del lado de psycopg (si no, `register_vector` no encuentra el tipo en `pg_type` y
    falla) — por eso el orden acá no es arbitrario."""
    # Lote 6 (B5): con timeout de conexión; sin `statement_timeout` (la indexación hace escrituras
    # largas a propósito).
    conn = conexion.abrir(statement_timeout=False)
    conn.execute(DDL_EXTENSION)
    register_vector(conn)
    return conn


def crear_esquema(conn: psycopg.Connection) -> None:
    conn.execute(DDL)
