"""Tabla de latencias de una corrida del corrector (Lote 6, D1).

Lee las líneas `latencia nota=...` que el agente escribe al terminar cada nota
(`agente.py::_procesar_nota`) en los `.log` de `academia2026-tp/correccion/corridas/<corrida>/`, y
muestra por escenario dónde se fue el tiempo: espera en la cola, Whisper, extracción, primer
mensaje a la tablet. Antes esto se reconstruía a mano cruzando timestamps del log.

    python -m evaluacion.latencias cpu4-lote6            # una corrida
    python -m evaluacion.latencias cpu4-lote6 otra -v    # varias, con el detalle por nota
"""

import argparse
import re
import statistics
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
CORRIDAS = RAIZ / "academia2026-tp" / "correccion" / "corridas"

_LINEA = re.compile(r"latencia nota=(?P<nota>\S+) (?P<campos>.*)")
_CAMPO = re.compile(r"(\w+)=(\S+)")
_ACUSE = r"acuse: la nota todavía no tuvo respuesta"
_CORTE = r"no terminó en \d+ s desde que arrancó"
_SUGERENCIA_CORTADA = r"sugerencia cortada por una nota"


def _p(valores: list[float], q: float) -> float:
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(round(q * (len(ordenados) - 1))))]


def leer(log: Path) -> list[dict]:
    notas = []
    for linea in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _LINEA.search(linea)
        if not m:
            continue
        fila = {"nota": m["nota"]}
        for clave, valor in _CAMPO.findall(m["campos"]):
            try:
                fila[clave] = float(valor)
            except ValueError:
                fila[clave] = None
        notas.append(fila)
    return notas


def contar(log: Path, patron: str) -> int:
    return len(re.findall(patron, log.read_text(encoding="utf-8", errors="replace")))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("corridas", nargs="+")
    ap.add_argument("-v", "--detalle", action="store_true")
    args = ap.parse_args()

    encabezado = (f"{'escenario':<18} {'notas':>5} {'whisper med/p90':>15} {'cola p90':>8} {'cola>30':>7} "
                  f"{'1er msj p90':>11} {'total>90':>8} {'acuses':>6} {'cortes':>6} {'sug.cortadas':>12}")
    for corrida in args.corridas:
        carpeta = CORRIDAS / corrida
        print(f"\n== {corrida}")
        print(encabezado)
        todas: list[dict] = []
        for log in sorted(carpeta.glob("*.log")):
            notas = leer(log)
            if not notas and not contar(log, r"sistema/estado"):
                continue
            todas.extend(notas)
            whisper = [n["whisper"] for n in notas if n.get("whisper")]
            cola = [n.get("cola") or 0.0 for n in notas]
            primero = [n.get("primer_mensaje") or 0.0 for n in notas]
            print(f"{log.stem:<18} {len(notas):>5} "
                  f"{statistics.median(whisper) if whisper else 0:>7.1f}/{_p(whisper, 0.9):<7.1f} "
                  f"{_p(cola, 0.9):>8.1f} {sum(c > 30 for c in cola):>7} {_p(primero, 0.9):>11.1f} "
                  f"{sum((n.get('total') or 0) > 90 for n in notas):>8} "
                  f"{contar(log, _ACUSE):>6} "
                  f"{contar(log, _CORTE):>6} "
                  f"{contar(log, _SUGERENCIA_CORTADA):>12}")
            if args.detalle:
                for n in notas:
                    print("   ", n)
        whisper = [n["whisper"] for n in todas if n.get("whisper")]
        if whisper:
            print(f"{'TOTAL':<18} {len(todas):>5} {statistics.median(whisper):>7.1f}/{_p(whisper, 0.9):<7.1f} "
                  f"{_p([n.get('cola') or 0 for n in todas], 0.9):>8.1f} "
                  f"{sum((n.get('cola') or 0) > 30 for n in todas):>7} "
                  f"{_p([n.get('primer_mensaje') or 0 for n in todas], 0.9):>11.1f} "
                  f"{sum((n.get('total') or 0) > 90 for n in todas):>8}")


if __name__ == "__main__":
    main()
