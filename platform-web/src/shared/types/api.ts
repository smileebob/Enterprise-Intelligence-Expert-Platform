/** 全局类型（与后端统一响应契约对齐）。 */

export interface ApiResponse<T> {
  code: string
  message: string
  data: T
}

export interface UserInfo {
  user_id: string
  username: string
  name: string
  role_id: string
  org?: string
  domain_scope: string[]
}

export interface LoginResult {
  access_token: string
  token_type: string
  expires_in: number
  user: UserInfo
}

export interface MeInfo extends UserInfo {
  permission_max: number
  permissions: string[]
}

export interface SessionItem {
  session_id: string
  domain_id: string
  title: string
  status: string
  updated_at: string | null
}

export interface Citation {
  citation_id: string
  chunk_id: string
  record_id: string
  source_type: string
  source_ref: string
  confidence: number | null
  category?: string | null
}

export interface ConflictSummary {
  conflict_id: string
  title: string
  internal: string
  external: string
  must_note: string
  status: string
}

export interface EvalDimension {
  code: string
  name: string
  score: number
  weight: number
  evidence_refs: string[]
  deductions: Array<{ points: number; reason: string }>
}

export interface EvalResult {
  eval_id: string
  scenario: string
  standard_version?: string
  total_score: number
  confidence: number
  dimensions: Record<string, EvalDimension>
  weights?: Record<string, number>
  risk_flags: Array<{ reason: string; points: number }>
  compliance_flags: Array<Record<string, unknown>>
  guardrails: string[]
  calibration?: { applied: boolean; items?: unknown[] }
  scored_by?: string
  degrade_level?: string
  confirmed_by?: string | null
}

export interface ChatMessageItem {
  message_id: string
  role: string
  content: string
  citations: Citation[]
  conflicts: ConflictSummary[]
  eval_ref: string | null
  degrade_level: string
  feedback: string | null
  trace_id: string | null
  meta: Record<string, unknown>
  created_at: string | null
}

export type SseEvent =
  | { event: 'step'; data: { node: string; status: string; summary: string } }
  | { event: 'citation'; data: Citation }
  | { event: 'token'; data: { text: string } }
  | { event: 'eval'; data: { total_score: number; scenario: string; confidence: number; compliance_flags?: unknown[] } }
  | { event: 'clarify'; data: { question: string; missing_slots: string[] } }
  | { event: 'error'; data: { code: string; message: string } }
  | { event: 'done'; data: { code: string; data: { message_id: string; degrade_level: string; annotations?: string[]; interrupted?: boolean } } }
