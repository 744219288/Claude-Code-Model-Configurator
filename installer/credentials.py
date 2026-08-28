"""Windows Credential Manager wrapper. Secrets never touch application logs."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import re
from typing import Iterable


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_SUCCESS = 0
ERROR_NOT_FOUND = 1168
API_KEY_TARGET = "ClaudeDeepSeekConfigurator/DeepSeekApiKey"
PROXY_TOKEN_TARGET = "ClaudeDeepSeekConfigurator/ProxyMasterKey"
PROVIDER_KEY_PREFIX = "ClaudeCNModelConfigurator/Provider"


class CredentialError(RuntimeError):
    pass


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


PCREDENTIALW = ctypes.POINTER(CREDENTIALW)
_ADVAPI32 = None


def _advapi32():
    """Load Advapi32 with thread-local GetLastError capture enabled."""
    global _ADVAPI32
    if not hasattr(ctypes, "WinDLL"):
        raise CredentialError("Windows Credential Manager 仅在 Windows 上可用")
    if _ADVAPI32 is None:
        _ADVAPI32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    return _ADVAPI32


def _last_error(operation: str) -> int:
    error = int(ctypes.get_last_error())
    if error == ERROR_SUCCESS:
        raise CredentialError(f"{operation}失败，Windows API 未返回有效错误码")
    return error


def _write_secret(target: str, value: str, username: str, comment: str) -> None:
    if not value:
        raise CredentialError("凭据内容不能为空")
    blob = value.encode("utf-16-le")
    blob_buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    credential = CREDENTIALW()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.Comment = comment
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(blob_buffer, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = username
    api = _advapi32()
    api.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    ctypes.set_last_error(ERROR_SUCCESS)
    if not api.CredWriteW(ctypes.byref(credential), 0):
        raise CredentialError(f"保存凭据失败，Windows 错误码 {_last_error('保存凭据')}")


def _read_secret(target: str) -> str | None:
    api = _advapi32()
    output = PCREDENTIALW()
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(PCREDENTIALW)]
    api.CredReadW.restype = wintypes.BOOL
    ctypes.set_last_error(ERROR_SUCCESS)
    if not api.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(output)):
        error = int(ctypes.get_last_error())
        if error == ERROR_NOT_FOUND:
            return None
        if error == ERROR_SUCCESS:
            _last_error("读取凭据")
        raise CredentialError(f"读取凭据失败，Windows 错误码 {error}")
    try:
        credential = output.contents
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return raw.decode("utf-16-le")
    finally:
        api.CredFree(output)


def _delete_secret(target: str) -> None:
    api = _advapi32()
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredDeleteW.restype = wintypes.BOOL
    ctypes.set_last_error(ERROR_SUCCESS)
    if not api.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        error = int(ctypes.get_last_error())
        # ERROR_NOT_FOUND means the requested end state is already satisfied.
        if error == ERROR_NOT_FOUND:
            return
        if error == ERROR_SUCCESS:
            _last_error("删除凭据")
        raise CredentialError(f"删除凭据失败，Windows 错误码 {error}")


def provider_api_key_target(provider_id: str) -> str:
    """Return a stable Credential Manager target for an allow-listed-style id."""
    if provider_id == "deepseek":
        # Preserve the V2.9.x target so an upgrade does not lose the saved key.
        return API_KEY_TARGET
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", provider_id):
        raise CredentialError(f"模型厂商标识无效：{provider_id}")
    return f"{PROVIDER_KEY_PREFIX}/{provider_id}/ApiKey"


def write_api_key(api_key: str, provider_id: str = "deepseek") -> None:
    if not api_key:
        raise CredentialError("API Key 不能为空")
    _write_secret(
        provider_api_key_target(provider_id), api_key, provider_id,
        f"{provider_id} API Key（由 Claude Code 国产模型配置器保存）",
    )


def read_api_key(provider_id: str = "deepseek") -> str | None:
    return _read_secret(provider_api_key_target(provider_id))


def write_proxy_token(token: str) -> None:
    _write_secret(
        PROXY_TOKEN_TARGET, token, "LiteLLM",
        "本机 LiteLLM 代理访问令牌（由 Claude Code + DeepSeek 一键配置器生成）",
    )


def read_proxy_token() -> str | None:
    return _read_secret(PROXY_TOKEN_TARGET)


def delete_saved_credentials(provider_ids: Iterable[str] = ()) -> None:
    targets = {API_KEY_TARGET}
    targets.update(provider_api_key_target(provider_id) for provider_id in provider_ids)
    for target in sorted(targets):
        _delete_secret(target)
    _delete_secret(PROXY_TOKEN_TARGET)
