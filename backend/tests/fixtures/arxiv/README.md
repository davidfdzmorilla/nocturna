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

## Regenerarlas

Las consultas exactas están en la tabla. Respeta los 3 s entre peticiones.
Al regenerar, revisa si los tests afirman sobre identificadores o fechas concretas.
