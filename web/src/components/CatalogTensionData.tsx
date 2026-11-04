import { useId } from "react";
import type { CatalogTension } from "@/lib/api/types";
import { candidateLegend } from "@/lib/findingTypes";
import { arxivAbsUrl, safeArchiveUrl } from "@/lib/externalLinks";
import {
  parameterTitle,
  formatNumber,
  formatSigma,
  formatWithErrors,
} from "@/lib/measurementFormat";
import { EvidenceQuote } from "./EvidenceQuote";
import {
  CAPTION,
  TABLE,
  TD,
  TD_VALUE,
  TH,
  TH_ROW,
  TableScroll,
} from "./TableScroll";

type Props = {
  data: CatalogTension;
  sourceUrl: string | null;
};

export function CatalogTensionData({ data, sourceUrl }: Props) {
  const captionId = useId();
  const archiveUrl = safeArchiveUrl(data.archive_url);
  const caption = `${parameterTitle(data.parameter)} de ${data.planet_name}: medida del artículo frente a soluciones previas del archivo`;

  return (
    <>
      <p className="mt-2 text-sm text-text">
        {candidateLegend("catalog_tension")}
      </p>
      <div className="mt-3">
        <TableScroll labelledBy={captionId}>
          <table className={`${TABLE} min-w-[42rem]`}>
            <caption id={captionId} className={CAPTION}>
              {caption}
            </caption>
            <thead>
              <tr>
                <th scope="col" className={TH}>
                  Medida del artículo
                </th>
                <th scope="col" className={TH}>
                  Cita del resumen
                </th>
                <th scope="col" className={TH}>
                  Solución previa del archivo
                </th>
                <th scope="col" className={TH}>
                  Diferencia
                </th>
              </tr>
            </thead>
            <tbody>
              {data.comparisons.map((c, index) => {
                const priorArxiv = arxivAbsUrl(c.prior.arxiv_id);
                return (
                  <tr key={index} className="align-top">
                    <th scope="row" className={TH_ROW}>
                      <span className="block">{c.paper.planet_name}</span>
                      <span className="block whitespace-nowrap">
                        {formatWithErrors(c.paper)}
                      </span>
                    </th>
                    <td className={TD}>
                      <EvidenceQuote
                        evidence={c.paper.evidence}
                        sourceUrl={sourceUrl}
                      />
                    </td>
                    <td className={TD}>
                      <span className="block">{c.prior.reference}</span>
                      <span className="block whitespace-nowrap">
                        {formatWithErrors(c.prior)}
                      </span>
                      {c.prior.is_default ? (
                        <span className="block text-text-muted">
                          Solución por defecto del archivo
                        </span>
                      ) : null}
                      {priorArxiv ? (
                        <a href={priorArxiv} rel="noopener noreferrer">
                          Ver la previa en arXiv
                        </a>
                      ) : null}
                    </td>
                    <td className={TD_VALUE}>{formatSigma(c.sigma)} σ</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </TableScroll>
      </div>
      <p className="mt-3 text-sm text-text-muted">
        Umbral: {formatNumber(data.threshold_sigma)} σ. σ de referencia:{" "}
        {formatSigma(data.reference_sigma)}.
      </p>
      {archiveUrl ? (
        <p className="mt-1 text-sm">
          <a href={archiveUrl} rel="noopener noreferrer">
            Ficha del planeta en el NASA Exoplanet Archive
          </a>
        </p>
      ) : null}
    </>
  );
}
