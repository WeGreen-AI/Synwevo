"""Stored-byte safety shared by Suite applications, after their own resource authorization."""
from synwevo_core.errors import ServiceError
from synwevo_core.quarantine import standing


def available_blob(store, digest):
    # A standing verdict wins even if a backup has restored a readable copy of the bytes.
    if digest in standing(store):
        raise ServiceError(409, "file_quarantined", "文件已被隔离，当前不能读取或处理")


def read_blob(store, digest):
    available_blob(store, digest)
    try:
        data = store.get_blob(digest)
    except FileNotFoundError:
        raise ServiceError(409, "file_unavailable", "文件当前不可用，请联系管理员核实存储状态") from None
    available_blob(store, digest)
    return data
