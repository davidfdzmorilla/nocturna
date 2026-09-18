import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchFindingsPage, fetchFinding } from "./client";
import type { FindingDetail } from "./types";

/**
 * `fetch` mockeado en cada test: ningún test de este fichero toca la red.
 * `NOCTURNA_API_URL` se fija a un valor de prueba salvo en el test que
 * comprueba justo su ausencia.
 */
const API_URL = "http://api-de-prueba.invalid";

function fakeResponse(init: {
  status: number;
  ok: boolean;
  json?: () => Promise<unknown>;
}) {
  return {
    status: init.status,
    ok: init.ok,
    json: init.json ?? (() => Promise.resolve({})),
  } as Response;
}

beforeEach(() => {
  vi.stubEnv("NOCTURNA_API_URL", API_URL);
  vi.stubGlobal("fetch", vi.fn());
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const sampleFinding: FindingDetail = {
  id: "1a2b3c4d-1111-2222-3333-444455556666",
  title: "Un hallazgo",
  published_at: "2026-01-01T00:00:00Z",
  level_curious: "curioso",
  type: "paper_explained",
  level_amateur: "aficionado",
  level_technical: "tecnico",
  source_url: "https://arxiv.org/abs/1234.5678",
};

describe("fetchFinding", () => {
  it("200 bien formado devuelve ok:true con los datos", async () => {
    vi.mocked(fetch).mockResolvedValue(
      fakeResponse({
        status: 200,
        ok: true,
        json: () => Promise.resolve(sampleFinding),
      }),
    );

    const result = await fetchFinding(sampleFinding.id);

    expect(result).toEqual({ ok: true, data: sampleFinding });
  });

  it("404 devuelve reason: not_found", async () => {
    vi.mocked(fetch).mockResolvedValue(
      fakeResponse({ status: 404, ok: false }),
    );

    const result = await fetchFinding(sampleFinding.id);

    expect(result).toEqual({ ok: false, reason: "not_found" });
  });

  it("500 devuelve reason: unavailable", async () => {
    vi.mocked(fetch).mockResolvedValue(
      fakeResponse({ status: 500, ok: false }),
    );

    const result = await fetchFinding(sampleFinding.id);

    expect(result).toEqual({ ok: false, reason: "unavailable" });
  });

  it("una excepción de red devuelve reason: unavailable", async () => {
    vi.mocked(fetch).mockRejectedValue(new Error("network down"));

    const result = await fetchFinding(sampleFinding.id);

    expect(result).toEqual({ ok: false, reason: "unavailable" });
  });

  it("JSON ilegible devuelve reason: unavailable", async () => {
    vi.mocked(fetch).mockResolvedValue(
      fakeResponse({
        status: 200,
        ok: true,
        json: () => Promise.reject(new SyntaxError("Unexpected token")),
      }),
    );

    const result = await fetchFinding(sampleFinding.id);

    expect(result).toEqual({ ok: false, reason: "unavailable" });
  });

  it("NOCTURNA_API_URL ausente devuelve reason: unavailable", async () => {
    vi.stubEnv("NOCTURNA_API_URL", "");

    const result = await fetchFinding(sampleFinding.id);

    expect(result).toEqual({ ok: false, reason: "unavailable" });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("el resultado nunca filtra el cuerpo del servidor, el código de estado ni la URL de la API", async () => {
    const secretBody = "SECRET_INTERNAL_STACK_TRACE_DETAIL";
    vi.mocked(fetch).mockResolvedValue(
      fakeResponse({
        status: 500,
        ok: false,
        json: () => Promise.resolve({ detail: secretBody }),
      }),
    );

    const result = await fetchFinding(sampleFinding.id);
    const serialized = JSON.stringify(result);

    expect(result).toEqual({ ok: false, reason: "unavailable" });
    expect(serialized).not.toContain(secretBody);
    expect(serialized).not.toContain("500");
    expect(serialized).not.toContain(API_URL);
  });
});

describe("fetchFindingsPage", () => {
  it("200 bien formado devuelve ok:true con los datos", async () => {
    const page = { items: [], page: 1, size: 20, total: 0 };
    vi.mocked(fetch).mockResolvedValue(
      fakeResponse({
        status: 200,
        ok: true,
        json: () => Promise.resolve(page),
      }),
    );

    const result = await fetchFindingsPage(1, 20);

    expect(result).toEqual({ ok: true, data: page });
  });
});
