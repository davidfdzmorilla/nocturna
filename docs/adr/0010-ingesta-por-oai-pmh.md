# ADR 0010 · La ingesta de arXiv pasa a OAI-PMH

Fecha: 2026-09-25 · Estado: aceptado · **Supersede ADR 0004** (en lo relativo a la vía de ingesta) · **Rectifica ADR 0009** (en una afirmación sobre `retry_max_elapsed_s`, ver § Rectificación)

## Contexto

ADR 0004 (T20) eligió la API pública de arXiv (`https://export.arxiv.org/api/query`, feed Atom) como vía de ingesta. ADR 0009 (T60.b) añadió reintentos ante fallos transitorios, motivado por un 406 observado el 2026-09-21 que parecía puntual.

No lo era. Entre el 21 y el 25 de septiembre hubo **cuatro episodios** de 406 (21, 22, 24 y 25), todos con la misma firma: cuerpo vacío, sin `Retry-After`, cabeceras de Fastly/Varnish. Los reintentos de T60.b **no recuperaron ni uno**: los episodios duraban más que la ventana de reintento (en la noche del 25, cuatro intentos a lo largo de 40,5 s, los cuatro rechazados). El 406 no era transitorio en el sentido que T60.b asumió, sino **episódico y más largo que cualquier ventana de reintento razonable**.

Consecuencia medida: la ingesta dejó de traer ítems durante cuatro días. El pipeline siguió funcionando porque consume una cola de `Item` en estado `NEW`, pero esa cola bajó de 49 a 9 en una sola noche. Dos noches más y las catorce noches de calibración de T60 no habrían medido nada, porque no habría habido trabajo que medir.

Lo que se descartó como causa, probándolo: `User-Agent`, `Accept`, `Accept-Encoding` (con `gzip, deflate`, con `identity` y ausente), versión HTTP (`curl --http1.1` y `--http2` pasaban), codificación de `%3A` frente a `:`, valor de `max_results`, y cliente síncrono frente a asíncrono. Con la misma URL, el mismo `User-Agent`, la misma IP y el mismo instante, `curl` y `urllib` pasaban donde `httpx` no.

## Decisión

**La vía de ingesta por defecto pasa a ser el OAI-PMH de arXiv**, con `metadataPrefix=arXivRaw`, contra `https://oaipmh.arxiv.org/oai`.

Es el endpoint que arXiv documenta para cosecha sistemática y programada, que es exactamente nuestro caso de uso, y vive en otro host: arXiv migró el OAI a dominio propio en marzo de 2025, de modo que `export.arxiv.org/oai2` solo redirige. Verificado el 2026-09-25: respondía `200` con `httpx` en el mismo rato en que `/api/query` seguía devolviendo `406`.

**La vía `api` no se borra.** `config/pipeline.toml` la selecciona con `ingest_via = "oai" | "api"`, y volver atrás es cambiar ese string. Es el único punto de comparación si OAI diera problemas.

### Por qué `arXivRaw` y no `arXiv` ni `oai_dc`

- `oai_dc` da las categorías en prosa (`Astrophysics - Earth and Planetary Astrophysics`) en vez de `astro-ph.EP`, y mete abstract y comentarios en dos `dc:description` indistinguibles. Rompería `Item.categories`.
- `arXiv` trae `<created>` con fecha pero **sin hora**, así que se perdería la hora de envío y con ella el orden dentro de la noche.
- `arXivRaw` es el único que trae el historial de versiones con timestamp RFC-2822 completo, que es exactamente nuestro `published_at`.

### El dominio no cambia

Verificado contra un paper que ya estaba en la base (`2609.24987`) y congelado en `backend/tests/test_oai_atom_equivalence.py`:

| campo | Atom | OAI-PMH `arXivRaw` |
|---|---|---|
| `external_id` | `/abs/2609.24987v1` → `2609.24987` | `oai:arXiv.org:2609.24987` → `2609.24987` |
| `published_at` | `<published>` | `<version version="v1"><date>`, idéntico al segundo |
| `categories` | primaria + resto | `<categories>`, primaria primero |
| `source` | `arxiv` | `arxiv` |

Sin migración, sin columna nueva, sin repasar las filas existentes. El relleno de emergencia del 2026-09-25 lo confirmó en producción: **70 ítems nuevos, 0 duplicados** contra las 92 filas que ya había.

Los dos invariantes que sostienen eso tienen test propio, porque romper cualquiera reingeriría la base entera como ítems nuevos en una sola noche: **`source` sigue siendo `"arxiv"`** con cualquier vía (nada de `"arxiv-oai"`), y **`external_id` sigue sin versión ni prefijo**.

### Diferencia conocida y aceptada: el LaTeX

`/api/query` normaliza algunos comandos LaTeX a Unicode (`\AA` → `Å`, `\mu` → `μ`); `arXivRaw` devuelve el fuente tal cual, que es lo que su nombre anuncia. El contenido es el mismo texto.

Se acepta: el abstract lo consume el Reader (un LLM, que lee igual de bien las dos formas) y la web publica los tres niveles que escribe el Popularizer, no el abstract en crudo. Convertirlo exigiría una dependencia nueva y arriesgaría romper fórmulas; usar el formato `arXiv` costaría la hora de envío. Congelado como diferencia conocida en el test de equivalencia.

## Alternativas rechazadas

