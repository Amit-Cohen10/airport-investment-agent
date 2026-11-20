import { BarChart3, BookOpen, ExternalLink, ListTree } from 'lucide-react'
import { useState } from 'react'
import type { AgentAnswer } from '../api/types'
import { Block } from './blocks/Blocks'
import { toolLabel } from './labels'

type Tab = 'evidence' | 'sources' | 'trace'
const TABS: Tab[] = ['evidence', 'sources', 'trace']

const CONFIDENCE_STYLE: Record<string, string> = {
  high: 'bg-teal-50 text-teal-700',
  medium: 'bg-amber-50 text-amber-700',
  low: 'bg-rose-50 text-rose-700',
}

export function EvidencePanel({ answer }: { answer: AgentAnswer | null }) {
  const [tab, setTab] = useState<Tab>('evidence')

  if (!answer) {
    return (
      <div className="flex h-full flex-col items-center justify-center px-8 text-center text-slate-500">
        <BarChart3 className="mb-3 h-8 w-8 text-slate-300" />
        <p className="text-sm font-medium text-slate-600">Evidence appears here</p>
        <p className="mt-1 max-w-xs text-xs">
          Every answer is backed by deterministic analyses on public FAA and BTS data. Tables, charts, sources and
          the agent's reasoning trace show up in this panel.
        </p>
      </div>
    )
  }

  const assumptions = unique(answer.evidence.flatMap((e) => e.assumptions))
  const caveats = unique(answer.evidence.flatMap((e) => e.caveats))

  return (
    <div className="flex h-full flex-col">
      <div
        className="flex gap-1 border-b border-slate-200 px-4 pt-3"
        role="tablist"
        aria-label="Answer details"
        onKeyDown={(e) => {
          // WAI-ARIA tabs pattern: arrow keys move between tabs.
          const i = TABS.indexOf(tab)
          if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
            e.preventDefault()
            const next = TABS[(i + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length]
            setTab(next)
            document.getElementById(`tab-${next}`)?.focus()
          }
        }}
      >
        <TabButton id="evidence" active={tab === 'evidence'} onClick={() => setTab('evidence')} icon={<BarChart3 className="h-4 w-4" />}>
          Evidence
        </TabButton>
        <TabButton id="sources" active={tab === 'sources'} onClick={() => setTab('sources')} icon={<BookOpen className="h-4 w-4" />}>
          Sources & assumptions
        </TabButton>
        <TabButton id="trace" active={tab === 'trace'} onClick={() => setTab('trace')} icon={<ListTree className="h-4 w-4" />}>
          Trace
        </TabButton>
      </div>

      <div
        className="flex-1 space-y-5 overflow-y-auto px-4 py-4 scroll-thin"
        role="tabpanel"
        id={`panel-${tab}`}
        aria-labelledby={`tab-${tab}`}
        tabIndex={0}
      >
        {answer.evidence_from_earlier_turn && (
          <p className="rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-800">
            This answer reused results from earlier in the conversation; the evidence below is from that earlier
            analysis, with its original data periods.
          </p>
        )}
        {tab === 'evidence' &&
          (answer.evidence.length === 0 ? (
            <p className="text-sm text-slate-500">No analyses were run for this answer.</p>
          ) : (
            answer.evidence.map((e, i) => (
              <div key={i} className="space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold uppercase tracking-wide text-brand-700">{toolLabel(e.tool)}</span>
                  {e.confidence && (
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${CONFIDENCE_STYLE[e.confidence]}`}>
                      {e.confidence} confidence
                    </span>
                  )}
                </div>
                {e.ui.map((block, j) => (
                  <Block key={j} block={block} />
                ))}
              </div>
            ))
          ))}

        {tab === 'sources' && (
          <>
            <Section title="Data sources">
              <ul className="space-y-2">
                {answer.sources.map((s, i) => (
                  <li key={i} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-[13px]">
                    <div className="flex items-start justify-between gap-2">
                      <span className="font-medium text-slate-800">{s.source}</span>
                      {s.url && (
                        <a href={s.url} target="_blank" rel="noreferrer" className="shrink-0 text-brand-600 hover:text-brand-700" aria-label="Open source">
                          <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                      )}
                    </div>
                    <div className="text-xs text-slate-500">
                      Period: {s.period}
                      {s.note ? ` · ${s.note}` : ''}
                    </div>
                  </li>
                ))}
              </ul>
            </Section>
            <Section title="Assumptions">
              <BulletList items={assumptions} />
            </Section>
            <Section title="Caveats & uncertainty">
              <BulletList items={caveats} />
            </Section>
          </>
        )}

        {tab === 'trace' && (
          <>
            <div className="grid grid-cols-3 gap-2 text-center">
              <Stat label="LLM calls" value={answer.trace.llm_calls} />
              <Stat label="Tokens" value={(answer.trace.prompt_tokens + answer.trace.completion_tokens).toLocaleString()} />
              <Stat label="Total" value={`${(answer.trace.total_ms / 1000).toFixed(1)} s`} />
            </div>
            <p className="text-xs text-slate-500">Model: {answer.trace.providers.join(', ') || 'n/a'}</p>
            <ol className="space-y-2">
              {answer.trace.steps.map((s, i) => (
                <li key={i} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-[13px]">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-slate-800">
                      {i + 1}. {s.type === 'llm' ? `LLM (${s.provider})` : toolLabel(s.tool ?? '')}
                    </span>
                    <span className="font-mono text-[11px] text-slate-400">{s.ms} ms</span>
                  </div>
                  {s.type === 'llm' && (
                    <div className="text-xs text-slate-500">
                      {s.tool_calls?.length ? `requested: ${s.tool_calls.join(', ')}` : 'wrote final answer'}
                    </div>
                  )}
                  {s.type === 'tool' && (
                    <>
                      <pre className="mt-1 overflow-x-auto rounded bg-slate-50 px-2 py-1 font-mono text-[11px] text-slate-600 scroll-thin">
                        {JSON.stringify(s.arguments)}
                      </pre>
                      <div className={`mt-1 text-xs ${s.ok ? 'text-slate-500' : 'text-amber-700'}`}>{s.summary}</div>
                    </>
                  )}
                </li>
              ))}
            </ol>
          </>
        )}
      </div>
    </div>
  )
}

function TabButton(props: {
  id: Tab
  active: boolean
  onClick: () => void
  icon: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      role="tab"
      id={`tab-${props.id}`}
      aria-selected={props.active}
      aria-controls={`panel-${props.id}`}
      tabIndex={props.active ? 0 : -1}
      onClick={props.onClick}
      className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 pb-2 text-[13px] font-medium ${
        props.active ? 'border-brand-600 text-brand-700' : 'border-transparent text-slate-500 hover:text-slate-700'
      }`}
    >
      {props.icon}
      {props.children}
    </button>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h4 className="mb-1.5 text-[13px] font-semibold text-slate-700">{title}</h4>
      {children}
    </section>
  )
}

function BulletList({ items }: { items: string[] }) {
  if (!items.length) return <p className="text-xs text-slate-500">None reported.</p>
  return (
    <ul className="list-disc space-y-1 pl-5 text-[13px] text-slate-700">
      {items.map((x) => (
        <li key={x}>{x}</li>
      ))}
    </ul>
  )
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-2 py-2">
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className="font-semibold tabular-nums text-slate-900">{value}</div>
    </div>
  )
}

const unique = (xs: string[]) => Array.from(new Set(xs))
