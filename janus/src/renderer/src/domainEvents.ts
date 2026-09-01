import { useEffect, useRef } from 'react'
import { janusAuthToken, websocketUrl } from './api'

export interface DomainEvent {
  topic: string
  event: string
  sequence?: number
  [key: string]: unknown
}

type Listener = (event: DomainEvent) => void

const listeners = new Map<string, Set<Listener>>()
let socket: WebSocket | null = null
let reconnectTimer: number | null = null
let reconnectAttempt = 0

function dispatch(event: DomainEvent): void {
  // 서버 큐가 넘쳐 무효화를 놓쳤다는 신호. 어떤 topic을 놓쳤는지 알 수 없으므로
  // 모든 구독자를 깨워 각자 다시 읽게 한다 — 아니면 낡은 화면이 그대로 남는다.
  if (event.topic === 'system' && event.event === 'resync') {
    for (const group of listeners.values()) for (const listener of group) listener(event)
    return
  }
  for (const listener of listeners.get(event.topic) ?? []) listener(event)
  for (const listener of listeners.get('*') ?? []) listener(event)
}

function scheduleReconnect(): void {
  if (listeners.size === 0 || reconnectTimer != null) return
  const delay = Math.min(4000, 250 * 2 ** reconnectAttempt++)
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null
    connect()
  }, delay)
}

function connect(): void {
  if (socket || listeners.size === 0 || import.meta.env.VITE_VISUAL_FIXTURE === '1') return
  const token = janusAuthToken()
  if (!token) return
  const next = new WebSocket(websocketUrl('/events'), ['janus', token])
  socket = next
  next.onopen = () => { reconnectAttempt = 0 }
  next.onmessage = (message) => {
    try { dispatch(JSON.parse(String(message.data)) as DomainEvent) } catch { /* invalid event */ }
  }
  next.onclose = () => {
    if (socket === next) socket = null
    scheduleReconnect()
  }
  next.onerror = () => next.close()
}

function disconnectIfIdle(): void {
  if (Array.from(listeners.values()).some((group) => group.size > 0)) return
  listeners.clear()
  if (reconnectTimer != null) window.clearTimeout(reconnectTimer)
  reconnectTimer = null
  reconnectAttempt = 0
  socket?.close()
  socket = null
}

export function subscribeDomainEvent(topic: string, listener: Listener): () => void {
  const group = listeners.get(topic) ?? new Set<Listener>()
  group.add(listener)
  listeners.set(topic, group)
  connect()
  return () => {
    group.delete(listener)
    if (group.size === 0) listeners.delete(topic)
    disconnectIfIdle()
  }
}

export function useDomainEvent(
  topic: string,
  listener: Listener,
  enabled = true
): void {
  const listenerRef = useRef(listener)
  listenerRef.current = listener
  useEffect(() => {
    if (!enabled) return
    return subscribeDomainEvent(topic, (event) => listenerRef.current(event))
  }, [topic, enabled])
}
