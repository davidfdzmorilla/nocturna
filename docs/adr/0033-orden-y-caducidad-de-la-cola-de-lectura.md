# ADR 0033 · Orden y caducidad de la cola de lectura

Fecha: 2026-10-17 · Estado: aceptado · Tarea: T94 · Relacionados: ADR 0018 §6, ADR 0022, ADR 0032

## Contexto

La ingesta no está limitada por `max_items_per_night` (ADR 0004), y el Reader lee 30 ítems por noche. ADR 0018 §6 fijó el orden de `next_unread` en `exoplanet_match DESC, fetched_at ASC, external_id ASC`: dentro de cada grupo de prioridad, primero lo que llevaba más tiempo en la cola.

La decisión abierta T20/T44 ("los ítems `new` que nunca se leen se acumulan") anticipaba el problema, y en octubre de 2026 se cumplió:

- Del 6 al 9 de octubre entraron entre 38 y 53 ítems nuevos por noche frente a 30 lecturas.
- T93 (ADR 0032) recupera además los envíos de jueves y viernes por la tarde que se perdían cada semana.
- Tras la recuperación del 8 al 15 de octubre quedaron 223 ítems `new`, todos sin `exoplanet_match`, publicados entre el 2 y el 15 de octubre.
- La cola crece entre 25 y 50 ítems al día, y lo que se publica cada noche es cada vez más viejo.

Datos de la base real (consulta de solo lectura, 2026-10-17):

- **Desfase entre envío (`published_at`, fecha de la v1) e ingesta:** mediana de 1,8 días, p90 de 3,9 y p99 de 8,1. El p99 lo forman las recuperaciones. En una semana normal el máximo es de unos 4,25 días: el lote del lunes trae envíos del jueves desde las 18:00.
- **Hallazgos afectados:** de 219 `paper_explained` publicados, 9 se leyeron más de 7 días después del envío, todos de recuperaciones.
- **Caducidad sobre la cola de ese día:** con N = 7 caducarían 43 de los 223 `new`; con N = 10, uno.

## Decisión

Decisión del autor: opción (c) de T20/T44, con D1 a, D2 N = 7, D3 b y D4 a de T94.

1. **Orden de la cola.**
   - `next_unread` ordena por `exoplanet_match DESC, published_at DESC, external_id ASC`, con el límite aplicado después.
   - La marca sigue siendo la primera clave: un ítem marcado y antiguo va antes que uno sin marca y reciente.
   - Dentro de cada grupo se lee primero lo más reciente, y `external_id` solo desempata.
   - Modifica ADR 0018 §6, que no se edita.
2. **Estado `expired`** (`ItemStatus.EXPIRED`), terminal y alcanzable solo desde `new` (`Item.expire()`).
   - Lo marca la caducidad, no el Reader ni el Editor, así que `discarded` conserva su significado.
   - Migración `e3a7c5b9d214`, que rehace el CHECK `ck_items_item_status` con el mismo patrón que `950738867fb9`. El downgrade falla si hay filas `expired`.
   - La API y la web no exponen el estado del `Item`.
3. **Regla de caducidad.** Caducan los `new` de `source = 'arxiv'`:
   - sin `exoplanet_match`;
   - con `published_at < now − [limits] max_unread_age_days` (= 7, sin valor por defecto en código).

   La aplican `ItemRepository.stale_unread` y `application/use_cases/expire_unread.py::expire_stale_unread`, sin tokens.
   - Los ítems marcados no caducan (D3 b): no se acumulan, se leen primero y son la materia de las medidas.
4. **Cuándo corre.**
   - **En `run-night`:** en la fase `expire`, tras la ingesta y antes del Reader. Corre aunque falle la ingesta, y también fuera de la ventana, porque no gasta tokens.
     - Si falla, la noche queda `partial` con `end=expire_error` y el Reader lee igual.
     - El recuento va en las notas del `Run` (`| expired=K |`) y en el log `night.expire`.
   - **En `run-item` y en las relecturas de T82:** no corre.
   - **En `--dry-run`:** corre dentro de la transacción que se deshace (T87). Imprime cuántos caducarían, y el reparto de lecturas v3/v2 ya los descuenta.
5. **Ítems caducados.**
   - Un ítem caducado que vuelve en una cosecha sigue caducado: `ON CONFLICT DO NOTHING` lo deja como está.
   - `run-item` lo rechaza, porque `ReadItem` exige `new`.
   - La relectura con `reader-v3` lo rechaza en `reread_refusal`.
   - Ninguno de los dos pide presupuesto antes de rechazarlo.

## Consecuencias

- La cola de ítems sin marca queda acotada a lo publicado en los últimos 7 días, y lo que se publica tiene como mucho una semana.
- **Lo que deja de leerse se pierde.** Un paper de más de una semana que no cupo no se lee nunca. Es la elección del autor: actualidad frente a exhaustividad.
- La primera noche caducan de golpe unos 40 ítems de la recuperación de T93, los publicados entre el 2 y el 9 de octubre.
- **Ítems que caducan la misma noche en que entran.** El suelo de OAI de ADR 0032 (`from_ − 7 días` a las 00:00 UTC) admite en una noche normal `published_at` de hace 7 a 8 días. Esos ítems, si no tienen marca, caducan esa misma noche. Casi siempre son duplicados ya presentes, pero también afecta a los lotes de después de un festivo de arXiv y a cualquier recuperación de más de 7 días (`--since` manual). Por eso `new=` en las notas puede contar ítems que acaban `expired` esa noche.
- **Sin cota para los marcados.** Si algún día entran más de 30 ítems con `exoplanet_match` por noche, su cola no tiene límite; hoy no pasa.
- La caducidad acota a 7 días los ítems sin marca que vuelven a `next_unread` tras un `RATE_LIMITED` o un `TIMEOUT` (OD T41/T44, que sigue abierta).
- Un ítem caducado que `scripts/backfill_exoplanet_match.py` marque después sigue caducado.

## Alternativas descartadas

- **`discarded` sin motivo**: mezcla la caducidad con el descarte del Reader y reabre `NEW → DISCARDED`, que ADR 0018 §4 rechazó.
- **`discarded` con una columna de motivo**: más migración, relleno de filas existentes y arrastra la decisión abierta sobre los motivos de `discarded`.
- **Solo el orden nuevo, sin caducidad**: la cola sigue creciendo sin cota.
- **Solo caducidad, con el orden antiguo**: se seguiría leyendo lo más viejo que aún no ha caducado.
- **Caducar en la ingesta**: no caduca nada si la ingesta falla.
- **Un comando aparte**: depende de que alguien lo lance.
- **N = 10**: hoy apenas cambia nada y deja publicar papers de hasta 10 días.
