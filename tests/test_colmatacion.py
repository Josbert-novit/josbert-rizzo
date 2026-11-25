"""Lote 9 (BUGS #78): colmatación acelerada por la tendencia del historian, con series sintéticas
parecidas a las del simulador (ΔP base 0,0011 kg/cm²/h en el F-201B, ruido de 0,008, muestreo de
un minuto, caudal de diseño 2.400 m3/d)."""

import math
import random
from datetime import UTC, datetime, timedelta

from sistema.procesamiento.colmatacion import (
    DetectorColmatacion,
    Umbrales,
    evaluar_colmatacion,
    id_evento,
)
from sistema.procesamiento.ordenes import tarea_estandar_clara

INICIO = datetime(2026, 11, 1, tzinfo=UTC)
BASE = 0.0011  # kg/cm²/h


def _series(horas: float, pendiente, dp0: float = 0.7, ondulacion: float = 0.0, cambio_en: float | None = None,
            semilla: int = 1):
    """`pendiente(h)` en kg/cm²/h sobre la ΔP corregida; la ΔP medida sale con el caudal real."""
    rng = random.Random(semilla)
    dp, caudal = [], []
    sucio = dp0
    for minuto in range(int(horas * 60)):
        h = minuto / 60
        t = INICIO + timedelta(minutes=minuto)
        if cambio_en is not None and abs(h - cambio_en) < 1 / 120:
            sucio = 0.30
        sucio += pendiente(h) / 60
        q = 2400 * (1 + ondulacion * math.sin(2 * math.pi * h / 24))
        dp.append({"ts": t.isoformat(), "valor": round(sucio * (q / 2400) ** 2 + rng.gauss(0, 0.008), 2)})
        caudal.append({"ts": t.isoformat(), "valor": round(q + rng.gauss(0, 4))})
    return dp, caudal, INICIO + timedelta(hours=horas)


def test_ensuciamiento_normal_no_dispara():
    dp, q, ahora = _series(24 * 10, lambda h: BASE)
    assert evaluar_colmatacion("F-201B", dp, q, ahora) is None


def test_ensuciamiento_cinco_veces_mas_rapido_dispara():
    dias = 24 * 10
    dp, q, ahora = _series(dias + 5, lambda h: BASE if h < dias else 5 * BASE)
    hallazgo = evaluar_colmatacion("F-201B", dp, q, ahora)
    assert hallazgo is not None
    assert hallazgo.veces > 3
    assert abs(hallazgo.pendiente_base - BASE) < 0.0005


def test_ondulacion_del_caudal_no_dispara_gracias_a_la_correccion():
    # ±3% de caudal = ±6% de ΔP. Medido: sin corregir dispara en 11 de las 24 horas del día.
    for hora in (3, 9, 15, 21):
        dp, q, ahora = _series(24 * 10 + hora, lambda h: BASE, dp0=1.0, ondulacion=0.03)
        assert evaluar_colmatacion("F-201B", dp, q, ahora) is None, hora


def test_cambio_de_cartuchos_dentro_de_la_ventana_no_dispara():
    dias = 24 * 10
    dp, q, ahora = _series(dias + 2, lambda h: BASE, cambio_en=dias)
    assert evaluar_colmatacion("F-201B", dp, q, ahora) is None


def test_sin_historial_suficiente_no_dice_nada():
    dp, q, ahora = _series(6, lambda h: 5 * BASE)
    assert evaluar_colmatacion("F-201B", dp, q, ahora) is None


def test_pendiente_alta_pero_chica_en_absoluto_no_dispara():
    # Un ciclo casi plano: 5 veces "nada" sigue siendo nada (mínimo absoluto).
    dias = 24 * 10
    dp, q, ahora = _series(dias + 5, lambda h: 0.0002 if h < dias else 0.001)
    assert evaluar_colmatacion("F-201B", dp, q, ahora, Umbrales()) is None


class HistorianFalso:
    def __init__(self, series: dict):
        self._series = series
        self.pedidos = 0

    def historial(self, tag, variable, desde, hasta):
        self.pedidos += 1
        d, h = datetime.fromisoformat(desde), datetime.fromisoformat(hasta)
        return [p for p in self._series[(tag, variable)] if d <= datetime.fromisoformat(p["ts"]) <= h]


def _detector(horas_falla: float):
    dias = 24 * 10
    dp, q, _ = _series(dias + horas_falla, lambda h: BASE if h < dias else 6 * BASE)
    http = HistorianFalso({("F-201B", "presion_diferencial"): dp, ("FT-401", "caudal"): q})
    return DetectorColmatacion(http, ["F-201B"]), http, INICIO + timedelta(hours=dias)


def test_detector_escala_recien_con_dos_revisiones_seguidas_y_una_sola_vez():
    detector, _, inicio_falla = _detector(10)
    eventos = []
    for media_hora in range(1, 21):
        ahora = inicio_falla + timedelta(minutes=30 * media_hora)
        nuevos = detector.revisar(ahora)
        if nuevos and not eventos:
            primera = ahora
        eventos += nuevos
    assert len(eventos) == 1
    evento = eventos[0]
    assert evento.equipo == "F-201B" and evento.tipo.value == "Escalado" and evento.origen == "sensor"
    assert "[FC48-3 §4.2]" in evento.resumen
    assert primera <= inicio_falla + timedelta(hours=6)


def test_detector_respeta_el_intervalo_entre_revisiones():
    detector, http, inicio_falla = _detector(2)
    detector.revisar(inicio_falla)
    pedidos = http.pedidos
    assert detector.revisar(inicio_falla + timedelta(minutes=10)) == []
    assert http.pedidos == pedidos


def test_detector_se_reinicia_si_el_reloj_de_planta_vuelve_atras():
    # Otro escenario: el simulador arranca con otro reloj y borra su historian.
    detector, _, inicio_falla = _detector(10)
    eventos = []
    for media_hora in range(1, 21):
        eventos += detector.revisar(inicio_falla + timedelta(minutes=30 * media_hora))
    assert len(eventos) == 1
    otra = []
    for media_hora in range(1, 21):
        otra += detector.revisar(inicio_falla - timedelta(days=3) + timedelta(minutes=30 * media_hora))
    assert detector._ultima_revision < inicio_falla
    assert otra == []  # en ese tramo del historial no hay colmatación


def test_id_del_evento_es_el_mismo_tras_un_reinicio_y_lleva_a_t_f_car():
    ahora = datetime(2026, 11, 6, 9, 30, tzinfo=UTC)
    assert id_evento("F-201B", ahora) == id_evento("F-201B", ahora + timedelta(hours=5))
    detector, _, inicio_falla = _detector(10)
    eventos = []
    for media_hora in range(1, 21):
        eventos += detector.revisar(inicio_falla + timedelta(minutes=30 * media_hora))
    # Mismo camino que un escalado del sensor de diferencial: la orden inmediata es T-F-CAR.
    assert tarea_estandar_clara(eventos[0]) == "T-F-CAR"


def test_con_la_dp_ya_en_alarma_no_opina_la_tendencia():
    # De la alarma en adelante escala DetectorSensores (alarma sostenida) con la misma T-F-CAR.
    dias = 24 * 10
    dp, q, ahora = _series(dias + 5, lambda h: BASE if h < dias else 5 * BASE, dp0=1.3)
    assert evaluar_colmatacion("F-201B", dp, q, ahora) is not None
    assert evaluar_colmatacion("F-201B", dp, q, ahora, alarma_alta=1.5) is None
    assert evaluar_colmatacion("F-201B", dp, q, ahora, alarma_alta=2.5) is not None
