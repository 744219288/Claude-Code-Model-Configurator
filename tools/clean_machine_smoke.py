"""Run a destructive-to-its-own-root clean-machine deployment smoke test.

The caller must provide a brand-new directory whose name contains
"clean-machine-smoke". No credentials, user environment variables, Claude Code,
or VS Code installations are changed.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from installer import core


def paths_for(root: Path) -> core.Paths:
    return core.Paths(
        root=root,
        runtime=root / "python312",
        venv=root / "venv",
        config=root / "litellm_config.yaml",
        state=root / "state.json",
        log=root / "proxy.log",
        pid=root / "proxy.pid",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--python", type=Path, help="Use a known-clean Python 3.12 when the host installer registry prevents a fresh install test")
    args = parser.parse_args()
    root = args.root.resolve()
    if "clean-machine-smoke" not in root.name.casefold():
        parser.error("测试目录名称必须包含 clean-machine-smoke")
    if root.exists():
        parser.error("测试目录必须不存在，以保证是全新环境")
    root.mkdir(parents=True)
    (root / ".clean-machine-smoke").write_text("owned test directory\n", encoding="ascii")

    paths = paths_for(root)
    started = time.monotonic()
    events: list[dict[str, object]] = []

    def progress(task: str, detail: str) -> None:
        elapsed = round(time.monotonic() - started, 1)
        events.append({"elapsed_seconds": elapsed, "task": task, "detail": detail})
        line = f"[{elapsed:7.1f}s] {task}: {detail}\n"
        sys.stdout.buffer.write(line.encode(sys.stdout.encoding or "utf-8", errors="backslashreplace"))
        sys.stdout.buffer.flush()

    report: dict[str, object] = {
        "version": core.APP_VERSION,
        "root": str(root),
        "started_with_existing_root": False,
        "temp_directory": tempfile.gettempdir(),
    }
    proxy_started = False
    try:
        installer = paths.root / "installers" / f"python-{core.PYTHON_VERSION}-amd64.exe"
        if args.python is None:
            # Pretend this is a new PC: ignore the host's py launcher and PATH.
            with mock.patch("installer.core.command_path", return_value=None):
                python = core.ensure_python312(paths, progress)
            report["python_installer"] = str(installer)
            report["installer_outside_temp"] = not installer.is_relative_to(Path(tempfile.gettempdir()).resolve())
            report["python_installer_exists"] = installer.is_file()
        else:
            python = str(args.python.resolve())
            probe = core.run_command([python, "-c", "import sys; assert sys.version_info[:2] == (3, 12); print(sys.version)"], timeout=30)
            if probe.returncode:
                raise core.InstallError(f"指定的 Python 3.12 不可用：{probe.stdout[-300:]}")
            report["python_installer"] = "skipped: host has a conflicting registered Python 3.12 installation"
            report["installer_outside_temp"] = True
            report["python_installer_exists"] = True
        report["python"] = python

        venv_python = core.ensure_venv_and_litellm(paths, python, progress)
        healthy, reason = core.verify_litellm_environment(venv_python, env=core.isolated_pip_environment())
        report["litellm_healthy"] = healthy
        report["litellm_reason"] = reason
        if not healthy:
            raise core.InstallError(f"LiteLLM 实际部署后校验失败：{reason}")

        token = "sk-local-clean-machine-smoke"
        with (
            mock.patch("installer.core.read_api_key", return_value="sk-clean-machine-smoke"),
            mock.patch("installer.core.ensure_proxy_token", return_value=token),
        ):
            core.start_proxy(paths, venv_python)
            proxy_started = True
            report["proxy_authenticated"] = core.port_owner_is_proxy(4000, token)
            if not report["proxy_authenticated"]:
                raise core.InstallError("LiteLLM 已启动，但令牌鉴权健康检查失败")

        report["proxy_stopped"] = core.stop_proxy(paths)
        proxy_started = False
        if not report["proxy_stopped"]:
            raise core.InstallError("测试代理未能安全停止")

        # A second run must reuse healthy components instead of reinstalling them.
        repeat_started = time.monotonic()
        if args.python is None:
            with mock.patch("installer.core.command_path", return_value=None):
                repeated_python = core.ensure_python312(paths, progress)
        else:
            repeated_python = python
        repeated_venv = core.ensure_venv_and_litellm(paths, repeated_python, progress)
        report["repeat_reused_python"] = repeated_python == python
        report["repeat_reused_venv"] = repeated_venv == venv_python
        report["repeat_seconds"] = round(time.monotonic() - repeat_started, 1)
        report["success"] = all(
            bool(report.get(name))
            for name in (
                "installer_outside_temp",
                "python_installer_exists",
                "litellm_healthy",
                "proxy_authenticated",
                "proxy_stopped",
                "repeat_reused_python",
                "repeat_reused_venv",
            )
        )
    except Exception as exc:
        report["success"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if proxy_started:
            core.stop_proxy(paths)
        report["elapsed_seconds"] = round(time.monotonic() - started, 1)
        report["events"] = events
        report_path = root / "clean-machine-deployment-report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"REPORT={report_path}", flush=True)
    return 0 if report.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
