"""Evaluación de retrieval (5.3): recall@k/hit@k/mrr de nuestro buscador semántico contra la
suite de 71 preguntas de `evaluacion/retrieval.json`.

Cálculo propio, sin depender de `corpus.evaluar` de `academia2026-tp` — ese paquete no es
instalable (vive suelto en `planta/corpus`) y acoplarnos a un checkout hermano para evaluar sería
frágil: el corrector podría no tener ambos repos uno al lado del otro. La suite de preguntas sí se
trae por la API de la planta (`archivo_json`), igual que el resto del corpus (5.1).

Correr dentro del contenedor del agente (necesita red hacia `sim`, `postgres` y `ollama`):
    docker compose exec agente python -m evaluacion.evaluar_retrieval

Con `--oficial` se evalúa además con `corpus.evaluar.evaluar` de la planta (lo que pide el Anexo
de la Entrega 1) y se compara contra su línea de base BM25, calculada en el momento sobre los
mismos fragmentos. El paquete `corpus` se monta solo para esta corrida, no es dependencia del
sistema:
    docker compose run --rm -v ../academia2026-tp/planta:/academia/planta:ro agente         python -m evaluacion.evaluar_retrieval --oficial
"""

import logging
import os
import sys
from dataclasses import dataclass, field

from sistema.integraciones.planta_http import PlantaHTTP
from sistema.rag.buscar import Fragmento, buscar
from sistema.rag.embeddings import Embeddings

log = logging.getLogger(__name__)

KS = (1, 3, 5, 10)


@dataclass
class Resultado:
    n: int = 0
    hit: dict[int, int] = field(default_factory=lambda: {k: 0 for k in KS})
    recall: dict[int, float] = field(default_factory=lambda: {k: 0.0 for k in KS})
    rr: float = 0.0
    distractor_en_top5: int = 0

    def registrar(self, relevantes: set[str], distractores: set[str], recuperadas: list[str]) -> None:
        self.n += 1
        for k in KS:
            top_k = recuperadas[:k]
            if relevantes & set(top_k):
                self.hit[k] += 1
            if relevantes:
                self.recall[k] += len(relevantes & set(top_k)) / len(relevantes)
        for i, cita in enumerate(recuperadas, start=1):
            if cita in relevantes:
                self.rr += 1 / i
                break
        if distractores & set(recuperadas[:5]):
            self.distractor_en_top5 += 1

    def reporte(self) -> dict:
        if self.n == 0:
            return {"n": 0}
        return {
            "n": self.n,
            "hit@k": {k: round(self.hit[k] / self.n, 3) for k in KS},
            "recall@k": {k: round(self.recall[k] / self.n, 3) for k in KS},
            "mrr": round(self.rr / self.n, 3),
            "distractor_en_top5": self.distractor_en_top5,
        }


def evaluar(buscador, suite: list[dict]) -> dict:
    """`buscador(pregunta, k) -> list[str]` (citas en orden). Separado de `main()` para poder
    testear con un `buscador` falso, sin DB ni Ollama."""
    total = Resultado()
    por_tipo: dict[str, Resultado] = {}
    detalle = []

    for pregunta in suite:
        relevantes = set(pregunta.get("relevantes") or [])
        distractores = set(pregunta.get("distractores") or [])
        recuperadas = buscador(pregunta["pregunta"], max(KS))

        total.registrar(relevantes, distractores, recuperadas)
        tipo = pregunta.get("tipo", "sin_tipo")
        por_tipo.setdefault(tipo, Resultado()).registrar(relevantes, distractores, recuperadas)
        detalle.append({"id": pregunta["id"], "recuperadas": recuperadas, "relevantes": sorted(relevantes)})

    return {
        "total": total.reporte(),
        "por_tipo": {tipo: r.reporte() for tipo, r in por_tipo.items()},
        "preguntas": detalle,
    }


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    http = PlantaHTTP()
    embeddings = Embeddings()
    suite = http.archivo_json("evaluacion/retrieval.json")

    def buscador(pregunta: str, k: int) -> list[str]:
        return [f.cita for f in buscar(embeddings, pregunta, k=k)]

    resultado = evaluar(buscador, suite)
    t = resultado["total"]
    print(f"Preguntas evaluadas: {t['n']}")
    print(f"hit@k:    {t['hit@k']}")
    print(f"recall@k: {t['recall@k']}")
    print(f"mrr:      {t['mrr']}")
    print(f"Distractor (no vigente) en el top-5: {t['distractor_en_top5']}/{t['n']}")
    print("\nPor tipo de pregunta:")
    for tipo, r in resultado["por_tipo"].items():
        print(f"  {tipo}: n={r['n']} hit@5={r['hit@k'][5]} recall@5={r['recall@k'][5]}")

    if "--detalle" in sys.argv:
        print("\nDetalle:")
        for p in resultado["preguntas"]:
            print(f"  {p['id']}: relevantes={p['relevantes']} recuperadas(top5)={p['recuperadas'][:5]}")


def main_oficial() -> None:
    """Evaluación con el evaluador oficial de la planta (`corpus.evaluar.evaluar`) y comparación
    contra su BM25 sobre los mismos fragmentos. `distractor@k` es la proporción de preguntas en las
    que una sección que no debería (p. ej. la Rev. C) aparece antes que cualquier relevante."""
    sys.path.insert(0, os.getenv("CORPUS_DIR", "/academia/planta"))
    from corpus.evaluar import BM25, evaluar as evaluar_oficial  # noqa: PLC0415

    logging.basicConfig(level=logging.WARNING)
    http = PlantaHTTP()
    embeddings = Embeddings()
    suite = http.archivo_json("evaluacion/retrieval.json")
    fragmentos = http.fragmentos()

    def buscador(pregunta: str, k: int) -> list[str]:
        return [f.cita for f in buscar(embeddings, pregunta, k=k)]

    for nombre, funcion in (("Nuestro (pgvector)", buscador), ("BM25 (línea de base)", BM25(fragmentos).buscar)):
        r = evaluar_oficial(funcion, suite)
        t = r["total"]
        print(f"\n{nombre} · {r['n']} preguntas")
        for metrica in ("hit", "recall", "distractor"):
            print(f"  {metrica + '@k':14}" + "  ".join(f"{k}={t[f'{metrica}@{k}']}" for k in KS))
        print(f"  mrr           {t['mrr']}")
        for tipo, m in r["por_tipo"].items():
            print(f"    {tipo:14} n={m['n']:2}  hit@5={m['hit@5']}  recall@5={m['recall@5']}")


if __name__ == "__main__":
    if "--oficial" in sys.argv:
        main_oficial()
        sys.exit(0)
    main()
