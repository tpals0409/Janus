import { useEffect, useRef } from 'react'

export interface DomainEvent {
  topic: string
  event: string
  sequence?: number
  [key: string]: unknown
}

type Listener = (event: DomainEvent) => void

const BASE = import.meta.env.VITE_JANUS_BASE ?? 'http://127.0.0.1:8765'
const listeners = new Map<string, Set<Listener>>()
let socket: WebSocket | null = null
let reconnectTimer: number | null = null
let reconnectAttempt = 0

function eventUrl(): string {
  const url = new URL('/events', BASE)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

function authToken(): string {
  return window.janus?.authToken ?? import.meta.env.VITE_JANUS_TOKEN ?? ''
}

function dispatch(event: DomainEvent): void {
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
  const token = authToken()
  if (!token) return
  const next = new WebSocket(eventUrl(), ['janus', token])
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

