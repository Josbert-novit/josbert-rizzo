"""Context builder (5.6) — diseño, no la implementación completa (eso es Entrega 2).

Arma el bloque de contexto que se le pasa a un LLM de consulta (no el de extracción de 4.x, que
no usa RAG) para responder una pregunta en lenguaje natural citando la sección exacta.

Diseño:
1. `buscar.buscar(embeddings, pregunta, k=5)` trae los `k` fragmentos más relevantes, ya filtrados
   por `vigente=true` (5.2) y opcionalmente por `equipo` si la pregunta menciona uno (resuelto
   contra el mismo catálogo de `procesamiento/resolucion.py`, para no reinventar la resolución de
   nombres de equipo).
2. Cada fragmento entra al contexto con su cita like antepuesta (`[TX350-D §3.2] <texto>`), nunca
   solo el texto — la cita es la razón de ser de 5.4: una respuesta sin cita no sirve.
3. Tope de tamaño: acumular fragmentos hasta un límite de caracteres (no de tokens — contar
   tokens exactos requiere el tokenizer del modelo elegido, que puede cambiar; un tope de
   caracteres conservador alcanza para no pasarse de la ventana de contexto del modelo local).
   Si un fragmento no entra entero, se descarta entero (no se trunca a la mitad: una cita con
   texto cortado es peor que no tener esa cita).
4. Deduplicar por documento+sección (puede pasar que la misma cita aparezca dos veces si se
   combinan resultados de más de una búsqueda, p. ej. pregunta + reformulación).
5. Lo que **no** entra al contexto por RAG (5.5): límites de alarma (`GET /api/planta`), stock y
   catálogo de tareas (`materiales.yaml` vía `integraciones/materiales.py`), estado en vivo de la
   planta (`GET /api/estado`). Eso son datos estructurados con un endpoint o archivo propio — RAG
   es para texto narrativo (manuales, contrato, historial), no para números que ya están
   disponibles exactos por API/SQL. Mezclar los dos en el mismo contexto de texto introduciría un
   paso de más (el LLM "leyendo" un número que ya se podía consultar directo) sin ninguna
   ganancia, y con el riesgo de que el LLM lo transcriba mal.

La Entrega 2 implementa esto como una función real (`armar_contexto(pregunta) -> str`) con el
prompt que arma la consulta al LLM; acá queda el diseño y el motivo de cada decisión.
"""
