import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

from sistema.integraciones.planta_http import PlantaHTTP

_ARTICULOS = {"el", "la", "los", "las", "un", "una"}


def _normalizar(texto: str) -> str:
    texto = texto.strip().lower()
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def _palabras(texto: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _normalizar(texto))


def _sin_articulo_inicial(palabras: list[str]) -> list[str]:
    i = 0
    while i < len(palabras) and palabras[i] in _ARTICULOS:
        i += 1
    return palabras[i:]


def _contiene(secuencia: list[str], buscada: list[str]) -> bool:
    """`buscada` aparece como palabras contiguas dentro de `secuencia` (no como substring de
    caracteres: "la 1" no tiene que matchear dentro de "la 10")."""
    n = len(buscada)
    return n > 0 and any(secuencia[i : i + n] == buscada for i in range(len(secuencia) - n + 1))


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
        #
        # Comparación por palabras y sin el artículo inicial (hallazgo H4 de la Fase 15, corrida
        # v6 del corrector): el LLM extrae "pulmón" a secas y la planta lo lista como "el
        # pulmón" — con substring de caracteres, "el pulmon" nunca estaba dentro de "pulmon".
        # Además se acepta que lo dicho sea el **final** de un nombre ("B" → "el filtro B",
        # "medio" → "la del medio"): el operador dice "el filtro B" y el LLM a veces extrae solo
        # la letra. Ese match parcial pesa por lo dicho, así que un nombre completo le gana.
        dicho_palabras = _palabras(dicho)
        dicho_nucleo = _sin_articulo_inicial(dicho_palabras)
        con_longitud: list[tuple[dict, int]] = []
        for equipo in equipos:
            mejor = 0
            for nombre in equipo.get("nombres", []):
                nucleo = _sin_articulo_inicial(_palabras(nombre))
                if _contiene(dicho_palabras, nucleo):
                    mejor = max(mejor, len(" ".join(nucleo)))
                elif dicho_nucleo and len(dicho_nucleo) < len(nucleo) and nucleo[-len(dicho_nucleo):] == dicho_nucleo:
                    mejor = max(mejor, len(" ".join(dicho_nucleo)))
            tipo = _palabras(equipo.get("tipo", ""))
            if _contiene(dicho_palabras, tipo):
                mejor = max(mejor, len(" ".join(tipo)))
            if mejor:
                con_longitud.append((equipo, mejor))

        if con_longitud:
            maximo = max(longitud for _, longitud in con_longitud)
            mejores = [equipo for equipo, longitud in con_longitud if longitud == maximo]
            if len(mejores) == 1:
                return Resolucion(ResultadoResolucion.resuelto, tag=mejores[0]["tag"])
            return Resolucion(ResultadoResolucion.ambiguo, candidatos=[e["tag"] for e in mejores])

        return Resolucion(ResultadoResolucion.no_encontrado)

    def tags(self) -> list[str]:
        return [e["tag"] for e in self._cargar_equipos()]

    def equipos_mencionados(self, texto: str) -> list[str]:
        """Equipos nombrados en un texto libre (la transcripción entera), por tag o por alguno de
        sus nombres informales completos (no por tipo genérico ni por recortes). Lote 3, H13: para
        saber de qué equipo es una pieza ("pierde por la empaquetadura") cuando el LLM la sacó como
        fila suelta."""
        palabras = _palabras(texto)
        mencionados = []
        for equipo in self._cargar_equipos():
            nombres = [equipo["tag"], *equipo.get("nombres", [])]
            if any(_contiene(palabras, self._nucleo_para_texto_libre(n)) for n in nombres):
                mencionados.append(equipo["tag"])
        return mencionados

    @staticmethod
    def _nucleo_para_texto_libre(nombre: str) -> list[str]:
        """Sin artículo, salvo que lo que quede sea solo un número: "la 1" tiene que aparecer
        completo, si no cualquier "1" de una lectura ("me dio 1.1") nombraría a B-101."""
        palabras = _palabras(nombre)
        nucleo = _sin_articulo_inicial(palabras)
        return palabras if all(p.isdigit() for p in nucleo) else nucleo

    def equipo_por_tag(self, tag: str) -> dict | None:
        for equipo in self._cargar_equipos():
            if equipo["tag"] == tag:
                return equipo
        return None
