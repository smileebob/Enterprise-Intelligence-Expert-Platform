"""抽象层错误类型（跨层共享）。"""

from __future__ import annotations


class LlmUnavailableError(Exception):
    """模型网关不可用（触发 L1/L2 降级）。"""


class ToolDeniedError(Exception):
    """工具权限不足或危险操作被拦截。"""

    def __init__(self, message: str, code: str = "PERM_DENIED"):
        self.code = code
        super().__init__(message)


class HookBlockedError(Exception):
    """Hook 拦截（输入注入/输出引用校验失败等）。"""

    def __init__(self, stage: str, code: str, message: str):
        self.stage = stage
        self.code = code
        self.message = message
        super().__init__(message)
