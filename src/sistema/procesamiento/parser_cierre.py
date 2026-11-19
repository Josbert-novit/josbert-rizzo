"""Parser determinístico de la nota de cierre (G): mismo criterio que el parte por voz (Lote 5,
"parser primero, LLM de respaldo").

Medido en la corrida `g1-tarea-estandar`: el LLM local (qwen2.5:1.5b) inventaba tareas y
repuestos que el técnico no dijo, copiándolos de los ejemplos del prompt (T-B-ROD, I-VIB, "C48"),
y tardaba más de 90 s compitiendo por la CPU con las notas. El técnico habla con frases fijas
("Hice cambio de cartuchos. Usé cuarenta y ocho cartucho filtrante 25 µm. Quedó en servicio…
Fueron tres con dos horas."), pero lo que llega es la transcripción de Whisper, que las deforma
(corrida `g1b-parser-cierre`, audio real):

    "Tiefro la orden … y se cambió de Cartuchos C48 Cartucho fitrante 25 micrones quedó en
     servicio, el diferencial está normal, fueron 2,8 horas"

Por eso no se depende de "Hice"/"Usé" ni de la puntuación: se buscan en TODO el texto los nombres
del catálogo propio (tareas y repuestos de `materiales.yaml`) con comparación aproximada por
palabra ("fitrante" ≈ "filtrante"), los números del nombre tienen que coincidir exacto ("25" no es
"10"), y la cantidad se lee justo antes del nombre ("cuarenta y ocho", "48", "un" o "C48"). Si no se
reconoce ninguna tarea, `agente._procesar_cierre` cae al LLM como antes.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from planta_contrato import Repuesto

from planta_kit.numeros import PALABRAS_NUMERO, leer_numero, normalizar
from sistema.integraciones import materiales
from sistema.llm.cierre import CierreExtraido

UMBRAL_COINCIDENCIA = 0.75
SIMILITUD_PALABRA = 0.8
_PALABRA = re.compile(r"[a-z0-9]+")
_VACIAS = {"de", "del", "la", "el", "los", "las", "y", "a", "en", "con", "un", "una", "tx", "m", "um", "mm",
           "micrones", "micras", "tamano"}


def _todas(texto: str) -> list[str]:
    return _PALABRA.findall(normalizar(texto))


def _significativas(texto: str) -> list[str]:
    return [t for t in _todas(texto) if t not in _VACIAS]


def _parecida(buscada: str, palabra: str) -> bool:
    if buscada.isdigit() or palabra.isdigit() or len(buscada) < 4:
        return buscada == palabra
    return buscada == palabra or SequenceMatcher(None, buscada, palabra).ratio() >= SIMILITUD_PALABRA


def _puntaje(nombre: list[str], ventana: list[str]) -> float:
    """Fracción de las palabras del nombre que aparecen (aproximadas) en la ventana. Los números
    del nombre son obligatorios: sin ellos el puntaje es 0."""
    if not nombre:
        return 0.0
    presentes = [any(_parecida(b, p) for p in ventana) for b in nombre]
    if any(not ok for b, ok in zip(nombre, presentes) if b.isdigit()):
        return 0.0
    return sum(presentes) / len(nombre)


def _nombre_catalogo(nombre: str) -> str:
    return nombre.split(" (")[0]


def _tareas(texto: str) -> list[str]:
    palabras = _significativas(texto)
    vistos: dict[str, float] = {}
    for t in materiales.tareas_estandar() + materiales.tareas_no_estandar():
        puntaje = _puntaje(_significativas(t["nombre"]), palabras)
        if puntaje >= UMBRAL_COINCIDENCIA:
            vistos[t["codigo"]] = max(puntaje, vistos.get(t["codigo"], 0))
    if not vistos:
        return []
    mejor = max(vistos.values())
    return [c for c, p in vistos.items() if p == mejor]


def _cantidad_antes(palabras: list[str], inicio: int) -> float | None:
    """La cantidad dicha justo antes del nombre: "cuarenta y ocho", "48", "un", o lo que Whisper
    escribe de "cuarenta y ocho" ("C48")."""
    if inicio == 0:
        return None
    anterior = palabras[inicio - 1]
    if anterior in PALABRAS_NUMERO and anterior not in {"y", "coma", "con", "punto", "medio"}:
        k = inicio - 1
        while k > 0 and palabras[k - 1] in PALABRAS_NUMERO:
            k -= 1
        numero = leer_numero(" ".join(palabras[k:inicio]))
        return numero.valor if numero else None
    # "48", o "C48"/"C48K" (Whisper de "cuarenta y ocho"); nunca el "350" de "TX-350".
    if inicio >= 2 and palabras[inicio - 2] == "tx":
        return None
    m = re.search(r"\d+", anterior)
    return float(m.group(0)) if m else None


def _repuestos(texto: str, preferidos: list[str]) -> list[Repuesto]:
    palabras = _todas(texto)
    encontrados: list[tuple[int, Repuesto]] = []
    usados: set[int] = set()
    candidatos = sorted(materiales.repuestos(), key=lambda r: r["codigo"] not in preferidos)
    for r in candidatos:
        nombre = _significativas(_nombre_catalogo(r["nombre"]))
        largo = len(_todas(_nombre_catalogo(r["nombre"]))) + 2
        coincidencias = []
        for i in range(len(palabras)):
            # El nombre empieza exactamente donde empieza la ventana: la cantidad queda antes.
            if i in usados or not _parecida(nombre[0], palabras[i]):
                continue
            ventana = [p for p in palabras[i:i + largo] if p not in _VACIAS]
            puntaje = _puntaje(nombre, ventana)
            if puntaje >= UMBRAL_COINCIDENCIA:
                cantidad = _cantidad_antes(palabras, i)
                coincidencias.append((puntaje, cantidad is not None, -i, cantidad))
        if not coincidencias:
            continue
        _, _, menos_i, cantidad = max(coincidencias)
        if cantidad:
            i = -menos_i
            usados.update(range(i, i + largo - 2))
            encontrados.append((i, Repuesto(codigo=r["codigo"], cantidad=cantidad)))
    return [rep for _, rep in sorted(encontrados, key=lambda x: x[0])]


_HORAS = re.compile(r"\b(?:fueron|trabaje|tarde)\s+(.{1,40}?)\s+horas?\b")
_PRUEBA = re.compile(r"\b(prob[eé]\b[^.]*|qued[oó] en servicio(?:,[^,.]*)?)", re.IGNORECASE)


def _horas(texto: str) -> float | None:
    m = _HORAS.search(normalizar(texto))
    if not m:
        return None
    dicho = re.sub(r"(\d+)\s*(?:con|coma)\s*(\d+)", r"\1.\2", m.group(1))
    numero = leer_numero(dicho)
    return numero.valor if numero else None


def parsear_cierre(texto: str, repuestos_preferidos: list[str] | None = None) -> CierreExtraido | None:
    """`None` si no se reconoce ninguna tarea (que decida el LLM). `repuestos_preferidos`: los de
    la orden, para desempatar nombres parecidos."""
    tareas = _tareas(texto or "")
    if not tareas:
        return None
    m = _PRUEBA.search(texto)
    return CierreExtraido(
        tareas=tareas,
        repuestos=_repuestos(texto, repuestos_preferidos or []),
        horas=_horas(texto),
        resultado_prueba=m.group(1).strip() if m else None,
    )
