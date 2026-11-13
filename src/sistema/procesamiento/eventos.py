"""Detección en planta (requerimiento B), en código y SIN LLM: a partir de un parte confirmado
o de un desvío sostenido en `planta/sensores/#`, genera un `EventoSistema` idempotente, clasificado
en `SinNovedad` o `Escalado` según los límites de `GET /api/planta` (no del manual Rev. C vencido).

Un pico aislado no escala: hace falta que la anomalía esté sostenida en el tiempo (ver
`UMBRAL_SOSTENIDO_S`). Un equipo parado o en reserva no reporta valor (`calidad: sin_dato` o,
en el parte, `valor: None` con la observación "parada, en reserva") y nunca se evalúa como anomalía.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from planta_contrato import Calidad, EventoSistema, LecturaSensor, TipoEvento

from sistema.dominio.parte import Parte
from sistema.integraciones.planta_http import PlantaHTTP
from sistema.procesamiento.limites import CatalogoLimites, EstadoBombas, nivel_anomalia
from sistema.procesamiento.tendencia import tendencia_reciente
from sistema.procesamiento.unidades import a_kgcm2
from sistema.procesamiento.variables import mapear_variable
from sistema.reloj import reloj

log = logging.getLogger(__name__)

_VARIABLES_DE_PRESION = {"presion_descarga", "presion_succion", "presion_diferencial"}

# Tiempo (simulado, según el `ts` de cada lectura) que una anomalía tiene que sostenerse antes de
# escalar. Para un valor de disparo (más grave: riesgo de parada de equipo) el umbral es menor.
UMBRAL_SOSTENIDO_S = 300.0
UMBRAL_SOSTENIDO_DISPARO_S = 60.0


def evaluar_parte(parte: Parte, catalogo: CatalogoLimites, http: PlantaHTTP | None = None) -> EventoSistema:
    """Un evento por parte confirmado: `SinNovedad` si ninguna lectura excede los límites de
    alarma, `Escalado` si alguna sí (comparada en su unidad canónica, no la que dictó el operador).
    Id estable (`EVT-PARTE-{parte.id}`): el mismo parte siempre da el mismo evento."""
    anomalas: list[tuple[str, str, str]] = []
    equipo_escalado: str | None = None
    for fila in parte.filas:
        if fila.tag is None or fila.valor is None:
            continue
        canonica = mapear_variable(fila.variable)
        if canonica is None:
            continue
        limite = catalogo.limite(fila.tag, canonica)
        valor_comparable = a_kgcm2(fila.valor, fila.unidad) if canonica in _VARIABLES_DE_PRESION else fila.valor
        nivel = nivel_anomalia(limite, valor_comparable)
        if nivel is None:
            continue
        equipo_escalado = equipo_escalado or fila.tag
        detalle = f"{fila.tag}/{canonica}={fila.valor}{fila.unidad or ''} ({nivel})"
        if http is not None:
            tendencia = tendencia_reciente(http, fila.tag, canonica, fila.hora)
            if tendencia:
                detalle += f", tendencia {tendencia} en las últimas horas"
        anomalas.append((fila.tag, canonica, detalle))

    hora = parte.filas[0].hora if parte.filas else reloj.ahora()
    if not anomalas:
        return EventoSistema(
            id=f"EVT-PARTE-{parte.id}",
            ts=hora,
            tipo=TipoEvento.sin_novedad,
            origen="parte",
            parte_id=parte.id,
            nota_ids=parte.nota_ids,
            resumen="Recorrida confirmada sin novedad.",
        )
    resumen = "Valores fuera de límite en la recorrida: " + "; ".join(d for _, _, d in anomalas)
    return EventoSistema(
        id=f"EVT-PARTE-{parte.id}",
        ts=hora,
        tipo=TipoEvento.escalado,
        equipo=equipo_escalado,
        origen="parte",
        parte_id=parte.id,
        nota_ids=parte.nota_ids,
        resumen=resumen,
    )


@dataclass
class _Racha:
    desde: datetime
    nivel: str
    evento_id: str | None = None


class DetectorSensores:
    """Sigue cada sensor (`planta/sensores/{tag}/{variable}`) y escala solo si la anomalía se
    sostiene por `UMBRAL_SOSTENIDO_S` (o `UMBRAL_SOSTENIDO_DISPARO_S` si es nivel disparo), no
    ante un pico aislado. El id del evento queda atado al inicio de la racha: la misma racha
    sostenida nunca genera un segundo evento distinto."""

    def __init__(
        self, catalogo: CatalogoLimites, http: PlantaHTTP | None = None, estado_bombas: EstadoBombas | None = None
    ) -> None:
        self._catalogo = catalogo
        self._http = http
        self._estado_bombas = estado_bombas or (EstadoBombas(http) if http is not None else None)
        self._rachas: dict[tuple[str, str], _Racha] = {}

    def procesar(self, lectura: LecturaSensor) -> EventoSistema | None:
        clave = (lectura.tag, lectura.variable)

        if lectura.calidad != Calidad.buena or lectura.valor is None:
            # Equipo parado o en reserva, o instrumento fuera de servicio: no es anomalía.
            self._rachas.pop(clave, None)
            return None

        if (
            self._estado_bombas is not None
            and self._estado_bombas.aplica_a_bomba_detenida(lectura.variable)
            and not self._estado_bombas.en_marcha(lectura.tag)
        ):
            # Bomba parada/en reserva: presión baja es esperable, no una anomalía (2.3).
            self._rachas.pop(clave, None)
            return None

        limite = self._catalogo.limite(lectura.tag, lectura.variable)
        nivel = nivel_anomalia(limite, lectura.valor)
        racha = self._rachas.get(clave)

        if nivel is None:
            self._rachas.pop(clave, None)
            return None

        if racha is None or racha.nivel != nivel:
            self._rachas[clave] = _Racha(desde=lectura.ts, nivel=nivel)
            return None

        umbral = UMBRAL_SOSTENIDO_DISPARO_S if nivel == "disparo" else UMBRAL_SOSTENIDO_S
        sostenido_s = (lectura.ts - racha.desde).total_seconds()
        if sostenido_s < umbral or racha.evento_id is not None:
            return None

        racha.evento_id = f"EVT-SENSOR-{lectura.tag}-{lectura.variable}-{racha.desde.isoformat()}"
        resumen = (
            f"{lectura.tag}/{lectura.variable} sostenido en nivel {nivel} "
            f"({lectura.valor} {limite.unidad}) desde hace {sostenido_s:.0f}s"
        )
        if self._http is not None:
            tendencia = tendencia_reciente(self._http, lectura.tag, lectura.variable, lectura.ts)
            if tendencia:
                resumen += f", tendencia {tendencia} en las últimas horas"

        return EventoSistema(
            id=racha.evento_id,
            ts=lectura.ts,
            tipo=TipoEvento.escalado,
            equipo=lectura.tag,
            origen="sensor",
            resumen=resumen,
        )
