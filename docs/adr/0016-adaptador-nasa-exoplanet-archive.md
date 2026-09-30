# ADR 0016 · Adaptador del NASA Exoplanet Archive

Fecha: 2026-09-30 · Estado: aceptado · **Resuelve** las decisiones abiertas de `OPEN_DECISIONS.md` sobre cortesía con el archivo, formato de `pl_refname`, bibcode con punto, columna Gaia, `m sin i` del lado del archivo y emparejamiento de nombres · **Corrige una cifra** de ADR 0015 (ver Evidencia) · Tarea: T74

## Contexto

ADR 0015 dejó el puerto `ExoplanetCatalog` (`resolve_planet`, `solutions`) y el cálculo de σ sin adaptador real. T74 tenía que implementarlo contra el NASA Exoplanet Archive sin servidor MCP (ADR 0012 §14), con cero tokens, y sacar un primer informe con `--dry-run` sobre las noches reales con `reader-v3`. Quedaban abiertos: cortesía con el archivo, cómo reconocer la solución del propio paper en `pl_refname`, qué hacer con las masas `m sin i` del archivo y cómo resolver los nombres del Reader.

## Decisión

1. **Dónde**: `infrastructure/exoplanet_archive/` (`client.py`, `names.py`, `mappers.py`, `catalog.py`). `ExoplanetArchiveCatalog` cumple el puerto por estructura; el puerto y `application/` no cambian. Dominio solo gana `TensionResult.reference_sigma()`, sobre el que se reescribe `is_candidate` sin cambiar su comportamiento.
2. **Cortesía** (decisión del autor al aprobar el plan): 2,0 s entre peticiones, techo de 40 peticiones por proceso comprobado antes de enviar (cuentan también las fallidas), 30 s de límite total por petición, `User-Agent` neutro `nocturna/0.1.0`. Valores en `[sources.exoplanet_archive]` de `pipeline.toml`, sin defaults en código. Sin reintentos: un fallo (HTTP ≠ 200, VOTABLE de error con 200, timeout, tope de tamaño, JSON de alias inválido, índice vacío) es `ExoplanetArchiveUnavailable` y se propaga.
3. **Resolución de nombres**: `normalize_name` (guiones unicode, espacios, frontera dígito-letra, minúsculas, y `,`, `~`, `\,` entre alfanuméricos como espacio) sobre el nombre de la medida; búsqueda exacta en un índice de `pl_name` de `pscomppars` pedido una sola vez y solo si hace falta; si no está, servicio de alias con el nombre limpio (`clean_name`), solo la rama de planeta, para cualquier nombre, una vez por nombre normalizado (negativos en caché). Un nombre que solo es de estrella no resuelve.
4. **Solución del propio paper**: `pl_refname` es HTML con enlace a ADS; `arxiv_id` sale del bibcode del `href` con `abs/\d{4}arXiv(\d{4})\.?(\d{4,5})`, que cubre los IDs con punto anteriores a 2015 y los de 5 dígitos. Una cita a revista da `None`.
5. **Masa del archivo**: solo filas con `pl_bmassprov == "Mass"` (decisión del autor al aprobar el plan). Filas sin valor, con valor ≤ 0 o no finito se omiten; un valor no numérico o un `*lim` fuera de −1/0/1 es error del archivo.
6. **Umbral**: `threshold_sigma = 3.0` en `[tension]` de `pipeline.toml`, sin default (ADR 0015 §3).
7. **`--dry-run`**: tras la ingesta y el plan de gasto, cruza todos los `Reading` con medidas guardados y muestra medidas utilizables, emparejadas, tramos de σ, candidatos y peticiones. Si el archivo falla, error por stderr y código 1. `run-night` real no consulta el archivo (T76); el validador de tiempo de ingesta suma ya el peor caso del archivo (40 × (30 + 2) = 1.280 s; con arXiv, 1.838 s ≤ 4.050 s), de forma conservadora.
8. **Informe inicial (opción (b), decisión del autor)**: T74 se cierra con el informe real disponible más un caso de control sobre datos grabados; la reserva de T75 se fija con un nuevo `--dry-run` cuando haya 7 noches con `reader-v3`.

## Evidencia

- Captura real (4 peticiones HTTP 200, 2026-09-30), guardada como fixtures: `2011arXiv1102.1375F` → `1102.1375`, `2015arXiv150907750N` → `1509.07750`; valores de `pl_bmassprov`: vacío, `Msini`, `Mass`, `Msin(i)/sin(i)`; índice de `pscomppars`: 6.372 nombres.
- V1298 Tau en el archivo: solución por defecto Livingston et al. 2026 (bibcode de revista `2026Natur.649..310L`, sin `arxiv_id`), b 13,1 ± 5,3 M⊕ y e 15,3 ± 4,2 M⊕; Suárez Mascareño et al. 2022 con `pl_bmassprov = Mass`, b 203,4 ± 60,4 M⊕ y e 368,7 ± 95,3 M⊕. El archivo no tiene fila del paper 2609.30038.
- Caso de control (test de integración sin red, fixtures reales): b candidato con σ mínimo **3,37** (3,396 / 3,368 / 3,909 / 3,681); e no candidato con σ mínimo 2,69. ADR 0015 citaba 3,40σ para b porque se calculó con 0,041 M_J para Livingston y dos medidas de b; la conclusión no cambia.
- `--dry-run` real (2026-09-30 10:03): código 0, cero tokens; 11 lecturas con extracción de medidas en 1 Run con `reader-v3`, ninguna con medidas; 0 resultados, 0 candidatos, 0 peticiones al archivo.

## Consecuencias

- El ritmo real de tensiones sigue sin medir; T75 queda bloqueada hasta 7 noches con `reader-v3`.
- `--dry-run` recalcula todo el histórico: con unos 20 planetas distintos llegará al techo de 40 y saldrá con código 1. Aceptado por el autor; se revisa en T76 (`OPEN_DECISIONS.md`).
- Un paper que el archivo cita por el bibcode de su revista no se reconoce como propio: se compara consigo mismo, σ ≈ 0, falso negativo (riesgo de ADR 0015 §7).
- Qué hace la noche si el archivo falla y si el enlace `planet_overview_url` pasa al puerto o a `Finding` siguen abiertos (T76, T72).

## Alternativas descartadas

- **Reintentos en el adaptador**: la tarea pide límite de tiempo, espaciado y techo; un fallo se propaga y decide el llamador.
- **Cruce por Gaia**: la columna `gaia_id` no existe y con nombres completos no hace falta.
- **Consultar el alias solo para nombres con dígitos** (plan inicial): quitado por el autor tras la revisión; perdía "Proxima Centauri b" o "Beta Pictoris b", que el índice escribe "Proxima Cen b" y "bet Pic b".
- **Leer `pl_masse` en lugar de filtrar `pl_bmassprov`**: el filtro usa la procedencia que el propio archivo declara.
- **Cortesía como constantes de código**: a diferencia de arXiv, el archivo no publica una cifra para TAP; los valores van en configuración.
