import { useId } from "react";
import type { IndependentConfirmation } from "@/lib/api/types";
import { arxivAbsUrl, safeArchiveUrl } from "@/lib/externalLinks";
import {
  parameterTitle,
  formatSigma,
  formatWithErrors,
} from "@/lib/measurementFormat";
import {
  CAPTION,
  TABLE,
  TD_VALUE,
  TH,
  TH_ROW,
  TableScroll,
} from "./TableScroll";

type Props = {
  data: IndependentConfirmation;
};

export function ConfirmationData({ data }: Props) {
  const captionId = useId();
  const archiveUrl = safeArchiveUrl(data.archive_url);
  const refArxiv = arxivAbsUrl(data.reference.arxiv_id);
  const caption = `${parameterTitle(data.parameter)} de ${data.paper_planet_name}: medida del artículo frente a la referencia del archivo`;

  return (
    <>
      <div className="mt-3">
        <TableScroll labelledBy={captionId}>
          <table className={TABLE}>
            <caption id={captionId} className={CAPTION}>
              {caption}
            </caption>
            <thead>
              <tr>
                <th scope="col" className={TH}>
                  Medida del artículo
                </th>
                <th scope="col" className={TH}>
                  Diferencia con la referencia
                </th>
              </tr>
            </thead>
            <tbody>
              {data.measurements.map((m, index) => (
                <tr key={index}>
                  <th scope="row" className={TH_ROW}>
                    <span className="block">{data.paper_planet_name}</span>
                    <span className="block whitespace-nowrap">
                      {formatWithErrors(m)}
                    </span>
                  </th>
                  <td className={TD_VALUE}>
                    {data.sigmas[index] !== undefined
                      ? `${formatSigma(data.sigmas[index])} σ`
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      </div>
      <p className="mt-3 text-sm text-text">
        Referencia del archivo: {data.reference.refname},{" "}
        {formatWithErrors(data.reference)}.
        {` Publicada en el archivo el ${data.reference.releasedate}.`}{" "}
        Diferencia máxima: {formatSigma(data.max_sigma)} σ.
      </p>
      {refArxiv ? (
        <p className="mt-1 text-sm">
          <a href={refArxiv} rel="noopener noreferrer">
            Ver la referencia en arXiv
          </a>
        </p>
      ) : null}
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
