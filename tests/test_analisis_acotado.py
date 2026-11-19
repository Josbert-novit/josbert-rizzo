"""Hallazgo H7 (Fase 15): el análisis de órdenes tiene que estar acotado y, si falla, dejar
igual una orden de inspección pendiente (nunca quedarse sin orden ni aprobarla sola)."""

from datetime import datetime, timezone

import httpx
import pytest
from planta_contrato import EstadoOrden, EventoSistema, TipoEvento

import sistema.procesamiento.ordenes as ordenes_mod
from sistema.llm.analisis import (
    MAX_TOKENS_SALIDA_ANALISIS,
    TIMEOUT_LLAMADA_ANALISIS_S,
    Diagnostico,
)
from sistema.config import config
from sistema.procesamiento.ordenes import MAX_REINTENTOS_ANALISIS, procesar_evento_escalado

AHORA = datetime.now(timezone.utc)


def _evento(equipo="B-101") -> EventoSistema:
    return EventoSistema(
        id="EVT-1", ts=AHORA, tipo=TipoEvento.escalado, equipo=equipo, origen="sensor",
        resumen="vibración sostenida sobre la alarma",
    )


class AnalizadorQueFalla:
    def __init__(self, error: Exception):
        self._error = error

    def analizar(self, evento, consumo=None):
        raise self._error


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("timeout"),
        ValueError("JSON cortado por num_predict"),
        ConnectionError("ollama caído"),
    ],
)
def test_si_el_analisis_falla_queda_inspeccion_pendiente(monkeypatch, error):
    monkeypatch.setattr(ordenes_mod, "buscar_fragmentos", lambda *a, **k: [])
    orden = procesar_evento_escalado(
        _evento(), http=None, embeddings=None, traductor=None, analizador=AnalizadorQueFalla(error),
    )
    assert orden is not None
    assert orden.estado == EstadoOrden.pendiente  # nunca Aprobada sin análisis
    assert orden.equipo == "B-101"
    assert orden.tareas == ["I-VIB"]  # inspección: vale para D2 cuando la evidencia no alcanza
    assert orden.evento_ids == ["EVT-1"]
    assert type(error).__name__ in orden.motivo


def test_esquema_para_el_llm_no_incluye_evidencia_texto():
    """El LLM intentaba llenarlo copiando toda la evidencia: generación sin fin."""
    esquema = Diagnostico.model_json_schema()
    assert "evidencia_texto" not in esquema["properties"]


def test_evidencia_texto_sigue_existiendo_para_el_revisor():
    d = Diagnostico().model_copy(update={"evidencia_texto": "contexto real"})
    assert d.evidencia_texto == "contexto real"


def test_listas_del_esquema_tienen_tope():
    esquema = Diagnostico.model_json_schema()
    assert esquema["properties"]["causas_probables"]["maxItems"] == 3
    borrador = esquema["$defs"]["BorradorOrden"]["properties"]
    assert borrador["tareas"]["maxItems"] == 4
    assert borrador["repuestos"]["maxItems"] == 6


def test_topes_de_la_llamada_entran_en_el_corte_de_la_orden():
    assert MAX_TOKENS_SALIDA_ANALISIS > 0
    assert (MAX_REINTENTOS_ANALISIS + 1) * TIMEOUT_LLAMADA_ANALISIS_S < config.timeout_orden_s
