# ADR 0037 · Un planeta que falla no tumba `evaluate-tensions`

Fecha: 2026-10-26 · Estado: aceptado · Tarea: T99 · Relacionados: ADR 0016 §2, ADR 0020 §3, ADR 0019

## Contexto

Desde la noche del 2026-10-21 al 22, `evaluate-tensions` falla todas las noches (código 1, tras `run-night`) y dentro del snapshot del viernes 2026-10-23 (código 3), con `ExoplanetArchiveUnavailable: el Exoplanet Archive no completó la respuesta en 30 s`.

El servicio de alias del archivo (`nph-aliaslookup.py`) se cuelga siempre con ciertos nombres (TOI-210 b, TIC 402710280 b, OGLE-2006-BLG-284 b, RX J0534.0-0221 b, TOI-6981 b: más de 35 s en cada intento) y responde en unos 3 s con otros (TOI-2211 b). El TAP responde con normalidad.

ADR 0016 §2 dice que un fallo del adaptador «es `ExoplanetArchiveUnavailable` y se propaga». ADR 0020 §3 aísla por planeta los fallos de resolución, pero solo cuando el alias responde algo anómalo. Si el alias no responde, el error sube hasta la CLI y un solo nombre colgado deja sin evaluar a todos los demás. La última evaluación escrita en la base real es del 2026-10-20 a las 22:15.

Desde T88, `evaluate-tensions` lee las soluciones previas de la base; la única red que usa es el alias.

## Decisión

Decisión del autor (T99): D1–D9 con las recomendaciones del plan. Modifica ADR 0016 §2 y precisa ADR 0020 §3, que no se editan.

1. **El alias que no responde es un fallo de resolución de ese planeta.** En `ExoplanetArchiveCatalog.resolve_planet`, una `ExoplanetArchiveUnavailable` de `lookup_alias` (timeout, HTTP ≠ 200, error de red, JSON inválido, o el techo de peticiones, `ArchiveRequestCapReached`) se convierte en `PlanetResolutionFailed`, con la causa encadenada. La traducción la hace el adaptador; el puerto y `ComputeTensions` no cambian.
2. **Lo que sigue abortando.** El índice local vacío (fuera de la traducción), las `InvariantViolation` y los errores de base siguen abortando la ejecución entera.
3. **Sin caché del fallo.** El fallo nunca se guarda como «ausente». Un planeta que falla no deja `TensionEvaluation` y se reintenta en la pasada siguiente, por el mismo mecanismo de ADR 0020 §3.
4. **Memoria dentro de una ejecución.** Un nombre cuyo alias no respondió no se vuelve a pedir en esa ejecución (misma instancia del catálogo). Las respuestas con estado anómalo no se memorizan. Nada se guarda entre ejecuciones.
5. **Sin reintento y sin cambiar el timeout de 30 s.** Los nombres colgados cuelgan en cada intento. El tiempo queda acotado por el techo de 40 peticiones.
6. **Códigos de salida.**
   - `evaluate-tensions` (y `--dry-run`): 0 sin fallos; **4** si guardó el resto y algún planeta quedó sin resolver; 1 solo si aborta.
   - `archive-snapshot`: **4** con fallos parciales de la evaluación; 3 si la evaluación o las tensiones entre soluciones lanzan una excepción. El 3 prevalece sobre el 4. El snapshot queda guardado en los dos casos.
   - `run-night --dry-run`: sin cambio, sale con 0 y lista los fallos.
   - El 4 de `run-night` (código heredado de `run-item`) es otra cosa: `run-night-scheduled.sh` distingue por cuál de las dos órdenes lo devuelve.
7. **Registro.**
   - Un `WARNING` `tension.resolution_failed` por planeta distinto, con `planet_name`, `parameters` y `reason`.
   - Un `INFO` `tension.evaluation_summary` en cada ejecución, con `evaluations_created`, `reevaluated`, `unchanged`, `kept_terminal`, `failed_groups` y `failed_planets`.
   - El informe de la CLI dice «FALLOS de resolución (sin fila, se reintentan): N grupos, M planetas».
   - `run-night-scheduled.sh` escribe «evaluate-tensions terminó con planetas sin resolver (código 4)»; `archive-snapshot-scheduled.sh`, un aviso propio para el 4.

## Consecuencias

- Los planetas cuyo alias responde se evalúan aunque otros se cuelguen. Con los datos del 2026-10-26: TOI-2211 b y c se evalúan y los otros 5 planetas quedan como fallo con código 4.
- Cada noche se siguen pagando unos 30 s por nombre colgado (hoy unos 5, ≈ 2,5 min). Con 40 nombres colgados serían unos 21 min, fuera de la ventana de gasto porque `evaluate-tensions` no usa tokens.
- Un fallo transitorio y uno permanente siguen sin distinguirse (decisión abierta T89, «Fallos permanentes de resolución»), aunque ahora un parcial ya no se confunde con un aborto.
- Sin migración, sin configuración nueva y sin efecto en el control de gasto.

## Alternativas descartadas

- **Aislar por ítem:** perdería los otros planetas del mismo paper.
- **Error de dominio nuevo `CatalogUnavailable` en el puerto:** más cambio para el mismo resultado.
- **Reintento o timeout menor:** los nombres colgados cuelgan siempre; reintentar dobla el coste, y no hay datos de si responden pasados los 40 s.
- **Salir con 0 o con 1 en los fallos parciales:** el 0 oculta el fallo en el informe de la mañana; el 1 lo confunde con un aborto.
- **Caché persistente del alias:** necesitaría tabla; queda en la decisión abierta T88.
