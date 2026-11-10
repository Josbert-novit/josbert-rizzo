import unicodedata
from dataclasses import dataclass, field
from enum import Enum

from sistema.integraciones.planta_http import PlantaHTTP


def _normalizar(texto: str) -> str:
    texto = texto.strip().lower()
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


class ResultadoResolucion(str, Enum):
    resuelto = "resuelto"
    ambiguo = "ambiguo"
    no_encontrado = "no_encontrado"


@dataclass
class Resolucion:
    resultado: ResultadoResolucion
    tag: str | None = None
    candidatos: list[str] = field(default_factory=list)


class ResolverNombres:
    """Resuelve el nombre informal de un equipo ("la dos", "el de la izquierda", "la de reserva")
    contra el listado de GET /api/planta. Si el nombre es genérico (p. ej. "la bomba" a secas) y
    hay más de un equipo del mismo tipo, queda ambiguo: hay que preguntarle al operador."""

    def __init__(self, http: PlantaHTTP) -> None:
        self._http = http
        self._equipos: list[dict] | None = None

    def _cargar_equipos(self) -> list[dict]:
        if self._equipos is None:
            self._equipos = self._http.equipos()["equipos"]
        return self._equipos

    def resolver(self, dicho: str) -> Resolucion:
        equipos = self._cargar_equipos()
        normalizado = _normalizar(dicho)

        for equipo in equipos:
            if _normalizar(equipo["tag"]) == normalizado:
                return Resolucion(ResultadoResolucion.resuelto, tag=equipo["tag"])

        # El operador no siempre repite el nombre tal cual: contesta con frases como "la correcta
        # es el filtro A." o "era el de la izquierda" (con relleno antes/después y puntuación). En
        # vez de pedir igualdad exacta, se busca el nombre (o el tipo, como última opción) más
        # específico que aparezca dentro de la frase: el más largo que matchee gana. Así "el
        # filtro A" (específico) le gana a "filtro" (genérico) aunque ambos aparezcan en la misma
        # respuesta, y se puede desambiguar entre equipos del mismo tipo. (Ver BUGS.md #15: antes
        # esto exigía coincidencia exacta y quedaba en loop infinito preguntando lo mismo.)
        con_longitud: list[tuple[dict, int]] = []
        for equipo in equipos:
            mejor = 0
            for nombre in equipo.get("nombres", []):
                candidato = _normalizar(nombre)
                if candidato and candidato in normalizado:
                    mejor = max(mejor, len(candidato))
            tipo = _normalizar(equipo.get("tipo", ""))
            if tipo and tipo in normalizado:
                mejor = max(mejor, len(tipo))
            if mejor:
                con_longitud.append((equipo, mejor))

        if con_longitud:
            maximo = max(longitud for _, longitud in con_longitud)
            mejores = [equipo for equipo, longitud in con_longitud if longitud == maximo]
            if len(mejores) == 1:
                return Resolucion(ResultadoResolucion.resuelto, tag=mejores[0]["tag"])
            return Resolucion(ResultadoResolucion.ambiguo, candidatos=[e["tag"] for e in mejores])

        return Resolucion(ResultadoResolucion.no_encontrado)

    def equipo_por_tag(self, tag: str) -> dict | None:
        for equipo in self._cargar_equipos():
            if equipo["tag"] == tag:
                return equipo
        return None
