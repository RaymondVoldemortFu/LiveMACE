export class ApiError extends Error {
  status: number
  requestId: string
  code?: string
  body?: unknown

  constructor(message: string, status: number, requestId: string, code?: string, body?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.requestId = requestId
    this.code = code
    this.body = body
  }
}

const API_BASE_URL = '/api'

function extractMessage(body: unknown): string | undefined {
  if (!body || typeof body !== 'object') return undefined
  const record = body as Record<string, unknown>
  const detail = record.detail
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object') {
    const detailRecord = detail as Record<string, unknown>
    const nested = detailRecord.error
    if (nested && typeof nested === 'object') {
      const nestedMessage = (nested as Record<string, unknown>).message
      if (typeof nestedMessage === 'string' && nestedMessage) return nestedMessage
    }
    if (typeof detailRecord.message === 'string' && detailRecord.message) return detailRecord.message
  }
  const error = record.error
  if (error && typeof error === 'object') {
    const message = (error as Record<string, unknown>).message
    if (typeof message === 'string' && message) return message
  }
  if (typeof record.message === 'string' && record.message) return record.message
  return undefined
}

function extractCode(body: unknown): string | undefined {
  if (!body || typeof body !== 'object') return undefined
  const error = (body as Record<string, unknown>).error
  if (!error || typeof error !== 'object') return undefined
  const code = (error as Record<string, unknown>).code
  return typeof code === 'string' ? code : undefined
}

async function toApiError(response: Response, requestId: string): Promise<ApiError> {
  const headerId = response.headers.get('x-request-id') || requestId
  try {
    const body = await response.json()
    return new ApiError(
      extractMessage(body) || `HTTP error! status: ${response.status}`,
      response.status,
      headerId,
      extractCode(body),
      body,
    )
  } catch (error) {
    if (error instanceof ApiError) throw error
    return new ApiError(`HTTP error! status: ${response.status}`, response.status, headerId)
  }
}

export async function apiRequest(endpoint: string, options: RequestInit = {}): Promise<Response> {
  const requestId = crypto.randomUUID()
  const headers = new Headers(options.headers)
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  headers.set('X-Request-ID', requestId)
  const response = await fetch(`${API_BASE_URL}${endpoint}`, { ...options, headers })
  if (!response.ok) throw await toApiError(response, requestId)
  const contentType = response.headers.get('content-type')
  if (!contentType || !contentType.includes('application/json')) {
    throw new ApiError('Response is not JSON', response.status, requestId)
  }
  return response
}

export async function apiJson<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const response = await apiRequest(endpoint, options)
  return response.json() as Promise<T>
}
