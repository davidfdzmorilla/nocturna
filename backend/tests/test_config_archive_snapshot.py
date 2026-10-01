"""`[sources.exoplanet_archive.snapshot]` de `pipeline.toml` (T81).

Los casos parten del TOML real y sustituyen un valor en `tmp_path`; ninguno
modifica `config/pipeline.toml`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from nocturna.infrastructure.config import (
    _COURTESY_CEILING_S,
    _HTTP_TIMEOUT_CEILING_S,
    ArchiveSnapshotConfig,
    load_pipeline_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_PIPELINE_TOML = REPO_ROOT / "config" / "pipeline.toml"
_SECTION = re.compile(
    r"^\[sources\.exoplanet_archive\.snapshot\]\n.*?(?=^\[)", re.DOTALL | re.MULTILINE
)
VALID = {
    "max_requests": "6",
    "request_timeout_s": "120.0",
    "max_response_bytes": "67108864",
    "planet_batch_size": "75",
    "max_change_fraction": "0.10",
}


def _toml(tmp_path: Path, **overrides: str | None) -> Path:
    """TOML real con la seccion del snapshot reescrita; `None` quita la clave."""
    values = {**VALID, **overrides}
    body = "".join(f"{k} = {v}\n" for k, v in values.items() if v is not None)
    text = REAL_PIPELINE_TOML.read_text(encoding="utf-8")
    assert _SECTION.search(text), "el TOML real debe tener la seccion del snapshot"
    new = f"[sources.exoplanet_archive.snapshot]\n{body}\n"
    path = tmp_path / "pipeline.toml"
    path.write_text(_SECTION.sub(lambda _m: new, text, count=1), encoding="utf-8")
    return path


def test_el_toml_real_carga_la_seccion_del_snapshot():
    snap = load_pipeline_config(REAL_PIPELINE_TOML).sources.exoplanet_archive.snapshot

    assert isinstance(snap, ArchiveSnapshotConfig)
    assert snap.max_requests == 6
    assert snap.request_timeout_s == 120.0
    assert snap.max_response_bytes == 64 * 1024 * 1024
    assert snap.planet_batch_size == 75
    assert snap.max_change_fraction == pytest.approx(0.10)


def test_los_valores_de_referencia_de_este_fichero_cargan(tmp_path):
    snap = load_pipeline_config(_toml(tmp_path)).sources.exoplanet_archive.snapshot
    assert snap.max_requests == 6


def test_falta_la_seccion_snapshot_falla(tmp_path):
    text = REAL_PIPELINE_TOML.read_text(encoding="utf-8")
    path = tmp_path / "pipeline.toml"
    path.write_text(_SECTION.sub("", text, count=1), encoding="utf-8")

    with pytest.raises(ValidationError, match="snapshot"):
        load_pipeline_config(path)


@pytest.mark.parametrize("key", list(VALID))
def test_falta_una_clave_del_snapshot_y_falla_sin_valor_por_defecto(tmp_path, key):
    with pytest.raises(ValidationError, match=key):
        load_pipeline_config(_toml(tmp_path, **{key: None}))


def test_clave_extra_en_snapshot_falla(tmp_path):
    with pytest.raises(ValidationError):
        load_pipeline_config(_toml(tmp_path, extra="1"))


@pytest.mark.parametrize("value", ["2", "1", "0", "-3"])
def test_max_requests_menor_que_3_falla(tmp_path, value):
    with pytest.raises(ValidationError, match="max_requests"):
        load_pipeline_config(_toml(tmp_path, max_requests=value))


def test_max_requests_igual_a_3_es_valido(tmp_path):
    cfg = load_pipeline_config(_toml(tmp_path, max_requests="3"))
    assert cfg.sources.exoplanet_archive.snapshot.max_requests == 3


@pytest.mark.parametrize("value", ["0", "-1", "101", "1000"])
def test_planet_batch_size_fuera_de_1_a_100_falla(tmp_path, value):
    with pytest.raises(ValidationError, match="planet_batch_size"):
        load_pipeline_config(_toml(tmp_path, planet_batch_size=value))


@pytest.mark.parametrize("value", ["1", "100"])
def test_planet_batch_size_en_los_extremos_es_valido(tmp_path, value):
    cfg = load_pipeline_config(_toml(tmp_path, planet_batch_size=value))
    assert cfg.sources.exoplanet_archive.snapshot.planet_batch_size == int(value)


@pytest.mark.parametrize("value", ["0", "0.0", "-0.1", "1.01", "2"])
def test_max_change_fraction_fuera_de_0_1_falla(tmp_path, value):
    with pytest.raises(ValidationError, match="max_change_fraction"):
        load_pipeline_config(_toml(tmp_path, max_change_fraction=value))


@pytest.mark.parametrize("value", ["0.0001", "1.0"])
def test_max_change_fraction_en_los_extremos_admitidos_es_valido(tmp_path, value):
    cfg = load_pipeline_config(_toml(tmp_path, max_change_fraction=value))
    assert cfg.sources.exoplanet_archive.snapshot.max_change_fraction == float(value)


@pytest.mark.parametrize("key", ["request_timeout_s", "max_response_bytes"])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_valores_no_positivos_fallan(tmp_path, key, value):
    with pytest.raises(ValidationError, match=key):
        load_pipeline_config(_toml(tmp_path, **{key: value}))


def test_la_configuracion_del_snapshot_es_inmutable():
    snap = load_pipeline_config(REAL_PIPELINE_TOML).sources.exoplanet_archive.snapshot
    with pytest.raises(ValidationError):
        snap.max_requests = 100  # type: ignore[misc]


# --- el validador del tiempo de ingesta no mira el snapshot -------------------


def test_los_valores_del_snapshot_no_entran_en_el_validador_de_tiempo_de_ingesta(tmp_path):
    """Un snapshot con techo y timeout enormes no hace fallar la carga: corre
    aparte de la noche (el validador solo suma arXiv y `max_requests_per_night`)."""
    cfg = load_pipeline_config(_toml(tmp_path, max_requests="100000", request_timeout_s="100000.0"))
    assert cfg.sources.exoplanet_archive.snapshot.max_requests == 100000


def test_el_peor_caso_de_la_ingesta_sigue_calculandose_con_max_requests_per_night(tmp_path):
    base = _toml(tmp_path, request_timeout_s="500.0", max_requests="3")
    text = base.read_text(encoding="utf-8").replace(
        "max_requests_per_night = 40", "max_requests_per_night = 1000"
    )
    base.write_text(text, encoding="utf-8")

    with pytest.raises(ValidationError, match=r"1000 peticiones x 32 s"):
        load_pipeline_config(base)


def test_el_peor_caso_de_la_ingesta_del_toml_real_es_el_de_antes_de_t81():
    """arXiv + Exoplanet Archive de la noche, sin sumandos del snapshot: se
    recalcula aqui con los campos de siempre y se comprueba que la configuracion
    real cabe con el mismo margen."""
    cfg = load_pipeline_config(REAL_PIPELINE_TOML)
    archive = cfg.sources.exoplanet_archive
    arxiv = cfg.sources.arxiv
    archive_s = archive.max_requests_per_night * (
        archive.request_timeout_s + archive.min_request_interval_s
    )
    arxiv_s = arxiv.max_requests_per_fetch * (
        arxiv.retry_max_elapsed_s + _HTTP_TIMEOUT_CEILING_S + _COURTESY_CEILING_S
    )
    assert archive_s + arxiv_s <= cfg.limits.run_timeout_s * 0.25