**Feeds RSS.** El abstract sí viene completo (comprobado), así que el motivo no es ese: **no tienen rango temporal**. Solo sirven el ciclo de anuncios más reciente, de modo que una noche perdida no se recupera jamás — la misma enfermedad que nos trajo aquí. Además `pubDate` es del canal, no del paper, así que `published_at` pasaría de hora de envío a fecha de anuncio, cambiando el significado de una columna con datos ya dentro. Queda como plan B documentado.

**Cambiar de biblioteca HTTP, imitar la huella TLS de `curl`, falsear cabeceras de navegador, rotar el `User-Agent` o usar proxies.** Rechazado, y conviene dejar escrito el razonamiento: la única hipótesis que queda en pie es que el CDN discrimina por huella de cliente. Si es así, **elegir un cliente por no tener esa huella es evadir la protección, aunque la biblioteca sea de la estándar y no se falsee ni una cabecera**. `urllib` pasaba; usarlo por eso habría sido exactamente eso.

La puerta correcta no es otra llave, es otra puerta, y arXiv la tiene señalizada. Si OAI-PMH también fallara, **la recomendación es aceptar menos material antes que una vía no documentada**.

## Consecuencias

- `infrastructure/arxiv/transport.py` (nuevo) concentra la petición con reintentos: las dos vías comparten clasificación de códigos, cortesía de 3 s y mensaje de agotamiento. Duplicarlo habría dado dos tablas capaces de divergir sin que nadie lo notara.
- `infrastructure/arxiv/oai.py` produce `ArxivEntry`, el **mismo DTO** que `atom.py`, para que las dos vías converjan en `mappers.py::entry_to_item`, único sitio donde nace un `Item`.
- `domain/` no cambia. `IngestArxiv` tampoco: sigue sin saber que existen dos vías, igual que no sabe que existe el espaciado de cortesía.
- Coste en tiempo de noche: +2 peticiones en el peor caso (de 6,2 a 9,3 min, del 2,3 % al 3,4 % de `run_timeout_s`). Caso típico medido: 2 peticiones, ~6 s. El techo lo pone `max_requests_per_fetch`, que cuenta peticiones **intentadas** (la cuota se cobra antes de pedir: contarlas al recibir dejaría que cada página agotada consumiera ~93 s sin gastar cuota).
- Ese peor caso solo es una cota porque `transport.py` acota la **duración total** de cada petición con `anyio.fail_after(REQUEST_DEADLINE_S)`. `httpx.Timeout` no sirve para eso: acota cada operación por separado, y su timeout de lectura es el máximo entre dos trozos de datos, así que una respuesta que gotea mantiene un `get` vivo indefinidamente. Sin esa cota, el único freno era el vigía de `hard_stop`: la noche no se pasaba de las 04:45, pero se perdía entera sin aviso hasta la mañana.
- La ventana `from`/`until` se pide sobre el `datestamp` de anuncio, y **el suelo del filtro fino NO es `since`**. Esto lo corrigió la revisión de esta misma tarea, y merece quedar escrito porque la primera versión se equivocó: un lote con `datestamp = D` contiene envíos de `(D-2 18:00 UTC, D-1 18:00 UTC]`, así que usar `since` (por defecto, ayer a medianoche) como suelo **descartaba la franja de envíos de la tarde anterior, la más poblada del día: 8 de 18 novedades medidas sobre una cosecha real, un 44 %**, y para siempre, porque la noche anterior no pedía ese `datestamp` y la siguiente lo pedía con el `since` ya por delante. El suelo baja dos días respecto a `from_` (`_DATESTAMP_LAG_DAYS`), y el evento `arxiv.oai_harvest` registra `filtered_out` para que un suelo mal puesto se vea la primera noche y no en una revisión.
- El solape entre noches es gratis **en base de datos** (`add_many` es `INSERT ... ON CONFLICT DO NOTHING`), pero no en presupuesto de peticiones: subir `oai_lookback_days` trae lotes más viejos que el suelo descarta y gasta `max_requests_per_fetch` en ellos. De ahí la cota superior (`le=7`) y el validador que cruza el peor caso de la ingesta con `limits.run_timeout_s`.
- `infrastructure/mcp/arxiv_server.py` sigue cableado a la vía `api` y no se toca: no tiene llamador en producción en fase 1. Registrado en `TECHNICAL_DEBT.md`.
- **`oaipmh.arxiv.org` está detrás del mismo Fastly.** No se promete inmunidad: se constata que hoy pasa donde la API no pasa, que el origen es distinto y que es el endpoint documentado para este uso. Los reintentos de ADR 0009 siguen puestos.

## Rectificación de ADR 0009

ADR 0009 describe `retry_max_elapsed_s` como «tope duro de toda la secuencia». **No lo es**, y el código nunca lo fue: la comprobación acota el *instante en que puede iniciarse* un nuevo intento, no el final de la secuencia, así que después queda por delante la petición en curso, que puede consumir el timeout HTTP completo (30 s). El techo real por petición es ≈ 93 s, no 60.

Se rectifica aquí porque los ADR no se editan. Los docstrings de `retry.py` y el comentario de `config/pipeline.toml` ya dicen lo correcto. En la misma línea, `retry_max_attempts` es un **tope, no una garantía**: con un `TransportError` que agote los 30 s de timeout solo caben dos intentos.

## Qué sigue vigente de ADR 0004

Todo lo demás: el servidor MCP como adaptador fino, la lógica de ingesta en `application/`, la selección de ítems con `max_items_per_night`, los 3 s de cortesía entre peticiones, y la regla de filtrar por fecha de envío (`published`) y no por la de revisión.
