import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { BarChartBlock, KpiBlock, TableBlock, UIBlock } from '../../api/types'
import { CellText, isNumericCell } from '../AirportCode'

// Categorical palette: ink, teal, amber, violet, rose, gray. Distinct in hue and lightness,
// colorblind-friendly order; the first series takes the monochrome product ink.
const PALETTE = ['#232325', '#2a8c82', '#c98a12', '#7a5bc4', '#c23a4b', '#909096']
const AXIS = { fontSize: 11, fill: '#6b6b70', fontFamily: 'Inter' }

export function Block({ block }: { block: UIBlock }) {
  switch (block.type) {
    case 'kpis':
      return <Kpis block={block} />
    case 'table':
      return <Table block={block} />
    case 'bar_chart':
      return <Chart block={block} />
  }
}

/** Stat grid: hairline-divided cells (a ledger, not a stack of cards). */
function Kpis({ block }: { block: KpiBlock }) {
  return (
    <section>
      <BlockTitle>{block.title}</BlockTitle>
      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-slate-200 bg-slate-200 sm:grid-cols-3">
        {block.items.map((kpi) => (
          <div key={kpi.label} className="bg-white px-3 py-2.5">
            <dt className="text-[12px] leading-tight text-slate-500">{kpi.label}</dt>
            <dd className="num mt-1 text-[17px] font-semibold leading-tight tracking-[-0.01em] text-slate-900">
              <CellText value={kpi.value} />
            </dd>
            {kpi.hint && <dd className="num mt-0.5 text-[11.5px] leading-snug text-slate-500">{kpi.hint}</dd>}
          </div>
        ))}
      </dl>
    </section>
  )
}

function Table({ block }: { block: TableBlock }) {
  // A column is numeric when every non-empty cell in it looks like a number.
  const numeric = block.columns.map((_, j) =>
    block.rows.every((row) => row[j] === null || row[j] === '-' || row[j] === 'n/a' || isNumericCell(row[j])),
  )
  return (
    <section>
      <BlockTitle>{block.title}</BlockTitle>
      <div className="overflow-x-auto rounded-md border border-slate-200 bg-white scroll-thin">
        <table className="num w-full text-left text-[13px]">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50">
              {block.columns.map((c, j) => (
                <th
                  key={c}
                  scope="col"
                  className={`whitespace-nowrap px-3 py-2 text-[11.5px] font-medium text-slate-500 ${numeric[j] ? 'text-right' : ''}`}
                >
                  <CellText value={c} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, i) => (
              <tr key={i} className="border-t border-slate-100 first:border-t-0 hover:bg-slate-50">
                {row.map((cell, j) => (
                  <td
                    key={j}
                    className={`px-3 py-1.5 align-top ${j === 0 ? 'font-medium text-slate-900' : 'text-slate-700'} ${
                      numeric[j] ? 'whitespace-nowrap text-right' : ''
                    }`}
                  >
                    <CellText value={cell} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {block.note && <p className="mt-1.5 text-[11.5px] leading-snug text-slate-500">{block.note}</p>}
    </section>
  )
}

function Chart({ block }: { block: BarChartBlock }) {
  const data = block.categories.map((category, i) => {
    const row: Record<string, string | number> = { category }
    block.series.forEach((s) => (row[s.name] = s.values[i]))
    return row
  })
  const height = Math.max(180, Math.min(320, 60 + block.categories.length * 28))
  const showLegend = block.series.length > 1
  return (
    <section>
      <BlockTitle>{block.title}</BlockTitle>
      <div className="rounded-md border border-slate-200 bg-white p-2" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16, top: 4, bottom: 4 }}>
            <CartesianGrid horizontal={false} stroke="#eef1f5" />
            <XAxis type="number" tick={AXIS} axisLine={{ stroke: '#e6e6e8' }} tickLine={false} unit={block.unit === '%' ? '%' : undefined} />
            <YAxis
              type="category"
              dataKey="category"
              width={96}
              tick={{ ...AXIS, fill: '#232325' }}
              axisLine={{ stroke: '#e6e6e8' }}
              tickLine={false}
            />
            <Tooltip
              cursor={{ fill: '#fafafa' }}
              contentStyle={{ fontSize: 12, fontFamily: 'Inter', borderRadius: 6, borderColor: '#e6e6e8' }}
              formatter={(v) => `${v}${block.unit === '%' ? '%' : ''}`}
            />
            {showLegend && <Legend wrapperStyle={{ fontSize: 11, fontFamily: 'Inter' }} iconSize={8} />}
            {block.series.map((s, i) => (
              <Bar
                key={s.name}
                dataKey={s.name}
                stackId={block.stacked ? 'a' : undefined}
                fill={PALETTE[i % PALETTE.length]}
                radius={block.stacked ? 0 : [0, 2, 2, 0]}
                maxBarSize={20}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

function BlockTitle({ children }: { children: React.ReactNode }) {
  return <h4 className="mb-2 text-[12.5px] font-semibold text-slate-700">{children}</h4>
}
