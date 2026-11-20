import { useMemo } from 'react'
import points from '../assets/airport-points.json'

/*
 * The home-screen "aerial view": the 233 peer airports the scoring model ranks, drawn from the
 * same data snapshot (backend/scripts/export_map_points.py), sized by annual passengers.
 * Albers equal-area conic (the standard US projection); contiguous US only, so Alaska, Hawaii and
 * the territories are left out of the drawing (not out of the analysis).
 */

interface Point {
  code: string
  lat: number
  lon: number
  pax: number
}

const W = 600
const H = 360
const rad = (d: number) => (d * Math.PI) / 180
const PHI1 = rad(29.5)
const PHI2 = rad(45.5)
const PHI0 = rad(37.5)
const LAMBDA0 = rad(-96)
const N = (Math.sin(PHI1) + Math.sin(PHI2)) / 2
const C = Math.cos(PHI1) ** 2 + 2 * N * Math.sin(PHI1)
const RHO0 = Math.sqrt(C - 2 * N * Math.sin(PHI0)) / N

function albers(lat: number, lon: number): [number, number] {
  const theta = N * (rad(lon) - LAMBDA0)
  const rho = Math.sqrt(C - 2 * N * Math.sin(rad(lat))) / N
  return [rho * Math.sin(theta), RHO0 - rho * Math.cos(theta)]
}

// Label placement for the highlighted codes: LAX and SNA sit ~35 mi apart, BOS is on the east edge,
// SFO on the west edge (its label goes inward so it never clips).
const LABEL_OFFSET: Record<string, [number, number]> = { LAX: [-50, -26], SNA: [12, 6], BOS: [-54, -10], SFO: [12, -24] }

const isContiguous = (p: Point) => p.lon > -125 && p.lon < -66 && p.lat > 24 && p.lat < 50

export function AirportMap({ highlight = [], className = '' }: { highlight?: string[]; className?: string }) {
  const dots = useMemo(() => {
    const inUs = (points as Point[]).filter(isContiguous)
    const projected = inUs.map((p) => ({ ...p, xy: albers(p.lat, p.lon) }))
    const xs = projected.map((p) => p.xy[0])
    const ys = projected.map((p) => p.xy[1])
    const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
    const pad = 18
    const scale = Math.min((W - 2 * pad) / (maxX - minX), (H - 2 * pad) / (maxY - minY))
    return projected.map((p) => ({
      code: p.code,
      x: pad + (p.xy[0] - minX) * scale,
      y: H - pad - (p.xy[1] - minY) * scale, // y grows downward in SVG
      r: 1.3 + 1.55 * Math.sqrt(p.pax / 1e6),
    }))
  }, [])

  const marked = dots.filter((d) => highlight.includes(d.code))

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className={`map-reveal ${className}`}
      role="img"
      aria-label={`Map of ${dots.length} US airports in the scoring universe, sized by annual passengers`}
    >
      {dots.map((d) => (
        <circle key={d.code} cx={d.x} cy={d.y} r={d.r} fill="#ffffff" fillOpacity={0.3} />
      ))}
      {marked.map((d) => (
        <g key={`m-${d.code}`}>
          <circle cx={d.x} cy={d.y} r={d.r + 4} fill="none" stroke="var(--color-sign)" strokeOpacity={0.45} />
          <circle cx={d.x} cy={d.y} r={Math.max(d.r, 2.5)} fill="var(--color-sign)" />
          <g
            transform={`translate(${d.x + (LABEL_OFFSET[d.code]?.[0] ?? d.r + 7)} ${d.y + (LABEL_OFFSET[d.code]?.[1] ?? -7)})`}
          >
            <rect width={40} height={19} rx={2.5} fill="var(--color-ink)" stroke="var(--color-sign)" strokeOpacity={0.5} />
            <text
              x={20}
              y={13.8}
              textAnchor="middle"
              fontFamily="IBM Plex Mono, monospace"
              fontWeight={600}
              fontSize={12.5}
              letterSpacing={0.6}
              fill="var(--color-sign)"
            >
              {d.code}
            </text>
          </g>
        </g>
      ))}
    </svg>
  )
}

export const AIRPORT_COUNT = (points as Point[]).length
