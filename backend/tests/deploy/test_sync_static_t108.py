"""T108 (D): comprobaciones estaticas de la sincronizacion Mac -> VPS.

Sin Docker, red ni Claude. Complementa `tests/test_deploy_no_claude_credentials.py`.
"""

import re
from pathlib import Path

import pytest
from deploy._checks import forbidden_terms

ROOT = Path(__file__).resolve().parents[3]
SYNC_SH = ROOT / "backend" / "scripts" / "sync-to-vps.sh"
RESTORE_SH = ROOT / "deploy" / "sync" / "restore.sh"
AUTHORIZED_KEYS = ROOT / "deploy" / "sync" / "authorized_keys.example"
SCRIPTS = [SYNC_SH, RESTORE_SH]
FILES = [*SCRIPTS, AUTHORIZED_KEYS]


def _code(path: Path) -> str:
    """El fichero sin lineas de comentario (los comentarios pueden explicar lo que NO se hace)."""
    return "\n".join(ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("#"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_los_ficheros_de_sync_existen(path):
    assert path.is_file()


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_sync_sin_claude_ni_anthropic(path):
    assert forbidden_terms(path.read_text()) == []


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
@pytest.mark.parametrize("term", ["POSTGRES_PASSWORD", "PGPASSWORD"])
def test_los_scripts_no_nombran_contrasenas(path, term):
    assert term not in path.read_text(), f"{path.name} nombra {term}"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_el_codigo_de_los_scripts_no_nombra_env(path):
    # Solo codigo: las cabeceras explican en comentarios que NO leen `deploy/.env`.
    assert ".env" not in _code(path), f"{path.name} nombra .env en codigo"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_los_scripts_no_leen_ficheros_de_entorno_en_el_codigo(path):
    code = _code(path)
    assert not re.search(r"\bsource\b|^\s*\.\s+\S", code, re.MULTILINE)
    assert "--env-file" not in code


def test_authorized_keys_example_empieza_por_restrict_y_command():
    entries = [ln for ln in AUTHORIZED_KEYS.read_text().splitlines() if ln.strip() and ln[0] != "#"]
    assert len(entries) == 1
    assert entries[0].startswith('restrict,command="')


def test_authorized_keys_example_fuerza_restore_sh_y_una_clave_ed25519():
    (entry,) = [ln for ln in AUTHORIZED_KEYS.read_text().splitlines() if ln and ln[0] != "#"]
    assert re.match(r'^restrict,command="[^"]*/deploy/sync/restore\.sh" ssh-ed25519 ', entry)
    assert "no-" not in entry.split(" ssh-ed25519")[0]  # solo restrict, sin reabrir nada


def test_gitignore_ignora_el_lock_de_la_restauracion():
    lines = (ROOT / ".gitignore").read_text().splitlines()
    assert "deploy/sync/.restore.lock" in lines


def test_restore_sh_valida_la_orden_con_una_expresion_anclada():
    text = RESTORE_SH.read_text()
    assert "command_re='^restore ([0-9a-f]{64}) ([0-9a-z_]{1,64})$'" in text
    assert "eval" not in _code(RESTORE_SH).split()


def test_los_dos_scripts_tienen_sintaxis_bash_valida():
    import subprocess

    for path in SCRIPTS:
        r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
