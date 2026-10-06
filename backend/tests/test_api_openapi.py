"""Congela que la especificación OpenAPI de la API de lectura no expone los
tres campos que `CLAUDE.md` prohíbe publicar (T50, corrección de revisión,
punto 5).

`api/schemas.py` ya documenta por qué `confidence`, `run_id` e `item_id` no
están en ningún esquema, y `test_api_schemas.py` lo comprueba serializando
cada esquema por separado -- por deducción, no directamente sobre lo que
`/openapi.json` expone de verdad. Este test cierra ese camino directamente
sobre la propia especificación.

`/docs`, `/redoc` y `/openapi.json` siguen expuestos y devuelven `200`: no
se desactivan aquí. Desactivarlos es una decisión de despliegue, que
`CLAUDE.md` prohíbe anticipar en fase 1; este test solo vigila el contenido
de la especificación, no si se sirve.
"""

import json

from nocturna.api.app import create_app

_FORBIDDEN_KEYS = (
    "confidence",
    "run_id",
    "item_id",
    "tension_evaluation_id",
    "solution_key",
    "soltype",
    "ttv_flag",
)


def test_openapi_no_expone_campos_prohibidos() -> None:
    spec = json.dumps(create_app().openapi())

    for key in _FORBIDDEN_KEYS:
        assert f'"{key}"' not in spec, f"la especificación OpenAPI expone '{key}'"


def test_openapi_parametro_type_de_findings_es_enum_con_los_cuatro_valores() -> None:
    spec = create_app().openapi()
    params = spec["paths"]["/findings"]["get"]["parameters"]
    type_param = next(p for p in params if p["name"] == "type")

    assert type_param["required"] is False
    schema = type_param["schema"]
    # `FindingType | None` se serializa como anyOf [enum $ref, null].
    ref = next(opt["$ref"] for opt in schema["anyOf"] if "$ref" in opt)
    enum_schema = spec["components"]["schemas"][ref.rsplit("/", 1)[1]]
    assert set(enum_schema["enum"]) == {
        "paper_explained",
        "catalog_tension",
        "primera_medida",
        "confirmacion_independiente",
    }
