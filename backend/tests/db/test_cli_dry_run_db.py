"""Tests de `nocturna.cli::main` para `run-night --dry-run`, contra PostgreSQL real.

`cli.py` no ofrece ningún parámetro para inyectar un `ArxivSource` falso ni
una sesión de base de datos alternativa: `_run_ingest` construye su propio
`httpx.AsyncClient`, su propio `ArxivClient` y su propia `unit_of_work`
internamente (ver su docstring). Sin tocar `cli.py`, la única costura
disponible es sustituir `httpx.AsyncClient` por una versión que sirve
`httpx.MockTransport` (`_patch_arxiv_transport`); y como `_run_ingest` sí
persiste de verdad, eso obliga a estos tests a correr contra
`nocturna_test` (fixtures de `tests/db/conftest.py`), con limpieza vía
`db_session_factory`.

Los tests que no necesitan una fuente arXiv ni base de datos (el camino sin
`--dry-run`, el cálculo del valor por defecto de `--since`) están en
`tests/test_cli_dry_run.py` (nombre de fichero distinto a propósito: pytest,
sin `__init__.py` en `tests/`, no admite dos módulos de test con el mismo
nombre base en directorios distintos).
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from nocturna import cli
from nocturna.cli import main
from nocturna.infrastructure.arxiv.retry import RetryPolicy
from nocturna.infrastructure.config import load_pipeline_config

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "arxiv"


def _load(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


class _Recorder:
    """Sirve siempre la misma respuesta y guarda cada petición recibida."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._response


