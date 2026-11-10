from sistema.procesamiento.unidades import normalizar_alias, resolver_unidad

BOMBA_PSI = {"tag": "B-102", "tipo": "bomba", "manometro_local": {"unidad": "psi"}}
FILTRO = {"tag": "F-201A", "tipo": "filtro"}


def test_normalizar_alias_kilos():
    assert normalizar_alias("kilos") == "kg/cm2"
    assert normalizar_alias("Kilo") == "kg/cm2"
    assert normalizar_alias("psi") == "psi"
    assert normalizar_alias(None) is None


def test_presion_local_usa_unidad_del_manometro_del_equipo():
    unidad, requiere, motivo = resolver_unidad("presion_descarga_local", 112.0, None, BOMBA_PSI)
    assert unidad == "psi"
    assert requiere is False
    assert motivo is None


def test_presion_local_con_unidad_dicha_distinta_pide_confirmacion():
    unidad, requiere, motivo = resolver_unidad("presion_descarga_local", 112.0, "kilos", BOMBA_PSI)
    assert unidad == "psi"
    assert requiere is True
    assert "manómetro local" in motivo


def test_diferencial_sospechoso_en_filtro_pide_confirmacion():
    unidad, requiere, motivo = resolver_unidad("presion_diferencial_local", 18.0, None, FILTRO)
    assert requiere is True
    assert "dieciocho" in motivo


def test_diferencial_normal_no_pide_confirmacion():
    unidad, requiere, motivo = resolver_unidad("presion_diferencial_local", 1.8, None, FILTRO)
    assert requiere is False
    assert motivo is None


def test_sin_equipo_resuelto_no_infiere_nada():
    unidad, requiere, motivo = resolver_unidad("presion_descarga_local", 112.0, "kilos", None)
    assert unidad == "kg/cm2"
    assert requiere is False
