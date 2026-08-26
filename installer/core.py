"""Installation, configuration and proxy lifecycle logic."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import concurrent.futures
import hashlib
import json
import os
import platform
import queue
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .credentials import (
    delete_saved_credentials, read_api_key, read_proxy_token,
    write_api_key, write_proxy_token,
)


APP_NAME = "ClaudeDeepSeekConfigurator"
APP_VERSION = "2.9.3"
PYTHON_VERSION = "3.12.10"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-amd64.exe"
LITELLM_VERSION = "1.80.11"
PYPI_OFFICIAL = "https://pypi.org/simple"
PYPI_TUNA = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
VSCODE_EXTENSION = "anthropic.claude-code"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_ANTHROPIC_URL = "https://api.deepseek.com/anthropic"
PROXY_URL = "http://127.0.0.1:4000"
OFFLINE_MANIFEST = "manifest.json"
OFFLINE_LOCK = "requirements.lock"
RELEASE_INTEGRITY = "release-integrity.json"
NODE_VERSION = "22.23.2"
NODE_ARCHIVE = f"node-v{NODE_VERSION}-win-x64.zip"
GIT_VERSION = "2.55.0.5"
GIT_ARCHIVE = f"PortableGit-{GIT_VERSION}-64-bit.7z.exe"
SUPPORTED_MODELS = {"deepseek-v4-flash", "deepseek-v4-pro[1m]"}
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_REASONING_MODEL = "deepseek-v4-pro[1m]"
# DeepSeek V4 系列（flash 与 pro）官方上下文窗口均为 1M tokens。
# Claude Code 不认识这些自定义模型名时会保守假定 200k 窗口并做自动压缩，
# 因此显式告诉 claude 真实窗口，避免上下文能力被误判。参见 stderr 警告中的
# CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT / CLAUDE_CODE_MAX_CONTEXT_TOKENS。
DEEPSEEK_CONTEXT_TOKENS = 1_000_000
GATEWAY_ENV_NAMES = (
    "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_EFFORT_LEVEL",
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
)
NPM_PACKAGE = "@anthropic-ai/claude-code"
NPM_REGISTRIES = {
    "npm_mirror": "https://registry.npmmirror.com",
    "npm_official": "https://registry.npmjs.org",
}
NETWORK_TARGETS = {
    "native": "https://claude.ai/install.cmd",
    "npm_mirror": "https://registry.npmmirror.com/@anthropic-ai%2Fclaude-code",
    "npm_official": "https://registry.npmjs.org/@anthropic-ai%2Fclaude-code",
    "deepseek": DEEPSEEK_BASE_URL,
}
UPDATE_MANIFEST_ENV = "CLAUDE_DEEPSEEK_UPDATE_MANIFEST_URL"


class InstallError(RuntimeError):
    pass


class InstallSourceError(InstallError):
    """All Claude Code installation paths failed and the UI should offer choices."""

    def __init__(self, failures: list[dict[str, str]], available: Iterable[str] = ()) -> None:
        self.failures = failures
        self.available = tuple(dict.fromkeys(available))
        summary = "；".join(
            f"{item.get('label', item.get('source', '安装源'))}：{item.get('message', '未完成')}"
            for item in failures[-4:]
        ) or "没有可用安装方式"
        super().__init__(f"Claude Code 自动安装未完成：{summary}")


@dataclass(frozen=True)
class InstallOptions:
    claude_strategy: str = "auto"
    install_vscode: bool = False
    proxy_url: str = ""
    allow_managed_node: bool = True


@dataclass(frozen=True)
class Paths:
    root: Path
    runtime: Path
    venv: Path
    config: Path
    state: Path
    log: Path
    pid: Path

    @classmethod
    def default(cls) -> "Paths":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
        return cls(base, base / "python312", base / "venv", base / "litellm_config.yaml", base / "state.json", base / "proxy.log", base / "proxy.pid")


Progress = Callable[[str, str], None]


def application_dir() -> Path:
    """Folder containing the portable EXE, or the source project while developing."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def program_root(paths: Paths | None = None) -> Path:
    """Stable immutable application payload; separate from mutable state/runtime."""
    if paths is not None:
        local = paths.root.parent
    else:
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local / "Programs" / APP_NAME


def offline_root(paths: Paths | None = None) -> Path:
    """Prefer the installed payload so desktop/recovery runs never depend on the ZIP."""
    override = os.environ.get("CLAUDE_DEEPSEEK_OFFLINE_ROOT", "").strip()
    candidates = [
        Path(override) if override else None,
        program_root(paths) / "offline",
        application_dir() / "offline",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.joinpath(OFFLINE_MANIFEST).is_file():
            return candidate
    # Return the adjacent path for actionable missing-payload diagnostics.
    return application_dir() / "offline"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_offline_manifest(root: Path | None = None) -> dict:
    manifest_path = (root or offline_root()) / OFFLINE_MANIFEST
    try:
        # Windows PowerShell 5.1's `-Encoding UTF8` writes a BOM. Accept both
        # BOM and BOM-less manifests so older/offline builds remain usable.
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and isinstance(data.get("files"), dict) else {}


def verify_offline_asset(path: Path, root: Path | None = None) -> tuple[bool, str]:
    """Verify a bundled file against the build-time manifest before executing/using it."""
    asset_root = (root or offline_root()).resolve()
    try:
        relative = path.resolve().relative_to(asset_root).as_posix()
    except (OSError, ValueError):
        return False, "文件不在离线组件目录内"
    entry = load_offline_manifest(asset_root).get("files", {}).get(relative)
    if not isinstance(entry, dict) or not entry.get("sha256"):
        return False, "清单中没有该文件"
    try:
        if int(entry.get("size", -1)) != path.stat().st_size:
            return False, "文件大小不一致"
        if _sha256(path).lower() != str(entry["sha256"]).lower():
            return False, "SHA256 校验不一致"
    except OSError as exc:
        return False, f"无法读取文件：{exc}"
    return True, "校验通过"


def bundled_python_installer(root: Path | None = None) -> Path | None:
    asset_root = root or offline_root()
    candidate = asset_root / "python" / f"python-{PYTHON_VERSION}-amd64.exe"
    healthy, _ = verify_offline_asset(candidate, asset_root)
    return candidate if healthy else None


def bundled_wheelhouse(root: Path | None = None) -> Path | None:
    asset_root = root or offline_root()
    wheels = asset_root / "wheels"
    if not wheels.is_dir():
        return None
    candidates = list(wheels.glob(f"litellm-{LITELLM_VERSION}-*.whl"))
    if not candidates:
        return None
    for wheel in wheels.glob("*.whl"):
        if not verify_offline_asset(wheel, asset_root)[0]:
            return None
    return wheels


def bundled_lockfile(root: Path | None = None) -> Path | None:
    asset_root = root or offline_root()
    lockfile = asset_root / OFFLINE_LOCK
    if not lockfile.is_file() or not verify_offline_asset(lockfile, asset_root)[0]:
        return None
    try:
        content = lockfile.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    return lockfile if "--hash=sha256:" in content else None


def bundled_node_archive(root: Path | None = None) -> Path | None:
    asset_root = root or offline_root()
    candidate = asset_root / "node" / NODE_ARCHIVE
    healthy, _ = verify_offline_asset(candidate, asset_root)
    return candidate if healthy else None


def bundled_git_archive(root: Path | None = None) -> Path | None:
    asset_root = root or offline_root()
    candidate = asset_root / "git" / GIT_ARCHIVE
    healthy, _ = verify_offline_asset(candidate, asset_root)
    return candidate if healthy else None


def verify_offline_payload(root: Path) -> tuple[bool, str]:
    """Verify every declared payload file and report the first concrete failure."""
    manifest_path = root / OFFLINE_MANIFEST
    if not manifest_path.is_file():
        return False, f"缺少离线清单：{manifest_path}"
    manifest = load_offline_manifest(root)
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, dict) or not files:
        return False, f"离线清单无效或为空：{manifest_path}"
    required = {
        f"node/{NODE_ARCHIVE}",
        f"git/{GIT_ARCHIVE}",
    }
    missing_entries = sorted(required.difference(files))
    if missing_entries:
        return False, "离线清单缺少必需项：" + "、".join(missing_entries)
    for relative in sorted(files):
        candidate = root / Path(relative)
        healthy, reason = verify_offline_asset(candidate, root)
        if not healthy:
            return False, f"{relative}：{reason}"
    return True, f"已验证 {len(files)} 个离线文件"


def install_managed_payload(paths: Paths, progress: Progress | None = None) -> Path:
    """Atomically install the complete adjacent payload beside the stable app copy."""
    destination = program_root(paths) / "offline"
    healthy, detail = verify_offline_payload(destination)
    if healthy:
        if progress:
            progress("安装包完整性", f"已安装固定离线资源·{detail}")
        return destination

    source = application_dir() / "offline"
    healthy, source_detail = verify_offline_payload(source)
    if not healthy:
        raise InstallError(
            "安装包没有完整解压，已拒绝继续。\n"
            f"当前 EXE 目录：{application_dir()}\n"
            f"离线资源目录：{source}\n"
            f"检查结果：{source_detail}\n"
            "请将整个 ZIP 保存到本地后执行“全部解压”，不要从微信或压缩包预览窗口直接运行 EXE。"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f"offline.new-{uuid.uuid4().hex}"
    if progress:
        progress("安装包完整性", "正在将已验证的离线资源安装到固定目录")
    try:
        shutil.copytree(source, staging)
        copied, copied_detail = verify_offline_payload(staging)
        if not copied:
            raise InstallError(f"固定离线资源复制后校验失败：{copied_detail}")
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=False)
        os.replace(staging, destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    if progress:
        progress("安装包完整性", f"固定离线资源已就绪·{source_detail}")
    return destination


def offline_asset_summary(root: Path | None = None) -> str:
    node_text = f"已内置 Node.js {NODE_VERSION}" if bundled_node_archive(root) else "需要时使用已有 npm"
    git_text = f"已内置 PortableGit {GIT_VERSION}" if bundled_git_archive(root) else "缺失"
    return (
        f"Git Bash：{git_text}；npm 运行环境：{node_text}；"
        "DeepSeek：Anthropic 接口直连；Claude Code：稳定版多源回退；VS Code：可选联网"
    )


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    safe = text
    for secret in secrets:
        if secret:
            safe = safe.replace(secret, "***")
    return safe


def verify_release_integrity() -> None:
    """Verify packaged releases when a post-signing integrity sidecar is present."""
    if not getattr(sys, "frozen", False):
        return
    sidecar = application_dir() / RELEASE_INTEGRITY
    if not sidecar.is_file():
        # Developer one-file builds remain runnable; package_v2.ps1 refuses to
        # publish without this post-signing sidecar.
        return
    try:
        metadata = json.loads(sidecar.read_text(encoding="utf-8-sig"))
        expected_hash = str(metadata["sha256"]).lower()
        expected_publisher = str(metadata.get("publisher", ""))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise InstallError("发布完整性文件无效，已拒绝启动") from exc
    executable = Path(sys.executable).resolve()
    if _sha256(executable).lower() != expected_hash:
        raise InstallError("配置器文件哈希与签名发布记录不一致，可能已被替换")
    verify_authenticode(executable, expected_publisher)


def command_path(name: str) -> str | None:
    if name == "claude":
        # Prefer Anthropic's native binary. PowerShell can otherwise select the
        # npm-generated claude.ps1 shim and block it under ExecutionPolicy.
        candidates = [
            Path.home() / ".local/bin/claude.exe",
            Path(shutil.which("claude.exe") or ""),
            Path(os.environ.get("APPDATA", "")) / "npm/claude.cmd",
        ]
        launcher = (Paths.default().root / "bin/claude.cmd").resolve()
        for candidate in candidates:
            if candidate.is_file() and candidate.resolve() != launcher:
                return str(candidate)
        return None
    found = shutil.which(name)
    if found:
        return found
    candidates: dict[str, list[Path]] = {
        "code": [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Microsoft VS Code/bin/code.cmd",
            Path(os.environ.get("ProgramFiles", "")) / "Microsoft VS Code/bin/code.cmd",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft VS Code/bin/code.cmd",
        ],
        "wt": [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WindowsApps/wt.exe",
        ],
    }
    for candidate in candidates.get(name, []):
        if candidate.is_file():
            return str(candidate)
    return None


def _normalise_path_entry(value: str) -> str:
    cleaned = os.path.expandvars(value.strip().strip('"'))
    return os.path.normcase(os.path.normpath(cleaned))


def broadcast_environment_change() -> None:
    if os.name != "nt":
        return
    try:
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "Environment", 0x0002, 3000, ctypes.byref(result),
        )
    except Exception:
        pass


def prepend_user_path(directory: Path) -> None:
    """Prepend a per-user command folder without relaxing PowerShell policy."""
    if os.name != "nt":
        return
    import winreg

    resolved = str(directory.resolve())
    wanted = _normalise_path_entry(resolved)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        try:
            current, value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, value_type = "", winreg.REG_EXPAND_SZ
        entries = [entry for entry in str(current).split(";") if entry.strip()]
        entries = [entry for entry in entries if _normalise_path_entry(entry) != wanted]
        winreg.SetValueEx(key, "Path", 0, value_type, ";".join([resolved, *entries]))

    process_entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    process_entries = [entry for entry in process_entries if _normalise_path_entry(entry) != wanted]
    os.environ["PATH"] = os.pathsep.join([resolved, *process_entries])
    broadcast_environment_change()


def install_stable_configurator(paths: Paths) -> Path | None:
    """Keep a stable local copy so terminal launch still works if the ZIP moves."""
    if not getattr(sys, "frozen", False):
        return None
    source = Path(sys.executable).resolve()
    destination = program_root(paths) / "ClaudeDeepSeekConfigurator.exe"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source == destination.resolve():
        return destination
    temporary = destination.with_suffix(".exe.new")
    try:
        shutil.copy2(source, temporary)
        if _sha256(source) != _sha256(temporary):
            raise InstallError("配置器本地副本校验失败")
        os.replace(temporary, destination)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise InstallError(f"无法创建稳定的终端启动入口：{exc}") from exc
    release_sidecar = application_dir() / RELEASE_INTEGRITY
    if release_sidecar.is_file():
        shutil.copy2(release_sidecar, destination.parent / RELEASE_INTEGRITY)
    return destination


def _batch_portable_path(path: Path) -> str:
    """Represent standard user paths with ASCII environment-variable tokens."""
    resolved = path.resolve()
    root_values = [
        ("LOCALAPPDATA", os.environ.get("LOCALAPPDATA")),
        ("APPDATA", os.environ.get("APPDATA")),
        ("USERPROFILE", os.environ.get("USERPROFILE") or str(Path.home())),
        ("ProgramFiles", os.environ.get("ProgramFiles")),
        ("ProgramFiles(x86)", os.environ.get("ProgramFiles(x86)")),
    ]
    roots = [(name, Path(value).resolve()) for name, value in root_values if value]
    roots.sort(key=lambda item: len(str(item[1])), reverse=True)
    for name, root in roots:
        try:
            relative = resolved.relative_to(root)
            return f"%{name}%\\{relative}" if relative.parts else f"%{name}%"
        except ValueError:
            continue
    return str(resolved)


def install_claude_terminal_launcher(paths: Paths, claude: str, configurator: Path | None = None) -> Path | None:
    """Create a CMD launcher that injects DeepSeek credentials only into Claude."""
    host = configurator or install_stable_configurator(paths)
    if not host:
        return None
    launcher_dir = paths.root / "bin"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    launcher = launcher_dir / "claude.cmd"
    host_text = _batch_portable_path(host)
    content = (
        "@echo off\r\n"
        "setlocal\r\n"
        f'"{host_text}" --run-claude %*\r\n'
        "exit /b %errorlevel%\r\n"
    )
    try:
        launcher.write_text(content, encoding="ascii")
    except UnicodeEncodeError as exc:
        raise InstallError("终端启动路径包含无法兼容当前 Windows 命令行的字符") from exc
    prepend_user_path(launcher_dir)
    return launcher


def run_command(args: list[str], timeout: int = 900, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        raise InstallError(f"操作超时：{Path(args[0]).name}") from exc
    except OSError as exc:
        if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 5:
            target = Path(args[0])
            raise InstallError(
                f"Windows 拒绝启动 {target.name}。安装文件位于：{target.parent}。"
                "请检查 Windows 安全中心的保护历史记录、第三方杀毒软件或单位电脑的应用控制策略，"
                "允许该文件后重新点击安装；如由单位策略管理，请联系管理员。"
            ) from exc
        raise InstallError(f"无法启动 {Path(args[0]).name}：{exc}") from exc


def _clean_progress_line(line: str) -> str:
    line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line).strip()
    line = re.sub(r"https?://\S+", "下载地址", line)
    return line[-240:]


def _elapsed_text(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}分{secs:02d}秒" if minutes else f"{secs}秒"


