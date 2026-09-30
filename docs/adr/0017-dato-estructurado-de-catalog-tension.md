# ADR 0017 · Dato estructurado de `catalog_tension` en `Finding`

Fecha: 2026-09-30 · Estado: aceptado · **Concreta ADR 0012 §1 y §13** (tipo nuevo sin entidad nueva; números y enlaces en un campo estructurado de `Finding`) · **Resuelve** las decisiones abiertas de `OPEN_DECISIONS.md` sobre `Item.status` con dos `Finding`, tres niveles o uno, y dónde vive el enlace al registro del archivo · Tarea: T72

## Contexto

ADR 0012 añade a fase 2 un `Finding.type` nuevo, `catalog_tension`, con los números del contraste en un campo estructurado de `Finding`, y deja su forma para T72. ADR 0015 fija qué es un candidato (`TensionResult.is_candidate`) y que un ítem con `interest_score < 4` queda `discarded` aunque tenga tensión. ADR 0016 deja el enlace a la ficha del planeta como función del adaptador (`planet_overview_url`). Quedaban abiertas tres decisiones que el autor tomó al aprobar el plan de T72 (2026-09-30).

## Decisión

1. **Tipo**: `FindingType.CATALOG_TENSION = "catalog_tension"`, ligado a `item_id` como `paper_explained`. Sin entidad nueva.
2. **`Item.status` sigue solo el camino `paper_explained`** (Reader → Popularizer → Editor). Crear, publicar o descartar un `catalog_tension` no cambia el estado del ítem; lo publicado se decide por `Finding.published_at`. Un ítem `discarded` puede tener un `catalog_tension` publicado. No cambia ninguna transición de `Item`.
3. **Tres niveles de texto** (`level_curious`, `level_amateur`, `level_technical`) también para `catalog_tension`. Las invariantes de `level_*`, el NOT NULL de la base, la API y el selector de nivel de la web no cambian.
4. **Campo `Finding.catalog_tension: CatalogTension | None`**, informado si y solo si `type == catalog_tension` (invariante en dominio y CHECK `ck_findings_catalog_tension_iff_type` en la base). `CatalogTension` es un value object congelado con `planet_name` (canónico del archivo), `parameter`, `archive_url` (ficha del planeta), `threshold_sigma`, `reference_sigma` y `comparisons`, cada una `CatalogTensionComparison(paper: Measurement, prior: CatalogSolution, sigma)`. Invariantes: comparaciones no vacías, del mismo parámetro y planeta del archivo; medida del paper `usable_for_tension` y previa `usable_as_prior`; σ finitos y ≥ 0; umbral finito y > 0; exactamente una previa por defecto (por igualdad de valor) y `reference_sigma` igual al σ mínimo frente a ella y ≥ umbral. Un `CatalogTension` solo describe candidatos.
5. **Enlace**: `archive_url` se guarda dentro de `catalog_tension` (foto fija de lo publicado, letra de ADR 0012 §13). Cómo llega a la aplicación lo decide T76.
6. **Construcción**: `catalog_tension_from(result, *, threshold_sigma, archive_url)` en `domain/tension.py`, que falla si el resultado no es candidato. `CatalogSolution` pasa de `domain/catalog.py` a `domain/entities.py` (lo necesita `Finding`) y `catalog.py` la reexporta.
7. **Persistencia**: columna `findings.catalog_tension` JSONB nullable con `none_as_null=True` (un `paper_explained` deja SQL NULL, no `'null'`). El JSON lleva `schema_version: 1`, que solo existe en infraestructura; una versión ausente o distinta es error. `paper` tiene la misma forma que un elemento de `readings.measurements`; enums por valor. No se guardan los errores intermedios en unidad canónica: se recalculan con ADR 0015.
8. **Migración `7c1e4a9b2d35`** (sobre `52ccb04d0f06`), aditiva: columna, CHECK de `finding_type` con los dos valores y CHECK iff. El downgrade falla con `RuntimeError` sin tocar nada si hay filas `catalog_tension`.

## Consecuencias

- `items.status` deja de significar "se publicó algo de este ítem"; los informes deben contar por tipo de `Finding`.
- T76 tiene que adaptar `EditNight`, que hoy indexa decisiones por `item_id`, descarta candidatos cuyo ítem no está `read` y llama a `publish()`/`discard()` del ítem por cada `Finding`; y el prompt del Editor, porque `unpublished_for_run` devolverá los dos tipos. No debe crearse ninguna fila `catalog_tension` antes de eso.
- El código nuevo con la base sin migrar falla (`UndefinedColumn`) al guardar o leer `Finding`, después de gastar tokens del Popularizer. Hay que migrar la base real justo tras el merge y fuera de 00:00–04:45. El código viejo con la base migrada funciona.
- Un JSON corrupto en una fila haría fallar la página del feed que la contiene (deuda registrada).

## Alternativas descartadas

- **`Item.status` = `published` si se publica cualquiera de los dos tipos**: exige `discarded → published` (rompe que `discarded` sea terminal) o retrasar el descarte hasta el Editor (contradice ADR 0015 §5).
- **Estado aparte en `Item` para la tensión**: sobrearquitectura para fase 2.
- **Un solo nivel de texto**: `level_*` nullables según tipo, migración del NOT NULL y API rota hasta T77.
- **Un texto copiado en los tres niveles**: los datos mentirían sobre lo que hay.
- **Derivar el enlace en la API desde `planet_name`**: contradice ADR 0012 §13 y haría depender `api/` del adaptador.
- **Enlace a ADS por solución previa**: aditivo, cabe en un `schema_version` 2 si hace falta.
