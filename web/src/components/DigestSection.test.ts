import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// El layout importa la hoja de estilos (Tailwind/PostCSS); aquí no hace falta.
vi.mock("@/app/globals.css", () => ({}));

import type { DigestEntry, TransitionKind } from "@/lib/api/types";
import { AI_FREE_NOTE, SECTION_LABELS, SECTION_ORDER, entriesByKind } from "@/lib/archiveDigest";
import {
  ALMENARA,
  COLLIER,
  GIACALONE,
  SCHMERLING,
  collierNames,
  makeEntry,
  toi2427b,
  value,
  week40,
  week41,
} from "@/lib/archiveFixtures";
import { DigestSection } from "./DigestSection";
import { SiteHeader } from "./SiteHeader";
import ArchiveNotFound from "@/app/archivo/not-found";
import RootLayout, { metadata } from "@/app/layout";

const section = (kind: TransitionKind, entries: DigestEntry[]) =>
  renderToStaticMarkup(createElement(DigestSection, { kind, entries }));

/** Las cuatro secciones como las pinta la página de la semana. */
const weekHtml = (entries: DigestEntry[]) =>
  SECTION_ORDER.map((k) => section(k, entriesByKind(entries, k))).join("");

describe("semana 2026-W40", () => {
  const html = weekHtml(week40.entries);

  it("cuatro secciones en orden, con recuento y vacías con texto", () => {
    const heads = SECTION_ORDER.map((k) => html.indexOf(SECTION_LABELS[k]));
    expect(heads.every((i) => i >= 0)).toBe(true);
    expect([...heads].sort((a, b) => a - b)).toEqual(heads);
    expect(html).toContain("Referencia cambiada (1)");
    expect(html).toContain("Planetas nuevos (3)");
    expect(html).toContain("Planetas recuperados (0)");
    expect(html).toContain("Planetas que pierden la referencia (0)");
    expect(html.match(/Ninguno esta semana\./g)).toHaveLength(2);
  });

  it("TOI-2427 b: referencias, fechas y valores", () => {
    expect(html).toContain("Giacalone et al. 2022");
    expect(html).toContain("Schmerling et al. 2025");
    expect(html).toContain("2022-02");
    expect(html).toContain("2025-07");
    expect(html).toContain("15 de febrero de 2022");
    expect(html).toContain("1 de octubre de 2026");
    expect(html).toContain("Referencia anterior");
    expect(html).toContain("Referencia nueva");
    expect(html).toContain("Alta en el archivo");
    expect(html).toContain("Detectado");
    expect(html).toContain("sin valor");
    expect(html).toContain("5,69 +0,51 / −0,5 M⊕");
    expect(html).toContain("1,8 ± 0,12 R⊕");
    expect(html).toContain("1,64 +0,12 / −0,11 R⊕");
    expect(html).toContain("1,3060011 ± 0,0000102 d");
    expect(html).toContain("1,305962 +0,000071 / −0,00007 d");
  });

  it("tabla accesible: caption, th scope y procedencia de la masa", () => {
    expect(html).toContain("<caption");
    expect(html).toContain("Parámetros de TOI-2427 b");
    expect(html).toContain('<th scope="col"');
    expect(html).toContain('<th scope="row"');
    expect(html).toContain("masa");
    expect(html).toContain("radio");
    expect(html).toContain("periodo");
    expect(html).toContain("Procedencia de la masa: –");
    expect(html).toContain("Procedencia de la masa: Mass");
    expect(html.match(/Procedencia de la masa/g)).toHaveLength(2);
  });

  it("nuevos agrupados por referencia con nombres enlazados a la ficha", () => {
    expect(html).toContain("Almenara et al. 2026 · 2 planetas");
    expect(html).toContain("Schmerling et al. 2025 · 1 planeta<");
    for (const name of ["HD 148797 b", "HD 148797 c", "TOI-2427 c"]) {
      expect(html).toContain(
        `href="https://exoplanetarchive.ipac.caltech.edu/overview/${encodeURIComponent(name)}"`,
      );
      expect(html).toContain(`>${name}</a>`);
    }
    // Los nuevos no llevan tabla ni fechas por planeta.
    expect(section("new_planet", entriesByKind(week40.entries, "new_planet"))).not.toContain(
      "<table",
    );
  });
});

describe("semana 2026-W41 (70 planetas nuevos)", () => {
  const html = weekHtml(week41.entries);

  it("todos los nombres presentes bajo un grupo", () => {
    expect(html).toContain("Collier et al. 2026 · 70 planetas");
    for (const name of collierNames) {
      expect(html).toContain(`>${name}</a>`);
    }
    expect(html).toContain("Planetas nuevos (70)");
  });

  it("las otras tres secciones salen vacías", () => {
    expect(html).toContain("Referencia cambiada (0)");
    expect(html.match(/Ninguno esta semana\./g)).toHaveLength(3);
  });
});

