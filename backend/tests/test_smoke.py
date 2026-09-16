"""Test trivial del "Hecho cuando" de T02.

Demuestra que el layout `src`, el build backend (hatchling) y `uv sync`
encajan: el paquete `nocturna` y sus cuatro subpaquetes son importables.
"""

import importlib


def test_el_paquete_nocturna_es_importable():
    nocturna = importlib.import_module("nocturna")
    domain = importlib.import_module("nocturna.domain")
    application = importlib.import_module("nocturna.application")
    infrastructure = importlib.import_module("nocturna.infrastructure")
    api = importlib.import_module("nocturna.api")

    assert nocturna is not None
    assert domain is not None
    assert application is not None
    assert infrastructure is not None
    assert api is not None


def test_el_marcador_manual_esta_registrado(pytestconfig):
    markers = pytestconfig.getini("markers")
    assert any(marker.startswith("manual:") for marker in markers), (
        "el marcador 'manual' debe seguir declarado en pyproject.toml; "
        "sin él, 'addopts = -m not manual' no filtra nada y un test que "
        "llame a Claude de verdad podría colarse en la suite por defecto"
    )
