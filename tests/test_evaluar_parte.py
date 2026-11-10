from evaluacion.evaluar_parte import _misma_variable, _valor_cercano, evaluar

VERDAD_SIN_AMBIGUEDAD = {
    "nota_id": "NOTA-1",
    "ambiguedades": [],
    "items": [
        {"tag": "B-101", "variable": "presion_descarga_local", "valor": 112.0, "unidad": "kg/cm2"},
    ],
}

VERDAD_CON_AMBIGUEDAD = {
    "nota_id": "NOTA-2",
    "ambiguedades": [{"dicho": "la bomba", "tag": "B-101", "tipo": "equipo"}],
    "items": [
        {"tag": "B-101", "variable": "temperatura_rodamiento_ir", "valor": 63.0, "unidad": "degC"},
    ],
}


def _parte(filas, nota_id="NOTA-1"):
    return {"nota_ids": [nota_id], "filas": filas}


def test_valor_cercano_tolera_pequenas_diferencias():
    assert _valor_cercano(112.0, 112.3) is True
    assert _valor_cercano(112.0, 115.0) is False
    assert _valor_cercano(None, None) is True
    assert _valor_cercano(None, 1.0) is False


def test_misma_variable_por_palabra_clave():
    assert _misma_variable("presión de descarga", "presion_descarga_local") is True
    assert _misma_variable("diferencial", "presion_diferencial") is True
    assert _misma_variable("vibración", "temperatura_rodamiento_ir") is False


def test_evaluar_nota_sin_ambiguedad_todo_correcto():
    filas = [{"tag": "B-101", "variable": "presión de descarga", "valor": 112.0}]
    r = evaluar([VERDAD_SIN_AMBIGUEDAD], {"NOTA-1": _parte(filas)})

    assert r.notas_evaluadas == 1
    assert r.items_verdad == 1
    assert r.tag_correcto == 1
    assert r.variable_correcta == 1
    assert r.valor_correcto == 1
    assert r.notas_con_ambiguedad_real == 0
    assert r.pregunto_sin_necesidad == 0


def test_evaluar_detecta_pregunta_de_mas_falso_positivo():
    filas = [{"tag": None, "ambiguo": True, "variable": "presión de descarga", "valor": 112.0}]
    r = evaluar([VERDAD_SIN_AMBIGUEDAD], {"NOTA-1": _parte(filas)})

    assert r.pregunto_sin_necesidad == 1
    # el tag no coincide (quedó sin resolver): no cuenta como acierto de tag
    assert r.tag_correcto == 0


def test_evaluar_detecta_ambiguedad_real_resuelta_con_pregunta():
    filas = [{"tag": "B-101", "ambiguo": False, "variable": "temperatura rodamiento", "valor": 63.0}]
    r = evaluar([VERDAD_CON_AMBIGUEDAD], {"NOTA-2": _parte(filas, "NOTA-2")})

    # el sistema resolvió sin preguntar (pasó por el fallback genérico, por ejemplo): no es ideal
    # pero tampoco es el caso que este test ejercita — ver el siguiente para el falso negativo.
    assert r.notas_con_ambiguedad_real == 1


def test_evaluar_detecta_falso_negativo_no_pregunto_debiendo():
    filas = [{"tag": "B-101", "ambiguo": False, "variable": "temperatura rodamiento", "valor": 63.0}]
    r = evaluar([VERDAD_CON_AMBIGUEDAD], {"NOTA-2": _parte(filas, "NOTA-2")})

    assert r.no_pregunto_debiendo == 1
    assert r.pregunto_cuando_debia == 0


def test_evaluar_detecta_verdadero_positivo_pregunto_correctamente():
    filas = [{"tag": None, "ambiguo": True, "candidatos": ["B-101", "B-102"], "variable": "temperatura rodamiento", "valor": 63.0}]
    r = evaluar([VERDAD_CON_AMBIGUEDAD], {"NOTA-2": _parte(filas, "NOTA-2")})

    assert r.pregunto_cuando_debia == 1
    assert r.no_pregunto_debiendo == 0


def test_evaluar_nota_no_procesada_no_cuenta():
    r = evaluar([VERDAD_SIN_AMBIGUEDAD], {})

    assert r.notas_evaluadas == 0
    assert r.items_verdad == 0


def test_reporte_no_explota_sin_datos():
    r = evaluar([], {})
    assert "s/d" in r.reporte()
