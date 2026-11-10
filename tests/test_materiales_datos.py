from sistema.integraciones import materiales as materiales_datos


def test_repuestos_trae_la_lista_completa():
    repuestos = materiales_datos.repuestos()
    assert any(r["codigo"] == "TX-EMP-KIT" for r in repuestos)


def test_repuesto_por_codigo():
    r = materiales_datos.repuesto("ACE-ISO220")
    assert r is not None
    assert r["unidad"] == "litro"


def test_repuesto_inexistente_devuelve_none():
    assert materiales_datos.repuesto("NO-EXISTE") is None


def test_lista_materiales_de_un_equipo():
    codigos = materiales_datos.lista_materiales("F-201A")
    assert "FC-CART-25" in codigos
    assert "FC-CART-10" not in codigos


def test_lista_materiales_equipo_desconocido_devuelve_vacio():
    assert materiales_datos.lista_materiales("NO-EXISTE") == []


def test_tareas_estandar_filtra_por_equipo():
    tareas = materiales_datos.tareas_estandar("F-201A")
    assert all("F-201A" in t["equipos"] for t in tareas)
    assert any(t["codigo"] == "T-F-CAR" for t in tareas)


def test_tareas_estandar_sin_equipo_trae_todas():
    todas = materiales_datos.tareas_estandar()
    filtradas = materiales_datos.tareas_estandar("B-101")
    assert len(todas) >= len(filtradas)


def test_tareas_no_estandar():
    tareas = materiales_datos.tareas_no_estandar()
    assert any(t["codigo"] == "T-B-ROD" for t in tareas)
