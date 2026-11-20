import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { BarChartBlock, KpiBlock, TableBlock, UIBlock } from '../../api/types'

// Validated categorical palette (distinct in hue and lightness, colorblind-friendly order).
const PALETTE = ['#2453e0', '#0d9488', '#d97706', '#9333ea', '#dc2626', '#64748b']

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

function Kpis({ block }: { block: KpiBlock }) {
  return (
    <section>
      <BlockTitle>{block.title}</BlockTitle>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {block.items.map((kpi) => (
          <div key={kpi.label} className="rounded-lg border border-slate-200 bg-white px-3 py-2">
            <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{kpi.label}</div>
            <div className="mt-0.5 text-lg font-semibold tabular-nums text-slate-900">{kpi.value}</div>
            {kpi.hint && <div className="text-xs text-slate-500">{kpi.hint}</div>}
          </div>
        ))}
      </div>
    </section>
  )
}

function Table({ block }: { block: TableBlock }) {
  return (
    <section>
      <BlockTitle>{block.title}</BlockTitle>
      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white scroll-thin">
        <table className="w-full text-left text-[13px]">
          <thead className="bg-slate-50 text-slate-600">
            <tr>
              {block.columns.map((c) => (
                <th key={c} className="whitespace-nowrap px-3 py-2 font-medium">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, i) => (
              <tr key={i} className="border-t border-slate-100">
                {row.map((cell, j) => (
                  <td key={j} className={`px-3 py-1.5 tabular-nums ${j === 0 ? 'font-medium text-slate-900' : 'text-slate-700'}`}>
                    {cell ?? '-'}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {block.note && <p className="mt-1 text-xs text-slate-500">{block.note}</p>}
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
      <div className="rounded-lg border border-slate-200 bg-white p-2" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16, top: 4, bottom: 4 }}>
            <CartesianGrid horizontal={false} stroke="#e2e8f0" />
            <XAxis type="number" tick={{ fontSize: 11, fill: '#64748b' }} unit={block.unit === '%' ? '%' : undefined} />
            <YAxis type="category" dataKey="category" width={92} tick={{ fontSize: 11, fill: '#334155' }} />
            <Tooltip
              cursor={{ fill: '#f1f5f9' }}
              contentStyle={{ fontSize: 12, borderRadius: 8, borderColor: '#e2e8f0' }}
              formatter={(v) => `${v}${block.unit === '%' ? '%' : ''}`}
            />
            {showLegend && <Legend wrapperStyle={{ fontSize: 11 }} iconSize={8} />}
            {block.series.map((s, i) => (
              <Bar
                key={s.name}
                dataKey={s.name}
                stackId={block.stacked ? 'a' : undefined}
                fill={PALETTE[i % PALETTE.length]}
                radius={block.stacked ? 0 : [0, 3, 3, 0]}
                maxBarSize={22}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

function BlockTitle({ children }: { children: React.ReactNode }) {
  return <h4 className="mb-1.5 text-[13px] font-semibold text-slate-700">{children}</h4>
}
