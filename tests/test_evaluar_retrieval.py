from evaluacion.evaluar_retrieval import evaluar


def test_hit_y_recall_perfectos_si_la_relevante_es_la_primera():
    suite = [{"id": "q1", "pregunta": "x", "relevantes": ["A"], "distractores": []}]

    def buscador(pregunta, k):
        return ["A", "B", "C"]

    r = evaluar(buscador, suite)["total"]
    assert r["hit@k"][1] == 1.0
    assert r["recall@k"][1] == 1.0
    assert r["mrr"] == 1.0


def test_no_hit_si_la_relevante_nunca_aparece():
    suite = [{"id": "q1", "pregunta": "x", "relevantes": ["A"], "distractores": []}]

    def buscador(pregunta, k):
        return ["B", "C", "D"]

    r = evaluar(buscador, suite)["total"]
    assert r["hit@k"][10] == 0.0
    assert r["recall@k"][10] == 0.0
    assert r["mrr"] == 0.0


def test_recall_parcial_con_varias_relevantes():
    suite = [{"id": "q1", "pregunta": "x", "relevantes": ["A", "B"], "distractores": []}]

    def buscador(pregunta, k):
        return ["A", "X", "Y"]  # solo una de las dos relevantes, en el top-3

    r = evaluar(buscador, suite)["total"]
    assert r["recall@k"][3] == 0.5
    assert r["hit@k"][3] == 1.0  # alcanza con una


def test_mrr_premia_el_ranking_mas_alto():
    suite = [{"id": "q1", "pregunta": "x", "relevantes": ["A"], "distractores": []}]

    def buscador(pregunta, k):
        return ["X", "Y", "A"]  # relevante en la posición 3

    r = evaluar(buscador, suite)["total"]
    assert r["mrr"] == round(1 / 3, 3)


def test_distractor_en_top5_se_cuenta():
    suite = [{"id": "q1", "pregunta": "x", "relevantes": ["TX350-D §3.2"], "distractores": ["TX350-C §3.2"]}]

    def buscador(pregunta, k):
        return ["TX350-D §3.2", "TX350-C §3.2"]

    r = evaluar(buscador, suite)["total"]
    assert r["distractor_en_top5"] == 1


def test_distractor_fuera_del_top5_no_se_cuenta():
    suite = [{"id": "q1", "pregunta": "x", "relevantes": ["A"], "distractores": ["D"]}]

    def buscador(pregunta, k):
        return ["A", "B", "C", "E", "F", "D"]  # D en posición 6, fuera del top-5

    r = evaluar(buscador, suite)["total"]
    assert r["distractor_en_top5"] == 0


def test_por_tipo_separa_resultados():
    suite = [
        {"id": "q1", "pregunta": "x", "tipo": "limites", "relevantes": ["A"], "distractores": []},
        {"id": "q2", "pregunta": "y", "tipo": "historial", "relevantes": ["B"], "distractores": []},
    ]

    def buscador(pregunta, k):
        return ["A"] if pregunta == "x" else ["Z"]

    por_tipo = evaluar(buscador, suite)["por_tipo"]
    assert por_tipo["limites"]["hit@k"][1] == 1.0
    assert por_tipo["historial"]["hit@k"][1] == 0.0


def test_sin_relevantes_no_rompe_el_recall():
    suite = [{"id": "q1", "pregunta": "x", "relevantes": [], "distractores": []}]

    def buscador(pregunta, k):
        return ["A", "B"]

    r = evaluar(buscador, suite)["total"]
    assert r["n"] == 1
    assert r["recall@k"][5] == 0.0
