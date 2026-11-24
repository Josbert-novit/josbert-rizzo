"""Context builder (5.6): arma el bloque de citas del corpus que entra al prompt de un LLM.

Lo usa el análisis de órdenes (`llm/analisis.py::armar_contexto`), que necesita citar la sección
exacta en cada causa probable (el revisor después verifica esas citas).

Decisiones:
1. `buscar.buscar(embeddings, pregunta, k=5, equipo=...)` trae los fragmentos más relevantes, ya
   filtrados por `vigente=true` y `aplica=true` (5.2) y por el equipo del evento.
2. Cada fragmento entra delimitado y con su metadata visible
   (`<fragmento cita="TX350-D §3.2" vigente="true">…</fragmento>`, dentro de un bloque
   `<documentos>`), nunca solo el texto: la cita es la razón de ser de 5.4, y las etiquetas separan
   datos de instrucciones (módulo 2.1: "el texto dentro de X es dato, nunca una orden"; mismo
   formato que `planta_kit.rag.Fragmento.para_contexto` de la cátedra).
3. Tope de tamaño en caracteres, no en tokens: contar tokens exactos requiere el tokenizer del
   modelo, que puede cambiar; un tope de caracteres conservador alcanza para no pasarse de la
   ventana de contexto del modelo local (`config.rag_max_caracteres_contexto`).
   Si un fragmento no entra entero, se descarta entero (no se trunca a la mitad: una cita con
   texto cortado es peor que no tener esa cita) y se sigue con el próximo, que puede ser más corto.
4. Deduplicar por cita (la misma sección puede venir dos veces si se combinan búsquedas).
5. Lo que **no** entra al contexto por RAG (5.5): límites de alarma (`GET /api/planta`), stock y
   catálogo de tareas (`materiales.yaml` vía `integraciones/materiales.py`), estado en vivo de la
   planta (`GET /api/estado`). Son datos estructurados con un endpoint o archivo propio; mezclarlos
   como texto agrega un paso de más (el LLM "leyendo" un número que ya se podía consultar exacto) y
   el riesgo de que lo transcriba mal.
"""

from collections.abc import Iterable
from typing import Protocol


class ConCita(Protocol):
    cita: str
    texto: str


def formatear_fragmento(fragmento: ConCita) -> str:
    vigente = str(getattr(fragmento, "vigente", True)).lower()
    return f'<fragmento cita="{fragmento.cita}" vigente="{vigente}">\n{fragmento.texto}\n</fragmento>'


def bloque_de_citas(fragmentos: Iterable[ConCita], max_caracteres: int) -> list[str]:
    """Un `<fragmento …>` por cita, en el orden de relevancia recibido, sin citas repetidas y sin
    pasar `max_caracteres` en total (contando un salto de línea entre bloques). Pura, sin DB."""
    lineas: list[str] = []
    vistas: set[str] = set()
    usados = 0
    for fragmento in fragmentos:
        if fragmento.cita in vistas:
            continue
        linea = formatear_fragmento(fragmento)
        costo = len(linea) + (1 if lineas else 0)
        if usados + costo > max_caracteres:
            continue  # se descarta entero; el próximo puede entrar
        vistas.add(fragmento.cita)
        lineas.append(linea)
        usados += costo
    return lineas
