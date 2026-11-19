"""Parser determinístico de notas de recorrida (texto transcripto -> filas del parte).

Es la primera pasada, barata y offline (corre en la PC de planta sin GPU en milisegundos). Reconoce las
frases típicas del operador; si queda texto que no entendió, `requiere_llm` es True y conviene pasar la
nota por el extractor con structured output (`extraccion.py`). Las dos salidas se validan igual.

Reglas que aplica (las mismas que espera la corrección):
- una fila por equipo y variable, aunque la nota mezcle equipos en cualquier orden;
- el sujeto se arrastra: "la dos está en ..., el rodamiento me dio ..." -> ambas filas son de la B-102;
- unidades: lo que se dijo, y si no se dijo, la del manómetro del equipo (B-101 kg/cm2, B-102 psi, B-103 bar);
- "la bomba" / "el filtro" a secas -> ambigüedad de equipo (hay que preguntar);
- diferencial dicho "dieciocho" en vez de "uno ocho" -> ambigüedad de valor (hay que preguntar);
- fotos: se asocian a la fila por la categoría de la URL (/media/fotos/empaquetadura/02.jpg).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .catalogo import Catalogo
from .nombres import Mencion, Resolutor
from .numeros import leer_numero, normalizar
from .seguridad import detectar
from .unidades import normalizar_unidad

OBS_NORMALES = {"parada, en reserva", "parada por mantenimiento"}
_NUM = r"(?P<n>[a-z0-9][a-z0-9 ,\.]*?)"

PATRONES = [
    ("presion_descarga_local",
     re.compile(r"(?:esta en|en|marca|tiene) " + _NUM + r"\s*(?P<u>kilogramos|kilos|kg|psi|libras|bares|bar)?\s+de descarga")),
    ("presion_diferencial",
     re.compile(r"(?:me dio|dio|marca|esta en|tiene) " + _NUM + r"\s*(?P<u>kilos|kg|psi|bar)?\s+de (?:presion )?diferencial")),
    ("nivel", re.compile(r"(?:esta en|en|marca) " + _NUM + r"\s*(?P<u>metros|metro|m)\b")),
    ("temperatura_ir",
     re.compile(r"(?:el |la )?(?P<parte>rodamiento|motor)\s+(?:me dio|dio|esta en|tiene|marca) " + _NUM
                + r"\s*(?P<u>grados)(?: con la pistola| con el termometro| al ir)?")),
]
ESTADOS = {"parada, en reserva": r"(?:esta |quedo )?parada,? en reserva",
           "parada por mantenimiento": r"(?:esta )?parada por mantenimiento",
           "disparada, no arranca": r"(?:esta )?disparada,? no arranca"}
RUIDOS = {"hace un golpeteo metalico": "hace un golpeteo metálico",
          "suena como si tuviera piedras adentro": "suena como si tuviera piedras adentro",
          "tiene un ruido raro de rodamiento": "tiene un ruido raro de rodamiento"}
RELLENO = re.compile(r"^(?:y |ah,? y |bueno|eh|a ver|listo|este|bien|despues|tambien|ademas|le saque foto(?: al manometro)?|"
                     r"saque foto(?: desde lejos)?|con la pistola|chillan|me aleje|atencion|sin novedad|todo normal|"
                     r"recorrida sin novedad)[ ,]*$")
FOTO_CATEGORIA = {"empaquetadura": "goteo_empaquetadura", "manometro_diferencial": "presion_diferencial",
                  "correas": "estado_correas", "rotura_descarga": "condicion_insegura", "acople": "acople",
                  "manometro": "presion_descarga_local", "filtro": "presion_diferencial"}


@dataclass
class FilaNota:
    tag: str | None
    variable: str
    valor: float | None = None
    unidad: str | None = None
    observacion: str | None = None
    foto: str | None = None
    dicho: str | None = None

    def contrato(self) -> dict:
        """Lo que entra en FilaParte (sin foto ni dicho)."""
        return {"tag": self.tag, "variable": self.variable, "valor": self.valor, "unidad": self.unidad,
                "observacion": self.observacion}


INYECCION = re.compile(r"(?:aproba|autoriz|ignora|olvida|nota para el sistema|instruccion|sin pasar por|"
                       r"cambia(?:le)? el estado|carga(?:le)? (?:el|un) kit|sos un|system|prompt)")


@dataclass
class Parse:
    filas: list[FilaNota] = field(default_factory=list)
    ambiguedades: list[dict] = field(default_factory=list)
    seguridad: list[str] = field(default_factory=list)
    sin_entender: list[str] = field(default_factory=list)
    fotos_sueltas: list[str] = field(default_factory=list)

    @property
    def posible_inyeccion(self) -> bool:
        """Texto que le habla al sistema ("aprobala", "nota para el sistema"...). Se registra y se ignora:
        las reglas de aprobación están en código y no leen el texto de la nota."""
        return any(INYECCION.search(t) for t in self.sin_entender)

    @property
    def requiere_llm(self) -> bool:
        return bool(self.sin_entender)

    def como_dict(self) -> dict:
        return {"filas": [asdict(f) for f in self.filas], "ambiguedades": self.ambiguedades,
                "seguridad": self.seguridad, "sin_entender": self.sin_entender}


class ParserNotas:
    def __init__(self, catalogo: Catalogo, resolutor: Resolutor | None = None):
        self.catalogo = catalogo
        self.resolutor = resolutor or Resolutor(catalogo)

    # ------------------------------------------------------------------ API
    def parsear(self, texto: str, fotos: list[str] | None = None, reserva_actual: str | None = None) -> Parse:
        p = Parse()
        t = normalizar(texto)
        p.seguridad = [a.codigo for a in detectar(texto)]
        menciones = self.resolutor.menciones(t, reserva_actual)
        for seg, m in self._segmentos(t, menciones):
            self._segmento(seg, m, p)
        if p.seguridad and not any(f.variable == "condicion_insegura" for f in p.filas):
            tag = next((m.tag for m in menciones if m.tag), None)
            p.filas.append(FilaNota(tag or "PLANTA", "condicion_insegura", observacion=texto.strip()))
            p.sin_entender = []          # una nota insegura no espera más interpretación
        self._asociar_fotos(p, fotos or [])
        return p

    # ------------------------------------------------------------------ internos
    @staticmethod
    def _segmentos(t: str, menciones: list[Mencion]):
        """Corta el texto en tramos, uno por mención de equipo; el tramo hereda el equipo hasta la siguiente."""
        if not menciones:
            yield t, None
            return
        if menciones[0].inicio > 0:
            yield t[:menciones[0].inicio], None
        for i, m in enumerate(menciones):
            fin = menciones[i + 1].inicio if i + 1 < len(menciones) else len(t)
            yield t[m.fin:fin], m

    def _segmento(self, seg: str, m: Mencion | None, p: Parse) -> None:
        tag = m.tag if m else None
        explicado: list[tuple[int, int]] = []

        if m and m.tag is None:
            p.ambiguedades.append({"tipo": "equipo", "tag": None, "dicho": m.dicho.strip(),
                                   "tipo_equipo": m.tipo_generico})

        def fila(variable, valor=None, unidad=None, obs=None, dicho=None, span=None):
            if span:
                explicado.append(span)
            p.filas.append(FilaNota(tag, variable, valor, unidad, obs, dicho=dicho))

        for variable, rx in PATRONES:
            for mm in rx.finditer(seg):
                num = leer_numero(mm.group("n"))
                if num is None:
                    continue
                unidad = normalizar_unidad(mm.groupdict().get("u"))
                var = variable
                if variable == "presion_descarga_local":
                    unidad = unidad or (self.catalogo.unidad_manometro(tag) if tag else None)
                elif variable == "presion_diferencial":
                    unidad = "kg/cm2"
                    if num.forma in ("entero", "digitos") and num.valor >= 3:
                        # "dieciocho" de diferencial: ¿quiso decir uno ocho? Se propone 1,8 y se pregunta.
                        p.ambiguedades.append({"tipo": "valor", "tag": tag, "dicho": num.valor,
                                               "propuesto": round(num.valor / 10, 1)})
                        num.valor = round(num.valor / 10, 1)
                elif variable == "nivel":
                    unidad = "m"
                elif variable == "temperatura_ir":
                    var = "temperatura_rodamiento_ir" if mm.group("parte") == "rodamiento" else "temperatura_motor_ir"
                    unidad = "degC"
                obs = None
                if var == "presion_diferencial" and num.valor >= 1.5:
                    obs = "diferencial alto"
                if var == "temperatura_rodamiento_ir" and num.valor > 75:
                    obs = "rodamiento caliente"
                fila(var, float(num.valor), unidad, obs, mm.group(0), mm.span())

        # Observaciones sin número
        for obs, rx in ESTADOS.items():
            if mm := re.search(rx, seg):
                fila("estado", obs=obs, dicho=mm.group(0), span=mm.span())
        if mm := re.search(r"(pierde bastante|chorrea|pierde mucho)[^,.]*empaquetadura", seg):
            fila("goteo_empaquetadura", obs="pérdida importante por empaquetadura", dicho=mm.group(0), span=mm.span())
        elif mm := re.search(r"(?:sigue )?(gotea|goteando|pierde)[^,.]*empaquetadura", seg):
            fila("goteo_empaquetadura", obs="goteo por empaquetadura", dicho=mm.group(0), span=mm.span())
        if mm := re.search(r"visor de aceite (?:esta )?bajo|casi no tiene aceite(?: en el visor)?|le falta aceite", seg):
            fila("nivel_aceite_visor", obs="nivel de aceite bajo", dicho=mm.group(0), span=mm.span())
        if mm := re.search(r"(?:las )?correas (?:estan|estan muy) (?P<e>[a-z ]+?)(?=,|\.|$| y )", seg):
            fila("estado_correas", obs=f"correas {mm.group('e').strip()}", dicho=mm.group(0), span=mm.span())
        for dicho_n, obs in RUIDOS.items():
            if mm := re.search(dicho_n, seg):
                fila("ruido", obs=obs, dicho=mm.group(0), span=mm.span())
        if mm := re.search(r"esta rebalsando(?: a la pileta)?", seg):
            nivel = next((f for f in p.filas if f.tag == tag and f.variable == "nivel"), None)
            if nivel:
                nivel.observacion = "rebalse a pileta"
            explicado.append(mm.span())

        # ¿Quedó algo sin explicar?
        resto = list(seg)
        for a, b in explicado:
            resto[a:b] = [" "] * (b - a)
        for trozo in re.split(r"[.,;!¡?¿]", "".join(resto)):
            trozo = re.sub(r"\s+", " ", trozo).strip()
            if trozo and not RELLENO.match(trozo) and not re.fullmatch(r"(?:en|la|el|y|de|del|ah)", trozo) \
                    and len(trozo) > 3:
                p.sin_entender.append(trozo)

    @staticmethod
    def _asociar_fotos(p: Parse, fotos: list[str]) -> None:
        for url in fotos:
            m = re.search(r"/fotos/([a-z_]+)/", url)
            variable = FOTO_CATEGORIA.get(m.group(1)) if m else None
            candidata = next((f for f in p.filas if f.variable == variable and f.foto is None
                              and (variable != "presion_diferencial" or (f.valor or 0) >= 1.5)), None) \
                or next((f for f in p.filas if f.variable == variable and f.foto is None), None)
            if candidata:
                candidata.foto = url
            else:
                p.fotos_sueltas.append(url)
