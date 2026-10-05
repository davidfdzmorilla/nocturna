"""Findings `primera_medida` y `confirmacion_independiente` (T89) contra PostgreSQL.

Las filas inválidas se insertan con SQL crudo para que salte el CHECK de la
base y no la validación del dominio.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime

import pytest
import sqlalchemy as sa
from factories import make_finding, make_item, make_reading, make_run
from sqlalchemy.exc import IntegrityError
from test_schema_constraints import _seed_item, _seed_run, _valid_finding_row

from nocturna.domain.entities import (
    ArchiveStatus,
    ConfirmationReference,
    FindingType,
    FirstMeasurement,
    IndependentConfirmation,
    MeasuredParameter,
    MeasurementUnit,
    PaperMeasurement,
)
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
)

R_EARTH = MeasurementUnit.R_EARTH
RADIUS = MeasuredParameter.RADIUS
PUBLISHED_AT = datetime(2026, 3, 2, tzinfo=UTC)
_JSON_COLUMNS = ("first_measurement", "independent_confirmation")


def _first() -> FirstMeasurement:
    return FirstMeasurement(
        paper_planet_name="TOI-6981 b",
        archive_planet_name=None,
        parameter=RADIUS,
        archive_status=ArchiveStatus.ABSENT,
        measurements=(PaperMeasurement(2.4, 0.1, 0.1, R_EARTH),),
    )


def _confirmation() -> IndependentConfirmation:
    return IndependentConfirmation(
        paper_planet_name="HIP 67522 b",
        archive_planet_name="HIP 67522 b",
        parameter=RADIUS,
        archive_url="https://exoplanetarchive.ipac.caltech.edu/overview/HIP%2067522%20b",
        measurements=(PaperMeasurement(10.0, 0.5, 0.5, R_EARTH),),
        reference=ConfirmationReference(
            refname="Chakraborty 2026",
            arxiv_id="2601.00002",
            value=10.2,
            err_plus=0.4,
            err_minus=0.4,
            unit=R_EARTH,
            releasedate=date(2026, 10, 1),
        ),
        sigmas=(0.3,),
        max_sigma=2.0,
        window_days=30,
        paper_published_at=PUBLISHED_AT,
    )


def _payload(column: str) -> dict:
    return (_first() if column == "first_measurement" else _confirmation()).to_json()


def _seed_evaluation(session, item_id: uuid.UUID, *, parameter: str = "radius") -> uuid.UUID:
    reading_id = session.execute(
        sa.text("SELECT id FROM readings WHERE item_id = :item"), {"item": item_id}
    ).scalar_one_or_none()
    if reading_id is None:
        reading = make_reading(item_id)
        SqlAlchemyReadingRepository(session).add(reading)
        session.flush()
        reading_id = reading.id
    evaluation_id = uuid.uuid4()
    session.execute(
        sa.text(
            "INSERT INTO tension_evaluation (id, reading_id, item_id, planet_name, parameter, "
            "status, detail, first_evaluated_at, evaluated_at) VALUES (:id, :reading, :item, "
            "'p', :parameter, 'awaiting_reference', CAST('{}' AS jsonb), :now, :now)"
        ),
        {
            "id": evaluation_id,
            "reading": reading_id,
            "item": item_id,
            "parameter": parameter,
            "now": PUBLISHED_AT,
        },
    )
    return evaluation_id


def _insert_raw(session, row: dict, **json_columns: dict | None) -> None:
    values = dict(row)
    casts = {}
    for name, payload in json_columns.items():
        values[name] = json.dumps(payload) if payload is not None else None
        casts[name] = f"CAST(:{name} AS jsonb)"
    columns = ", ".join(values)
    placeholders = ", ".join(casts.get(name, f":{name}") for name in values)
    session.execute(sa.text(f"INSERT INTO findings ({columns}) VALUES ({placeholders})"), values)


def _row(session, **overrides) -> dict:
    item_id, run_id = _seed_item(session), _seed_run(session)
    return _valid_finding_row(item_id, run_id, **overrides)


def _evaluation_for(session, row: dict, **kw) -> uuid.UUID:
    return _seed_evaluation(session, row["item_id"], **kw)


# --- CHECK de tipo -----------------------------------------------------------


@pytest.mark.parametrize(
    ("type_", "column"),
    [
        ("primera_medida", "first_measurement"),
        ("confirmacion_independiente", "independent_confirmation"),
    ],
)
def test_insert_valido_de_cada_tipo_nuevo(db_session, type_, column):
    row = _row(db_session, type=type_)
    row["tension_evaluation_id"] = _evaluation_for(db_session, row)

    _insert_raw(db_session, row, **{column: _payload(column)})

    assert (
        db_session.execute(
            sa.text("SELECT type FROM findings WHERE id = :id"), {"id": row["id"]}
        ).scalar_one()
        == type_
    )


def test_tipo_inventado_falla_incluso_con_payload_y_evaluacion(db_session):
    row = _row(db_session, type="primera_medida_x")
    row["tension_evaluation_id"] = _evaluation_for(db_session, row)
    with pytest.raises(IntegrityError, match="finding_type"):
        _insert_raw(db_session, row, first_measurement=_payload("first_measurement"))


# --- CHECK "si y solo si" ----------------------------------------------------


def test_primera_medida_sin_payload_falla(db_session):
    row = _row(db_session, type="primera_medida")
    row["tension_evaluation_id"] = _evaluation_for(db_session, row)
    with pytest.raises(IntegrityError, match="first_measurement_iff_type"):
        _insert_raw(db_session, row)


def test_confirmacion_sin_payload_falla(db_session):
    row = _row(db_session, type="confirmacion_independiente")
    row["tension_evaluation_id"] = _evaluation_for(db_session, row)
    with pytest.raises(IntegrityError, match="independent_confirmation_iff_type"):
        _insert_raw(db_session, row)


@pytest.mark.parametrize("type_", ["paper_explained", "confirmacion_independiente"])
def test_payload_first_measurement_en_otro_tipo_falla(db_session, type_):
    row = _row(db_session, type=type_)
    with pytest.raises(IntegrityError, match="first_measurement_iff_type"):
        _insert_raw(db_session, row, first_measurement=_payload("first_measurement"))


def test_payload_independent_confirmation_en_paper_explained_falla(db_session):
    row = _row(db_session, type="paper_explained")
    with pytest.raises(IntegrityError, match="independent_confirmation_iff_type"):
        _insert_raw(db_session, row, independent_confirmation=_payload("independent_confirmation"))


def test_payload_independent_confirmation_en_primera_medida_falla(db_session):
    row = _row(db_session, type="primera_medida")
    row["tension_evaluation_id"] = _evaluation_for(db_session, row)
    with pytest.raises(IntegrityError, match="independent_confirmation_iff_type"):
        _insert_raw(
            db_session,
            row,
            first_measurement=_payload("first_measurement"),
            independent_confirmation=_payload("independent_confirmation"),
        )


@pytest.mark.parametrize(
    ("type_", "column"),
    [
        ("primera_medida", "first_measurement"),
        ("confirmacion_independiente", "independent_confirmation"),
    ],
)
def test_tipo_nuevo_sin_evaluacion_falla(db_session, type_, column):
    row = _row(db_session, type=type_)
    with pytest.raises(IntegrityError, match="tension_evaluation_id_iff_type"):
        _insert_raw(db_session, row, **{column: _payload(column)})


def test_paper_explained_con_evaluacion_falla(db_session):
    row = _row(db_session, type="paper_explained")
    row["tension_evaluation_id"] = _evaluation_for(db_session, row)
    with pytest.raises(IntegrityError, match="tension_evaluation_id_iff_type"):
        _insert_raw(db_session, row)


# --- FK e índice único -------------------------------------------------------


def test_evaluacion_inexistente_falla_por_fk(db_session):
    row = _row(db_session, type="primera_medida")
    row["tension_evaluation_id"] = uuid.uuid4()
    with pytest.raises(IntegrityError, match="fk_findings_tension_evaluation_id"):
        _insert_raw(db_session, row, first_measurement=_payload("first_measurement"))


def test_indice_unico_evaluacion_y_tipo(db_session):
    row = _row(db_session, type="primera_medida")
    evaluation_id = _evaluation_for(db_session, row)
    row["tension_evaluation_id"] = evaluation_id
    _insert_raw(db_session, row, first_measurement=_payload("first_measurement"))

    second = _valid_finding_row(row["item_id"], row["run_id"], type="primera_medida")
    second["tension_evaluation_id"] = evaluation_id
    with pytest.raises(IntegrityError, match="uq_findings_tension_evaluation_id_type"):
        _insert_raw(db_session, second, first_measurement=_payload("first_measurement"))


def test_misma_evaluacion_con_los_dos_tipos_es_valida(db_session):
    row = _row(db_session, type="primera_medida")
    evaluation_id = _evaluation_for(db_session, row)
    row["tension_evaluation_id"] = evaluation_id
    _insert_raw(db_session, row, first_measurement=_payload("first_measurement"))

    other = _valid_finding_row(row["item_id"], row["run_id"], type="confirmacion_independiente")
    other["tension_evaluation_id"] = evaluation_id
    _insert_raw(db_session, other, independent_confirmation=_payload("independent_confirmation"))


def test_varios_paper_explained_sin_evaluacion_no_chocan_con_el_indice(db_session):
    item_id, run_id = _seed_item(db_session), _seed_run(db_session)
    for _ in range(2):
        _insert_raw(db_session, _valid_finding_row(item_id, run_id))


# --- repositorio -------------------------------------------------------------


def _seed_world(session):
    item, run = make_item(), make_run()
    SqlAlchemyItemRepository(session).add_many([item])
    SqlAlchemyRunRepository(session).add(run)
    session.flush()
    return item, run


def test_ida_y_vuelta_de_primera_medida_por_el_repositorio(db_session):
    item, run = _seed_world(db_session)
    evaluation_id = _seed_evaluation(db_session, item.id)
    repo = SqlAlchemyFindingRepository(db_session)
    finding = make_finding(
        item.id,
        run.id,
        type=FindingType.PRIMERA_MEDIDA,
        first_measurement=_first(),
        tension_evaluation_id=evaluation_id,
    )
    finding.publish(confidence=0.7, at=PUBLISHED_AT)
    repo.add(finding)
    db_session.flush()
    db_session.expire_all()

    loaded = repo.get_published(finding.id)

    assert loaded == finding
    assert loaded.first_measurement == _first()
    assert loaded.independent_confirmation is None
    assert loaded.tension_evaluation_id == evaluation_id


def test_ida_y_vuelta_de_confirmacion_independiente_por_el_repositorio(db_session):
    item, run = _seed_world(db_session)
    evaluation_id = _seed_evaluation(db_session, item.id)
    repo = SqlAlchemyFindingRepository(db_session)
    finding = make_finding(
        item.id,
        run.id,
        type=FindingType.CONFIRMACION_INDEPENDIENTE,
        independent_confirmation=_confirmation(),
        tension_evaluation_id=evaluation_id,
    )
    repo.add(finding)
    db_session.flush()
    db_session.expire_all()

    [loaded] = repo.unpublished_for_run(run.id)

    assert loaded == finding
    assert loaded.independent_confirmation == _confirmation()
    assert loaded.first_measurement is None


def test_un_paper_explained_vuelve_sin_payloads_nuevos(db_session):
    item, run = _seed_world(db_session)
    repo = SqlAlchemyFindingRepository(db_session)
    finding = make_finding(item.id, run.id)
    repo.add(finding)
    db_session.flush()
    db_session.expire_all()

    [loaded] = repo.unpublished_for_run(run.id)

    assert loaded.first_measurement is None
    assert loaded.independent_confirmation is None
    assert loaded.tension_evaluation_id is None


def test_evaluation_ids_with_finding(db_session):
    item, run = _seed_world(db_session)
    ev_first = _seed_evaluation(db_session, item.id)
    ev_both = _seed_evaluation(db_session, item.id, parameter="mass")
    ev_none = _seed_evaluation(db_session, item.id, parameter="period")
    repo = SqlAlchemyFindingRepository(db_session)
    published = make_finding(
        item.id,
        run.id,
        type=FindingType.PRIMERA_MEDIDA,
        first_measurement=_first(),
        tension_evaluation_id=ev_first,
    )
    published.publish(confidence=0.5, at=PUBLISHED_AT)
    for finding in (
        published,
        make_finding(
            item.id,
            run.id,
            type=FindingType.PRIMERA_MEDIDA,
            first_measurement=_first(),
            tension_evaluation_id=ev_both,
        ),
        make_finding(
            item.id,
            run.id,
            type=FindingType.CONFIRMACION_INDEPENDIENTE,
            independent_confirmation=_confirmation(),
            tension_evaluation_id=ev_both,
        ),
        make_finding(item.id, run.id),
    ):
        repo.add(finding)
    db_session.flush()

    assert repo.evaluation_ids_with_finding(FindingType.PRIMERA_MEDIDA) == frozenset(
        {ev_first, ev_both}
    )
    assert repo.evaluation_ids_with_finding(FindingType.CONFIRMACION_INDEPENDIENTE) == frozenset(
        {ev_both}
    )
    assert repo.evaluation_ids_with_finding(FindingType.PAPER_EXPLAINED) == frozenset()
    assert ev_none not in repo.evaluation_ids_with_finding(FindingType.PRIMERA_MEDIDA)
