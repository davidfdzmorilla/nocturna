import { describe, expect, it } from "vitest";
import {
  arxivAbsUrl,
  safeArchiveUrl,
  safeArxivSourceUrl,
} from "./externalLinks";

const FICHA = "https://exoplanetarchive.ipac.caltech.edu/overview/HIP%2067522%20b";

describe("safeArchiveUrl", () => {
  it("acepta la ficha real", () => {
    expect(safeArchiveUrl(FICHA)).toBe(FICHA);
  });

  it.each([
    "javascript:alert(1)",
    "http://exoplanetarchive.ipac.caltech.edu/overview/X",
    "https://evil.example/overview/X",
    "https://exoplanetarchive.ipac.caltech.edu.evil.example/overview/X",
    "https://exoplanetarchive.ipac.caltech.edu/overview",
    "https://exoplanetarchive.ipac.caltech.edu/overview/",
    'https://exoplanetarchive.ipac.caltech.edu/overview/X"onmouseover="x',
    "https://exoplanetarchive.ipac.caltech.edu/overview/X Y",
    "",
  ])("rechaza %s", (url) => {
    expect(safeArchiveUrl(url)).toBeNull();
  });

  it("null da null", () => {
    expect(safeArchiveUrl(null)).toBeNull();
  });
});

describe("arxivAbsUrl", () => {
  it("acepta ids nuevos, con versión y antiguos", () => {
    expect(arxivAbsUrl("2410.01234")).toBe("https://arxiv.org/abs/2410.01234");
    expect(arxivAbsUrl("2410.01234v2")).toBe(
      "https://arxiv.org/abs/2410.01234v2",
    );
    expect(arxivAbsUrl("astro-ph/0601234")).toBe(
      "https://arxiv.org/abs/astro-ph/0601234",
    );
  });

  it.each([
    "",
    "javascript:alert(1)",
    "2410.01234/../../x",
    "2410.1",
    "https://evil.example",
    '2410.01234"><script>',
    "2410.01234 ",
  ])("rechaza %s", (id) => {
    expect(arxivAbsUrl(id)).toBeNull();
  });

  it("null da null", () => {
    expect(arxivAbsUrl(null)).toBeNull();
  });
});

describe("safeArxivSourceUrl", () => {
  it("solo el prefijo de arXiv", () => {
    expect(safeArxivSourceUrl("https://arxiv.org/abs/2410.01234")).not.toBeNull();
    expect(safeArxivSourceUrl("http://arxiv.org/abs/2410.01234")).toBeNull();
    expect(safeArxivSourceUrl("javascript:1")).toBeNull();
    expect(safeArxivSourceUrl(null)).toBeNull();
  });

  it.each([
    "https://arxiv.org/abs/",
    "https://arxiv.org/abs/x",
    "https://arxiv.org/abs/2410.01234/../../x",
    'https://arxiv.org/abs/2410.01234"onmouseover="x',
  ])("rechaza %s", (url) => {
    expect(safeArxivSourceUrl(url)).toBeNull();
  });

  it("acepta ids con versión y antiguos", () => {
    expect(safeArxivSourceUrl("https://arxiv.org/abs/2410.01234v2")).not.toBeNull();
    expect(safeArxivSourceUrl("https://arxiv.org/abs/astro-ph/0601234")).not.toBeNull();
  });
});
