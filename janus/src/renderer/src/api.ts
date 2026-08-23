import type { BackendStatus } from './types'

export const JANUS_BASE = import.meta.env.VITE_JANUS_BASE ?? 'http://127.0.0.1:8765'

export function janusAuthToken(): string {
  return window.janus?.authToken ?? import.meta.env.VITE_JANUS_TOKEN ?? ''
}

export function websocketUrl(path: string): string {
  const url = new URL(path, JANUS_BASE)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

export function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('X-Janus-Token', janusAuthToken())
  return fetch(input, { ...init, headers })
}

export class ApiError extends Error {
  constructor(readonly status: number, detail?: string) {
    super(detail || `Janus API ${status}`)
  }
}

// Default stays `any` for legacy store call sites; new domain clients should pass T.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function apiJson<T = any>(input: RequestInfo | URL, init: RequestInit = {}): Promise<T> {
  const response = await apiFetch(input, init)
  if (!response.ok) {
    let detail = ''
    try { detail = String((await response.clone().json()).detail ?? '') }
    catch { detail = await response.text() }
    throw new ApiError(response.status, detail)
  }
  return response.json() as Promise<T>
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function janusApi<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  return apiJson<T>(`${JANUS_BASE}${path}`, init)
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export async function readBackendStatus(): Promise<BackendStatus | null> {
  try { return (await window.janus?.backendStatus()) ?? null }
  catch { return null }
}
