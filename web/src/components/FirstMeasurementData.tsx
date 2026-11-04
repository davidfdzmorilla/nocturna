import { useId } from "react";
import type { FirstMeasurement } from "@/lib/api/types";
import { safeArchiveUrl } from "@/lib/externalLinks";
import { formatWithErrors, parameterTitle } from "@/lib/measurementFormat";
import {
  CAPTION,
  TABLE,
  TD_VALUE,
  TH,
  TH_ROW,
  TableScroll,
} from "./TableScroll";

type Props = {
  data: FirstMeasurement;
};

const STATUS_TEXT: Record<FirstMeasurement["archive_status"], string> = {
  absent: "El planeta no figura en el NASA Exoplanet Archive.",
  no_comparable_solution:
    "El archivo tiene el planeta, pero ninguna solución publicada y confirmada con error en ambos sentidos para este parámetro.",
};

export function FirstMeasurementData({ data }: Props) {
  const captionId = useId();
  const archiveUrl = safeArchiveUrl(data.archive_url);
  const caption = `${parameterTitle(data.parameter)} de ${data.paper_planet_name} medida en el artículo`;

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
                  Planeta
                </th>
                <th scope="col" className={TH}>
                  Medida del artículo
                </th>
              </tr>
            </thead>
            <tbody>
              {data.measurements.map((m, index) => (
                <tr key={index}>
                  <th scope="row" className={TH_ROW}>
                    {data.paper_planet_name}
                  </th>
                  <td className={TD_VALUE}>{formatWithErrors(m)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      </div>
      <p className="mt-3 text-sm text-text-muted">
        {STATUS_TEXT[data.archive_status]}
        {data.archive_planet_name
          ? ` Nombre en el archivo: ${data.archive_planet_name}.`
          : ""}
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
