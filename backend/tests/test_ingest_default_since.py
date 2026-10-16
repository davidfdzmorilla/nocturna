"""`default_since` (T93): desde cuándo ingesta la noche cuando no hay `--since`.

Función pura: sin base de datos, sin red. La marca es el último `fetched_at`
de arXiv; la ventana nunca baja de ayer 00:00 UTC y nunca sube de
`max_catchup_days` atrás.
"""

from datetime import UTC, datetime

import pytest

from nocturna.application.use_cases.ingest_arxiv import default_since, since_was_capped

_CATCHUP = 7


@pytest.mark.parametrize(
    ("now", "last", "expected"),
    [
        pytest.param(
            datetime(2026, 10, 13, 22, 5, tzinfo=UTC),
            None,
            datetime(2026, 10, 12, tzinfo=UTC),
            id="sin-marca-es-ayer",
        ),
        pytest.param(
            datetime(2026, 10, 13, 22, 5, tzinfo=UTC),
            datetime(2026, 10, 12, 22, 5, tzinfo=UTC),
            datetime(2026, 10, 12, tzinfo=UTC),
            id="marca-de-anoche-es-ayer",
        ),
        pytest.param(
            datetime(2026, 10, 13, 22, 5, tzinfo=UTC),
            datetime(2026, 10, 9, 22, 5, tzinfo=UTC),
            datetime(2026, 10, 9, tzinfo=UTC),
            id="marca-hace-4-dias-es-esa-fecha-caso-2026-10-14",
        ),
        pytest.param(
            datetime(2026, 10, 13, 22, 5, tzinfo=UTC),
            datetime(2026, 9, 13, 22, 5, tzinfo=UTC),
            datetime(2026, 10, 6, tzinfo=UTC),
            id="marca-hace-30-dias-se-acota-a-max-catchup",
        ),
        pytest.param(
            datetime(2026, 10, 13, 22, 5, tzinfo=UTC),
            datetime(2026, 10, 13, 21, 0, tzinfo=UTC),
            datetime(2026, 10, 12, tzinfo=UTC),
            id="marca-de-hoy-es-ayer",
        ),
        pytest.param(
            datetime(2026, 12, 1, 23, 5, tzinfo=UTC),
            datetime(2026, 11, 28, 23, 5, tzinfo=UTC),
            datetime(2026, 11, 28, tzinfo=UTC),
            id="now-23-05-utc-horario-de-invierno",
        ),
        pytest.param(
            datetime(2026, 10, 13, 22, 5, tzinfo=UTC),
            datetime(2026, 10, 6, 3, 0, tzinfo=UTC),
            datetime(2026, 10, 6, tzinfo=UTC),
            id="marca-justo-en-el-limite-del-catchup",
        ),
    ],
)
def test_default_since(now: datetime, last: datetime | None, expected: datetime) -> None:
    result = default_since(now=now, last_ingest_at=last, max_catchup_days=_CATCHUP)

    assert result == expected
    assert result.tzinfo is UTC


def test_default_since_cruza_el_cambio_de_anio() -> None:
    result = default_since(
        now=datetime(2027, 1, 1, 12, 0, tzinfo=UTC),
        last_ingest_at=None,
        max_catchup_days=_CATCHUP,
    )

    assert result == datetime(2026, 12, 31, tzinfo=UTC)


def test_default_since_un_catchup_menor_acota_antes() -> None:
    result = default_since(
        now=datetime(2026, 10, 13, 22, 5, tzinfo=UTC),
        last_ingest_at=datetime(2026, 10, 9, 22, 5, tzinfo=UTC),
        max_catchup_days=2,
    )

    assert result == datetime(2026, 10, 11, tzinfo=UTC)


def test_since_was_capped_en_el_limite_no_acota_y_default_since_coincide() -> None:
    now = datetime(2026, 10, 16, 22, 5, tzinfo=UTC)
    last = datetime(2026, 10, 9, 21, 0, tzinfo=UTC)

    assert default_since(now=now, last_ingest_at=last, max_catchup_days=7) == datetime(
        2026, 10, 9, tzinfo=UTC
    )
    assert since_was_capped(now=now, last_ingest_at=last, max_catchup_days=7) is False


@pytest.mark.parametrize(
    ("last", "expected"),
    [
        (None, False),
        (datetime(2026, 10, 8, 23, 59, tzinfo=UTC), True),
        (datetime(2026, 10, 15, 3, 0, tzinfo=UTC), False),
    ],
    ids=["sin-marca", "un-dia-mas-alla-del-tope", "reciente"],
)
def test_since_was_capped(last: datetime | None, expected: bool) -> None:
    now = datetime(2026, 10, 16, 22, 5, tzinfo=UTC)

    assert since_was_capped(now=now, last_ingest_at=last, max_catchup_days=7) is expected
