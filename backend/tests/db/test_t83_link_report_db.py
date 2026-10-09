"""T83: `backend/scripts/t83_link_report.py`, el informe de solo lectura del enlace
entre las dos vías (arXiv y NASA Exoplanet Archive). Contra `nocturna_test`.

## Interfaz que fijan estos tests (la implementa el backend igual)

Módulo `scripts/t83_link_report.py`, cargado por ruta (no es paquete):

- `build_report(session, *, since: date, rule: OwnSolutionRule, now: datetime,
  max_sigma: float, window_days: int) -> LinkReport`. SÍNCRONO (los tests lo llaman fuera
  de un bucle de eventos; si recalcula con `ComputeTensions` usa
  `anyio.run`). Solo lee. `LinkReport` es un
  `dataclass(frozen=True)` con:
  - (a) `a_total: int` (filas activas con `releasedate >= since`), `a_with_arxiv_id: int`,
    `a_crossing: tuple[str, ...]` (`external_id` de `items` con `source='arxiv'` que
    aparecen como `arxiv_id` de esas filas, ordenados).
  - (b) `b_count: int`, `b_min: int | None`, `b_median: float | None`, `b_max: int | None`:
    desfase en meses `releasedate` - mes del `arxiv_id` (YYMM) de TODAS las filas activas
    con `arxiv_id` (no solo las posteriores a `since`).
  - (c) `c_journal_rows: int` (filas activas con `releasedate >= since` y sin `arxiv_id`),
    `c_own_value_match_rows: int` y `c_ambiguous_rows: int`: de esas, las que
    `classify_solution` marca `OWN_VALUE_MATCH` / `AMBIGUOUS` frente a alguna lectura
    vigente con medidas del mismo planeta (mismo `pl_name` normalizado) y parámetro. Una
    fila cuenta en `own_value_match` si alguna lectura la casa; si no, en `ambiguous` si
    alguna la deja ambigua.
  - (d) `d_evaluations: int` (filas de `tension_evaluation`),
    `d_changed: tuple[EvaluationChange, ...]`,
    `d_reference_not_independent: tuple[ReferenceAudit, ...]`,
    `d_confirmations: tuple[ConfirmationAudit, ...]`. Las evaluaciones guardadas se
    recalculan con la regla nueva sobre las soluciones activas del archivo del
    `archive_planet_name` guardado (sin red ni alias).
- `EvaluationChange(evaluation_id, external_id, planet_name, parameter, old_status,
  new_status, old_own_solution_key, new_own_solution_key)`: evaluaciones cuyo estado o
  cuya `own_solution_key` cambian con la regla nueva.
- `ReferenceAudit(evaluation_id, external_id, planet_name, parameter, provenance)`:
  evaluaciones `evaluated` cuya referencia guardada (`result.reference()`) no es
  `INDEPENDENT` con la regla nueva (`provenance: SolutionProvenance`).
- `ConfirmationAudit(evaluation_id, external_id, planet_name, parameter, provenance,
  still_eligible: bool)`: evaluaciones elegibles como `confirmacion_independiente` con la
  regla ANTERIOR a T83 (σ, ventana y parámetro, sin mirar la procedencia), con la
  procedencia de su referencia y si lo siguen siendo con `confirmation_eligible` nuevo.
- `render(report, since: date) -> str`: líneas `clave=valor`, una por dato:
  `a.total`, `a.with_arxiv_id`, `a.crossing` (recuento), `a.crossing_ids` (separados por
  comas), `b.count`, `b.min`, `b.median`, `b.max`, `c.journal_rows`,
  `c.own_value_match_rows`, `c.ambiguous_rows`, `d.evaluations`, `d.changed`,
  `d.reference_not_independent`, `d.confirmations_blocked_today`,
  `d.confirmations_still_eligible`. Puede haber más líneas (listados, títulos) que no
  tengan la forma `[a-d].nombre=valor`.
- `main(argv: list[str] | None = None, *, now: datetime | None = None) -> int`: `--since
  YYYY-MM-DD` (por defecto 2026-08-01); lee `NOCTURNA_DATABASE_URL`, la regla de
  `[tension.own_solution]` y `confirmation_max_sigma` / `confirmation_window_days` de
  `config/pipeline.toml`; abre `unit_of_work(factory, commit=False)`; imprime `render` por
  stdout y devuelve 0. No escribe en ninguna tabla.

## Escenario sembrado (since = 2026-08-01, now = 2026-10-05, regla 0,01 / 6 meses)

- WASP-12 b, paper A 2609.00001 (2026-09-15): r1 arXiv 2606.18045 13,8; r3 revista 14,05;
  r4 revista 20,0; r5 revista 14,0 con pubdate 2020-01.
- KELT-9 b, paper B 2609.00002 (2026-09-25): r7 revista 14,0 (pubdate 2026-10).
- HD 1 b, paper C 2609.00003 (2026-09-20): r2 arXiv 2609.00003 14,0; r8 arXiv 2607.00001 13,8.
- Z b, sin paper: r6 arXiv 2605.00001, releasedate 2026-07-01.

Medidas de A, B y C: masa 14,0 ± 1,0 M_earth.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType

import anyio
import pytest
import sqlalchemy as sa
from factories import make_item, make_reading
from fakes.clock import FakeClock
from helpers.exoplanet import make_measurement, make_own_solution_rule, make_period_rule
from test_archive_repository import save, snapshot, solution

from nocturna.application.use_cases.compute_tensions import ComputeTensions
from nocturna.domain.archive import ArchiveParameterValue, catalog_solution_from_archive
from nocturna.domain.entities import MeasuredParameter, MeasurementUnit
from nocturna.domain.own_solution import SolutionProvenance
from nocturna.domain.tension import (
    EvaluationStatus,
    TensionEvaluation,
    TensionResult,
    check_period,
    compare,
)
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyArchiveRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyTensionEvaluationRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.exoplanet_archive.catalog import ExoplanetArchiveCatalog

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "t83_link_report.py"
SINCE = date(2026, 8, 1)
NOW = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
MASS = MeasuredParameter.MASS
M_E = MeasurementUnit.M_EARTH


def _load_script() -> ModuleType:
    if not SCRIPT_PATH.exists():
        pytest.fail(f"{SCRIPT_PATH} no existe: T83 lo define como informe de solo lectura.")
    spec = importlib.util.spec_from_file_location("t83_link_report", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["t83_link_report"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script() -> ModuleType:
    return _load_script()


class _NoAlias:
    async def lookup_alias(self, name: str):  # pragma: no cover
        raise AssertionError(f"alias consultado para {name!r}")


def _row(pl_name, ref_key, mass, *, arxiv_id, pubdate, released):
    return solution(
        pl_name,
        ref_key,
        mass=mass,
        arxiv_id=arxiv_id,
        pl_pubdate=pubdate,
        releasedate=released,
    )


R1 = _row(
    "WASP-12 b",
    "2026arXiv260618045C",
    13.8,
    arxiv_id="2606.18045",
    pubdate="2026-09",
    released=date(2026, 10, 1),
)
R3 = _row(
    "WASP-12 b",
    "2026AJ....1..3A",
    14.05,
    arxiv_id=None,
    pubdate="2026-10",
    released=date(2026, 10, 3),
)
R4 = _row(
    "WASP-12 b",
    "2026AJ....1..4A",
    20.0,
    arxiv_id=None,
    pubdate="2026-10",
    released=date(2026, 10, 3),
)
R5 = _row(
    "WASP-12 b",
    "2020AJ....1..5A",
    14.0,
    arxiv_id=None,
    pubdate="2020-01",
    released=date(2026, 10, 3),
)
R7 = _row(
    "KELT-9 b",
    "2026AJ....1..7A",
    14.0,
    arxiv_id=None,
    pubdate="2026-10",
    released=date(2026, 10, 3),
)
R2 = _row(
    "HD 1 b",
    "2026arXiv260900003C",
    14.0,
    arxiv_id="2609.00003",
    pubdate="2026-10",
    released=date(2026, 10, 2),
)
R8 = _row(
    "HD 1 b",
    "2026arXiv260700001C",
    13.8,
    arxiv_id="2607.00001",
    pubdate="2026-08",
    released=date(2026, 9, 1),
)
R6 = _row(
    "Z b",
    "2026arXiv260500001C",
    5.0,
    arxiv_id="2605.00001",
    pubdate="2026-05",
    released=date(2026, 7, 1),
)
ALL_ROWS = [R1, R3, R4, R5, R7, R2, R8, R6]


def _paper(session, external_id, planet, published_at):
    items = SqlAlchemyItemRepository(session)
    item = make_item(external_id=external_id, published_at=published_at)
    items.add_many([item])
    session.flush()
    measurement = make_measurement(14.0, 1.0, 1.0, planet_name=planet, unit=M_E)
    reading = make_reading(item.id, measurements=(measurement,))
    SqlAlchemyReadingRepository(session).add(reading)
    session.flush()
    return item, reading, measurement


async def _seed(session) -> dict[str, TensionEvaluation]:
    """Siembra el escenario de la tabla del docstring; devuelve las evaluaciones guardadas."""
    archive = SqlAlchemyArchiveRepository(session)
    save(archive, snapshot(), ALL_ROWS)
    session.flush()

    _paper(session, "2609.00001", "WASP-12 b", datetime(2026, 9, 15, 8, 0, tzinfo=UTC))
    item_b, reading_b, paper_b = _paper(
        session, "2609.00002", "KELT-9 b", datetime(2026, 9, 25, 8, 0, tzinfo=UTC)
    )
    item_c, reading_c, _ = _paper(
        session, "2609.00003", "HD 1 b", datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    )

    # B: evaluación de ANTES de T83. La versión de revista del propio paper (R7) se
    # trató como previa: `evaluated` con σ = 0 y sin `own_solution_key`.
    prior = catalog_solution_from_archive(R7, MASS, is_default=False)
    assert prior is not None
    stored_b = TensionEvaluation(
        reading_id=reading_b.id,
        item_id=item_b.id,
        planet_name="KELT-9 b",
        parameter=MASS,
        measurements=(paper_b,),
        status=EvaluationStatus.EVALUATED,
        evaluated_at=NOW,
        archive_planet_name="KELT-9 b",
        result=TensionResult(
            item_id=item_b.id,
            planet_name="KELT-9 b",
            parameter=MASS,
            comparisons=(compare(paper_b, prior),),
            reading_id=reading_b.id,
        ),
    )

    # C: evaluación correcta (la propia R2 se excluye por `arxiv_id`, la referencia es R8).
    compute = ComputeTensions(
        ExoplanetArchiveCatalog(archive, _NoAlias()),  # type: ignore[arg-type]
        threshold_sigma=3.0,
        period_rule=make_period_rule(),
        own_solution_rule=make_own_solution_rule(),
        clock=FakeClock(NOW),
    )
    report = await compute([(item_c, reading_c)])
    (stored_c,) = report.evaluations
    assert stored_c.status == EvaluationStatus.EVALUATED
    assert stored_c.own_solution_key == R2.solution_key

    evaluations = SqlAlchemyTensionEvaluationRepository(session)
    evaluations.add(stored_b)
    evaluations.add(stored_c)
    session.flush()
    return {"B": stored_b, "C": stored_c}


def _seed_sync(session) -> dict[str, TensionEvaluation]:
    """Los tests son síncronos: `build_report` y `main` pueden usar `anyio.run` por dentro."""
    return anyio.run(_seed, session)


def _kwargs():
    return {
        "since": SINCE,
        "rule": make_own_solution_rule(),
        "now": NOW,
        "max_sigma": 2.0,
        "window_days": 30,
    }


def test_recuentos_a_cruce_por_arxiv_id_con_items(db_session, script):
    _seed_sync(db_session)

    report = script.build_report(db_session, **_kwargs())

    assert report.a_total == 7, "r6 (releasedate 2026-07-01) queda fuera"
    assert report.a_with_arxiv_id == 3
    assert report.a_crossing == ("2609.00003",)


def test_recuentos_b_desfase_en_meses_de_todas_las_filas_con_arxiv_id(db_session, script):
    _seed_sync(db_session)

    report = script.build_report(db_session, **_kwargs())

    assert report.b_count == 4, "r6 cuenta aunque sea anterior a `since`"
    assert (report.b_min, report.b_median, report.b_max) == (1, 2.0, 4)


def test_recuentos_c_filas_de_revista_frente_a_las_medidas(db_session, script):
    _seed_sync(db_session)

    report = script.build_report(db_session, **_kwargs())

    assert report.c_journal_rows == 4
    assert report.c_own_value_match_rows == 2, "r3 frente al paper A y r7 frente al B"
    assert report.c_ambiguous_rows == 1, "r4; r5 es independiente por su pl_pubdate"


def test_auditoria_d_cambios_de_estado_o_de_clave(db_session, script):
    stored = _seed_sync(db_session)

    report = script.build_report(db_session, **_kwargs())

    assert report.d_evaluations == 2
    (change,) = report.d_changed
    assert change.evaluation_id == stored["B"].id
    assert (change.external_id, change.planet_name, change.parameter) == (
        "2609.00002",
        "KELT-9 b",
        MASS,
    )
    assert change.old_status == EvaluationStatus.EVALUATED
    assert change.new_status == EvaluationStatus.CLOSED_LOOP
    assert change.old_own_solution_key is None
    assert change.new_own_solution_key == R7.solution_key


def test_auditoria_d_referencias_que_dejan_de_ser_independientes(db_session, script):
    stored = _seed_sync(db_session)

    report = script.build_report(db_session, **_kwargs())

    (audit,) = report.d_reference_not_independent
    assert audit.evaluation_id == stored["B"].id
    assert audit.provenance == SolutionProvenance.OWN_VALUE_MATCH
    assert (audit.external_id, audit.planet_name) == ("2609.00002", "KELT-9 b")


def test_auditoria_d_confirmaciones_bloqueadas_hoy_y_su_clasificacion_nueva(db_session, script):
    stored = _seed_sync(db_session)

    report = script.build_report(db_session, **_kwargs())

    by_id = {c.evaluation_id: c for c in report.d_confirmations}
    assert set(by_id) == {stored["B"].id, stored["C"].id}
    assert by_id[stored["B"].id].provenance == SolutionProvenance.OWN_VALUE_MATCH
    assert by_id[stored["B"].id].still_eligible is False
    assert by_id[stored["C"].id].provenance == SolutionProvenance.INDEPENDENT
    assert by_id[stored["C"].id].still_eligible is True


def test_la_ventana_de_confirmacion_usa_now_y_window_days(db_session, script):
    _seed_sync(db_session)

    report = script.build_report(
        db_session, **{**_kwargs(), "now": datetime(2027, 1, 1, tzinfo=UTC)}
    )

    assert report.d_confirmations == ()


def test_sin_datos_no_falla_y_da_ceros(db_session, script):
    report = script.build_report(db_session, **_kwargs())

    assert (report.a_total, report.a_with_arxiv_id, report.a_crossing) == (0, 0, ())
    assert report.b_count == 0 and report.b_min is None and report.b_median is None
    assert (report.c_journal_rows, report.d_evaluations) == (0, 0)
    assert report.d_changed == () and report.d_confirmations == ()


def _table_counts(factory) -> dict[str, int]:
    with factory() as session:
        names = (
            session.execute(
                sa.text(
                    "select table_name from information_schema.tables "
                    "where table_schema = 'public' and table_type = 'BASE TABLE' "
                    "order by table_name"
                )
            )
            .scalars()
            .all()
        )
        return {
            name: session.execute(sa.text(f'select count(*) from "{name}"')).scalar_one()
            for name in names
        }


def _parse(output: str) -> dict[str, str]:
    return dict(re.findall(r"^([a-d]\.\w+)=(.*)$", output, flags=re.M))


def test_main_imprime_los_recuentos_y_no_escribe_en_ninguna_tabla(
    monkeypatch, db_session_factory, test_database_url, capsys, script
):
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)
    with unit_of_work(db_session_factory) as session:
        _seed_sync(session)
    before = _table_counts(db_session_factory)
    assert before["tension_evaluation"] == 2 and before["archive_solution"] == len(ALL_ROWS)

    code = script.main(["--since", "2026-08-01"], now=NOW)

    out = capsys.readouterr().out
    assert code == 0
    assert _table_counts(db_session_factory) == before, "el informe no escribe"
    values = _parse(out)
    assert values["a.total"] == "7"
    assert values["a.with_arxiv_id"] == "3"
    assert values["a.crossing"] == "1"
    assert values["a.crossing_ids"] == "2609.00003"
    assert (values["b.count"], values["b.min"], values["b.median"], values["b.max"]) == (
        "4",
        "1",
        "2.0",
        "4",
    )
    assert values["c.journal_rows"] == "4"
    assert values["c.own_value_match_rows"] == "2"
    assert values["c.ambiguous_rows"] == "1"
    assert values["d.evaluations"] == "2"
    assert values["d.changed"] == "1"
    assert values["d.reference_not_independent"] == "1"
    assert values["d.confirmations_blocked_today"] == "2"
    assert values["d.confirmations_still_eligible"] == "1"


def test_main_since_por_defecto_es_2026_08_01(
    monkeypatch, db_session_factory, test_database_url, capsys, script
):
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)
    with unit_of_work(db_session_factory) as session:
        _seed_sync(session)

    assert script.main([], now=NOW) == 0

    assert _parse(capsys.readouterr().out)["a.total"] == "7"


def test_main_since_posterior_reduce_las_filas_recientes(
    monkeypatch, db_session_factory, test_database_url, capsys, script
):
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)
    with unit_of_work(db_session_factory) as session:
        _seed_sync(session)

    assert script.main(["--since", "2026-10-02"], now=NOW) == 0

    values = _parse(capsys.readouterr().out)
    assert values["a.total"] == "5", "r1 (10-01) y r8 (09-01) quedan fuera"
    assert values["a.with_arxiv_id"] == "1"
    assert values["b.count"] == "4", "(b) no depende de `since`"


def test_el_texto_del_script_no_menciona_claude_ni_el_proveedor_ni_el_presupuesto():
    # El script importa nocturna.cli y por tanto carga budget sin usarlo: aquí solo se
    # comprueba que su propio texto no nombra claude, LLMProvider ni BudgetGuard.
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    imports = "\n".join(
        ln for ln in source.splitlines() if ln.startswith(("import ", "from ", "    from "))
    )

    for forbidden in ("claude", "LLMProvider", "BudgetGuard", "nocturna.infrastructure.llm"):
        assert forbidden not in imports, forbidden
    assert "run_agent" not in source


def test_el_script_solo_abre_unidades_de_trabajo_sin_commit():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "commit=False" in source
    assert "session.commit(" not in source and ".add(" not in source


def test_t92_el_periodo_evaluated_contra_la_fila_r_pasa_a_closed_loop_con_masa_radio_y_periodo(
    db_session, script
):
    """Forma de HD 715 b: la fila R trae masa, radio y periodo del propio paper (sin
    `arxiv_id`). Masa y radio casan por valor; el periodo, guardado `evaluated` contra R
    con σ = 0, debe verse como cambio `evaluated -> closed_loop` en el apartado (d)."""
    planet = "HD 715 b"
    row_r = _row(
        planet,
        "2026AJ....1..9A",
        14.0,
        arxiv_id=None,
        pubdate="2026-10",
        released=date(2026, 10, 3),
    )
    row_r = replace(
        row_r,
        is_default=True,
        radius=ArchiveParameterValue(value=0.9, err1=0.05, err2=-0.05, lim=0),
        period=ArchiveParameterValue(value=6.0, err1=0.01, err2=-0.01, lim=0),
    )
    archive = SqlAlchemyArchiveRepository(db_session)
    save(archive, snapshot(), [row_r])
    db_session.flush()

    items = SqlAlchemyItemRepository(db_session)
    item = make_item(external_id="2609.00004", published_at=datetime(2026, 9, 28, 8, 0, tzinfo=UTC))
    items.add_many([item])
    db_session.flush()
    mass = make_measurement(14.0, 1.0, 1.0, planet_name=planet, unit=M_E)
    radius = make_measurement(
        0.95,
        0.05,
        0.05,
        planet_name=planet,
        parameter=MeasuredParameter.RADIUS,
        unit=MeasurementUnit.R_EARTH,
    )
    period = make_measurement(
        6.0,
        0.01,
        0.01,
        planet_name=planet,
        parameter=MeasuredParameter.PERIOD,
        unit=MeasurementUnit.DAY,
    )
    reading = make_reading(item.id, measurements=(mass, radius, period))
    SqlAlchemyReadingRepository(db_session).add(reading)
    db_session.flush()

    prior = catalog_solution_from_archive(row_r, MeasuredParameter.PERIOD, is_default=True)
    assert prior is not None
    stored = TensionEvaluation(
        reading_id=reading.id,
        item_id=item.id,
        planet_name=planet,
        parameter=MeasuredParameter.PERIOD,
        measurements=(period,),
        status=EvaluationStatus.EVALUATED,
        evaluated_at=NOW,
        archive_planet_name=planet,
        result=TensionResult(
            item_id=item.id,
            planet_name=planet,
            parameter=MeasuredParameter.PERIOD,
            comparisons=(compare(period, prior),),
            reading_id=reading.id,
        ),
        period_check=check_period((period,), prior, make_period_rule()),
    )
    SqlAlchemyTensionEvaluationRepository(db_session).add(stored)
    db_session.flush()

    report = script.build_report(db_session, **_kwargs())

    (change,) = report.d_changed
    assert change.evaluation_id == stored.id
    assert (change.external_id, change.planet_name, change.parameter) == (
        "2609.00004",
        planet,
        MeasuredParameter.PERIOD,
    )
    assert change.old_status == EvaluationStatus.EVALUATED
    assert change.new_status == EvaluationStatus.CLOSED_LOOP
    assert change.old_own_solution_key is None
    assert change.new_own_solution_key == row_r.solution_key
