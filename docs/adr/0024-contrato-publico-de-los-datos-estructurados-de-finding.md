# ADR 0024 · Contrato público de los datos estructurados de `Finding`

Fecha: 2026-10-06 · Estado: aceptado · Tarea: T77 · Relacionados: ADR 0012 §6 y §13, ADR 0017, ADR 0020

## Contexto

Desde T72 y T89, un `Finding` puede llevar uno de tres datos estructurados (`catalog_tension`, `first_measurement`, `independent_confirmation`), guardados como JSONB con metadatos internos: claves del histórico del archivo (`solution_key`, `soltype`, `pl_pubdate`, `ttv_flag`), parámetros de la regla de confirmación y el id de la evaluación de origen. La API de lectura solo exponía `type` en el detalle, y la web mostraba todos los tipos como `paper_explained`. El dato del paper en `catalog_tension` incluye `evidence`, una cita literal del abstract: texto de terceros.

## Decisión

1. **Lista blanca**: la API expone cada dato estructurado con un esquema propio construido campo a campo (`from_domain`), nunca volcando el JSON guardado. No salen `confidence`, `run_id`, `item_id`, `tension_evaluation_id`, `solution_key`, `soltype`, `pl_pubdate`, `ttv_flag`, el `releasedate` de las previas, `window_days`, `paper_published_at`, `limit` ni `origin`. Un test recorre el JSON a cualquier profundidad buscando esas claves.
2. **Forma**: `FindingSummary` lleva `type`; `FindingDetail` lleva los tres datos como campos separados, cada uno `null` salvo el de su tipo, igual que en el dominio y en la base. El listado no lleva datos estructurados. `GET /findings?type=` filtra por un tipo; un valor fuera del enum da `422`. El `404` sigue siendo el mismo para "no existe" y "no publicado".
3. **Texto de terceros**: `evidence`, referencias del archivo y títulos se muestran en la web solo como texto escapado; `dangerouslySetInnerHTML` está prohibido en `web/src/` (guarda de test). `evidence` se muestra como cita literal en inglés (`lang="en"`), sin traducir, con atribución y enlace al resumen en arXiv; puede llevar LaTeX crudo.
4. **Enlaces**: todo enlace externo de la web se construye con funciones que validan el destino: la ficha del planeta solo con el prefijo `https://exoplanetarchive.ipac.caltech.edu/overview/`, y arXiv solo con un identificador válido. Un enlace que no valida no se pinta.
5. **Presentación** (web): etiqueta de texto para cada tipo, etiqueta "Candidato" solo en `catalog_tension` (ADR 0012 §6) y bloque "Datos del contraste" en los tres niveles, con los números del dato estructurado.

## Consecuencias

- Añadir un campo al JSON guardado no lo publica: hay que añadirlo al esquema y a su test.
- La web tiene un test de contrato con los conjuntos de claves de cada dato, alineado con el de la API.
- Un enlace por solución previa (por ejemplo, a ADS) exigiría ampliar `CatalogTension` (`schema_version` 2, ADR 0017).

## Alternativas descartadas

- **Volcar el JSON guardado tal cual**: expondría metadatos internos y ataría el contrato público al formato de almacenamiento.
- **Un campo `data` polimórfico**: pierde la correspondencia con los CHECK "si y solo si" del dominio y de la base.
- **Renderizar `evidence` como HTML o LaTeX**: superficie de inyección; no aporta lo suficiente.
