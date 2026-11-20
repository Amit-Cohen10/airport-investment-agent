import points from '../assets/airport-points.json'

/** An IATA airport code, set like a taxiway location sign (see .apt-code in index.css). */
export function AirportCode({ code }: { code: string }) {
  return (
    <span className="apt-code" aria-label={`Airport ${code}`}>
      {code}
    </span>
  )
}

const CODE_ONLY = /^[A-Z]{3}$/
// The scored airports always render as airports (USA is Concord-Padgett Regional). Other three-letter
// uppercase values do too (route destinations such as HKG), except known non-airport tokens: weather
// flight categories, data sources and abbreviations.
const SCORED = new Set(points.map((p) => p.code))
const NOT_AIRPORTS = new Set(['VFR', 'IFR', 'MVFR', 'LIFR', 'NAS', 'FAA', 'BTS', 'TTM', 'THE'])
export const isAirportCode = (text: string) => CODE_ONLY.test(text) && (SCORED.has(text) || !NOT_AIRPORTS.has(text))
const CODE_AND_NAME = /^([A-Z]{3}) - (.+)$/

/**
 * Render a table cell or heading: airport codes become code chips; everything else is unchanged text.
 * Purely presentational: the underlying values come from the backend as-is.
 */
export function CellText({ value }: { value: string | number | null }) {
  if (value === null || value === undefined) return <>-</>
  const text = String(value)
  if (isAirportCode(text)) return <AirportCode code={text} />
  const named = CODE_AND_NAME.exec(text)
  if (named && isAirportCode(named[1])) {
    return (
      <span className="inline-flex items-baseline gap-2">
        <AirportCode code={named[1]} />
        <span className="text-slate-600">{named[2]}</span>
      </span>
    )
  }
  return <>{text}</>
}

/** Numeric-looking cells (12.4%, 21.0M, 18.3 min, 19/233, +0.5 pp, "18.3 min (p68)") are right-aligned. */
export const isNumericCell = (value: string | number | null) =>
  typeof value === 'number' ||
  (typeof value === 'string' &&
    /^[~+\-−]?[\d.,]+(\/\d+)?\s?(%|pp|M|K|B|min|s|ms)?(\s\(p\d+\))?$/.test(value.trim()))
