"""Context builder (5.6): `rag.contexto.bloque_de_citas`, puro, sin DB."""

from dataclasses import dataclass

from sistema.rag.contexto import bloque_de_citas, formatear_fragmento


@dataclass
class Frag:
    cita: str
    texto: str
    vigente: bool = True


def f(cita: str, texto: str) -> str:
    return f'<fragmento cita="{cita}" vigente="true">\n{texto}\n</fragmento>'


def test_delimita_con_cita_y_vigencia_y_respeta_el_orden():
    lineas = bloque_de_citas([Frag("TX350-D §3.2", "uno"), Frag("MBP-4 §6.1", "dos")], 1000)
    assert lineas == [f("TX350-D §3.2", "uno"), f("MBP-4 §6.1", "dos")]
    assert 'vigente="false"' in formatear_fragmento(Frag("TX350-C §3.2", "x", vigente=False))


def test_deduplica_por_cita():
    lineas = bloque_de_citas([Frag("A §1", "x"), Frag("A §1", "x"), Frag("B §2", "y")], 1000)
    assert lineas == [f("A §1", "x"), f("B §2", "y")]


def test_descarta_entero_el_que_no_entra_y_sigue_con_el_proximo():
    largo = Frag("LARGO §1", "z" * 500)
    corto = Frag("CORTO §2", "ok")
    lineas = bloque_de_citas([Frag("A §1", "a" * 50), largo, corto], 170)  # 102 + 1 + 58
    assert lineas == [f("A §1", "a" * 50), f("CORTO §2", "ok")]
    assert all("z" not in linea for linea in lineas)


def test_nunca_pasa_el_tope_contando_saltos_de_linea():
    frags = [Frag(f"D §{i}", "t" * 20) for i in range(20)]
    tope = 200
    lineas = bloque_de_citas(frags, tope)
    assert len("\n".join(lineas)) <= tope
    assert len(lineas) > 1


def test_sin_fragmentos_o_tope_cero():
    assert bloque_de_citas([], 1000) == []
    assert bloque_de_citas([Frag("A §1", "x")], 0) == []


# Lote 7: prefijos de tarea solo para nomic; bge-m3 embebe el texto tal cual.
class _ModeloEco:
    def embed_documents(self, textos):
        self.docs = textos
        return [[0.0] for _ in textos]

    def embed_query(self, texto):
        self.consulta = texto
        return [0.0]


def test_bge_m3_sin_prefijos_y_nomic_con_prefijos():
    from sistema.rag.embeddings import Embeddings

    eco = _ModeloEco()
    Embeddings(eco, nombre_modelo="bge-m3").embed_documentos(["a"])
    Embeddings(eco, nombre_modelo="bge-m3").embed_consulta("q")
    assert eco.docs == ["a"] and eco.consulta == "q"

    Embeddings(eco, nombre_modelo="nomic-embed-text").embed_documentos(["a"])
    Embeddings(eco, nombre_modelo="nomic-embed-text").embed_consulta("q")
    assert eco.docs == ["search_document: a"] and eco.consulta == "search_query: q"
