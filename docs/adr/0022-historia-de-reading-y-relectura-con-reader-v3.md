# ADR 0022 · Historia de `Reading` y relectura con reader-v3

Fecha: 2026-10-05 · Estado: aceptado · **Sustituye la decisión de `OPEN_DECISIONS.md` "Unicidad de `Reading` por `Item`" (T11/T41, 2026-09-17)** · Tarea: T82 · Origen: autor, 2026-10-01 (2609.35979, HIP 67522 b y c, estuvo a punto de leerse con v2)

## Contexto

Antes de T79, los ítems de astro-ph.EP que nombran un planeta concreto podían leerse con `reader-v2`, que no extrae medidas. Esas lecturas no entran en el contraste con el archivo (T88/T89). Hasta ahora no había forma de volver a leerlas: `ReadItem` solo lee ítems `new` y la base tenía una `Reading` única por `Item` (`uq_readings_item_id`). Tras una noche completa, un ítem leído queda en `discarded` o `published`, casi nunca en `read`.

## Decisión

1. **Comando**: `nocturna run-item <item_id> --reader v3 --force` (las dos banderas juntas; sin ellas `run-item` no cambia). Relee un ítem `read`, `discarded` o `published` con `reader-v3` y nada más: ni Popularizer ni Editor.
2. **Elegibilidad**, comprobada antes de cualquier `authorize`: el ítem cumple los criterios de v3 de ADR 0018 §2 (categoría en `measurement_categories` y `exoplanet_match`); `--reader v3` no fuerza la variante. La lectura vigente no tiene medidas extraídas (`measurements` es `None`). Así no puede haber dos evaluaciones del mismo paper, planeta y parámetro en T88/T89. Releer de v3 a v3 queda fuera.
3. **Historia**: la lectura anterior se conserva. `readings.superseded_at` la marca como sustituida y un índice único parcial (`uq_readings_item_id_current`, `WHERE superseded_at IS NULL`) impone una sola vigente por ítem. `ReadingRepository.get_for_item` y `with_measurements` devuelven solo la vigente, y `supersede` marca la anterior y añade la nueva en la misma transacción. `Reading` gana `prompt_version`. La migración (`f7c2d8e4a951`) lo rellena desde `agent_calls` solo cuando no es ambiguo y su downgrade falla si hay lecturas sustituidas.
4. **Estado y hallazgos**: `Item.status` y los `Finding` existentes no cambian. Si la lectura falla (JSON inválido dos veces, timeout, límite o error), el ítem no se marca `failed` y la lectura anterior sigue vigente. Las medidas nuevas se evalúan tras la noche (`evaluate-tensions`) y sus hallazgos de medida los decide el Editor la noche siguiente (ADR 0020).
5. **Gasto**: la relectura pasa por `AgentRunner` y `BudgetGuard` con la estimación de v3, como cualquier llamada, y solo dentro de la ventana 00:00–04:45; no hay bandera para saltarla. Abre su propio `Run`, con `notes = "reread"`, y nunca adopta un `Run` en curso: si hay uno, sale con código 9 sin gastar. Cada invocación tiene presupuesto completo (decisión abierta de T41 sobre `run-item`); el autor limita a mano cuántas lanza por noche (≤ 5).
6. **Métricas**: los `Run` de relectura no son noches. El informe de la mañana (`night_report.sql`) y el recuento de `Run` con `reader-v3` los excluyen, y el criterio de 7 noches de T75 cuenta solo noches de `run-night`.

## Consecuencias

- Se pueden recuperar las medidas de ítems leídos con v2 antes de T79, a unos 6.400 tokens por ítem.
- La relectura se lanza a mano, entre el final de la noche y las 04:45. Si un proceso de relectura muere y deja su `Run` en `RUNNING`, la siguiente `run-night` lo cierra como `KILLED` y sobrescribe `notes`, con lo que se pierde la marca `reread` y ese `Run` contaría en las métricas; mientras siga vivo, `run-item` sin banderas lo adoptaría. Afecta solo a métricas, no al gasto.
- Las consultas sobre `readings` tienen que filtrar las sustituidas. Las existentes ya lo hacen.

## Alternativas descartadas

- **Sustituir la lectura en el sitio**: pierde la lectura v2 con su `prompt_version`, que el autor pidió conservar.
- **Forzar v3 en ítems que no cumplen ADR 0018**: contradice el filtro; un falso negativo se corrige en la lista y con `backfill_exoplanet_match.py`.
- **Encolar las relecturas en `run-night`**: sería otra tarea; cambia el gasto de la noche.
- **Adoptar el `Run` de la noche**: gastaría del pool del Reader y de su tope de llamadas.
