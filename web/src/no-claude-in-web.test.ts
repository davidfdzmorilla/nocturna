/**
 * Equivalente frontend de `backend/tests/test_llm_call_sites.py`: congela
 * el invariante que `CLAUDE.md` deja tajante para la web fase 1 ("Cero
 * llamadas a Claude, cero lógica de análisis"; ver también
 * `src/lib/api/config.ts` y `src/lib/api/client.ts`, que documentan por
 * qué `NOCTURNA_API_URL` no lleva `NEXT_PUBLIC_` y por qué todo el
 * `fetch` a la API vive en un único módulo de servidor).
 *
 * Escanea el contenido de `web/package.json` y de todo fichero de texto
 * bajo `web/src/**` en busca de `anthropic`, `claude`,
 * `ANTHROPIC_API_KEY` y `@anthropic-ai/` (comparación insensible a
 * mayúsculas, así que las cuatro formas se reducen en la práctica a dos
 * substrings: `anthropic` y `claude` -- se listan las cuatro de todas
 * formas porque así se pidió en el plan, documentando la intención
 * explícita en vez de solo el substring que la implementa).
 *
 * Este propio fichero se autoexcluye del escaneo -- necesita nombrar
 * "claude"/"anthropic" en su docstring y en sus fixtures sintéticas para
 * documentar y probar lo que bloquea, igual que
 * `test_llm_call_sites.py` excluye `test_no_claude_guard.py` de su propio
 * escaneo por la misma razón.
 *
 * Dos excepciones más, descubiertas al escribir este test contra el árbol
 * real (no hipotéticas: la primera versión, sin ellas, falló contra 3
 * ficheros ya existentes en `src/`):
 *
 * 1. Referencias al propio fichero de gobierno `CLAUDE.md` (p. ej. "ver
 *    `CLAUDE.md`" en `api/config.ts`, `api/client.ts`, `LevelNav.tsx`):
 *    el *nombre del fichero* contiene el substring prohibido "claude" sin
 *    que el código tenga nada que ver con el SDK. Se eliminan del
 *    contenido antes de comparar (`stripClaudeMdReferences`), de forma
 *    genérica: cualquier fichero nuevo que cite `CLAUDE.md` en un
 *    comentario -- convención habitual en este proyecto -- no debería
 *    tener que entrar en una lista aparte cada vez.
 * 2. `CLAUDE_MENTION_ALLOWLIST`: mención explícita y verificada a mano de
 *    la palabra "Claude" en prosa para describir la propia política ("la
 *    web ... nunca llama a Claude", en `api/client.ts`), no una referencia
 *    a `CLAUDE.md` ni código. A diferencia del punto 1, esto NO se
 *    generaliza con una regexp de frase (frágil si cambia la redacción):
 *    cada entrada es una decisión consciente, igual que
 *    `_is_legitimate_sensitive_import_site` en `test_llm_call_sites.py`.
 *
 * **Qué NO es este fichero.** No es una guarda en tiempo de ejecución: no
 * impide que alguien escriba `fetch("https://api.anthropic.com/...")`
 * mañana y lo despliegue sin correr `pnpm test` antes, ni entiende
 * ofuscación (concatenar `"anthr" + "opic"`, codificar en base64, importar
 * un paquete que a su vez importe el SDK de Anthropic sin que su nombre
 * aparezca aquí). Es un detector de descuidos por coincidencia de texto,
 * evasible por cualquiera que lo intente a propósito. Su valor no es
 * cerrar el agujero -- eso lo hace la propia arquitectura (la web nunca
 * tiene credenciales de Claude ni motivo para importar su SDK, y
 * `client.ts` es el único módulo que hace `fetch`, hacia
 * `NOCTURNA_API_URL`, no hacia Anthropic) -- sino que un despiste (un
 * `console.log` con un ejemplo de prompt pegado sin querer, una
 * dependencia añadida por error, una URL de la API de Claude copiada de
 * otro proyecto) se convierta en un fallo visible de `pnpm test` en vez
 * de colarse en silencio.
 */

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

const THIS_FILE = fileURLToPath(import.meta.url);
// Este fichero vive en web/src/no-claude-in-web.test.ts.
const SRC_DIR = dirname(THIS_FILE);
const WEB_ROOT = dirname(SRC_DIR);
const PACKAGE_JSON_PATH = join(WEB_ROOT, "package.json");

/** Solo ficheros de texto plausibles: evita leer binarios (p. ej. favicon.ico) como si fueran texto. */
const TEXT_EXTENSIONS = new Set([
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".mjs",
  ".cjs",
  ".mts",
  ".cts",
  ".json",
  ".css",
]);

const FORBIDDEN_TERMS = [
  "anthropic",
  "claude",
  "ANTHROPIC_API_KEY",
  "@anthropic-ai/",
];

/**
 * Rutas (relativas a `web/`) donde una mención de "claude" en prosa está
 * verificada a mano como documentación de la política, no código. Ver
 * punto 2 del docstring de cabecera. Ampliar esta lista es una decisión
 * consciente: cada entrada nueva exige releer la línea real antes de
 * añadirla.
 */
const CLAUDE_MENTION_ALLOWLIST: Record<string, readonly string[]> = {
  "src/lib/api/client.ts": ["claude"], // "...la web es de solo lectura y nunca llama a Claude..."
};

function collectFiles(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const stats = statSync(full);
    if (stats.isDirectory()) {
      files.push(...collectFiles(full));
    } else {
      files.push(full);
    }
  }
  return files;
}

