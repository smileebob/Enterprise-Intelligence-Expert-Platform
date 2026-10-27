/** 政务办公工作台类型（与后端 /api/v1/gov/* 契约对齐，全部显式声明，无 any）。 */

/** 政策解读结果类型：澄清 / 已生成摘录 / 证据不足。 */
export type InterpretOutcome = 'clarify' | 'answer' | 'insufficient'

/** 论断支持状态（后端 _assess_claim_support 输出）。 */
export type SupportStatus = 'direct' | 'partial' | 'insufficient'

export interface Clarification {
  field: string
  question: string
}

export interface PolicyClaim {
  claim_id: string
  claim_type?: string
  text: string
  citation_ids: string[]
  support_status: SupportStatus
  support_method?: string
  coverage?: number
  negation_risk?: boolean
  matched_terms?: string[]
}

export interface PolicyCitation {
  citation_id: string
  doc_id: string
  title: string
  document_number: string
  version_id: string
  version_label: string
  effective_from?: string | null
  effective_to?: string | null
  source_url: string
}

export interface PolicyConflict {
  conflict_type: string
  message: string
  doc_ids: string[]
}

export interface PolicyInterpretRequest {
  question: string
  jurisdiction?: string
  subject?: string
  as_of?: string
}

export interface PolicyInterpretResult {
  outcome: InterpretOutcome
  question?: string
  as_of?: string
  summary?: string
  clarifications?: Clarification[]
  claims: PolicyClaim[]
  citations: PolicyCitation[]
  conflicts: PolicyConflict[]
  warnings: string[]
  manual_review_required?: boolean
  unverified_claim_ids?: string[]
  retrieval?: Record<string, unknown>
}

/** 公文文种（后端 doc_type 可选值）。 */
export type GovDocType = 'notice' | 'request' | 'report' | 'letter' | 'minutes'

/** 草稿状态机。 */
export type DraftStatus = 'needs_revision' | 'pending_review' | 'approved' | 'rejected'

export type ReviewAction = 'approve' | 'reject'

export interface DraftFact {
  text: string
  source_ref: string
}

export interface DocSection {
  heading: string
  content: string
}

export type CheckSeverity = 'error' | 'warning' | 'info'

export interface DraftCheck {
  code: string
  severity: CheckSeverity
  message: string
  location: string
}

export interface DraftReviewInfo {
  reviewed_by?: string | null
  reviewed_at?: string | null
  note?: string
}

export interface DocumentDraft {
  draft_id: string
  owner_id?: string
  doc_type: GovDocType
  title: string
  recipient: string
  issuing_authority: string
  document_date: string
  purpose: string
  sections: DocSection[]
  facts: DraftFact[]
  policy_citations: string[]
  checks: DraftCheck[]
  template: { template_id: string; version: string }
  status: DraftStatus
  version: number
  review?: DraftReviewInfo
  updated_at?: string | null
}

export interface DocumentDraftRequest {
  doc_type: GovDocType
  title: string
  recipient: string
  issuing_authority: string
  document_date: string
  purpose: string
  facts: DraftFact[]
  sections?: DocSection[]
  policy_citations: string[]
}

/** 文稿/总结/咨询模式。 */
export type WritingMode = 'content_writing' | 'summarization' | 'consultation_draft'

export interface WritingSection {
  heading: string
  content: string
  source_refs?: string[]
  warning?: string
}

export interface WritingKeyPoint {
  point_id: string
  unit_id: string
  text: string
  source_ref: string
}

export interface WritingTask {
  task_id: string
  text: string
  source_ref: string
  unit_id: string
}

export interface WritingCoverage {
  source_units?: number
  represented_units?: number
  source_materials?: number
  used_materials?: number
  ratio: number
}

/** 咨询答复的分类条目（条件/材料/渠道/时限），均带条款证据。 */
export interface ConsultationItem {
  text: string
  citation_ids: string[]
}

/** 结果中的政策引用校验信息。 */
export interface PolicyCitationRef {
  citation_id: string
  status?: string
  doc_id?: string
  title?: string
  version_id?: string
  clause_no?: string
}

export interface WritingResult {
  summary?: string
  key_points?: WritingKeyPoint[]
  tasks?: WritingTask[]
  sections?: WritingSection[]
  coverage?: WritingCoverage
  public_reply?: string
  internal_basis?: PolicyCitation[]
  conditions?: ConsultationItem[]
  required_materials?: ConsultationItem[]
  channels?: ConsultationItem[]
  time_limits?: ConsultationItem[]
  outline_suggestions?: Array<{ heading: string }>
  generation_mode?: string
  publish_status?: string
  manual_review_required?: boolean
  interpretation?: PolicyInterpretResult
  warnings?: string[]
  policy_citations?: PolicyCitationRef[]
  high_risk_markers?: string[]
  materials?: DraftFact[]
}

export interface WritingDraft {
  draft_id: string
  owner_id?: string
  mode: WritingMode
  request: string
  audience: string
  jurisdiction: string
  subject: string
  as_of?: string | null
  materials: DraftFact[]
  outline: Array<{ heading: string }>
  policy_citations: string[]
  result: WritingResult
  checks: DraftCheck[]
  status: DraftStatus
  version: number
  review?: DraftReviewInfo
  updated_at?: string | null
}

export interface WritingDraftRequest {
  mode: WritingMode
  request: string
  audience: string
  jurisdiction: string
  subject: string
  as_of: string | null
  materials: DraftFact[]
  outline: Array<{ heading: string }>
  policy_citations: string[]
}

/** 创建类接口统一返回 { draft, duplicated }（幂等命中时 duplicated=true）。 */
export interface DraftCreateResult<T> {
  draft: T
  duplicated: boolean
}

export interface DraftList<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface PolicyVersionSummary {
  version_id: string
  doc_id: string
  version_label: string
  document_number: string
  review_status: string
  effective_from?: string | null
  effective_to?: string | null
}

export interface PolicyVersionCompare {
  doc_id: string
  left: PolicyVersionSummary
  right: PolicyVersionSummary
  summary: { added: number; removed: number; changed: number; unchanged: number }
  added: string[]
  removed: string[]
  changed: Array<{ clause_no: string; left: string; right: string }>
}
