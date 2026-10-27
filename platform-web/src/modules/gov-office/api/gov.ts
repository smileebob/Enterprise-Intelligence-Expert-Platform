/** 政务办公域 API：解读为只读分析；草稿创建/审核为写接口，统一携带 Idempotency-Key。 */
import { newIdempotencyKey, request } from '@/shared/api/request'
import type {
  DocumentDraft,
  DocumentDraftRequest,
  DraftCreateResult,
  DraftList,
  PolicyInterpretRequest,
  PolicyInterpretResult,
  PolicyVersionCompare,
  ReviewAction,
  WritingDraft,
  WritingDraftRequest,
} from '../types'

function writeHeaders(): Record<string, string> {
  return { 'Idempotency-Key': newIdempotencyKey() }
}

/** 政策解读（缺少地区/时间时后端返回 outcome=clarify）。 */
export function interpretPolicy(body: PolicyInterpretRequest): Promise<PolicyInterpretResult> {
  return request.post<PolicyInterpretResult>('/gov/policies/interpret', body)
}

/** 公文起草：模板装配 + 事实核对，返回待审草稿。 */
export function createDocumentDraft(
  body: DocumentDraftRequest,
): Promise<DraftCreateResult<DocumentDraft>> {
  return request.post<DraftCreateResult<DocumentDraft>>('/gov/documents/drafts', body, {
    headers: writeHeaders(),
  })
}

export function comparePolicyVersions(
  docId: string,
  left: string,
  right: string,
): Promise<PolicyVersionCompare> {
  return request.get<PolicyVersionCompare>(
    `/gov/policies/${encodeURIComponent(docId)}/versions/${encodeURIComponent(left)}/compare`,
    { params: { other: right } },
  )
}

export function listDocumentDrafts(status = 'pending_review'): Promise<DraftList<DocumentDraft>> {
  return request.get<DraftList<DocumentDraft>>('/gov/documents/drafts', {
    params: { status, page: 1, page_size: 50 },
  })
}

export function listWritingDrafts(status = 'pending_review'): Promise<DraftList<WritingDraft>> {
  return request.get<DraftList<WritingDraft>>('/gov/writing/drafts', {
    params: { status, page: 1, page_size: 50 },
  })
}

/** 下载审核通过公文的 ZIP（DOCX 清洁稿 + 证据 JSON）。 */
export async function downloadDocumentExport(draftId: string): Promise<void> {
  const response = await fetch(`/api/v1/gov/documents/drafts/${draftId}/export`, {
    headers: { Authorization: `Bearer ${localStorage.getItem('token') || ''}` },
  })
  if (!response.ok) {
    let message = `导出失败（${response.status}）`
    try {
      const payload = (await response.json()) as { message?: string }
      if (payload.message) message = payload.message
    } catch {
      /* keep default */
    }
    throw new Error(message)
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `gov-${draftId}.zip`
  anchor.click()
  URL.revokeObjectURL(url)
}

/** 公文草稿审核（approve/reject），返回审核后的草稿视图。 */
export function reviewDocumentDraft(
  draftId: string,
  action: ReviewAction,
  note: string,
): Promise<DocumentDraft> {
  return request.post<DocumentDraft>(
    `/gov/documents/drafts/${draftId}/review`,
    { action, note },
    { headers: writeHeaders() },
  )
}

/** 文稿撰写 / 总结提炼 / 咨询答复拟稿（抽取式，带来源映射）。 */
export function createWritingDraft(
  body: WritingDraftRequest,
): Promise<DraftCreateResult<WritingDraft>> {
  return request.post<DraftCreateResult<WritingDraft>>('/gov/writing/drafts', body, {
    headers: writeHeaders(),
  })
}

/** 文稿草稿审核（approve/reject），返回审核后的草稿视图。 */
export function reviewWritingDraft(
  draftId: string,
  action: ReviewAction,
  note: string,
): Promise<WritingDraft> {
  return request.post<WritingDraft>(
    `/gov/writing/drafts/${draftId}/review`,
    { action, note },
    { headers: writeHeaders() },
  )
}
