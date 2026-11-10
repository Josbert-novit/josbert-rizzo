from sistema.procesamiento.variables import mapear_variable


def test_mapea_descarga():
    assert mapear_variable("presión de descarga") == "presion_descarga"


def test_mapea_diferencial():
    assert mapear_variable("diferencial") == "presion_diferencial"


def test_mapea_vibracion():
    assert mapear_variable("Vibración") == "vibracion"


def test_no_reconocida_devuelve_none():
    assert mapear_variable("goteo por la empaquetadura") is None
