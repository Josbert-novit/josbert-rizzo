"""Números dichos en campo -> float.

Cubre lo que dice el operador y lo que puede escribir el transcriptor:

    "ciento doce"                -> 112        "mil seiscientos cuarenta" -> 1640
    "uno ocho" / "uno coma ocho" -> 1.8        "uno con tres"             -> 1.3
    "uno y medio"                -> 1.5        "cero nueve"               -> 0.9
    "112" / "1.640" / "1,8"      -> 112 / 1640 / 1.8
    "cincuenta y un grados"      -> 51

`leer_numero` devuelve también la forma en que se dijo: sirve para detectar ambigüedades
("dieciocho" de diferencial cuando quiso decir "uno ocho").
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_UNI = {"cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
        "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14,
        "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
        "veintiun": 21, "veintiuno": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24,
        "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29}
_DEC = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80,
        "noventa": 90}
_CEN = {"cien": 100, "ciento": 100, "doscientos": 200, "trescientos": 300, "cuatrocientos": 400,
        "quinientos": 500, "seiscientos": 600, "setecientos": 700, "ochocientos": 800, "novecientos": 900}
PALABRAS_NUMERO = set(_UNI) | set(_DEC) | set(_CEN) | {"mil", "y", "coma", "con", "medio", "punto"}


def normalizar(texto: str) -> str:
    """minúsculas y sin tildes ("está" -> "esta"); conserva la ñ como n."""
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


@dataclass
class Numero:
    valor: float
    forma: str          # entero | coma | con | pegado | medio | digitos
    texto: str


def _grupos(tokens: list[str]) -> list[int]:
    """Agrupa palabras en números enteros. "uno ocho" son dos grupos; "ciento doce", uno."""
    grupos: list[int] = []
    total = cur = 0
    ultimo: str | None = None
    abierto = False

    def cerrar():
        nonlocal total, cur, ultimo, abierto
        if abierto:
            grupos.append(total + cur)
        total = cur = 0
        ultimo, abierto = None, False

    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "y":
            i += 1
            continue
        if t in _UNI:
            cat, v = "uni", _UNI[t]
        elif t in _DEC:
            cat, v = "dec", _DEC[t]
        elif t in _CEN:
            cat, v = "cen", _CEN[t]
        elif t == "mil":
            cat, v = "mil", 1000
        else:
            i += 1
            continue
        permitido = {None: {"uni", "dec", "cen", "mil"}, "cen": {"dec", "uni", "mil"}, "dec": {"uni", "mil"},
                     "uni": {"mil"}, "mil": {"cen", "dec", "uni"}}[ultimo]
        # "treinta y dos" sí, "treinta dos" no; "veinte" (uni) no admite otra unidad.
        if cat == "uni" and ultimo == "dec" and (i == 0 or tokens[i - 1] != "y"):
            permitido = set()
        if cat not in permitido:
            cerrar()
        if cat == "mil":
            total += (cur or 1) * 1000
            cur = 0
        else:
            cur += v
        ultimo, abierto = cat, True
        i += 1
    cerrar()
    return grupos


def _decimal(entero: int, dec: int) -> float:
    return entero + dec / (10 ** len(str(dec)))


_RE_DIGITOS = re.compile(r"\d+(?:[.,]\d+)?(?:\s+\d)?")


def leer_numero(texto: str) -> Numero | None:
    """Lee un número dicho en palabras o escrito en dígitos. None si no hay número."""
    t = normalizar(texto).strip()
    m = _RE_DIGITOS.search(t)
    if m:
        s = m.group(0)
        if " " in s:                                    # "1 8" -> 1.8 (transcripción de "uno ocho")
            a, b = s.split()
            return Numero(_decimal(int(a), int(b)), "pegado", s)
        if re.fullmatch(r"\d{1,3}\.\d{3}", s):          # "1.640" es mil seiscientos cuarenta
            return Numero(float(s.replace(".", "")), "digitos", s)
        return Numero(float(s.replace(",", ".")), "digitos", s)

    tokens = re.findall(r"[a-z]+", t)
    tokens = [x for x in tokens if x in PALABRAS_NUMERO]
    if not tokens or all(x in {"y", "coma", "con", "punto", "medio"} for x in tokens):
        return None
    for sep, forma in (("coma", "coma"), ("punto", "coma"), ("con", "con")):
        if sep in tokens:
            k = tokens.index(sep)
            iz, de = _grupos(tokens[:k]), _grupos(tokens[k + 1:])
            if iz and de:
                return Numero(_decimal(iz[0], de[0]), forma, texto)
    if tokens[-1] == "medio":
        g = _grupos(tokens[:-1])
        if g:
            return Numero(g[0] + 0.5, "medio", texto)
    g = _grupos(tokens)
    if not g:
        return None
    if len(g) >= 2 and g[0] < 10 and g[1] < 10:
        return Numero(_decimal(g[0], g[1]), "pegado", texto)
    return Numero(float(g[0]), "entero", texto)


def numero_a_palabras(n: int) -> str:
    """Inverso simple (para que el sistema le hable al operador como habla él)."""
    inv_uni = {v: k for k, v in _UNI.items() if k not in ("un", "una", "veintiun")}
    inv_dec = {v: k for k, v in _DEC.items()}
    inv_cen = {v: k for k, v in _CEN.items() if k != "cien"}
    if n < 30:
        return inv_uni[n]
    if n < 100:
        d, u = divmod(n, 10)
        return inv_dec[d * 10] + (f" y {inv_uni[u]}" if u else "")
    if n == 100:
        return "cien"
    if n < 1000:
        c, r = divmod(n, 100)
        return inv_cen[c * 100] + (f" {numero_a_palabras(r)}" if r else "")
    m, r = divmod(n, 1000)
    return ("mil" if m == 1 else f"{numero_a_palabras(m)} mil") + (f" {numero_a_palabras(r)}" if r else "")


def decir(valor: float) -> str:
    """112 -> 'ciento doce'; 1.8 -> 'uno coma ocho'."""
    if float(valor).is_integer():
        return numero_a_palabras(int(valor))
    entero = int(valor)
    dec = int(round((valor - entero) * 10))
    return f"{numero_a_palabras(entero)} coma {numero_a_palabras(dec)}"
