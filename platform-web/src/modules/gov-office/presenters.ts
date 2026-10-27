/** 政务工作台展示映射：文种/模式/状态/严重度的中文标签与颜色。 */
import type { CheckSeverity, DraftStatus, GovDocType, SupportStatus, WritingMode } from './types'

export type TagType = 'success' | 'warning' | 'danger' | 'info'

export const DOC_TYPE_OPTIONS: Array<{ value: GovDocType; label: string }> = [
  { value: 'notice', label: '通知' },
  { value: 'request', label: '请示' },
  { value: 'report', label: '报告' },
  { value: 'letter', label: '函' },
  { value: 'minutes', label: '纪要' },
]

export const WRITING_MODE_OPTIONS: Array<{ value: WritingMode; label: string; description: string }> = [
  { value: 'content_writing', label: '文稿撰写', description: '按提纲组织材料，逐段标注来源' },
  { value: 'summarization', label: '总结提炼', description: '抽取材料要点、任务与覆盖情况' },
  { value: 'consultation_draft', label: '咨询答复拟稿', description: '公众答复与内部依据同源，发布前须人工审核' },
]

export function docTypeLabel(docType: string): string {
  return DOC_TYPE_OPTIONS.find((item) => item.value === docType)?.label || docType
}

const DRAFT_STATUS_LABELS: Record<string, string> = {
  needs_revision: '待修改',
  pending_review: '待审核',
  approved: '已通过',
  rejected: '已退回',
}

export function draftStatusLabel(status: DraftStatus | string): string {
  return DRAFT_STATUS_LABELS[status] || status
}

export function draftStatusType(status: DraftStatus | string): TagType {
  if (status === 'approved') return 'success'
  if (status === 'pending_review') return 'warning'
  if (status === 'rejected' || status === 'needs_revision') return 'danger'
  return 'info'
}

export function draftStatusDescription(status: DraftStatus | string): string {
  if (status === 'pending_review') return '草稿待人工审核，未签发、未发布'
  if (status === 'approved') return '已通过人工审核，仍需线下签发流程，系统不自动发布'
  if (status === 'rejected') return '已被退回，请按审核意见修改后重新提交'
  if (status === 'needs_revision') return '存在阻断性审校项，需修改后重新提交'
  return ''
}

const SUPPORT_LABELS: Record<string, string> = {
  direct: '直接支持',
  partial: '部分支持',
  insufficient: '证据不足',
}

export function supportStatusLabel(status: SupportStatus | string): string {
  return SUPPORT_LABELS[status] || status
}

export function supportStatusType(status: SupportStatus | string): TagType {
  if (status === 'direct') return 'success'
  if (status === 'partial') return 'warning'
  if (status === 'insufficient') return 'danger'
  return 'info'
}

export function severityType(severity: CheckSeverity | string): TagType {
  if (severity === 'error') return 'danger'
  if (severity === 'warning') return 'warning'
  return 'info'
}

export function severityLabel(severity: CheckSeverity | string): string {
  if (severity === 'error') return '阻断'
  if (severity === 'warning') return '提示'
  return '说明'
}

export function interpretOutcomeLabel(outcome: string): string {
  if (outcome === 'answer') return '已生成条款摘录'
  if (outcome === 'clarify') return '需补充条件'
  if (outcome === 'insufficient') return '证据不足'
  return outcome
}

export function interpretOutcomeType(outcome: string): TagType {
  if (outcome === 'answer') return 'success'
  if (outcome === 'clarify') return 'warning'
  if (outcome === 'insufficient') return 'danger'
  return 'info'
}

export function coveragePercent(ratio: number | undefined): number {
  if (typeof ratio !== 'number' || Number.isNaN(ratio)) return 0
  return Math.round(Math.min(1, Math.max(0, ratio)) * 100)
}

/** 统一取可展示的错误文案（拦截器已弹出全局提示，这里用于面板内留存）。 */
export function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) return error.message
  return fallback
}

/** 逗号/空白分隔的政策引用 ID 解析。 */
export function parseCitationIds(text: string): string[] {
  return text
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}
