/** 对话域 store：会话、消息、流式回合状态（规则 N-03：仅跨页面状态入 store）。 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as chatApi from '@/modules/chat/api/chat'
import type { ChatMessageItem, Citation, EvalResult, SessionItem, SseEvent } from '@/shared/types/api'
import { request } from '@/shared/api/request'

export interface StepItem {
  node: string
  status: string
  summary: string
}

export const useChatStore = defineStore('chat', () => {
  const domainId = ref<string>('chemical')
  const sessions = ref<SessionItem[]>([])
  const currentSessionId = ref<string>('')
  const messages = ref<ChatMessageItem[]>([])
  const streaming = ref(false)
  const streamText = ref('')
  const steps = ref<StepItem[]>([])
  const citations = ref<Citation[]>([])
  const evalResult = ref<EvalResult | null>(null)
  const clarify = ref<{
    question: string
    missing_slots?: string[]
    kind?: string
    approval_id?: string
    review_id?: string
    reasons?: string[]
    tool?: string
    card?: { tool: string; args_preview: Record<string, string>; risk: string }
  } | null>(null)
  const annotations = ref<string[]>([])
  const degradeLevel = ref<string>('L0')
  const errorMessage = ref<string>('')
  const demoMode = ref<string>('offline')

  async function loadSessions() {
    sessions.value = await chatApi.listSessions()
  }

  async function openSession(sessionId: string) {
    currentSessionId.value = sessionId
    resetTurnState()
    const data = await chatApi.listMessages(sessionId)
    messages.value = data.items
    const session = sessions.value.find((item) => item.session_id === sessionId)
    if (session) domainId.value = session.domain_id
  }

  async function newSession(domain: string) {
    const data = await chatApi.createSession(domain)
    domainId.value = domain
    await loadSessions()
    await openSession(data.session_id)
    return data.session_id
  }

  function resetTurnState() {
    streamText.value = ''
    steps.value = []
    citations.value = []
    evalResult.value = null
    clarify.value = null
    annotations.value = []
    errorMessage.value = ''
  }

  function handleEvent(event: SseEvent) {
    switch (event.event) {
      case 'step':
        steps.value = [...steps.value, event.data]
        break
      case 'citation':
        citations.value = [...citations.value, event.data]
        break
      case 'token':
        streamText.value += event.data.text
        break
      case 'eval':
        // 即时预览（完整结果在 done 帧与消息 eval_ref 中）
        evalResult.value = {
          ...(evalResult.value as EvalResult),
          total_score: event.data.total_score,
          scenario: event.data.scenario,
          confidence: event.data.confidence,
        } as EvalResult
        break
      case 'clarify':
        clarify.value = event.data
        break
      case 'error':
        errorMessage.value = event.data.message
        break
      case 'done': {
        degradeLevel.value = event.data.data?.degrade_level || 'L0'
        annotations.value = event.data.data?.annotations || []
        const full = (event.data.data as { eval?: EvalResult }).eval
        if (full) {
          evalResult.value = full
        }
        break
      }
      default:
        break
    }
  }

  async function loadEval(evalId: string) {
    if (!evalId) return
    try {
      evalResult.value = await request.get<EvalResult>(`/evaluations/${evalId}`)
    } catch {
      /* 评估加载失败不阻断对话 */
    }
  }

  async function send(text: string) {
    if (!currentSessionId.value || streaming.value) return
    streaming.value = true
    resetTurnState()
    messages.value = [
      ...messages.value,
      {
        message_id: `local-${Date.now()}`,
        role: 'user',
        content: text,
        citations: [],
        conflicts: [],
        eval_ref: null,
        degrade_level: 'L0',
        feedback: null,
        trace_id: null,
        meta: {},
        created_at: null,
      },
    ]
    try {
      await chatApi.sendMessage(currentSessionId.value, text, handleEvent)
    } finally {
      streaming.value = false
      await refreshMessages()
    }
  }

  async function resume(value: string, approvalId = '', reviewId = '') {
    if (!currentSessionId.value || streaming.value) return
    streaming.value = true
    clarify.value = null
    try {
      await chatApi.resumeSession(currentSessionId.value, value, handleEvent, approvalId, reviewId)
    } finally {
      streaming.value = false
      await refreshMessages()
    }
  }

  async function refreshMessages() {
    if (!currentSessionId.value) return
    const data = await chatApi.listMessages(currentSessionId.value)
    messages.value = data.items
    const last = [...data.items].reverse().find((item) => item.role === 'assistant')
    if (last) {
      citations.value = last.citations || []
      const meta = last.meta as { steps?: StepItem[]; annotations?: string[]; demo_mode?: string }
      steps.value = meta.steps || steps.value
      annotations.value = meta.annotations || annotations.value
      degradeLevel.value = last.degrade_level || 'L0'
      demoMode.value = meta.demo_mode || demoMode.value
      if (last.eval_ref) {
        void loadEval(last.eval_ref)
      }
    }
  }

  function feedback(messageId: string, rating: 'adopted' | 'modified' | 'rejected', correction = '') {
    return chatApi.sendFeedback(messageId, rating, correction).then(refreshMessages)
  }

  return {
    domainId,
    sessions,
    currentSessionId,
    messages,
    streaming,
    streamText,
    steps,
    citations,
    evalResult,
    clarify,
    annotations,
    degradeLevel,
    errorMessage,
    demoMode,
    loadSessions,
    openSession,
    newSession,
    send,
    resume,
    refreshMessages,
    feedback,
  }
})
