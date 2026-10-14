# ADR 0029 · Tensiones entre soluciones del archivo

Fecha: 2026-10-14 · Estado: aceptado · Tarea: T85 · Relacionados: ADR 0012 (fase 2), ADR 0015 (σ), ADR 0016 (masa solo `Mass`), ADR 0017 (`catalog_tension`), ADR 0019 (histórico del archivo), ADR 0020 (regla de periodo), ADR 0024 (contrato público), ADR 0025 (reserva del redactor), ADR 0026 (redactor de tensiones), ADR 0028 (resumen semanal)

## Contexto

Las decisiones del autor del 2026-10-01 (estudio de viabilidad 3(b)) piden una segunda fuente de tensiones: dos soluciones del propio NASA Exoplanet Archive para el mismo planeta que no son compatibles. El estudio no está en el repositorio. Sus reglas solo se nombran en la tarea (1–3, 5, 6 y 7) y en OD:238. La regla 4 no aparece en ningún sitio, y las reglas 3 y 6 no tienen definición operativa.

Datos reales (base, 2026-10-14, solo lectura):

- 4.276 planetas tienen al menos dos referencias `Published Confirmed` distintas.
- Todas contra todas hay 2.347 pares ≥ 3σ en periodo, 531 en radio y 214 en masa. Compararlo todo inundaría al redactor.
- Con altas desde el 2026-08-01 frente a otra solución, hay 16 pares ≥ 3σ con la solución por defecto (12 de periodo y 4 de masa).
- Cada semana entran de 1 a 8 altas frente a la solución por defecto, y como mucho una de ellas pasa de 3σ.
- 28 de las 192 altas desde el 2026-08-01 tienen otra fila del mismo paper para el mismo planeta.
- HD 202206 c, el caso de la vía que pide T86: Cao et al. 2026 (solución por defecto, alta del 2026-08-20) frente a Benedict & Harrison 2017 da 8,2σ en masa y 3,46σ en periodo.

## Decisión

1. **Partición** (D1). T85 calcula y guarda, sin tokens. T85.e redacta y edita. T85.w las muestra en la web. Así la cola real se mide antes de gastar, y no se publica un tipo que la web no sabe pintar.
2. **Cálculo** (D2). Va al final de `archive-snapshot`, en otra unidad de trabajo tras `evaluate-tensions`; un fallo da el código 3 de siempre y el snapshot se conserva. También está el comando `nocturna solution-tensions [--dry-run]`, que sale con 0 o 1 y nunca con 3. `run-night --dry-run` añade la sección "Tensiones entre soluciones", que solo lista los pares aún no calculados.
3. **Qué se compara** (D4). Cada alta `Published Confirmed` activa con `releasedate >= [solution_tensions] since` (2026-08-01, que incluye HD 202206 c) contra una sola referencia, con el σ frente al resto de soluciones `Published Confirmed` utilizables como contexto (`others`). Las 40.000 soluciones anteriores nunca se comparan entre sí. El cálculo es idempotente por `(new_solution_key, parameter)`.
4. **Referencia** (D5, ampliada por el autor al revisar). Nunca es una fila del mismo paper que la alta: ni el mismo `ref_key` ni, si los dos lo traen, el mismo `arxiv_id`.
   - Si la alta no es la solución por defecto, la referencia es la solución por defecto vigente.
   - Si la alta es la solución por defecto, o la solución por defecto vigente es del mismo paper, la referencia es la `Published Confirmed` utilizable más reciente de otro paper, con el orden de T88: `pl_pubdate`, después `releasedate`, después `solution_key`.
   - Se guarda `reference_role` (`default` o `most_recent`).
   - Sin candidata, o si la alta no tiene un valor utilizable en ese parámetro, no se guarda fila; solo cuenta en el informe (`no_reference`).
5. **Filtros** (motivo de exclusión guardado, en este orden):
   - **Regla 1** (`not_published_confirmed`): las dos soluciones son `Published Confirmed`.
   - **Regla 2** (`same_reference`, D7): `ref_key` distinto y, si los dos lo traen, `arxiv_id` distinto. Tras el punto 4 solo queda como defensa.
   - **Regla 3** (`old_paper`, D6): se excluye la alta si su `pl_pubdate` es más de `max_pubdate_age_months` (24) meses anterior a su `releasedate`. Un `pl_pubdate` nulo o ilegible cuenta como reciente. Solo se aplica a la alta.
   - **TTV** (`period_ttv`, D8): con `ttv_flag` en cualquiera de las dos, el periodo no se compara.
   - **Umbral** (`below_threshold`): `[tension] threshold_sigma`.
   - **Regla 5** (`period_min_difference`, `period_alias`, D8): `[tension.period]`, como en T88.
   - La masa solo se compara con `pl_bmassprov = 'Mass'` (ADR 0016). Los valores llevan errores positivos y no son límites.
   - La σ es la de ADR 0015: el error de cada lado que mira al otro valor (`domain/tension.py::pairwise_sigma`, compartida con `compare`).