def run_command_stream(
    args: list[str], on_line: Callable[[str, float], None], timeout: int = 1200,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command while streaming output without allowing reads to block timeout."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            args, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            env=env, creationflags=flags, bufsize=1,
        )
    except OSError as exc:
        raise InstallError(f"无法启动 {Path(args[0]).name}：{exc}") from exc
    lines: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        assert process.stdout is not None
        with process.stdout:
            for output_line in process.stdout:
                lines.put(output_line)
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    started = time.monotonic()
    output: list[str] = []
    reader_done = False
    while process.poll() is None or not reader_done:
        elapsed = time.monotonic() - started
        if elapsed > timeout:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            raise InstallError(f"操作超时：{Path(args[0]).name}（已等待 {_elapsed_text(elapsed)}）")
        try:
            item = lines.get(timeout=0.25)
        except queue.Empty:
            continue
        if item is None:
            reader_done = True
            continue
        output.append(item)
        cleaned = _clean_progress_line(item)
        if cleaned:
            on_line(cleaned, elapsed)
    return subprocess.CompletedProcess(args, process.returncode, "".join(output[-500:]))


def require_success(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode:
        tail = (result.stdout or "")[-1600:].strip()
        raise InstallError(f"{label}失败（代码 {result.returncode}）\n{tail}")


def is_supported_windows() -> bool:
    if platform.system() != "Windows":
        return False
    version = sys.getwindowsversion() if hasattr(sys, "getwindowsversion") else None
    return not version or version.major >= 10


def is_supported_architecture() -> bool:
    return platform.machine().lower() in {"amd64", "x86_64"}


def windows_compatibility_label() -> str:
    version = sys.getwindowsversion() if hasattr(sys, "getwindowsversion") else None
    build = getattr(version, "build", 0) if version else 0
    release = "Windows 11" if build >= 22000 else "Windows 10"
    return f"{release} x64（内部版本 {build or '未知'}）"


def check_install_location(paths: Paths, minimum_free_bytes: int = 2 * 1024**3) -> int:
    """Fail early for locked-down profiles and nearly full system drives."""
    try:
        paths.root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="write-test-", dir=paths.root, delete=True) as probe:
            probe.write(b"ok")
            probe.flush()
        free = shutil.disk_usage(paths.root).free
    except OSError as exc:
        raise InstallError(f"无法写入当前用户的安装目录：{paths.root}\n{exc}") from exc
    if free < minimum_free_bytes:
        raise InstallError(f"系统盘空间不足，需要至少 {minimum_free_bytes / 1024**3:.0f} GB 可用空间")
    return free


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


class _WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", ctypes.c_ulong), ("pcwszFilePath", ctypes.c_wchar_p), ("hFile", ctypes.c_void_p), ("pgKnownSubject", ctypes.c_void_p)]


class _WINTRUST_DATA(ctypes.Structure):
    _fields_ = [
        ("cbStruct", ctypes.c_ulong), ("pPolicyCallbackData", ctypes.c_void_p),
        ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", ctypes.c_ulong),
        ("fdwRevocationChecks", ctypes.c_ulong), ("dwUnionChoice", ctypes.c_ulong),
        ("pFile", ctypes.POINTER(_WINTRUST_FILE_INFO)), ("dwStateAction", ctypes.c_ulong),
        ("hWVTStateData", ctypes.c_void_p), ("pwszURLReference", ctypes.c_wchar_p),
        ("dwProvFlags", ctypes.c_ulong), ("dwUIContext", ctypes.c_ulong),
    ]


class _CRYPTOAPI_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


class _CRYPT_ALGORITHM_IDENTIFIER(ctypes.Structure):
    _fields_ = [("pszObjId", ctypes.c_char_p), ("Parameters", _CRYPTOAPI_BLOB)]


class _CMSG_SIGNER_INFO_PREFIX(ctypes.Structure):
    """Leading fields of CMSG_SIGNER_INFO; Issuer + SerialNumber locate the leaf cert."""

    _fields_ = [
        ("dwVersion", wintypes.DWORD),
        ("Issuer", _CRYPTOAPI_BLOB),
        ("SerialNumber", _CRYPTOAPI_BLOB),
    ]


class _CERT_INFO_FIND(ctypes.Structure):
    """CERT_FIND_ISSUER_SERIAL only reads the Issuer and SerialNumber fields."""

    _fields_ = [
        ("dwVersion", wintypes.DWORD),
        ("SerialNumber", _CRYPTOAPI_BLOB),
        ("SignatureAlgorithm", _CRYPT_ALGORITHM_IDENTIFIER),
        ("Issuer", _CRYPTOAPI_BLOB),
    ]


def _authenticode_signer(path: Path) -> str | None:
    """Return the Authenticode signer's display name, or None.

    Uses CryptQueryObject + CryptMsgGetParam + CertFindCertificateInStore instead
    of PowerShell, so it keeps working regardless of execution policy. Best-effort:
    returns None when the file is not an embedded-signed PE or the name cannot
    be read.
    """
    if os.name != "nt":
        return None
    cert_query_object_file = 1
    cert_query_content_flag_pkcs7_signed_embed = 0x400
    cert_query_format_flag_binary = 2
    cmsg_signer_info_param = 6
    cert_find_issuer_serial = 0x000B0001
    cert_name_simple_display_type = 4

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CryptQueryObject.argtypes = [
        wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
    ]
    crypt32.CryptQueryObject.restype = wintypes.BOOL
    crypt32.CryptMsgGetParam.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
    ]
    crypt32.CryptMsgGetParam.restype = wintypes.BOOL
    crypt32.CertFindCertificateInStore.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
    ]
    crypt32.CertFindCertificateInStore.restype = ctypes.c_void_p
    crypt32.CertGetNameStringW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.c_wchar_p, wintypes.DWORD,
    ]
    crypt32.CertGetNameStringW.restype = wintypes.DWORD
    crypt32.CertFreeCertificateContext.argtypes = [ctypes.c_void_p]
    crypt32.CertFreeCertificateContext.restype = wintypes.BOOL
    crypt32.CryptMsgClose.argtypes = [ctypes.c_void_p]
    crypt32.CryptMsgClose.restype = wintypes.BOOL
    crypt32.CertCloseStore.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    crypt32.CertCloseStore.restype = wintypes.BOOL

    store = ctypes.c_void_p()
    message = ctypes.c_void_p()
    encoding = wintypes.DWORD()
    path_buffer = ctypes.create_unicode_buffer(str(path))
    if not crypt32.CryptQueryObject(
        cert_query_object_file, path_buffer,
        cert_query_content_flag_pkcs7_signed_embed, cert_query_format_flag_binary,
        0, ctypes.byref(encoding), None, None, ctypes.byref(store), ctypes.byref(message), None,
    ):
        return None
    try:
        size = wintypes.DWORD()
        if not crypt32.CryptMsgGetParam(message, cmsg_signer_info_param, 0, None, ctypes.byref(size)) or not size.value:
            return None
        raw = (ctypes.c_ubyte * size.value)()
        if not crypt32.CryptMsgGetParam(message, cmsg_signer_info_param, 0, raw, ctypes.byref(size)):
            return None
        signer = ctypes.cast(raw, ctypes.POINTER(_CMSG_SIGNER_INFO_PREFIX)).contents
        find_info = _CERT_INFO_FIND()
        find_info.SerialNumber = signer.SerialNumber
        find_info.Issuer = signer.Issuer
        cert_context = crypt32.CertFindCertificateInStore(
            store, encoding.value, 0, cert_find_issuer_serial, ctypes.byref(find_info), None,
        )
        if not cert_context:
            return None
        try:
            length = crypt32.CertGetNameStringW(
                cert_context, cert_name_simple_display_type, 0, None, None, 0,
            )
            if length <= 1:
                return None
            buffer = ctypes.create_unicode_buffer(length)
            crypt32.CertGetNameStringW(
                cert_context, cert_name_simple_display_type, 0, None, buffer, length,
            )
            return buffer.value
        finally:
            crypt32.CertFreeCertificateContext(cert_context)
    finally:
        crypt32.CryptMsgClose(message)
        crypt32.CertCloseStore(store, 0)


def _publisher_matches(signer: str, expected: str) -> bool:
    """Case-insensitive publisher allow-list match."""
    if not expected:
        return True
    if not signer:
        return False
    return expected.casefold() in signer.casefold()


def verify_authenticode(path: Path, expected_publisher: str = "") -> None:
    """Use Windows WinVerifyTrust; independent of PowerShell and execution policy."""
    if os.name != "nt" or not path.is_file():
        raise InstallError(f"无法验证下载文件的 Windows 数字签名：{path.name}")
    action_bytes = uuid.UUID("00aac56b-cd44-11d0-8cc2-00c04fc295ee").bytes_le
    action = _GUID.from_buffer_copy(action_bytes)
    file_info = _WINTRUST_FILE_INFO(ctypes.sizeof(_WINTRUST_FILE_INFO), str(path.resolve()), None, None)
    trust_data = _WINTRUST_DATA()
    trust_data.cbStruct = ctypes.sizeof(_WINTRUST_DATA)
    trust_data.dwUIChoice = 2  # WTD_UI_NONE
    trust_data.fdwRevocationChecks = 0  # WTD_REVOKE_NONE
    trust_data.dwUnionChoice = 1  # WTD_CHOICE_FILE
    trust_data.pFile = ctypes.pointer(file_info)
    trust_data.dwStateAction = 0
    trust_data.dwProvFlags = 0x1000  # WTD_CACHE_ONLY_URL_RETRIEVAL
    wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
    verify = wintrust.WinVerifyTrust
    verify.argtypes = [ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(_WINTRUST_DATA)]
    verify.restype = ctypes.c_long
    status = verify(ctypes.c_void_p(-1), ctypes.byref(action), ctypes.byref(trust_data))
    if status != 0:
        raise InstallError(f"下载文件没有通过 Windows 数字签名验证，已停止安装：{path.name}")
    if expected_publisher:
        signer = _authenticode_signer(path)
        if not signer:
            raise InstallError(f"无法读取下载文件的签名发布者，已停止安装：{path.name}")
        if not _publisher_matches(signer, expected_publisher):
            raise InstallError(
                f"下载文件的签名发布者不匹配（期望包含 {expected_publisher}，实际 {signer}），已停止安装：{path.name}"
            )


def find_python312(paths: Paths) -> str | None:
    candidates: list[Path] = [paths.runtime / "python.exe"]
    py = command_path("py")
    if py:
        try:
            result = run_command([py, "-3.12", "-c", "import sys; print(sys.executable)"], timeout=15)
            if result.returncode == 0 and result.stdout.strip():
                candidates.append(Path(result.stdout.strip().splitlines()[-1]))
        except InstallError:
            pass
    python_command = command_path("python")
    if python_command:
        candidates.append(Path(python_command))
    for variable, suffix in (
        ("LOCALAPPDATA", Path("Programs/Python/Python312/python.exe")),
        ("ProgramFiles", Path("Python312/python.exe")),
    ):
        base = os.environ.get(variable)
        if base:
            candidates.append(Path(base) / suffix)
    if os.name == "nt":
        try:
            import winreg

            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for key_name in (
                    r"SOFTWARE\Python\PythonCore\3.12\InstallPath",
                    r"SOFTWARE\WOW6432Node\Python\PythonCore\3.12\InstallPath",
                ):
                    try:
                        with winreg.OpenKey(hive, key_name) as key:
                            candidates.append(Path(str(winreg.QueryValue(key, None))) / "python.exe")
                    except OSError:
                        pass
        except ImportError:
            pass
    seen: set[str] = set()
    for candidate in candidates:
        identity = os.path.normcase(str(candidate.resolve(strict=False)))
        if identity in seen or not candidate.is_file():
            continue
        seen.add(identity)
        try:
            result = run_command([str(candidate), "-c", "import sys; print(sys.version_info[:2])"], timeout=15)
        except InstallError:
            continue
        if result.returncode == 0 and "(3, 12)" in result.stdout:
            return str(candidate)
    return None


def python312_registrations() -> list[dict[str, str]]:
    """Return Python 3.12 install registrations, including broken installs.

    A runnable python.exe is not enough to detect an interrupted or legacy
    installation: the PSF bundle can remain registered after its target files
    have been removed and will then enter maintenance mode on the next run.
    """
    if os.name != "nt":
        return []
    import winreg

    registrations: list[dict[str, str]] = []
    seen: set[str] = set()
    views = (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
    for hive_name, hive in (("current_user", winreg.HKEY_CURRENT_USER), ("local_machine", winreg.HKEY_LOCAL_MACHINE)):
        for view in dict.fromkeys(views):
            try:
                with winreg.OpenKey(
                    hive, r"SOFTWARE\Python\PythonCore\3.12\InstallPath",
                    0, winreg.KEY_READ | view,
                ) as key:
                    target = str(winreg.QueryValue(key, None) or "").strip()
                    try:
                        executable = str(winreg.QueryValueEx(key, "ExecutablePath")[0] or "").strip()
                    except OSError:
                        executable = ""
            except OSError:
                continue
            if not target:
                continue
            identity = os.path.normcase(os.path.abspath(os.path.expandvars(target)))
            if identity in seen:
                continue
            seen.add(identity)
            registrations.append({
                "scope": hive_name,
                "target": target,
                "executable": executable or str(Path(target) / "python.exe"),
            })
    return registrations


def python312_bundle_registrations() -> list[dict[str, str]]:
    """Find PSF Python 3.12.10 bundle entries shown in Installed Apps."""
    if os.name != "nt":
        return []
    import winreg

    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    views = (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
    uninstall_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive_name, hive in (("current_user", winreg.HKEY_CURRENT_USER), ("local_machine", winreg.HKEY_LOCAL_MACHINE)):
        for view in dict.fromkeys(views):
            try:
                root = winreg.OpenKey(hive, uninstall_key, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with root:
                index = 0
                while True:
                    try:
                        key_name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(root, key_name) as item:
                            values: dict[str, str] = {}
                            for name in ("DisplayName", "DisplayVersion", "Publisher", "UninstallString", "InstallLocation"):
                                try:
                                    values[name] = str(winreg.QueryValueEx(item, name)[0] or "")
                                except OSError:
                                    values[name] = ""
                    except OSError:
                        continue
                    display = values["DisplayName"].casefold()
                    version = values["DisplayVersion"]
                    publisher = values["Publisher"].casefold()
                    if "python 3.12.10" not in display and not (
                        version.startswith("3.12.10") and "python" in display and "python software foundation" in publisher
                    ):
                        continue
                    identity = (hive_name, key_name.casefold())
                    if identity in seen:
                        continue
                    seen.add(identity)
                    found.append({
                        "scope": hive_name, "key": key_name,
                        "display_name": values["DisplayName"], "version": version,
                        "uninstall_string": values["UninstallString"],
                        "install_location": values["InstallLocation"],
                    })
    return found


def _path_is_within(path: str | Path, parent: Path) -> bool:
    try:
        Path(path).resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def app_owned_python_registration(paths: Paths) -> dict[str, str] | None:
    for registration in python312_registrations():
        if _path_is_within(registration.get("target", ""), paths.root):
            return registration
    return None


def _safe_proxy_label(proxy_url: str) -> str:
    if not proxy_url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(proxy_url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme or 'http'}://{host}{port}" if host else ""
    except (TypeError, ValueError):
        return ""


def detect_system_proxy() -> dict[str, str]:
    """Return sanitized proxy endpoints discovered by Python/Windows."""
    detected: dict[str, str] = {}
    for scheme, value in urllib.request.getproxies().items():
        safe = _safe_proxy_label(str(value))
        if scheme in {"http", "https"} and safe:
            detected[scheme] = safe
    return detected


def _probe_url(url: str, timeout: float = 5.0, proxy_url: str = "") -> dict[str, object]:
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}", "Range": "bytes=0-0"},
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}),
    ) if proxy_url else urllib.request.build_opener()
    try:
        with opener.open(request, timeout=timeout) as response:
            response.read(1)
            status = int(getattr(response, "status", 200) or 200)
        return {
            "reachable": True,
            "usable": 200 <= status < 400,
            "status": status,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": "",
        }
    except urllib.error.HTTPError as exc:
        return {
            "reachable": True,
            "usable": 200 <= exc.code < 400,
            "status": int(exc.code),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": f"HTTP {exc.code}",
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "reachable": False, "usable": False, "status": 0,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": _clean_progress_line(str(exc)),
        }


def probe_install_sources(timeout: float = 5.0, proxy_url: str = "") -> dict[str, dict[str, object]]:
    """Probe independent sources concurrently; results guide ordering but never prove a download."""
    results: dict[str, dict[str, object]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(NETWORK_TARGETS)) as executor:
        pending = {
            executor.submit(_probe_url, url, timeout, proxy_url): name
            for name, url in NETWORK_TARGETS.items()
        }
        for future in concurrent.futures.as_completed(pending):
            name = pending[future]
            try:
                results[name] = future.result()
            except Exception as exc:  # defensive: a probe must never crash installation
                results[name] = {
                    "reachable": False, "usable": False, "status": 0,
                    "latency_ms": 0, "error": _clean_progress_line(str(exc)),
                }
    results["winget"] = {
        "reachable": bool(command_path("winget")),
        "usable": bool(command_path("winget")),
        "status": 0, "latency_ms": 0,
        "error": "" if command_path("winget") else "未安装或不在 PATH",
    }
    return results


