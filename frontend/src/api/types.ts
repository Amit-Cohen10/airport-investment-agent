// Mirrors backend/app/domain/models.py and the /api/chat answer payload.

export type Confidence = 'high' | 'medium' | 'low'

export interface Provenance {
  source: string
  period: string
  url?: string
  note?: string
}

export interface Kpi {
  label: string
  value: string
  hint?: string
}

export interface KpiBlock {
  type: 'kpis'
  title: string
  items: Kpi[]
}

export interface TableBlock {
  type: 'table'
  title: string
  columns: string[]
  rows: (string | number | null)[][]
  note?: string
}

export interface BarChartBlock {
  type: 'bar_chart'
  title: string
  categories: string[]
  series: { name: string; values: number[] }[]
  stacked: boolean
  unit?: string
}

export type UIBlock = KpiBlock | TableBlock | BarChartBlock

export interface ToolEvidence {
  tool: string
  ok: boolean
  summary: string
  data: Record<string, unknown>
  provenance: Provenance[]
  assumptions: string[]
  caveats: string[]
  confidence?: Confidence
  ui: UIBlock[]
}

export interface TraceStep {
  type: 'llm' | 'tool'
  tool?: string
  provider?: string
  ms: number
  ok?: boolean
  summary?: string
  arguments?: unknown
  tool_calls?: string[]
}

export interface AgentAnswer {
  session_id: string
  answer: string
  evidence: ToolEvidence[]
  sources: Provenance[]
  grounding: { checked: number; unverified: string[]; grounded_ratio: number }
  trace: {
    steps: TraceStep[]
    llm_calls: number
    prompt_tokens: number
    completion_tokens: number
    providers: string[]
    total_ms: number
  }
}

export type StreamEvent =
  | { type: 'session'; session_id: string; resumed: boolean }
  | { type: 'status'; message: string }
  | { type: 'tool_start'; id: string; tool: string; arguments: unknown }
  | { type: 'tool_end'; id: string; tool: string; ok: boolean; summary: string; ms: number }
  | ({ type: 'answer' } & AgentAnswer)
  | { type: 'error'; message: string }

export interface Health {
  status: string
  llm: { providers: string[] } | null
  llm_error: string | null
  data_periods: Record<string, string>
}
