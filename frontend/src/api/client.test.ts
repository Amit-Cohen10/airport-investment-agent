import { afterEach, describe, expect, it, vi } from 'vitest'
import { streamChat } from './client'
import type { StreamEvent } from './types'

/** Serve `body` as a fetch response stream, split into the given chunk sizes. */
function mockStream(body: string, chunkSize: number) {
  const bytes = new TextEncoder().encode(body)
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (let i = 0; i < bytes.length; i += chunkSize) controller.enqueue(bytes.slice(i, i + chunkSize))
      controller.close()
    },
  })
  vi.stubGlobal('fetch', vi.fn(async () => new Response(stream, { status: 200 })))
}

const sse = (events: object[], eol = '\r\n') =>
  events.map((e) => `event: x${eol}data: ${JSON.stringify(e)}${eol}${eol}`).join('')

afterEach(() => vi.unstubAllGlobals())

describe('streamChat SSE parsing', () => {
  const events = [
    { type: 'session', session_id: 's1' },
    { type: 'tool_start', id: 'c1', tool: 'analyze_route_mix', arguments: { airport: 'ANC' } },
    { type: 'answer', answer: 'line1\n\nline2 with "quotes" and unicode ✓' },
  ]

  it.each([1, 2, 3, 7, 64, 4096])('parses CRLF events split into %i-byte chunks', async (size) => {
    mockStream(sse(events), size)
    const got: StreamEvent[] = []
    await streamChat('q', null, (e) => got.push(e))
    expect(got).toEqual(events)
  })

  it('parses LF-only streams', async () => {
    mockStream(sse(events, '\n'), 5)
    const got: StreamEvent[] = []
    await streamChat('q', null, (e) => got.push(e))
    expect(got).toHaveLength(3)
  })

  it('surfaces the server error detail', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'LLM not configured' }), { status: 503 })))
    await expect(streamChat('q', null, () => undefined)).rejects.toThrow('LLM not configured')
  })

  it('sends the session id', async () => {
    mockStream(sse([]), 10)
    await streamChat('hello', 'abc', () => undefined)
    const body = JSON.parse((vi.mocked(fetch).mock.calls[0][1] as RequestInit).body as string)
    expect(body).toEqual({ message: 'hello', session_id: 'abc' })
  })
})
