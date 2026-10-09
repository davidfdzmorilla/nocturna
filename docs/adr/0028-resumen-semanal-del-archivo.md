# ADR 0028 · Resumen semanal de cambios de solución por defecto

Fecha: 2026-10-09 · Estado: aceptado · Tarea: T84 · Relacionados: ADR 0012 (fase 2), ADR 0019 (histórico del archivo), ADR 0024 (contrato público), ADR 0025 (reserva del redactor)

## Contexto

Las decisiones del autor del 2026-10-01 (estudio de viabilidad 3(b)) piden un producto semanal fijo sobre el NASA Exoplanet Archive: qué planetas cambian de solución por defecto, con qué paper y qué parámetros cambian. El snapshot de los viernes (T81) ya guarda cada transición en `archive_default_change`, salvo los planetas que pierden su solución por defecto, que solo quedaban en el log (OD de T81/T84).

Datos reales (base, 2026-10-09):

- Una fila con `old_solution_key` nulo no es un cambio: el planeta no tenía solución por defecto antes del snapshot (nuevo en el archivo o que la recupera).
- Snapshot 2026-10-02: 1 cambio (TOI-2427 b) y 3 planetas nuevos. Snapshot 2026-10-09: 0 cambios y 70 planetas nuevos (todos con `releasedate` 2026-10-08, confirmados por tránsito). Ninguna pérdida en los logs.
- El estudio 3(b) no está en el repositorio. Su cifra de ~6,5 cambios por semana no se reproduce si solo se cuentan los cambios.

## Decisión

1. **Sin LLM** (D1 c). El resumen es determinista y no gasta tokens. El redactor sobre el resumen queda para una tarea aparte (T84.r), con la pregunta abierta de dónde corre.
2. **Modelo de lectura, no `Finding`** (D2 a). El resumen se calcula al leer, a partir de `archive_default_change` y `archive_solution`; no hay tabla de resumen. Lo publicado deja de ser solo `Finding`: la API expone `GET /archive/weeks` y `GET /archive/weeks/{week}` (`422` si el formato no vale, `404` si la semana no tiene snapshot), con esquemas por lista blanca como ADR 0024 y sin `solution_key` ni metadatos internos. La web (T84.w) tendrá su página.
3. **Sin Editor** (D3 a). Son datos del archivo con plantillas, sin texto de IA; el resumen es visible en cuanto se guarda el snapshot. La guarda de cambios masivos del 10 % (ADR 0019) protege de una respuesta rota.
4. **Cuatro secciones** (D4 b), derivadas al leer en `domain/archive_digest.py::classify_transition`:
   - `changed`: tenía solución por defecto y pasa a otra (la principal), con los cambios de masa, radio y periodo (valor, errores, cota y, en masa, procedencia `Msini`/`Mass`), comparados con la misma canonización que `solution_key`.
   - `new_planet`: sin solución por defecto previa y sin ninguna solución vista en un snapshot anterior.
   - `regained`: sin solución por defecto previa, pero el planeta ya se había visto en un snapshot anterior. Es una definición operativa: incluye también un planeta que solo tenía soluciones no por defecto y gana la primera, caso que el archivo no produce de forma normal.
   - `lost`: pierde su solución por defecto sin sustituta.
   Un renombrado se ve como un planeta nuevo más una pérdida; no se emparejan.
5. **Fila de pérdida** (D5 a). Migración `d8b1e4f7a203`: `archive_default_change.new_solution_key` admite nulos, con `CHECK (num_nonnulls(old_solution_key, new_solution_key) >= 1)`. `save_snapshot` escribe, en la misma transacción, una fila por planeta perdido con su solución por defecto anterior y sin nueva; si no la encuentra, el snapshot falla entero. `diff_snapshot` y la guarda no cambian. El downgrade se niega si hay filas de pérdida.
6. **Semana ISO** (D6 a) de lunes a domingo en `window.timezone`, intervalo `[inicio, fin)` en hora local, con todos los snapshots de la semana; las transiciones de un mismo planeta salen todas, en orden.
7. **Unidades** (D7) como en T91; el cambio de procedencia de la masa se indica aparte y cuenta como cambio aunque el número no cambie.
8. **Consulta local**: `nocturna archive-digest [--week YYYY-Www]`, de solo lectura y sin red; por defecto, la última semana con snapshot.

## Consecuencias

- No cambia el gasto: ni `budget.py`, ni `run-night`, ni el único camino a Claude.
- La API lee por primera vez `pipeline.toml`, solo `window.timezone` y solo en `/archive/*`; si falta o no valida, esas rutas dan `500` y el resto sigue funcionando.
- Con los datos actuales, la sección principal estará casi siempre vacía y la de planetas nuevos puede ser larga (70 en una semana). La forma de mostrarla se decide en T84.w.
- Las pérdidas anteriores a la migración no se reconstruyen; los logs del 2026-10-02 y 2026-10-09 dicen que no hubo ninguna.
- Tras una caída total de soluciones por defecto no se registrarían transiciones (`diff_snapshot`), pero la guarda del 10 % aborta antes ese snapshot.
- `ListDigestWeeks` hace una consulta por semana; aceptable con un snapshot semanal.
- El código nuevo sobre una base sin migrar hace fallar entero el snapshot con una pérdida; la migración va el mismo día del merge y antes del viernes a las 10:00.

## Alternativas descartadas

- **Un `Finding` por semana o por cambio**: necesita un `Item` que no encaja con un snapshot, pasa por el Editor y gasta tokens; con 70 entradas rompería su reserva.
- **Redactor dentro de `run-night` o en un `Run` propio**: compite con las tensiones por la reserva y el tope del redactor, o crea un segundo `Run` en la ventana. Se decide en T84.r con datos.
- **Solo la sección de cambios**: casi siempre vacía.
- **Tabla aparte para las pérdidas o columna `kind`**: el tipo se deriva al leer y cubre las filas existentes sin rellenarlas.
- **Aprobación del autor por semana**: columna o tabla nueva para datos que no son texto de IA.
