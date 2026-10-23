"""受控载荷存储（05 §4.5）：每 payload 独立密钥 AES-GCM，删除密钥即 crypto-shredding。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.runs.models import RunPayload, RunPayloadSource

_NONCE_SIZE = 12
_STORAGE_KEY_RE = re.compile(r"^PL-[0-9a-f]{32}\.bin$")


class PayloadUnavailable(Exception):
    """载荷已删除/过期/密钥丢失。"""


class PayloadIntegrityError(Exception):
    """载荷摘要不匹配或文件缺失。"""


class EncryptedPayloadStore:
    def __init__(self, db: Session, root: Path, master_secret: str):
        self.db = db
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._master_key = hashlib.sha256(f"run-payload-master:{master_secret}".encode()).digest()

    def _path(self, storage_key: str) -> Path:
        if not _STORAGE_KEY_RE.match(storage_key):
            raise ValueError("非法载荷存储键")
        path = (self.root / storage_key).resolve()
        if path.parent != self.root.resolve():
            raise ValueError("载荷路径越界")
        return path

    def put(
        self,
        data: bytes,
        *,
        owner_id: str,
        domain_id: str,
        classification: str = "internal",
        sources: list[tuple[str, str]] | None = None,
        schema_version: str = "1",
        retention_days: int | None = None,
    ) -> RunPayload:
        opaque_id = f"PL-{uuid.uuid4().hex}"
        storage_key = f"{opaque_id}.bin"
        path = self._path(storage_key)
        key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(_NONCE_SIZE)
        aad = opaque_id.encode("utf-8")
        ciphertext = AESGCM(key).encrypt(nonce, data, aad)
        envelope = nonce + ciphertext
        # 先 durable 写对象，再建 DB 引用（05 §4.5）
        tmp = path.with_suffix(".tmp")
        with open(tmp, "wb") as handle:
            handle.write(envelope)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

        wrap_nonce = os.urandom(_NONCE_SIZE)
        wrapped_key = AESGCM(self._master_key).encrypt(wrap_nonce, key, aad)
        key_ref = base64.urlsafe_b64encode(wrap_nonce + wrapped_key).decode("ascii")
        expires_at = None
        if retention_days is not None:
            expires_at = datetime.now(UTC) + timedelta(days=retention_days)
        row = RunPayload(
            opaque_id=opaque_id,
            classification=classification,
            owner_id=owner_id,
            domain_id=domain_id,
            storage_key=storage_key,
            cipher_digest=hashlib.sha256(envelope).hexdigest(),
            content_digest=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            key_ref=key_ref,
            schema_version=schema_version,
            expires_at=expires_at,
            deletion_state="available",
        )
        self.db.add(row)
        for source_type, source_ref in sources or []:
            self.db.add(
                RunPayloadSource(opaque_id=opaque_id, source_type=source_type, source_ref=source_ref[:128])
            )
        self.db.flush()
        return row

    def load(self, opaque_id: str) -> bytes:
        row = self.db.get(RunPayload, opaque_id)
        if row is None or row.deletion_state != "available" or not row.key_ref:
            raise PayloadUnavailable("载荷不可用（已删除或已过期）")
        path = self._path(row.storage_key)
        if not path.exists():
            raise PayloadIntegrityError("载荷文件缺失")
        envelope = path.read_bytes()
        if hashlib.sha256(envelope).hexdigest() != row.cipher_digest:
            raise PayloadIntegrityError("载荷摘要不匹配")
        wrapped = base64.urlsafe_b64decode(row.key_ref.encode("ascii"))
        wrap_nonce, wrapped_key = wrapped[:_NONCE_SIZE], wrapped[_NONCE_SIZE:]
        aad = opaque_id.encode("utf-8")
        key = AESGCM(self._master_key).decrypt(wrap_nonce, wrapped_key, aad)
        nonce, ciphertext = envelope[:_NONCE_SIZE], envelope[_NONCE_SIZE:]
        data = AESGCM(key).decrypt(nonce, ciphertext, aad)
        if hashlib.sha256(data).hexdigest() != row.content_digest:
            raise PayloadIntegrityError("载荷内容摘要不匹配")
        return data

    def load_json(self, opaque_id: str) -> dict:
        return json.loads(self.load(opaque_id).decode("utf-8"))

    def delete(self, opaque_id: str) -> bool:
        """删除载荷：阻断读取、清理密钥与文件（审计事件由调用方保留）。"""
        row = self.db.get(RunPayload, opaque_id)
        if row is None:
            return False
        row.deletion_state = "deleted"
        row.key_ref = ""
        try:
            self._path(row.storage_key).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        self.db.flush()
        return True

    def discard_file(self, storage_key: str) -> None:
        try:
            self._path(storage_key).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    def purge_expired(self, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        rows = (
            self.db.execute(
                select(RunPayload).where(
                    RunPayload.deletion_state == "available", RunPayload.expires_at.is_not(None)
                )
            )
            .scalars()
            .all()
        )
        count = 0
        for row in rows:
            expires_at = row.expires_at
            if expires_at is None:
                continue
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= moment:
                self.delete(row.opaque_id)
                count += 1
        self.db.commit()
        return count
