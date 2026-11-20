import { useCallback, useEffect, useRef, useState } from 'react'

/* Minimal typings for the (still prefixed in Chrome) Web Speech API. */
interface SpeechRecognitionResultLike {
  isFinal: boolean
  0: { transcript: string }
}
interface SpeechRecognitionEventLike {
  resultIndex: number
  results: ArrayLike<SpeechRecognitionResultLike>
}
interface SpeechRecognitionLike {
  lang: string
  interimResults: boolean
  continuous: boolean
  start(): void
  stop(): void
  onresult: ((e: SpeechRecognitionEventLike) => void) | null
  onend: (() => void) | null
  onerror: ((e: { error: string }) => void) | null
}
type RecognitionCtor = new () => SpeechRecognitionLike

function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

/** Voice input (speech-to-text) and spoken answers (text-to-speech), feature-detected. */
export function useSpeech(onFinalTranscript: (text: string) => void) {
  const [listening, setListening] = useState(false)
  const [interim, setInterim] = useState('')
  const [error, setError] = useState<string | null>(null)
  const recRef = useRef<SpeechRecognitionLike | null>(null)
  const callbackRef = useRef(onFinalTranscript)
  callbackRef.current = onFinalTranscript

  const canListen = typeof window !== 'undefined' && recognitionCtor() !== null
  const canSpeak = typeof window !== 'undefined' && 'speechSynthesis' in window

  const start = useCallback(() => {
    const Ctor = recognitionCtor()
    if (!Ctor) return
    const rec = new Ctor()
    rec.lang = 'en-US'
    rec.interimResults = true
    rec.continuous = false
    let finalText = ''
    rec.onresult = (e) => {
      let partial = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i]
        if (r.isFinal) finalText += r[0].transcript
        else partial += r[0].transcript
      }
      setInterim(finalText + partial)
    }
    rec.onerror = (e) => setError(e.error === 'not-allowed' ? 'Microphone permission denied' : `Voice error: ${e.error}`)
    rec.onend = () => {
      setListening(false)
      setInterim('')
      if (finalText.trim()) callbackRef.current(finalText.trim())
    }
    setError(null)
    recRef.current = rec
    setListening(true)
    rec.start()
  }, [])

  const stop = useCallback(() => recRef.current?.stop(), [])

  const speak = useCallback((markdown: string) => {
    if (!('speechSynthesis' in window)) return
    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(toSpeech(markdown))
    utterance.lang = 'en-US'
    utterance.rate = 1.05
    window.speechSynthesis.speak(utterance)
  }, [])

  const cancelSpeech = useCallback(() => {
    if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  }, [])

  useEffect(() => () => recRef.current?.stop(), [])

  return { canListen, canSpeak, listening, interim, error, start, stop, speak, cancelSpeech }
}

/** Speak only the direct answer (first real paragraph, skipping italic notes such as the offline-mode
 *  disclaimer) without markdown symbols. */
function toSpeech(markdown: string): string {
  const blocks = markdown.split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean)
  const firstBlock = blocks.find((b) => !/^_.*_$/s.test(b)) ?? blocks[0] ?? markdown
  return firstBlock.replace(/[*_#`>|]/g, '').replace(/\[(.*?)\]\(.*?\)/g, '$1').trim()
}
