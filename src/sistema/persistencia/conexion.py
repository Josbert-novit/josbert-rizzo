"""Conexión a Postgres compartida por toda la persistencia (Lote 6, B5/B6).

Antes, cada `conectar()` (diálogos, órdenes, fotos) abría la conexión sin tope de tiempo y corría
todo su DDL (`CREATE TABLE`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) en **cada** conexión: el
latido abre dos cada 2 s. Ahora:

- `connect_timeout` y `statement_timeout`: un Postgres trabado no cuelga para siempre el hilo del
  latido, la reconciliación o una orden — falla rápido, se registra y se reintenta.
- El DDL de cada módulo se aplica una sola vez por proceso (si falla, se reintenta en la próxima
  conexión). Las tablas no desaparecen mientras el proceso vive: el corrector borra los volúmenes
  con `down -v`, que también reinicia este proceso.
"""

import threading

import psycopg

from sistema.config import config

_ddl_aplicado: set[str] = set()
_lock_ddl = threading.Lock()


def abrir(*, statement_timeout: bool = True) -> psycopg.Connection:
    opciones = f"-c statement_timeout={config.postgres_statement_timeout_ms}" if statement_timeout else ""
    return psycopg.connect(
        config.postgres_dsn,
        autocommit=True,
        connect_timeout=config.postgres_connect_timeout_s,
        options=opciones,
    )


def conectar(clave: str, ddl: str) -> psycopg.Connection:
    """Abre una conexión y, la primera vez en este proceso, crea las tablas de `clave`."""
    conn = abrir()
    if clave not in _ddl_aplicado:
        try:
            with _lock_ddl:
                if clave not in _ddl_aplicado:
                    conn.execute(ddl)
                    _ddl_aplicado.add(clave)
        except Exception:
            conn.close()
            raise
    return conn


def olvidar_ddl() -> None:
    """Solo para tests."""
    _ddl_aplicado.clear()
