# Fixtures T71.c: salidas reales del Reader (T71.b)

Origen: humo manual `-m manual` de T71.b, ejecutado el 2026-09-29, prompt
experimental `reader-measures-exp1` (precursor de `reader-v3`), sobre los 4
abstracts de `tests/fixtures/t71b/abstracts.json`. Registro completo del
humo en `attribution.json` (fuera de este repositorio, en el scratchpad de
la sesión de T71.b).

Cada fichero `<external_id>.reader-measures-exp1.json` contiene el
`output_text` **crudo y real** devuelto por el modelo para ese abstract,
copiado tal cual (sin reformatear, sin corregir nada) desde
`attribution.json`. Son datos reales, no inventados para el test.

Ficheros:

- `2609.17025.reader-measures-exp1.json` — real. `measurements: []` (el
  paper es sobre relaciones masa-radio de una población, no mide un
  planeta concreto).
- `2609.20748.reader-measures-exp1.json` — real. Una medida de masa de
  `"RX J0534.0-0221 b"`, `origin: "this_work"`.
- `2609.26894.reader-measures-exp1.json` — real. Tres medidas (radio, masa,
  periodo) de `"TOI-2109 b"`, las tres `origin: "literature"`.
- `2609.30038.reader-measures-exp1.json` — real. Siete medidas de masa. El
  modelo devolvió `planet_name` como **solo la letra** del planeta (`"b"`/
  `"e"`), no el nombre completo (anfitriona + letra) que pide `reader-v3`:
  es la limitación que `reader-v3.md` intenta corregir con la instrucción
  de completar la anfitriona, y que la salvaguarda de
  `reader_measurements.filter_measurements` atrapa (`HOST_NOT_FOUND`) si el
  modelo no la completa.
- `2609.30038.reader-measures-exp1.derived-fullname.json` — **DERIVADA A
  MANO**, no es una salida real de Claude. Parte del fichero real de arriba
  y cambia únicamente el campo `planet_name` de cada una de las 7 medidas:
  `"b"` → `"V1298 Tau b"`, `"e"` → `"V1298 Tau e"`. Ningún otro campo se
  toca (mismos valores, errores, unidades, `evidence`, `origin`). Existe
  para probar el camino en el que el Reader **sí** complementa la
  anfitriona tal como pide `reader-v3.md`, que el humo real de T71.b no
  cubrió para este abstract.

Todos los ficheros son solo el `output_text`: no llevan la envoltura
`{"records": [...]}` de `attribution.json`, para que los tests puedan
leerlos con `Path.read_text()` y pasarlos directamente a
`parse_reader_v3_output`/`FakeLLMProvider.respond(raw=...)`.
