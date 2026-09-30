# Fixtures del NASA Exoplanet Archive

Respuestas reales de la API TAP y del servicio de alias, capturadas una sola vez
y guardadas verbatim. Ningún test sale a la red: se sirven con `httpx.MockTransport`.

Capturadas el **2026-09-28** contra `https://exoplanetarchive.ipac.caltech.edu`
con `User-Agent: nocturna/0.1.0`, `timeout=30s` y ≥2 s entre peticiones.
Máximo 5 peticiones reales para este sondeo (T71, paso 0); una de ellas fue
un reintento tras un error de columna, así que no hay margen para más capturas
sin volver a salir a la red.

| Fichero | Consulta / URL | Para qué |
|---|---|---|
| `ps_wasp12_masses_radii_period.csv` | TAP sync, `format=csv`: `select top 5 pl_name,hostname,default_flag,pl_refname,pl_bmasse,pl_bmasseerr1,pl_bmasseerr2,pl_bmasselim,pl_rade,pl_radeerr1,pl_radeerr2,pl_radelim,pl_orbper,pl_orbpererr1,pl_orbpererr2,pl_orbperlim from ps where hostname='WASP-12'` | 5 referencias distintas para WASP-12 b en la tabla `ps` (no la "por defecto" `pscomppars`): masa/radio/periodo con error simétrico, error asimétrico (`pl_bmasseerr1 != -pl_bmasseerr2` en Hebb et al. 2009), campos vacíos cuando la referencia no mide ese parámetro, y `pl_refname` como HTML `<a href=...ui.adsabs.harvard.edu/abs/...>` (ADS), nunca como URL de arXiv |
| `pscomppars_error_gaia_id.xml` | TAP sync, `format=csv`, columna `gaia_id` en `select pl_name,hostname,hd_name,hip_name,tic_id,gaia_id,sy_pnum from pscomppars` | **`gaia_id` no existe** en `pscomppars` (tampoco en `ps`). El TAP devuelve HTTP 200 con un VOTABLE de error (`QUERY_STATUS=ERROR`, `ORA-00904: 'GAIA_ID': invalid identifier`) aunque se pidió `format=csv`: un error de columna no da el `format` pedido, da XML |
| `pscomppars_ids_sample30.csv` | **recortado**, ver nota abajo: `select pl_name,hostname,hd_name,hip_name,tic_id,sy_pnum from pscomppars` (sin `gaia_id`, tras el error anterior) | Identificadores cruzados por planeta en la vista "un registro por planeta". `hd_name`/`hip_name` casi siempre vacíos (la mayoría de hosts son Kepler/K2/TIC sin HD/HIP), `tic_id` casi siempre presente, `sy_pnum` como entero de multiplicidad del sistema |
| `ps_count_arxiv_refname.csv` | TAP sync, `format=csv`: `select count(*) from ps where pl_refname like '%arXiv%'` | `103` filas cuyo `pl_refname` contiene el literal `arXiv` (probablemente preprints sin publicación formal todavía). Solo se capturó el `count`; no hay ejemplo literal de un `pl_refname` con `arXiv` en estas fixtures, porque agotar el cupo de 5 peticiones con una consulta adicional habría dejado sin capturar la de alias (petición 4). Los `pl_refname` capturados en `ps_wasp12_masses_radii_period.csv` son todos del estilo ADS (`<a refstr=HEBB_ET_AL__2009 href=https://ui.adsabs.harvard.edu/abs/...>`), no del estilo arXiv — queda como decisión abierta para el paso siguiente confirmar el formato exacto de esos 103 |
| `aliaslookup_wasp12.json` | `https://exoplanetarchive.ipac.caltech.edu/cgi-bin/Lookup/nph-aliaslookup.py?objname=WASP-12` | Respuesta completa (no recortada, 3606 bytes). **`Content-Type: text/plain`** pero el cuerpo es JSON. Estructura anidada: `manifest` (estado de la resolución), `system.system_info.alias_set` (alias del sistema), `system.objects.stellar_set.stars` (una entrada por estrella, con sus propios alias: Gaia DR2/DR3, TIC, TYC, 2MASS, WISE, designación SuperWASP) y `system.objects.planet_set.planets` (alias por planeta, incluye designación TESS `TOI-1725.01`) |

## Nota sobre `pscomppars_ids_sample30.csv`

La consulta real devolvió **6372 filas / 316823 bytes**. El fichero de la fixture
se recortó a las primeras 30 filas (31 líneas contando la cabecera) para no
versionar un CSV de 300 KB; el tamaño real queda documentado aquí, no en el
fichero.

## Petición que no se pudo completar como estaba escrita

La petición 2 tal como estaba especificada (`... , gaia_id, ...`) falló con
`ORA-00904: 'GAIA_ID': invalid identifier` en las tablas `ps` y `pscomppars`.
Se repitió sin esa columna (sigue dentro del cupo de 5 peticiones reales,
contando la fallida) para poder capturar `pscomppars_ids_sample30.csv`. Pendiente
para el siguiente paso: averiguar el nombre real de la columna de Gaia en el
Exoplanet Archive (si existe) o confirmar que no está expuesta en estas tablas.

## Capturas de T74 (2026-09-30)

Cuatro peticiones reales al TAP sync (`format=csv`), `User-Agent:
nocturna/0.1.0`, timeout 30 s, 2,5 s entre ellas. Respuestas guardadas tal cual.

| Fichero | Consulta | Notas |
|---|---|---|
| `ps_refname_arxiv_top10.csv` | `select top 10 pl_name,pl_refname from ps where pl_refname like '%arXiv%'` | 10 filas, todas preprints. El bibcode del `href` lleva `arXiv`: `2011arXiv1102.1375F` (4+4 dígitos, con punto) y `2015arXiv150907750N` (sin punto, 4+5). Regex `abs/\d{4}arXiv(\d{4})\.?(\d{4,5})` -> `1102.1375` y `1509.07750` |
| `ps_v1298tau.csv` | `select pl_name,hostname,default_flag,pl_refname,pl_bmassprov,pl_bmasse,pl_bmasseerr1,pl_bmasseerr2,pl_bmasselim,pl_rade,pl_radeerr1,pl_radeerr2,pl_radelim,pl_orbper,pl_orbpererr1,pl_orbpererr2,pl_orbperlim from ps where pl_name in ('V1298 Tau b','V1298 Tau e')` | 12 filas. Default (`default_flag=1`): Livingston et al. 2026 (`2026Natur.649..310L`, revista: sin id arXiv) para b y e. Incluye Suárez Mascareño et al. 2022 (`Mass`) y el nombre con entidades HTML (`Su&aacute;rez Mascare&ntilde;o`). No hay fila del paper 2609.30038 |
| `ps_distinct_bmassprov.csv` | `select distinct pl_bmassprov from ps` | Valores: vacío, `Msini`, `Mass`, `Msin(i)/sin(i)`. Solo `Mass` es masa verdadera |
| `pscomppars_names_full.csv` | `select pl_name from pscomppars` | **Completa, sin recortar**: 6372 filas / 91976 bytes |

## Regenerarlas

Las consultas exactas están en la tabla. Respeta ≥2 s entre peticiones y el
`User-Agent: nocturna/0.1.0`.
