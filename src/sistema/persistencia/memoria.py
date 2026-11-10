"""Memoria de conversación (5.7): persiste en Postgres el `EstadoDialogo` de cada nota en curso
(el parte en borrador, qué fila está en pregunta, el presupuesto de pasos consumido, los intentos
de desambiguación) — para que un reinicio del agente no pierda una conversación a mitad de
camino. Hasta acá (ver docstring de `procesamiento/dialogo.py`) esto vivía solo en memoria del
proceso.

**Qué se guarda**: el estado completo necesario para retomar el diálogo exactamente donde se dejó
— no es un log de auditoría (eso ya existe: cada nota deja su `transcripcion.txt` y cada parte su
`parte.json` en disco, vía `guardar_parte`), es memoria de trabajo de una conversación abierta.

**Por cuánto tiempo**: mientras el parte siga en `Borrador`. Se borra apenas el parte queda
`Confirmado` — en ese momento ya no hay conversación en curso que recordar, el resultado final
vive en `parte.json`, no hace falta que seguir ocupando una fila en Postgres. **No** hay una
limpieza por tiempo absoluto (p. ej. un cron que borre diálogos de más de N horas sin respuesta):
un diálogo que nunca se contesta se queda en la tabla indefinidamente. Es una limitación conocida,
no resuelta acá por alcance — distinta del timeout de 4.1 (`TIMEOUT_NOTA_S`), que corta el
*procesamiento inicial* de la nota (transcripción + extracción), no la espera de una respuesta del
operador en la tablet, que puede tardar legítimamente lo que tarde la recorrida real.

La identidad estable de una conversación es el `nota_id` (no el `mensaje_id`, que cambia en cada
vuelta del diálogo) — así un `UPSERT` por nota siempre pisa la fila anterior de esa misma
conversación en vez de dejar basura de vueltas previas.
"""

import json
from datetime import datetime, timezone

import psycopg

from sistema.config import config
from sistema.dominio.parte import Parte
from sistema.llm.control import ConsumoTokens
from sistema.procesamiento.dialogo import EstadoDialogo

DDL = """
CREATE TABLE IF NOT EXISTS dialogos_pendientes (
    nota_id TEXT PRIMARY KEY,
    mensaje_id TEXT NOT NULL,
    estado JSONB NOT NULL,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS dialogos_pendientes_mensaje_id_idx ON dialogos_pendientes (mensaje_id);
"""


def conectar() -> psycopg.Connection:
    conn = psycopg.connect(config.postgres_dsn, autocommit=True)
    conn.execute(DDL)
    return conn


def _estado_a_json(estado: EstadoDialogo) -> dict:
    return {
        "parte": json.loads(estado.parte.model_dump_json()),
        "mensaje_id_pendiente": estado.mensaje_id_pendiente,
        "fila_en_pregunta": estado.fila_en_pregunta,
        "consumo": {
            "pasos": estado.consumo.pasos,
            "tokens_prompt": estado.consumo.tokens_prompt,
            "tokens_respuesta": estado.consumo.tokens_respuesta,
        },
        "intentos_desambiguacion": estado.intentos_desambiguacion,
    }


def _json_a_estado(datos: dict) -> EstadoDialogo:
    return EstadoDialogo(
        parte=Parte.model_validate(datos["parte"]),
        mensaje_id_pendiente=datos["mensaje_id_pendiente"],
        fila_en_pregunta=datos["fila_en_pregunta"],
        consumo=ConsumoTokens(**datos["consumo"]),
        intentos_desambiguacion={int(k): v for k, v in datos["intentos_desambiguacion"].items()},
    )


def guardar(conn: psycopg.Connection, nota_id: str, mensaje_id: str, estado: EstadoDialogo) -> None:
    conn.execute(
        """
        INSERT INTO dialogos_pendientes (nota_id, mensaje_id, estado, actualizado_en)
        VALUES (%s, %s, %s::jsonb, %s)
        ON CONFLICT (nota_id) DO UPDATE SET
            mensaje_id = EXCLUDED.mensaje_id, estado = EXCLUDED.estado,
            actualizado_en = EXCLUDED.actualizado_en
        """,
        (nota_id, mensaje_id, json.dumps(_estado_a_json(estado)), datetime.now(timezone.utc)),
    )


def eliminar(conn: psycopg.Connection, nota_id: str) -> None:
    conn.execute("DELETE FROM dialogos_pendientes WHERE nota_id = %s", (nota_id,))


def cargar_todos(conn: psycopg.Connection) -> dict[str, EstadoDialogo]:
    """Para recuperar en el arranque: `{mensaje_id: EstadoDialogo}`, igual que el diccionario en
    memoria que reemplaza — así un reinicio del agente retoma las conversaciones abiertas en vez
    de perderlas (quedaban mudas hasta la próxima nota, sin poder entender la respuesta
    pendiente)."""
    filas = conn.execute("SELECT mensaje_id, estado FROM dialogos_pendientes").fetchall()
    return {mensaje_id: _json_a_estado(estado) for mensaje_id, estado in filas}
