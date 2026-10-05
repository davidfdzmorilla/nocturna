# ADR 0020 · Regla de referencia, límites, medidas en espera y hallazgos de medida

Fecha: 2026-10-05 · Estado: aceptado · **Supersede ADR 0015 §6 y matiza ADR 0012 §6** · Tareas: T88 y T89 · Origen: decisiones del autor del 2026-10-02 tras la primera noche con medidas (2609.35979, HIP 67522 b y c)

## Contexto

La primera noche con medidas de `reader-v3` (2026-10-02) dio cinco medidas de HIP 67522 b y c. La solución por defecto del archivo (Barber et al. 2024) no tiene masa, y el archivo acababa de publicar Chakraborty et al. 2026 (arXiv 2606.18045): para b, 13,8 ± 1,0 M⊕; para c, solo una cota superior de 22 M⊕. Con ADR 0015 §6, sin solución por defecto con el parámetro no hay candidato, y la medida se perdía aunque existiera una previa publicada y confirmada. Además, TOI-210 b y TOI-6981 b no están en el archivo: con las reglas vigentes, su medida no producía nada. El autor decidió el 2026-10-02 cuatro puntos, implementados en T88 (cálculo y estados) y T89 (publicación).

ADR 0015 y ADR 0019 no se editan. ADR 0019 cita "ADR 0020 (T86)" para el criterio de cierre de la fase 2; ese criterio pasa a ADR 0021 (T86), por la renumeración del autor del 2026-10-02.

## Decisión

1. **Regla de referencia por parámetro** (T88; supersede ADR 0015 §6). La referencia es la solución por defecto del archivo si es `Published Confirmed` con error bilateral en ese parámetro; si no, la `Published Confirmed` más reciente que lo tenga con error bilateral, ordenada por `pl_pubdate`, después `releasedate` y desempate por `solution_key`. La solución del propio paper (`arxiv_id == Item.external_id`) se excluye siempre. Las previas salen del histórico `archive_solution` (ADR 0019), no de una consulta en vivo. `CatalogTension` (ADR 0017) no cambia: `catalog_tension_from` sigue exigiendo que la referencia sea la solución por defecto; publicar una tensión frente a otra referencia queda abierto (T76).
2. **Límites superiores** (T88). Una cota no es referencia. La medida es incompatible con la cota si (x − L)/e_minus ≥ `threshold_sigma` en todas las medidas del paper, y consistente en otro caso. "Consistente con límite" no se publica. Periodo: diferencia mínima ΔP/P ≥ 1e-4 o ΔP ≥ 1 h; un alias (razón a menos de 0,01 de un entero 2 ≤ n ≤ 5) es etiqueta, no descarte; con `ttv_flag` no se comparan periodos.
3. **Medidas en espera** (T88). Cada (lectura, planeta del Reader, parámetro) con medidas utilizables tiene una `TensionEvaluation` en la tabla `tension_evaluation`, con uno de cinco estados: `awaiting_reference`, `evaluated`, `consistent_with_limit`, `incompatible_with_limit` y `closed_loop` (la solución que entra es la del propio paper). Solo `awaiting_reference` se reevalúa. Se evalúa al final de `archive-snapshot`, en `nocturna evaluate-tensions` y, desde T89, tras cada noche desde `run-night-scheduled.sh` (nunca en `--dry-run`; su fallo no cambia el código de salida de la noche). Un planeta solo cuenta como ausente del archivo si el servicio de alias responde `System Not Found`; cualquier otra respuesta es un fallo de resolución: esa medida no se evalúa esa vez, no se cachea y se reintenta en la siguiente pasada.
4. **Dos tipos nuevos de `Finding`** (T89; matiza ADR 0012 §6). ADR 0012 §6 exigía una discrepancia calculada para publicar. Desde aquí también se publica, con sus números y su enlace, lo que el contraste determinista dice cuando **no** hay tensión:
   - **`primera_medida`**: una `TensionEvaluation` `awaiting_reference` de masa o radio con error bilateral, sin cota y sin solución propia. Dos subtipos guardados en el dato: el planeta no está en el archivo (`absent`) o está sin ninguna solución `Published Confirmed` comparable (`no_comparable_solution`). Es "primera en el archivo", no "primera en la literatura".
   - **`confirmacion_independiente`**: una `TensionEvaluation` `evaluated` de masa o radio cuyas medidas están todas a σ ≤ `confirmation_max_sigma` (2,0) frente a la referencia del punto 1, con la más reciente de las dos entradas (paper: `Item.published_at`; referencia: `releasedate`) dentro de `confirmation_window_days` (30) contados desde la fecha local de la noche. La independencia solo se comprueba como artículos distintos; método y equipo no se pueden comprobar con los datos disponibles y el texto no los afirma. **Desactivado** (`confirmation_enabled = false`) hasta que T83 reconozca la solución propia citada por el bibcode de la revista: sin eso, el propio paper podría salir como su confirmación.
   - Un `Finding` por evaluación, único por (`tension_evaluation_id`, `type`). Datos estructurados en JSONB con `schema_version` (`first_measurement`, `independent_confirmation`), con CHECK de "si y solo si" por tipo, como ADR 0017; `Item.status` no cambia. Título y tres niveles por plantillas deterministas en castellano, sin tokens. Pasan por el Editor en su única llamada por noche (prompt `editor-v2`, que trata "compatible" como información), que decide por `candidate_id` (el id del `Finding`) en lugar de `item_id`. Tope `max_candidates_per_night` (5) por `Run`; el validador de la reserva del Editor cuenta `max_items_per_night + max_candidates_per_night`.

## Consecuencias

- Una medida ya no se pierde por falta de solución por defecto ni por ausencia del planeta: queda en espera y cada snapshot la reevalúa.
- Una noche con solo candidatos de medida llama al Editor (antes cerraba sin candidatos). Peor caso de la estimación: 2.500 + (30 + 5) × 850 = 32.250 ≤ 60.000 de reserva. `budget.py` no cambia; la generación de candidatos no llama a ningún agente.
- Los candidatos de medida que el Editor no llega a decidir no se vuelven a ofrecer (misma política que `paper_explained`).
- Una evaluación terminal no se recalcula aunque el archivo cambie después (abierto en `OPEN_DECISIONS.md`).
- La web no distingue todavía los tipos (T77): el tipo va explícito en el título de la plantilla.

## Alternativas descartadas

- **Mantener ADR 0015 §6**: perdía medidas como la de HIP 67522 b, comparables con una previa publicada y confirmada.
- **Usar una cota superior como referencia para el σ**: una cota no tiene error bilateral; se compara aparte (punto 2).
- **Redactar los tipos nuevos con el Popularizer o con el redactor de T75/T76**: gastaba del pool de Reader y Popularizer o esperaba a T75; las plantillas no inventan cifras y se pueden sustituir cuando exista el redactor.
- **Publicar `confirmacion_independiente` antes de T83**: riesgo de presentar el propio paper como confirmación.