def install_source_summary(results: dict[str, dict[str, object]]) -> str:
    labels = {
        "native": "Anthropic 官方", "npm_mirror": "npm 国内镜像",
        "npm_official": "npm 官方", "winget": "WinGet", "deepseek": "DeepSeek",
    }
    parts = []
    for name in ("native", "npm_mirror", "npm_official", "winget", "deepseek"):
        item = results.get(name, {})
        if item.get("usable"):
            latency = int(item.get("latency_ms") or 0)
            parts.append(f"{labels[name]}可用" + (f"（{latency}ms）" if latency else ""))
        elif item.get("reachable"):
            parts.append(f"{labels[name]}有响应但不可用")
        else:
            parts.append(f"{labels[name]}不通")
    return "；".join(parts)


def record_install_attempt(paths: Paths, source: str, result: subprocess.CompletedProcess[str] | None = None,
                           message: str = "") -> None:
    state = load_state(paths)
    attempts = state.get("install_attempts")
    attempts = attempts if isinstance(attempts, list) else []
    output = _clean_progress_line((result.stdout or "")[-1200:]) if result is not None else ""
    attempts.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "returncode": result.returncode if result is not None else None,
        "message": _clean_progress_line(message or output),
    })
    state["install_attempts"] = attempts[-30:]
    _write_state(paths, state)


def download(
    url: str, destination: Path, progress: Progress | None = None,
    attempts: int = 3, proxy_url: str = "",
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
        partial.unlink(missing_ok=True)
        try:
            open_request = (
                urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}),
                ).open
                if proxy_url else urllib.request.urlopen
            )
            with open_request(request, timeout=45) as response, partial.open("wb") as output:
                try:
                    total = int(response.headers.get("Content-Length") or "0")
                except (TypeError, ValueError):
                    total = 0
                received = 0
                started = time.monotonic()
                last_report = 0.0
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    output.write(chunk)
                    received += len(chunk)
                    now = time.monotonic()
                    if progress and (now - last_report >= 0.5 or received == total):
                        elapsed = max(now - started, 0.1)
                        speed = received / elapsed / 1024 / 1024
                        downloaded = received / 1024 / 1024
                        if total:
                            detail = f"{destination.name}：{received * 100 // total}% · {downloaded:.1f}/{total / 1024 / 1024:.1f} MB · {speed:.1f} MB/s"
                        else:
                            detail = f"{destination.name}：已下载 {downloaded:.1f} MB · {speed:.1f} MB/s"
                        progress("下载运行环境", detail)
                        last_report = now
            if total and received != total:
                raise OSError(f"文件不完整：应为 {total} 字节，实际 {received} 字节")
            partial.replace(destination)
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            partial.unlink(missing_ok=True)
            if attempt < attempts:
                if progress:
                    progress("下载运行环境", f"网络中断，正在进行第 {attempt + 1}/{attempts} 次重试…")
                time.sleep(min(attempt * 2, 4))
    destination.unlink(missing_ok=True)
    raise InstallError(f"下载失败，已重试 {attempts} 次。请检查网络、VPN 或代理设置：{last_error}") from last_error


