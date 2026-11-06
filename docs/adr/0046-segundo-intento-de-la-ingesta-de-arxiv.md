# ADR 0046 · Segundo intento de la ingesta de arXiv dentro de la ventana

Fecha: 2026-11-06 · Estado: aceptado · Tarea: T106 · Relacionados: ADR 0010, 0032, 0044, 0045

## Contexto

El reintento del adaptador (T60.b, `infrastructure/arxiv/retry.py`) hace 4 intentos, con base de 5 s, `retry_max_elapsed_s = 60` y 30 s de timeout HTTP por petición. Cuando el OAI cuelga, el reintento se agota en ~65 s.

Seis noches terminaron con `ArxivUnavailable` en la ingesta: 2026-10-15, 10-23, 10-24, 10-31, 11-01 y 11-05. En casi todas se agotó `max_elapsed` a los 63–67 s, con 2 intentos por `request_deadline`.

La ingesta fallida termina hacia las 00:06, con ~4 h 30 min de ventana libre. En las seis noches se leyó la cola y se ejecutó el Editor. La noche siguiente recuperó los papers por la marca de ADR 0032.

## Decisión

1. **Un único segundo intento, en `RunNight`** (`application/use_cases/run_night.py`), no en el adaptador. Se hace tras `ingest_retry_delay_s = 900` s de espera, antes de la caducidad (ADR 0033) y del Reader.
2. **Solo si cabe:** `deadline_s > 0` y quedan al menos `ingest_retry_min_remaining_s = 3600` s de ventana tras la espera. Si no, se salta (`skipped:window`) sin dormir.
3. `ingest_retry_delay_s = 0` desactiva el segundo intento (`skipped:disabled`).
4. **Cancelable por el vigía** de `hard_stop` y `run_timeout_s`: la espera y el segundo intento pueden cortarse y la noche cierra `KILLED`. Se reintenta ante cualquier `Exception` y no se captura `asyncio.CancelledError`. Nunca hay más de dos intentos.
5. Los ítems del segundo intento se leen esa noche con el mismo `limits.max_items_per_night` y con `BudgetGuard`. No hay llamadas nuevas a agentes. `budget.py` no cambia.
6. **Estado de la noche (D1):** `COMPLETED` si el segundo intento va bien. `PARTIAL` con `end=ingest_error` si falla o se salta.
7. **`notes` (D3):** segmento `ingest_retry=ok:<Clase>|error:<Clase>|skipped:window|skipped:disabled`, justo tras `ingest=`, solo si el primer intento falló. Sin fallo, `notes` no cambia. Se mantiene un único `end=`: la ingesta fallida va en `ingest=` y `classify_night` la añade como causa secundaria.
8. **Informe matinal (D4):** si la ingesta se recuperó, muestra «Ingesta recuperada en el segundo intento (<Clase>)» (`morning_report.py`).
9. **Validación al cargar (`PipelineConfig`):**
   - `ingest_retry_delay_s + ingest_retry_min_remaining_s <= limits.run_timeout_s`.
   - El peor caso de la ingesta sigue dentro del 25 % de `run_timeout_s`: 558 (arXiv) + 1.280 (Exoplanet Archive) + 900 (espera) + 558 (segundo intento de arXiv) = 3.296 s ≤ 4.050 s.
   - Efecto: el máximo que admite el validador para `exoplanet_archive.max_requests_per_night` baja de 109 a 63. El valor configurado (40) no cambia.

## Alternativas descartadas

- **Reintento en el adaptador.** No conoce la ventana ni `hard_stop`, no puede comprobar el margen y sigue acotado a 60 s por `retry_max_elapsed_s`. Los episodios de CDN observados duran más (CALIBRACION, Paso 1.5).
- **Varios intentos.** No hay datos de cuánto tarda el OAI en volver (D2). Con un único intento, el coste en ventana queda acotado.
- **Segundo intento tras el Editor.** La noche ya ha publicado sin los papers nuevos, y el Editor no se puede repetir (una vez por noche).
- **`partial` siempre, sin segundo intento.** Deja la noche sin recuperar aunque haya ventana libre.
- **`completed` con nota.** Esconde en el estado una ingesta fallida que la revisión de la mañana debe ver.
- **Segmento `degraded=`.** Otra clave que el parser y el informe tendrían que leer. `ingest_retry=` ya lleva el dato.

## Consecuencias

- **Gasto:** 0 tokens adicionales. `budget.py` y `nightly_tokens` no cambian. Revisión `budget-guard-review` aprobada (ciclo 1).
- **Ventana:** la espera de 900 s cuenta contra `run_timeout_s`. Si el Mac se duerme o `hard_stop` llega antes, el vigía corta.
- **Datos:** no se sabe cuánto tarda el OAI en volver. `night.ingest_retry` lo medirá (CALIBRACION, Paso 1.5). Si el segundo intento falla siempre, hay que revisar `ingest_retry_delay_s`.
- **Límite conocido:** la cosecha de `fetch_new` es todo o nada. Si falla una categoría, se pierden todas (el 2026-11-01 se perdió astro-ph.EP porque falló astro-ph.GA). El segundo intento repite la cosecha entera. Registrado en `TECHNICAL_DEBT.md`.
- **Lectura de `notes`:** `parse_run_notes` lee el segmento con tolerancia; un valor que no reconoce deja el campo en `None`.
- **ADR 0045:** su consecuencia «abierta para T106» (un solo motivo `end=`) queda resuelta aquí (D3). El ADR 0045 no se edita.
