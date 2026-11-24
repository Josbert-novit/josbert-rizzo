"""Esquema de pgvector para el corpus documental (5.1).

Una fila por fragmento (sección de manual/contrato, u orden del historial), con los metadatos
necesarios para filtrar las trampas del corpus (5.2) sin depender del LLM para eso: `vigente`
(Rev. C vs D), `aplica` (secciones de otros modelos de la familia TX o de otras plantas, que no
corresponden a PIA-3) y `equipos`.
"""

import psycopg
from pgvector.psycopg import register_vector

from sistema.config import config
from sistema.persistencia import conexion

# Dimensión del vector según el modelo de embeddings (Lote 7: bge-m3 por defecto).
DIMENSIONES = {"bge-m3": 1024, "nomic-embed-text": 768}
DIMENSION_EMBEDDING = DIMENSIONES.get(config.rag_modelo_embeddings.split(":")[0], 1024)
CONFIG_FULL_TEXT = "es_sin_tildes"  # `spanish` + `unaccent`, ver DDL

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

-- Cambio de modelo de embeddings (Lote 7: nomic 768 → bge-m3 1024): si la columna de una base ya
-- indexada tiene otra dimensión, se recrea vacía y la pasada 2 de `indexar.py` la vuelve a llenar
-- (desde el cache precalculado, cuya clave incluye el modelo).
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'rag_fragmentos'::regclass AND attname = 'embedding'
          AND atttypmod <> {DIMENSION_EMBEDDING}
    ) THEN
        ALTER TABLE rag_fragmentos DROP COLUMN embedding;
        ALTER TABLE rag_fragmentos ADD COLUMN embedding vector({DIMENSION_EMBEDDING});
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS rag_fragmentos_embedding_idx
    ON rag_fragmentos USING hnsw (embedding vector_cosine_ops);
-- Lote 7: clave (modelo + texto embebido) del vector guardado. Si cambia el modelo o lo que se
-- embebe (contextual chunking), la fila se re-embebe aunque `embedding` no sea NULL.
ALTER TABLE rag_fragmentos ADD COLUMN IF NOT EXISTS embedding_clave TEXT;
CREATE INDEX IF NOT EXISTS rag_fragmentos_vigente_idx ON rag_fragmentos (vigente);
-- Bases indexadas antes de agregar `aplica`: la columna se suma sin reindexar desde cero.
ALTER TABLE rag_fragmentos ADD COLUMN IF NOT EXISTS aplica BOOLEAN NOT NULL DEFAULT true;

-- Búsqueda sin tildes (bug #67): el historial de órdenes está escrito sin tildes ("valvulas",
-- "caudalimetro", "succion") y el diccionario `spanish` no las quita, así que "válvulas" de una
-- pregunta no coincidía con "valvulas" del texto. Configuración propia = `spanish` + `unaccent`.
CREATE EXTENSION IF NOT EXISTS unaccent;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = '{CONFIG_FULL_TEXT}') THEN
        CREATE TEXT SEARCH CONFIGURATION {CONFIG_FULL_TEXT} (COPY = spanish);
        ALTER TEXT SEARCH CONFIGURATION {CONFIG_FULL_TEXT}
            ALTER MAPPING FOR hword, hword_part, word WITH unaccent, spanish_stem;
    END IF;
END $$;

-- Búsqueda híbrida (Entrega 2, hallazgo H1): parte léxica en full-text de Postgres. Columna
-- generada, así está lista apenas se inserta el texto — sin esperar a Ollama. El título pesa más
-- (`A`) que el cuerpo (`B`), igual que la línea de base BM25 de la cátedra (título ×3).
-- `tsv` (con `spanish`, con tildes) era la columna anterior: se borra en bases ya indexadas.
ALTER TABLE rag_fragmentos DROP COLUMN IF EXISTS tsv;
ALTER TABLE rag_fragmentos ADD COLUMN IF NOT EXISTS tsv_st tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('{CONFIG_FULL_TEXT}', coalesce(titulo, '')), 'A') ||
        setweight(to_tsvector('{CONFIG_FULL_TEXT}', coalesce(doc, '') || ' ' || texto), 'B')
    ) STORED;
CREATE INDEX IF NOT EXISTS rag_fragmentos_tsv_st_idx ON rag_fragmentos USING gin (tsv_st);
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
