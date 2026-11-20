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
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-brand-600 px-4 py-2.5 text-[15px] text-white shadow-sm">
          {message.text}
        </div>
      </div>
    )
  }

  const answer = message.answer
  return (
    <div
      className={`rounded-2xl rounded-bl-sm border bg-white px-4 py-3 shadow-sm transition ${
        selected ? 'border-brand-500 ring-2 ring-brand-100' : 'border-slate-200'
      } ${answer ? 'cursor-pointer' : ''}`}
      onClick={answer ? onSelect : undefined}
    >
      {(message.steps?.length ?? 0) > 0 && (
        <ul className="mb-2 space-y-1">
          {message.steps!.map((s, i) => (
            <li key={i} className="flex items-center gap-2 text-[13px] text-slate-600">
              {!s.done ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-brand-600" />
              ) : s.ok ? (
                <CheckCircle2 className="h-3.5 w-3.5 text-teal-600" />
              ) : (
                <XCircle className="h-3.5 w-3.5 text-amber-600" />
              )}
              <span className="font-medium">{toolLabel(s.tool)}</span>
              {s.done && s.summary && <span className="truncate text-slate-500">· {s.summary}</span>}
              {s.done && s.ms !== undefined && <span className="ml-auto shrink-0 font-mono text-[11px] text-slate-400">{s.ms} ms</span>}
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
        <div className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {message.error}
        </div>
      )}

      {answer && (
        <>
          <div className="prose-answer">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{answer.answer}</ReactMarkdown>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-2 text-xs">
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
                className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
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
  if (grounding.unverified.length === 0) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-teal-50 px-2 py-0.5 font-medium text-teal-700" title="Every number in the answer matches a tool output">
        <ShieldCheck className="h-3.5 w-3.5" /> {grounding.checked} numbers verified
      </span>
    )
  }
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 font-medium text-amber-700"
      title={`Not found in tool outputs: ${grounding.unverified.join(', ')}`}
    >
      <AlertTriangle className="h-3.5 w-3.5" /> {grounding.unverified.length} unverified: {grounding.unverified.slice(0, 3).join(', ')}
    </span>
  )
}

const formatMs = (ms: number) => (ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`)
