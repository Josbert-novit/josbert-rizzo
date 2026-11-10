"""Tendencia de un sensor en las últimas horas, para dar contexto a un escalado (2.4)."""

from datetime import datetime, timedelta

from sistema.integraciones.planta_http import PlantaHTTP

HORAS_TENDENCIA = 4.0


def tendencia_reciente(
    http: PlantaHTTP, tag: str, variable: str, hasta: datetime, horas: float = HORAS_TENDENCIA
) -> str | None:
    """"subiendo" / "bajando" / "estable", o None si no hay suficiente historial."""
    desde = hasta - timedelta(hours=horas)
    try:
        historial = http.historial(tag, variable, desde.isoformat(), hasta.isoformat())
    except Exception:
        return None
    valores = [p["valor"] for p in historial if p.get("valor") is not None]
    if len(valores) < 2:
        return None
    mitad = len(valores) // 2
    promedio_inicio = sum(valores[:mitad]) / mitad
    promedio_fin = sum(valores[mitad:]) / (len(valores) - mitad)
    delta = promedio_fin - promedio_inicio
    margen = abs(promedio_inicio) * 0.03 or 0.01
    if delta > margen:
        return "subiendo"
    if delta < -margen:
        return "bajando"
    return "estable"
