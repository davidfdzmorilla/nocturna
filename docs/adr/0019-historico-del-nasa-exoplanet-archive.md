# ADR 0019 · Histórico semanal del NASA Exoplanet Archive

Fecha: 2026-10-01 · Estado: aceptado · **Abre la segunda vía de la fase 2** (decisión del autor del 2026-10-01 tras el estudio de viabilidad de la opción 3(b)) · El criterio de cierre de fase 2 que supersede a ADR 0012 pasa a ADR 0020 (T86) · Tarea: T81

## Contexto

El estudio de viabilidad de la opción 3(b) (2026-10-01, cero tokens) midió que el NASA Exoplanet Archive publica unas 16 filas nuevas y unos 6,5 cambios de solución por defecto por semana, en actualizaciones irregulares de los jueves. `ps` tiene 40.188 filas sin identificador estable: (`pl_name`, `pl_refname`) no es único, `rowupdate` no es fiable y no cambia cuando cambia la solución por defecto, y las bajas solo se ven comparando con un histórico propio. El autor decidió incorporar la vía del archivo como segunda vía de la fase 2, unida a la de arXiv por `arxiv_id`, y arrancar cuanto antes un histórico semanal.

## Decisión

1. **Subcomando y lanzador propios**: `nocturna archive-snapshot [--full] [--dry-run]`, fuera de `run-night`: no hay `Run`, ni `BudgetGuard`, ni llamadas a Claude, ni ventana. Lo programa un agente de launchd propio (`com.nocturna.archive-snapshot`, viernes a las 10:00 Europe/Madrid, después de la actualización de los jueves en EE. UU.) con su envoltorio `archive-snapshot-scheduled.sh`. `run-night-scheduled.sh` no cambia.
2. **Completo o incremental**: completo si no hay ningún snapshot completo en el mes natural en curso (zona `window.timezone`) o con `--full`; el primero es la carga inicial. Incremental: filas con `releasedate >=` la máxima vista, todas las soluciones por defecto, y todas las filas de los planetas afectados (filas nuevas, solución por defecto cambiada o perdida) en lotes; si los lotes no caben en el techo de peticiones, salta a completo. 29 columnas; `rowupdate` no se usa.
3. **Identidad de la solución, `solution_key` v1**: sha256 en hex de `pl_name | ref_key | soltype | 12 valores` (valor, err1, err2 y límite de masa, radio y periodo), en orden fijo. Floats con `float()` y `repr()` (`1.50`, `1.5` y `1.5e0` dan la misma clave; `-0.0` pasa a `0.0`), nulos como cadena vacía. `ref_key` es el bibcode de ADS del enlace de `pl_refname` o, si no hay, el texto de la referencia normalizado: no el HTML en crudo, para que un retoque del HTML del archivo no cambie las claves (decisión del autor). Un dato inválido hace fallar el snapshot entero. Clave dorada fijada en los tests (V1298 Tau b, David et al. 2019). Los duplicados exactos se fusionan y se cuentan.
4. **Diferencias entre snapshots** (regla pura de dominio): altas, bajas (`removed_at`), reactivaciones, cambios de solución por defecto (`archive_default_change`, con la clave anterior nula si el planeta es nuevo) y planetas que pierden su solución por defecto. En incremental, las bajas solo cuentan dentro de los planetas afectados (los únicos de los que se tienen todas las filas); la comparación de altas se hace contra todas las claves activas. El primer snapshot no registra cambios de solución por defecto.
5. **Guarda de cambios masivos**: si un snapshot daría de baja más del 10 % (`max_change_fraction`) de las claves activas, o cambiaría o perdería más del 10 % de las soluciones por defecto, se aborta sin escribir nada. No aplica al primer snapshot. Protege el histórico frente a un cambio de formato del archivo un viernes sin nadie mirando.
6. **Persistencia**: tablas `archive_snapshot`, `archive_solution` (PK `solution_key`, `first_seen`/`last_seen`, `is_default_current`, `removed_at`; índice único parcial: como mucho una solución por defecto vigente por planeta) y `archive_default_change`; migración `b4d7f1a26c93`. Escritura atómica en una sola transacción, upsert por bloques, sin reintentos (ADR 0016 §2): si falla, código 1 y el incremental siguiente recupera.
7. **Tamaño y techo**: el snapshot crea su propio cliente con techo de 6 peticiones y tope de respuesta de 64 MiB (`[sources.exoplanet_archive.snapshot]`, sin valores por defecto en código); el catálogo de T74 conserva sus 20 MB y su `max_requests_per_night`, y el validador de tiempo de ingesta no cambia.
8. **`--dry-run`** hace las peticiones reales y calcula las diferencias, pero no escribe nada (a diferencia de `run-night --dry-run`).
9. **Qué no hace**: no calcula tensiones, no crea `Item` ni `Finding`, no enlaza con arXiv (guarda `arxiv_id`, no lo cruza) y no toca `run-night` ni `budget.py`. Eso es T83–T85.

## Evidencia

- Captura real del 2026-10-01 (3 peticiones): 40.188 filas sin ningún nulo en `releasedate` ni `soltype`; `releasedate` en `YYYY-MM-DD` y comparable como fecha en ADQL; `pl_pubdate` en `YYYY-MM`; 32 filas publicadas en los últimos 7 días (11,7 KB).
- Carga sintética de 40.000 soluciones: 3,4 s el primer snapshot y 3,5 s el upsert completo.
- 19.995 doubles aleatorios y casos límite vuelven de PostgreSQL con el mismo `repr`, así que la clave recalculada al leer coincide con la guardada (depende de `extra_float_digits >= 1`, el valor por defecto desde PostgreSQL 12).

## Consecuencias

- Un preprint que el archivo pasa a citar con bibcode de revista cambia de clave y aparece como una baja y un alta.
- Las filas publicadas con una `releasedate` anterior a la máxima vista solo se ven en el completo mensual.
- El código nuevo con la base sin migrar no afecta a `run-night` ni a la API (no consultan las tablas nuevas); `archive-snapshot` sí falla. El código viejo con la base migrada ignora las tablas.
- El lanzador duplica la lógica de comprobaciones previas de `run-night-scheduled.sh` (deuda registrada).

## Alternativas descartadas

- **Dentro de `run-night`**: un fallo del archivo afectaría al `Run` y obligaría a cambiar el validador de tiempo de ingesta.
- **`pl_refname` en crudo en la clave**: cambiaría todas las claves con un retoque del HTML.
- **Paginar el volcado completo**: con 29 columnas ronda los 15 MB; basta un tope de respuesta configurable.
- **Reintentos automáticos**: el incremental siguiente ya recupera.
