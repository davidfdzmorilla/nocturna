import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  fetchArchiveWeeks: vi.fn(),
  fetchArchiveWeek: vi.fn(),
}));

import { fetchArchiveWeeks } from "@/lib/api/client";
import {
  archiveIndexState,
  archiveWeekState,
  homeDigestState,
  latestWeekWithChanges,
} from "./archiveRoute";
import { week40, week42WithText } from "./archiveFixtures";
import type { ApiResult } from "./api/client";
import type {
  DigestWeek,
  DigestWeeksResponse,
  WeeklyDigest,
} from "./api/types";

beforeEach(() => {
  vi.mocked(fetchArchiveWeeks).mockReset();
});

describe("archiveIndexState", () => {
  const weeks: DigestWeeksResponse = {
    weeks: [{ week: "2026-W41", snapshots: 1, changed: 0, new_planet: 70, regained: 0, lost: 0 }],
  };

  it("lista", async () => {
    vi.mocked(fetchArchiveWeeks).mockResolvedValue({ ok: true, data: weeks });
    expect(archiveIndexState(await fetchArchiveWeeks())).toEqual({ kind: "weeks", data: weeks });
  });

  it("vacía", async () => {
    vi.mocked(fetchArchiveWeeks).mockResolvedValue({ ok: true, data: { weeks: [] } });
    expect(archiveIndexState(await fetchArchiveWeeks())).toEqual({ kind: "empty" });
  });

  it("unavailable (también si la API dijera not_found)", async () => {
    vi.mocked(fetchArchiveWeeks).mockResolvedValue({ ok: false, reason: "unavailable" });
    expect(archiveIndexState(await fetchArchiveWeeks())).toEqual({ kind: "unavailable" });
    expect(archiveIndexState({ ok: false, reason: "not_found" })).toEqual({
      kind: "unavailable",
    });
  });
});

describe("archiveWeekState (la página de la semana lo cubre con el cliente simulado)", () => {
  it.each(["2026-W00", "2026-w41", "../x", ""])(
    "semana inválida %j: not_found aunque hubiera resultado",
    (raw) => {
      expect(archiveWeekState(raw, null)).toEqual({ kind: "not_found" });
      expect(archiveWeekState(raw, { ok: true, data: week40 })).toEqual({ kind: "not_found" });
    },
  );

  it("not_found de la API", () => {
    expect(archiveWeekState("2026-W41", { ok: false, reason: "not_found" })).toEqual({
      kind: "not_found",
    });
  });

  it("unavailable", () => {
    expect(archiveWeekState("2026-W41", { ok: false, reason: "unavailable" })).toEqual({
      kind: "unavailable",
    });
  });

  it("digest", () => {
    const result: ApiResult<WeeklyDigest> = { ok: true, data: week40 };
    expect(archiveWeekState("2026-W40", result)).toEqual({
      kind: "digest",
      week: "2026-W40",
      data: week40,
    });
  });
});

const wk = (week: string, n: number): DigestWeek => ({
  week,
  snapshots: 1,
  changed: 0,
  new_planet: n,
  regained: 0,
  lost: 0,
});

describe("latestWeekWithChanges", () => {
  it("lista vacía", () => {
    expect(latestWeekWithChanges([])).toBeNull();
  });

  it("todas a cero", () => {
    expect(
      latestWeekWithChanges([wk("2026-W44", 0), wk("2026-W43", 0)]),
    ).toBeNull();
  });

  it("salta las semanas vacías recientes", () => {
    const weeks = [
      wk("2026-W44", 0),
      wk("2026-W43", 0),
      wk("2026-W42", 3),
      wk("2026-W41", 70),
    ];
    expect(latestWeekWithChanges(weeks)).toEqual(wk("2026-W42", 3));
  });

  it("ignora una semana con formato inválido", () => {
    const weeks = [wk("../x", 5), wk("2026-W41", 2)];
    expect(latestWeekWithChanges(weeks)).toEqual(wk("2026-W41", 2));
    expect(latestWeekWithChanges([wk("2026-W99", 5)])).toBeNull();
  });
});

describe("homeDigestState", () => {
  const weeks: ApiResult<DigestWeeksResponse> = {
    ok: true,
    data: { weeks: [wk("2026-W43", 0), wk("2026-W42", 1)] },
  };

  it("listado con error: unavailable", () => {
    expect(homeDigestState({ ok: false, reason: "unavailable" }, null)).toEqual(
      {
        kind: "unavailable",
      },
    );
  });

  it("sin semanas con cambios: none", () => {
    expect(
      homeDigestState({ ok: true, data: { weeks: [wk("2026-W43", 0)] } }, null),
    ).toEqual({ kind: "none" });
  });

  it("detalle con texto", () => {
    expect(homeDigestState(weeks, { ok: true, data: week42WithText })).toEqual({
      kind: "week",
      week: "2026-W42",
      summary: wk("2026-W42", 1),
      text: week42WithText.writer_text,
    });
  });

  it("detalle con writer_text null", () => {
    expect(homeDigestState(weeks, { ok: true, data: week40 })).toMatchObject({
      kind: "week",
      week: "2026-W42",
      text: null,
    });
  });

  it.each(["unavailable", "not_found"] as const)(
    "detalle %s: semana sin texto",
    (reason) => {
      expect(homeDigestState(weeks, { ok: false, reason })).toMatchObject({
        kind: "week",
        week: "2026-W42",
        text: null,
      });
    },
  );
});