/** Elimina referencias al fichero de gobierno `CLAUDE.md` (ver punto 1 del docstring de cabecera). */
function stripClaudeMdReferences(content: string): string {
  return content.replace(/claude\.md/gi, "");
}

/** Términos prohibidos presentes en `content` (comparación insensible a mayúsculas). */
function containsForbiddenTerms(content: string): string[] {
  const lower = content.toLowerCase();
  return FORBIDDEN_TERMS.filter((term) => lower.includes(term.toLowerCase()));
}

/** Términos prohibidos presentes en `path` (vacío si no hay ninguno, o si no es texto legible). Sin la excepción de `CLAUDE.md`: úsala directamente solo en los tests de regresión sintética. */
function findForbiddenTerms(path: string): string[] {
  if (!TEXT_EXTENSIONS.has(extname(path))) {
    return [];
  }
  return containsForbiddenTerms(readFileSync(path, "utf8"));
}

function scan(): Record<string, string[]> {
  const files = [PACKAGE_JSON_PATH, ...collectFiles(SRC_DIR)].filter(
    (file) => file !== THIS_FILE,
  );
  const violations: Record<string, string[]> = {};
  for (const file of files) {
    if (!TEXT_EXTENSIONS.has(extname(file))) {
      continue;
    }
    const relativePath = relative(WEB_ROOT, file);
    const content = stripClaudeMdReferences(readFileSync(file, "utf8"));
    const allowed = new Set(CLAUDE_MENTION_ALLOWLIST[relativePath] ?? []);
    const found = containsForbiddenTerms(content).filter(
      (term) => !allowed.has(term),
    );
    if (found.length > 0) {
      violations[relativePath] = found;
    }
  }
  return violations;
}

describe("web nunca llama a Claude", () => {
  it("package.json y src/** no mencionan Anthropic/Claude", () => {
    expect(scan()).toEqual({});
  });

  it("CLAUDE_MENTION_ALLOWLIST no tiene entradas obsoletas", () => {
    // Espejo de `stale_allowlist_entries` en test_llm_call_sites.py: si el
    // fichero allowlistado ya no menciona el término (se reescribió el
    // comentario, se borró la línea...), la entrada debe quitarse a mano,
    // no arrastrarse sin uso perdonando algo que ya no existe.
    const stale = Object.entries(CLAUDE_MENTION_ALLOWLIST).filter(
      ([relativePath, terms]) => {
        const content = stripClaudeMdReferences(
          readFileSync(join(WEB_ROOT, relativePath), "utf8"),
        );
        const present = containsForbiddenTerms(content);
        return !terms.some((term) => present.includes(term));
      },
    );

    expect(stale).toEqual([]);
  });
});

describe("stripClaudeMdReferences", () => {
  it("elimina referencias a CLAUDE.md (cualquier combinación de mayúsculas)", () => {
    expect(
      stripClaudeMdReferences("ver `CLAUDE.md` para más detalle"),
    ).not.toContain("claude");
    expect(stripClaudeMdReferences("ver claude.md")).not.toContain("claude");
  });

  it("no elimina una mención de Claude que no vaya seguida de .md", () => {
    const stripped = stripClaudeMdReferences("nunca llama a Claude");
    expect(stripped.toLowerCase()).toContain("claude");
  });
});

describe("scan() detecta el término prohibido cuando está presente (regresión sintética)", () => {
  // Precedente exacto: backend/tests/test_llm_call_sites.py::
  // test_reexport_de_query_desde_agent_sdk_provider_tambien_se_detecta.
  // Prueba el detector contra un fichero sintético fuera de web/src, no
  // inyectando el término en el árbol real: así no hace falta dejar
  // "anthropic"/"claude" de verdad en ningún fichero del proyecto para
  // ejercitar esta regla. La demostración contra el árbol real (inyectar,
  // ver el test principal fallar, restaurar) se hizo a mano y se reporta
  // aparte, precisamente para no dejar ese mutante commiteado.
  let tmpDir: string | undefined;

  afterEach(() => {
    if (tmpDir && existsSync(tmpDir)) {
      rmSync(tmpDir, { recursive: true, force: true });
    }
    tmpDir = undefined;
  });

  it("un fichero con 'anthropic' se marca como violación", () => {
    tmpDir = mkdtempSync(join(tmpdir(), "no-claude-in-web-"));
    const synthetic = join(tmpDir, "synthetic.ts");
    writeFileSync(
      synthetic,
      "// import { Anthropic } from '@anthropic-ai/sdk';\n",
    );

    expect(findForbiddenTerms(synthetic)).toContain("anthropic");
  });

  it("un fichero con 'Claude' (mayúscula) se marca como violación (insensible a mayúsculas)", () => {
    tmpDir = mkdtempSync(join(tmpdir(), "no-claude-in-web-"));
    const synthetic = join(tmpDir, "synthetic.ts");
    writeFileSync(synthetic, "// Claude Agent SDK\n");

    expect(findForbiddenTerms(synthetic)).toContain("claude");
  });

  it("un fichero limpio no se marca", () => {
    tmpDir = mkdtempSync(join(tmpdir(), "no-claude-in-web-"));
    const synthetic = join(tmpDir, "synthetic.ts");
    writeFileSync(synthetic, "// nada sospechoso aquí\n");

    expect(findForbiddenTerms(synthetic)).toEqual([]);
  });
});
