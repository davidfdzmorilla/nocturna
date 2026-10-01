# ADR 0018 · Filtro de exoplanetas en la ingesta y prioridad en la cola de lectura

Fecha: 2026-10-01 · Estado: aceptado · **Modifica ADR 0014 §1** (criterio de selección de `reader-v3`) y **el orden de `next_unread`** · **Resuelve** la decisión abierta de T75 "Restringir `reader-v3` a ítems que traten de exoplanetas" · Tarea: T79

## Contexto

Las dos primeras noches con `reader-v3` (2026-09-30 y 2026-10-01) leyeron 25 ítems de astro-ph.EP con el prompt de medidas y no guardaron ninguna medida. 14 de los 25 eran de sistema solar o física espacial (cometas, TNO, asteroides, auroras de Júpiter, Saturno, viento solar, ionosfera), donde una medida contrastable es imposible; `reader-v3` cuesta unos 1.800 tokens más por ítem que `reader-v2`. Revisados a mano, el prompt y los filtros no perdían nada: la extracción desde abstracts tiene un techo estructural. El autor aceptó el 2026-10-01 restringir v3 a ítems de exoplanetas con un filtro en la ingesta y priorizar esos ítems en la cola.

## Decisión

1. **Marca en la ingesta**: `Item.exoplanet_match` (columna `items.exoplanet_match BOOLEAN NOT NULL DEFAULT false`, migración `a79e3c5d8f12`), calculada por `IngestArxiv` con `ExoplanetFilter` (`domain/exoplanet_filter.py`, regla pura) sobre título y abstract, tras normalizar `~`, `\,` y comas entre alfanuméricos como espacio (`normalize_text`, que reutiliza también `infrastructure/exoplanet_archive/names.py`). Si un camino no la rellena vale `false`, que lleva a lo barato. Los duplicados de una ingesta conservan su marca; para recalcular está `scripts/backfill_exoplanet_match.py` (idempotente, con `--dry-run`), que se lanza tras migrar y cada vez que cambie la lista.
2. **Selección de variante**: `reader-v3` solo si el ítem tiene una categoría de `[reader] measurement_categories` **y** `exoplanet_match` es cierto; si no, `reader-v2`. `measurement_categories = []` sigue apagando v3. La variante se decide antes de autorizar y cada una autoriza con su propia estimación.
3. **Planeta concreto frente a exoplanetas en general**: el filtro busca **planetas o sistemas concretos** (una designación de catálogo, o la palabra `exoplanet(s)`), no el tema. Los ítems de exoplanetas en general sin planeta designado —estudios de población (relaciones masa-radio, "hybrid worlds"), discos, planetesimales, estrellas anfitrionas, manchas estelares— van a `reader-v2` **por diseño**: sin un planeta concreto no puede haber una medida contrastable. Son 9 de los 32 casos del fixture `tests/fixtures/t79/ep_items.json` (dos de ellos, 2609.17025 y 2609.34635, reetiquetados por el autor desde "planeta concreto" el 2026-10-01); 2 de los 9 casan porque usan la palabra `exoplanet(s)`, y su resultado está congelado en los tests.
4. **Los EP que no casan se leen con `reader-v2`**, no se descartan: descartar exigiría una transición `NEW → DISCARDED` y sacaría del feed divulgativo los papers de sistema solar.
5. **Lista** (en `[exoplanet_filter]` de `pipeline.toml`, sin valores por defecto en código; un regex que no compila falla al cargar). Palabras clave: `exoplanet`, `exoplanets` (palabra completa, sin distinguir mayúsculas; sin `planet(s)` ni `exoplanetary`). Ocho patrones de designación, que distinguen mayúsculas:
   - [0] catálogos con número y letra opcional: TOI, KOI, K2, Kepler, WASP, HAT-P, HATS, KELT, NGTS, CoRoT, XO, TrES, Qatar; un número seguido de unidad de tiempo (year, yr, day, campaign, quarter) no cuenta ("Kepler 4-year baseline");
   - [1] TRAPPIST-1;
   - [2] catálogos de estrella con letra de planeta obligatoria: HD, HIP, GJ, Gliese, LHS, LTT, HR, Ross, Wolf, L, TWA, PDS ("L 98-59 b", "LTT 1445A b", "TWA 7 b", "PDS 70 b");
   - [3] designación + abreviatura de constelación + letra ("V1298 Tau b", "AU Mic b");
   - [4] estrella variable `V####` + constelación sin letra ("the V1298 Tau system");
   - [5] coordenadas `J…±…` + letra ("RX J0534.0-0221 b");
   - [6] genitivo completo sin letra solo con número de Flamsteed ("61 Cygni");
   - [7] genitivo completo con letra obligatoria ("tau Bootis b", "Proxima Centauri b").
   La letra de planeta es minúscula, así que "HD 189733 A" no casa.
