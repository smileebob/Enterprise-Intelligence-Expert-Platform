"""记录型模型网关（05 §5.2）：实际发送给模型的有效输入/输出在边界落受控载荷。"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core.runs.service import RunService, canonical_json


@dataclass
class RunContext:
    run_id: str = ""
    owner_id: str = ""
    domain_id: str = ""


class RecordingModelGateway:
    """委托原网关；仅在当前回合有 run_id 时记录 model.requested/returned/failed。"""

    def __init__(self, delegate: Any, db: Session, context: RunContext):
        self.delegate = delegate
        self.db = db
        self.context = context

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def _digest(self, payload: Any) -> str:
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()  # noqa: S324

    def _service(self) -> RunService:
        return RunService(self.db)

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model_alias: str = "default",
        temperature: float = 0.0,
        json_mode: bool = False,
        timeout_s: float | None = None,
    ):
        if not self.context.run_id:
            return await self.delegate.chat(
                messages,
                model_alias=model_alias,
                temperature=temperature,
                json_mode=json_mode,
                timeout_s=timeout_s,
            )
        call_id = f"{self.context.run_id}:model:chat:{uuid.uuid4().hex[:8]}"
        payload = {
            "kind": "chat",
            "messages": messages,
            "model_alias": model_alias,
            "temperature": temperature,
            "json_mode": json_mode,
        }
        service = self._service()
        service.append_event(
            self.context.run_id,
            "model.requested",
            node="model",
            call_id=call_id,
            payload=payload,
            classification="sensitive",
            metadata={
                "kind": "chat",
                "model_alias": model_alias,
                "message_count": len(messages),
                "input_digest": self._digest(payload),
                "summary": f"模型请求（{model_alias}）",
            },
        )
        try:
            result = await self.delegate.chat(
                messages,
                model_alias=model_alias,
                temperature=temperature,
                json_mode=json_mode,
                timeout_s=timeout_s,
            )
        except Exception as exc:
            service.append_event(
                self.context.run_id,
                "model.failed",
                node="model",
                call_id=call_id,
                metadata={"kind": "chat", "error_type": type(exc).__name__, "summary": "模型调用失败"},
            )
            raise
        service.append_event(
            self.context.run_id,
            "model.returned",
            node="model",
            call_id=call_id,
            payload={"kind": "chat", "content": result.content, "model": result.model},
            classification="sensitive",
            metadata={
                "kind": "chat",
                "model": result.model,
                "degrade_level": getattr(result, "degrade_level", "L0"),
                "output_digest": self._digest(result.content),
                "summary": f"模型返回（{result.model}）",
            },
        )
        return result

    async def embed(self, texts: list[str], *, model_alias: str = "embed") -> list[list[float]]:
        if not self.context.run_id:
            return await self.delegate.embed(texts, model_alias=model_alias)
        call_id = f"{self.context.run_id}:model:embed:{uuid.uuid4().hex[:8]}"
        service = self._service()
        service.append_event(
            self.context.run_id,
            "model.requested",
            node="model",
            call_id=call_id,
            payload={"kind": "embed", "texts": texts},
            classification="sensitive",
            metadata={
                "kind": "embed",
                "model_alias": model_alias,
                "count": len(texts),
                "input_digest": self._digest(texts),
                "summary": f"向量化请求（{len(texts)} 条）",
            },
        )
        try:
            vectors = await self.delegate.embed(texts, model_alias=model_alias)
        except Exception as exc:
            service.append_event(
                self.context.run_id,
                "model.failed",
                node="model",
                call_id=call_id,
                metadata={"kind": "embed", "error_type": type(exc).__name__, "summary": "向量化失败"},
            )
            raise
        service.append_event(
            self.context.run_id,
            "model.returned",
            node="model",
            call_id=call_id,
            payload={"kind": "embed", "vectors": vectors},
            classification="sensitive",
            metadata={"kind": "embed", "count": len(vectors), "summary": "向量化完成"},
        )
        return vectors

    async def rerank(self, query: str, docs: list[str], *, model_alias: str = "rerank") -> list[float]:
        if not self.context.run_id:
            return await self.delegate.rerank(query, docs, model_alias=model_alias)
        call_id = f"{self.context.run_id}:model:rerank:{uuid.uuid4().hex[:8]}"
        service = self._service()
        payload = {"kind": "rerank", "query": query, "docs": docs}
        service.append_event(
            self.context.run_id,
            "model.requested",
            node="model",
            call_id=call_id,
            payload=payload,
            classification="sensitive",
            metadata={
                "kind": "rerank",
                "model_alias": model_alias,
                "docs": len(docs),
                "input_digest": self._digest(payload),
                "summary": f"精排请求（{len(docs)} 条）",
            },
        )
        try:
            scores = await self.delegate.rerank(query, docs, model_alias=model_alias)
        except Exception as exc:
            service.append_event(
                self.context.run_id,
                "model.failed",
                node="model",
                call_id=call_id,
                metadata={"kind": "rerank", "error_type": type(exc).__name__, "summary": "精排失败"},
            )
            raise
        service.append_event(
            self.context.run_id,
            "model.returned",
            node="model",
            call_id=call_id,
            payload={"kind": "rerank", "scores": scores},
            metadata={"kind": "rerank", "count": len(scores), "summary": "精排完成"},
        )
        return scores
