"""Evaluación del parte por voz (4.3): compara los partes que armó el agente contra
`GET /api/verdad` de la planta (el parte esperado de cada nota, "cerrado": nuestro sistema no
puede depender de él en producción, solo esta suite de evaluación lo consulta).

Mide:
- Aciertos por campo (tag, variable, valor) sobre los ítems esperados de cada nota ya procesada.
- Si el sistema preguntó por ambigüedad de equipo **solo cuando correspondía**: ni de más
  (pregunta sobre una nota sin ambigüedad real) ni de menos (no detectó una ambigüedad real).

Fuentes:
- `GET {HISTORIAN_URL}/api/verdad`: el parte esperado de cada nota.
- `GET {CENTRAL_URL}/api/partes`: los partes que arma nuestro agente (ver
  `src/sistema/interfaces/central.py`), uno por nota ya procesada en esta corrida. Una nota de
  `verdad` que el agente todavía no procesó (no estaba arriba, o la recorrida no llegó a tocarla)
  simplemente no entra en la muestra: esto evalúa precisión sobre lo que el agente efectivamente
  vio, no cobertura temporal.

Uso (con la planta y el agente ya levantados):

    .venv/Scripts/python evaluacion/evaluar_parte.py
"""

import os
import unicodedata
from dataclasses import dataclass

import httpx

HISTORIAN_URL = os.getenv("HISTORIAN_URL", "http://localhost:8100")
CENTRAL_URL = os.getenv("CENTRAL_URL", "http://localhost:8200")

TOLERANCIA_VALOR = 0.5  # margen absoluto: redondeos y conversión de unidades no cuentan como error

# Normaliza tanto la variable canónica de `verdad` (p. ej. "presion_descarga_local") como la
# variable libre que extrajo el LLM (p. ej. "presión de descarga") a una clave común, por
# palabra clave — extiende el criterio de `procesamiento/variables.py` a las lecturas puramente
# locales (sin sensor conectado), que acá también hace falta comparar.
_PALABRAS_CLAVE: list[tuple[str, str]] = [
    ("diferencial", "diferencial"),
    ("descarga", "descarga"),
    ("succion", "succion"),
    ("aspiracion", "succion"),
    ("vibracion", "vibracion"),
    ("rodamiento", "temp_rodamiento"),
    ("motor", "temp_motor"),
    ("aceite", "temp_aceite"),
    ("carter", "temp_aceite"),
    ("goteo", "goteo"),
    ("empaquetadura", "goteo"),
    ("caudal", "caudal"),
    ("nivel", "nivel"),
    ("correa", "correas"),
]


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sin_tildes.lower()


def _clave_variable(variable: str) -> str | None:
    normalizada = _normalizar(variable)
    for palabra, clave in _PALABRAS_CLAVE:
        if palabra in normalizada:
            return clave
    return None


def _misma_variable(variable_fila: str | None, variable_verdad: str) -> bool:
    if variable_fila is None:
        return False
    return _clave_variable(variable_fila) == _clave_variable(variable_verdad)


def _valor_cercano(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a == b
    return abs(a - b) <= TOLERANCIA_VALOR


def _mejor_fila(item: dict, filas: list[dict]) -> dict | None:
    candidatas = [f for f in filas if f.get("tag") == item["tag"]]
    if not candidatas:
        return None
    exacta = [f for f in candidatas if _misma_variable(f.get("variable"), item["variable"])]
    return exacta[0] if exacta else candidatas[0]


@dataclass
class Resultado:
    notas_evaluadas: int = 0
    items_verdad: int = 0
    tag_correcto: int = 0
    variable_correcta: int = 0
    valor_correcto: int = 0
    notas_con_ambiguedad_real: int = 0
    pregunto_cuando_debia: int = 0  # verdadero positivo
    no_pregunto_debiendo: int = 0  # falso negativo (más grave: dejó pasar una ambigüedad real)
    pregunto_sin_necesidad: int = 0  # falso positivo (preguntó de más)

    def reporte(self) -> str:
        def pct(n: int, total: int) -> str:
            return f"{100 * n / total:.0f}%" if total else "s/d"

        return (
            f"Notas evaluadas: {self.notas_evaluadas}\n"
            f"Ítems esperados: {self.items_verdad}\n"
            f"  tag correcto:      {pct(self.tag_correcto, self.items_verdad)} "
            f"({self.tag_correcto}/{self.items_verdad})\n"
            f"  variable correcta: {pct(self.variable_correcta, self.items_verdad)} "
            f"({self.variable_correcta}/{self.items_verdad})\n"
            f"  valor correcto:    {pct(self.valor_correcto, self.items_verdad)} "
            f"({self.valor_correcto}/{self.items_verdad})\n"
            f"Ambigüedad de equipo — notas con ambigüedad real: {self.notas_con_ambiguedad_real}\n"
            f"  preguntó cuando correspondía (TP): {self.pregunto_cuando_debia}\n"
            f"  no preguntó debiendo (FN):         {self.no_pregunto_debiendo}\n"
            f"  preguntó de más (FP):              {self.pregunto_sin_necesidad}"
        )


def evaluar(verdad: list[dict], partes_por_nota: dict[str, dict]) -> Resultado:
    """`verdad` (`GET /api/verdad`) mezcla el parte esperado de cada nota (lo que evalúa esta
    función) con entradas de diálogo (`tipo: "dialogo"`, sin `nota_id`) que registran cada
    intercambio por la tablet — esas son otra evaluación posible (calidad del diálogo) y no se
    usan acá."""
    r = Resultado()
    for nota in verdad:
        if "nota_id" not in nota:
            continue
        parte = partes_por_nota.get(nota["nota_id"])
        if parte is None:
            continue
        r.notas_evaluadas += 1
        filas = parte.get("filas", [])

        for item in nota.get("items") or []:
            r.items_verdad += 1
            fila = _mejor_fila(item, filas)
            if fila is None:
                continue
            if fila.get("tag") == item["tag"]:
                r.tag_correcto += 1
            if _misma_variable(fila.get("variable"), item["variable"]):
                r.variable_correcta += 1
            if _valor_cercano(fila.get("valor"), item["valor"]):
                r.valor_correcto += 1

        tiene_ambiguedad_real = bool(nota.get("ambiguedades"))
        pregunto_por_equipo = any(f.get("tag") is None or f.get("ambiguo") for f in filas)
        if tiene_ambiguedad_real:
            r.notas_con_ambiguedad_real += 1
            if pregunto_por_equipo:
                r.pregunto_cuando_debia += 1
            else:
                r.no_pregunto_debiendo += 1
        elif pregunto_por_equipo:
            r.pregunto_sin_necesidad += 1
    return r


def main() -> None:
    verdad = httpx.get(f"{HISTORIAN_URL}/api/verdad", timeout=10).raise_for_status().json()
    partes = httpx.get(f"{CENTRAL_URL}/api/partes", timeout=10).raise_for_status().json()
    partes_por_nota = {p["nota_ids"][0]: p for p in partes if p.get("nota_ids")}

    resultado = evaluar(verdad, partes_por_nota)
    print(resultado.reporte())


if __name__ == "__main__":
    main()
