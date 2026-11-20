import { AlertTriangle, CheckCircle2, Loader2, ShieldCheck, Volume2, XCircle } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { AgentAnswer } from '../api/types'
import type { ChatMessage } from '../hooks/useChat'
import { toolLabel } from './labels'

interface Props {
  message: ChatMessage
  selected: boolean
  onSelect: () => void
  onSpeak?: (text: string) => void
}

export function MessageView({ message, selected, onSelect, onSpeak }: Props) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-[18px] rounded-br-md bg-ink px-4 py-2.5 text-[14.5px] leading-snug text-white">
          {message.text}
        </div>
      </div>
    )
  }

  const answer = message.answer
  return (
    <div
      className={`rounded-xl border bg-white px-4 py-3.5 shadow-[0_1px_2px_rgb(0_0_0/0.04)] transition-colors ${
        selected ? 'border-slate-900 ring-4 ring-slate-900/5' : 'border-slate-200'
      } ${answer ? 'cursor-pointer hover:border-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-900' : ''}`}
      onClick={answer ? onSelect : undefined}
      role={answer ? 'button' : undefined}
      tabIndex={answer ? 0 : undefined}
      aria-label={answer ? 'Show the evidence for this answer' : undefined}
      aria-pressed={answer ? selected : undefined}
      onKeyDown={
        answer
          ? (e) => {
              if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
                e.preventDefault()
                onSelect()
              }
            }
          : undefined
      }
    >
      {message.notice && <p className="mb-2 rounded-md bg-brand-50 px-3 py-1.5 text-xs text-brand-900">{message.notice}</p>}
      {(message.steps?.length ?? 0) > 0 && (
        <ul className="mb-3 space-y-1 border-b border-slate-100 pb-2.5">
          {message.steps!.map((s, i) => (
            <li key={i} className="flex min-w-0 items-center gap-2 text-[12.5px] text-slate-600">
              {!s.done ? (
                <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-brand-600" />
              ) : s.ok ? (
                <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600" />
              ) : (
                <XCircle className="h-3.5 w-3.5 shrink-0 text-amber-600" />
              )}
              <span className="shrink-0 font-medium text-slate-700">{toolLabel(s.tool)}</span>
              {s.done && s.summary && <span className="truncate text-slate-500">· {s.summary}</span>}
              {s.done && s.ms !== undefined && <span className="num ml-auto shrink-0 font-mono text-[11px] text-slate-400">{s.ms} ms</span>}
            </li>
          ))}
        </ul>
      )}

      {message.pending && (
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin" /> {message.status}
        </div>
      )}

      {message.error && (
        <div className="flex items-start gap-2 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 ring-1 ring-amber-200/70">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {message.error}
        </div>
      )}

      {answer && (
        <>
          <div className="prose-answer">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{answer.answer}</ReactMarkdown>
          </div>
          <div className="num mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-2.5 text-xs">
            <GroundingBadge grounding={answer.grounding} />
            <span className="text-slate-400">
              {answer.evidence.length} analyses · {answer.sources.length} sources · {formatMs(answer.trace.total_ms)}
            </span>
            {onSpeak && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  onSpeak(answer.answer)
                }}
                className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700"
                aria-label="Read answer aloud"
              >
                <Volume2 className="h-3.5 w-3.5" /> Listen
              </button>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function GroundingBadge({ grounding }: { grounding: AgentAnswer['grounding'] }) {
  // A numeric match against the analyses' outputs: it catches invented or misread figures,
  // it does not prove the narrative is right. The wording says exactly that.
  const badges = []
  if (grounding.checked === 0) {
    badges.push(
      <span key="none" className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-0.5 font-medium text-slate-600 ring-1 ring-slate-200">
        No figures to check
      </span>,
    )
  } else if (grounding.unverified.length === 0) {
    badges.push(
      <span
        key="ok"
        className="inline-flex items-center gap-1 rounded-md bg-emerald-50 px-2 py-0.5 font-medium text-emerald-800 ring-1 ring-emerald-200/70"
        title="Every figure in the answer matches a value returned by the analyses (numeric match, not a proof of the narrative)"
      >
        <ShieldCheck className="h-3.5 w-3.5" /> {grounding.checked} figures match the data
      </span>,
    )
  } else {
    badges.push(
      <span
        key="bad"
        className="inline-flex items-center gap-1 rounded-md bg-amber-50 px-2 py-0.5 font-medium text-amber-800 ring-1 ring-amber-200/70"
        title={`Not found in the analyses' outputs: ${grounding.unverified.join(', ')}`}
      >
        <AlertTriangle className="h-3.5 w-3.5" /> {grounding.unverified.length} not found in data:{' '}
        {grounding.unverified.slice(0, 3).join(', ')}
      </span>,
    )
  }
  if (grounding.confidence_overstated) {
    badges.push(
      <span
        key="conf"
        className="inline-flex items-center gap-1 rounded-md bg-amber-50 px-2 py-0.5 font-medium text-amber-800 ring-1 ring-amber-200/70"
        title="The answer states a higher confidence than the analyses returned"
      >
        <AlertTriangle className="h-3.5 w-3.5" /> Confidence overstated
      </span>,
    )
  }
  return <>{badges}</>
}

const formatMs = (ms: number) => (ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`)
