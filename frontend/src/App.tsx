import { ArrowUp, Database, Mic, MicOff, Plane, RotateCcw, Square, Volume2, VolumeX } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchHealth } from './api/client'
import type { Health } from './api/types'
import { EvidencePanel } from './components/EvidencePanel'
import { EXAMPLE_QUESTIONS, FOLLOW_UPS } from './components/labels'
import { MessageView } from './components/MessageView'
import { useChat } from './hooks/useChat'
import { useSpeech } from './hooks/useSpeech'

export default function App() {
  const { messages, busy, send, stop, reset } = useChat()
  const [input, setInput] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [autoSpeak, setAutoSpeak] = useState(false)
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const offlineOnly = health?.llm?.providers.every((p) => p.startsWith('offline')) ?? false

  const ask = async (text: string) => {
    setInput('')
    setSelectedId(null)
    const answer = await send(text)
    if (answer && autoSpeak) speech.speak(answer.answer)
  }
  const speech = useSpeech((transcript) => void ask(transcript))

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => setHealthError(true))
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  // Evidence panel shows the selected answer, or the latest one.
  const answered = messages.filter((m) => m.role === 'assistant' && m.answer)
  const active = useMemo(
    () => answered.find((m) => m.id === selectedId) ?? answered[answered.length - 1] ?? null,
    [answered, selectedId],
  )

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (input.trim() && !busy) void ask(input)
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-5 py-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-900 text-white">
          <Plane className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <h1 className="text-[15px] font-semibold leading-tight text-slate-900">Airport Investment Intelligence</h1>
          <p className="truncate text-xs text-slate-500">
            Terminal-expansion screening on public FAA & BTS data · deterministic scoring, AI explanation
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {health && (
            <span
              className="hidden items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600 md:inline-flex"
              title={Object.entries(health.data_periods).map(([k, v]) => `${k}: ${v}`).join('\n')}
            >
              <Database className="h-3.5 w-3.5" /> Traffic data through {health.data_periods.traffic?.split(' - ')[1]}
            </span>
          )}
          {healthError ? (
            <span className="rounded-full bg-rose-50 px-2.5 py-1 text-xs text-rose-700">Backend unreachable</span>
          ) : (
            health?.llm && (
              <span
                className={`rounded-full px-2.5 py-1 text-xs ${offlineOnly ? 'bg-amber-50 text-amber-700' : 'bg-teal-50 text-teal-700'}`}
                title={`LLM chain: ${health.llm.providers.join(' -> ')}`}
              >
                {offlineOnly ? 'Offline mode (no LLM key)' : health.llm.providers[0]}
              </span>
            )
          )}
          <button
            type="button"
            onClick={() => {
              reset()
              setSelectedId(null)
            }}
            className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
          >
            <RotateCcw className="h-4 w-4" /> New chat
          </button>
        </div>
      </header>

      <main className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <section className="flex min-h-0 flex-1 flex-col lg:w-[46%] lg:flex-none">
          <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto px-4 py-5 sm:px-6 scroll-thin">
            {messages.length === 0 ? (
              <EmptyState onPick={(q) => void ask(q)} />
            ) : (
              messages.map((m) => (
                <MessageView
                  key={m.id}
                  message={m}
                  selected={active?.id === m.id && answered.length > 1}
                  onSelect={() => setSelectedId(m.id)}
                  onSpeak={speech.canSpeak ? speech.speak : undefined}
                />
              ))
            )}
            {messages.length > 0 && !busy && (
              <div className="flex flex-wrap gap-2 pt-1">
                {FOLLOW_UPS.map((q) => (
                  <button
                    key={q}
                    type="button"
                    onClick={() => void ask(q)}
                    className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs text-slate-600 hover:border-brand-500 hover:text-brand-700"
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}
          </div>

          <form onSubmit={submit} className="border-t border-slate-200 bg-white px-4 py-3 sm:px-6">
            {speech.error && <p className="mb-1 text-xs text-amber-700">{speech.error}</p>}
            <div className="flex items-end gap-2 rounded-xl border border-slate-300 bg-white px-3 py-2 focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-100">
              <textarea
                value={speech.listening ? speech.interim : input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) submit(e)
                }}
                rows={1}
                placeholder={speech.listening ? 'Listening...' : 'Ask about US airports, e.g. "Compare LAX and SNA congestion"'}
                className="max-h-32 min-h-[24px] flex-1 resize-none bg-transparent text-[15px] outline-none placeholder:text-slate-400"
                aria-label="Message"
              />
              {speech.canSpeak && (
                <IconButton
                  label={autoSpeak ? 'Stop reading answers aloud' : 'Read answers aloud'}
                  onClick={() => {
                    if (autoSpeak) speech.cancelSpeech()
                    setAutoSpeak(!autoSpeak)
                  }}
                  active={autoSpeak}
                >
                  {autoSpeak ? <Volume2 className="h-4 w-4" /> : <VolumeX className="h-4 w-4" />}
                </IconButton>
              )}
              {speech.canListen && (
                <IconButton
                  label={speech.listening ? 'Stop listening' : 'Ask by voice'}
                  onClick={speech.listening ? speech.stop : speech.start}
                  active={speech.listening}
                  disabled={busy}
                >
                  {speech.listening ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
                </IconButton>
              )}
              {busy ? (
                <button type="button" onClick={stop} aria-label="Stop" className="rounded-lg bg-slate-800 p-2 text-white hover:bg-slate-700">
                  <Square className="h-4 w-4" />
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!input.trim()}
                  aria-label="Send"
                  className="rounded-lg bg-brand-600 p-2 text-white hover:bg-brand-700 disabled:bg-slate-300"
                >
                  <ArrowUp className="h-4 w-4" />
                </button>
              )}
            </div>
            <p className="mt-1.5 text-[11px] text-slate-400">
              Scores are screening signals computed by code from public data; the AI explains them. Not investment advice.
            </p>
          </form>
        </section>

        <aside className="min-h-[50vh] border-t border-slate-200 bg-slate-50 lg:min-h-0 lg:flex-1 lg:border-l lg:border-t-0">
          <EvidencePanel answer={active?.answer ?? null} />
        </aside>
      </main>
    </div>
  )
}

function EmptyState({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div className="mx-auto max-w-2xl pt-6 sm:pt-12">
      <h2 className="text-xl font-semibold text-slate-900">Where will terminal investment pay off?</h2>
      <p className="mt-1.5 text-sm text-slate-600">
        Ask about US airports. The agent ranks and compares them with a deterministic scoring model on FAA enplanements,
        BTS T-100 traffic, on-time performance and airfares, then explains its reasoning, assumptions and confidence.
      </p>
      <div className="mt-6 grid gap-2 sm:grid-cols-2">
        {EXAMPLE_QUESTIONS.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => onPick(q)}
            className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-left text-sm text-slate-700 shadow-sm transition hover:border-brand-500 hover:shadow"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  )
}

function IconButton(props: { label: string; onClick: () => void; active?: boolean; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      disabled={props.disabled}
      aria-label={props.label}
      title={props.label}
      className={`rounded-lg p-2 transition disabled:opacity-40 ${
        props.active ? 'bg-rose-50 text-rose-600' : 'text-slate-500 hover:bg-slate-100 hover:text-slate-700'
      }`}
    >
      {props.children}
    </button>
  )
}
