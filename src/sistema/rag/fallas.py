"""Traduce entre el catálogo de fallas de la planta (F01-F10) y los códigos propios de cada
fabricante (TX-T01, FC-T01, ...) — la trampa de "códigos distintos" de 5.2. La tabla sale de
MBP-4 §6.1 (`GET /api/documentos/MBP-4/secciones?numero=6.1`), no está hardcodeada a mano: si se
regenera el corpus con otra semilla, se vuelve a parsear de la fuente real en vez de quedar
desactualizada.
"""

import re

from sistema.integraciones.planta_http import PlantaHTTP

_FILA = re.compile(r"^(F\d{2})\s*\|\s*[^|]+\|\s*([^|]+)\s*\|")


def _parsear_tabla(texto: str) -> dict[str, list[str]]:
    f_a_fabricante: dict[str, list[str]] = {}
    for linea in texto.splitlines():
        m = _FILA.match(linea.strip())
        if not m:
            continue
        codigo_f, columna_fabricante = m.group(1), m.group(2)
        # La columna puede traer más de un código separado por "/" (F10: "TX-T08 / FC-T04").
        codigos = [c.strip() for c in columna_fabricante.split("/") if c.strip()]
        f_a_fabricante[codigo_f] = codigos
    return f_a_fabricante


class TraductorFallas:
    def __init__(self, http: PlantaHTTP) -> None:
        self._http = http
        self._f_a_fabricante: dict[str, list[str]] | None = None
        self._fabricante_a_f: dict[str, str] = {}

    def _cargar(self) -> None:
        if self._f_a_fabricante is not None:
            return
        secciones = self._http.secciones("MBP-4", numero="6.1")
        texto = secciones[0]["texto"] if secciones else ""
        self._f_a_fabricante = _parsear_tabla(texto)
        for codigo_f, codigos_fabricante in self._f_a_fabricante.items():
            for codigo in codigos_fabricante:
                self._fabricante_a_f[codigo] = codigo_f

    def a_codigo_fabricante(self, codigo_f: str) -> list[str]:
        """"F07" -> ["FC-T01"]."""
        self._cargar()
        return self._f_a_fabricante.get(codigo_f.strip().upper(), [])

    def a_codigo_planta(self, codigo_fabricante: str) -> str | None:
        """"TX-T07" -> "F09"."""
        self._cargar()
        return self._fabricante_a_f.get(codigo_fabricante.strip().upper())
