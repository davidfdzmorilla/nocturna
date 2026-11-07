import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// `cache` de React no memoriza fuera de un render de servidor: se sustituye por
// una memoización por argumentos para comprobar que las funciones de lectura
// que comparten `generateMetadata` y la página están envueltas en `cache()`.
vi.mock("react", () => ({
  cache: <A extends unknown[], R>(fn: (...a: A) => R) => {
    const memo = new Map<string, R>();
    return (...a: A) => {
      const k = JSON.stringify(a);
      if (!memo.has(k)) memo.set(k, fn(...a));
      return memo.get(k) as R;
    };
  },
}));

import {
  fetchArchiveWeek,
  fetchArchiveWeeks,
  fetchFinding,
  fetchFindingsPage,
} from "./client";

beforeEach(() => {
  vi.stubEnv("NOCTURNA_API_URL", "http://api.test");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) })),
  );
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("una petición a la API por render", () => {
  it.each([
    ["fetchFindingsPage", () => fetchFindingsPage(1, 20, null)],
    ["fetchArchiveWeeks", () => fetchArchiveWeeks()],
    ["fetchFinding", () => fetchFinding("x")],
    ["fetchArchiveWeek", () => fetchArchiveWeek("2026-W41")],
  ])("%s: dos llamadas iguales, un solo fetch", async (_n, call) => {
    await call();
    await call();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
