"""Persistencia de órdenes (3.3, 3.5 — Fase 12): mismo esqueleto que `memoria.py` (UPSERT por id
estable en Postgres), pero para `OrdenSistema` en vez de `EstadoDialogo`. Es lo que hace sobrevivir
un corte de luz (F3): el estado de una orden vive acá, no en memoria del proceso ni en el broker
— un reinicio del agente recupera exactamente las órdenes pendientes de publicar y las publica de
nuevo (ver `agente.py`, hilo de reconciliación) en vez de perderlas.

`pendiente_publicar`: toda escritura (del análisis, Fase 10/8, o de una decisión humana vía
`interfaces/central.py`, Fase 12.3) la deja en `true` hasta que el hilo de reconciliación la
publica en `sistema/ordenes` y la marca `false` — el mismo patrón publish-then-persist-best-effort
que ya usa `_avanzar_dialogo`, pero invertido: acá se persiste primero, se publica después, porque
una decisión humana tiene que sobrevivir aunque el agente se caiga antes de llegar a publicarla.
"""

from datetime import datetime, timezone

import psycopg
from planta_contrato import OrdenSistema

from sistema.config import config

DDL = """
CREATE TABLE IF NOT EXISTS ordenes (
    id TEXT PRIMARY KEY,
    orden JSONB NOT NULL,
    pendiente_publicar BOOLEAN NOT NULL DEFAULT true,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ordenes_pendiente_publicar_idx ON ordenes (pendiente_publicar) WHERE pendiente_publicar;

-- Lote 3 (Fase 15): lo que propone el análisis con LLM para una orden ya publicada. Es una
-- sugerencia para el planificador (la ve por `interfaces/central.py`), NUNCA se publica en
-- `sistema/ordenes` ni cambia la orden — ver `procesamiento/ordenes.py::orden_inmediata`.
CREATE TABLE IF NOT EXISTS sugerencias_ordenes (
    orden_id TEXT PRIMARY KEY,
    sugerencia JSONB NOT NULL,
    creada_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def conectar() -> psycopg.Connection:
    conn = psycopg.connect(config.postgres_dsn, autocommit=True)
    conn.execute(DDL)
    return conn


def guardar(conn: psycopg.Connection, orden: OrdenSistema, pendiente_publicar: bool = True) -> None:
    """Idempotente por `id`, como exige el contrato ("cada publicación es el estado actual de la
    orden"): la fila de una orden siempre pisa la anterior, nunca se acumula historial acá
    (el historial real de decisiones no es parte de este TP — lo que importa es el estado actual)."""
    conn.execute(
        """
        INSERT INTO ordenes (id, orden, pendiente_publicar, actualizado_en)
        VALUES (%s, %s::jsonb, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            orden = EXCLUDED.orden, pendiente_publicar = EXCLUDED.pendiente_publicar,
            actualizado_en = EXCLUDED.actualizado_en
        """,
        (orden.id, orden.model_dump_json(), pendiente_publicar, datetime.now(timezone.utc)),
    )


def cargar(conn: psycopg.Connection, orden_id: str) -> OrdenSistema | None:
    fila = conn.execute("SELECT orden FROM ordenes WHERE id = %s", (orden_id,)).fetchone()
    return OrdenSistema.model_validate(fila[0]) if fila else None


def cargar_todas(conn: psycopg.Connection) -> list[OrdenSistema]:
    filas = conn.execute("SELECT orden FROM ordenes ORDER BY actualizado_en").fetchall()
    return [OrdenSistema.model_validate(f[0]) for f in filas]


def marcar_publicada(conn: psycopg.Connection, orden_id: str) -> None:
    conn.execute("UPDATE ordenes SET pendiente_publicar = false WHERE id = %s", (orden_id,))


def guardar_sugerencia(conn: psycopg.Connection, orden_id: str, sugerencia: OrdenSistema) -> None:
    conn.execute(
        """
        INSERT INTO sugerencias_ordenes (orden_id, sugerencia) VALUES (%s, %s::jsonb)
        ON CONFLICT (orden_id) DO UPDATE SET sugerencia = EXCLUDED.sugerencia, creada_en = now()
        """,
        (orden_id, sugerencia.model_dump_json()),
    )


def cargar_sugerencia(conn: psycopg.Connection, orden_id: str) -> OrdenSistema | None:
    fila = conn.execute(
        "SELECT sugerencia FROM sugerencias_ordenes WHERE orden_id = %s", (orden_id,)
    ).fetchone()
    return OrdenSistema.model_validate(fila[0]) if fila else None


def pendientes_de_publicar(conn: psycopg.Connection) -> list[OrdenSistema]:
    filas = conn.execute(
        "SELECT orden FROM ordenes WHERE pendiente_publicar = true ORDER BY actualizado_en"
    ).fetchall()
    return [OrdenSistema.model_validate(f[0]) for f in filas]
