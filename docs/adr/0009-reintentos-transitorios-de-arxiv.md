# ADR 0009 · Reintentos ante fallos transitorios de arXiv

Fecha: 2026-09-22 · Estado: aceptado · **Supersede ADR 0004** (en lo relativo a reintentos)

## Contexto

ADR 0004 (T20, 2026-09-16) decidió «sin reintentos ante fallos transitorios de arXiv, para no dejar un bucle colgado de madrugada». Esa decisión se basaba en el análisis de T20: la ingesta es batch nocturno, no interactiva; una noche con arXiv caído reporta sin datos y se sigue.

El 2026-09-21 a las 06:03 UTC, las verificaciones del lanzador nocturno fallaron con `ArxivUnavailable: la API de arXiv respondió 406 Not Acceptable`. Diagnóstico en profundidad:

- **Cuerpo vacío**, sin `Retry-After` en cabeceras
- **Cabeceras de Fastly/Varnish** presentes (lo rechazaba el CDN delante de arXiv, no la aplicación de arXiv misma)
- **Transitorio**: la misma consulta con la misma URL, `User-Agent`, `Accept` y parámetros devolvió `200 OK` minutos después
- **Sin correlación clara con nuestro cliente**: `curl` pasaba intermitentemente donde `httpx` fallaba, pero sin que ninguna cabecera (User-Agent, Accept, Accept-Encoding, versión HTTP, codificación de %3A, max_results) lo explicara
- **Descartadas las causas rastreables**: se probaron `User-Agent` ("curl-alike", "Mozilla", neutro), `Accept` (con/sin XML), `Accept-Encoding`, versión HTTP, y fueron variaciones sin correlación

La decisión anterior es correcta en espíritu (no dejar colgada la máquina de madrugada), pero **inadecuada en práctica** frente a un fallo transitorio de la infraestructura de arXiv (la red mundial no es determinista).

## Decisión

**Reintentar ante fallos transitorios con backoff exponencial y jitter**, configurable desde `pipeline.toml`, **sin cambiar de biblioteca HTTP ni añadir cabeceras "de navegador"**. La política vive en `infrastructure/arxiv/retry.py` (`Retrier`):

- `retry_max_attempts`: número de intentos (incluido el primero). Default: 4.
- `retry_base_delay_s`: espera base antes del 2.º intento, se dobla cada vez. Default: 5 s (nominal: 5, 10, 20 s; peor caso con jitter: 7,5, 15, 30 s ≈ 52,5 s totales < 60 s).
- `retry_max_elapsed_s`: tope duro de toda la secuencia. Default: 60 s.

**Fallos reintentables**:
- 406 (Not Acceptable)
- 429 (Too Many Requests)
- Cualquier 5xx (error de servidor)
- `httpx.TransportError` (timeout, corte de conexión, RemoteProtocolError)

**Fallos no reintentables**:
- Cualquier otro 4xx (400, 404, 414, etc. = consulta mal formada, esperar no ayuda)
- `httpx.HTTPError` no-transporte (URL inválida = error de código, no transitorio)

**Eventos de log estructurados** (en `extra` del logger JSON):
- `arxiv.retry` (warning): cuando se reintenta
- `arxiv.retry_recovered` (info): cuando se recupera tras uno o más reintentos
- `arxiv.retry_exhausted` (error): cuando se agotan los reintentos

**Ejecución**: `Retrier` vive dentro de `ArxivClient._get()`, que es la única llamada HTTP a arXiv. La ingesta pasa por un único `unit_of_work`, así que los reintentos no fragmentan transacciones. La espera es `anyio.sleep` (no `time.sleep`), así que es cancelable: si `hard_stop` interrumpe la noche a las 04:45, el reintento en vuelo es cancelado correctamente por el `asyncio.timeout` de T44 (ADR 0005).

## Alternativas descartadas

- **Cambiar de biblioteca HTTP** (de `httpx` a `requests`, `urllib`, etc.): sin evidencia de que la biblioteca sea la causa del 406. El mismo `httpx` funcionó minutos después con la misma configuración. Es optimización sin diagnóstico. Coste: acoplamiento a nueva dependencia, duplicación de cifras de gasto lateral (cada biblioteca tiene su overhead).

- **Imitar la "huella TLS" de `curl`**: `curl` pasó intermitentemente donde `httpx` fallaba, pero de forma no reproducible; sin evidencia de que la diferencia sea TLS/cabeceras/compresión. Perseguir una diferencia fantasma de cliente es frágil y expone el cambio a que futuros cambios de `curl` o de arXiv rompan nuestro "disfraz". Además: imitar una herramienta es evadir una protección del CDN, no arreglarlo.

- **Añadir cabeceras "de navegador"** (User-Agent: Mozilla, Accept-Language, Referer, etc.): igual que la anterior, es disfraz. El CDN puede rechazar peticiones legítimas de clientes por cambios no predecibles en su lógica. Cabeceras de navegador aquí son mentira: somos un bot de batch, no un navegador.

- **Mantener la política de T20 ("sin reintentos")**: correcta bajo el supuesto de "arXiv nunca cae, solo nuestra red". La evidencia del 2026-09-21 falsea ese supuesto: arXiv, o la red que lo rodea, tuvo un fallo transitorio que nuestro cliente no podía diagnosticar más profundamente.

## Consecuencias

- **Ingesta más robusta**: una noche con 406 intermitente no pierde el acceso a arXiv si el CDN se recupera en segundos.
- **Gasto de tiempo acotado**: el peor caso son ~60 segundos por petición (worst case: 4 páginas × 60 s = 4 minutos de ingesta). Sigue siendo negligible frente a la ventana de 4h 45m.
- **Ventana y presupuesto sin cambios**: la ingesta sigue fuera de `BudgetGuard` porque no gasta tokens de Claude. Los reintentos no compiten con el presupuesto de agentes.
- **Lo que sigue del ADR 0004 es vigente**:
  - El servidor MCP es adaptador fino, no cambia
  - La lógica de ingesta vive en `application/` y `infrastructure/arxiv/`, no cambia
  - `max_items_per_night` se aplica en T44, no en la ingesta
  - `MIN_REQUEST_INTERVAL_S = 3` sigue siendo constante, no configurable
  - La política de cortesía de arXiv (3 s entre peticiones) sigue siendo ley

- **Calibración pendiente**: los valores iniciales (4 intentos, 5 s base, 60 s tope) son conservadores, sin datos de campo más allá del único 406 observado. T60 mide catorce noches y cuenta `arxiv.retry_recovered` vs `arxiv.retry_exhausted` para validarlos.

## Hipótesis no confirmada

La diferencia entre `curl` y `httpx` ante el 406 podría deberse a **múltiples factores no aislables a distancia**: pool de conexiones, timing de reintento del cliente, diferencias de implementación de TLS, comportamiento de resolución DNS, o interacción con la máquina del autor. Sin acceso a trazas de nivel de paquete o cambios en arXiv/CDN, no se puede confirmar la causa. **Se acepta la incertidumbre y se mitiga con reintentos**, que es más general (funciona para cualquier causa de 406, conocida o no).