def stage_installer(source: Path, destination: Path) -> Path:
    """Copy a verified local installer into the app-owned cache without using %TEMP%."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    partial.unlink(missing_ok=True)
    try:
        with source.open("rb") as input_stream, partial.open("wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
        if partial.stat().st_size != source.stat().st_size:
            raise OSError("复制后的文件大小不一致")
        partial.replace(destination)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise InstallError(f"无法准备安装文件 {destination.name}：{exc}") from exc
    return destination


def prepare_python_installer(paths: Paths, progress: Progress | None = None) -> Path:
    local_installer = bundled_python_installer()
    if local_installer:
        if progress:
            progress("Python 3.12", "正在使用内置安装包（已通过 SHA256 校验）")
    else:
        if progress:
            progress("Python 3.12", "未找到可用的内置安装包，正在联网下载官方安装包")
    installer = paths.root / "installers" / f"python-{PYTHON_VERSION}-amd64.exe"
    if local_installer is not None:
        if progress:
            progress("Python 3.12", "正在把内置安装包准备到本机安全目录")
        stage_installer(local_installer, installer)
    else:
        download(PYTHON_URL, installer, progress)
    if progress:
        progress("Python 3.12", "正在验证 Python Software Foundation 数字签名")
    verify_authenticode(installer, "Python Software Foundation")
    return installer


# ---------------------------------------------------------------------------
# 遗留安装链说明（V2.9.0 起主流程已不再使用）：
# 以下 ensure_python312 / venv / LiteLLM 相关的 "安装侧" 函数是 V2.7–V2.9.2
# 本地代理架构的遗留代码。V2.9.3 采用 deepseek.com/anthropic 直连，主动安装流程
# （install_all）不再调用它们。它们之所以保留，是因为：
#   1) 旧版本（V2.7–V2.9.2）卸载时需要用 stop_proxy / python 登记清理等机制
#      把代理与私有 Python 一并还原，不能因为本版不装就丢掉清理能力；
#   2) export_diagnostics 依赖其中部分函数收集诊断信息。
# 因此这些函数目前只作 "清理/诊断" 用途，不参与 V2.9.3 的安装路径。
# 若要彻底移除，必须在确认旧版卸载与诊断流程完全迁移后另立分支进行，
# 不可影响 install_all 与 uninstall_all 的既有契约。
# ---------------------------------------------------------------------------
def ensure_python312(paths: Paths, progress: Progress) -> str:
    existing = find_python312(paths)
    if existing:
        progress("Python 3.12", "已就绪，保留现有安装")
        return existing

    registrations = python312_registrations()
    owned_registration = app_owned_python_registration(paths)
    if registrations and not owned_registration:
        targets = "、".join(item.get("target", "未知位置") for item in registrations[:3])
        raise InstallError(
            "检测到电脑原有的 Python 3.12 安装登记，但对应 python.exe 无法运行。"
            f"为避免修改用户原有软件，配置器已停止安装。请先在 Windows“已安装的应用”中修复 Python 3.12：{targets}"
        )

    installer = prepare_python_installer(paths, progress)
    if owned_registration:
        progress("Python 3.12", "检测到旧版配置器遗留的安装登记，正在安全修复")
        repair_log = paths.root / "python-repair.log"
        repair = run_command([
            str(installer), "/repair", "/quiet", "/log", str(repair_log),
        ], timeout=1200)
        repaired = find_python312(paths) if repair.returncode == 0 else None
        if repaired:
            progress("Python 3.12", "旧版私有运行环境已修复并纳入卸载记录")
            return repaired

        progress("Python 3.12", "旧登记修复未完成，正在撤销损坏登记后重新安装")
        legacy_uninstall_log = paths.root / "python-legacy-uninstall.log"
        removed = run_command([
            str(installer), "/uninstall", "/quiet", "/log", str(legacy_uninstall_log),
        ], timeout=1200)
        if removed.returncode or app_owned_python_registration(paths):
            raise InstallError(
                "旧版配置器留下的 Python 3.12 Windows 安装登记已损坏，自动修复和安全撤销均未完成。"
                f"请导出诊断包后处理；修复日志：{repair_log}；卸载日志：{legacy_uninstall_log}"
            )
        if paths.runtime.exists():
            _remove_tree(paths.runtime)

    progress("Python 3.12", "正在静默安装（仅当前用户）")
    install_log = paths.root / "python-install.log"
    result = run_command([
        str(installer), "/quiet", "InstallAllUsers=0", "Include_launcher=0",
        "Include_test=0", "Include_doc=0", "Include_tcltk=0", "Include_idle=0",
        "Shortcuts=0", "AssociateFiles=0", "PrependPath=0", f"TargetDir={paths.runtime}",
        "/log", str(install_log),
    ], timeout=1200)
    require_success(result, "Python 3.12 安装")
    python = find_python312(paths)
    if not python:
        raise InstallError(
            "Python 安装程序已结束，但未找到可用的 Python 3.12。"
            f"安装日志：{install_log}。如果电脑曾安装或损坏过 Python 3.12，请先在“已安装的应用”中修复或卸载旧版本后重试。"
        )
    return python


def ensure_private_venv(paths: Paths, python: str, progress: Progress) -> str:
    """Create or repair the app-owned Python 3.12 venv and guarantee pip exists."""
    venv_python = paths.venv / "Scripts/python.exe"
    venv_usable = False
    if venv_python.is_file():
        probe = run_command([str(venv_python), "-c", "import sys; assert sys.version_info[:2] == (3, 12)"], timeout=30)
        venv_usable = probe.returncode == 0
    if not venv_usable:
        args = [python, "-m", "venv"]
        if venv_python.exists():
            try:
                paths.venv.resolve().relative_to(paths.root.resolve())
            except (OSError, ValueError) as exc:
                raise InstallError("独立环境路径异常，已停止自动修复") from exc
            progress("独立环境", "检测到损坏或版本不匹配，正在安全重建")
            args.append("--clear")
        else:
            progress("独立环境", "正在创建")
        args.append(str(paths.venv))
        require_success(run_command(args, timeout=180), "创建独立环境")
    pip_env = isolated_pip_environment()
    pip_probe = run_command([str(venv_python), "-m", "pip", "--version"], timeout=30, env=pip_env)
    if pip_probe.returncode:
        progress("独立环境", "正在修复内置 pip")
        require_success(run_command([str(venv_python), "-m", "ensurepip", "--upgrade"], timeout=180, env=pip_env), "修复 pip")
        pip_probe = run_command([str(venv_python), "-m", "pip", "--version"], timeout=30, env=pip_env)
        require_success(pip_probe, "检查 pip")
    return str(venv_python)


def ensure_venv_and_litellm(paths: Paths, python: str, progress: Progress) -> str:
    venv_python = Path(ensure_private_venv(paths, python, progress))
    pip_env = isolated_pip_environment()
    metadata_probe = run_command([
        str(venv_python), "-c", "import importlib.metadata as m; print(m.version('litellm'))",
    ], timeout=30)
    healthy, reason = verify_litellm_environment(str(venv_python))
    if healthy:
        progress("LiteLLM", f"固定版本 {LITELLM_VERSION} 已就绪")
        return str(venv_python)
    repair_existing = metadata_probe.returncode == 0
    if repair_existing:
        progress("LiteLLM", f"检测到不完整的已有安装，正在自动修复：{reason}")
    wheelhouse = bundled_wheelhouse()
    lockfile = bundled_lockfile()
    sources: list[tuple[str, str | Path]] = []
    if wheelhouse:
        sources.append(("内置离线包", wheelhouse))
    sources.extend([("PyPI 官方源", PYPI_OFFICIAL), ("清华大学 TUNA 备用源", PYPI_TUNA)])
    last_result: subprocess.CompletedProcess[str] | None = None
    for attempt, (source_name, source_url) in enumerate(sources, start=1):
        progress("LiteLLM", f"第 {attempt}/{len(sources)} 次尝试 · {source_name} · 准备安装固定版本 {LITELLM_VERSION}")
        pip_args = [
            str(venv_python), "-m", "pip", "install", "--disable-pip-version-check",
            "--upgrade", "--no-cache-dir",
        ]
        if isinstance(source_url, Path):
            pip_args.extend(["--no-index", "--find-links", str(source_url)])
        else:
            # Python 3.12 venvs on clean PCs can contain pip versions predating
            # --resume-retries. Use only long-supported options here.
            pip_args.extend(["--retries", "3", "--timeout", "30", "--index-url", source_url])
        if lockfile:
            pip_args.extend(["--require-hashes", "-r", str(lockfile)])
        else:
            pip_args.append(f"litellm[proxy]=={LITELLM_VERSION}")
        if repair_existing or (wheelhouse and attempt > 1):
            pip_args.append("--force-reinstall")

        def report(line: str, elapsed: float, source: str = source_name) -> None:
            friendly = line
            if "Retrying" in line:
                friendly = "网络响应较慢，pip 正在自动重试…"
            elif line.startswith("Collecting "):
                friendly = "正在准备 " + line.removeprefix("Collecting ")
            elif line.startswith("Installing collected packages"):
                friendly = "下载完成，正在安装依赖文件…"
            elif line.startswith("Successfully installed"):
                friendly = "依赖安装完成，正在做版本检查…"
            progress("LiteLLM", f"{source} · 已用 {_elapsed_text(elapsed)}\n{friendly}")

        last_result = run_command_stream(pip_args, report, timeout=1200, env=pip_env)
        if last_result.returncode == 0:
            healthy, reason = verify_litellm_environment(str(venv_python), env=pip_env)
            if healthy:
                break
            last_result = subprocess.CompletedProcess(pip_args, 1, f"安装后完整性检查失败：{reason}")
            progress("LiteLLM", f"{source_name} 下载完成，但完整性检查未通过：{reason}")
        if attempt < len(sources):
            if isinstance(source_url, Path):
                progress("LiteLLM", "内置离线包未能完整安装，正在自动联网补齐")
                continue
            if "HASHES" in (last_result.stdout or "").upper() or "hash" in (last_result.stdout or "").lower():
                progress("LiteLLM", "检测到下载文件哈希不一致；已禁用缓存，正在切换备用源重新下载")
            else:
                progress("LiteLLM", f"{source_name} 未完成，正在自动尝试下一个来源")
    assert last_result is not None
    require_success(last_result, "LiteLLM 安装")
    healthy, reason = verify_litellm_environment(str(venv_python), env=pip_env)
    if not healthy:
        raise InstallError(f"LiteLLM 安装后完整性检查失败：{reason}")
    return str(venv_python)


def isolated_pip_environment() -> dict[str, str]:
    """Ignore machine-wide pip policies that can silently alter this private venv install."""
    env = os.environ.copy()
    for name in (
        "PIP_REQUIRE_HASHES", "PIP_CONSTRAINT", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL",
        "PIP_NO_INDEX", "PIP_FIND_LINKS", "PIP_TRUSTED_HOST", "PIP_NO_CACHE_DIR",
    ):
        env.pop(name, None)
    env["PIP_CONFIG_FILE"] = os.devnull
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    return env


def verify_litellm_environment(venv_python: str, env: dict[str, str] | None = None) -> tuple[bool, str]:
    """Version metadata alone is insufficient after an interrupted pip transaction."""
    probe = run_command([
        venv_python, "-c",
        "import importlib.metadata as m; v=m.version('litellm'); "
        "assert v=='1.80.11', v; import litellm, fastapi, starlette, uvicorn, pydantic; print('版本='+v)",
    ], timeout=60, env=env)
    if probe.returncode:
        detail = (probe.stdout or "").strip().splitlines()
        return False, (detail[-1] if detail else "关键模块无法导入")[-300:]
    dependency_check = run_command([venv_python, "-m", "pip", "check"], timeout=120, env=env)
    if dependency_check.returncode:
        detail = (dependency_check.stdout or "").strip().splitlines()
        return False, (detail[-1] if detail else "依赖关系不完整")[-300:]
    return True, f"版本={LITELLM_VERSION}，关键模块和依赖检查通过"


def managed_node_dir(paths: Paths) -> Path:
    return paths.root / "managed-node"


def managed_git_dir(paths: Paths) -> Path:
    return paths.root / "managed-git"


def _git_bash_candidates(paths: Paths) -> list[Path]:
    candidates = [
        managed_git_dir(paths) / "bin" / "bash.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Git" / "bin" / "bash.exe",
    ]
    git = command_path("git.exe") or command_path("git")
    if git:
        git_path = Path(git).resolve()
        candidates.extend([
            git_path.parent.parent / "bin" / "bash.exe",
            git_path.parent.parent / "usr" / "bin" / "bash.exe",
        ])
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate))
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def find_git_bash(paths: Paths) -> Path | None:
    for candidate in _git_bash_candidates(paths):
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def environment_with_git(paths: Paths, env: dict[str, str] | None = None) -> dict[str, str]:
    result = dict(env or os.environ)
    bash = find_git_bash(paths)
    if not bash:
        return result
    git_root = bash.parent.parent
    result["CLAUDE_CODE_GIT_BASH_PATH"] = str(bash)
    additions = [git_root / "cmd", git_root / "bin"]
    result["PATH"] = os.pathsep.join([
        *(str(item) for item in additions if item.is_dir()),
        result.get("PATH", ""),
    ])
    return result


def ensure_git(paths: Paths, progress: Progress) -> str:
    """Provide Git Bash without requiring an administrator or changing system PATH."""
    existing = find_git_bash(paths)
    if existing:
        git = existing.parent.parent / "cmd" / "git.exe"
        result = run_command([str(git), "--version"], timeout=30) if git.is_file() else None
        if result is not None and result.returncode == 0:
            progress("Git Bash", f"{result.stdout.strip()} 已就绪")
            return str(existing)

    root = offline_root(paths)
    archive = bundled_git_archive(root)
    if not archive:
        candidate = root / "git" / GIT_ARCHIVE
        _, reason = verify_offline_asset(candidate, root)
        raise InstallError(
            f"安装包中的 Git for Windows 不可用：{reason}。\n"
            f"应存在：{candidate}\n请重新完整解压 V{APP_VERSION} 测试包后运行。"
        )
    if os.name == "nt":
        verify_authenticode(archive, "Johannes Schindelin")
    progress("Git Bash", f"正在解压隔离的 PortableGit {GIT_VERSION}（无需管理员权限）")
    destination = managed_git_dir(paths)
    paths.root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="managed-git-", dir=paths.root) as temporary:
        staging = Path(temporary) / "git"
        staging.mkdir(parents=True)
        require_success(
            run_command([str(archive), "-y", f"-o{staging}"], timeout=600),
            "PortableGit 解压",
        )
        git = staging / "cmd" / "git.exe"
        bash = staging / "bin" / "bash.exe"
        if not git.is_file() or not bash.is_file():
            raise InstallError("PortableGit 解压完成但缺少 git.exe 或 bash.exe")
        require_success(run_command([str(git), "--version"], timeout=30), "Git 验证")
        if destination.exists():
            _remove_tree(destination)
        shutil.move(str(staging), str(destination))
    bash = destination / "bin" / "bash.exe"
    progress("Git Bash", f"PortableGit {GIT_VERSION} 已安装并验证")
    return str(bash)


def managed_npm_prefix(paths: Paths) -> Path:
    return paths.root / "managed-npm"


def managed_claude_path(paths: Paths) -> Path | None:
    for candidate in (
        managed_npm_prefix(paths) / "claude.cmd",
        managed_npm_prefix(paths) / "claude.exe",
    ):
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def managed_npm_environment(paths: Paths, proxy_url: str = "") -> dict[str, str]:
    env = environment_with_git(paths)
    node_dir = managed_node_dir(paths)
    env["PATH"] = os.pathsep.join([str(node_dir), env.get("PATH", "")])
    env["npm_config_prefix"] = str(managed_npm_prefix(paths))
    env["npm_config_cache"] = str(paths.root / "cache" / "npm")
    env["npm_config_update_notifier"] = "false"
    if proxy_url:
        env["HTTP_PROXY"] = proxy_url
        env["HTTPS_PROXY"] = proxy_url
        env["npm_config_proxy"] = proxy_url
        env["npm_config_https_proxy"] = proxy_url
    return env


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            target = (destination / entry.filename).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise InstallError("Node.js 离线包包含不安全路径，已拒绝解压") from exc
        bundle.extractall(destination)


def ensure_managed_node(paths: Paths, progress: Progress) -> str:
    node_dir = managed_node_dir(paths)
    node = node_dir / "node.exe"
    npm = node_dir / "npm.cmd"
    if node.is_file() and npm.is_file():
        version = run_command([str(node), "--version"], timeout=30)
        if version.returncode == 0 and re.search(r"v(?:2[2-9]|[3-9]\d)\.", version.stdout or ""):
            progress("npm 运行环境", f"受管理 Node.js {version.stdout.strip()} 已就绪")
            return str(npm)
    asset_root = offline_root(paths)
    archive = bundled_node_archive(asset_root)
    if not archive:
        candidate = asset_root / "node" / NODE_ARCHIVE
        _, reason = verify_offline_asset(candidate, asset_root)
        raise InstallError(
            f"安装包中的受管理 Node.js 不可用：{reason}。\n"
            f"应存在：{candidate}\n请重新完整解压 V{APP_VERSION} 测试包后运行。"
        )
    progress("npm 运行环境", f"正在准备受管理 Node.js {NODE_VERSION}（不会修改系统 PATH）")
    paths.root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="managed-node-", dir=paths.root) as temporary:
        staging = Path(temporary)
        _safe_extract_zip(archive, staging)
        candidates = list(staging.rglob("node.exe"))
        if len(candidates) != 1 or not (candidates[0].parent / "npm.cmd").is_file():
            raise InstallError("Node.js 离线包结构不完整")
        source = candidates[0].parent
        if node_dir.exists():
            _remove_tree(node_dir)
        shutil.move(str(source), str(node_dir))
    if os.name == "nt":
        verify_authenticode(node, "OpenJS Foundation")
    require_success(run_command([str(node), "--version"], timeout=30), "Node.js 验证")
    require_success(run_command([str(npm), "--version"], timeout=30, env=managed_npm_environment(paths)), "npm 验证")
    return str(npm)


def _source_failure(source: str, label: str, message: str,
                    result: subprocess.CompletedProcess[str] | None = None) -> dict[str, str]:
    detail = message
    if result is not None and result.stdout:
        tail = _clean_progress_line(result.stdout[-1200:])
        if tail:
            detail = f"{message}（代码 {result.returncode}：{tail}）"
    return {"source": source, "label": label, "message": detail}


def _system_npm_is_compatible(npm: str | None) -> bool:
    if not npm:
        return False
    node = command_path("node.exe") or command_path("node")
    if not node:
        return False
    result = run_command([node, "--version"], timeout=30)
    match = re.search(r"v(\d+)", result.stdout or "") if result.returncode == 0 else None
    return bool(match and int(match.group(1)) >= 22)


def _claude_source_order(strategy: str, probes: dict[str, dict[str, object]] | None) -> list[str]:
    # Prefer package managers with atomic/stable releases. npm stays as the
    # final zero-admin fallback and is pinned only after its Windows package is visible.
    allowed = ["winget", "native", "npm_mirror", "npm_official"]
    if strategy != "auto":
        if strategy not in allowed:
            raise InstallError(f"未知 Claude Code 安装方案：{strategy}")
        return [strategy]
    if not probes:
        return allowed
    online = [name for name in allowed if probes.get(name, {}).get("usable")]
    return online + [name for name in allowed if name not in online]


def _json_scalar(output: str) -> str:
    try:
        value = json.loads(output)
    except (TypeError, ValueError):
        value = output.strip().strip('"')
    return str(value).strip() if not isinstance(value, (dict, list)) else ""


def resolve_claude_npm_version(
    npm: str, registry: str, env: dict[str, str],
) -> str:
    """Select a published main/platform pair; never install a racing @latest."""
    tags = run_command([
        npm, "view", NPM_PACKAGE, "dist-tags", "--json", f"--registry={registry}",
    ], timeout=120, env=env)
    require_success(tags, "Claude Code 稳定版本查询")
    try:
        values = json.loads(tags.stdout or "{}")
    except ValueError as exc:
        raise InstallError("npm 返回的 Claude Code 版本信息无效") from exc
    if not isinstance(values, dict):
        raise InstallError("npm 没有返回 Claude Code 发布标签")
    checked: list[str] = []
    for tag in ("stable", "latest"):
        version = str(values.get(tag) or "").strip()
        if not re.fullmatch(r"\d+(?:\.\d+){2,3}(?:[-+][0-9A-Za-z.-]+)?", version):
            continue
        checked.append(f"{tag}={version}")
        platform_package = f"@anthropic-ai/claude-code-win32-x64@{version}"
        platform_result = run_command([
            npm, "view", platform_package, "version", "--json", f"--registry={registry}",
        ], timeout=120, env=env)
        if platform_result.returncode == 0 and _json_scalar(platform_result.stdout or "") == version:
            return version
    detail = "、".join(checked) if checked else "无 stable/latest 标签"
    raise InstallError(f"npm 主包与 Windows x64 平台包尚未同步（{detail}），已拒绝安装不完整版本")


def ensure_claude(
    progress: Progress, outcome: dict[str, str] | None = None,
    paths: Paths | None = None, strategy: str = "auto",
    probes: dict[str, dict[str, object]] | None = None,
    proxy_url: str = "", allow_managed_node: bool = True,
) -> str:
    persist_attempts = paths is not None
    paths = paths or Paths.default()

    def remember(source: str, result: subprocess.CompletedProcess[str] | None = None,
                 message: str = "") -> None:
        if persist_attempts:
            record_install_attempt(paths, source, result, message)

    def selected(path: str, method: str, manager: str = "", **details: str) -> str:
        if outcome is not None:
            outcome.update({"path": path, "method": method, "manager": manager, **details})
        return path

    existing = command_path("claude")
    if existing:
        if Path(existing).suffix.lower() == ".exe":
            verify_authenticode(Path(existing), "Anthropic")
        require_success(run_command([existing, "--version"], timeout=30), "Claude Code 验证")
        progress("Claude Code", "现有版本已安装并验证，保持原安装来源和版本")
        return selected(existing, "existing")
    managed = managed_claude_path(paths)
    if managed:
        verification = run_command(
            [str(managed), "--version"], timeout=30, env=managed_npm_environment(paths, proxy_url),
        )
        require_success(verification, "受管理 Claude Code 验证")
        progress("Claude Code", "配置器管理的 Claude Code 已验证，直接复用")
        match = re.search(r"\d+(?:\.\d+)+", verification.stdout or "")
        return selected(str(managed), "managed-npm", str(managed_node_dir(paths) / "npm.cmd"),
                        managed_root=str(managed_npm_prefix(paths)),
                        version=match.group(0) if match else "")

    failures: list[dict[str, str]] = []
    labels = {
        "native": "Anthropic 官方安装", "npm_mirror": "npm 国内镜像",
        "npm_official": "npm 官方源", "winget": "WinGet",
    }
    for source in _claude_source_order(strategy, probes):
        if source == "native":
            progress("Claude Code", "正在尝试 Anthropic 官方原生安装器")
            try:
                with tempfile.TemporaryDirectory(prefix="claude-native-") as temp:
                    installer = Path(temp) / "install.cmd"
                    download(
                        "https://claude.ai/install.cmd", installer, progress,
                        attempts=3 if strategy == "native" else 1, proxy_url=proxy_url,
                    )
                    env = environment_with_git(paths)
                    if proxy_url:
                        env.update({"HTTP_PROXY": proxy_url, "HTTPS_PROXY": proxy_url})
                    result = run_command(
                        ["cmd.exe", "/d", "/c", str(installer), "stable"], timeout=1200, env=env,
                    )
                    remember(source, result)
                    if result.returncode:
                        failures.append(_source_failure(source, labels[source], "安装器未完成", result))
                        continue
            except InstallError as exc:
                remember(source, message=str(exc))
                failures.append(_source_failure(source, labels[source], str(exc).splitlines()[0]))
                continue
            candidate = command_path("claude")
            if candidate and Path(candidate).suffix.lower() == ".exe":
                verify_authenticode(Path(candidate), "Anthropic")
                verification = run_command([candidate, "--version"], timeout=30)
                require_success(verification, "Claude Code 验证")
                progress("Claude Code", "Anthropic 官方原生版本已安装并验证")
                match = re.search(r"\d+(?:\.\d+)+", verification.stdout or "")
                return selected(candidate, "native", version=match.group(0) if match else "")
            failures.append(_source_failure(source, labels[source], "安装后未找到 claude.exe"))

        elif source in NPM_REGISTRIES:
            registry = NPM_REGISTRIES[source]
            progress("Claude Code", f"正在使用{labels[source]}安装")
            system_npm = command_path("npm.cmd") or command_path("npm")
            if system_npm and not _system_npm_is_compatible(system_npm):
                progress("npm 运行环境", "检测到的系统 Node.js 低于 22，将改用隔离的受管理版本")
                system_npm = None
            managed_npm = ""
            npm = system_npm
            env = environment_with_git(paths)
            method = "npm"
            try:
                if not npm and allow_managed_node:
                    managed_npm = ensure_managed_node(paths, progress)
                    npm = managed_npm
                    env = managed_npm_environment(paths, proxy_url)
                    method = "managed-npm"
                if not npm:
                    raise InstallError("电脑没有 npm，安装包也未提供受管理 Node.js")
                version = resolve_claude_npm_version(npm, registry, env)
                progress("Claude Code", f"已确认主包与 Windows x64 平台包同步：{version}")
                args = [
                    npm, "install", "-g", f"{NPM_PACKAGE}@{version}",
                    f"--registry={registry}", "--include=optional", "--no-audit", "--no-fund",
                ]
                result = run_command(args, timeout=1200, env=env)
                remember(source, result)
                if result.returncode:
                    failures.append(_source_failure(source, labels[source], "npm 安装未完成", result))
                    continue
                candidate_path = managed_claude_path(paths) if method == "managed-npm" else None
                candidate = str(candidate_path) if candidate_path else command_path("claude")
                if not candidate:
                    failures.append(_source_failure(source, labels[source], "npm 完成但未找到 claude 命令"))
                    continue
                verification = run_command([candidate, "--version"], timeout=30, env=env)
                require_success(verification, "Claude Code 验证")
                progress("Claude Code", f"已通过{labels[source]}安装并验证")
                match = re.search(r"\d+(?:\.\d+)+", verification.stdout or "")
                details = {"registry": registry, "version": match.group(0) if match else ""}
                if method == "managed-npm":
                    details["managed_root"] = str(managed_npm_prefix(paths))
                    details["node_root"] = str(managed_node_dir(paths))
                return selected(candidate, method, npm, **details)
            except InstallError as exc:
                remember(source, message=str(exc))
                failures.append(_source_failure(source, labels[source], str(exc).splitlines()[0]))

        elif source == "winget":
            winget = command_path("winget")
            if not winget:
                failures.append(_source_failure(source, labels[source], "当前电脑没有可用 WinGet"))
                continue
            progress("Claude Code", "正在尝试 WinGet（失败时会保留详细返回代码）")
            args = [
                winget, "install", "--id", "Anthropic.ClaudeCode", "-e", "--source", "winget",
                "--accept-package-agreements", "--accept-source-agreements", "--silent",
                "--disable-interactivity", "--verbose-logs",
            ]
            if proxy_url:
                args.extend(["--proxy", proxy_url])
            result = run_command(args, timeout=1200, env=environment_with_git(paths))
            remember(source, result)
            if result.returncode:
                failures.append(_source_failure(source, labels[source], "安装未完成", result))
                continue
            candidate = command_path("claude")
            if candidate and Path(candidate).suffix.lower() == ".exe":
                verify_authenticode(Path(candidate), "Anthropic")
                verification = run_command([candidate, "--version"], timeout=30)
                require_success(verification, "Claude Code 验证")
                progress("Claude Code", "已通过 WinGet 安装并验证")
                match = re.search(r"\d+(?:\.\d+)+", verification.stdout or "")
                return selected(candidate, "winget", winget, version=match.group(0) if match else "")
            failures.append(_source_failure(source, labels[source], "WinGet 完成但未找到 claude.exe"))

    available = ["npm_mirror", "npm_official", "native"]
    if command_path("winget"):
        available.append("winget")
    raise InstallSourceError(failures, available)


def vscode_extension_is_installed(extension_root: Path | None = None) -> bool:
    """Check VS Code's active extension registry without starting another Code CLI."""
    root = extension_root or (Path.home() / ".vscode" / "extensions")
    registry = root / "extensions.json"
    try:
        entries = json.loads(registry.read_text(encoding="utf-8-sig"))
        if isinstance(entries, list):
            for entry in entries:
                identifier = entry.get("identifier", {}) if isinstance(entry, dict) else {}
                if str(identifier.get("id", "")).casefold() == VSCODE_EXTENSION.casefold():
                    return True
    except (OSError, ValueError, TypeError):
        pass

    # Older VS Code versions may not have an extensions.json registry. Exclude
    # folders listed in .obsolete so an interrupted/removed update is not
    # mistaken for an active installation.
    obsolete: set[str] = set()
    try:
        data = json.loads((root / ".obsolete").read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            obsolete = {str(name).casefold() for name, removed in data.items() if removed}
    except (OSError, ValueError, TypeError):
        pass
    prefix = VSCODE_EXTENSION.casefold() + "-"
    try:
        return any(
            item.is_dir() and item.name.casefold().startswith(prefix) and item.name.casefold() not in obsolete
            for item in root.iterdir()
        )
    except OSError:
        return False


def ensure_vscode(
    progress: Progress, paths: Paths | None = None,
    outcome: dict[str, str] | None = None,
) -> str:
    install_method = "existing"
    manager = ""
    existing = command_path("code")
    if not existing:
        winget = command_path("winget")
        if winget:
            progress("VS Code", "正在通过 WinGet 安装")
            result = run_command([winget, "install", "--id", "Microsoft.VisualStudioCode", "-e", "--scope", "user", "--accept-package-agreements", "--accept-source-agreements", "--silent"], timeout=1200)
            if result.returncode == 0:
                existing = command_path("code")
                if existing:
                    install_method = "winget"
                    manager = winget
            else:
                progress("VS Code", "WinGet 未完成，正在改用微软官方安装包")
        if not existing:
            progress("VS Code", "正在下载微软官方安装包")
            if paths is None:
                temporary = tempfile.TemporaryDirectory(prefix="claude-deepseek-vscode-")
                installer = Path(temporary.name) / "VSCodeUserSetup.exe"
            else:
                temporary = None
                installer = paths.root / "installers" / "VSCodeUserSetup-x64.exe"
            try:
                download("https://update.code.visualstudio.com/latest/win32-x64-user/stable", installer, progress)
                progress("VS Code", "正在验证 Microsoft 数字签名")
                verify_authenticode(installer, "Microsoft Corporation")
                require_success(run_command([str(installer), "/VERYSILENT", "/NORESTART", "/MERGETASKS=!runcode"], timeout=1200), "VS Code 安装")
                install_method = "installer"
            finally:
                if temporary is not None:
                    temporary.cleanup()
        existing = command_path("code")
    if not existing:
        raise InstallError("VS Code 安装完成但命令尚不可见，请重启配置器后重试")
    if outcome is not None:
        outcome.update({"path": existing, "method": install_method, "manager": manager})
    if vscode_extension_is_installed():
        progress("VS Code 扩展", "Anthropic 官方扩展已安装，直接复用")
        return existing
    progress("VS Code 扩展", "正在安装 Anthropic 官方扩展")
    require_success(run_command([existing, "--install-extension", VSCODE_EXTENSION], timeout=600), "Claude Code 扩展安装")
    return existing


def ensure_proxy_token() -> str:
    token = read_proxy_token()
    if token:
        return token
    token = "sk-local-" + secrets.token_urlsafe(32)
    write_proxy_token(token)
    return token


def write_litellm_config(paths: Paths) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    content = """model_list:
  - model_name: deepseek-chat
    litellm_params:
      model: deepseek/deepseek-chat
      api_base: https://api.deepseek.com
      api_key: os.environ/DEEPSEEK_API_KEY
  - model_name: deepseek-reasoner
    litellm_params:
      model: deepseek/deepseek-reasoner
      api_base: https://api.deepseek.com
      api_key: os.environ/DEEPSEEK_API_KEY

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  disable_spend_logs: true
"""
    paths.config.write_text(content, encoding="utf-8")


def gateway_environment(model: str, api_key: str) -> dict[str, str]:
    if model not in SUPPORTED_MODELS:
        raise InstallError(f"不支持的 DeepSeek 模型：{model}")
    return {
        "ANTHROPIC_BASE_URL": DEEPSEEK_ANTHROPIC_URL,
        "ANTHROPIC_AUTH_TOKEN": api_key,
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": DEFAULT_REASONING_MODEL,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": DEFAULT_REASONING_MODEL,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": DEFAULT_MODEL,
        "CLAUDE_CODE_SUBAGENT_MODEL": DEFAULT_MODEL,
        "CLAUDE_CODE_EFFORT_LEVEL": "max",
        # 这两个变量一起确保 claude 用真实窗口而不是对未知模型名猜 200k：
        # - MAX_CONTEXT_TOKENS 显式给出 DeepSeek V4 的真实 1M 窗口。
        # - DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT 是兜底，取消仅因模型名
        #   未被识别而施加的窗口假设，交由 API 侧处理。
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(DEEPSEEK_CONTEXT_TOKENS),
        "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT": "1",
    }


def set_user_environment(model: str, _unused_token: str = "") -> None:
    """Compatibility no-op: V2.9.3 no longer persists gateway credentials or routing."""
    if model not in SUPPORTED_MODELS:
        raise InstallError(f"不支持的 DeepSeek 模型：{model}")


def save_state(
    paths: Paths, model: str, python: str, claude: str, code: str,
    terminal_launcher: str = "", configurator: str = "",
    environment_backup: dict[str, str | None] | None = None,
    git_bash: str = "",
) -> None:
    data = load_state(paths)
    data.update({
        "model": model, "python": python, "claude": claude, "code": code,
        "proxy_url": "", "gateway_url": DEEPSEEK_ANTHROPIC_URL,
        "connection_mode": "direct-anthropic", "terminal_launcher": terminal_launcher,
        "configurator": configurator,
        "git_bash": git_bash,
        "environment_backup": environment_backup or {},
        "install_complete": True,
    })
    _write_state(paths, data)


def load_state(paths: Paths) -> dict[str, object]:
    try:
        return json.loads(paths.state.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _write_state(paths: Paths, data: dict[str, object]) -> None:
    """Atomically persist the rollback journal so an interrupted install remains removable."""
    paths.root.mkdir(parents=True, exist_ok=True)
    temporary = paths.state.with_suffix(paths.state.suffix + ".new")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, paths.state)


def _user_path_entries() -> list[str]:
    if os.name != "nt":
        return []
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, "Path")
    except FileNotFoundError:
        return []
    return [entry for entry in str(value).split(";") if entry.strip()]


