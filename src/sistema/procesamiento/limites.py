"""Límites de alarma de cada sensor, desde `GET /api/planta` (`sensores`) — NO del manual Rev. C,
que está vencido. Misma entrada (tag, variable, valor) siempre da la misma decisión."""

import time
from dataclasses import dataclass

from sistema.integraciones.planta_http import PlantaHTTP

_VARIABLES_PRESION_BOMBA = {"presion_succion", "presion_descarga"}


@dataclass
class Limite:
    unidad: str
    alarma_alta: float | None = None
    alarma_baja: float | None = None
    disparo: float | None = None


def nivel_anomalia(limite: Limite | None, valor: float | None) -> str | None:
    """None si está dentro de rango normal, "alarma" o "disparo" (más grave) si lo excede."""
    if limite is None or valor is None:
        return None
    if limite.disparo is not None and valor >= limite.disparo:
        return "disparo"
    if limite.alarma_alta is not None and valor >= limite.alarma_alta:
        return "alarma"
    if limite.alarma_baja is not None and valor <= limite.alarma_baja:
        return "alarma"
    return None


class CatalogoLimites:
    def __init__(self, http: PlantaHTTP) -> None:
        self._http = http
        self._limites: dict[tuple[str, str], Limite] | None = None

    def _cargar(self) -> dict[tuple[str, str], Limite]:
        if self._limites is None:
            sensores = self._http.equipos()["sensores"]
            self._limites = {
                (s["tag"], s["variable"]): Limite(
                    unidad=s["unidad"],
                    alarma_alta=s.get("alarma_alta"),
                    alarma_baja=s.get("alarma_baja"),
                    disparo=s.get("disparo"),
                )
                for s in sensores
            }
        return self._limites

    def limite(self, tag: str, variable: str) -> Limite | None:
        return self._cargar().get((tag, variable))


class EstadoBombas:
    """Si una bomba no está en marcha (parada, en reserva, en mantenimiento), su presión de
    succión/descarga va a leer bajo por diseño: eso no es una anomalía (2.3). Consulta
    `GET /api/estado` (en el contrato: "estado completo de la planta, lo que ve el visor") con
    un cache corto para no pegarle a cada lectura de sensor."""

    def __init__(self, http: PlantaHTTP, ttl_s: float = 5.0) -> None:
        self._http = http
        self._ttl_s = ttl_s
        self._cache: dict[str, str] = {}
        self._actualizado = 0.0

    def _refrescar(self) -> None:
        ahora = time.monotonic()
        if ahora - self._actualizado < self._ttl_s:
            return
        self._actualizado = ahora
        try:
            estado = self._http.estado()
        except Exception:
            return
        self._cache = {b["tag"]: b["estado"] for b in estado.get("bombas", [])}

    def en_marcha(self, tag: str) -> bool:
        self._refrescar()
        return self._cache.get(tag, "marcha") == "marcha"

    def aplica_a_bomba_detenida(self, variable: str) -> bool:
        return variable in _VARIABLES_PRESION_BOMBA
