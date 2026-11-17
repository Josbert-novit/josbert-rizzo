# Corridas del corrector automático

Salida sin editar de `academia2026-tp/correccion/corregir.py` corrido contra este repo
(escenarios `martes-tranquilo` y `fuga-sin-enlace`). Se copian acá porque el corrector deja
los resultados dentro del repo de la planta, que no forma parte de esta entrega.

| Corrida | Fecha | Commit evaluado | Nota |
|---|---|---|---|
| [josbert-rizzo](corridas/josbert-rizzo/informe.md) | 2026-11-10 | `84a2abf` | 62.0 / 100 · APRUEBA |
| [josbert-rizzo-v2](corridas/josbert-rizzo-v2/informe.md) | 2026-11-13 | `674f395` | 63.4 / 100 · APRUEBA |

Las dos corridas usaron la planta en el commit
[`ab65b95`](https://github.com/NovitSoftware/academia2026-tp/tree/ab65b95/correccion) de
`NovitSoftware/academia2026-tp`. Qué mide cada criterio (A1, B2, E3...) y cómo se puntúa está en
la [rúbrica](https://github.com/NovitSoftware/academia2026-tp/blob/ab65b95/correccion/rubrica.yaml)
y en [`puntuar.py`](https://github.com/NovitSoftware/academia2026-tp/blob/ab65b95/correccion/puntuar.py).
No hace falta clonar la planta para leer los resultados, solo para volver a correrlos.

Cada carpeta tiene:

- `informe.md`: puntaje por criterio de la rúbrica.
- `resultado.json`: el mismo puntaje en formato máquina.
- `<escenario>.json` / `<escenario>.log`: mensajes y logs de cada escenario.
- `build.log`: build de las imágenes Docker.
- `meta.json`: commit, fecha y parámetros de la corrida.

Para reproducirla (con la planta clonada al lado, en `../academia2026-tp`):

```bash
cd ../academia2026-tp
python correccion/corregir.py --repo ../josbert-rizzo --escenarios martes-tranquilo fuga-sin-enlace
```

Ver `correccion/README.md` de la planta para las opciones exactas del corrector.
