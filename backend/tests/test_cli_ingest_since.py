"""T93: `_resolve_since` calcula `since` desde la marca de la base, salvo `--since`.

Sin red ni base de datos: repositorio, unidad de trabajo e `IngestArxiv` son dobles.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from nocturna import cli
from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.infrastructure.config import load_pipeline_config

REAL_PIPELINE_TOML = Path(__file__).resolve().parents[2] / "config" / "pipeline.toml"


class _Session:
    def __init__(self) -> None:
        self.open = False


class _Repo:
    def __init__(self, last: datetime | None, *, forbid: bool = False) -> None:
        self._last = last
        self._forbid = forbid
        self.sources_asked: list[str] = []
        self.session_open_when_asked: list[bool] = []

    def __call__(self, session: _Session) -> _Repo:
        self._session = session
        return self

    def last_fetched_at(self, source: str) -> datetime | None:
        if self._forbid:
            raise AssertionError("--since explícito no debe consultar el repositorio")
        self.sources_asked.append(source)
        self.session_open_when_asked.append(self._session.open)
        return self._last


class _Ingest:
    since_seen: list[datetime] = []

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def __call__(self, *, since: datetime, categories: object, max_results: int):
        type(self).since_seen.append(since)
        return IngestResult(fetched=0, new=0, duplicates=0, skipped=0, truncated=False, items=[])


@pytest.fixture(autouse=True)
def uow(monkeypatch: pytest.MonkeyPatch) -> list[_Session]:
    """Sustituye `unit_of_work` por una que registra apertura y cierre."""
    sessions: list[_Session] = []

    @contextmanager
    def fake_uow(_factory: object, *, commit: bool = True) -> Iterator[_Session]:
        assert commit is False, "la lectura de la marca no debe poder confirmar"
        session = _Session()
        session.open = True
        sessions.append(session)
        try:
            yield session
        finally:
            session.open = False

    monkeypatch.setattr(cli, "unit_of_work", fake_uow)
    return sessions


@pytest.fixture
def ingest(monkeypatch: pytest.MonkeyPatch) -> type[_Ingest]:
    _Ingest.since_seen = []
    monkeypatch.setattr(cli, "IngestArxiv", _Ingest)
    return _Ingest


@pytest.mark.anyio
async def test_sin_since_consulta_la_marca_de_arxiv_y_la_usa(
    monkeypatch: pytest.MonkeyPatch, ingest: type[_Ingest]
) -> None:
    last = datetime.now(UTC) - timedelta(days=4)
    repo = _Repo(last)
    monkeypatch.setattr(cli, "SqlAlchemyItemRepository", repo)
    config = load_pipeline_config(REAL_PIPELINE_TOML)

    since, _ = cli._resolve_since(SimpleNamespace(), since=None, config=config)  # type: ignore[arg-type]
    await cli._ingest_into(
        SimpleNamespace(),  # type: ignore[arg-type]
        since=since,
        categories=["astro-ph.EP"],
        config=config,
    )

    assert repo.sources_asked == ["arxiv"]
    assert ingest.since_seen == [datetime.combine(last.date(), datetime.min.time(), tzinfo=UTC)]


@pytest.mark.anyio
async def test_sin_since_registra_el_evento_arxiv_ingest_since(
    monkeypatch: pytest.MonkeyPatch, ingest: type[_Ingest], caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(cli, "SqlAlchemyItemRepository", _Repo(None))
    monkeypatch.setattr(cli._logger, "disabled", False)
    caplog.set_level(logging.INFO)
    config = load_pipeline_config(REAL_PIPELINE_TOML)

    since, _ = cli._resolve_since(SimpleNamespace(), since=None, config=config)  # type: ignore[arg-type]
    await cli._ingest_into(
        SimpleNamespace(),  # type: ignore[arg-type]
        since=since,
        categories=["astro-ph.EP"],
        config=config,
    )

    eventos = [r for r in caplog.records if getattr(r, "event", None) == "arxiv.ingest_since"]
    assert len(eventos) == 1
    for campo in ("since", "last_ingest_at", "capped"):
        assert campo in eventos[0].__dict__


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("dias_atras", "capped_esperado"),
    [(None, False), (4, False), (400, True)],
    ids=["sin-marca", "marca-reciente", "marca-anterior-al-tope"],
)
async def test_el_evento_arxiv_ingest_since_informa_capped_segun_el_tope(
    monkeypatch: pytest.MonkeyPatch,
    ingest: type[_Ingest],
    caplog: pytest.LogCaptureFixture,
    dias_atras: int | None,
    capped_esperado: bool,
) -> None:
    last = None if dias_atras is None else datetime.now(UTC) - timedelta(days=dias_atras)
    monkeypatch.setattr(cli, "SqlAlchemyItemRepository", _Repo(last))
    monkeypatch.setattr(cli._logger, "disabled", False)
    caplog.set_level(logging.INFO)
    config = load_pipeline_config(REAL_PIPELINE_TOML)

    since, _ = cli._resolve_since(SimpleNamespace(), since=None, config=config)  # type: ignore[arg-type]
    await cli._ingest_into(
        SimpleNamespace(),  # type: ignore[arg-type]
        since=since,
        categories=["astro-ph.EP"],
        config=config,
    )

    eventos = [r for r in caplog.records if getattr(r, "event", None) == "arxiv.ingest_since"]
    assert len(eventos) == 1
    assert eventos[0].capped is capped_esperado  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_since_explicito_manda_y_no_consulta_el_repositorio(
    monkeypatch: pytest.MonkeyPatch, ingest: type[_Ingest]
) -> None:
    monkeypatch.setattr(cli, "SqlAlchemyItemRepository", _Repo(None, forbid=True))
    explicit = datetime(2026, 9, 1, tzinfo=UTC)
    config = load_pipeline_config(REAL_PIPELINE_TOML)

    since, _ = cli._resolve_since(SimpleNamespace(), since=explicit, config=config)  # type: ignore[arg-type]
    await cli._ingest_into(
        SimpleNamespace(),  # type: ignore[arg-type]
        since=since,
        categories=["astro-ph.EP"],
        config=config,
    )

    assert ingest.since_seen == [explicit]


@pytest.mark.anyio
async def test_la_marca_se_lee_en_una_sesion_ya_cerrada_antes_de_la_descarga(
    monkeypatch: pytest.MonkeyPatch, ingest: type[_Ingest], uow: list[_Session]
) -> None:
    repo = _Repo(datetime.now(UTC) - timedelta(days=2))
    monkeypatch.setattr(cli, "SqlAlchemyItemRepository", repo)
    config = load_pipeline_config(REAL_PIPELINE_TOML)
    ingest_session = _Session()
    ingest_session.open = True

    since, _ = cli._resolve_since(SimpleNamespace(), since=None, config=config)  # type: ignore[arg-type]

    assert repo.session_open_when_asked == [True]
    assert len(uow) == 1
    assert uow[0].open is False  # cerrada antes de que `_ingest_into` descargue
    await cli._ingest_into(
        ingest_session,  # type: ignore[arg-type]
        since=since,
        categories=["astro-ph.EP"],
        config=config,
    )
    assert ingest.since_seen == [since]
    assert len(uow) == 1  # la ingesta no abre otra sesión para consultar la marca


@pytest.mark.parametrize(
    ("dias_atras", "origen"),
    [(None, "ayer, sin ingesta previa"), (0, "ayer"), (1, "ayer"), (4, "desde la última ingesta")],
)
def test_la_etiqueta_distingue_ayer_de_desde_la_ultima_ingesta(
    monkeypatch: pytest.MonkeyPatch, dias_atras: int | None, origen: str
) -> None:
    last = None if dias_atras is None else datetime.now(UTC) - timedelta(days=dias_atras)
    monkeypatch.setattr(cli, "SqlAlchemyItemRepository", _Repo(last))
    config = load_pipeline_config(REAL_PIPELINE_TOML)

    _, etiqueta = cli._resolve_since(SimpleNamespace(), since=None, config=config)  # type: ignore[arg-type]

    assert etiqueta.startswith(origen)


def test_cli_ya_no_expone_default_since_propio() -> None:
    assert not hasattr(cli, "_default_since")
