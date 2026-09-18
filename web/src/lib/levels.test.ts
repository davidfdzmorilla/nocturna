import { describe, expect, it } from "vitest";
import { parseLevelParam, levelText, DEFAULT_LEVEL } from "./levels";
import type { FindingDetail } from "./api/types";

describe("parseLevelParam", () => {
  it("valor ausente cae al nivel por defecto (curioso)", () => {
    expect(parseLevelParam(undefined)).toBe(DEFAULT_LEVEL);
    expect(parseLevelParam(undefined)).toBe("curioso");
  });

  it("valor inválido cae al nivel por defecto", () => {
    expect(parseLevelParam("experto")).toBe("curioso");
  });

  it('"curioso" se interpreta tal cual', () => {
    expect(parseLevelParam("curioso")).toBe("curioso");
  });

  it('"aficionado" se interpreta tal cual', () => {
    expect(parseLevelParam("aficionado")).toBe("aficionado");
  });

  it('"tecnico" se interpreta tal cual', () => {
    expect(parseLevelParam("tecnico")).toBe("tecnico");
  });
});

describe("levelText", () => {
  // Textos distinguibles a propósito: un mapeo cruzado (p. ej. devolver
  // level_amateur para "curioso") se detecta al instante, no se cuela
  // porque dos niveles compartan texto por casualidad.
  const finding: FindingDetail = {
    id: "1a2b3c4d-1111-2222-3333-444455556666",
    title: "Hallazgo de prueba",
    published_at: "2026-01-01T00:00:00Z",
    level_curious: "TEXTO_CURIOSO",
    type: "paper_explained",
    level_amateur: "TEXTO_AFICIONADO",
    level_technical: "TEXTO_TECNICO",
    source_url: null,
  };

  it('"curioso" devuelve level_curious', () => {
    expect(levelText(finding, "curioso")).toBe("TEXTO_CURIOSO");
  });

  it('"aficionado" devuelve level_amateur', () => {
    expect(levelText(finding, "aficionado")).toBe("TEXTO_AFICIONADO");
  });

  it('"tecnico" devuelve level_technical', () => {
    expect(levelText(finding, "tecnico")).toBe("TEXTO_TECNICO");
  });
});
