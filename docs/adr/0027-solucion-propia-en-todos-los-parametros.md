# ADR 0027 · Solución propia en todos los parámetros del planeta

Fecha: 2026-10-09 · Estado: aceptado · **Amplía ADR 0023 §2** · Tarea: T92 · Origen: revisión de la noche del 2026-10-08 (HD 715 b)

## Contexto

ADR 0023 reconoce como propia del paper una solución del archivo por `arxiv_id` o, sin él, por coincidencia de valor en masa o radio con fecha plausible. `ComputeTensions` clasificaba cada grupo (planeta del Reader, parámetro) solo con las medidas de ese grupo. En el periodo nunca se busca coincidencia de valor, así que una fila propia por masa o radio seguía siendo previa del periodo.

Primer caso real: HD 715 b (2610.08065). La fila `62b8938e48…` es propia por valor en masa y radio (`closed_loop`), pero el periodo del mismo paper se evaluó frente a ella con σ = 0 y quedó guardado como `evaluated`. En la base real (2026-10-09) es la única evaluación afectada: era su única previa y no tiene ningún `Finding`.

La `solution_key` identifica una fila del archivo (sha256 de `pl_name`, referencia, `soltype` y valores) y es la misma en las listas de masa, radio y periodo de esa fila; incluye `pl_name`, así que nunca coincide entre planetas. ADR 0023 no se edita.

## Decisión

1. **Propia en un parámetro, propia en todos.** `ComputeTensions` procesa cada `Reading` en dos fases. En la primera resuelve cada planeta una vez, pide sus soluciones, descarta los periodos con `ttv_flag` y clasifica cada grupo con `classify_solution` y las medidas de ese grupo, como antes. Entre fases, `domain/own_solution.py::own_solution_keys` reúne las claves de todas las filas `OWN_ARXIV_ID` u `OWN_VALUE_MATCH` de la `Reading`. En la segunda, toda fila con una de esas claves se trata como propia en cualquier grupo: queda fuera de las previas, de las comparaciones y de la cota superior.
2. **`own_solution_key` por grupo** (D2): solo se rellena en los grupos donde la fila propia aparece en la lista de ese parámetro (la menor clave propia presente). Si la fila no trae ese parámetro (p. ej. masa como `msini`), el grupo no cambia y conserva `primera_medida`.
3. **Consumidores sin cambios.** `classify_solution`, `SolutionProvenance`, `catalog_tension_skip_reason`, `confirmation_eligible`, `first_measurement_eligible` y `SelectTensions` no cambian. Una referencia que solo es propia por propagación sale `AMBIGUOUS` al reclasificarla con las medidas de la evaluación, así que ya estaba bloqueada para `catalog_tension` y `confirmacion_independiente`.
4. **Evaluaciones guardadas** (D1): no se reabren. La de HD 715 b (periodo) queda `evaluated` con σ = 0; no alimenta ningún hallazgo (`below_threshold` y `reference_not_independent`). Pertenece a la decisión abierta "Evaluaciones congeladas cuando el archivo cambia después". Las `awaiting_reference` sí se reevalúan con la regla nueva.
5. **Medición.** `scripts/t83_link_report.py` recalcula cada evaluación con todas las medidas del planeta en la `Reading`; en la base real (2026-10-09) lista un cambio: HD 715 b periodo `evaluated -> closed_loop`.

## Consecuencias

- Sin migración, sin configuración nueva y sin LLM: no afecta al gasto.
- Un falso positivo de coincidencia por valor en masa o radio (dentro de `value_rel_tolerance`) excluye ahora esa fila también en los demás parámetros del planeta.
- Un fallo de resolución de un nombre del Reader falla todos sus grupos de forma explícita; los demás planetas de la lectura se evalúan.
- Un grupo de periodo descartado por TTV no aporta claves; no cambia nada, porque la procedencia propia de una fila también aparece en su masa o su radio.
- Queda abierto: dos nombres del Reader para el mismo planeta con fallo de resolución en el que detectaría la fila propia; el otro grupo podría evaluarse frente a ella y quedar terminal. El informe de `t83_link_report.py` filtra por el nombre exacto del Reader y no reproduce esa unión.

## Alternativas descartadas

- **Clasificar cada grupo con todas las medidas de la `Reading`**: un valor de otro planeta podría coincidir por casualidad.
- **Nuevo valor de `SolutionProvenance`**: cambiaría `classify_solution` y sus usos en T76 y T89 sin necesidad.
- **Reabrir la evaluación de HD 715 b** (script o `UPDATE` a mano): exige una transición nueva desde `evaluated` o saltarse el dominio, y adelanta la decisión abierta sobre evaluaciones congeladas.
- **Marcar `own_solution_key` en todos los grupos del planeta**: convertiría en `closed_loop` casos que hoy son `primera_medida`.
