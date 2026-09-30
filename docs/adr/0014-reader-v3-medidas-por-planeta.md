# ADR 0014 · Reader v3: medidas estructuradas por planeta dentro de `Reading`

Fecha: 2026-09-30 · Estado: aceptado · **Confirma en firme ADR 0013** (su condición §2, la prueba manual de T71.b, se cumplió) · **Cambia CLAUDE.md** § Modelo de dominio, § Agentes y § Control de gasto · Tarea: T71.c

## Contexto

ADR 0013 pasó al Reader la atribución de cada medida a su planeta, condicionada a una prueba manual. T71.b la hizo con un prompt experimental: atribución correcta en los 4 abstracts de prueba (el nombre de V1298 Tau salió incompleto, "b"/"e"). El autor adoptó la vía (c) en firme el 2026-09-29 y aprobó el plan revisado de fase 2. T71.c la lleva a producción.

## Decisión

1. **Dos variantes del Reader que conviven.** Los ítems con alguna categoría en `[reader] measurement_categories` (hoy `["astro-ph.EP"]`, incluidos los cross-list) se leen con `reader-v3`, que además devuelve `measurements`; el resto sigue con `reader-v2`, sin cambios. Lista vacía = v3 apagado (marcha atrás sin código). Cada `AgentCall` graba el `prompt_version` de su variante.
2. **Medidas dentro de `Reading`, sin entidad nueva.** Value object `Measurement` (`planet_name`, `parameter` mass/radius/period, `value`, `err_plus`, `err_minus`, `unit`, `limit`, `origin` this_work/literature, `evidence`) con invariantes: `value` finito y > 0, errores finitos ≥ 0 o ausentes, unidad coherente con el parámetro, nombre y evidencia no vacíos. `Reading.measurements: tuple[Measurement, ...] | None`: `None` = no extraído (v2 e histórico), `()` = extraído sin medidas. Salen de la misma llamada, son un hecho inmutable de esa lectura y no tienen identidad propia.
3. **Persistencia**: columna `readings.measurements` JSONB con `none_as_null=True` (sin ello `None` se guardaría como JSON `null` y se perdería la distinción); migración `52ccb04d0f06` con downgrade; filas históricas en NULL, sin backfill.
4. **Filtros en Python por medida, nunca por ítem**: una medida mal formada, que rompe una invariante, cuya `evidence` no es cita literal del abstract, cuyo valor no aparece en la `evidence`, o cuya anfitriona no aparece en el **abstract o el título** del ítem (límite de palabra; `,`, `~` y `\,` tratados como espacio; anfitrionas genéricas y prefijos de catálogo sin número rechazados) se descarta sola, se registra en el log (`reader.measurement_discarded`) y **no provoca reintento**. `objects` no sirve de verificación: es salida del mismo modelo. Solo un `measurements` ausente o que no sea lista es fallo estructural y reintenta como cualquier JSON inválido.
5. **Para σ solo cuentan** las medidas con `origin = this_work`, `limit = none` y los dos errores (`usable_for_tension`); es el contrato con T73.
6. **Nombre completo del planeta** (anfitriona + letra) en `reader-v3`; la normalización definitiva es de T74.
7. **Gasto**: cada variante autoriza con su propia estimación (`reader_estimated_tokens` para v2, `reader_v3_estimated_tokens = 13000` para v3), pasada a `authorize` por su `AgentRunner`; `budget.py` no cambia. `max_items_per_night` baja de 40 a 30 (adelantado desde T75). Ambos caminos de la CLI construyen el Reader con un único helper.
8. **`m sin i`** queda fuera del esquema v3 por ahora (riesgo aceptado mientras no se publique nada); añadirla sería un `reader-v4` y un valor de enum, sin migración.

## Evidencia

- Prueba manual con el camino de producción (2026-09-29): 4/4 abstracts de T71.b atribuidos correctamente, nombres completos, 0 descartes, 28.402 tokens (máx. 8.817 por abstract).
- Primera noche real con `reader-v3` (2026-09-30): `completed`, 30 ítems leídos (11 con v3), 9 publicados, 197.570 tokens. Reader v3: media 5.575 y máximo 7.338 tokens por llamada (v2: 3.545), un reintento recuperado. Las 11 lecturas v3 guardaron `()`; revisión manual: sin omisiones ni atribuciones falsas (7 abstracts de ciencia planetaria del sistema solar, uno con `m sin i` sin error, y 2 medidas de un candidato sin designación de planeta, "61 Cygni AB", descartadas por el filtro de anfitriona).

## Consecuencias

- Coste: unos 2.000 tokens más por ítem de astro-ph.EP que con v2; la estimación de 13.000 cubre el máximo observado con ~1,8×. Se ajusta en T75.
- astro-ph.EP incluye mucha ciencia planetaria del sistema solar: la v3 se aplica a ítems que nunca darán medidas contrastables. Restringirla queda abierto para T75.
- El ritmo de medidas contrastables por noche es bajo; T74 lo medirá sobre noches acumuladas.
- Comparabilidad: las lecturas se distinguen por `prompt_version` (`reader-v2`/`reader-v3`).

## Alternativas descartadas

- **Un solo prompt con `measurements` opcional**: haría ambiguo `None` frente a `()` y aplicaría el coste a todas las categorías.
- **Reintentar el ítem ante una medida inválida**: duplica el coste del ítem por un defecto local.
- **Tabla o entidad propia de medidas, o guardarlas en `AgentCall`**: no tienen identidad ni ciclo de vida propios; sería un agregado innecesario.
- **Validar la anfitriona contra `objects`**: la revisión reprodujo una atribución a otro planeta real (WASP-12 b frente a WASP-121 b).