def _known_component_paths(paths: Paths) -> dict[str, Path]:
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    return {
        "claude_native": home / ".local" / "bin" / "claude.exe",
        "claude_share": home / ".local" / "share" / "claude",
        "claude_data": home / ".claude",
        "claude_npm": roaming / "npm" / "claude.cmd",
        "claude_managed": managed_npm_prefix(paths),
        "node_managed": managed_node_dir(paths),
        "npm_cache": paths.root / "cache" / "npm",
        "vscode_program": local / "Programs" / "Microsoft VS Code",
        "vscode_uninstaller": local / "Programs" / "Microsoft VS Code" / "unins000.exe",
        "vscode_data": roaming / "Code",
        "vscode_local_data": local / "Code",
        "vscode_extensions": home / ".vscode",
        "python_runtime": paths.runtime,
    }


def capture_install_baseline(paths: Paths) -> dict[str, object]:
    """Capture the pre-install state used to remove only components this app adds."""
    known = _known_component_paths(paths)
    python = find_python312(paths)
    claude = command_path("claude")
    code = command_path("code")
    return {
        "format": 2,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "python": {
            "present": bool(python), "path": python or "",
            "registrations": python312_registrations(),
            "bundle_registrations": python312_bundle_registrations(),
            "runtime_existed": known["python_runtime"].exists(),
        },
        "claude": {
            "present": bool(claude), "path": claude or "",
            "native_existed": known["claude_native"].is_file(),
            "share_existed": known["claude_share"].exists(),
            "data_existed": known["claude_data"].exists(),
            "npm_existed": known["claude_npm"].is_file(),
            "managed_existed": known["claude_managed"].exists(),
            "managed_node_existed": known["node_managed"].exists(),
        },
        "vscode": {
            "present": bool(code), "path": code or "",
            "program_existed": known["vscode_program"].exists(),
            "data_existed": known["vscode_data"].exists(),
            "local_data_existed": known["vscode_local_data"].exists(),
            "extensions_dir_existed": known["vscode_extensions"].exists(),
        },
        "vscode_extension": {"present": vscode_extension_is_installed()},
        "user_path": _user_path_entries(),
        "desktop_shortcut": {
            "primary_existed": desktop_shortcut_path().exists(),
            "legacy_existed": legacy_desktop_shortcut_path().exists(),
        },
    }


def ensure_install_journal(paths: Paths) -> dict[str, object]:
    state = load_state(paths)
    ownership = state.get("ownership")
    if not isinstance(ownership, dict) or ownership.get("format") not in {1, 2}:
        baseline = capture_install_baseline(paths)
        shortcut_state = baseline.get("desktop_shortcut")
        shortcut_state = shortcut_state if isinstance(shortcut_state, dict) else {}
        backup_dir = paths.root / "backups"
        for label, shortcut in (
            ("primary", desktop_shortcut_path()),
            ("legacy", legacy_desktop_shortcut_path()),
        ):
            if shortcut.exists():
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup = backup_dir / f"desktop-{label}.lnk"
                shutil.copy2(shortcut, backup)
                shortcut_state[f"{label}_backup"] = str(backup)
        baseline["desktop_shortcut"] = shortcut_state
        ownership = {
            "format": 2,
            "baseline": baseline,
            "installed": {},
        }
    else:
        # Preserve the original V2.9 baseline while adding evidence that older
        # builds could not capture. A registration inside our managed root is
        # necessarily configurator-owned even when python.exe has disappeared.
        baseline = ownership.get("baseline")
        baseline = baseline if isinstance(baseline, dict) else {}
        python_baseline = baseline.get("python")
        python_baseline = python_baseline if isinstance(python_baseline, dict) else {}
        if "registrations" not in python_baseline:
            python_baseline["registrations"] = python312_registrations()
            python_baseline["runtime_existed"] = paths.runtime.exists()
        if "bundle_registrations" not in python_baseline:
            python_baseline["bundle_registrations"] = python312_bundle_registrations()
        baseline["python"] = python_baseline
        ownership["baseline"] = baseline
        ownership["format"] = 2
    state["ownership"] = ownership
    state["install_complete"] = False
    _write_state(paths, state)
    return ownership


def record_owned_component(paths: Paths, name: str, details: dict[str, object]) -> None:
    state = load_state(paths)
    ownership = state.get("ownership")
    if not isinstance(ownership, dict):
        raise InstallError("安装所有权记录丢失，已停止继续安装")
    installed = ownership.setdefault("installed", {})
    if not isinstance(installed, dict):
        raise InstallError("安装所有权记录损坏，已停止继续安装")
    previous = installed.get(name)
    merged = dict(previous) if isinstance(previous, dict) else {}
    merged.update(details)
    installed[name] = merged
    state["ownership"] = ownership
    _write_state(paths, state)


def record_added_user_path_entries(paths: Paths) -> None:
    state = load_state(paths)
    ownership = state.get("ownership")
    if not isinstance(ownership, dict):
        return
    baseline = ownership.get("baseline")
    baseline = baseline if isinstance(baseline, dict) else {}
    previous = baseline.get("user_path")
    previous = previous if isinstance(previous, list) else []
    before = {_normalise_path_entry(str(entry)) for entry in previous}
    expected = {_normalise_path_entry(str(paths.root / "bin"))}
    installed = ownership.get("installed")
    installed = installed if isinstance(installed, dict) else {}
    for name in ("claude", "vscode"):
        component = installed.get(name)
        component = component if isinstance(component, dict) else {}
        if component.get("installed_by_app") and component.get("path"):
            expected.add(_normalise_path_entry(str(Path(str(component["path"])).parent)))
    added = [
        entry for entry in _user_path_entries()
        if _normalise_path_entry(entry) not in before and _normalise_path_entry(entry) in expected
    ]
    ownership["added_user_path"] = added
    state["ownership"] = ownership
    _write_state(paths, state)


def capture_user_environment(paths: Paths) -> dict[str, str | None]:
    state = load_state(paths)
    existing = state.get("environment_backup")
    if isinstance(existing, dict):
        return {str(name): value if isinstance(value, str) else None for name, value in existing.items()}
    if os.name != "nt":
        return {}
    import winreg

    backup: dict[str, str | None] = {}
    legacy_values = {
        "ANTHROPIC_BASE_URL": PROXY_URL,
        "ANTHROPIC_AUTH_TOKEN": "dummy",
        "ANTHROPIC_MODEL": str(state.get("model") or "deepseek-chat"),
        "ANTHROPIC_SMALL_FAST_MODEL": "deepseek-chat",
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
    } if state else {}
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        for name in GATEWAY_ENV_NAMES:
            try:
                value, _ = winreg.QueryValueEx(key, name)
                value = str(value)
                # A pre-V2.8 state file proves matching V2.7 values were written
                # by this app, not inherited from the user's original setup.
                backup[name] = None if legacy_values.get(name) == value else value
            except FileNotFoundError:
                backup[name] = None
    return backup


def record_environment_backup(paths: Paths, backup: dict[str, str | None]) -> None:
    """Persist the environment rollback point before changing the registry."""
    state = load_state(paths)
    if not isinstance(state.get("environment_backup"), dict):
        state["environment_backup"] = backup
        _write_state(paths, state)


def _matches_configurator_environment(name: str, value: str, state: dict[str, object], token: str) -> bool:
    # V2.8.x could delete its state file before cleaning the registry. These
    # values are app-specific enough to recover that legacy uninstall while
    # V2.9.1 installations still use their exact captured backup above.
    legacy_values: dict[str, set[str]] = {
        "ANTHROPIC_BASE_URL": {PROXY_URL},
        "ANTHROPIC_MODEL": {"deepseek-chat", "deepseek-reasoner"},
        "ANTHROPIC_SMALL_FAST_MODEL": {"deepseek-chat"},
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": {"1"},
        "ANTHROPIC_DEFAULT_OPUS_MODEL": {DEFAULT_REASONING_MODEL},
        "ANTHROPIC_DEFAULT_SONNET_MODEL": {DEFAULT_REASONING_MODEL},
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": {DEFAULT_MODEL},
        "CLAUDE_CODE_SUBAGENT_MODEL": {DEFAULT_MODEL},
        "CLAUDE_CODE_EFFORT_LEVEL": {"max"},
    }
    if name == "ANTHROPIC_AUTH_TOKEN":
        return bool((token and value == token) or value.startswith("sk-local-"))
    return value in legacy_values.get(name, set())


def remove_user_environment(paths: Paths) -> None:
    if os.name != "nt":
        return
    import winreg

    state = load_state(paths)
    backup = state.get("environment_backup")
    backup = backup if isinstance(backup, dict) else {}
    token = read_proxy_token() or ""
    managed_names = GATEWAY_ENV_NAMES
    launcher_dir = paths.root / "bin"
    wanted_path = _normalise_path_entry(str(launcher_dir))
    ownership = state.get("ownership")
    ownership = ownership if isinstance(ownership, dict) else {}
    baseline = ownership.get("baseline")
    baseline = baseline if isinstance(baseline, dict) else {}
    baseline_path = baseline.get("user_path")
    baseline_path = baseline_path if isinstance(baseline_path, list) else []
    baseline_normalised = {_normalise_path_entry(str(entry)) for entry in baseline_path}
    added_entries = ownership.get("added_user_path")
    added_entries = added_entries if isinstance(added_entries, list) else []
    removed_paths = {wanted_path, *(_normalise_path_entry(str(entry)) for entry in added_entries)}
    installed = ownership.get("installed")
    installed = installed if isinstance(installed, dict) else {}
    known = _known_component_paths(paths)
    claude_component = installed.get("claude")
    claude_component = claude_component if isinstance(claude_component, dict) else {}
    vscode_component = installed.get("vscode")
    vscode_component = vscode_component if isinstance(vscode_component, dict) else {}
    candidates: list[Path] = []
    if claude_component.get("installed_by_app"):
        candidates.extend([known["claude_native"].parent])
        if claude_component.get("path"):
            candidates.append(Path(str(claude_component["path"])).parent)
    if vscode_component.get("installed_by_app"):
        candidates.append(known["vscode_program"] / "bin")
        if vscode_component.get("path"):
            candidates.append(Path(str(vscode_component["path"])).parent)
    for candidate in candidates:
        normalised = _normalise_path_entry(str(candidate))
        if normalised not in baseline_normalised:
            removed_paths.add(normalised)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        for name in managed_names:
            try:
                current, _ = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                continue
            # Preserve values the user changed after installation. Legacy
            # app-specific values are recognized even if the old state file
            # was deleted before registry cleanup.
            if not _matches_configurator_environment(name, str(current), state, token):
                continue
            previous = backup.get(name)
            if isinstance(previous, str):
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, previous)
                os.environ[name] = previous
            else:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
                os.environ.pop(name, None)
        try:
            current_path, value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current_path, value_type = "", winreg.REG_EXPAND_SZ
        entries = [entry for entry in str(current_path).split(";") if entry.strip()]
        entries = [entry for entry in entries if _normalise_path_entry(entry) not in removed_paths]
        winreg.SetValueEx(key, "Path", 0, value_type, ";".join(entries))
    process_entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    process_entries = [entry for entry in process_entries if _normalise_path_entry(entry) not in removed_paths]
    os.environ["PATH"] = os.pathsep.join(process_entries)
    broadcast_environment_change()


def port_owner_is_proxy(port: int = 4000, proxy_token: str | None = None) -> bool:
    try:
        token = proxy_token or read_proxy_token() or "dummy"
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/models",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
            return response.status == 200 and isinstance(body.get("data"), list)
    except Exception:
        return False


def is_port_open(port: int = 4000) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def listening_pid(port: int = 4000) -> int | None:
    if os.name != "nt":
        return None
    result = run_command(["netstat.exe", "-ano", "-p", "TCP"], timeout=20)
    if result.returncode:
        return None
    suffix = f":{port}"
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[0].upper() == "TCP" and fields[1].endswith(suffix):
            if fields[3].upper() == "LISTENING" and fields[4].isdigit():
                return int(fields[4])
    return None


def process_executable(pid: int) -> Path | None:
    """Return a process image path using a low-privilege Windows API."""
    if os.name != "nt" or pid <= 0:
        return None
    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return Path(buffer.value)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _proxy_process_record(paths: Paths) -> tuple[int, int]:
    """Read (launcher_pid, listener_pid), accepting the legacy single-PID file."""
    try:
        text = paths.pid.read_text(encoding="utf-8").strip()
        data = json.loads(text)
        if isinstance(data, dict):
            return int(data.get("launcher_pid", 0)), int(data.get("listener_pid", 0))
        legacy = int(data)
        return legacy, legacy
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0, 0


def _write_proxy_process_record(paths: Paths, launcher_pid: int, listener_pid: int = 0) -> None:
    paths.pid.write_text(
        json.dumps({"launcher_pid": launcher_pid, "listener_pid": listener_pid}, separators=(",", ":")),
        encoding="ascii",
    )


def stop_proxy(paths: Paths) -> bool:
    """Stop the recorded proxy tree only when its listener still owns port 4000."""
    launcher_pid, listener_pid = _proxy_process_record(paths)
    if listener_pid <= 0:
        return not is_port_open(4000)
    if os.name != "nt" or listening_pid(4000) != listener_pid:
        return False
    kill_pid = listener_pid
    launcher_path = process_executable(launcher_pid)
    if launcher_path is not None:
        try:
            launcher_path.resolve().relative_to(paths.venv.resolve())
            kill_pid = launcher_pid
        except (OSError, ValueError):
            pass
    result = run_command(["taskkill.exe", "/PID", str(kill_pid), "/T", "/F"], timeout=30)
    if result.returncode:
        return False
    for _ in range(20):
        if not is_port_open(4000):
            paths.pid.unlink(missing_ok=True)
            return True
        time.sleep(0.2)
    return False


