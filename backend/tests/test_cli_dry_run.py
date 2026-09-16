"""Tests de `nocturna.cli` para el subcomando `run-night`, sin PostgreSQL.

Se dividen en dos ficheros:

- Este (`tests/test_cli_dry_run.py`): comportamiento que no necesita tocar
  PostgreSQL ni una fuente arXiv -- el camino sin `--dry-run` (no debe hacer
  nada) y el cálculo del valor por defecto de `--since`.
- `tests/db/test_cli_dry_run_db.py`: todo lo que exige ejecutar `main()` de
  verdad hasta el final -- ingesta con una fuente arXiv falsa, propagación
  de `--since`/`--categories`, errores de arXiv y la comprobación de que
  `claude_agent_sdk` no se importa durante `--dry-run`. `cli.py` no ofrece
  ningún parámetro para sustituir `ArxivClient` o la sesión de base de datos
  por un doble (`_run_ingest` construye su propio `httpx.AsyncClient` y su
  propia `unit_of_work` internamente, ver docstring de ese módulo), así que
  la única costura disponible sin tocarlo es sustituir `httpx.AsyncClient`
  por uno que sirva `httpx.MockTransport` -- y eso obliga a esos tests a
  correr contra la base de datos real de test, porque `_run_ingest` persiste
  de verdad. De ahí la separación.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from nocturna import cli
from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.domain.entities import Item

_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def _item(*, external_id: str, abstract: str) -> Item:
    return Item(
        source="arxiv",
        external_id=external_id,
        title=f"Título de {external_id}",
        abstract=abstract,
        categories=["astro-ph.EP"],
        published_at=_NOW,
        fetched_at=_NOW,
    )


def test_dry_run_imprime_vista_previa_del_abstract_truncada_en_los_largos(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`Hecho cuando` de T20 exige que `--dry-run` muestre los abstracts: sin
    esta vista previa bajo cada ítem, el reporte solo listaría títulos y no
    lo cumpliría. Un abstract largo se recorta a `_ABSTRACT_PREVIEW_CHARS`
    caracteres con marca de truncamiento (`…`); uno corto se imprime entero
    y sin la marca."""
    long_abstract = "A" * 250
    short_abstract = "Resumen corto que no necesita truncarse."
    result = IngestResult(
        fetched=2,
        new=2,
        duplicates=0,
        skipped=0,
        truncated=False,
        items=[
            _item(external_id="2609.00001", abstract=long_abstract),
            _item(external_id="2609.00002", abstract=short_abstract),
        ],
    )

    cli._print_dry_run_report(result)

    captured = capsys.readouterr().out
    lines = captured.splitlines()
    assert f"    {'A' * 200}…" in lines
    assert f"    {short_abstract}" in lines
    assert "A" * 201 not in captured


def test_run_night_sin_dry_run_devuelve_2_y_menciona_t44(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(["run-night"])

    captured = capsys.readouterr()
    assert code == 2
    assert "T44" in captured.err
    assert captured.out == ""


def test_run_night_sin_dry_run_no_toca_configuracion_ni_red_ni_base_de_datos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ninguna de las piezas que solo hacen falta para una ingesta real
    (configuración, cliente HTTP, motor de base de datos) debe invocarse
    cuando falta `--dry-run`: se sustituyen todas por dobles que revientan
    si alguien las llama, y `main()` debe devolver 2 sin tocarlas."""

    def _boom(*args, **kwargs):
        raise AssertionError("no debería llamarse sin --dry-run")

    monkeypatch.setattr(cli, "Settings", _boom)
    monkeypatch.setattr(cli, "load_pipeline_config", _boom)
    monkeypatch.setattr(cli, "create_db_engine", _boom)
    monkeypatch.setattr(httpx, "AsyncClient", _boom)

    assert cli.main(["run-night"]) == 2


def test_valor_por_defecto_de_since_no_se_calcula_al_construir_el_parser() -> None:
    """El default de `--since` en el parser es `None`, no un `datetime` ya
    calculado: si se calculara al construir el parser, quedaría fijado al
    momento de importar el módulo, no al de ejecutar `run-night`."""
    parser = cli._build_parser()

    args = parser.parse_args(["run-night", "--dry-run"])

    assert args.since is None


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # Caso normal, a media tarde.
        (datetime(2026, 9, 16, 10, 0, tzinfo=UTC), datetime(2026, 9, 15, 0, 0, tzinfo=UTC)),
        # Justo en la medianoche: "ayer" sigue siendo el día natural anterior.
        (datetime(2026, 9, 16, 0, 0, 0, tzinfo=UTC), datetime(2026, 9, 15, 0, 0, tzinfo=UTC)),
        # Cruce de año.
        (datetime(2026, 1, 1, 12, 0, tzinfo=UTC), datetime(2025, 12, 31, 0, 0, tzinfo=UTC)),
    ],
    ids=["media-tarde", "medianoche-exacta", "cruce-de-año"],
)
def test_default_since_es_ayer_a_medianoche_utc(now: datetime, expected: datetime) -> None:
    result = cli._default_since(now)

    assert result == expected
    assert result.tzinfo is UTC