6. **Orden de la cola**: `next_unread` ordena `exoplanet_match DESC, fetched_at ASC, external_id ASC` y aplica el límite después. La prioridad la da la marca aunque el ítem no sea de una categoría de v3 (se lee con v2, sin coste extra).
7. **Significado de la marca**: "casa con la lista vigente cuando se evaluó", no "se leyó con v3"; lo que se usó lo dice `AgentCall.prompt_version`.

## Falsos positivos aceptados

- **Genitivo con número de Flamsteed sin letra (patrón [6])**: casa "47 Tucanae" (cúmulo globular), "30 Doradus" (nebulosa), "40 Eridani", "70 Ophiuchi". Se acepta porque el coste es asimétrico: un falso positivo cuesta leer con v3 un paper que casi nunca llega de astro-ph.EP (en GA o SR solo sube en la cola y se lee con v2), y quitar el patrón perdería 61 Cygni, un caso real del fixture.
- **"Kepler 1 d"** (número seguido de "d" como abreviatura de días): `d` no se excluye porque es letra de planeta ("Kepler-90 d", "Kepler-11 d").

## Evidencia

- Fixture real de 32 casos (25 ítems EP de las dos noches con v3, TOI-6981 b, los 4 de T71.b y 2 negativos sintéticos): 7 con planeta concreto, todos casan (V1298 Tau, TOI-6981 b, RX J0534.0-0221 b y TWA 7 b, TOI-2109 b, HD 126053 b, 61 Cyg, TOI-125); 16 que no deben casar, ninguno casa (14 de sistema solar y los sintéticos HD 189733 A y formación planetaria); 9 de exoplanetas en general, casan 2.
- Dos pasadas de revisión con `budget-guard-review`. La primera encontró falsos positivos en la lista de la primera iteración ("dos mayúsculas + constelación" sin letra y genitivo con prefijo griego sin letra marcaban RR Lyrae, BL Lac, RS CVn, ZZ Ceti, TW Hya, HL Tau, FU Ori, eta Carinae, delta Scuti, rho Ophiuchi…); la segunda, números seguidos de unidades de tiempo y TWA/PDS sin letra ("TWA 7 disk", "PDS 110 dipper"). Con la lista final, los tests de dominio fijan **43 cadenas que no deben casar y 36 positivos de control que sí**, todos en verde.

## Consecuencias

- Menos llamadas a v3: unos 2.000 tokens menos por cada ítem EP reconducido a v2. El presupuesto, la reserva del Editor y `max_items_per_night` no cambian.
- Un paper que hable de "el sistema AU Mic", "TW Hya" o "PDS 70" sin nombrar un planeta con letra va a v2.
- Con más de 30 ítems marcados por noche, los no marcados más antiguos pueden quedar relegados (deuda registrada).
- El código nuevo con la base sin migrar falla antes de gastar tokens: la ingesta degrada a `partial`, `next_unread` falla, el `Run` queda `failed` y la API da 500 en el detalle. Hay que migrar y rellenar antes de la noche (secuencia en `docs/DEVELOPMENT_WORKFLOW.md`). El código viejo con la base migrada funciona.
- Las noches con v3 anteriores a T79 cuentan para las 7 que espera T75 (decisión del autor, 2026-10-01).

## Alternativas descartadas

- **Filtro antes del Reader, calculado al seleccionar** (sin columna ni migración): no es "en la ingesta", carga toda la cola cada noche y no deja rastro.
- **Descartar los EP que no casan**: transición nueva y pérdida de papers divulgativos.
- **Añadir `planet(s)` a las palabras clave**: recupera estudios de población (que no dan medidas) a cambio de falsos positivos de sistema solar y formación planetaria.
- **Añadir `exoplanetary`**: los papers con planetas concretos ya los recogen las designaciones.
- **Anfitriona sin letra con cualquier prefijo de dos mayúsculas, y genitivo con prefijo griego sin letra**: falsos positivos masivos de estrellas variables, discos y nubes.
- **TWA y PDS con letra opcional**: marcaban discos y estrellas jóvenes.
- **Quitar el genitivo con número de Flamsteed**: perdería 61 Cygni (ver "Falsos positivos aceptados").