describe("cambio sin parámetros, recuperados y perdidos", () => {
  it("cambio sin parámetros distintos", () => {
    const html = section("changed", [
      makeEntry("changed", "X b", GIACALONE, SCHMERLING, []),
    ]);
    expect(html).toContain("Sin cambios en masa, radio ni periodo.");
    expect(html).not.toContain("<table");
  });

  it("recuperado y perdido muestran 'Referencia' y no la nota de cambio", () => {
    const rec = section("regained", [makeEntry("regained", "R b", null, ALMENARA)]);
    const lost = section("lost", [makeEntry("lost", "L b", COLLIER, null)]);
    expect(rec).toContain("Referencia</dt>");
    expect(rec).toContain("Almenara et al. 2026");
    expect(lost).toContain("Collier et al. 2026");
    expect(rec).not.toContain("Sin cambios en masa");
    expect(lost).not.toContain("Sin cambios en masa");
  });

  it("pl_pubdate nulo se pinta como guion", () => {
    const html = section("regained", [
      makeEntry("regained", "R b", null, { ...ALMENARA, pl_pubdate: null }),
    ]);
    expect(html).toContain("Publicación</dt><dd>–</dd>");
  });
});

describe("seguridad de los enlaces y del texto de terceros", () => {
  it("planet_url con javascript: no se enlaza (el nombre sale como texto)", () => {
    const bad = { ...toi2427b, planet_url: "javascript:alert(1)" };
    const html = section("changed", [bad]);
    expect(html).not.toContain("javascript:");
    expect(html).not.toContain("<a ");
    expect(html).toContain("TOI-2427 b");
    const group = section("new_planet", [
      { ...makeEntry("new_planet", "N b", null, ALMENARA), planet_url: "javascript:alert(1)" },
    ]);
    expect(group).not.toContain("javascript:");
    expect(group).not.toContain("<a ");
  });

  it("URL de otro dominio tampoco se enlaza", () => {
    const html = section("changed", [{ ...toi2427b, planet_url: "https://evil.example/overview/X" }]);
    expect(html).not.toContain("evil.example");
  });

  it("una referencia con <script> sale escapada", () => {
    const evil = { reference: "<script>alert(1)</script>", pl_pubdate: "<b>x</b>", releasedate: "2026-10-01" };
    const html =
      section("changed", [makeEntry("changed", "X b", GIACALONE, evil)]) +
      section("new_planet", [makeEntry("new_planet", "Y b", null, evil)]);
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<b>x</b>");
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
  });

  it("un nombre de planeta con <script> sale escapado", () => {
    const html = section("changed", [{ ...toi2427b, pl_name: "<script>x</script>" }]);
    expect(html).not.toContain("<script>x");
  });
});

describe("procedencia de la masa con otros valores", () => {
  it("Msini se muestra tal cual", () => {
    const change = {
      parameter: "mass" as const,
      old: value(3, 0.1, 0.1, "M_earth"),
      new: value(4, null, null, "M_earth", "lower"),
      old_mass_provenance: "Msini",
      new_mass_provenance: null,
    };
    const html = section("changed", [
      makeEntry("changed", "M b", GIACALONE, SCHMERLING, [change]),
    ]);
    expect(html).toContain("Procedencia de la masa: Msini");
    expect(html).toContain("&gt; 4,0 M⊕");
  });
});

describe("claves de las entradas", () => {
  it("dos changed del mismo planeta con distinto detected_at: ambas salen, sin aviso de clave", () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    const a = { ...toi2427b, detected_at: "2026-10-01T03:00:00Z" };
    const b = { ...toi2427b, detected_at: "2026-10-03T03:00:00Z" };
    const html = section("changed", [a, b]);
    expect(html.match(/TOI-2427 b/g)!.length).toBeGreaterThanOrEqual(2);
    expect(html).toContain("Referencia cambiada (2)");
    expect(err.mock.calls.flat().join(" ")).not.toContain("same key");
    err.mockRestore();
  });
});

describe("textos de D6, D4, D7 y D9", () => {
  it("nota de D6", () => {
    expect(AI_FREE_NOTE).toBe(
      "Datos del NASA Exoplanet Archive presentados con plantillas fijas; esta sección no contiene texto generado por IA.",
    );
  });

  it("SiteHeader: enlace a /archivo y banner intacto", () => {
    const html = renderToStaticMarkup(createElement(SiteHeader));
    expect(html).toContain('href="/archivo"');
    expect(html).toContain(">Resumen del archivo</a>");
    expect(html).not.toContain("aria-current");
    expect(html).toContain('href="/"');
    expect(html).toContain('role="note"');
    expect(html).toContain(
      "Análisis generado automáticamente por IA. No es un resultado científico validado.",
    );
  });

  it("layout: pie y meta-descripción de D7", () => {
    const text =
      "Nocturna. Hallazgos generados automáticamente a partir de arXiv astro-ph; datos de exoplanetas del NASA Exoplanet Archive.";
    expect(metadata.description).toBe(text);
    const html = renderToStaticMarkup(createElement(RootLayout, null, "contenido"));
    expect(html).toContain("<footer");
    expect(html.replace(/\s+/g, " ")).toContain(text);
    expect(html).toContain("Análisis generado automáticamente por IA.");
  });

  it("not-found de /archivo: texto de D9 y enlace", () => {
    const html = renderToStaticMarkup(createElement(ArchiveNotFound));
    expect(html).toContain("Esta semana no tiene resumen");
    expect(html).toContain('href="/archivo"');
  });
});
