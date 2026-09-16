# ADR 0004 · Ingesta de arXiv: lógica en aplicación, MCP como adaptador

Fecha: 2026-09-16 · Estado: aceptado

## Contexto

T20 implementa la ingesta nocturna de abstracts de arXiv. CLAUDE.md exige servidores MCP in-process (`create_sdk_mcp_server`), pero la ingesta debe ocurrir **sin llamar a ningún agente LLM** (es fase de ingesta pura, no análisis). T20 requiere un cliente HTTP, un parser XML, deduplicación y persistencia. La pregunta arquitectónica es: ¿dónde vive la lógica y qué papel juega MCP?

## Decisión

**El servidor MCP es un adaptador fino, no el camino de la ingesta.** La lógica vive en `application/use_cases/ingest_arxiv.py` y `infrastructure/arxiv/`. El servidor MCP (`infrastructure/mcp/arxiv_server.py`) expone dos herramientas que los agentes invocarán en T40+; son traductores que delegan al cliente sin persistir.

- **Imposibilidad estructural de persistir**, no disciplina: `create_arxiv_mcp_server` no recibe ni `ItemRepository` ni sesión. Si una herramienta escribiera, sería el modelo quien decide cuándo crear `Item` y bajo qué transacción, rompiendo el `unit_of_work` único de ADR 0003.
- **Separación de gasto de tokens**: `fetch_new` devuelve **solo metadatos** (title, categories, published_at, arxiv_id), sin abstracts. `get_abstract` trae una sola entrada **completa**. Un `fetch_new` con 400 abstracts serían ~300 000 tokens de entrada: el presupuesto de la noche entera en una herramienta. Dato observado: con `max_results_cap = 400`, la salida de `fetch_new` ronda ~25 000 tokens de entrada (8% de `nightly_tokens`), así que al cablear ambas herramientas en T40 el cap de la herramienta debería mantenerse por debajo del tope de la ingesta.
- **El CLI es el composition root**: `cli.py` es el único sitio que llama a `Settings()`, `load_pipeline_config()`, crea el motor de BD y abre `unit_of_work`. Ahí es donde se instancia `ArxivClient` y se invoca `IngestArxiv` dentro de una transacción.

## Alternativas descartadas

- **Poner la lógica de ingesta dentro del servidor MCP**: rompería la frontera de persistencia (ADR 0003); quien orquesta la ingesta necesitaría duplicar el `unit_of_work` y T44 no sabría si la ingesta ya pasó en esta noche.
- **`fetch_new` devuelve abstracts completos**: quemamos el presupuesto en una llamada de herramienta; además los agentes no necesitan todos los abstracts, solo los de los papers que les interesan.
- **Herramienta única de "fetch y enriquece"**: los abstracts llegan en segundo plano (T40+), pero la ingesta es T20 y ya ha terminado; además la lógica de cuándo buscar un abstract es del Reader, no del cliente HTTP.

## Consecuencias

- T20 crea Items ingeridos, pero el Reader (T41+) invoca `get_abstract` para leer el text completo cuando lo necesita.
- Los valores `page_size = 100` y `max_results_per_fetch = 400` viven en `pipeline.toml`, propuestos a ojo en T20. El data medido en los primeros `--dry-run` reales determinará si se ajustan (abierto en OPEN_DECISIONS).
- Defecto encontrado y corregido: la paginación de `fetch_new` comparaba entradas **válidas** (`parsed.entries`) con el tamaño de página, de modo que un solo paper malformado cortaba la noche en silencio y la reportaba como completa. Arreglado comparando entradas válidas + descartadas (`parsed.entries + parsed.skipped`) contra `page_max`. Instructivo: la corazonada de que "algo está mal" resultó justo.
- Sin reintentos ante 5xx de arXiv: una noche con arXiv caído no ingesta nada y lo reporta. Deliberado en fase 1 para no dejar un bucle colgado de madrugada. Registrado como deuda técnica.
- Los 4xx no se distinguen: un 400 o un 429 cae en el parser y sale como "la respuesta de arXiv no es XML válido", mensaje confuso. Registrado como deuda técnica; el arreglo no debe introducir reintentos.
- `external_id` se guarda sin versión (ej: `2307.12345`, no `2307.12345v2`): conservar la versión haría que una revisión entrara como ítem nuevo y consumiera otra lectura del Reader por un paper ya analizado. Consecuencia aceptada: una revisión hoy de un paper de hace meses no se ingesta.
- `published_at` se toma de `<published>`, no de `<updated>`: fase 1 publica novedades. Consecuencia aceptada: un paper revisado hoy pero enviado hace meses no se ingesta.
- **`max_items_per_night` no se aplica en la ingesta sino en la selección (T44)**: recortar aquí perdería papers para siempre cuando `since` avance; además es ahorro ilusorio de presupuesto — la ingesta cuesta HTTP a arXiv, no suscripción a Claude.
- Los 3 segundos entre peticiones (`MIN_REQUEST_INTERVAL_S`) son constante de módulo, no clave de configuración: es política de arXiv, no una palanca que el autor deba poder relajar por error.
