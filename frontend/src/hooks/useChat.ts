import { useCallback, useRef, useState } from 'react'
import { deleteSession, streamChat } from '../api/client'
import type { AgentAnswer } from '../api/types'

export interface ProgressStep {
  tool: string
  done: boolean
  ok?: boolean
  summary?: string
  ms?: number
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  answer?: AgentAnswer
  steps?: ProgressStep[]
  status?: string
  error?: string
  pending?: boolean
}

let counter = 0
const nextId = () => `m${Date.now()}_${counter++}`

/** Conversation state + streaming. One session per page; "New chat" resets it. */
export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy] = useState(false)
  const sessionRef = useRef<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const patch = (id: string, fn: (m: ChatMessage) => ChatMessage) =>
    setMessages((all) => all.map((m) => (m.id === id ? fn(m) : m)))

  const send = useCallback(async (text: string): Promise<AgentAnswer | null> => {
    const question = text.trim()
    if (!question || busy) return null
    const assistantId = nextId()
    setMessages((all) => [
      ...all,
      { id: nextId(), role: 'user', text: question },
      { id: assistantId, role: 'assistant', text: '', steps: [], pending: true, status: 'Thinking...' },
    ])
    setBusy(true)
    const controller = new AbortController()
    abortRef.current = controller
    let final: AgentAnswer | null = null
    try {
      await streamChat(
        question,
        sessionRef.current,
        (event) => {
          switch (event.type) {
            case 'session':
              sessionRef.current = event.session_id
              break
            case 'status':
              patch(assistantId, (m) => ({ ...m, status: event.message }))
              break
            case 'tool_start':
              patch(assistantId, (m) => ({ ...m, steps: [...(m.steps ?? []), { tool: event.tool, done: false }] }))
              break
            case 'tool_end':
              patch(assistantId, (m) => {
                const steps = [...(m.steps ?? [])]
                const i = steps.findIndex((s) => s.tool === event.tool && !s.done)
                if (i >= 0) steps[i] = { ...steps[i], done: true, ok: event.ok, summary: event.summary, ms: event.ms }
                return { ...m, steps }
              })
              break
            case 'answer':
              final = event
              patch(assistantId, (m) => ({ ...m, pending: false, text: event.answer, answer: event }))
              break
            case 'error':
              patch(assistantId, (m) => ({ ...m, pending: false, error: event.message }))
              break
          }
        },
        controller.signal,
      )
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Request failed'
      if (!controller.signal.aborted) patch(assistantId, (m) => ({ ...m, pending: false, error: message }))
    } finally {
      patch(assistantId, (m) => (m.pending ? { ...m, pending: false, error: m.error ?? 'Stopped.' } : m))
      setBusy(false)
      abortRef.current = null
    }
    return final
  }, [busy])

  const stop = useCallback(() => abortRef.current?.abort(), [])

  const reset = useCallback(() => {
    abortRef.current?.abort()
    if (sessionRef.current) void deleteSession(sessionRef.current)
    sessionRef.current = null
    setMessages([])
  }, [])

  return { messages, busy, send, stop, reset }
}
