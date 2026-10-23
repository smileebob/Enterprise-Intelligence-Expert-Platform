"""M0 六个抽象接口（技术方案 02 §2.1–§2.3）。

上层（编排/融合/评估/Hook/记忆语义）与领域插件一律不直接触碰具体存储与模型 SDK，
只依赖这些接口；开发/演示适配器（sqlite/pgvector/文件/OpenAI 兼容）与生产适配器可整体替换。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from core.abstraction.dtos import (
    AuditEntry,
    ChatResult,
    ConflictRecord,
    EvalStandardRecord,
    KbChunkRecord,
    KbDocumentRecord,
    MemoryItemRecord,
    RetrievalQuery,
    ScoredChunk,
)


@runtime_checkable
class KnowledgeRepository(Protocol):
    """知识仓储：文档/知识块/实体/关系/冲突（分区召回与权限过滤在实现层下推）。"""

    def upsert_documents(self, docs: list[KbDocumentRecord]) -> None: ...
    def upsert_chunks(self, chunks: list[KbChunkRecord]) -> None: ...
    def get_chunk(self, chunk_id: str) -> KbChunkRecord | None: ...
    def get_document(self, doc_id: str) -> KbDocumentRecord | None: ...
    def search(self, q: RetrievalQuery) -> list[ScoredChunk]: ...
    def chunks_by_refs(self, refs: list[str], domain_id: str, permission_max: int | None = None) -> list[ScoredChunk]:
        """按 source_ref / 记录 ID 取块（用于补齐冲突双方主张的引用证据；permission_max 下推过滤）。"""
        ...
    def count_chunks(self, domain_id: str, source_type: str | None = None) -> int: ...
    def list_conflicts(self, domain_id: str, status: str | None = None) -> list[ConflictRecord]: ...
    def get_conflict(self, conflict_id: str) -> ConflictRecord | None: ...
    def upsert_conflicts(self, conflicts: list[ConflictRecord]) -> None: ...
    def resolve_conflict(self, conflict_id: str, resolution: dict[str, Any]) -> None: ...


@runtime_checkable
class MemoryRepository(Protocol):
    def upsert_item(self, item: MemoryItemRecord) -> None: ...
    def get_item(self, memory_id: str) -> MemoryItemRecord | None: ...
    def list_items(
        self,
        layer: str | None = None,
        owner_id: str | None = None,
        domain_id: str | None = None,
        status: str | None = "active",
        limit: int = 50,
    ) -> list[MemoryItemRecord]: ...
    def similar_items(
        self, owner_id: str, layer: str, embedding: list[float], threshold: float = 0.9
    ) -> list[MemoryItemRecord]: ...
    def add_event(self, memory_id: str, event_type: str, detail: dict[str, Any], actor_id: str | None) -> None: ...
    def list_events(self, memory_id: str) -> list[dict[str, Any]]: ...
    def delete_item(self, memory_id: str) -> None: ...


@runtime_checkable
class EvalRepository(Protocol):
    def upsert_standard(self, standard: EvalStandardRecord) -> None: ...
    def get_standard(self, standard_id: str) -> EvalStandardRecord | None: ...
    def get_active_standard(self, domain_id: str, object_type: str) -> EvalStandardRecord | None: ...
    def list_standards(self, domain_id: str | None = None) -> list[EvalStandardRecord]: ...
    def save_result(self, result: dict[str, Any]) -> None: ...
    def get_result(self, eval_id: str) -> dict[str, Any] | None: ...
    def list_results(self, domain_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]: ...


@runtime_checkable
class AuditRepository(Protocol):
    """审计日志（只追加，规则 03 §3.5）。"""

    def append(self, entry: AuditEntry) -> None: ...
    def query(
        self,
        trace_id: str | None = None,
        hook_type: str | None = None,
        actor_id: str | None = None,
        ts_from: str | None = None,
        ts_to: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class BlobRepository(Protocol):
    """多模态原件（本地文件卷，生产可换 MinIO/OSS）。"""

    def put(self, blob_id: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...
    def get(self, blob_id: str) -> bytes | None: ...
    def exists(self, blob_id: str) -> bool: ...


@runtime_checkable
class ModelGateway(Protocol):
    """模型网关（§2.3）：全平台唯一知道具体厂商的地方。"""

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model_alias: str = "default",
        temperature: float = 0.0,
        json_mode: bool = False,
        timeout_s: float | None = None,
    ) -> ChatResult: ...

    async def embed(self, texts: list[str], *, model_alias: str = "embed") -> list[list[float]]: ...

    async def rerank(self, query: str, docs: list[str], *, model_alias: str = "rerank") -> list[float]: ...
