import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { FindingDetail } from "@/lib/api/types";
import { EvidenceQuote } from "./EvidenceQuote";
import { FindingData } from "./FindingData";
import { TypeBadge } from "./TypeBadge";
import { TypeFilter } from "./TypeFilter";

const FICHA = "https://exoplanetarchive.ipac.caltech.edu/overview/HIP%2067522%20b";

const base: FindingDetail = {
  id: "1a2b3c4d-1111-2222-3333-444455556666",
  type: "paper_explained",
  title: "t",
  published_at: "2026-10-02T03:00:00Z",
  level_curious: "c",
  level_amateur: "a",
  level_technical: "t",
  source_url: "https://arxiv.org/abs/2410.01234",
  catalog_tension: null,
  first_measurement: null,
  independent_confirmation: null,
};

const render = (el: Parameters<typeof renderToStaticMarkup>[0]) =>
  renderToStaticMarkup(el);

describe("EvidenceQuote", () => {
  it("escapa HTML, lleva lang=en y atribución enlazada", () => {
    const html = render(
      createElement(EvidenceQuote, {
        evidence: "mass <script>alert(1)</script> $M_p$",
        sourceUrl: "https://arxiv.org/abs/2410.01234",
      }),
    );
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain('<blockquote lang="en"');
    expect(html).toContain("Cita literal del resumen en arXiv");
    expect(html).toContain('href="https://arxiv.org/abs/2410.01234"');
  });

  it("sin source_url válido no enlaza", () => {
    const html = render(
      createElement(EvidenceQuote, {
        evidence: "x",
        sourceUrl: "javascript:alert(1)",
      }),
    );
    expect(html).not.toContain("href");
    expect(html).toContain("Cita literal del resumen en arXiv");
  });
});

describe("FindingData", () => {
  it("paper_explained no pinta nada", () => {
    expect(render(createElement(FindingData, { finding: base }))).toBe("");
  });

  it("tipo sin su payload no pinta nada", () => {
    expect(
      render(
        createElement(FindingData, {
          finding: { ...base, type: "primera_medida" },
        }),
      ),
    ).toBe("");
  });

  it("primera_medida: tabla con caption, th scope y enlace a la ficha", () => {
    const finding: FindingDetail = {
      ...base,
      type: "primera_medida",
      first_measurement: {
        paper_planet_name: "HIP 67522 c",
        archive_planet_name: "HIP 67522 b",
        parameter: "mass",
        archive_status: "no_comparable_solution",
        archive_url: FICHA,
        measurements: [
          {
            value: 13.8,
            err_plus: 1,
            err_minus: 1,
            unit: "M_earth",
          },
        ],
      },
    };
    const html = render(createElement(FindingData, { finding }));
    expect(html).toContain("Datos del contraste");
    expect(html).toContain("<caption");
    expect(html).toContain('scope="col"');
    expect(html).toContain('scope="row"');
    expect(html).toContain("13,8 ± 1 M⊕");
    expect(html).toContain(`href="${FICHA}"`);
    expect(html).not.toContain("Candidato");
  });

  it("primera_medida con url de ficha no válida no enlaza", () => {
    const finding: FindingDetail = {
      ...base,
      type: "primera_medida",
      first_measurement: {
        paper_planet_name: "X b",
        archive_planet_name: null,
        parameter: "radius",
        archive_status: "absent",
        archive_url: "javascript:alert(1)",
        measurements: [],
      },
    };
    const html = render(createElement(FindingData, { finding }));
    expect(html).not.toContain("javascript:");
  });

  it("catalog_tension: leyenda, σ, evidencia y previa por defecto", () => {
    const finding: FindingDetail = {
      ...base,
      type: "catalog_tension",
      catalog_tension: {
        planet_name: "X b",
        parameter: "mass",
        archive_url: FICHA,
        threshold_sigma: 3,
        reference_sigma: 3.37,
        comparisons: [
          {
            paper: {
              planet_name: "X b",
              value: 2.8,
              err_plus: 0.5,
              err_minus: 0.4,
              unit: "M_jup",
              evidence: "We find <b>2.8</b> Mjup",
            },
            prior: {
              reference: "Doe et al. 2020 <i>x</i>",
              value: 1.2,
              err_plus: 0.3,
              err_minus: 0.3,
              unit: "M_jup",
              is_default: true,
              arxiv_id: "2001.01234",
            },
            sigma: 3.37,
          },
        ],
      },
    };
    const html = render(createElement(FindingData, { finding }));
    expect(html).toContain("3,37 σ");
    expect(html).toContain("Candidato: discrepancia calculada automáticamente");
    expect(html).toContain("Solución por defecto del archivo");
    expect(html).toContain('href="https://arxiv.org/abs/2001.01234"');
    expect(html).toContain("&lt;b&gt;2.8&lt;/b&gt;");
    expect(html).toContain("&lt;i&gt;x&lt;/i&gt;");
    expect(html).not.toContain("<b>");
    expect(html).toContain("2,8 +0,5 / −0,4 M♃");
  });

  it("confirmacion_independiente", () => {
    const finding: FindingDetail = {
      ...base,
      type: "confirmacion_independiente",
      independent_confirmation: {
        paper_planet_name: "X b",
        archive_planet_name: "X b",
        parameter: "radius",
        archive_url: FICHA,
        measurements: [
          { value: 0.969, err_plus: 0.017, err_minus: 0.017, unit: "R_jup" },
        ],
        reference: {
          refname: "Smith et al. 2019",
          arxiv_id: null,
          value: 0.95,
          err_plus: 0.02,
          err_minus: 0.02,
          unit: "R_jup",
          releasedate: "2019-05-01",
        },
        sigmas: [0.7],
        max_sigma: 0.7,
      },
    };
    const html = render(createElement(FindingData, { finding }));
    expect(html).toContain("0,969 ± 0,017 R♃");
    expect(html).toContain("0,70 σ");
    expect(html).toContain("Smith et al. 2019");
  });
});

describe("TypeBadge y TypeFilter", () => {
  it("Candidato solo en catalog_tension", () => {
    expect(render(createElement(TypeBadge, { type: "catalog_tension" }))).toContain(
      "Candidato",
    );
    expect(render(createElement(TypeBadge, { type: "primera_medida" }))).not.toContain(
      "Candidato",
    );
  });

  it("el filtro marca aria-current en el activo y enlaza los cinco", () => {
    const html = render(createElement(TypeFilter, { activeType: "primera_medida" }));
    expect(html).toContain('aria-label="Filtrar por tipo"');
    expect(html.match(/aria-current="page"/g)).toHaveLength(1);
    expect(html).toMatch(
      /<a aria-current="page"[^>]*href="\/\?tipo=primera-medida">Primera medida<\/a>/,
    );
    expect(html).toContain('href="/"');
    expect(html.match(/<a /g)).toHaveLength(5);
  });

  it("sin tipo activo, Todos es el actual", () => {
    const html = render(createElement(TypeFilter, { activeType: null }));
    expect(html).toMatch(/<a aria-current="page"[^>]*href="\/">Todos<\/a>/);
  });
});
