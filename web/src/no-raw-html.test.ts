/**
 * Guarda: el texto de terceros (abstract de arXiv, referencias del
 * archivo) solo se pinta como nodo de texto de React, que escapa. Ningún
 * fichero de `src/` puede usar la API de HTML crudo de React.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const THIS_FILE = fileURLToPath(import.meta.url);
const SRC_DIR = dirname(THIS_FILE);
const FORBIDDEN = ["dangerously" + "SetInnerHTML", "inner" + "HTML ="];

function collect(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    return statSync(full).isDirectory() ? collect(full) : [full];
  });
}

describe("sin HTML crudo en src/", () => {
  it("ningún fichero usa la API de HTML crudo", () => {
    const offenders = collect(SRC_DIR)
      .filter((f) => f !== THIS_FILE)
      .filter((f) => [".ts", ".tsx", ".js", ".jsx"].includes(extname(f)))
      .filter((f) => {
        const text = readFileSync(f, "utf8");
        return FORBIDDEN.some((term) => text.includes(term));
      })
      .map((f) => relative(SRC_DIR, f));
    expect(offenders).toEqual([]);
  });
});
