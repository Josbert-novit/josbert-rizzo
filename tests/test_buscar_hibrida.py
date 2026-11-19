"""Búsqueda híbrida (hallazgo H1, Fase 15): partes puras, sin Postgres ni Ollama."""

import threading

from sistema.llm.control import TrabajoUrgente
from sistema.rag.buscar import K_RRF, consulta_full_text, fusionar_rrf
from sistema.rag.indexar import filas_de_texto


# --- fusión RRF ---------------------------------------------------------------------------

def test_rrf_premia_lo_que_aparece_en_las_dos_listas():
    lexico = ["A", "B", "C"]
    semantico = ["C", "D", "A"]
    ids = [fid for fid, _ in fusionar_rrf(lexico, semantico)]
    # A: 1/61 + 1/63, C: 1/63 + 1/61 → empatan arriba; B y D solo en una lista, abajo.
    assert set(ids[:2]) == {"A", "C"}
    assert set(ids[2:]) == {"B", "D"}


def test_rrf_con_una_lista_vacia_respeta_el_orden_de_la_otra():
    """Mientras faltan embeddings (o Ollama no responde), la búsqueda es solo léxica."""
    assert [fid for fid, _ in fusionar_rrf(["X", "Y", "Z"], [])] == ["X", "Y", "Z"]


def test_rrf_sin_resultados():
    assert fusionar_rrf([], []) == []


def test_rrf_puntaje_es_suma_de_reciprocos_de_posicion():
    (fid, puntaje), = fusionar_rrf(["A"], ["A"])
    assert fid == "A"
    assert puntaje == 2 / (K_RRF + 1)


def test_rrf_combina_posiciones_no_puntajes():
    """Algo segundo en las dos listas le gana a algo primero en una sola."""
    ids = [fid for fid, _ in fusionar_rrf(["solo-lexico", "ambas"], ["solo-semantico", "ambas"])]
    assert ids[0] == "ambas"


# --- consulta full-text -------------------------------------------------------------------

def test_consulta_es_or_de_palabras():
    assert consulta_full_text("límite de vibración") == "límite | de | vibración"


def test_consulta_sin_caracteres_que_rompan_to_tsquery():
    consulta = consulta_full_text("¿qué dice TX350-D §3.2 (Rev. D) & 'presión' | ! :* <-> _x?")
    assert consulta is not None
    for prohibido in "&!()':*<>§?¿_":
        assert prohibido not in consulta
    assert "tx350" in consulta.split(" | ")


def test_consulta_conserva_codigos_y_numeros():
    partes = consulta_full_text("falla E 4 en F-201B").split(" | ")
    assert "4" in partes  # número de un dígito se conserva
    assert "f" in partes or "201b" in partes
    assert "e" not in partes  # letra suelta (no número) se descarta


def test_consulta_sin_duplicados_y_en_minuscula():
    assert consulta_full_text("Bomba bomba BOMBA") == "bomba"


def test_consulta_vacia():
    assert consulta_full_text("") is None
    assert consulta_full_text("¿?!") is None


# --- pasada 1 del indexador (solo texto) --------------------------------------------------

def test_filas_de_texto_sin_embedding_y_con_metadatos():
    documentos = [{"id": "TX350-D", "tipo": "manual_fabricante", "equipos": ["B-101", "B-102"]}]
    fragmentos = [
        {"id": "TX350-D §3.2", "doc": "TX350-D", "numero": "3.2", "titulo": "Límites de operación",
         "clave": None, "pagina": 12, "vigente": True, "texto": "Presión máxima..."},
        {"id": "TX350-D §9.9", "doc": "TX350-D", "titulo": "Límites de otros modelos de la familia",
         "vigente": True, "texto": "TX-250..."},
    ]
    filas = filas_de_texto(fragmentos, documentos)
    assert len(filas) == 2
    assert len(filas[0]) == 11  # sin columna de embedding
    fid, doc, numero, titulo, _clave, pagina, vigente, aplica, tipo, equipos, texto = filas[0]
    assert (fid, doc, numero, pagina, vigente, aplica, tipo) == (
        "TX350-D §3.2", "TX350-D", "3.2", 12, True, True, "manual_fabricante")
    assert equipos == ["B-101", "B-102"]
    assert texto == "Presión máxima..."
    assert filas[1][7] is False  # sección de otros modelos: aplica=false


# --- pausa del indexador ------------------------------------------------------------------

def test_trabajo_urgente_activo_mientras_hay_alguno_en_curso():
    t = TrabajoUrgente()
    assert not t.activo()
    with t:
        assert t.activo()
        with t:  # nota y orden solapadas
            assert t.activo()
        assert t.activo()  # terminó una, la otra sigue: la indexación sigue en pausa
    assert not t.activo()


def test_trabajo_urgente_se_libera_aunque_falle():
    t = TrabajoUrgente()
    try:
        with t:
            raise RuntimeError("falló la nota")
    except RuntimeError:
        pass
    assert not t.activo()


def test_trabajo_urgente_seguro_entre_hilos():
    t = TrabajoUrgente()

    def trabajar():
        for _ in range(1000):
            with t:
                pass

    hilos = [threading.Thread(target=trabajar) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert not t.activo()
