"""多模态原件仓储 · 本地文件卷（默认形态；生产可换 MinIO/OSS 适配器）。"""

from __future__ import annotations

import uuid
from pathlib import Path


class LocalBlobRepository:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, blob_id: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        safe = blob_id.replace("..", "_").replace("/", "_").replace("\\", "_")
        path = self.root / f"{safe}_{uuid.uuid4().hex[:8]}"
        path.write_bytes(data)
        return str(path)

    def get(self, blob_id: str) -> bytes | None:
        path = Path(blob_id)
        if not path.is_absolute():
            path = self.root / blob_id
        return path.read_bytes() if path.exists() else None

    def exists(self, blob_id: str) -> bool:
        path = Path(blob_id)
        if not path.is_absolute():
            path = self.root / blob_id
        return path.exists()
