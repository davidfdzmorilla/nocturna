"""Siembra tres hallazgos `[DEMO]` publicados, para probar la API de lectura
(T50) y desbloquear la web (T51) antes de que la primera noche real (T44,
pendiente del autor) produzca algo que enseñar.

## Por qué existe

Hoy la base local no tiene ningún `Finding` publicado: el pipeline nocturno
todavía no ha corrido de verdad. Sin datos, no hay nada que enseñar en
`GET /findings` ni en la web. Este script crea un `Run` cerrado y tres
`Item` + `Finding` publicados, con textos de divulgación plausibles en
español, para poder desarrollar y probar contra algo real sin esperar.

**Ningún test depende de este script.** Los tests de la suite siembran sus
propios datos con `tests/db/factories.py`; esto es una herramienta de mano
para el autor, no un fixture de test.

## Por qué vive fuera de `src/nocturna/`

- No es importable desde el paquete (`[tool.hatch.build.targets.wheel]
  packages = ["src/nocturna"]` en `pyproject.toml` no lo incluye).
- No se registra en `[project.scripts]`: esa tabla sigue teniendo una sola
  entrada, `nocturna` (`test_seed_demo_script.py` congela esto).
- Así no hay ningún camino por el que este script acabe empaquetado ni
  expuesto como comando instalado.

## Guarda: `NOCTURNA_ALLOW_SEED`

Misma mecánica que `NOCTURNA_ALLOW_REAL_CLAUDE` usa para los humos manuales
(`tests/manual/conftest.py`): sin la variable de entorno en el proceso, el
script no toca la base de datos y explica por `stderr` qué hace y cómo
autorizarlo. A diferencia del guarda de los humos manuales, este no gasta
la suscripción de Claude -- no llama a ningún agente -- pero sí escribe en
una base de datos real, y una ejecución accidental (por ejemplo, contra la
base equivocada por un `.env` mal puesto) no debe pasar en silencio.

## No borra nada

El script solo inserta. `docker compose down -v` + `alembic upgrade head`
es como se limpia la base local si hace falta empezar de cero -- no es
responsabilidad de este script. Toda la siembra ocurre en una única unidad
de trabajo (`unit_of_work`): si algo falla a mitad de camino (por ejemplo,
lanzarlo dos veces contra la misma base sin limpiarla, que colisiona por
`external_id` y deja un `Finding` en memoria con un `item_id` que no llegó
a existir en la tabla `items`), la transacción entera se deshace y la base
queda exactamente como estaba antes de la segunda ejecución -- falla alto y
claro, no corrompe datos a medias.

## Publicación por el mismo camino que el Editor

Cada `Finding` se publica llamando a `Finding.publish(confidence, at)`
-- el mismo método que usa `EditNight` (`application/use_cases/
edit_night.py`) -- antes de persistirlo con `FindingRepository.add`; el
`Item` correspondiente pasa por `mark_read()` y `publish()`, las mismas dos
transiciones de estado que recorrería un ítem editado de verdad. Ningún
campo guardado (`confidence`, `published_at`, `Item.status`) se escribe a
mano por debajo del dominio.

## Uso

    docker compose up -d
    cd backend && uv run alembic upgrade head
    NOCTURNA_ALLOW_SEED=1 uv run python scripts/seed_demo.py
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from nocturna.domain.entities import Finding, FindingType, Item, Run, RunStatus
from nocturna.infrastructure.config import Settings, load_pipeline_config
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import (
    create_db_engine,
    create_session_factory,
    unit_of_work,
)

_ALLOW_SEED_ENV_VAR = "NOCTURNA_ALLOW_SEED"

_RUN_COMMAND = f"{_ALLOW_SEED_ENV_VAR}=1 uv run python scripts/seed_demo.py"

_REMEDY = (
    f"seed_demo.py saltado: falta {_ALLOW_SEED_ENV_VAR} en el entorno.\n\n"
    "Este script escribe en la base de datos configurada (por defecto la de "
    "docker compose, ver Settings.database_url): crea un Run cerrado con "
    "notes='demo-seed' y tres Item + Finding publicados con título "
    "'[DEMO] ...', para poder probar la API de lectura (T50) y la web (T51) "
    "sin esperar a la primera noche real. No borra nada existente.\n\n"
    f"Para autorizarlo explícitamente:\n\n    {_RUN_COMMAND}\n"
)


@dataclass(frozen=True, slots=True)
class _DemoFinding:
    external_id: str
    categories: list[str]
    abstract: str
    finding_title: str
    level_curious: str
    level_amateur: str
    level_technical: str
    confidence: float


_DEMO_FINDINGS: tuple[_DemoFinding, ...] = (
    _DemoFinding(
        external_id="9999.00001",
        categories=["astro-ph.HE"],
        abstract=(
            "We report the interferometric localization of a repeating fast "
            "radio burst source to a low-mass dwarf host galaxy, based on six "
            "additional bursts detected during a follow-up campaign with a "
            "long-baseline radio array."
        ),
        finding_title=(
            "[DEMO] Un repetidor rápido de ondas de radio traiciona su "
            "escondite en una galaxia enana"
        ),
        level_curious=(
            "Los telescopios llevan años captando destellos de radio que duran solo "
            "milisegundos pero liberan tanta energía como el Sol emite en días "
            "enteros. Se llaman fast radio bursts, y la mayoría aparece una sola "
            "vez, lo que hace casi imposible saber de dónde vienen. Este repetidor "
            "es distinto: ha lanzado la misma señal varias veces desde el mismo "
            "punto del cielo, y eso ha permitido a los astrónomos rastrear su "
            "origen hasta una pequeña galaxia enana, mucho más simple y menos "
            "poblada de estrellas que la Vía Láctea. El hallazgo importa porque el "
            "entorno donde nace la señal da pistas sobre qué clase de objeto "
            "extremo -probablemente una estrella de neutrones muy magnetizada- "
            "puede producir semejante estallido de energía."
        ),
        level_amateur=(
            "Las ráfagas rápidas de radio (FRB, por sus siglas en inglés) son "
            "pulsos de milisegundos que en ese brevísimo instante liberan una "
            "energía comparable a la que el Sol produce en varios días. La "
            "inmensa mayoría se observa una única vez, así que localizar su "
            "origen exacto es un problema abierto desde su descubrimiento en "
            "2007. Este caso es uno de los pocos repetidores conocidos: la misma "
            "fuente ha emitido varios estallidos, lo que ha permitido a los "
            "radiotelescopios de la red usada en este trabajo triangular su "
            "posición con precisión suficiente para identificar la galaxia "
            "anfitriona. Se trata de una galaxia enana, con una masa estelar muy "
            "inferior a la de la Vía Láctea y una tasa de formación estelar baja, "
            "un entorno distinto al de los repetidores localizados hasta ahora, "
            "casi todos en galaxias más masivas. Los autores discuten la "
            "posibilidad de que la fuente sea un magnetar joven, una estrella de "
            "neutrones con un campo magnético extraordinariamente intenso, "
            "aunque el modelo no queda cerrado con las observaciones disponibles."
        ),
        level_technical=(
            "Se reporta la localización interferométrica de un repetidor de "
            "ráfagas rápidas de radio (FRB) mediante observaciones coordinadas "
            "con un array de antenas de línea de base larga, que alcanza una "
            "resolución angular de sub-arcosegundo suficiente para asociar la "
            "fuente a una galaxia anfitriona concreta con una probabilidad de "
            "coincidencia por azar inferior al 1%. Se detectaron seis estallidos "
            "adicionales durante la campaña de seguimiento, con anchos "
            "temporales entre 0.3 y 2.1 milisegundos y medidas de dispersión "
            "(DM) consistentes entre sí dentro del error instrumental, lo que "
            "confirma que todos proceden de la misma fuente física y no de una "
            "superposición casual de eventos no relacionados. El exceso de DM "
            "respecto al valor esperado por el modelo galáctico de electrones "
            "libres se atribuye mayoritariamente al medio circungaláctico de la "
            "anfitriona, no al medio intergaláctico difuso, dada la distancia "
            "estimada mediante corrimiento al rojo espectroscópico (z ≈ 0.03). "
            "La galaxia anfitriona presenta una masa estelar de aproximadamente "
            "10^8 masas solares y una tasa de formación estelar específica "
            "elevada para su masa, características compartidas con el entorno "
            "de al menos otro repetidor bien estudiado, lo que refuerza la "
            "hipótesis de que estos objetos favorecen galaxias enanas con "
            "formación estelar activa reciente. Los autores modelan la fuente "
            "como un magnetar formado tras una supernova de colapso de núcleo o "
            "una fusión de estrellas compactas, y descartan un origen en un "
            "núcleo galáctico activo por la ausencia de emisión persistente en "
            "radio coincidente con la posición. Se discuten las implicaciones "
            "para el uso de FRB como sondas cosmológicas del medio bariónico "
            "difuso, señalando que una contribución circungaláctica no "
            "despreciable complica la extracción de la densidad electrónica "
            "intergaláctica a partir de la medida de dispersión sin un modelo "
            "específico de la anfitriona."
        ),
        confidence=0.82,
    ),
    _DemoFinding(
        external_id="9999.00002",
        categories=["astro-ph.EP"],
        abstract=(
            "We present a near-infrared transmission spectrum of a sub-Neptune "
            "exoplanet obtained from multiple transits, revealing an absorption "
            "feature consistent with water vapor at moderate statistical "
            "significance."
        ),
        finding_title=(
            "[DEMO] Indicios de vapor de agua en la atmósfera de un exoplaneta "
            "templado tipo Neptuno"
        ),
        level_curious=(
            "Un equipo ha usado un telescopio espacial para observar cómo cambia "
            "la luz de una estrella cuando su planeta pasa por delante de ella, "
            "un tránsito que dura pocas horas. Al descomponer esa luz en "
            "distintos colores, encontraron una señal compatible con vapor de "
            "agua en la atmósfera del planeta, un mundo del tamaño de Neptuno que "
            "orbita mucho más cerca de su estrella que la Tierra del Sol. No es "
            "un planeta habitable -su temperatura es demasiado alta-, pero "
            "confirma que la técnica funciona incluso para planetas de este "
            "tamaño, no solo para los gigantes gaseosos más grandes, y abre la "
            "puerta a estudiar atmósferas de mundos más parecidos al nuestro en "
            "el futuro."
        ),
        level_amateur=(
            "La espectroscopia de tránsito consiste en observar cómo se atenúa "
            "la luz de una estrella en distintas longitudes de onda mientras un "
            "planeta pasa por delante de ella; si la atmósfera del planeta "
            "absorbe luz de un color concreto, el tránsito se ve un poco más "
            "profundo justo en esa longitud de onda. Aplicando esta técnica a un "
            "planeta de tamaño similar a Neptuno, que completa una órbita en "
            "apenas unos días, los autores detectan una señal de absorción "
            "compatible con vapor de agua, con una significancia estadística "
            "moderada pero por encima del umbral habitual para reclamar una "
            "detección tentativa. El planeta orbita demasiado cerca de su "
            "estrella para ser habitable: su temperatura de equilibrio supera "
            "los 600 kelvin. El interés del resultado no es la habitabilidad, "
            "sino demostrar que la instrumentación actual puede caracterizar "
            "atmósferas de planetas de masa intermedia, un rango de tamaños "
            "mucho más abundante en la galaxia que los gigantes gaseosos ya "
            "estudiados en detalle, y que hasta ahora había sido difícil de "
            "abordar con esta precisión."
        ),
        level_technical=(
            "Se presenta un espectro de transmisión en el infrarrojo cercano de "
            "un planeta de tipo sub-Neptuno (radio ≈ 3.2 R⊕, masa ≈ 8 M⊕) "
            "obtenido a partir de varios tránsitos observados con "
            "espectroscopia de baja resolución, cubriendo el rango de 0.6 a 2.8 "
            "micras. El ajuste conjunto de la curva de luz y el espectro revela "
            "una característica de absorción centrada en torno a 1.4 micras, "
            "consistente con la banda de vapor de agua, con una significancia "
            "de aproximadamente 3.2 sigma frente a un modelo de continuo plano "
            "sin rasgos moleculares. Se descartan artefactos instrumentales "
            "mediante la inspección de las curvas de luz en canales fuera de la "
            "banda de interés y la comparación con estrellas de referencia "
            "observadas en el mismo campo. El ajuste retrieval, con un modelo de "
            "atmósfera de equilibrio químico y perfiles de temperatura "
            "parametrizados, favorece una metalicidad atmosférica varias veces "
            "superior a la solar y una razón carbono-oxígeno subsolar, aunque "
            "los intervalos de confianza son amplios dado el nivel de ruido "
            "fotométrico alcanzado. No se detecta evidencia significativa de "
            "metano ni de dióxido de carbono en el rango espectral cubierto, lo "
            "que los autores atribuyen tanto a limitaciones de sensibilidad como "
            "a una posible química atmosférica dominada por procesos "
            "fotoquímicos en la atmósfera superior, dada la temperatura de "
            "equilibrio estimada en 610 ± 40 K. La masa y el radio del planeta "
            "son consistentes con una envoltura de hidrógeno-helio de baja masa "
            "sobre un núcleo rocoso-helado, sin necesidad de invocar una "
            "atmósfera dominada por vapor de agua en el interior. Los autores "
            "señalan que observaciones adicionales con mayor resolución "
            "espectral y mayor cobertura en el infrarrojo medio serían "
            "necesarias para restringir mejor la composición y descartar "
            "degeneraciones entre metalicidad y cobertura de nubes."
        ),
        confidence=0.68,
    ),
    _DemoFinding(
        external_id="9999.00003",
        categories=["astro-ph.HE"],
        abstract=(
            "We report the gravitational-wave detection of a binary black hole "
            "merger with an unusually asymmetric mass ratio, challenging "
            "isolated binary evolution and dynamical formation channels."
        ),
        finding_title=(
            "[DEMO] LIGO y Virgo detectan la fusión de dos agujeros negros con "
            "una masa inusualmente asimétrica"
        ),
        level_curious=(
            "Los detectores de ondas gravitacionales han vuelto a captar el "
            "instante en que dos agujeros negros se fusionan en uno solo, "
            "liberando en fracciones de segundo más energía que la que emiten "
            "todas las estrellas del universo visible juntas en ese mismo "
            "tiempo. Lo llamativo de este caso es que los dos agujeros negros "
            "tenían masas muy diferentes entre sí, mucho más de lo habitual en "
            "las fusiones detectadas hasta ahora. Esa diferencia de masa es "
            "difícil de explicar con los modelos actuales de cómo nacen y "
            "evolucionan las parejas de agujeros negros, y obliga a los "
            "astrofísicos a revisar sus ideas sobre los caminos que llevan a "
            "estas fusiones tan poco simétricas."
        ),
        level_amateur=(
            "La red de detectores de ondas gravitacionales ha identificado una "
            "nueva fusión de agujeros negros a partir de la señal característica "
            "de 'chirrido' que producen estos eventos: una vibración del "
            "espacio-tiempo que aumenta en frecuencia y amplitud justo antes del "
            "instante de la fusión. El análisis de la forma de onda permite "
            "estimar las masas de los dos objetos antes de fusionarse, y en este "
            "caso el resultado destaca por la fuerte asimetría: uno de los "
            "agujeros negros es varias veces más masivo que el otro, una "
            "proporción rara entre las decenas de fusiones observadas hasta la "
            "fecha por esta red de detectores. Los modelos de formación de "
            "sistemas binarios de agujeros negros, ya sea a través de la "
            "evolución de estrellas masivas en pareja o mediante capturas "
            "dinámicas en cúmulos estelares densos, no predicen con facilidad "
            "proporciones de masa tan extremas, así que este evento se convierte "
            "en un caso de referencia para poner a prueba esos modelos y quizá "
            "revisar alguno de sus supuestos."
        ),
        level_technical=(
            "Se reporta la detección de una fusión de agujeros negros binarios "
            "mediante la red de detectores interferométricos, con una razón "
            "señal-ruido combinada superior a 12 y una probabilidad de falsa "
            "alarma inferior a 1 por cada varios miles de años de tiempo de "
            "observación equivalente. El análisis de inferencia bayesiana de la "
            "forma de onda, empleando plantillas de relatividad numérica "
            "calibradas con modelos de precesión de espín, restringe las masas "
            "en el sistema del marco del detector en m1 ≈ 38 M☉ y m2 ≈ 6 M☉, con "
            "una razón de masas q = m2/m1 marcadamente distinta de la unidad y "
            "sistemáticamente más extrema que la distribución típica del "
            "catálogo de eventos previos de esta red. Los espines individuales "
            "están débilmente restringidos por la degeneración habitual entre "
            "masa y espín efectivo, pero el espín efectivo combinado del "
            "sistema es consistente con valores moderados y positivos, sin "
            "evidencia significativa de precesión detectable en la forma de "
            "onda con la relación señal-ruido disponible. La distancia de "
            "luminosidad inferida, con su correspondiente incertidumbre, sitúa "
            "el evento a un corrimiento al rojo cosmológico bajo, compatible con "
            "la sensibilidad esperada de la red para sistemas de esta masa "
            "total. Los autores comparan la razón de masas inferida con las "
            "predicciones de canales de formación aislada (evolución binaria de "
            "estrellas masivas con transferencia de masa) y de formación "
            "dinámica en cúmulos estelares densos, señalando que ninguno de los "
            "dos canales reproduce con facilidad razones de masa tan asimétricas "
            "sin invocar mecanismos adicionales, como episodios de acreción "
            "hipercrítica o captura dinámica secuencial en entornos de alta "
            "densidad estelar. Se discute la contribución de este evento a la "
            "distribución de razón de masas del catálogo acumulado y su papel en "
            "las restricciones sobre la función de masa de agujeros negros de "
            "origen estelar."
        ),
        confidence=0.91,
    ),
)


def _require_opt_in() -> None:
    if _ALLOW_SEED_ENV_VAR not in os.environ:
        print(_REMEDY, file=sys.stderr)
        sys.exit(1)


def _build_items_and_findings(
    demo_findings: tuple[_DemoFinding, ...], run_id: UUID, now: datetime
) -> tuple[list[Item], list[Finding]]:
    """Construye, en memoria, los `Item` ya leídos y sus `Finding` publicados.

    Cada `Item` recorre `mark_read()` (como lo dejaría el Reader real) y
    `publish()` (como lo dejaría el Editor real tras aprobar su `Finding`);
    cada `Finding` se publica con `publish(confidence, at)` antes de que
    nada lo persista, exactamente igual que `EditNight.__call__` (ver
    docstring del módulo).
    """
    items: list[Item] = []
    findings: list[Finding] = []
    for offset, demo in enumerate(demo_findings):
        published_at = now - timedelta(days=len(demo_findings) - offset, hours=3)
        fetched_at = published_at + timedelta(minutes=20)
        item = Item(
            source="arxiv",
            external_id=demo.external_id,
            title=demo.finding_title.removeprefix("[DEMO] "),
            abstract=demo.abstract,
            categories=demo.categories,
            published_at=published_at,
            fetched_at=fetched_at,
        )
        item.mark_read()

        finding = Finding(
            item_id=item.id,
            run_id=run_id,
            type=FindingType.PAPER_EXPLAINED,
            title=demo.finding_title,
            level_curious=demo.level_curious,
            level_amateur=demo.level_amateur,
            level_technical=demo.level_technical,
        )
        finding.publish(confidence=demo.confidence, at=published_at + timedelta(hours=4))
        item.publish()

        items.append(item)
        findings.append(finding)
    return items, findings


def main() -> None:
    _require_opt_in()

    settings = Settings()
    config = load_pipeline_config()
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)

    now = datetime.now(UTC)
    run = Run(started_at=now - timedelta(minutes=10), budget_tokens=config.budget.nightly_tokens)
    items, findings = _build_items_and_findings(_DEMO_FINDINGS, run.id, now)

    with unit_of_work(session_factory) as session:
        items_repo = SqlAlchemyItemRepository(session)
        runs_repo = SqlAlchemyRunRepository(session)
        findings_repo = SqlAlchemyFindingRepository(session)

        # El Run se inserta y se confirma (flush) antes de los Finding que lo
        # referencian por FK: sin `relationship()` entre modelos (ver
        # `infrastructure/db/models.py`), este orden explícito es más claro
        # que fiarse del orden de flush automático de SQLAlchemy.
        runs_repo.add(run)
        session.flush()

        inserted = items_repo.add_many(items)

        for finding in findings:
            findings_repo.add(finding)

        run.items_fetched = len(items)
        run.items_read = len(items)
        run.findings_published = len(findings)
        run.finish(RunStatus.COMPLETED, at=now, notes="demo-seed")
        runs_repo.save(run)

    print(
        f"Run {run.id} cerrado como demo-seed: {inserted} Item(s) nuevo(s) insertado(s), "
        f"{len(findings)} Finding(s) [DEMO] publicados."
    )


if __name__ == "__main__":
    main()