def _patch_arxiv_transport(
    monkeypatch: pytest.MonkeyPatch, handle: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Sustituye `httpx.AsyncClient` por uno que sirve `handle` vía
    `httpx.MockTransport`, sin tocar `cli.py` (ver docstring del módulo)."""
    real_async_client = httpx.AsyncClient

    def _fake_async_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handle)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _fake_async_client)


@pytest.fixture(autouse=True)
def _use_test_database(monkeypatch: pytest.MonkeyPatch, test_database_url: str) -> None:
    """Apunta la `Settings()` que construye `main()` a `nocturna_test`, como
    hace `tests/db/conftest.py::_database_url_env` para Alembic."""
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)


async def _instant_sleep(_delay_s: float) -> None:
    return None


@pytest.fixture
def _fast_arxiv_network_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evita las dos esperas reales que `_run_ingest` produce con más de una
    petición a arXiv, sin tocar `cli.py`: ambas se arreglan parcheando
    símbolos propios de `nocturna.cli`, no internos de terceros.

    1. **El backoff de `Retrier`.** `cli.py` no ofrece ninguna costura para
       inyectar `sleep=...` (`_run_ingest` construye su propio `Retrier` con
       los valores por defecto, ver su docstring), pero sí es dueño de
       `arxiv_retry_policy_from_config`: se parchea para que devuelva la
       misma `max_attempts` (el camino real de "cuatro peticiones,
       agotamiento, mensaje legible" sigue intacto) con un `base_delay_s`
       minúsculo. `RetryPolicy` es una dataclass sin validación propia -- a
       diferencia de `ArxivConfig`, que si exigiera este mismo valor
       fallaría al cargar -- así que admite un backoff nominal irrelevante
       sin rodeos. Sin este parche, hasta ~52 s reales de backoff
       (`retry_base_delay_s = 5.0`, `retry_max_attempts = 4` en
       `config/pipeline.toml`, con jitter).

    2. **El espaciado de cortesía de `RateLimiter` entre reintentos.**
       Hallazgo posterior al primer intento de arreglo de este test: antes
       de T60.b, `ArxivClient._get` solo llamaba `limiter.acquire()` una vez
       por página, y la primera llamada de un `RateLimiter` nunca espera
       (`_last_request_at` es `None`), así que este límite nunca se notaba
       aquí. Desde T60.b, `_get` adquiere el limitador dentro de CADA
       intento -- para que el reintento también respete el espaciado real
       de arXiv, ver el docstring de `client.py::_get` -- así que el
       segundo intento en adelante siempre tiene que esperar
       `MIN_REQUEST_INTERVAL_S = 3.0 s` reales de verdad, sin relación
       ninguna con la política de reintento. `cli.py` construye ese
       `RateLimiter` con su `sleep` por defecto (`RateLimiter(MIN_REQUEST_INTERVAL_S)`,
       sin costura), igual que con `Retrier`; se parchea `cli.RateLimiter`
       -- el símbolo importado en `nocturna.cli`, no `infrastructure.arxiv.rate_limit`
       -- por una fábrica que conserva `min_interval_s` (así que
       `test_hay_una_espera_de_3s_entre_paginas`, en `test_arxiv_client.py`,
       sigue siendo la única prueba de que ese espaciado existe de verdad)
       pero fija `sleep=_instant_sleep`."""
    real_policy_from_config = cli.arxiv_retry_policy_from_config

    def _fast_policy_from_config(config: object) -> RetryPolicy:
        policy = real_policy_from_config(config)
        return RetryPolicy(
            max_attempts=policy.max_attempts,
            base_delay_s=0.001,
            max_elapsed_s=1.0,
        )

    monkeypatch.setattr(cli, "arxiv_retry_policy_from_config", _fast_policy_from_config)

    real_rate_limiter_cls = cli.RateLimiter

    def _fast_rate_limiter(min_interval_s: float, **kwargs: object) -> object:
        kwargs.setdefault("sleep", _instant_sleep)
        return real_rate_limiter_cls(min_interval_s, **kwargs)

    monkeypatch.setattr(cli, "RateLimiter", _fast_rate_limiter)


def test_dry_run_con_fuente_falsa_imprime_una_linea_por_item_y_el_resumen_y_devuelve_0(
    monkeypatch: pytest.MonkeyPatch,
    db_session_factory: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recorder = _Recorder(httpx.Response(200, content=_load("feed_three_entries.xml")))
    _patch_arxiv_transport(monkeypatch, recorder.handle)

    code = main(
        [
            "run-night",
            "--dry-run",
            "--since",
            "2000-01-01",
            "--categories",
            "astro-ph.EP,astro-ph.GA",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert len(recorder.requests) == 1
    for external_id in ("2609.17526", "2609.17505", "2609.17383"):
        assert external_id in captured.out
    assert "fetched=3 new=3 duplicates=0 skipped=0" in captured.out
    assert "AVISO" not in captured.out


def test_categories_pasadas_por_cli_llegan_a_la_peticion_arxiv(
    monkeypatch: pytest.MonkeyPatch, db_session_factory: object
) -> None:
    recorder = _Recorder(httpx.Response(200, content=_load("feed_three_entries.xml")))
    _patch_arxiv_transport(monkeypatch, recorder.handle)

    code = main(
        [
            "run-night",
            "--dry-run",
            "--since",
            "2000-01-01",
            "--categories",
            "astro-ph.HE,astro-ph.SR",
        ]
    )

    assert code == 0
    assert len(recorder.requests) == 1
    assert recorder.requests[0].url.params["search_query"] == "cat:astro-ph.HE OR cat:astro-ph.SR"


def test_since_pasado_por_cli_filtra_los_items_anteriores(
    monkeypatch: pytest.MonkeyPatch,
    db_session_factory: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Las tres entradas de la fixture se publicaron el 2026-09-15: un
    `--since` posterior debe dejar la ingesta en cero, prueba de que el
    valor pasado por CLI llega de verdad hasta `ArxivClient.fetch_new`."""
    recorder = _Recorder(httpx.Response(200, content=_load("feed_three_entries.xml")))
    _patch_arxiv_transport(monkeypatch, recorder.handle)

    code = main(
        [
            "run-night",
            "--dry-run",
            "--since",
            "2026-09-16",
            "--categories",
            "astro-ph.EP,astro-ph.GA",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "fetched=0 new=0 duplicates=0 skipped=0" in captured.out


def test_sin_since_ni_categories_usa_los_valores_por_defecto(
    monkeypatch: pytest.MonkeyPatch,
    db_session_factory: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frozen_now = datetime(2026, 9, 16, 10, 0, 0, tzinfo=UTC)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return frozen_now if tz is None else frozen_now.astimezone(tz)

    recorder = _Recorder(httpx.Response(200, content=_load("feed_three_entries.xml")))
    _patch_arxiv_transport(monkeypatch, recorder.handle)
    monkeypatch.setattr(cli, "datetime", _FrozenDatetime)

    code = cli.main(["run-night", "--dry-run"])

    captured = capsys.readouterr()
    assert code == 0
    assert len(recorder.requests) == 1
    expected_categories = load_pipeline_config().sources.arxiv.categories
    expected_query = " OR ".join(f"cat:{category}" for category in expected_categories)
    assert recorder.requests[0].url.params["search_query"] == expected_query
    # Con "ahora" congelado a 2026-09-16T10:00Z, el default es
    # 2026-09-15T00:00Z: anterior a las tres publicaciones de la fixture.
    assert "fetched=3 new=3 duplicates=0 skipped=0" in captured.out


def test_arxiv_unavailable_produce_mensaje_legible_sin_traza_tras_agotar_los_reintentos(
    monkeypatch: pytest.MonkeyPatch,
    db_session_factory: object,
    capsys: pytest.CaptureFixture[str],
    _fast_arxiv_network_waits: None,
) -> None:
    """Un 500 persistente ya no falla a la primera: T60.b introduce
    reintentos (`config/pipeline.toml` real: 4 intentos), así que este caso
    dispara 4 peticiones, todas a la misma página, antes de que `--dry-run`
    devuelva 1 con un mensaje legible. Antes de T60.b esto era una única
    petición sin reintento -- ver `tests/test_arxiv_client.py` para el
    detalle del backoff en sí; aquí solo importa que `cli.py` propaga el
    fallo final igual que siempre. El número de peticiones esperado se lee
    de `config/pipeline.toml` en vez de escribirse a mano: `retry_max_attempts`
    ya está congelado por `test_config.py::test_carga_el_pipeline_toml_del_repositorio`,
    y duplicar aquí "4" acopla este test al valor del TOML en dos sitios
    sin necesidad -- si se recalibra, este test debe seguir describiendo
    "agota los reintentos configurados", no un número concreto."""
    expected_attempts = load_pipeline_config().sources.arxiv.retry_max_attempts
    recorder = _Recorder(httpx.Response(500, content=b"internal error"))
    _patch_arxiv_transport(monkeypatch, recorder.handle)

    code = main(
        [
            "run-night",
            "--dry-run",
            "--since",
            "2000-01-01",
            "--categories",
            "astro-ph.EP,astro-ph.GA",
        ]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert len(recorder.requests) == expected_attempts, (
        "debe agotar sources.arxiv.retry_max_attempts de config/pipeline.toml"
    )
    assert "Traceback" not in captured.err
    assert "arXiv" in captured.err
    assert captured.out == ""


def test_arxiv_feed_error_produce_mensaje_legible_sin_traza_y_sin_reintentos(
    monkeypatch: pytest.MonkeyPatch,
    db_session_factory: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recorder = _Recorder(httpx.Response(200, content=_load("feed_error.xml")))
    _patch_arxiv_transport(monkeypatch, recorder.handle)

    code = main(
        [
            "run-night",
            "--dry-run",
            "--since",
            "2000-01-01",
            "--categories",
            "astro-ph.EP,astro-ph.GA",
        ]
    )

    captured = capsys.readouterr()
    assert code != 0
    assert len(recorder.requests) == 1, "no debe reintentar"
    assert "Traceback" not in captured.err
    assert "arXiv" in captured.err
    assert captured.out == ""


_SUBPROCESS_SCRIPT = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    import httpx

    payload = Path(sys.argv[1]).read_bytes()

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    _real_async_client = httpx.AsyncClient

    def _fake_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handle)
        return _real_async_client(*args, **kwargs)

    httpx.AsyncClient = _fake_async_client

    from nocturna.cli import main

    code = main(
        [
            "run-night",
            "--dry-run",
            "--since",
            "2000-01-01",
            "--categories",
            "astro-ph.EP,astro-ph.GA",
        ]
    )
    if code != 0:
        print(f"exit code inesperado: {code}", file=sys.stderr)
        sys.exit(1)
    if "claude_agent_sdk" in sys.modules:
        print("claude_agent_sdk SE IMPORTO durante --dry-run", file=sys.stderr)
        sys.exit(1)
    print("OK")
    """
)


def test_claude_agent_sdk_no_se_importa_durante_dry_run(
    test_database_url: str, db_session_factory: object
) -> None:
    """El test que más importa de este fichero (ver el plan): tras un
    `run-night --dry-run` real, `claude_agent_sdk` no debe aparecer en
    `sys.modules`. Corre en un subproceso interprete-limpio a propósito: si
    otro test de la sesión ya importó `claude_agent_sdk` (por ejemplo uno
    que ejercite `LLMProvider`), `sys.modules` de *este* proceso ya lo
    tendría y la aserción pasaría sin probar nada. Un subproceso arranca
    desde `sys.modules` vacío de verdad.
    """
    env = dict(os.environ)
    env["NOCTURNA_DATABASE_URL"] = test_database_url

    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT, str(FIXTURES_DIR / "feed_three_entries.xml")],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"subproceso falló (code={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "OK" in result.stdout
    assert "Traceback" not in result.stderr
