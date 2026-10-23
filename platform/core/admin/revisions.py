"""领域配置修订的稳定标识与摘要（发布/回滚/CAS 共用）。"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(config: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()  # noqa: S324 - 非加密用途


def new_revision_id() -> str:
    return f"REV-{uuid.uuid4().hex[:16]}"


def new_operation_id() -> str:
    return f"OP-{uuid.uuid4().hex[:16]}"
