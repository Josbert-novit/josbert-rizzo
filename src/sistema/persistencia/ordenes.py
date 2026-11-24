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

from sistema.persistencia import conexion

DDL = """
CREATE TABLE IF NOT EXISTS ordenes (
    id TEXT PRIMARY KEY,
    orden JSONB NOT NULL,
    pendiente_publicar BOOLEAN NOT NULL DEFAULT true,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Correlation ID (Anexo 3.3): une evento -> orden -> cierre. Es el id del evento raíz que abrió la
-- orden (el primero de `evento_ids`; los escalados que se suman después no lo cambian). El cierre
-- llega con `orden_id`, así que la cadena completa se reconstruye con una sola consulta.
ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS correlacion_id TEXT
    GENERATED ALWAYS AS (orden->'evento_ids'->>0) STORED;
CREATE INDEX IF NOT EXISTS ordenes_correlacion_idx ON ordenes (correlacion_id);
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


def correlacion_id(orden: OrdenSistema) -> str:
    """Mismo valor que la columna generada `correlacion_id`: el evento raíz de la orden."""
    return orden.evento_ids[0] if orden.evento_ids else orden.id


def conectar() -> psycopg.Connection:
    # Lote 6 (B5/B6): con timeouts y el DDL una sola vez por proceso (ver `conexion.py`).
    return conexion.conectar("ordenes", DDL)


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


def marcar_publicada(conn: psycopg.Connection, orden_id: str, version: datetime | None = None) -> bool:
    """Lote 6 (B2): con `version` (el `actualizado_en` de lo que se publicó), solo marca si la fila
    no cambió en el medio — si `central` o un escalado que se suma la tocaron entre la lectura y la
    publicación, queda pendiente y la próxima vuelta publica la versión nueva."""
    if version is None:
        cursor = conn.execute("UPDATE ordenes SET pendiente_publicar = false WHERE id = %s", (orden_id,))
    else:
        cursor = conn.execute(
            "UPDATE ordenes SET pendiente_publicar = false WHERE id = %s AND actualizado_en = %s",
            (orden_id, version),
        )
    return cursor.rowcount > 0


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
    return [orden for orden, _version in pendientes_de_publicar_con_version(conn)]


def pendientes_de_publicar_con_version(conn: psycopg.Connection) -> list[tuple[OrdenSistema, datetime]]:
    filas = conn.execute(
        "SELECT orden, actualizado_en FROM ordenes WHERE pendiente_publicar = true ORDER BY actualizado_en"
    ).fetchall()
    return [(OrdenSistema.model_validate(f[0]), f[1]) for f in filas]
