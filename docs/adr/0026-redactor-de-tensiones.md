# ADR 0026 · Redactor de tensiones e integración en `run-night`

Fecha: 2026-10-08 · Estado: aceptado · Tarea: T76 · Relacionados: ADR 0006 (agentes sin herramientas), ADR 0012 §4, §12 y §13, ADR 0017 (`catalog_tension`), ADR 0020 (hallazgos de medida, `editor-v2`), ADR 0023 (solución propia), ADR 0025 (reserva del redactor)

## Contexto

La fase 2 calcula en Python la tensión entre una medida de un paper y las soluciones previas del NASA Exoplanet Archive (T73, T88), y el dominio ya admite el tipo `catalog_tension` (T72). Faltaba el agente que redacta el texto de esas tensiones y su lugar en la noche. ADR 0025 ya reservó el presupuesto (rol `writer`, 24.000 tokens, tope de 2 llamadas). En la base, a 2026-10-08, hay 3 evaluaciones con σ ≥ 3 en 7 noches con `reader-v3`, todas frente a la solución por defecto del archivo.

## Decisión

1. **Sobre evaluaciones ya guardadas.** El redactor no cruza nada: trabaja sobre las `TensionEvaluation` que dejó `evaluate-tensions` al terminar la noche anterior (T89 D10). La noche no consulta el archivo, a costa de una noche de retraso entre la lectura y la redacción. No hay ventana de recencia: las evaluaciones ya guardadas se redactan.
2. **Qué evaluación es elegible** (regla pura `catalog_tension_skip_reason` en `domain/measurement_findings.py`): estado `evaluated`; `reference_sigma` ≥ `threshold_sigma`; referencia igual a la solución por defecto (lo único que admite `CatalogTension` v1); referencia `INDEPENDENT` según `classify_solution` (ADR 0023; excluye las ambiguas, que pueden ser el propio paper revisado por la revista); en periodo, diferencia mínima y sin sospecha de alias. `incompatible_with_limit` y las tensiones frente a otra referencia quedan fuera.
3. **Selección y redacción separadas.** La selección (`SelectTensions`, solo lectura, sin proveedor ni guard) descarta además las evaluaciones ya redactadas, los ítems marcados como fallidos y las que no forman una `CatalogTension` válida, y ordena por σ descendente. La construcción de la `CatalogTension` se hace antes de autorizar nada: una tensión inválida no cuesta tokens. `run-night --dry-run` solo usa la selección.
4. **Una llamada por tensión** (`WriteTensions`), **a través de `AgentRunner`** con rol `writer`, la estimación `writer_estimated_tokens` y hasta `max_calls_per_item` intentos (uno y reintento). Conversación nueva, sin herramientas (ADR 0006). Modelo en `[models] writer` de `pipeline.toml` (Sonnet). Prompt `writer-v1`; el dato va entre `<tension>` y `</tension>`, cada valor en una línea y sin `<` ni `>`, e incluye las medidas con su `evidence`, la referencia y el σ frente a cada solución previa. El prompt pide nombrar las previas compatibles, no inventar causas ni cifras y no presentar ningún valor como "el correcto".
5. **El `Finding`** se crea sin publicar, con `type = catalog_tension`, el dato `CatalogTension` y `tension_evaluation_id`. El vínculo con la evaluación es obligatorio también para este tipo (migración `a3f6d9c1b852`, CHECK `ck_findings_tension_evaluation_id_iff_type`); el índice único `(tension_evaluation_id, type)` impide redactar dos veces la misma evaluación. `Item.status` no cambia (ADR 0017).
6. **Fallida.** No hay estado propio: un ítem con `max_calls_per_item` o más llamadas `invalid_output` del redactor, sumando todas las noches, no se vuelve a redactar. Sin esa marca, una tensión con salida siempre inválida gastaría la reserva cada noche.
7. **Lugar en la noche:** Reader → Popularizer → hallazgos de medida → redactor → Editor. La fase no corre si la noche ya ha decidido saltarse el Editor. Ante una denegación del guard: `OUTSIDE_WINDOW` cierra `killed` sin Editor; `RUN_NOT_RUNNING`, como las demás fases; el tope de llamadas termina la fase sin degradar la noche; el presupuesto agotado la deja `partial` (`writer_budget_exhausted`) y sigue al Editor (ADR 0025 §4). `RATE_LIMITED` deja `partial` sin Editor. Un error inesperado termina la fase (no sigue gastando en el siguiente candidato), deja `partial` (`writer_error`) y sigue al Editor; nunca se captura la cancelación del `hard_stop`.
8. **Editor `editor-v3`.** Un único bloque `<candidates>` con los cuatro tipos; para `catalog_tension`, una línea `data` acotada (planeta, parámetro, valores, referencia, σ, umbral, número de previas y su rango de σ), nunca `evidence`. El prompt la presenta como "discrepancia calculada, no verificada" y pide omitirla si el título o el texto que ve afirman más que los números. K = 5 de T89 no se aplica a este tipo: lo acota el tope del redactor, que el validador de la reserva del Editor ya cuenta. `editor-v2.md` se conserva.

## Consecuencias

- `budget.py` no cambia. El único camino nuevo hacia Claude es la fase del redactor → `AgentRunner`; `test_llm_call_sites.py` sigue con una sola entrada. Sin tensiones elegibles no hay ni autorización ni llamada.
- Como mucho 2 llamadas y 2 `catalog_tension` por noche. Una llamada que gaste más de lo estimado sale de la reserva del Editor (riesgo heredado, OD 71), acotado a esas 2 llamadas.
- La marca de fallida es por ítem: una tensión envenenada bloquea las demás evaluaciones del mismo paper. `AGENT_ERROR` y `TIMEOUT` no marcan y se reintentan la noche siguiente, dentro del tope.
- Una tensión redactada en una noche que acaba sin Editor queda redactada y no vuelve a ofrecerse, como los hallazgos de medida de T89.
- El Editor solo ve el título y el nivel curioso; los niveles aficionado y técnico no los revisa nadie antes de publicar. Las cifras fiables están en el dato estructurado, que la web muestra aparte (ADR 0024).
- Si el código llega a una base sin migrar, el `Finding` choca con el CHECK antiguo después de pagar la llamada; la fase termina en el primer error y la noche queda `partial`. La migración va justo tras el merge.
- `evidence` y el título del ítem llegan al redactor y su texto se publica sin revisión humana: mismo riesgo de inyección que el Popularizer, mitigado por el saneado y la etiqueta.
- Quedan abiertas: tensiones frente a una referencia que no es la por defecto e `incompatible_with_limit` (OD 244, `CatalogTension` v2), la marca por evaluación en vez de por ítem, y validar en Python que el texto no cita cifras ajenas al dato.

## Alternativas descartadas

- **Cruzar dentro de `run-night`**, tras el Reader: adelanta una noche, pero mete red en la ventana y obliga a decidir qué hace la noche si el archivo falla.
- **Opus como redactor**: más caro en el límite semanal; el Editor (Opus) ya revisa después.
- **Deduplicar leyendo el JSON** en vez de enlazar `tension_evaluation_id`: sin garantía en la base.
- **Columna de fallida en `TensionEvaluation`**: otra migración para un caso que la marca por ítem cubre.
- **Reintentar siempre**: una salida siempre inválida gastaría la reserva cada noche.
- **Publicar frente a referencias ambiguas**: pueden ser el mismo trabajo en su versión de revista.
