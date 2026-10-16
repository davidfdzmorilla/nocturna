# ADR 0032 · Suelo y `since` de la cosecha OAI

Fecha: 2026-10-16 · Estado: aceptado · Tarea: T93 · Relacionados: ADR 0010

## Contexto

ADR 0010 llevó la ingesta a OAI-PMH y fijó dos reglas que se han mostrado insuficientes:

- **Suelo del filtro fino a `from_ − 2 días`** (`_DATESTAMP_LAG_DAYS = 2`). Supone que el lote con `datestamp` D solo trae envíos de `(D−2 18:00, D−1 18:00]` UTC. Es cierto de martes a viernes, pero no para los lotes del lunes ni del martes:
  - el del lunes trae `(jue 18:00, vie 18:00]`;
  - el del martes trae `(vie 18:00, lun 18:00]`.

  El desfase llega a 4 días y 6 horas, y más con un festivo de arXiv.
- **`since` por defecto igual a ayer a las 00:00 UTC.** Tras una noche sin ejecutar o con la ingesta fallida, los lotes intermedios no se piden nunca.

Datos de la base real (consulta de solo lectura, 2026-10-15):

- **Pérdida semanal.** Desde el paso a OAI, los envíos del jueves y del viernes a partir de las 18:00 UTC son **cero todas las semanas** (24 y 25 de septiembre, 1, 2 y 8 de octubre). Los demás días tienen entre 10 y 25 envíos de tarde. Se perdían del orden de 15 a 25 papers por semana en EP y GA.
- **Hueco del corte de luz.** No hubo noches el 11, 12 y 13 de octubre. La del 14 cosechó los `datestamp` del 12 al 13 con suelo el 10 y descartó los 56 registros. La del 15 falló por un timeout de OAI. Faltan en la base los envíos desde el jueves 8 a las 18:00 hasta el domingo 11.
- **Visibilidad del lote.** El lote con `datestamp` D ya se ve en OAI la noche U = D (22:05 UTC). Es la hipótesis H2 del plan: la forma de la pérdida, jueves y viernes de tarde, encaja con ella y no con H1, que pierde el lunes entero.

## Decisión

1. **Suelo a `from_ − 7 días`** (`_DATESTAMP_LAG_DAYS = 7` en `infrastructure/arxiv/oai_client.py`, D1). Cubre el fin de semana y una semana con festivo.
   - El suelo sigue existiendo para descartar las reediciones de papers antiguos que arrastra la cosecha: el `datestamp` es de modificación, no de envío.
   - Las revisiones y cross-lists de la última semana que entren de más las absorbe `add_many` con `ON CONFLICT DO NOTHING`.
2. **`since` por defecto desde la última ingesta correcta** (D2), calculado por la función pura `application/use_cases/ingest_arxiv.py::default_since`:

   ```
   since = 00:00 UTC de max(min(ayer, marca), hoy − max_catchup_days)
   ```

   - **La marca** es `max(items.fetched_at)` de las filas con `source = 'arxiv'`, que lee `ItemRepository.last_fetched_at(source)`.
     - Filtrar por `source` es obligatorio, porque los `Item` de `exoplanet_archive` (T85) también tienen `fetched_at`.
     - No se leen las notas de `runs`.
     - La marca solo avanza cuando se guarda algún ítem: no la mueven ni una ingesta correcta sin ítems nuevos ni un `--dry-run`, que deshace la transacción.
     - Sin marca, `since` vale ayer.
   - **`[sources.arxiv] max_catchup_days = 7`** (`ge=1, le=14`) acota cuánto se recupera sola una noche. Si la marca es anterior, el log `arxiv.ingest_since` lo señala con `capped=true`, y para ir más atrás queda `--since` a mano.
   - De martes a viernes `since` sigue siendo ayer, así que la ventana y el número de peticiones no cambian. La noche del sábado repite el lote del viernes y no guarda nada, así que la marca no avanza y las del domingo y el lunes piden desde el viernes; lo mismo tras un festivo de arXiv. Es inocuo: los duplicados los absorbe `ON CONFLICT` y las peticiones siguen acotadas por `max_requests_per_fetch`.
   - `--since` explícito manda y no consulta la marca.
   - El `--dry-run` imprime el `since` usado y su origen.
3. **`max_results_per_fetch` de 400 a 800** (D4). Una recuperación de cinco lotes de dos categorías ronda los 300–370 registros: el `--dry-run` con `--since 2026-10-08` devolvió 366 en 43 s.
   - Es un límite de cortesía y de memoria, no de gasto: `max_items_per_night` sigue limitando la lectura.
   - El techo de peticiones (`max_requests_per_fetch = 6`) no cambia.

## Consecuencias

- Se recuperan cada semana los envíos del jueves y del viernes de tarde, que entran por la cola normal.
- Un hueco de hasta 7 días se recupera solo la noche siguiente. Más allá, el log lo marca y la recuperación es manual.
- Un truncado durante la recuperación sigue avanzando la marca y pierde lo cortado (D4 c, descartado por ahora: exigiría una marca distinta de `fetched_at`).
- Los envíos del 8 al 11 de octubre quedaron fuera de la recuperación automática: la noche del 2026-10-16 corrió con el código anterior y avanzó la marca al 15. Se recuperan con una noche única con `--since 2026-10-08` (D3 a, la del 2026-10-17).

## Alternativas descartadas

- **Suelo de 4 días**: es exacto para una semana normal, pero se queda corto con un festivo de arXiv.
- **Suelo por registro a partir de su `datestamp`**: es más preciso, pero obliga a cambiar el DTO `ArxivEntry` y las dos vías de ingesta.
- **Marca a partir de las notas de `runs`**: obliga a interpretar texto libre, y una ingesta correcta sin ítems avanzaría la marca.
- **Solo recuperación manual con `--since`**: el hueco del corte de luz muestra que nadie se acuerda a tiempo.
