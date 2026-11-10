"""Verifica un valor dictado contra el rango histórico del sensor conectado equivalente
(GET /api/historian/{tag}/{variable}, 45 días). Un valor fuera de ese rango pide confirmación
antes de guardarse.

Las lecturas puramente locales (temperatura por IR, goteo, estado de correas) no tienen un
sensor conectado equivalente: no hay con qué comparar, así que no se verifican."""

from datetime import datetime, timedelta

from sistema.integraciones.planta_http import PlantaHTTP
from sistema.procesamiento.unidades import a_kgcm2

VENTANA_DIAS = 45
MARGEN = 0.15
_VARIABLES_DE_PRESION = {"presion_descarga", "presion_succion", "presion_diferencial"}


class VerificadorRango:
    def __init__(self, http: PlantaHTTP) -> None:
        self._http = http
        self._cache: dict[tuple[str, str], tuple[float, float] | None] = {}

    def _rango(self, tag: str, variable: str, hasta: datetime) -> tuple[float, float] | None:
        clave = (tag, variable)
        if clave not in self._cache:
            desde = hasta - timedelta(days=VENTANA_DIAS)
            historial = self._http.historial(tag, variable, desde.isoformat(), hasta.isoformat())
            valores = [p["valor"] for p in historial if p.get("valor") is not None]
            self._cache[clave] = (min(valores), max(valores)) if valores else None
        return self._cache[clave]

    def verificar(
        self, tag: str, variable_canonica: str, valor: float, unidad: str | None, hasta: datetime
    ) -> tuple[bool, str | None]:
        rango = self._rango(tag, variable_canonica, hasta)
        if rango is None:
            return False, None

        minimo, maximo = rango
        valor_comparable = (
            a_kgcm2(valor, unidad) if variable_canonica in _VARIABLES_DE_PRESION else valor
        )
        amplitud = maximo - minimo
        margen = amplitud * MARGEN if amplitud > 0 else abs(maximo) * MARGEN
        if valor_comparable < minimo - margen or valor_comparable > maximo + margen:
            return True, (
                f"{valor} {unidad or ''} está fuera del rango histórico de {tag}/{variable_canonica} "
                f"({minimo:.1f}–{maximo:.1f}, últimos {VENTANA_DIAS} días): confirmar con el operador"
            ).strip()
        return False, None