def proxy_start_commands(paths: Paths, python: str) -> list[list[str]]:
    """Prefer pip's supported console entry point; retain a direct CLI fallback."""
    options = ["--config", str(paths.config), "--host", "127.0.0.1", "--port", "4000"]
    scripts = Path(python).parent
    commands: list[list[str]] = []
    for name in ("litellm.exe", "litellm.cmd", "litellm"):
        launcher = scripts / name
        if launcher.is_file():
            commands.append([str(launcher), *options])
            break
    commands.append([
        python, "-c", "from litellm.proxy.proxy_cli import run_server; run_server()", *options,
    ])
    return commands


def _hidden_background_startup() -> tuple[int, subprocess.STARTUPINFO | None]:
    """Return Windows process options that never allocate or show a console window."""
    if os.name != "nt":
        return 0, None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    # CREATE_NO_WINDOW is ignored when combined with DETACHED_PROCESS.  A new
    # process group is enough to keep LiteLLM independent from the caller while
    # preserving CREATE_NO_WINDOW, and SW_HIDE provides a second line of defence
    # for console launchers on older Windows builds.
    creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    return creationflags, startupinfo


def start_proxy(paths: Paths, venv_python: str | None = None) -> None:
    proxy_token = ensure_proxy_token()
    if is_port_open(4000):
        if port_owner_is_proxy(4000, proxy_token):
            return
        if port_owner_is_proxy(4000, "dummy") and stop_proxy(paths):
            pass
        else:
            raise InstallError("端口 4000 已被其他程序占用。请关闭占用程序后再试。")
    api_key = read_api_key()
    if not api_key:
        raise InstallError("未找到已保存的 DeepSeek API Key")
    python = venv_python or str(paths.venv / "Scripts/python.exe")
    if not Path(python).is_file():
        raise InstallError("LiteLLM 独立环境尚未安装")
    healthy, reason = verify_litellm_environment(python, env=isolated_pip_environment())
    if not healthy:
        raise InstallError(f"LiteLLM 环境不完整（{reason}）。请点击“一键安装并配置”自动修复。")
    write_litellm_config(paths)
    env = os.environ.copy()
    env["DEEPSEEK_API_KEY"] = api_key
    env["LITELLM_MASTER_KEY"] = proxy_token
    env["LITELLM_LOG"] = "ERROR"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    creationflags, startupinfo = _hidden_background_startup()
    commands = proxy_start_commands(paths, python)
    for attempt, command in enumerate(commands, start=1):
        log_handle = paths.log.open("w" if attempt == 1 else "a", encoding="utf-8")
        try:
            if attempt > 1:
                log_handle.write("\nLiteLLM 标准入口未启动，正在尝试内置备用入口。\n")
                log_handle.flush()
            process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=log_handle, stderr=subprocess.STDOUT,
                env=env, creationflags=creationflags, startupinfo=startupinfo, close_fds=True,
            )
        except OSError as exc:
            log_handle.write(f"无法启动入口：{exc}\n")
            continue
        finally:
            log_handle.close()
        _write_proxy_process_record(paths, process.pid)
        for _ in range(90):
            if process.poll() is not None:
                break
            if port_owner_is_proxy(4000, proxy_token):
                listener = listening_pid(4000)
                if listener is not None:
                    _write_proxy_process_record(paths, process.pid, listener)
                    return
            time.sleep(1)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    detail = proxy_log_tail(paths, api_key)
    paths.pid.unlink(missing_ok=True)
    raise InstallError(f"LiteLLM 后台进程启动失败。\n{detail}" if detail else "LiteLLM 后台进程启动失败，代理没有输出诊断信息")


def proxy_log_tail(paths: Paths, api_key: str = "") -> str:
    try:
        content = paths.log.read_text(encoding="utf-8", errors="replace")[-6000:]
    except OSError:
        return ""
    content = redact(content, [api_key])
    content = re.sub(r"(?i)(authorization|api[_ -]?key)(\s*[:=]\s*)\S+", r"\1\2***", content)
    lines = [_clean_progress_line(line) for line in content.splitlines() if line.strip()]
    return "代理日志末尾：\n" + "\n".join(lines[-12:]) if lines else ""


def classify_api_response(status: int, body: str) -> tuple[bool, str]:
    lowered = body.lower()
    if status < 400:
        return True, "连接成功，DeepSeek API 可用"
    balance_markers = ("insufficient balance", "余额不足", "402")
    if any(marker in lowered for marker in balance_markers):
        return True, "连接成功，但 API 余额不足"
    if status in (401, 403) or "authentication" in lowered or "invalid api key" in lowered:
        return False, "连接失败：API Key 无效或没有权限"
    return False, f"连接失败：DeepSeek 返回 HTTP {status}"


def test_connection(model: str) -> tuple[bool, str]:
    api_key = read_api_key()
    if not api_key:
        return False, "请先填写并保存 DeepSeek API Key"
    if model not in SUPPORTED_MODELS:
        return False, f"不支持的 DeepSeek 模型：{model}"
    api_model = "deepseek-v4-pro" if model.startswith("deepseek-v4-pro") else "deepseek-v4-flash"
    payload = json.dumps({"model": api_model, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 1}).encode()
    endpoint = f"{DEEPSEEK_BASE_URL}/chat/completions"
    request = urllib.request.Request(
        endpoint, data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return classify_api_response(response.status, response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return classify_api_response(exc.code, exc.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"连接失败：请检查网络设置（{exc}）"


def launch_claude(paths: Paths) -> None:
    state = load_state(paths)
    claude = state.get("claude") or command_path("claude")
    if not claude or not Path(claude).exists():
        raise InstallError("未找到 Claude Code，请先完成一键安装")
    api_key = read_api_key()
    if not api_key:
        raise InstallError("未找到已保存的 DeepSeek API Key，请先完成配置")
    env = environment_with_git(paths)
    env.update(gateway_environment(str(state.get("model") or DEFAULT_MODEL), api_key))
    commands = claude_terminal_commands(claude)
    last_error: OSError | None = None
    for command, flags in commands:
        try:
            subprocess.Popen(command, env=env, cwd=str(Path.home()), creationflags=flags)
            return
        except OSError as exc:
            last_error = exc
    raise InstallError(f"无法打开 Claude Code 终端：{last_error}")


def run_claude_inline(paths: Paths, arguments: list[str] | None = None) -> int:
    """Run Claude in the current terminal and inject the API key only into its child."""
    state = load_state(paths)
    claude = str(state.get("claude") or command_path("claude") or "")
    if not claude or not Path(claude).exists():
        raise InstallError("未找到 Claude Code，请先完成一键安装")
    api_key = read_api_key()
    if not api_key:
        raise InstallError("未找到已保存的 DeepSeek API Key，请先完成配置")
    env = environment_with_git(paths)
    env.update(gateway_environment(str(state.get("model") or DEFAULT_MODEL), api_key))
    try:
        result = subprocess.run([claude, *(arguments or [])], env=env, check=False)
    except OSError as exc:
        raise InstallError(f"无法启动 Claude Code：{exc}") from exc
    return int(result.returncode)


def claude_terminal_commands(claude: str) -> list[tuple[list[str], int]]:
    """Prefer Windows Terminal for Unicode/ANSI rendering, then use UTF-8 CMD."""
    commands: list[tuple[list[str], int]] = []
    terminal = command_path("wt")
    quoted_claude = subprocess.list2cmdline([claude])
    cmd_line = f"chcp 65001>nul & call {quoted_claude}"
    if terminal:
        commands.append(([
            terminal, "new-tab", "--title", "Claude Code", "cmd.exe", "/d", "/k", cmd_line,
        ], subprocess.CREATE_NO_WINDOW))
    commands.append(([
        "cmd.exe", "/d", "/k", cmd_line,
    ], subprocess.CREATE_NEW_CONSOLE))
    return commands


def launch_vscode(paths: Paths) -> None:
    state = load_state(paths)
    code = state.get("code") or command_path("code")
    if not code or not Path(code).exists():
        raise InstallError("未找到 VS Code，请先完成一键安装")
    api_key = read_api_key()
    if not api_key:
        raise InstallError("未找到已保存的 DeepSeek API Key，请先完成配置")
    env = environment_with_git(paths)
    env.update(gateway_environment(str(state.get("model") or DEFAULT_MODEL), api_key))
    subprocess.Popen([code, str(Path.home())], env=env, creationflags=subprocess.CREATE_NO_WINDOW)


def desktop_shortcut_path() -> Path:
    desktop_buffer = ctypes.create_unicode_buffer(260)
    desktop = Path.home() / "Desktop"
    if os.name == "nt":
        try:
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, desktop_buffer) == 0:
                desktop = Path(desktop_buffer.value)
        except Exception:
            pass
    return desktop / "Claude Code + DeepSeek.lnk"


def legacy_desktop_shortcut_path() -> Path:
    return desktop_shortcut_path().with_name("Claude Code + DeepSeek 一键配置器.lnk")


def create_desktop_shortcut(executable: str, arguments: str = "") -> bool:
    if os.name != "nt":
        return False
    shortcut = desktop_shortcut_path()
    desktop = shortcut.parent
    desktop.mkdir(parents=True, exist_ok=True)
    shortcut_env = os.environ.copy()
    shortcut_env["CLAUDE_DEEPSEEK_SHORTCUT_PATH"] = str(shortcut)
    shortcut_env["CLAUDE_DEEPSEEK_SHORTCUT_TARGET"] = executable
    shortcut_env["CLAUDE_DEEPSEEK_SHORTCUT_ARGUMENTS"] = arguments
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:CLAUDE_DEEPSEEK_SHORTCUT_PATH);"
        "$s.TargetPath=$env:CLAUDE_DEEPSEEK_SHORTCUT_TARGET;"
        "$s.Arguments=$env:CLAUDE_DEEPSEEK_SHORTCUT_ARGUMENTS;"
        "$s.WorkingDirectory=(Split-Path $env:CLAUDE_DEEPSEEK_SHORTCUT_TARGET);"
        "$s.IconLocation=$env:CLAUDE_DEEPSEEK_SHORTCUT_TARGET+',0';"
        "$s.Description='打开 Claude Code + DeepSeek 配置器';$s.Save()"
    )
    try:
        result = run_command(
            ["powershell.exe", "-NoProfile", "-Command", script],
            timeout=30, env=shortcut_env,
        )
        return result.returncode == 0 and shortcut.exists()
    except InstallError:
        return False


def system_status(paths: Paths) -> dict[str, object]:
    state = load_state(paths)
    payload_ok, payload_detail = verify_offline_payload(offline_root(paths))
    return {
        "version": APP_VERSION,
        "windows": windows_compatibility_label(),
        "architecture": platform.machine(),
        "api_key_saved": bool(read_api_key()),
        "connection_mode": state.get("connection_mode", "direct-anthropic"),
        "gateway_url": state.get("gateway_url", DEEPSEEK_ANTHROPIC_URL),
        "model": state.get("model", "尚未配置"),
        "git_ready": bool(find_git_bash(paths)),
        "claude_ready": bool(state.get("claude") or command_path("claude")),
        "vscode_ready": bool(state.get("code") or command_path("code")),
        "payload_ready": payload_ok,
        "payload_detail": payload_detail,
        "offline_assets": offline_asset_summary(offline_root(paths)),
    }


def _version_key(value: str) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)+", value or "")
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()


def _claude_version(path: str, env: dict[str, str] | None = None) -> str:
    result = run_command([path, "--version"], timeout=30, env=env)
    if result.returncode:
        return ""
    match = re.search(r"\d+(?:\.\d+)+", result.stdout or "")
    return match.group(0) if match else (result.stdout or "").strip()


def _fetch_json(url: str, timeout: float = 8.0) -> dict[str, object]:
    request = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise InstallError("更新源返回格式无效")
    return value


