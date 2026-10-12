/** 对话域 API（含 SSE 流式发送，规则 05 §5.2：fetch 流读取）。 */
import { request } from '@/shared/api/request'
import type { ChatMessageItem, SessionItem, SseEvent } from '@/shared/types/api'

export function listSessions(limit = 20): Promise<SessionItem[]> {
  return request.get('/chat/sessions', { params: { limit } })
}

export function createSession(domainId: string, title = ''): Promise<{ session_id: string }> {
  return request.post('/chat/sessions', { domain_id: domainId, title })
}

export function listMessages(sessionId: string, pageSize = 50): Promise<{ items: ChatMessageItem[] }> {
  return request.get(`/chat/sessions/${sessionId}/messages`, { params: { page_size: pageSize } })
}

export function sendFeedback(
  messageId: string,
  rating: 'adopted' | 'modified' | 'rejected',
  correction = '',
  comment = '',
): Promise<Record<string, unknown>> {
  return request.post(`/chat/messages/${messageId}/feedback`, { rating, correction, comment })
}

/** SSE 帧解析：event:/data: 双行帧（支持跨 chunk 缓冲）。 */
async function consumeSse(resp: Response, onEvent: (event: SseEvent) => void) {
  const reader = resp.body?.getReader()
  if (!reader) return
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let index = buffer.indexOf('\n\n')
    while (index !== -1) {
      const block = buffer.slice(0, index)
      buffer = buffer.slice(index + 2)
      index = buffer.indexOf('\n\n')
      let eventName = ''
      let dataText = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) eventName = line.slice(7).trim()
        else if (line.startsWith('data: ')) dataText = line.slice(6)
      }
      if (eventName && dataText) {
        try {
          onEvent({ event: eventName, data: JSON.parse(dataText) } as SseEvent)
        } catch {
          /* 忽略非法帧 */
        }
      }
    }
  }
}

async function postSse(path: string, body: unknown, onEvent: (event: SseEvent) => void) {
  const resp = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${localStorage.getItem('token') || ''}`,
    },
    body: JSON.stringify(body),
  })
  if (!resp.ok) {
    let message = `请求失败（${resp.status}）`
    try {
      const payload = await resp.json()
      message = payload.message || message
    } catch {
      /* keep */
    }
    onEvent({ event: 'error', data: { code: `HTTP_${resp.status}`, message } })
    return
  }
  await consumeSse(resp, onEvent)
}

export function sendMessage(sessionId: string, text: string, onEvent: (event: SseEvent) => void) {
  return postSse(`/api/v1/chat/sessions/${sessionId}/messages`, { text }, onEvent)
}

export function resumeSession(sessionId: string, value: string, onEvent: (event: SseEvent) => void) {
  return postSse(`/api/v1/chat/sessions/${sessionId}/resume`, { value }, onEvent)
}
