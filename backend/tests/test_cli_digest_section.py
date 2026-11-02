"""T84.r: subsección «Resumen semanal» del `run-night --dry-run` (pura y determinista).

Supuesto de interfaz: `cli._format_digest_section(pending, *, enabled, estimated_tokens)`
con `pending: PendingDigestWeeks`. Cero tokens. Fichero en español.
"""

from __future__ import annotations

import re

from helpers.archive_digest import (
    NOW,
    changed,
    digest_text,
    load_reader,
    make_digest_selector,
    new_planet,
)
from helpers.run_night import Environment, make_policy

from nocturna import cli


def _pending(*, calendar: bool = True):
    env = Environment(items=[], policy=make_policy(), now=NOW, with_digest=True)
    if calendar:
        load_reader(env.digest_reader, "2026-W39", new_planet("Z b", "2026-W39"))
        load_reader(env.digest_reader, "2026-W40", changed("A b", "2026-W40"))
        load_reader(env.digest_reader, "2026-W41", new_planet("N b", "2026-W41"))
        load_reader(env.digest_reader, "2026-W42", changed("C b", "2026-W42"))
        load_reader(env.digest_reader, "2026-W43")
        load_reader(env.digest_reader, "2026-W44", new_planet("M b", "2026-W44"))
        load_reader(env.digest_reader, "2026-W46", new_planet("O b", "2026-W46"))
        env.digest_texts.add(digest_text("2026-W41", env.run.id))
        env.digest_texts.add(digest_text("2026-W42", env.run.id, status="failed"))
    return make_digest_selector(env).pending()


def _text(*, enabled: bool = True, calendar: bool = True) -> str:
    return cli._format_digest_section(
        _pending(calendar=calendar), enabled=enabled, estimated_tokens=12000
    )


def test_la_cabecera_nombra_el_resumen_semanal_y_los_cero_tokens() -> None:
    text = _text()
    assert "Resumen semanal pendiente del redactor" in text
    assert "cero tokens en el ensayo" in text


def test_con_el_interruptor_apagado_la_seccion_se_lista_y_lo_dice() -> None:
    text = _text(enabled=False)
    assert "redactor del resumen semanal desactivado (writer_enabled = false)" in text
    assert "elegible · 2026-W44" in text  # se lista igualmente, como la del archivo


def test_con_el_interruptor_encendido_no_lleva_la_marca() -> None:
    assert "desactivado" not in _text(enabled=True)


def test_resumen_de_recuentos_y_estimacion() -> None:
    assert "elegibles=2 excluidas=5 estimación por llamada=12000 tokens" in _text()


def test_las_semanas_elegibles_van_mas_reciente_primero_con_el_tamano_del_bloque() -> None:
    pending = _pending()
    text = cli._format_digest_section(pending, enabled=True, estimated_tokens=12000)
    lines = [ln for ln in text.splitlines() if ln.strip().startswith("elegible ·")]
    assert [re.search(r"2026-W\d\d", ln).group(0) for ln in lines] == ["2026-W44", "2026-W40"]
    for line, candidate in zip(lines, pending.candidates, strict=True):
        sizes = {str(len(candidate.block)), str(len(candidate.block.encode("utf-8")))}
        assert any(re.search(rf"\b{n}\b", line) for n in sizes), line


def test_cada_semana_excluida_sale_con_su_motivo() -> None:
    text = _text()
    for week, reason in (
        ("2026-W39", "before_since"),
        ("2026-W41", "already_written"),
        ("2026-W42", "writer_failed"),
        ("2026-W43", "no_transitions"),
        ("2026-W46", "open_week"),
    ):
        assert f"excluida · {week} · {reason}" in text, (week, reason)


def test_sin_semanas_la_seccion_sigue_saliendo_con_ceros() -> None:
    text = _text(calendar=False)
    assert "elegibles=0 excluidas=0" in text
    assert "elegible ·" not in text and "excluida ·" not in text


def test_es_determinista() -> None:
    assert _text() == _text()
