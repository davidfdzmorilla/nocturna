"""Lee el JSON del hook por stdin y devuelve campos concretos. Uso: python3 hook_input.py <campo>...
Campos: command | file_path | content | agent | session
Si el JSON no se puede leer, sale con 3 para que el guard falle cerrado."""
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(3)
ti = d.get("tool_input") or {}
out = []
for f in sys.argv[1:]:
    if f == "command":
        out.append(str(ti.get("command") or ""))
    elif f == "file_path":
        out.append(str(ti.get("file_path") or ""))
    elif f == "content":
        parts = [str(ti.get("content") or ""), str(ti.get("new_string") or "")]
        parts += [str(e.get("new_string") or "") for e in (ti.get("edits") or [])]
        out.append("\n".join(parts))
    elif f == "agent":
        out.append(str(d.get("agent_name") or d.get("agent_type") or "?"))
    elif f == "session":
        out.append(str(d.get("session_id") or ""))
print("\x1f".join(out))
