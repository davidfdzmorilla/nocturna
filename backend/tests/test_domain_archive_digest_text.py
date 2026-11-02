"""T84.r, dominio puro: `DigestText`, `is_week_closed` y `digest_writer_skip_reason`.

Sin base de datos ni LLM. Fichero en español.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from helpers.archive_digest import changed, make_digest, new_planet

from nocturna.domain.archive_digest import (
    DigestSkipReason,
    DigestText,
    DigestTextStatus,
    digest_writer_skip_reason,
    is_week_closed,
)
from nocturna.domain.errors import InvariantViolation

TZ = ZoneInfo("Europe/Madrid")
RUN = uuid4()
NOW = datetime(2026, 11, 10, 2, 0, tzinfo=UTC)
CREATED = datetime(2026, 11, 10, 2, 5, tzinfo=UTC)


def _text(**overrides: object) -> DigestText:
    base: dict[str, object] = {
        "week": "2026-W42",
        "status": DigestTextStatus.WRITTEN,
        "input_sha256": "a" * 64,
        "prompt_version": "writer-digest-v1",
        "run_id": RUN,
        "created_at": CREATED,
        "title": "Un título",
        "summary": "Un resumen.",
    }
    base.update(overrides)
    return DigestText(**base)  # type: ignore[arg-type]


def _failed(**overrides: object) -> DigestText:
    return _text(status=DigestTextStatus.FAILED, title=None, summary=None, **overrides)


# --- DigestText ---------------------------------------------------------------


def test_los_estados_son_written_y_failed() -> None:
    assert {s.value for s in DigestTextStatus} == {"written", "failed"}


def test_un_texto_written_valido_se_construye() -> None:
    text = _text()
    assert text.status is DigestTextStatus.WRITTEN and text.title == "Un título"


def test_un_texto_failed_valido_no_lleva_titulo_ni_resumen() -> None:
    text = _failed()
    assert text.title is None and text.summary is None


def test_digest_text_es_inmutable() -> None:
    with pytest.raises(AttributeError):
        _text().title = "otro"  # type: ignore[misc]


@pytest.mark.parametrize("missing", ["title", "summary"])
def test_written_exige_titulo_y_resumen(missing: str) -> None:
    with pytest.raises(InvariantViolation):
        _text(**{missing: None})


@pytest.mark.parametrize("blank", ["", "   "])
def test_written_rechaza_titulo_o_resumen_en_blanco(blank: str) -> None:
    with pytest.raises(InvariantViolation):
        _text(title=blank)
    with pytest.raises(InvariantViolation):
        _text(summary=blank)


@pytest.mark.parametrize("field", ["title", "summary"])
def test_failed_no_admite_titulo_ni_resumen(field: str) -> None:
    with pytest.raises(InvariantViolation):
        _text(
            status=DigestTextStatus.FAILED,
            **{"title": None, "summary": None, field: "algo"},
        )


@pytest.mark.parametrize("week", ["2026-W5", "2026W42", "26-W42", "2026-42", "", "2026-W420"])
def test_la_semana_debe_tener_formato_yyyy_www(week: str) -> None:
    with pytest.raises(InvariantViolation):
        _text(week=week)


# --- is_week_closed -----------------------------------------------------------


def test_domingo_23_59_la_semana_sigue_abierta() -> None:
    # W42 de 2026: lunes 12 - domingo 18 de octubre (CEST, UTC+2).
    sunday_2359 = datetime(2026, 10, 18, 21, 59, tzinfo=UTC)
    assert is_week_closed("2026-W42", sunday_2359, TZ) is False


def test_lunes_00_00_la_semana_esta_cerrada() -> None:
    monday_0000 = datetime(2026, 10, 18, 22, 0, tzinfo=UTC)  # 00:00 del lunes 19, CEST
    assert is_week_closed("2026-W42", monday_0000, TZ) is True


def test_la_semana_en_curso_y_las_futuras_estan_abiertas() -> None:
    assert is_week_closed("2026-W46", NOW, TZ) is False
    assert is_week_closed("2026-W47", NOW, TZ) is False


def test_las_pasadas_estan_cerradas() -> None:
    assert is_week_closed("2026-W40", NOW, TZ) is True


def test_cambio_de_hora_de_otono_el_cierre_es_la_medianoche_local() -> None:
    # W43 de 2026 contiene el fin del horario de verano (domingo 25 de octubre):
    # el lunes 26 00:00 local son las 23:00 UTC del domingo, no las 22:00.
    assert is_week_closed("2026-W43", datetime(2026, 10, 25, 22, 59, tzinfo=UTC), TZ) is False
    assert is_week_closed("2026-W43", datetime(2026, 10, 25, 23, 0, tzinfo=UTC), TZ) is True


def test_cambio_de_hora_de_primavera_el_cierre_es_la_medianoche_local() -> None:
    # W13 de 2026 contiene el inicio del horario de verano (domingo 29 de marzo).
    assert is_week_closed("2026-W13", datetime(2026, 3, 29, 21, 59, tzinfo=UTC), TZ) is False
    assert is_week_closed("2026-W13", datetime(2026, 3, 29, 22, 0, tzinfo=UTC), TZ) is True


def test_la_semana_44_de_2026_cierra_el_lunes_2_de_noviembre_a_las_00_00_locales() -> None:
    assert is_week_closed("2026-W44", datetime(2026, 11, 1, 22, 59, tzinfo=UTC), TZ) is False
    assert is_week_closed("2026-W44", datetime(2026, 11, 1, 23, 0, tzinfo=UTC), TZ) is True


def test_is_week_closed_rechaza_semana_invalida() -> None:
    with pytest.raises(ValueError):
        is_week_closed("2026-W5", NOW, TZ)


# --- digest_writer_skip_reason ------------------------------------------------


def _digest(week: str = "2026-W42"):
    return make_digest(week, changed("A b", week))


def _reason(digest, *, existing=None, since="2026-W40", now=NOW):
    return digest_writer_skip_reason(digest, now=now, tz=TZ, since_week=since, existing=existing)


def test_los_motivos_son_los_cinco_del_plan() -> None:
    assert {r.value for r in DigestSkipReason} == {
        "before_since",
        "open_week",
        "no_transitions",
        "already_written",
        "writer_failed",
    }


def test_semana_elegible_no_tiene_motivo() -> None:
    assert _reason(_digest()) is None


def test_semana_anterior_a_since_week() -> None:
    assert _reason(_digest("2026-W39")) is DigestSkipReason.BEFORE_SINCE


def test_la_propia_since_week_es_elegible() -> None:
    assert _reason(_digest("2026-W40")) is None


def test_semana_abierta() -> None:
    assert _reason(_digest("2026-W46")) is DigestSkipReason.OPEN_WEEK


def test_semana_cerrada_sin_transiciones() -> None:
    empty = make_digest("2026-W42")
    assert _reason(empty) is DigestSkipReason.NO_TRANSITIONS


def test_ya_redactada() -> None:
    assert _reason(_digest(), existing=_text()) is DigestSkipReason.ALREADY_WRITTEN


def test_redactor_fallido() -> None:
    assert _reason(_digest(), existing=_failed()) is DigestSkipReason.WRITER_FAILED


def test_orden_before_since_antes_que_open_week() -> None:
    far_past = datetime(2026, 9, 1, tzinfo=UTC)
    # W39 abierta (now = 1 sep) y anterior a since: gana before_since.
    assert _reason(_digest("2026-W39"), now=far_past) is DigestSkipReason.BEFORE_SINCE


def test_orden_open_week_antes_que_no_transitions() -> None:
    assert _reason(make_digest("2026-W46")) is DigestSkipReason.OPEN_WEEK


def test_orden_no_transitions_antes_que_already_written() -> None:
    empty = make_digest("2026-W42")
    assert _reason(empty, existing=_text()) is DigestSkipReason.NO_TRANSITIONS


def test_orden_before_since_antes_que_already_written() -> None:
    assert _reason(_digest("2026-W39"), existing=_text(week="2026-W39")) is (
        DigestSkipReason.BEFORE_SINCE
    )


def test_un_planeta_nuevo_cuenta_como_transicion() -> None:
    digest = make_digest("2026-W42", new_planet("N b", "2026-W42"))
    assert _reason(digest) is None
