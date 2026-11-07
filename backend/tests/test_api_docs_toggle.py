"""T109: `api_docs_enabled` y motor de solo lectura de la API."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from nocturna.api import deps
from nocturna.api.app import create_app
from nocturna.infrastructure.config import Settings
from nocturna.infrastructure.db import session as session_module
from nocturna.infrastructure.db.session import create_db_engine

_DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


def test_docs_disabled_returns_404_and_keeps_health() -> None:
    app = create_app(Settings(api_docs_enabled=False))
    client = TestClient(app)
    for path in _DOC_PATHS:
        assert client.get(path).status_code == 404
    assert client.get("/health").status_code != 404


def test_docs_enabled_by_default() -> None:
    assert Settings().api_docs_enabled is True
    client = TestClient(create_app(Settings()))
    for path in _DOC_PATHS:
        assert client.get(path).status_code == 200


def test_api_docs_enabled_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOCTURNA_API_DOCS_ENABLED", "false")
    assert Settings().api_docs_enabled is False


def test_get_session_factory_uses_read_only_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def _fake(settings: Settings, **kwargs: bool) -> object:
        calls.append(dict(kwargs))
        return create_db_engine(settings)

    monkeypatch.setattr(deps, "create_db_engine", _fake)
    deps.get_session_factory.cache_clear()
    try:
        factory = deps.get_session_factory()
    finally:
        deps.get_session_factory.cache_clear()
    assert isinstance(factory, sessionmaker)
    assert calls == [{"read_only": True}]


def test_create_db_engine_read_only_option(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []

    def _fake_create_engine(url: str, **kwargs: dict[str, object]) -> object:
        captured.append(kwargs["connect_args"])
        return object()

    monkeypatch.setattr(session_module, "create_engine", _fake_create_engine)
    settings = Settings()
    session_module.create_db_engine(settings, read_only=True)
    session_module.create_db_engine(settings)
    assert captured[0]["options"] == "-c timezone=UTC -c default_transaction_read_only=on"
    assert captured[1]["options"] == "-c timezone=UTC"
