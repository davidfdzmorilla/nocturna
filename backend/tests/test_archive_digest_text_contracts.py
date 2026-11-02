"""T84.r: contratos del texto del resumen semanal sin base de datos.

El repositorio SQLAlchemy y el doble en memoria cumplen el `Protocol` del dominio; `AgentWork`
lleva `digest_texts`; el esquema de la API es una lista blanca. Fichero en español.
"""

from __future__ import annotations

import dataclasses

from fakes.digest import InMemoryArchiveDigestTextRepository
from test_repositories_satisfy_protocols import _assert_conforms, _protocol_methods

from nocturna.application.unit_of_work import AgentWork
from nocturna.domain.repositories import ArchiveDigestTextRepository
from nocturna.infrastructure.db.repositories import SqlAlchemyArchiveDigestTextRepository


def test_el_protocolo_declara_get_all_y_add() -> None:
    assert sorted(_protocol_methods(ArchiveDigestTextRepository)) == ["add", "all", "get"]


def test_sqlalchemy_archive_digest_text_repository_cumple_el_protocolo() -> None:
    repo: ArchiveDigestTextRepository = SqlAlchemyArchiveDigestTextRepository(None)
    _assert_conforms(ArchiveDigestTextRepository, type(repo))


def test_el_doble_en_memoria_tiene_los_mismos_metodos_que_el_protocolo() -> None:
    for name in _protocol_methods(ArchiveDigestTextRepository):
        assert callable(getattr(InMemoryArchiveDigestTextRepository, name)), name


def test_agent_work_lleva_digest_texts() -> None:
    assert "digest_texts" in {f.name for f in dataclasses.fields(AgentWork)}


def test_digest_text_out_es_una_lista_blanca_de_dos_campos() -> None:
    from nocturna.api.schemas import DigestTextOut

    out = DigestTextOut(title="Un título", summary="Un resumen.")
    assert set(out.model_dump(mode="json")) == {"title", "summary"}


def test_weekly_digest_out_trae_writer_text_nulo_por_defecto_y_con_texto_cuando_se_le_da() -> None:
    from nocturna.api.schemas import DigestTextOut, WeeklyDigestOut

    empty = WeeklyDigestOut(week="2026-W42", snapshots=1, entries=[]).model_dump(mode="json")
    assert empty["writer_text"] is None
    text = DigestTextOut(title="t", summary="s")
    full = WeeklyDigestOut(week="2026-W42", snapshots=1, entries=[], writer_text=text)
    assert full.model_dump(mode="json")["writer_text"] == {"title": "t", "summary": "s"}