6. **Etiquetas, no descartes** (D9, D10).
   - `model_dependent` (regla 6, definición operativa): el paper de la alta o el de la referencia aporta más de una fila activa para el planeta.
   - `controversial` (regla 7): `pl_controv_flag` en cualquiera de las dos.
   - Las verán el Editor y el redactor en T85.e.
7. **Persistencia** (D11). Tabla `solution_tension` (migración `b6d2f8a4c917`), una foto fija por (alta, parámetro) sin estados, incluidas las excluidas con su motivo. Tiene CHECK de parámetro, rol y motivo; `sigma` NOT NULL; e `item_id` solo si es elegible. `TensionEvaluation` no se reutiliza: exige una `Reading` y medidas del Reader.
8. **Item del archivo** (D12). Se crea solo para altas con algún par elegible, con `get-or-create` por `(source, external_id)`. Lleva `source = 'exoplanet_archive'`, `external_id = solution_key` de la alta, título "{planeta}: {referencia}", abstract de plantilla, `published_at` = `releasedate` a las 00:00 en `window.timezone` y estado `new` para siempre: `Item.status` sigue solo el camino `paper_explained` (ADR 0017).
9. **Tipo de `Finding`** (D13). `solution_tension`, con el dato `SolutionTensionData` (JSONB `schema_version` 1) y `solution_tension_id`, informados si y solo si el tipo es el suyo, con índice único `(solution_tension_id, type)`. En T85 nadie crea estos hallazgos. El filtro `?type=` de la API sigue aceptando solo los cuatro tipos publicados (`FindingTypeFilter`) hasta T85.e.
10. **Guardas de gasto.** Un ítem del archivo nunca llega a un agente:
    - `next_unread` solo devuelve `source = 'arxiv'` (contrato del puerto, también en el fake).
    - `run-item` y `run-item --reader v3 --force` salen con 10 antes de abrir un `Run`.
    - `ReadItem`, la relectura y `PopularizeReading` lanzan `InvariantViolation` antes de autorizar.
    - La cola de `night_report.sql` y `backfill_exoplanet_match.py` solo cuentan arXiv.
    - El downgrade de la migración se niega si hay ítems de otra fuente, porque el `next_unread` anterior se los daría al Reader.
11. **Configuración**: `[solution_tensions]` con `since`, `max_pubdate_age_months` y `writer_enabled = false`, sin valores por defecto en código. El interruptor (D16) lo usa T85.e y se activa al cerrar T85.w.
12. **Lo que fija ya T85.e**: el redactor actúa dentro de `_phase_writer` con el rol `writer`, el tope de 2 llamadas y la reserva de 24.000 compartidos, primero arXiv y después el archivo (D3). Usa el prompt nuevo `writer-archive-v1` y `editor-v4` (D15), y las etiquetas van en la línea `data` del Editor (D14).

## Consecuencias

- No cambia el gasto de la noche: `budget.py` y `test_llm_call_sites.py` no cambian, y el cálculo no recibe proveedor, guard ni runner.
- Con el código nuevo y la base sin migrar, el mapeo de `findings` lee columnas que no existen y la noche falla. La migración va el mismo día del merge, antes de las 00:00 y fuera de 00:00–04:45.
- Cola inicial esperada: unas 16 tensiones elegibles, unas 8 noches de redactor en T85.e si arXiv no ocupa el tope.
- Los ítems del archivo quedan en `new` para siempre, fuera de la cola.
- Una solución ya calculada no se recalcula si el archivo la cambia después; una baja deja la fila como foto fija.
- Si la alta no es la solución por defecto y esta no tiene valor utilizable en ese parámetro, no hay referencia. Queda abierto si se cae a la más reciente de otro paper.

## Alternativas descartadas

- **Todas contra todas**: miles de pares ≥ 3σ antiguos, una avalancha para el redactor.
- **Solo la solución por defecto anterior según `archive_default_change`**: no existe antes del 2026-10-02 y deja fuera HD 202206 c.
- **Reutilizar `TensionEvaluation` o `catalog_tension`**: rompe sus invariantes y el contrato de ADR 0024.
- **Calcular en `run-night`**: los datos solo cambian con el snapshot de los viernes.
- **Una sola tarea con redactor y web**: gastaría antes de medir la cola y publicaría un tipo que la web no conoce.
