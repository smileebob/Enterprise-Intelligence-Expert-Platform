"""密码哈希（argon2，I-06）——底座安全工具：core 与 app 共用，避免 core 反向依赖 app。"""

from __future__ import annotations

import logging

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

logger = logging.getLogger(__name__)

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """校验口令：期望内的校验失败按 False；未知异常记录后按失败处理（不得静默吞掉）。"""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False
    except Exception:  # noqa: BLE001 - argon2 内部异常不得中断认证流程，但必须留痕
        logger.exception("password verify raised unexpected error")
        return False
