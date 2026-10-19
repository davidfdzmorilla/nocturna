# ADR 0034 · Un resultado no se publica dos veces por las dos vías

Fecha: 2026-10-19 · Estado: aceptado · Tarea: T95 · Relacionados: ADR 0026, ADR 0029, ADR 0030 §4

## Contexto

La fase 2 tiene dos vías que llegan al redactor:

- **arXiv** (ADR 0026): `catalog_tension`, `primera_medida` y `confirmacion_independiente` a partir de las medidas de un paper.
- **El archivo** (ADR 0029, ADR 0030): `solution_tension` entre la solución que el NASA Exoplanet Archive da de alta y la de referencia.

Cuando un paper llega primero por arXiv y semanas después el archivo incorpora su solución, el mismo resultado sale dos veces. T85 solo impide que la referencia sea del mismo paper que la alta. Nada cruza la tensión del archivo con lo que ya produjo la vía arXiv, y el Editor no ve los hallazgos de noches anteriores.

Casos reales (consulta de solo lectura, 2026-10-19):

- **HD 65216 c, periodo.**
  - El 2026-10-08 se publicó un `catalog_tension` del paper arXiv 2610.02783, que el 2026-10-07 ya había dado una `primera_medida` de su masa.
  - El snapshot del 2026-10-16 trajo la solución de Wallace et al. 2026, con `arxiv_id = 2610.02783`, como nueva por defecto. `solution-tensions` la comparó con Wittenmyer et al. 2019 (10,09σ).
  - El Editor publicó el `solution_tension` la noche del 2026-10-16 con confianza 0,65.
- **TOI-2158 b, radio.**
  - El 2026-10-09 se redactó un `catalog_tension` del paper arXiv 2609.30915, que el Editor no publicó.
  - La noche del 2026-10-18 se redactó el `solution_tension` de la solución de Knudstrup et al. 2026 (3,18σ), del mismo paper. Fue una llamada del redactor para algo que el Editor ya había rechazado, y tampoco se publicó.

En la base había 10 tensiones del archivo elegibles, todas ya redactadas. En los dos casos el paper llegó primero por arXiv; no hay ninguno en sentido contrario. De las 10, 6 tienen una alta sin `arxiv_id`.

## Decisión

Decisión del autor (T95): D1 a, D2 b, D3 a, D4 a, D5 a y D6. Modifica ADR 0030 §4, que no se edita.

1. **Dónde (D1 a).**
   - `SelectSolutionTensions.pending()` excluye la tensión con el motivo `same_paper_arxiv` antes del redactor.
   - Es una comprobación al seleccionar, no al calcular:
     - `solution_tension` sigue siendo una foto fija y no hay migración;
     - el motivo no entra en el enum `SkipReason`, que es una columna con CHECK;
     - ve los hallazgos de arXiv posteriores al cálculo y se aplica a las filas pendientes ya guardadas.
   - Orden de los motivos: `not_eligible`, `already_written`, `same_paper_arxiv`, `writer_failed`, `invalid_tension`. Una tensión ya redactada sigue saliendo como `already_written`.
2. **Qué cuenta (D2 b).** Cualquier `Finding` de tipo `catalog_tension`, `primera_medida` o `confirmacion_independiente`, publicado o no, cuya `TensionEvaluation` cuelga de un `Item` con `source = 'arxiv'`.
   - Un rechazo del Editor por la vía arXiv también cuenta: el mismo resultado no vuelve a pasar por el redactor con otro nombre.
   - En una misma noche la pasada de arXiv escribe antes de que la del archivo seleccione (ADR 0030 §2), así que el Editor no recibe dos candidatos del mismo resultado.
   - Una `TensionEvaluation` sin `Finding` no cuenta. Si contara, un paper que quedó por debajo del umbral frente a la referencia de entonces taparía una tensión del archivo que es nueva.
3. **Coincidencia (D3 a, D4 a).** La clave es `(arxiv_id, planeta, parámetro)`:
   - el paper, por `Item.external_id` frente al `arxiv_id` de la solución dada de alta, los dos sin versión;
   - el planeta, por `TensionEvaluation.archive_planet_name` frente a `SolutionTension.planet_name`, los dos el `pl_name` canónico del archivo;
   - el parámetro, el mismo.

   Una alta sin `arxiv_id` nunca coincide. La regla es la función pura `domain/solution_tension.py::told_by_arxiv`. Las claves las reúne la aplicación con los repositorios que ya existen, sin puerto nuevo.
4. **Fallos.** Una evaluación con hallazgo cuyo `Item` no existe es una `InvariantViolation`.
   - `pending()` la propaga y se aplica ADR 0030 §2: la noche queda `partial` con `writer_error`, sin llamadas del archivo, y el Editor sigue.
   - Es preferible a redactar sin el filtro.
5. **Solo en un sentido (D5 a).** Un `catalog_tension` cuyo paper ya salió antes como `solution_tension` no se filtra en T95.
6. **Lo ya escrito (D6).**
   - No se toca ninguna fila de `solution_tension`.
   - El `solution_tension` publicado de HD 65216 c sigue publicado: no hay ningún mecanismo para despublicar y no se inventa uno.

## Consecuencias

- **Gasto:** el filtro solo quita candidatos antes de `_write_loop`. El redactor del archivo hace las mismas llamadas o menos. No cambian `budget.py`, el rol `writer`, su reserva, su tope ni el Editor.
- **Coste de la selección:** `pending()` hace, una vez por noche, tres consultas de hallazgos, una lectura de todas las evaluaciones (28 a 2026-10-19) y un `items.get` por cada evaluación con hallazgo (unas 19). Es despreciable; si `tension_evaluation` llega a miles de filas, conviene una consulta con JOIN en el repositorio.
- **`run-night --dry-run`:** lista la tensión como `excluida · planeta (parámetro) · same_paper_arxiv`.
  - No ejecuta la pasada de arXiv. Si esa noche se fuera a escribir un hallazgo de arXiv del mismo paper, el ensayo lista como elegible una tensión que la noche real excluiría: sobreestima, sin riesgo para el gasto.
- **Lo que no cubre:**
  - las altas sin `arxiv_id`, que pueden seguir duplicando un resultado de arXiv;
  - el sentido contrario (D5).

  Quedan como decisiones abiertas en `OPEN_DECISIONS.md`.
- **HD 65216 c** queda con dos hallazgos publicados del mismo resultado.

## Alternativas descartadas

- **Motivo nuevo al calcular, en `solution-tensions`:** exige migrar el CHECK. Además, como foto fija no ve un hallazgo de arXiv posterior al cálculo, y las filas ya guardadas no cambian.
- **Publicar los dos y enlazarlos:** necesita esquema, API y web, y siguen siendo dos hallazgos.
- **Dar al Editor los hallazgos previos del planeta:** gasta tokens, obliga a un `editor-v5` y cambia su estimación.
- **Solo un `catalog_tension` publicado (D2 a) o solo hallazgos publicados (D2 c):** no habrían evitado TOI-2158 b, y una misma noche podía llevar los dos al Editor.
- **Mismo planeta con cualquier parámetro (D3 b):** la `primera_medida` de masa de un paper taparía una tensión de periodo distinta.
- **Coincidencia también por valores (D4 b):** solo funciona si la evaluación vio la solución, y en HD 65216 c no la vio.
- **Aceptarlo:** se repite cada vez que el archivo incorpora un paper que ya pasó por arXiv.
