"""Cola de fotos pendientes de análisis de visión (Fase 13.2): mismo motivo que `ordenes.py` —
cuando no hay enlace (`enlace_arriba()`), el análisis de una foto no puede bloquear nada (ni la
alerta de seguridad ni el borrador de orden), así que se encola acá en vez de esperar. Un hilo de
reconciliación en `agente.py` la reintenta cuando vuelve el enlace.
"""

import psycopg

from sistema.config import config

DDL = """
CREATE TABLE IF NOT EXISTS fotos_pendientes_analisis (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    orden_id TEXT,
    resuelta BOOLEAN NOT NULL DEFAULT false,
    creada_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fotos_pendientes_sin_resolver_idx
    ON fotos_pendientes_analisis (resuelta) WHERE NOT resuelta;
"""


def conectar() -> psycopg.Connection:
    conn = psycopg.connect(config.postgres_dsn, autocommit=True)
    conn.execute(DDL)
    return conn


def encolar(conn: psycopg.Connection, foto_id: str, url: str, orden_id: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO fotos_pendientes_analisis (id, url, orden_id) VALUES (%s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (foto_id, url, orden_id),
    )


def pendientes(conn: psycopg.Connection) -> list[dict]:
    filas = conn.execute(
        "SELECT id, url, orden_id FROM fotos_pendientes_analisis WHERE NOT resuelta ORDER BY creada_en"
    ).fetchall()
    return [{"id": fid, "url": url, "orden_id": orden_id} for fid, url, orden_id in filas]


def marcar_resuelta(conn: psycopg.Connection, foto_id: str) -> None:
    conn.execute("UPDATE fotos_pendientes_analisis SET resuelta = true WHERE id = %s", (foto_id,))


def contar_sin_resolver(conn: psycopg.Connection) -> int:
    fila = conn.execute("SELECT count(*) FROM fotos_pendientes_analisis WHERE NOT resuelta").fetchone()
    return fila[0] if fila else 0
