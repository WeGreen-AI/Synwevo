"""跨服务共用的错误类型。

`ServiceError` 原本定义在 Synwevo Docs 的 `services.py` 里，于是身份服务为了抛一个 409 也得导入
Synwevo Docs 的业务模块。错误契约是三方共用的（HTTP 状态、机器可读的 code、给人看的 message、
可选的结构化 detail），所以它属于共享层，而不是属于第一个用到它的应用。
"""
from __future__ import annotations


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail or {}


def not_found(what: str):
    raise ServiceError(404, "not_found", f"{what}不存在或已删除")
