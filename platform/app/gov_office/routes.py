"""政务政策知识接口：导入、审核、发布、适用版本、检索和版本对照。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.common.errors import biz
from app.common.responses import ok
from app.db import get_db
from app.deps import assert_domain_access, idempotency_key, require_permissions
from app.gov_office.conversations import GovInterpretConversationService
from app.gov_office.documents import GovDocumentService
from app.gov_office.exporting import GovDocumentExportService
from app.gov_office.schemas import (
    ApplicableQueryIn,
    DocumentDraftIn,
    DocumentDraftReviewIn,
    DocumentDraftUpdateIn,
    InterpretSessionCreateIn,
    InterpretSessionTurnIn,
    PolicyClauseUpdateIn,
    PolicyImportIn,
    PolicyInterpretIn,
    PolicyReviewIn,
    PolicySearchIn,
    PolicySupportIn,
    WritingDraftIn,
    WritingDraftUpdateIn,
)
from app.gov_office.services import GovPolicyService
from app.gov_office.writing import GovWritingService
from core.abstraction.dtos import Actor
from core.gateway.permissions import role_permissions

router = APIRouter(tags=["gov-office"])
DOMAIN_ID = "gov_office"


def _published_only(db: Session, actor: Actor) -> bool:
    return "knowledge.write" not in role_permissions(
        db, actor.role_id, fresh=True
    )


def _required_idempotency(
    value: str | None = Depends(idempotency_key),
) -> str:
    if not value:
        raise biz("IDEMPOTENCY_REQUIRED", "写操作必须携带 Idempotency-Key", 400)
    return value


@router.post(
    "/gov/policies/import",
    status_code=201,
    summary="导入政策文件、版本与条款（幂等）",
)
def import_policy(
    body: PolicyImportIn,
    actor: Actor = Depends(require_permissions("knowledge.write")),
    db: Session = Depends(get_db),
    idem: str = Depends(_required_idempotency),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(GovPolicyService(db).import_policy(body, actor, idem))


@router.get("/gov/policies", summary="政策文件分页列表")
def list_policies(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    jurisdiction: str | None = None,
    knowledge_status: str | None = None,
    actor: Actor = Depends(require_permissions("knowledge.read")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    result = GovPolicyService(db).list_documents(
        page=page,
        page_size=page_size,
        jurisdiction=jurisdiction,
        knowledge_status=knowledge_status,
        published_only=_published_only(db, actor),
    )
    return ok(result)


@router.post("/gov/policies/applicable", summary="按地区与事项日期解析适用版本")
def applicable_versions(
    body: ApplicableQueryIn,
    actor: Actor = Depends(require_permissions("knowledge.read")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(GovPolicyService(db).applicable_versions(body, actor.permission_max))


@router.post("/gov/policies/search", summary="有效政策条款混合检索")
async def search_policies(
    body: PolicySearchIn,
    actor: Actor = Depends(require_permissions("retrieval.search")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(await GovPolicyService(db).search(body, actor.permission_max))


@router.post(
    "/gov/interpret/sessions",
    status_code=201,
    summary="创建政策解读会话（缺少条件时保存澄清状态）",
)
async def create_interpret_session(
    body: InterpretSessionCreateIn,
    actor: Actor = Depends(require_permissions("chat.use")),
    db: Session = Depends(get_db),
    idem: str = Depends(_required_idempotency),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(
        await GovInterpretConversationService(db).create(body, actor, idem)
    )


@router.get(
    "/gov/interpret/sessions/{session_id}",
    summary="查看政策解读会话与轮次",
)
def get_interpret_session(
    session_id: str,
    actor: Actor = Depends(require_permissions("chat.use")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(GovInterpretConversationService(db).get(session_id, actor))


@router.post(
    "/gov/interpret/sessions/{session_id}/messages",
    summary="补充政策解读条件或提出后续问题",
)
async def update_interpret_session(
    session_id: str,
    body: InterpretSessionTurnIn,
    actor: Actor = Depends(require_permissions("chat.use")),
    db: Session = Depends(get_db),
    idem: str = Depends(_required_idempotency),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(
        await GovInterpretConversationService(db).turn(
            session_id, body, actor, idem
        )
    )


@router.post("/gov/policies/support-check", summary="检查论断是否受引用条款支持")
async def check_policy_support(
    body: PolicySupportIn,
    actor: Actor = Depends(require_permissions("retrieval.search")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(await GovPolicyService(db).check_support(body, actor.permission_max))


@router.post("/gov/policies/interpret", summary="政策解读（缺少条件先澄清）")
async def interpret_policy(
    body: PolicyInterpretIn,
    actor: Actor = Depends(require_permissions("chat.use")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(await GovPolicyService(db).interpret(body, actor.permission_max))


@router.post(
    "/gov/policies/relations/{relation_id}/review",
    summary="人工审核政策版本关系",
)
def review_policy_relation(
    relation_id: str,
    body: PolicyReviewIn,
    actor: Actor = Depends(require_permissions("knowledge.write")),
    db: Session = Depends(get_db),
    _: str = Depends(_required_idempotency),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(GovPolicyService(db).review_relation(relation_id, body, actor))


@router.get("/gov/policies/{doc_id}", summary="政策文件、版本与关系")
def get_policy(
    doc_id: str,
    actor: Actor = Depends(require_permissions("knowledge.read")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(
        GovPolicyService(db).get_document(
            doc_id, published_only=_published_only(db, actor)
        )
    )


@router.post(
    "/gov/policies/{doc_id}/versions/{version_label}/embeddings",
    summary="为已审核政策版本构建检索向量",
)
async def build_policy_embeddings(
    doc_id: str,
    version_label: str,
    actor: Actor = Depends(require_permissions("knowledge.write")),
    db: Session = Depends(get_db),
    _: str = Depends(_required_idempotency),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(
        await GovPolicyService(db).build_embeddings(doc_id, version_label, actor)
    )


@router.get(
    "/gov/policies/{doc_id}/versions/{version_label}/compare",
    summary="两个政策版本的条款差异",
)
def compare_policy_versions(
    doc_id: str,
    version_label: str,
    other: str = Query(min_length=1, max_length=96),
    actor: Actor = Depends(require_permissions("knowledge.read")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(
        GovPolicyService(db).compare_versions(
            doc_id,
            version_label,
            other,
            published_only=_published_only(db, actor),
        )
    )


@router.put(
    "/gov/policies/{doc_id}/versions/{version_label}/clauses/{clause_no}",
    summary="新增或校正草稿政策条款（带 revision 防止覆盖）",
)
def update_policy_clause(
    doc_id: str,
    version_label: str,
    clause_no: str,
    body: PolicyClauseUpdateIn,
    actor: Actor = Depends(require_permissions("knowledge.write")),
    db: Session = Depends(get_db),
    _: str = Depends(_required_idempotency),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(
        GovPolicyService(db).update_clause(
            doc_id, version_label, clause_no, body, actor
        )
    )


@router.get(
    "/gov/policies/{doc_id}/versions/{version_label}",
    summary="政策版本与条款",
)
def get_policy_version(
    doc_id: str,
    version_label: str,
    actor: Actor = Depends(require_permissions("knowledge.read")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(
        GovPolicyService(db).get_version(
            doc_id, version_label, published_only=_published_only(db, actor)
        )
    )


@router.post(
    "/gov/policies/{doc_id}/versions/{version_label}/review",
    summary="人工审核政策版本",
)
def review_policy_version(
    doc_id: str,
    version_label: str,
    body: PolicyReviewIn,
    actor: Actor = Depends(require_permissions("knowledge.write")),
    db: Session = Depends(get_db),
    _: str = Depends(_required_idempotency),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(
        GovPolicyService(db).review_version(doc_id, version_label, body, actor)
    )


@router.post("/gov/policies/{doc_id}/publish", summary="发布政策文件")
def publish_policy(
    doc_id: str,
    actor: Actor = Depends(require_permissions("knowledge.write")),
    db: Session = Depends(get_db),
    _: str = Depends(_required_idempotency),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(GovPolicyService(db).publish_document(doc_id, actor))


@router.post(
    "/gov/writing/drafts",
    status_code=201,
    summary="形成文稿、总结或咨询草稿（抽取式，带来源映射）",
)
async def create_writing_draft(
    body: WritingDraftIn,
    actor: Actor = Depends(require_permissions("chat.use")),
    db: Session = Depends(get_db),
    idem: str = Depends(_required_idempotency),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(await GovWritingService(db).create(body, actor, idem))


@router.get("/gov/writing/drafts", summary="文稿草稿列表（本人或审核人）")
def list_writing_drafts(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: str | None = None,
    actor: Actor = Depends(require_permissions("knowledge.read")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(
        GovWritingService(db).list_drafts(
            actor, page=page, page_size=page_size, status=status
        )
    )


@router.get("/gov/writing/drafts/{draft_id}", summary="查看文稿草稿")
def get_writing_draft(
    draft_id: str,
    actor: Actor = Depends(require_permissions("knowledge.read")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(GovWritingService(db).get(draft_id, actor))


@router.put("/gov/writing/drafts/{draft_id}", summary="修改文稿草稿")
async def update_writing_draft(
    draft_id: str,
    body: WritingDraftUpdateIn,
    actor: Actor = Depends(require_permissions("chat.use")),
    db: Session = Depends(get_db),
    _: str = Depends(_required_idempotency),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(
        await GovWritingService(db).update(draft_id, body, actor, _)
    )


@router.post("/gov/writing/drafts/{draft_id}/review", summary="审核文稿草稿")
def review_writing_draft(
    draft_id: str,
    body: DocumentDraftReviewIn,
    actor: Actor = Depends(require_permissions("output.review")),
    db: Session = Depends(get_db),
    _: str = Depends(_required_idempotency),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(GovWritingService(db).review(draft_id, body, actor))


@router.post(
    "/gov/documents/drafts",
    status_code=201,
    summary="形成结构化公文草稿并执行模板与事实核对",
)
def create_document_draft(
    body: DocumentDraftIn,
    actor: Actor = Depends(require_permissions("chat.use")),
    db: Session = Depends(get_db),
    idem: str = Depends(_required_idempotency),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(GovDocumentService(db).create(body, actor, idem))


@router.get("/gov/documents/drafts", summary="公文草稿列表（本人或审核人）")
def list_document_drafts(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: str | None = None,
    actor: Actor = Depends(require_permissions("knowledge.read")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(
        GovDocumentService(db).list_drafts(
            actor, page=page, page_size=page_size, status=status
        )
    )


@router.get("/gov/documents/drafts/{draft_id}", summary="查看公文草稿")
def get_document_draft(
    draft_id: str,
    actor: Actor = Depends(require_permissions("knowledge.read")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(GovDocumentService(db).get(draft_id, actor))


@router.put("/gov/documents/drafts/{draft_id}", summary="修改公文草稿")
def update_document_draft(
    draft_id: str,
    body: DocumentDraftUpdateIn,
    actor: Actor = Depends(require_permissions("chat.use")),
    db: Session = Depends(get_db),
    _: str = Depends(_required_idempotency),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(
        GovDocumentService(db).update(draft_id, body, actor, _)
    )


@router.get(
    "/gov/documents/drafts/{draft_id}/export",
    summary="导出审核通过公文（DOCX 清洁稿 + JSON 证据包）",
)
def export_document_draft(
    draft_id: str,
    actor: Actor = Depends(require_permissions("knowledge.read")),
    db: Session = Depends(get_db),
):
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    content, digest = GovDocumentExportService(db).export(draft_id, actor)
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="gov-{draft_id}.zip"',
            "X-DOCX-SHA256": digest,
        },
    )


@router.post("/gov/documents/drafts/{draft_id}/review", summary="审核公文草稿")
def review_document_draft(
    draft_id: str,
    body: DocumentDraftReviewIn,
    actor: Actor = Depends(require_permissions("output.review")),
    db: Session = Depends(get_db),
    _: str = Depends(_required_idempotency),
) -> dict:
    assert_domain_access(actor, DOMAIN_ID, db=db, require_online=False)
    return ok(GovDocumentService(db).review(draft_id, body, actor))
