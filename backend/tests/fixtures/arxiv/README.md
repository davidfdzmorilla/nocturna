# Fixtures de arXiv

Respuestas reales de la API de arXiv, capturadas una sola vez y guardadas verbatim.
Ningún test sale a la red: se sirven con `httpx.MockTransport`.

Capturadas el **2026-09-16** contra `https://export.arxiv.org/api/query`
con `User-Agent: nocturna/0.1.0` y 3 s entre peticiones, como exige la política de arXiv.

| Fichero | Consulta | Para qué |
|---|---|---|
| `feed_three_entries.xml` | `search_query=cat:astro-ph.EP OR cat:astro-ph.GA&sortBy=submittedDate&sortOrder=descending&start=0&max_results=3` | Caso normal: 3 entradas, títulos y abstracts multilínea, varias categorías |
| `feed_page_1.xml` | idéntica a la anterior | Primera página de la paginación |
| `feed_page_2.xml` | igual con `start=3` | Segunda página |
| `feed_revised_entries.xml` | `...&sortBy=lastUpdatedDate&sortOrder=descending&max_results=8` | **`published` != `updated`**: 4 de las 8 son revisiones. `2602.11270v2` se envió el 2026-02-11 y se revisó el 2026-09-15. Con `updated` se ingestaría hoy; con `published`, no. También trae sufijos `v2` para el recorte de versión en `external_id` |
| `feed_single.xml` | `id_list=2301.00001&max_results=1` | Respuesta de `get_abstract` |
| `feed_empty.xml` | `search_query=cat:astro-ph.EP AND all:zzzzzznoexiste` | `totalResults = 0`, sin entradas |
| `feed_error.xml` | `id_list=id_invalido_xyz` | **Feed de error de arXiv, que llega con HTTP 200** y una única entrada de título `Error`. Sin este caso, un id inválido parecería una respuesta vacía normal |

`feed_malformed_entry.xml` **no** es una captura: se deriva de `feed_three_entries.xml`
quitando el `<summary>` de la segunda entrada, para probar que una entrada incompleta
se descarta y se cuenta en `skipped` en vez de tumbar la ingesta entera.

## `oai/` — vía OAI-PMH (T60.c)

Capturadas contra `https://oaipmh.arxiv.org/oai` el 2026-09-25 (la URL nueva:
`export.arxiv.org/oai2` está obsoleta y redirige allí desde marzo de 2025).

| fichero | consulta | qué cubre |
|---|---|---|
| `list_records_ep.xml` | `verb=ListRecords&metadataPrefix=arXivRaw&set=physics:astro-ph:EP&from=2026-09-24&until=2026-09-24` | 27 registros reales, sin `resumptionToken`. Incluye `2509.12737`, con v1 de 2025 y v2 de 2026: la revisión de un paper viejo que la cosecha trae porque su `datestamp` de anuncio es reciente, y que el filtro `published_at >= since` debe descartar. |
| `error_bad_argument.xml` | la misma con `from=until=2001-01-01` | Error de protocolo real: arXiv responde `badArgument` («start date too early») cuando el rango precede a su `earliestDatestamp`. |
| `list_records_three_entries.xml` | **derivada**: tres `verb=GetRecord&metadataPrefix=arXivRaw` (`2609.17526`, `2609.17505`, `2609.17383`) envueltos en un `ListRecords` | Los **mismos tres papers** que `feed_three_entries.xml` por la otra vía. Es la base de `test_oai_atom_equivalence.py`, que congela que cambiar de vía no altera el `external_id` ni el dominio. |
| `list_records_empty.xml` | **derivada** | `noRecordsMatch`: la respuesta que el protocolo define para un rango válido sin novedades, que NO es un error. arXiv no la devolvió en las capturas (dio `badArgument` antes), así que se reprodujo a mano. |

Las derivadas lo declaran en un comentario XML dentro del propio fichero,
igual que `feed_malformed_entry.xml`.

Nota sobre el LaTeX: `arXivRaw` devuelve el fuente tal cual, así que un
abstract puede traer `\AA` donde el Atom de `/api/query` trae `Å`. Es una
diferencia conocida y congelada en `test_oai_atom_equivalence.py`, no un
defecto de las fixturas.

## Regenerarlas

Las consultas exactas están en la tabla. Respeta los 3 s entre peticiones.
Al regenerar, revisa si los tests afirman sobre identificadores o fechas concretas.
