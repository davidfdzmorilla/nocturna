# ADR 0023 · Solución propia por `arxiv_id` y por valores

Fecha: 2026-10-06 · Estado: aceptado · **Supersede ADR 0015 §7 y amplía la exclusión de la solución propia de ADR 0020 §1** · Tarea: T83 · Origen: decisiones del autor del 2026-10-01 (dos vías unidas) y del 2026-10-02 (`confirmacion_independiente` no se publica hasta reconocer la solución propia citada por bibcode de revista)

## Contexto

La solución del propio paper se reconocía solo por `arxiv_id` (ADR 0015 §7). En la base real (2026-10-06), solo 104 de 40.037 soluciones activas del archivo traen `arxiv_id`, y de las 131 altas desde 2026-08-01 alrededor del 80 % citan un bibcode de revista. Cuando la versión de revista del propio paper entre en el archivo sin `arxiv_id`, la medida en espera se compararía consigo misma (σ ≈ 0), pasaría a `evaluated`, generaría una `confirmacion_independiente` falsa (T89) y se perdería `closed_loop` (T88). ADR 0015 §7 llamaba a ese σ ≈ 0 "fallo hacia el lado seguro"; desde T89 no lo es.

`Item` no guarda autores ni DOI, el snapshot del archivo no guarda DOI, y la correspondencia arXiv ↔ bibcode de revista de ADS exige un token. ADR 0015 y ADR 0020 no se editan.

## Decisión

1. **Clasificación** de cada solución del archivo respecto a un paper (`domain/own_solution.py`, `classify_solution`), en este orden:
   - con `arxiv_id`: igual al `external_id` del ítem → propia (`own_arxiv_id`); distinto → independiente;
   - sin `arxiv_id` y con `pl_pubdate` anterior al mes de `Item.published_at` menos `pubdate_margin_months` → independiente;
   - sin `arxiv_id`, masa o radio, sin cota, y alguna medida del paper del mismo parámetro a una distancia relativa ≤ `value_rel_tolerance` del valor del archivo, en unidad canónica → propia (`own_value_match`);
   - en cualquier otro caso → ambigua. Un `pl_pubdate` nulo o ilegible cuenta como plausible. En periodo no se compara por valor (dos papers recientes del mismo planeta coinciden al 1e-4): sin `arxiv_id` y con fecha plausible es ambigua.
2. **Uso**: `ComputeTensions` excluye de las previas las propias (las dos formas) y con ellas decide `closed_loop` y `own_solution_key`; las ambiguas siguen siendo previas. `confirmacion_independiente` exige que la referencia sea independiente; la regla se aplica al generar candidatos, así que protege también las evaluaciones guardadas antes de T83 sin reabrir estados terminales.
3. **Parámetros** en `[tension.own_solution]` de `pipeline.toml`, sin valores por defecto en código: `value_rel_tolerance = 0.01` y `pubdate_margin_months = 6`.
4. **Sin tabla de enlaces ni migración**: el enlace se calcula en cada evaluación. El evento "arXiv detecta antes, el archivo confirma después" es `closed_loop`. `scripts/t83_link_report.py` mide, sin escribir, el cruce por `arxiv_id`, el retraso preprint → archivo, las coincidencias por valor y el efecto de la regla en las evaluaciones guardadas.
5. **`confirmation_enabled`** sigue en `false`; activarlo es una decisión aparte del autor, tras ejecutar el script en la base real.

## Consecuencias

- Una versión de revista con los mismos valores que el preprint cierra el bucle en lugar de confirmarse a sí misma.
- Si la revista cambia los valores tras el arbitraje, la solución queda ambigua: se sigue comparando, pero no puede sostener una confirmación. Se puede perder alguna confirmación real de un paper de revista contemporáneo.
- Un paper independiente posterior con casi el mismo valor (menos del 1 % de diferencia) y sin `arxiv_id` se tomaría por la solución propia y daría `closed_loop`.
- Una cota superior del propio paper en su versión de revista (sin `arxiv_id`, fecha plausible) sale ambigua y sigue siendo previa: la medida podría quedar `consistent_with_limit` frente a su propia cota.
- La medición de `scripts/t83_link_report.py` empareja planetas por nombre normalizado, sin alias; puede quedarse corta.
- Resolver la ambigüedad del todo exige autores en `Item` o ADS; queda diferido (`OPEN_DECISIONS.md`).

## Alternativas descartadas

- **DOI o journal-ref de arXivRaw**: casi siempre vacíos al ingerir; habría que reconsultar cada ítem y el archivo no guarda DOI.
- **ADS API**: token y servicio externo nuevos para un problema sin casos medidos todavía.
- **Excluir las referencias ambiguas de las previas**: podría dar `primera_medida` o `closed_loop` falsos.
- **Tabla de enlaces ítem ↔ solución**: T84–T86 aún no tienen diseño.
