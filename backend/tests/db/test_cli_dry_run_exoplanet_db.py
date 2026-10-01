"""`run-night --dry-run` muestra `[exo]` y el reparto reader-v3/v2 (T79).

Mismo montaje que `test_cli_dry_run_db.py` (feed Atom con `httpx.MockTransport`,
vía `api`, contra `nocturna_test`). Sin Claude ni red.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import httpx
import pytest
from test_cli_dry_run_db import _load, _patch_arxiv_transport, _Recorder

from nocturna import cli
from nocturna.cli import main

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "arxiv"

# Feed `feed_three_entries.xml`: 2609.17526 (EP), 2609.17505 (EP+SR), 2609.17383 (HE+GA).
_TITLE_17383 = b"X-ray to Mid-IR Spectral Energy Distributions"


@pytest.fixture(autouse=True)
def _via_api(monkeypatch: pytest.MonkeyPatch) -> None:
    real = cli.load_pipeline_config

    def _patched(*args: object, **kwargs: object):
        config = real(*args, **kwargs)
        arxiv = config.sources.arxiv.model_copy(update={"ingest_via": "api"})
        sources = config.sources.model_copy(update={"arxiv": arxiv})
        return config.model_copy(update={"sources": sources})

    monkeypatch.setattr(cli, "load_pipeline_config", _patched)


@pytest.fixture(autouse=True)
def _use_test_database(monkeypatch: pytest.MonkeyPatch, test_database_url: str) -> None:
    assert test_database_url.rsplit("/", 1)[-1] == "nocturna_test"
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)


def _run(monkeypatch, capsys, feed: bytes) -> str:
    recorder = _Recorder(httpx.Response(200, content=feed))
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
    assert code == 0
    return capsys.readouterr().out


def _line_of(out: str, external_id: str) -> str:
    return next(line for line in out.splitlines() if external_id in line)


def test_dry_run_marca_exo_el_item_que_casa_y_cuenta_un_reader_v3(
    monkeypatch, db_session_factory, capsys
):
    """2609.17505 (astro-ph.EP) trata de los anfitriones TOI-732, K2-18...: casa por designación."""
    out = _run(monkeypatch, capsys, _load("feed_three_entries.xml"))

    assert _line_of(out, "2609.17505").rstrip().endswith("[exo]")
    assert "[exo]" not in _line_of(out, "2609.17526")
    assert "[exo]" not in _line_of(out, "2609.17383")
    assert "de los 3 a leer, 1 con reader-v3 y 2 con reader-v2" in out


def test_dry_run_sin_ningun_item_exoplanetario_no_marca_y_todo_es_reader_v2(
    monkeypatch, db_session_factory, capsys
):
    feed = _load("feed_three_entries.xml").replace(b"TOI-", b"ZOI-").replace(b"K2-18", b"Q9-18")

    out = _run(monkeypatch, capsys, feed)

    assert "[exo]" not in out
    assert "de los 3 a leer, 0 con reader-v3 y 3 con reader-v2" in out


def test_dry_run_item_con_match_fuera_de_measurement_categories_es_exo_pero_reader_v2(
    monkeypatch, db_session_factory, capsys
):
    """2609.17383 es astro-ph.HE/GA: casa con el filtro, pero no está en
    `[reader] measurement_categories` (astro-ph.EP), así que lee con v2: dos
    ítems `[exo]` y solo uno con reader-v3."""
    feed = _load("feed_three_entries.xml").replace(_TITLE_17383, b"Mass of TOI-6981 b")

    out = _run(monkeypatch, capsys, feed)

    assert _line_of(out, "2609.17383").rstrip().endswith("[exo]")
    assert _line_of(out, "2609.17505").rstrip().endswith("[exo]")
    assert out.count("[exo]") == 2
    assert "de los 3 a leer, 1 con reader-v3 y 2 con reader-v2" in out


_SUBPROCESS_SCRIPT = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    import httpx

    payload = Path(sys.argv[1]).read_bytes()

    def handle(request):
        return httpx.Response(200, content=payload)

    _real = httpx.AsyncClient

    def _fake(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handle)
        return _real(*args, **kwargs)

    httpx.AsyncClient = _fake

    from nocturna.cli import main

    code = main(["run-night", "--dry-run", "--since", "2000-01-01",
                 "--categories", "astro-ph.EP,astro-ph.GA"])
    if code != 0:
        print(f"exit code inesperado: {code}", file=sys.stderr)
        sys.exit(1)
    if "claude_agent_sdk" in sys.modules:
        print("claude_agent_sdk SE IMPORTO durante --dry-run", file=sys.stderr)
        sys.exit(1)
    print("OK")
    """
)


def test_dry_run_con_exo_sigue_sin_importar_claude_agent_sdk(test_database_url, db_session_factory):
    env = dict(os.environ)
    env["NOCTURNA_DATABASE_URL"] = test_database_url

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _SUBPROCESS_SCRIPT,
            # Solo se comprueba que el camino nuevo (marca, reparto v3/v2) no arrastra el SDK.
            str(FIXTURES_DIR / "oai" / "list_records_ep.xml"),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "OK" in result.stdout
    assert "con reader-v3" in result.stdout
