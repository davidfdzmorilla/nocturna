import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  fetchArchiveWeeks: vi.fn(),
  fetchArchiveWeek: vi.fn(),
}));

import { fetchArchiveWeeks } from "@/lib/api/client";
import { archiveIndexState, archiveWeekState } from "./archiveRoute";
import { week40 } from "./archiveFixtures";
import type { ApiResult } from "./api/client";
import type { DigestWeeksResponse, WeeklyDigest } from "./api/types";

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
