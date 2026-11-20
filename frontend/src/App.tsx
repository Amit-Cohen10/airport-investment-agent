import { ArrowRight, ArrowUp, Database, Mic, MicOff, Plane, RotateCcw, Square, Volume2, VolumeX } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchHealth } from './api/client'
import type { Health } from './api/types'
import { AIRPORT_COUNT, AirportMap, DRAWN_COUNT } from './components/AirportMap'
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
  const endRef = useRef<HTMLDivElement>(null)
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
    // scrollIntoView works whichever element scrolls (the chat pane on desktop, the page on mobile).
    // Only once a conversation exists: the home screen must open at its top.
    if (messages.length > 0) endRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
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
      <header className="flex items-center gap-3 border-b border-black/40 bg-ink px-4 py-2.5 text-white sm:px-5">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-ink-soft text-sign ring-1 ring-sign/30">
          <Plane className="h-[18px] w-[18px]" aria-hidden />
        </div>
        <div className="min-w-0">
          <h1 className="truncate font-display text-[16px] font-normal leading-tight tracking-[-0.02em]">
            <span className="sm:hidden">Airport Intelligence</span>
            <span className="hidden sm:inline">Airport Investment Intelligence</span>
          </h1>
          <p className="hidden truncate text-[12px] leading-tight text-slate-400 sm:block">
            Terminal-expansion screening on public FAA &amp; BTS data · deterministic scoring, AI explanation
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {health && (
            <span
              className="num hidden items-center gap-1.5 rounded-md border border-white/10 px-2.5 py-1 text-[12px] text-slate-300 md:inline-flex"
              title={Object.entries(health.data_periods).map(([k, v]) => `${k}: ${v}`).join('\n')}
            >
              <Database className="h-3.5 w-3.5" /> Traffic data through {health.data_periods.traffic?.split(' - ')[1]}
            </span>
          )}
          {healthError ? (
            <span className="rounded-md bg-rose-500/15 px-2.5 py-1 text-[12px] text-rose-200 ring-1 ring-rose-400/30">Backend unreachable</span>
          ) : (
            health?.llm && (
              <span
                className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1 text-[12px] ring-1 ${
                  offlineOnly ? 'bg-amber-400/10 text-amber-200 ring-amber-300/30' : 'bg-emerald-400/10 text-emerald-200 ring-emerald-300/25'
                }`}
                title={`LLM chain: ${health.llm.providers.join(' -> ')}`}
              >
                <span className={`h-1.5 w-1.5 rounded-full ${offlineOnly ? 'bg-amber-300' : 'bg-emerald-300'}`} aria-hidden />
                {offlineOnly ? (<><span className="hidden sm:inline">Offline mode (no LLM key)</span><span className="sm:hidden">Offline</span></>) : health.llm.providers[0].split(':')[0]}
              </span>
            )
          )}
          <button
            type="button"
            aria-label="New chat"
            title="New chat"
            onClick={() => {
              reset()
              setSelectedId(null)
            }}
            className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-white/25 px-3.5 py-1.5 text-[13px] text-white transition-colors hover:bg-white hover:text-ink"
          >
            <RotateCcw className="h-4 w-4" aria-hidden />
            <span className="hidden sm:inline">New chat</span>
          </button>
        </div>
      </header>

      <main className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
        <section className="flex min-h-[88vh] flex-col lg:min-h-0 lg:w-[46%] lg:flex-none">
          <div className="flex-1 space-y-4 overflow-y-auto px-4 py-5 sm:px-6 scroll-thin" aria-live="polite" aria-busy={busy}>
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
                    className="rounded-full border border-slate-300 bg-white px-3 py-1 text-[12.5px] text-slate-700 transition-colors hover:border-slate-900 hover:text-slate-900"
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}
            <div ref={endRef} />
          </div>

          <form
            onSubmit={submit}
            className="sticky bottom-0 bg-gradient-to-t from-slate-50 from-60% to-slate-50/0 px-4 pb-3 pt-5 sm:px-6 lg:static"
          >
            {speech.error && <p className="mb-1 text-xs text-amber-700">{speech.error}</p>}
            <div className="flex items-end gap-2 rounded-[22px] border border-slate-200 bg-white py-1.5 pl-4 pr-1.5 shadow-[0_10px_30px_-14px_rgb(0_0_0/0.25),0_1px_2px_rgb(0_0_0/0.04)] transition-[border-color,box-shadow] focus-within:border-slate-900 focus-within:shadow-[0_12px_32px_-14px_rgb(0_0_0/0.3),0_0_0_4px_rgb(23_23_25/0.05)]">
              <textarea
                value={speech.listening ? speech.interim : input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) submit(e)
                }}
                rows={1}
                placeholder={speech.listening ? 'Listening...' : 'Ask about US airports, e.g. "Compare LAX and SNA congestion"'}
                className="scroll-none max-h-32 min-h-[24px] flex-1 resize-none self-center bg-transparent py-1 text-[15px] outline-none placeholder:text-slate-500"
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
                <button type="button" onClick={stop} aria-label="Stop" className="rounded-full bg-slate-900 p-2 text-white hover:bg-black">
                  <Square className="h-4 w-4" />
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!input.trim()}
                  aria-label="Send"
                  className="rounded-full bg-slate-900 p-2 text-white transition-colors hover:bg-black disabled:bg-slate-200 disabled:text-slate-400"
                >
                  <ArrowUp className="h-4 w-4" />
                </button>
              )}
            </div>
            <p className="mt-1.5 text-[11px] text-slate-500">
              Scores are screening priorities computed by code from public data; the AI explains them. Not investment advice.
            </p>
          </form>
        </section>

        <aside className="min-h-[70vh] border-t border-slate-200 bg-slate-100 lg:min-h-0 lg:flex-1 lg:border-l lg:border-t-0">
          <EvidencePanel answer={active?.answer ?? null} />
        </aside>
      </main>
    </div>
  )
}

function EmptyState({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div className="mx-auto max-w-2xl pt-2 sm:pt-6">
      <section className="rise overflow-hidden rounded-2xl bg-ink px-6 pb-5 pt-7 text-white sm:px-8 sm:pt-9">
        <h2 className="max-w-[22ch] font-display text-[34px] font-light leading-[1.05] tracking-[-0.04em] sm:text-[42px]">
          Where is terminal investment worth investigating?
        </h2>
        <div className="mt-5 grid items-end gap-4 2xl:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
          <div>
            <p className="text-[14px] leading-relaxed text-slate-300">
              Ask about US airports. The agent ranks and compares them with a deterministic scoring model on FAA
              enplanements, BTS T-100 traffic, on-time performance and airfares, then explains its reasoning,
              assumptions and confidence.
            </p>
            <p className="num mt-5 text-[12px] leading-snug text-slate-400">
              {AIRPORT_COUNT} US airports in the scoring universe; the {DRAWN_COUNT} in the contiguous US are drawn,
              sized by passengers · public FAA &amp; BTS data
            </p>
          </div>
          <AirportMap
            highlight={['BOS', 'LAX', 'SNA', 'SFO']}
            className="-mb-2 ml-auto block h-auto w-full sm:-mr-2 sm:max-h-[205px] sm:w-auto 2xl:max-h-none 2xl:w-full"
          />
        </div>
      </section>
      <ul className="rise mt-4 divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white [animation-delay:60ms]">
        {EXAMPLE_QUESTIONS.map((q) => (
          <li key={q}>
            <button
              type="button"
              onClick={() => onPick(q)}
              className="group flex w-full items-center gap-3 px-4 py-3.5 text-left text-[14px] text-slate-700 transition-colors hover:bg-slate-50 hover:text-slate-900"
            >
              <span className="flex-1">{q}</span>
              <ArrowRight
                className="h-4 w-4 shrink-0 text-slate-300 transition-transform group-hover:translate-x-0.5 group-hover:text-slate-900"
                aria-hidden
              />
            </button>
          </li>
        ))}
      </ul>
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
      className={`rounded-full p-2 transition-colors disabled:opacity-40 ${
        props.active ? 'bg-rose-50 text-rose-600' : 'text-slate-500 hover:bg-slate-100 hover:text-slate-700'
      }`}
    >
      {props.children}
    </button>
  )
}