def check_updates(paths: Paths, timeout: float = 8.0) -> dict[str, object]:
    """Read-only update check. Existing external components are never modified."""
    state = load_state(paths)
    claude_component = _component_record(state, "claude")
    claude_path = str(claude_component.get("path") or state.get("claude") or command_path("claude") or "")
    claude_env = managed_npm_environment(paths) if claude_component.get("method") == "managed-npm" else None
    current_claude = _claude_version(claude_path, claude_env) if claude_path else ""
    latest_claude = ""
    registry_used = ""
    errors: list[str] = []
    for name in ("npm_mirror", "npm_official"):
        url = NPM_REGISTRIES[name] + "/@anthropic-ai%2Fclaude-code"
        try:
            metadata = _fetch_json(url, timeout)
            tags = metadata.get("dist-tags")
            tags = tags if isinstance(tags, dict) else {}
            latest_claude = str(tags.get("stable") or tags.get("latest") or "")
            if latest_claude:
                registry_used = NPM_REGISTRIES[name]
                break
        except (InstallError, urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            errors.append(f"{name}: {_clean_progress_line(str(exc))}")

    app_update: dict[str, object] = {
        "current": APP_VERSION, "latest": "", "available": False,
        "configured": False, "message": "正式签名更新源尚未配置",
    }
    manifest_url = os.environ.get(UPDATE_MANIFEST_ENV, "").strip()
    if manifest_url:
        app_update["configured"] = True
        try:
            manifest = _fetch_json(manifest_url, timeout)
            latest = str(manifest.get("version") or "")
            required = all(str(manifest.get(name) or "") for name in ("url", "sha256", "publisher"))
            if not latest or not required:
                raise InstallError("配置器更新清单缺少 version/url/sha256/publisher")
            app_update.update({
                "latest": latest,
                "available": _version_key(latest) > _version_key(APP_VERSION),
                "message": str(manifest.get("notes") or "已读取签名更新清单"),
                "manifest": manifest,
            })
        except Exception as exc:
            app_update["message"] = f"检查失败：{_clean_progress_line(str(exc))}"

    managed = claude_component.get("method") == "managed-npm" and bool(claude_component.get("installed_by_app"))
    return {
        "configurator": app_update,
        "claude": {
            "current": current_claude or "未知",
            "latest": latest_claude or "未知",
            "available": bool(
                current_claude and latest_claude and _version_key(latest_claude) > _version_key(current_claude)
            ),
            "managed": managed,
            "source": str(claude_component.get("registry") or registry_used or "现有外部安装"),
            "message": "可由配置器安全更新" if managed else "外部安装仅提示，不会擅自更新",
        },
        "runtime": {
            "node": NODE_VERSION,
            "git": GIT_VERSION,
            "gateway": "DeepSeek Anthropic 直连",
            "message": "Git 与 Node.js 均为隔离、哈希锁定的本地组件",
        },
        "errors": errors,
    }


def update_managed_claude(paths: Paths, progress: Progress) -> str:
    state = load_state(paths)
    component = _component_record(state, "claude")
    if component.get("method") != "managed-npm" or not component.get("installed_by_app"):
        raise InstallError("当前 Claude Code 不是配置器管理的版本，只显示更新提示，不会擅自修改")
    npm = str(component.get("manager") or (managed_node_dir(paths) / "npm.cmd"))
    claude = str(component.get("path") or managed_claude_path(paths) or "")
    if not Path(npm).is_file() or not claude:
        raise InstallError("受管理 npm 运行环境不完整，请重新运行安装修复")
    env = managed_npm_environment(paths)
    previous = _claude_version(claude, env)
    registry = str(component.get("registry") or NPM_REGISTRIES["npm_mirror"])
    version = resolve_claude_npm_version(npm, registry, env)
    progress("检查更新", f"已确认 Windows 平台包同步，正在通过 {registry} 更新到 {version}")
    result = run_command([
        npm, "install", "-g", f"{NPM_PACKAGE}@{version}", f"--registry={registry}",
        "--include=optional", "--no-audit", "--no-fund",
    ], timeout=1200, env=env)
    record_install_attempt(paths, "managed-update", result)
    if result.returncode:
        if previous:
            run_command([
                npm, "install", "-g", f"{NPM_PACKAGE}@{previous}", f"--registry={registry}",
                "--include=optional", "--no-audit", "--no-fund",
            ], timeout=1200, env=env)
        raise InstallError(_source_failure("managed-update", "Claude Code 更新", "更新失败，已尝试恢复原版本", result)["message"])
    current = _claude_version(claude, env)
    if not current:
        if previous:
            run_command([
                npm, "install", "-g", f"{NPM_PACKAGE}@{previous}", f"--registry={registry}",
                "--include=optional", "--no-audit", "--no-fund",
            ], timeout=1200, env=env)
        raise InstallError("更新后验证失败，已尝试恢复原版本")
    component["version"] = current
    component["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_state(paths, state)
    return f"Claude Code 已从 {previous or '未知版本'} 更新到 {current}。"


def prepare_configurator_update(paths: Paths, manifest: dict[str, object], progress: Progress) -> Path:
    version = str(manifest.get("version") or "")
    url = str(manifest.get("url") or "")
    expected_hash = str(manifest.get("sha256") or "").lower()
    publisher = str(manifest.get("publisher") or "")
    if _version_key(version) <= _version_key(APP_VERSION):
        raise InstallError("更新清单没有比当前配置器更高的版本")
    if urllib.parse.urlsplit(url).scheme.lower() != "https":
        raise InstallError("配置器更新只允许 HTTPS 下载地址")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or not publisher:
        raise InstallError("更新清单的 SHA-256 或签名发布者无效")
    update_dir = paths.root / "updates"
    update_dir.mkdir(parents=True, exist_ok=True)
    staged = update_dir / f"ClaudeDeepSeekConfigurator-{version}.exe"
    progress("检查更新", f"正在下载配置器 {version}")
    download(url, staged, progress, attempts=3)
    if _sha256(staged).lower() != expected_hash:
        staged.unlink(missing_ok=True)
        raise InstallError("配置器更新包 SHA-256 与签名清单不一致")
    verify_authenticode(staged, publisher)
    state = load_state(paths)
    state["pending_app_update"] = {
        "version": version, "path": str(staged), "sha256": expected_hash,
        "publisher": publisher, "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_state(paths, state)
    return staged


def schedule_configurator_update(paths: Paths, staged: Path) -> None:
    """Replace the stable local copy after this process exits, retaining one rollback backup."""
    pending = load_state(paths).get("pending_app_update")
    pending = pending if isinstance(pending, dict) else {}
    if Path(str(pending.get("path") or "")) != staged or not staged.is_file():
        raise InstallError("配置器更新包尚未通过本次会话验证")
    target = program_root(paths) / "ClaudeDeepSeekConfigurator.exe"
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not powershell:
        raise InstallError("找不到 PowerShell，无法安全替换正在运行的配置器")
    env = os.environ.copy()
    env.update({
        "CLAUDE_DEEPSEEK_UPDATE_PID": str(os.getpid()),
        "CLAUDE_DEEPSEEK_UPDATE_SOURCE": str(staged),
        "CLAUDE_DEEPSEEK_UPDATE_TARGET": str(target),
    })
    script = (
        "$ErrorActionPreference='Stop';"
        "$p=[int]$env:CLAUDE_DEEPSEEK_UPDATE_PID;"
        "$s=$env:CLAUDE_DEEPSEEK_UPDATE_SOURCE;$t=$env:CLAUDE_DEEPSEEK_UPDATE_TARGET;"
        "Wait-Process -Id $p -ErrorAction SilentlyContinue;"
        "$b=$t+'.bak';$n=$t+'.new';"
        "try {"
        "if(Test-Path -LiteralPath $t){Copy-Item -LiteralPath $t -Destination $b -Force};"
        "Copy-Item -LiteralPath $s -Destination $n -Force;Move-Item -LiteralPath $n -Destination $t -Force;"
        "Start-Process -FilePath $t"
        "} catch {if(Test-Path -LiteralPath $b){Copy-Item -LiteralPath $b -Destination $t -Force};exit 1}"
    )
    subprocess.Popen(
        [powershell, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
        env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def export_diagnostics(paths: Paths, destination: Path) -> Path:
    """Export a support archive without credentials, tokens, or raw environment values."""
    destination = destination.with_suffix(".zip") if destination.suffix.lower() != ".zip" else destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    api_key = read_api_key() or ""
    proxy_token = read_proxy_token() or ""
    state = load_state(paths)
    safe_state = {
        name: value for name, value in state.items()
        if name not in {"environment_backup", "ownership"}
    }
    ownership = state.get("ownership")
    ownership = ownership if isinstance(ownership, dict) else {}
    installed = ownership.get("installed")
    installed = installed if isinstance(installed, dict) else {}
    safe_state["ownership_summary"] = {
        str(name): {
            "installed_by_app": bool(component.get("installed_by_app")),
            "method": str(component.get("method", "")),
            "status": str(component.get("status", "")),
            "rolled_back": bool(component.get("rolled_back")),
        }
        for name, component in installed.items() if isinstance(component, dict)
    }
    try:
        raw_log = paths.log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raw_log = "代理日志不存在。"
    safe_log = redact(raw_log, (api_key, proxy_token))
    safe_log = re.sub(r"(?i)(authorization|api[_ -]?key|master[_ -]?key)(\s*[:=]\s*)\S+", r"\1\2***", safe_log)
    python_logs: dict[str, str] = {}
    for log_path in paths.root.glob("python-*.log") if paths.root.exists() else ():
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        content = redact(content, (api_key, proxy_token))
        python_logs[log_path.name + ".txt"] = content
    if not python_logs:
        python_logs["python-install.log.txt"] = "Python 安装、修复或卸载日志不存在。"
    manifest = load_offline_manifest()
    manifest_summary = {
        "format": manifest.get("format"),
        "created_at": manifest.get("created_at"),
        "python_version": manifest.get("python_version"),
        "litellm_version": manifest.get("litellm_version"),
        "verified_file_count": len(manifest.get("files", {})),
    }
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("summary.json", json.dumps(system_status(paths), ensure_ascii=False, indent=2))
        archive.writestr("state.json", json.dumps(safe_state, ensure_ascii=False, indent=2))
        archive.writestr("offline-summary.json", json.dumps(manifest_summary, ensure_ascii=False, indent=2))
        archive.writestr("proxy.log.txt", safe_log)
        for name, content in python_logs.items():
            archive.writestr(name, content)
        archive.writestr("README.txt", "此诊断包不包含 DeepSeek API Key、代理访问令牌或用户环境变量值。\n")
    return destination


def _validate_managed_root(paths: Paths) -> Path:
    expected = Paths.default().root.resolve()
    actual = paths.root.resolve()
    if actual != expected or actual.name != APP_NAME:
        raise InstallError(f"卸载目录校验失败，已拒绝删除：{actual}")
    return actual


def _validate_program_root(paths: Paths) -> Path:
    expected = program_root(Paths.default()).resolve()
    actual = program_root(paths).resolve()
    if actual != expected:
        raise InstallError(f"程序目录不属于配置器，已拒绝删除：{actual}")
    return actual


def _schedule_root_removal(root: Path) -> None:
    script = (
        "$targetPid=[int]$args[0];$target=$args[1];"
        "Wait-Process -Id $targetPid -Timeout 90 -ErrorAction SilentlyContinue;"
        "for($i=0;$i -lt 30;$i++){"
        "Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue;"
        "if(-not (Test-Path -LiteralPath $target)){exit 0};"
        "Start-Sleep -Milliseconds 500};"
        "$shell=New-Object -ComObject WScript.Shell;"
        "$null=$shell.Popup('配置器文件未能完全删除。请重启电脑后再次运行卸载。',0,'卸载未完成',16);exit 1"
    )
    flags, startupinfo = _hidden_background_startup()
    process = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script, str(os.getpid()), str(root)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags, startupinfo=startupinfo, close_fds=True,
    )
    if process.poll() is not None:
        raise InstallError("无法启动卸载清理程序，配置器文件尚未删除")


def _remove_tree(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=False)
    elif path.exists():
        path.unlink()


def _remove_empty_parents(path: Path, stop: Path) -> None:
    current = path
    stop = stop.resolve()
    while current.exists() and current != stop:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _component_record(state: dict[str, object], name: str) -> dict[str, object]:
    ownership = state.get("ownership")
    ownership = ownership if isinstance(ownership, dict) else {}
    installed = ownership.get("installed")
    installed = installed if isinstance(installed, dict) else {}
    component = installed.get(name)
    return component if isinstance(component, dict) else {}


def _mark_component_rolled_back(paths: Paths, state: dict[str, object], name: str) -> None:
    component = _component_record(state, name)
    if not component:
        return
    component["rolled_back"] = True
    _write_state(paths, state)


def _ownership_baseline(state: dict[str, object], name: str) -> dict[str, object]:
    ownership = state.get("ownership")
    ownership = ownership if isinstance(ownership, dict) else {}
    baseline = ownership.get("baseline")
    baseline = baseline if isinstance(baseline, dict) else {}
    component = baseline.get(name)
    return component if isinstance(component, dict) else {}


def migrate_legacy_owned_artifacts(paths: Paths, state: dict[str, object]) -> dict[str, object]:
    """Adopt only legacy artifacts whose location proves configurator ownership."""
    registration = app_owned_python_registration(paths)
    bundles = python312_bundle_registrations()
    legacy_path = str(state.get("python") or "")
    legacy_path_proves_ownership = bool(legacy_path and _path_is_within(legacy_path, paths.root))
    if not registration and not (bundles and legacy_path_proves_ownership):
        return state
    ownership = state.get("ownership")
    ownership = ownership if isinstance(ownership, dict) else {}
    installed = ownership.get("installed")
    installed = installed if isinstance(installed, dict) else {}
    python_component = installed.get("python")
    if not isinstance(python_component, dict):
        installer = paths.root / "installers" / f"python-{PYTHON_VERSION}-amd64.exe"
        if not installer.is_file():
            prepare_python_installer(paths)
        installed["python"] = {
            "installed_by_app": True,
            "status": "legacy-adopted",
            "method": "legacy-repair",
            "path": registration.get("executable", str(paths.runtime / "python.exe")) if registration else legacy_path,
            "installer": str(installer),
            "owned_bundle_keys": [item.get("key", "") for item in bundles],
        }
    baseline = ownership.get("baseline")
    baseline = baseline if isinstance(baseline, dict) else {}
    python_baseline = baseline.get("python")
    python_baseline = python_baseline if isinstance(python_baseline, dict) else {}
    python_baseline.setdefault("present", False)
    python_baseline.setdefault("path", "")
    if registration:
        python_baseline["legacy_owned_registration"] = registration
    python_baseline["legacy_owned_bundle_keys"] = [item.get("key", "") for item in bundles]
    baseline["python"] = python_baseline
    ownership.update({"format": 2, "baseline": baseline, "installed": installed})
    state["ownership"] = ownership
    state.setdefault("install_complete", False)
    _write_state(paths, state)
    return state


def legacy_external_cleanup_candidates(paths: Paths) -> dict[str, str]:
    """List ambiguous external components from pre-ownership releases."""
    state = load_state(paths)
    if bool(state.get("install_complete")):
        return {}
    candidates: dict[str, str] = {}
    claude = command_path("claude")
    code = command_path("code")
    if claude and not _component_record(state, "claude").get("installed_by_app"):
        candidates["claude"] = claude
    if code and not _component_record(state, "vscode").get("installed_by_app"):
        candidates["vscode"] = code
    if vscode_extension_is_installed() and not _component_record(state, "vscode_extension").get("installed_by_app"):
        candidates["vscode_extension"] = VSCODE_EXTENSION
    return candidates


def adopt_confirmed_legacy_external_components(
    paths: Paths, state: dict[str, object], candidates: dict[str, str],
) -> dict[str, object]:
    """Adopt ambiguous V2.8.x components only after explicit user confirmation."""
    if not candidates:
        return state
    ownership = state.get("ownership")
    ownership = ownership if isinstance(ownership, dict) else {}
    baseline = ownership.get("baseline")
    baseline = baseline if isinstance(baseline, dict) else {}
    installed = ownership.get("installed")
    installed = installed if isinstance(installed, dict) else {}
    known = _known_component_paths(paths)

    if "claude" in candidates:
        path = candidates["claude"]
        suffix = Path(path).suffix.lower()
        method = "npm" if suffix in {".cmd", ".ps1"} else "native"
        installed["claude"] = {
            "installed_by_app": True, "status": "legacy-user-confirmed",
            "method": method, "path": path,
            "manager": (command_path("npm") or command_path("npm.cmd") or "") if method == "npm" else "",
        }
        baseline["claude"] = {
            "present": False, "path": "", "native_existed": False,
            "share_existed": False, "data_existed": False, "npm_existed": False,
        }
    if "vscode" in candidates:
        installed["vscode"] = {
            "installed_by_app": True, "status": "legacy-user-confirmed",
            "method": "auto", "path": candidates["vscode"],
            "uninstaller": str(known["vscode_uninstaller"]),
        }
        baseline["vscode"] = {
            "present": False, "path": "", "program_existed": False,
            "data_existed": False, "local_data_existed": False,
            "extensions_dir_existed": False,
        }
    if "vscode_extension" in candidates:
        installed["vscode_extension"] = {
            "installed_by_app": True, "status": "legacy-user-confirmed", "id": VSCODE_EXTENSION,
        }
        baseline["vscode_extension"] = {"present": False}
    ownership.update({"format": 2, "baseline": baseline, "installed": installed})
    state["ownership"] = ownership
    _write_state(paths, state)
    return state


def _remove_owned_vscode_extension_files(paths: Paths, state: dict[str, object]) -> None:
    root = _known_component_paths(paths)["vscode_extensions"]
    if not root.exists():
        return
    for folder in root.glob("anthropic.claude-code-*"):
        _remove_tree(folder)
    registry = root / "extensions.json"
    try:
        entries = json.loads(registry.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        entries = None
    if isinstance(entries, list):
        filtered = []
        for entry in entries:
            identifier = entry.get("identifier", {}) if isinstance(entry, dict) else {}
            if str(identifier.get("id", "")).casefold() != VSCODE_EXTENSION.casefold():
                filtered.append(entry)
        if len(filtered) != len(entries):
            registry.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")


def _uninstall_owned_vscode_extension(paths: Paths, state: dict[str, object]) -> None:
    component = _component_record(state, "vscode_extension")
    if not component.get("installed_by_app") or component.get("rolled_back"):
        return
    vscode = _component_record(state, "vscode")
    code = str(vscode.get("path") or state.get("code") or command_path("code") or "")
    if code and Path(code).exists():
        result = run_command([code, "--uninstall-extension", VSCODE_EXTENSION], timeout=180)
        if result.returncode and vscode_extension_is_installed():
            raise InstallError(f"Anthropic VS Code 扩展卸载失败：{_clean_progress_line(result.stdout)[-500:]}")
    else:
        _remove_owned_vscode_extension_files(paths, state)
    if vscode_extension_is_installed():
        raise InstallError("Anthropic VS Code 扩展仍在使用中。请完全关闭 VS Code 后再次卸载。")
    _mark_component_rolled_back(paths, state, "vscode_extension")


def _uninstall_owned_vscode(paths: Paths, state: dict[str, object]) -> None:
    component = _component_record(state, "vscode")
    if not component.get("installed_by_app") or component.get("rolled_back"):
        return
    method = str(component.get("method", ""))
    if method == "winget":
        manager = str(component.get("manager") or command_path("winget") or "")
        if not manager:
            raise InstallError("找不到 WinGet，无法撤销由配置器安装的 VS Code")
        result = run_command([
            manager, "uninstall", "--id", "Microsoft.VisualStudioCode", "-e", "--scope", "user",
            "--silent", "--accept-source-agreements",
        ], timeout=1200)
        require_success(result, "VS Code 卸载")
    else:
        uninstaller = Path(str(component.get("uninstaller") or _known_component_paths(paths)["vscode_uninstaller"]))
        if uninstaller.is_file():
            require_success(run_command([
                str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            ], timeout=1200), "VS Code 卸载")
        elif _known_component_paths(paths)["vscode_program"].exists():
            manager = command_path("winget")
            if not manager:
                raise InstallError("找不到 VS Code 官方卸载程序，已保留安装记录以便重试")
            require_success(run_command([
                manager, "uninstall", "--id", "Microsoft.VisualStudioCode", "-e", "--scope", "user",
                "--silent", "--accept-source-agreements",
            ], timeout=1200), "VS Code 卸载")

    baseline = _ownership_baseline(state, "vscode")
    known = _known_component_paths(paths)
    cleanup = (
        ("program_existed", known["vscode_program"]),
        ("data_existed", known["vscode_data"]),
        ("local_data_existed", known["vscode_local_data"]),
        ("extensions_dir_existed", known["vscode_extensions"]),
    )
    for marker, target in cleanup:
        if not baseline.get(marker) and target.exists():
            _remove_tree(target)
        if not baseline.get(marker) and target.exists():
            raise InstallError(f"VS Code 卸载后仍有配置器新增内容未删除：{target}")
    _mark_component_rolled_back(paths, state, "vscode")


def _uninstall_owned_claude(paths: Paths, state: dict[str, object]) -> None:
    component = _component_record(state, "claude")
    if not component.get("installed_by_app") or component.get("rolled_back"):
        return
    method = str(component.get("method", ""))
    baseline = _ownership_baseline(state, "claude")
    known = _known_component_paths(paths)
    if method in {"auto", "pending"} and not baseline.get("npm_existed") and known["claude_npm"].exists():
        manager = command_path("npm") or command_path("npm.cmd")
        if manager:
            require_success(run_command([
                manager, "uninstall", "-g", "@anthropic-ai/claude-code",
            ], timeout=1200), "Claude Code npm 卸载")
    if method in {"auto", "pending"} and not baseline.get("managed_existed"):
        pending_managed = known.get("claude_managed", managed_npm_prefix(paths))
        if pending_managed.exists():
            _remove_tree(pending_managed)
    if method == "npm":
        manager = str(component.get("manager") or command_path("npm") or command_path("npm.cmd") or "")
        if not manager:
            raise InstallError("找不到 npm，无法撤销由配置器安装的 Claude Code")
        require_success(run_command([
            manager, "uninstall", "-g", "@anthropic-ai/claude-code",
        ], timeout=1200), "Claude Code npm 卸载")
    elif method == "managed-npm":
        managed_root = known.get("claude_managed", managed_npm_prefix(paths))
        if not baseline.get("managed_existed") and managed_root.exists():
            _remove_tree(managed_root)
    elif method == "winget":
        manager = str(component.get("manager") or command_path("winget") or "")
        if not manager:
            raise InstallError("找不到 WinGet，无法撤销由配置器安装的 Claude Code")
        require_success(run_command([
            manager, "uninstall", "--id", "Anthropic.ClaudeCode", "-e", "--silent",
            "--accept-source-agreements",
        ], timeout=1200), "Claude Code 卸载")

    if not baseline.get("native_existed") and known["claude_native"].exists():
        _remove_tree(known["claude_native"])
        _remove_empty_parents(known["claude_native"].parent, Path.home())
    if not baseline.get("share_existed") and known["claude_share"].exists():
        _remove_tree(known["claude_share"])
        _remove_empty_parents(known["claude_share"].parent, Path.home())
    if not baseline.get("data_existed") and known["claude_data"].exists():
        _remove_tree(known["claude_data"])
    node_managed = known.get("node_managed", managed_node_dir(paths))
    npm_cache = known.get("npm_cache", paths.root / "cache" / "npm")
    claude_managed = known.get("claude_managed", managed_npm_prefix(paths))
    if not baseline.get("managed_node_existed") and node_managed.exists():
        _remove_tree(node_managed)
    if npm_cache.exists():
        _remove_tree(npm_cache)
    for marker, target in (
        ("native_existed", known["claude_native"]),
        ("share_existed", known["claude_share"]),
        ("data_existed", known["claude_data"]),
        ("npm_existed", known["claude_npm"]),
        ("managed_existed", claude_managed),
        ("managed_node_existed", node_managed),
    ):
        if not baseline.get(marker) and target.exists():
            raise InstallError(f"Claude Code 卸载后仍有配置器新增内容未删除：{target}")
    _mark_component_rolled_back(paths, state, "claude")


def _uninstall_owned_python(paths: Paths, state: dict[str, object]) -> None:
    component = _component_record(state, "python")
    if not component.get("installed_by_app") or component.get("rolled_back"):
        return
    registration = app_owned_python_registration(paths)
    baseline = _ownership_baseline(state, "python")
    baseline_bundles = baseline.get("bundle_registrations")
    baseline_bundles = baseline_bundles if isinstance(baseline_bundles, list) else []
    legacy_keys = baseline.get("legacy_owned_bundle_keys")
    legacy_keys = legacy_keys if isinstance(legacy_keys, list) else []
    component_keys = component.get("owned_bundle_keys")
    component_keys = component_keys if isinstance(component_keys, list) else []
    explicitly_owned_keys = {str(key).casefold() for key in [*legacy_keys, *component_keys] if key}
    baseline_keys = {
        str(item.get("key", "")).casefold() for item in baseline_bundles
        if isinstance(item, dict) and str(item.get("key", "")).casefold() not in explicitly_owned_keys
    }

    def owned_bundles() -> list[dict[str, str]]:
        return [
            item for item in python312_bundle_registrations()
            if str(item.get("key", "")).casefold() not in baseline_keys
        ]

    if not registration and not owned_bundles():
        if paths.runtime.exists():
            _remove_tree(paths.runtime)
        _mark_component_rolled_back(paths, state, "python")
        return
    installer = Path(str(component.get("installer") or ""))
    if not installer.is_file():
        installer = prepare_python_installer(paths)
    uninstall_log = paths.root / "python-uninstall.log"
    result = run_command([
        str(installer), "/uninstall", "/quiet", "/log", str(uninstall_log),
    ], timeout=1200)
    if result.returncode or app_owned_python_registration(paths) or owned_bundles():
        repair_log = paths.root / "python-uninstall-repair.log"
        repair = run_command([
            str(installer), "/repair", "/quiet", "/log", str(repair_log),
        ], timeout=1200)
        if repair.returncode:
            raise InstallError(
                "配置器安装的 Python 3.12 卸载失败，自动修复也未完成。"
                f"已保留现场供重试：{uninstall_log}；{repair_log}"
            )
        result = run_command([
            str(installer), "/uninstall", "/quiet", "/log", str(uninstall_log),
        ], timeout=1200)
    require_success(result, "Python 3.12 卸载")
    if app_owned_python_registration(paths):
        raise InstallError("Python 3.12 文件虽已处理，但 Windows 安装登记仍存在，卸载尚未完成")
    if owned_bundles():
        raise InstallError("Python 3.12 文件虽已处理，但 Windows“已安装的应用”登记仍存在，卸载尚未完成")
    if paths.runtime.exists():
        _remove_tree(paths.runtime)
    if paths.runtime.exists():
        raise InstallError(f"Python 3.12 私有运行目录仍未删除：{paths.runtime}")
    _mark_component_rolled_back(paths, state, "python")


def rollback_owned_components(paths: Paths, state: dict[str, object]) -> None:
    """Reverse external installs in dependency order, preserving the baseline."""
    _uninstall_owned_vscode_extension(paths, state)
    _uninstall_owned_vscode(paths, state)
    _uninstall_owned_claude(paths, state)
    _uninstall_owned_python(paths, state)


def _restore_desktop_shortcuts(state: dict[str, object]) -> None:
    baseline = _ownership_baseline(state, "desktop_shortcut")
    for label, shortcut in (
        ("primary", desktop_shortcut_path()),
        ("legacy", legacy_desktop_shortcut_path()),
    ):
        existed = bool(baseline.get(f"{label}_existed"))
        backup = Path(str(baseline.get(f"{label}_backup") or ""))
        if existed and backup.is_file():
            shortcut.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, shortcut)
        elif not existed:
            shortcut.unlink(missing_ok=True)


def verify_uninstall_outcome(paths: Paths, state: dict[str, object]) -> None:
    """Refuse to claim success while app-controlled persistent state remains."""
    remaining: list[str] = []
    if read_api_key() is not None:
        remaining.append("Windows 凭据管理器中的 DeepSeek API Key")
    if read_proxy_token() is not None:
        remaining.append("Windows 凭据管理器中的代理令牌")

    baseline = _ownership_baseline(state, "desktop_shortcut")
    for label, shortcut in (
        ("primary", desktop_shortcut_path()),
        ("legacy", legacy_desktop_shortcut_path()),
    ):
        if not baseline.get(f"{label}_existed") and shortcut.exists():
            remaining.append(str(shortcut))

    if os.name == "nt":
        import winreg
        backup = state.get("environment_backup")
        backup = backup if isinstance(backup, dict) else {}
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            for name in GATEWAY_ENV_NAMES:
                expected = backup.get(name)
                try:
                    current = str(winreg.QueryValueEx(key, name)[0])
                except FileNotFoundError:
                    current = None
                if isinstance(expected, str):
                    if current != expected:
                        remaining.append(f"环境变量 {name} 未恢复")
                elif current is not None:
                    # Values changed by the user after installation are
                    # intentionally preserved and are not configurator residue.
                    token = read_proxy_token() or ""
                    if _matches_configurator_environment(name, current, state, token):
                        remaining.append(f"环境变量 {name}")
    if remaining:
        raise InstallError("卸载验证未通过，仍检测到：" + "；".join(remaining))


def uninstall_all(paths: Paths, remove_confirmed_legacy_external: bool = False) -> str:
    """Restore the pre-install baseline and remove every component added by this app."""
    root = _validate_managed_root(paths)
    app_root = _validate_program_root(paths)
    state = load_state(paths)
    state = migrate_legacy_owned_artifacts(paths, state)
    if remove_confirmed_legacy_external:
        state = adopt_confirmed_legacy_external_components(
            paths, state, legacy_external_cleanup_candidates(paths),
        )
    if is_port_open(4000) and not stop_proxy(paths):
        raise InstallError("无法确认或停止配置器管理的 LiteLLM 代理，已停止卸载以避免误杀其他程序")
    rollback_owned_components(paths, state)
    remove_user_environment(paths)
    delete_saved_credentials()
    _restore_desktop_shortcuts(state)
    verify_uninstall_outcome(paths, state)
    executable = Path(sys.executable).resolve()
    if root.exists():
        shutil.rmtree(root, ignore_errors=False)
    if getattr(sys, "frozen", False) and executable.is_relative_to(app_root):
        _schedule_root_removal(app_root)
        return "本次安装新增的组件已回滚；窗口关闭后将删除配置器剩余文件。"
    if app_root.exists():
        shutil.rmtree(app_root, ignore_errors=False)
    return "已恢复到运行配置器之前的组件状态。"


def install_all(
    paths: Paths, api_key: str, model: str, progress: Progress,
    options: InstallOptions | None = None,
) -> None:
    options = options or InstallOptions()
    progress("系统检查", "正在检查 Windows 版本和处理器架构")
    if not is_supported_windows():
        raise InstallError("本配置器仅支持 Windows 10/11")
    if not is_supported_architecture():
        raise InstallError("当前通用版支持 Windows 10/11 x64；ARM64 和 32 位系统请勿继续安装")
    key = api_key.strip()
    if not key:
        raise InstallError("请输入 DeepSeek API Key")
    if model not in SUPPORTED_MODELS:
        raise InstallError("不支持的模型")
    free = check_install_location(paths)
    progress("系统检查", f"{windows_compatibility_label()} 环境检查通过 · 系统盘可用 {free / 1024**3:.1f} GB")
    if getattr(sys, "frozen", False):
        install_managed_payload(paths, progress)
    ownership = ensure_install_journal(paths)
    baseline = ownership.get("baseline")
    baseline = baseline if isinstance(baseline, dict) else {}
    previous_installed = ownership.get("installed")
    previous_installed = previous_installed if isinstance(previous_installed, dict) else {}
    progress("离线组件", offline_asset_summary(offline_root(paths)))
    progress("安全保存", "正在写入 Windows 凭据管理器")
    write_api_key(key)
    progress("直连网关", "DeepSeek Anthropic 兼容接口已启用，无需 Python、LiteLLM 或本机代理")
    git_bash = ensure_git(paths, progress)
    progress("安装源检测", "正在并行检测国内镜像、官方源、WinGet 与 DeepSeek 连通性")
    probes = probe_install_sources(timeout=5.0, proxy_url=options.proxy_url)
    progress("安装源检测", install_source_summary(probes))
    state = load_state(paths)
    state["last_source_probe"] = {
        name: {
            "reachable": bool(item.get("reachable")),
            "usable": bool(item.get("usable")),
            "status": int(item.get("status") or 0),
            "latency_ms": int(item.get("latency_ms") or 0),
            "error": str(item.get("error") or "")[-240:],
        }
        for name, item in probes.items()
    }
    _write_state(paths, state)
    claude_before = baseline.get("claude")
    claude_before = claude_before if isinstance(claude_before, dict) else {}
    previous_claude = previous_installed.get("claude")
    previous_claude = previous_claude if isinstance(previous_claude, dict) else {}
    previous_claude_owned = bool(previous_claude.get("installed_by_app"))
    record_owned_component(paths, "claude", {
        "installed_by_app": previous_claude_owned or not bool(claude_before.get("present")),
        "status": "pending",
        "method": str(previous_claude.get("method") or "auto"),
        "path": str(previous_claude.get("path") or ""),
    })
    record_owned_component(paths, "managed_node", {
        "installed_by_app": not bool(claude_before.get("managed_node_existed")),
        "status": "pending",
        "method": "portable-archive",
        "path": str(managed_node_dir(paths)),
        "version": NODE_VERSION,
    })
    claude_outcome: dict[str, str] = {}
    claude = ensure_claude(
        progress, claude_outcome, paths=paths,
        strategy=options.claude_strategy, probes=probes,
        proxy_url=options.proxy_url, allow_managed_node=options.allow_managed_node,
    )
    claude_owned = previous_claude_owned or (claude_outcome.get("method") != "existing" and (
        not bool(claude_before.get("present")) or claude_outcome.get("path") != claude_before.get("path")
    ))
    if previous_claude_owned and claude_outcome.get("method") == "existing":
        claude_outcome["method"] = str(previous_claude.get("method") or "auto")
        claude_outcome["manager"] = str(previous_claude.get("manager") or "")
    record_owned_component(paths, "claude", {
        **claude_outcome,
        "installed_by_app": claude_owned,
        "status": "complete",
    })
    managed_node_used = claude_outcome.get("method") == "managed-npm"
    record_owned_component(paths, "managed_node", {
        "installed_by_app": managed_node_used and not bool(claude_before.get("managed_node_existed")),
        "status": "complete" if managed_node_used else "unused",
        "method": "portable-archive" if managed_node_used else "none",
        "path": str(managed_node_dir(paths)) if managed_node_used else "",
        "version": NODE_VERSION if managed_node_used else "",
    })
    vscode_before = baseline.get("vscode")
    vscode_before = vscode_before if isinstance(vscode_before, dict) else {}
    extension_before = baseline.get("vscode_extension")
    extension_before = extension_before if isinstance(extension_before, dict) else {}
    previous_vscode = previous_installed.get("vscode")
    previous_vscode = previous_vscode if isinstance(previous_vscode, dict) else {}
    previous_vscode_owned = bool(previous_vscode.get("installed_by_app"))
    previous_extension = previous_installed.get("vscode_extension")
    previous_extension = previous_extension if isinstance(previous_extension, dict) else {}
    previous_extension_owned = bool(previous_extension.get("installed_by_app"))
    record_owned_component(paths, "vscode", {
        "installed_by_app": bool(options.install_vscode) and (
            previous_vscode_owned or not bool(vscode_before.get("present"))
        ),
        "status": "pending",
        "method": str(previous_vscode.get("method") or "auto"),
        "path": str(previous_vscode.get("path") or ""),
        "uninstaller": str(_known_component_paths(paths)["vscode_uninstaller"]),
    })
    record_owned_component(paths, "vscode_extension", {
        "installed_by_app": bool(options.install_vscode) and (
            previous_extension_owned or not bool(extension_before.get("present"))
        ),
        "status": "pending", "id": VSCODE_EXTENSION,
    })
    code_outcome: dict[str, str] = {}
    if options.install_vscode:
        code = ensure_vscode(progress, paths, code_outcome)
        vscode_owned = previous_vscode_owned or (
            code_outcome.get("method") != "existing" and not bool(vscode_before.get("present"))
        )
        if previous_vscode_owned and code_outcome.get("method") == "existing":
            code_outcome["method"] = str(previous_vscode.get("method") or "auto")
            code_outcome["manager"] = str(previous_vscode.get("manager") or "")
        record_owned_component(paths, "vscode", {
            **code_outcome,
            "installed_by_app": vscode_owned,
            "status": "complete",
            "uninstaller": str(_known_component_paths(paths)["vscode_uninstaller"]),
        })
        record_owned_component(paths, "vscode_extension", {
            "installed_by_app": previous_extension_owned or (
                not bool(extension_before.get("present")) and vscode_extension_is_installed()
            ),
            "status": "complete", "id": VSCODE_EXTENSION,
        })
    else:
        code = command_path("code") or ""
        progress("VS Code", "已按用户选择跳过（不影响 Claude Code 核心功能）")
        progress("VS Code 扩展", "已跳过可选扩展")
        record_owned_component(paths, "vscode", {
            "installed_by_app": previous_vscode_owned,
            "status": "skipped", "method": "existing" if code else "none", "path": code,
            "uninstaller": str(_known_component_paths(paths)["vscode_uninstaller"]),
        })
        record_owned_component(paths, "vscode_extension", {
            "installed_by_app": previous_extension_owned,
            "status": "skipped", "id": VSCODE_EXTENSION,
        })
    progress("配置", "正在准备 DeepSeek Anthropic 直连启动环境")
    environment_backup = capture_user_environment(paths)
    record_environment_backup(paths, environment_backup)
    # Remove V2.7-V2.9.2 persistent proxy routing. V2.9.3 injects the API key
    # and routing only into the launched Claude/VS Code child process.
    remove_user_environment(paths)
    paths.config.unlink(missing_ok=True)
    progress("终端入口", "正在配置 PowerShell/CMD 的 claude 命令")
    configurator = install_stable_configurator(paths)
    terminal_launcher = install_claude_terminal_launcher(paths, claude, configurator)
    record_added_user_path_entries(paths)
    progress("连接测试", "正在验证 DeepSeek API")
    ok, message = test_connection(model)
    if not ok:
        raise InstallError(message)
    if getattr(sys, "frozen", False):
        progress("桌面入口", "正在创建 Claude Code + DeepSeek 快捷方式")
        record_owned_component(paths, "desktop_shortcut", {
            "installed_by_app": True,
            "status": "pending",
            "path": str(desktop_shortcut_path()),
        })
        if not create_desktop_shortcut(str(configurator or sys.executable)):
            raise InstallError("无法在当前用户桌面创建启动快捷方式，请检查桌面目录权限后重试")
        legacy_desktop_shortcut_path().unlink(missing_ok=True)
        record_owned_component(paths, "desktop_shortcut", {
            "installed_by_app": True,
            "status": "complete",
            "path": str(desktop_shortcut_path()),
        })
    save_state(
        paths, model, "", claude, code,
        str(terminal_launcher or ""), str(configurator or ""),
        environment_backup, git_bash,
    )
    progress("完成", message)
