from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import subprocess
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

import installer.core as core
from installer.core import (
    Paths, bundled_lockfile, bundled_python_installer, bundled_wheelhouse,
    capture_user_environment, check_install_location, create_desktop_shortcut,
    claude_terminal_commands, classify_api_response,
    command_path, ensure_proxy_token, export_diagnostics, gateway_environment,
    install_claude_terminal_launcher, install_stable_configurator,
    download, ensure_claude, ensure_private_venv, ensure_python312,
    ensure_venv_and_litellm, ensure_vscode, find_python312, install_all, start_proxy, stop_proxy,
    isolated_pip_environment, proxy_log_tail, proxy_start_commands, redact, run_command, run_command_stream,
    stage_installer,
    set_user_environment, verify_authenticode, verify_litellm_environment, verify_offline_asset,
    uninstall_all, verify_release_integrity, vscode_extension_is_installed, write_litellm_config, _publisher_matches,
)


def temp_paths(root: Path) -> Paths:
    return Paths(root, root / "python312", root / "venv", root / "config.yaml", root / "state.json", root / "proxy.log", root / "proxy.pid")


class ResponseClassificationTests(unittest.TestCase):
    def test_success(self):
        self.assertEqual(classify_api_response(200, "{}"), (True, "连接成功，DeepSeek API 可用"))

    def test_insufficient_balance_is_connected(self):
        ok, message = classify_api_response(402, '{"message":"Insufficient Balance"}')
        self.assertTrue(ok)
        self.assertEqual(message, "连接成功，但 API 余额不足")

    def test_invalid_key(self):
        ok, message = classify_api_response(401, "invalid api key")
        self.assertFalse(ok)
        self.assertIn("API Key 无效", message)


class ConfigurationTests(unittest.TestCase):
    def test_legacy_environment_values_are_recognized_without_state_file(self):
        state = {}
        self.assertTrue(core._matches_configurator_environment(
            "ANTHROPIC_MODEL", "deepseek-reasoner", state, "",
        ))
        self.assertTrue(core._matches_configurator_environment(
            "ANTHROPIC_AUTH_TOKEN", "sk-local-old-token", state, "",
        ))
        self.assertFalse(core._matches_configurator_environment(
            "ANTHROPIC_MODEL", "user-custom-model", state, "",
        ))

    @mock.patch("installer.core.command_path", return_value="npm.cmd")
    def test_explicit_legacy_cleanup_confirmation_adopts_external_components(self, command):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            state = core.adopt_confirmed_legacy_external_components(paths, {}, {
                "claude": r"C:\Users\test\AppData\Roaming\npm\claude.cmd",
                "vscode": r"C:\Users\test\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd",
                "vscode_extension": "anthropic.claude-code",
            })
        installed = state["ownership"]["installed"]
        self.assertEqual(installed["claude"]["status"], "legacy-user-confirmed")
        self.assertEqual(installed["claude"]["method"], "npm")
        self.assertTrue(installed["vscode"]["installed_by_app"])
        self.assertTrue(installed["vscode_extension"]["installed_by_app"])

    @mock.patch("installer.core.subprocess.run", side_effect=PermissionError(13, "Permission denied"))
    def test_access_denied_has_actionable_windows_security_message(self, _run):
        with self.assertRaisesRegex(Exception, "Windows 拒绝启动.*Windows 安全中心"):
            run_command([r"C:\blocked\python312.exe"])

    def test_local_installer_is_staged_atomically_outside_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "offline" / "python.exe"
            destination = root / "app" / "installers" / "python.exe"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"signed-installer-placeholder")
            self.assertEqual(stage_installer(source, destination), destination)
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertFalse(destination.with_name("python.exe.part").exists())

    @mock.patch("installer.core.desktop_shortcut_path")
    @mock.patch("installer.core.run_command")
    def test_desktop_shortcut_opens_configurator_with_embedded_exe_icon(self, run, shortcut_path):
        with tempfile.TemporaryDirectory() as folder:
            shortcut = Path(folder) / "Claude Code + DeepSeek 一键配置器.lnk"
            shortcut_path.return_value = shortcut

            def create_shortcut(command, **_kwargs):
                shortcut.touch()
                return mock.Mock(returncode=0, stdout="")

            run.side_effect = create_shortcut
            self.assertTrue(create_desktop_shortcut(r"C:\Program Files\Configurator\app.exe"))

        command = run.call_args.args[0]
        self.assertIn("IconLocation", command[3])
        self.assertIn("Arguments", command[3])
        shortcut_env = run.call_args.kwargs["env"]
        self.assertEqual(shortcut_env["CLAUDE_DEEPSEEK_SHORTCUT_TARGET"], r"C:\Program Files\Configurator\app.exe")
        self.assertEqual(shortcut_env["CLAUDE_DEEPSEEK_SHORTCUT_ARGUMENTS"], "")

    def test_install_location_accepts_unicode_path(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder) / "中文 用户")
            self.assertGreater(check_install_location(paths, minimum_free_bytes=1), 0)
            self.assertTrue(paths.root.is_dir())

    def test_unwritable_install_location_fails_early(self):
        with tempfile.TemporaryDirectory() as folder:
            blocked = Path(folder) / "not-a-directory"
            blocked.write_text("occupied", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "无法写入"):
                check_install_location(temp_paths(blocked), minimum_free_bytes=1)

    @mock.patch("installer.core.shutil.disk_usage")
    def test_low_disk_space_fails_before_download(self, disk_usage):
        disk_usage.return_value = mock.Mock(free=100)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(Exception, "空间不足"):
                check_install_location(temp_paths(Path(folder)), minimum_free_bytes=101)

    @mock.patch("installer.core.run_command")
    def test_missing_pip_is_repaired_with_ensurepip(self, run):
        run.side_effect = [
            mock.Mock(returncode=0, stdout="Python 3.12"),
            mock.Mock(returncode=1, stdout="No module named pip"),
            mock.Mock(returncode=0, stdout="pip installed"),
            mock.Mock(returncode=0, stdout="pip 24.0"),
        ]
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder) / "中文 用户")
            venv_python = paths.venv / "Scripts/python.exe"
            venv_python.parent.mkdir(parents=True)
            venv_python.touch()
            self.assertEqual(ensure_private_venv(paths, "python.exe", lambda *_: None), str(venv_python))
        self.assertIn("ensurepip", run.call_args_list[2].args[0])

    @mock.patch("installer.core.run_command")
    def test_broken_private_venv_is_rebuilt_safely(self, run):
        run.side_effect = [
            mock.Mock(returncode=1, stdout="broken"),
            mock.Mock(returncode=0, stdout="created"),
            mock.Mock(returncode=0, stdout="pip 24.0"),
        ]
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            venv_python = paths.venv / "Scripts/python.exe"
            venv_python.parent.mkdir(parents=True)
            venv_python.touch()
            ensure_private_venv(paths, "python.exe", lambda *_: None)
        self.assertIn("--clear", run.call_args_list[1].args[0])
        self.assertEqual(run.call_args_list[1].args[0][-1], str(paths.venv))

    @mock.patch("installer.core.command_path", return_value=None)
    def test_terminal_falls_back_to_utf8_cmd(self, command):
        commands = claude_terminal_commands(r"C:\Users\中文 用户\.local\bin\claude.exe")
        self.assertEqual(commands[0][0][0], "cmd.exe")
        self.assertIn("chcp 65001", commands[0][0][-1])
        self.assertIn("中文 用户", commands[0][0][-1])

    @mock.patch("installer.core.command_path", return_value=r"C:\Program Files\WindowsApps\wt.exe")
    def test_terminal_prefers_windows_terminal(self, command):
        commands = claude_terminal_commands("claude.exe")
        self.assertEqual(commands[0][0][0], r"C:\Program Files\WindowsApps\wt.exe")
        self.assertEqual(commands[1][0][0], "cmd.exe")

    def test_proxy_prefers_console_entrypoint_and_has_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            python = paths.venv / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.touch()
            launcher = python.parent / "litellm.exe"
            launcher.touch()
            commands = proxy_start_commands(paths, str(python))
            self.assertEqual(commands[0][0], str(launcher))
            self.assertNotIn("-m", commands[0])
            self.assertIn("litellm.proxy.proxy_cli", commands[1][2])
            self.assertIn("--config", commands[0])

    def test_proxy_fallback_does_not_use_package_main(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            python = paths.venv / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.touch()
            commands = proxy_start_commands(paths, str(python))
            self.assertEqual(len(commands), 1)
            self.assertNotEqual(commands[0][1:3], ["-m", "litellm"])

    @mock.patch("installer.core.listening_pid", return_value=303)
    @mock.patch("installer.core.time.sleep")
    @mock.patch("installer.core.ensure_proxy_token", return_value="sk-local-test")
    @mock.patch("installer.core.port_owner_is_proxy", return_value=True)
    @mock.patch("installer.core.is_port_open", return_value=False)
    @mock.patch("installer.core.verify_litellm_environment", return_value=(True, "ok"))
    @mock.patch("installer.core.read_api_key", return_value="sk-secret")
    @mock.patch("installer.core.subprocess.Popen")
    def test_proxy_retries_cli_fallback_after_launcher_exits(self, popen, key, verify, port_open, owner, token, sleep, listener):
        failed = mock.Mock(pid=101)
        failed.poll.return_value = 1
        running = mock.Mock(pid=102)
        running.poll.return_value = None
        popen.side_effect = [failed, running]
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            python = paths.venv / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.touch()
            (python.parent / "litellm.exe").touch()
            start_proxy(paths, str(python))
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(popen.call_args.kwargs["env"]["LITELLM_MASTER_KEY"], "sk-local-test")
        self.assertTrue(popen.call_args_list[0].args[0][0].endswith("litellm.exe"))
        self.assertIn("litellm.proxy.proxy_cli", popen.call_args_list[1].args[0][2])
        if os.name == "nt":
            flags = popen.call_args.kwargs["creationflags"]
            startupinfo = popen.call_args.kwargs["startupinfo"]
            self.assertTrue(flags & subprocess.CREATE_NO_WINDOW)
            self.assertTrue(flags & subprocess.CREATE_NEW_PROCESS_GROUP)
            self.assertFalse(flags & subprocess.DETACHED_PROCESS)
            self.assertTrue(startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW)
            self.assertEqual(startupinfo.wShowWindow, subprocess.SW_HIDE)

    @mock.patch("installer.core.listening_pid", return_value=303)
    @mock.patch("installer.core.time.sleep")
    @mock.patch("installer.core.ensure_proxy_token", return_value="sk-local-test")
    @mock.patch("installer.core.port_owner_is_proxy")
    @mock.patch("installer.core.is_port_open", return_value=False)
    @mock.patch("installer.core.verify_litellm_environment", return_value=(True, "ok"))
    @mock.patch("installer.core.read_api_key", return_value="sk-secret")
    @mock.patch("installer.core.subprocess.Popen")
    def test_slow_first_proxy_start_gets_90_seconds(self, popen, key, verify, port_open, owner, token, sleep, listener):
        process = mock.Mock(pid=202)
        process.poll.return_value = None
        popen.return_value = process
        owner.side_effect = [False] * 88 + [True]
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            python = paths.venv / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.touch()
            start_proxy(paths, str(python))
        self.assertEqual(owner.call_count, 89)
        self.assertEqual(sleep.call_count, 88)

    def test_offline_assets_require_matching_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            asset = root / "python" / "python-3.12.10-amd64.exe"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"trusted-test-file")
            import hashlib
            manifest = {"files": {"python/python-3.12.10-amd64.exe": {
                "size": asset.stat().st_size,
                "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
            }}}
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertTrue(verify_offline_asset(asset, root)[0])
            self.assertEqual(bundled_python_installer(root), asset)
            asset.write_bytes(b"tampered")
            self.assertFalse(verify_offline_asset(asset, root)[0])
            self.assertIsNone(bundled_python_installer(root))

    def test_wheelhouse_requires_litellm_and_all_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wheels = root / "wheels"
            wheels.mkdir()
            wheel = wheels / "litellm-1.80.11-py3-none-any.whl"
            wheel.write_bytes(b"wheel")
            import hashlib
            (root / "manifest.json").write_text(json.dumps({"files": {
                "wheels/litellm-1.80.11-py3-none-any.whl": {
                    "size": 5, "sha256": hashlib.sha256(b"wheel").hexdigest()
                }
            }}), encoding="utf-8")
            self.assertEqual(bundled_wheelhouse(root), wheels)

    def test_offline_manifest_accepts_windows_powershell_utf8_bom(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            asset = root / "python" / "python-3.12.10-amd64.exe"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"python-installer")
            import hashlib
            manifest = {"files": {"python/python-3.12.10-amd64.exe": {
                "size": asset.stat().st_size,
                "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
            }}}
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8-sig")
            self.assertEqual(bundled_python_installer(root), asset)

    def test_config_uses_environment_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            write_litellm_config(paths)
            content = paths.config.read_text(encoding="utf-8")
            self.assertIn("os.environ/DEEPSEEK_API_KEY", content)
            self.assertIn("deepseek-chat", content)
            self.assertIn("deepseek-reasoner", content)
            self.assertNotIn("sk-secret", content)
            self.assertIn("master_key: os.environ/LITELLM_MASTER_KEY", content)
            self.assertNotIn("sk-local", content)

    def test_gateway_environment_uses_direct_deepseek_anthropic_api(self):
        values = gateway_environment("deepseek-v4-pro[1m]", "sk-deepseek-test")
        self.assertEqual(values["ANTHROPIC_BASE_URL"], "https://api.deepseek.com/anthropic")
        self.assertEqual(values["ANTHROPIC_AUTH_TOKEN"], "sk-deepseek-test")
        self.assertEqual(values["ANTHROPIC_MODEL"], "deepseek-v4-pro[1m]")
        self.assertEqual(values["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "deepseek-v4-flash")

    @mock.patch("installer.core.write_proxy_token")
    @mock.patch("installer.core.read_proxy_token", return_value=None)
    @mock.patch("installer.core.secrets.token_urlsafe", return_value="generated-token")
    def test_proxy_token_is_generated_once_and_saved(self, random_token, read_token, write_token):
        self.assertEqual(ensure_proxy_token(), "sk-local-generated-token")
        write_token.assert_called_once_with("sk-local-generated-token")

    def test_offline_lock_requires_manifest_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lock = root / "requirements.lock"
            content = "demo==1.0 --hash=sha256:" + "a" * 64 + "\n"
            lock.write_text(content, encoding="utf-8")
            import hashlib
            manifest = {"files": {"requirements.lock": {
                "size": lock.stat().st_size,
                "sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
            }}}
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(bundled_lockfile(root), lock)
            lock.write_text(content + "tampered", encoding="utf-8")
            self.assertIsNone(bundled_lockfile(root))

    @mock.patch("installer.core.system_status", return_value={"version": "2.8", "api_key_saved": True})
    @mock.patch("installer.core.read_proxy_token", return_value="sk-local-secret")
    @mock.patch("installer.core.read_api_key", return_value="sk-deepseek-secret")
    def test_diagnostics_export_redacts_all_credentials(self, api_key, proxy_token, status):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder) / "app")
            paths.root.mkdir()
            paths.log.write_text(
                "Authorization: sk-local-secret\napi_key=sk-deepseek-secret\nstartup failed",
                encoding="utf-8",
            )
            (paths.root / "python-install.log").write_text("python installer maintenance mode", encoding="utf-8")
            paths.state.write_text(json.dumps({
                "model": "deepseek-chat",
                "environment_backup": {"SECRET": "value"},
                "ownership": {
                    "baseline": {"user_path": [r"C:\Users\private\secret-bin"]},
                    "installed": {"python": {"installed_by_app": True, "method": "official-installer"}},
                },
            }), encoding="utf-8")
            target = export_diagnostics(paths, Path(folder) / "diagnostics.zip")
            with zipfile.ZipFile(target) as archive:
                combined = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())
            self.assertNotIn("sk-local-secret", combined)
            self.assertNotIn("sk-deepseek-secret", combined)
            self.assertNotIn("environment_backup", combined)
            self.assertNotIn("secret-bin", combined)
            self.assertIn("ownership_summary", combined)
            self.assertIn("startup failed", combined)
            self.assertIn("python installer maintenance mode", combined)

    @mock.patch("installer.core.verify_authenticode")
    def test_release_integrity_checks_hash_and_signature(self, verify_signature):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / "configurator.exe"
            executable.write_bytes(b"signed-build")
            import hashlib
            (root / "release-integrity.json").write_text(json.dumps({
                "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "publisher": "Example Publisher",
            }), encoding="utf-8")
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", str(executable)), \
                    mock.patch("installer.core.application_dir", return_value=root):
                verify_release_integrity()
                verify_signature.assert_called_once_with(executable.resolve(), "Example Publisher")
                executable.write_bytes(b"tampered")
                with self.assertRaisesRegex(Exception, "哈希"):
                    verify_release_integrity()

    @mock.patch("installer.core.time.sleep")
    @mock.patch("installer.core.urllib.request.urlopen")
    def test_download_retries_and_atomically_finishes(self, urlopen, sleep):
        class Response:
            headers = {"Content-Length": "4"}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _):
                if not hasattr(self, "done"):
                    self.done = True
                    return b"data"
                return b""

        urlopen.side_effect = [urllib.error.URLError("temporary"), Response()]
        events = []
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "setup.exe"
            download("https://example.invalid/setup.exe", target, lambda a, b: events.append((a, b)))
            self.assertEqual(target.read_bytes(), b"data")
            self.assertFalse(target.with_name("setup.exe.part").exists())
        self.assertEqual(urlopen.call_count, 2)
        self.assertTrue(any("重试" in detail for _, detail in events))

    @mock.patch("installer.core.urllib.request.urlopen")
    def test_incomplete_download_is_never_promoted(self, urlopen):
        class ShortResponse:
            headers = {"Content-Length": "5"}

            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _):
                if not hasattr(self, "done"):
                    self.done = True
                    return b"data"
                return b""

        urlopen.return_value = ShortResponse()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "setup.exe"
            with self.assertRaisesRegex(Exception, "下载失败"):
                download("https://example.invalid/setup.exe", target, attempts=1)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_name("setup.exe.part").exists())

    @mock.patch("installer.core.find_python312")
    @mock.patch("installer.core.bundled_python_installer", return_value=None)
    @mock.patch("installer.core.download")
    @mock.patch("installer.core.verify_authenticode")
    @mock.patch("installer.core.run_command")
    def test_fresh_pc_python_installs_per_user_private_runtime(self, run, signature, fetch, bundled, find):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            installed = paths.runtime / "python.exe"
            find.side_effect = [None, str(installed)]
            run.return_value = mock.Mock(returncode=0, stdout="")
            result = ensure_python312(paths, lambda *_: None)
        self.assertEqual(result, str(installed))
        install_args = run.call_args.args[0]
        self.assertIn("InstallAllUsers=0", install_args)
        self.assertIn("PrependPath=0", install_args)
        self.assertIn("Shortcuts=0", install_args)
        self.assertIn("AssociateFiles=0", install_args)
        self.assertIn(f"TargetDir={paths.runtime}", install_args)
        fetch.assert_called_once()
        self.assertEqual(
            fetch.call_args.args[1],
            paths.root / "installers" / "python-3.12.10-amd64.exe",
        )
        signature.assert_called_once()

    @mock.patch("installer.core.command_path")
    @mock.patch("installer.core.download")
    @mock.patch("installer.core.verify_authenticode")
    @mock.patch("installer.core.run_command")
    @mock.patch("installer.core.vscode_extension_is_installed", return_value=False)
    def test_vscode_without_winget_uses_official_installer(self, installed, run, signature, fetch, command):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            command.side_effect = [None, None, "code.cmd"]
            run.return_value = mock.Mock(returncode=0, stdout="")
            self.assertEqual(ensure_vscode(lambda *_: None, paths), "code.cmd")
            self.assertIn("update.code.visualstudio.com", fetch.call_args.args[0])
            self.assertEqual(fetch.call_args.args[1], paths.root / "installers" / "VSCodeUserSetup-x64.exe")
            self.assertIn("--install-extension", run.call_args_list[-1].args[0])
            self.assertNotIn("--force", run.call_args_list[-1].args[0])

    @mock.patch("installer.core.command_path", return_value="code.cmd")
    @mock.patch("installer.core.run_command")
    @mock.patch("installer.core.vscode_extension_is_installed", return_value=True)
    def test_existing_vscode_extension_is_reused_without_forced_update(self, installed, run, command):
        messages = []
        self.assertEqual(ensure_vscode(lambda task, detail: messages.append((task, detail))), "code.cmd")
        run.assert_not_called()
        self.assertIn(("VS Code 扩展", "Anthropic 官方扩展已安装，直接复用"), messages)

    def test_vscode_extension_registry_ignores_obsolete_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / "anthropic.claude-code-1.0.0-win32-x64"
            old.mkdir()
            (root / ".obsolete").write_text(json.dumps({old.name: True}), encoding="utf-8")
            self.assertFalse(vscode_extension_is_installed(root))
            (root / "extensions.json").write_text(
                json.dumps([{"identifier": {"id": "anthropic.claude-code"}}]), encoding="utf-8",
            )
            self.assertTrue(vscode_extension_is_installed(root))

    @mock.patch("installer.core.command_path")
    @mock.patch("installer.core.download")
    @mock.patch("installer.core.verify_authenticode")
    @mock.patch("installer.core.run_command")
    def test_claude_without_winget_uses_native_installer(self, run, signature, fetch, command):
        command.side_effect = [None, None, "claude.exe"]
        run.return_value = mock.Mock(returncode=0, stdout="2.1.0")
        with mock.patch("installer.core.managed_claude_path", return_value=None), \
                mock.patch("installer.core.environment_with_git", return_value={}):
            self.assertEqual(ensure_claude(lambda *_: None), "claude.exe")
        self.assertEqual(fetch.call_args.args[0], "https://claude.ai/install.cmd")
        self.assertIn("stable", run.call_args_list[0].args[0])
        signature.assert_called_once()

    @mock.patch("installer.core.command_path")
    @mock.patch("installer.core.download")
    @mock.patch("installer.core.verify_authenticode")
    @mock.patch("installer.core.run_command")
    def test_existing_npm_claude_is_preserved_without_native_migration(self, run, signature, fetch, command):
        existing = r"C:\Users\test\AppData\Roaming\npm\claude.cmd"
        command.return_value = existing
        run.return_value = mock.Mock(returncode=0, stdout="2.1.0")
        result = ensure_claude(lambda *_: None)
        self.assertEqual(result, existing)
        fetch.assert_not_called()
        signature.assert_not_called()
        self.assertEqual(run.call_args.args[0], [existing, "--version"])

    @mock.patch("installer.core.command_path", return_value="py.exe")
    @mock.patch("installer.core.run_command", return_value=mock.Mock(returncode=1, stdout="No suitable Python runtime found"))
    def test_existing_python_314_does_not_count_as_python_312(self, run, command):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(find_python312(temp_paths(Path(folder))))
        self.assertTrue(any("-3.12" in call.args[0] for call in run.call_args_list))

    @mock.patch("installer.core.command_path", return_value=None)
    @mock.patch("installer.core.run_command", return_value=mock.Mock(returncode=0, stdout="(3, 12)\n"))
    def test_python312_is_discovered_in_standard_per_user_location(self, run, command):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            candidate = root / "Programs" / "Python" / "Python312" / "python.exe"
            candidate.parent.mkdir(parents=True)
            candidate.touch()
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(root), "ProgramFiles": str(root / "Program Files")}):
                found = find_python312(temp_paths(root / "app"))
        self.assertEqual(found, str(candidate))
        self.assertEqual(run.call_args.args[0][0], str(candidate))

    @mock.patch("installer.core.ensure_proxy_token", return_value="sk-local-test")
    @mock.patch("installer.core.port_owner_is_proxy", return_value=False)
    @mock.patch("installer.core.is_port_open", return_value=True)
    def test_foreign_process_on_port_4000_fails_early(self, port_open, owner, token):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(Exception, "端口 4000"):
                start_proxy(temp_paths(Path(folder)))

    @mock.patch("installer.core.ensure_proxy_token", return_value="sk-local-test")
    @mock.patch("installer.core.port_owner_is_proxy", return_value=True)
    @mock.patch("installer.core.is_port_open", return_value=True)
    def test_existing_litellm_proxy_is_reused(self, port_open, owner, token):
        with tempfile.TemporaryDirectory() as folder:
            start_proxy(temp_paths(Path(folder)))

    def test_redacts_all_secrets(self):
        self.assertEqual(redact("key=sk-abc and sk-def", ["sk-abc", "sk-def"]), "key=*** and ***")

    def test_signature_rejects_missing_file(self):
        with self.assertRaisesRegex(Exception, "数字签名"):
            verify_authenticode(Path("missing-setup.exe"), "Microsoft Corporation")

    def test_publisher_matches_is_case_insensitive_substring(self):
        self.assertTrue(_publisher_matches("Microsoft Corporation", "microsoft"))
        self.assertTrue(_publisher_matches("Anthropic PBC", "Anthropic"))
        self.assertFalse(_publisher_matches("Acme Corp", "Microsoft"))
        self.assertFalse(_publisher_matches("", "Microsoft"))
        self.assertTrue(_publisher_matches("Acme Corp", ""))

    @mock.patch("installer.core._authenticode_signer", return_value=None)
    @mock.patch("installer.core.ctypes.WinDLL")
    def test_signature_rejects_unreadable_publisher(self, win_dll, signer):
        win_dll.return_value.WinVerifyTrust.return_value = 0
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "signed.exe"
            target.write_bytes(b"MZ-fake")
            with self.assertRaisesRegex(Exception, "无法读取.*发布者"):
                verify_authenticode(target, "Microsoft Corporation")

    @mock.patch("installer.core.prepend_user_path")
    def test_terminal_launcher_injects_direct_environment_via_configurator(self, prepend):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "中文用户"
            local = profile / "AppData" / "Local"
            paths = temp_paths(local / "ClaudeDeepSeekConfigurator")
            configurator = paths.root / "ClaudeDeepSeekConfigurator.exe"
            claude = profile / ".local" / "bin" / "claude.exe"
            configurator.parent.mkdir(parents=True)
            configurator.touch()
            claude.parent.mkdir(parents=True)
            claude.touch()
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local), "USERPROFILE": str(profile)}):
                launcher = install_claude_terminal_launcher(paths, str(claude), configurator)
            self.assertIsNotNone(launcher)
            content = launcher.read_text(encoding="ascii")
            self.assertNotIn("--start-proxy", content)
            self.assertIn("--run-claude %*", content)
            self.assertIn(r"%LOCALAPPDATA%\ClaudeDeepSeekConfigurator\ClaudeDeepSeekConfigurator.exe", content)
            self.assertNotIn("中文用户", content)
            prepend.assert_called_once_with(paths.root / "bin")

    @mock.patch("installer.core.shutil.which", return_value=r"C:\Users\test\AppData\Roaming\npm\claude.cmd")
    @mock.patch("installer.core.Path.home")
    def test_command_path_prefers_native_exe_over_npm(self, home, which):
        with tempfile.TemporaryDirectory() as folder:
            home.return_value = Path(folder)
            native = Path(folder) / ".local" / "bin" / "claude.exe"
            native.parent.mkdir(parents=True)
            native.touch()
            self.assertEqual(command_path("claude"), str(native))

    @unittest.skipUnless(os.name == "nt", "PowerShell command resolution is Windows-only")
    def test_powershell_resolves_safe_cmd_before_npm_ps1(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            safe = root / "safe"
            npm = root / "npm"
            safe.mkdir()
            npm.mkdir()
            (safe / "claude.cmd").write_text("@echo safe", encoding="ascii")
            (npm / "claude.ps1").write_text("Write-Output npm-ps1", encoding="ascii")
            (npm / "claude.cmd").write_text("@echo npm-cmd", encoding="ascii")
            env = os.environ.copy()
            env["PATH"] = os.pathsep.join([str(safe), str(npm), env.get("PATH", "")])
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", "(Get-Command claude).Source"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            resolved = Path(result.stdout.strip())
            self.assertEqual(resolved.name.lower(), "claude.cmd")
            self.assertEqual(resolved.parent.name.lower(), "safe")

    @mock.patch("installer.core.run_command")
    @mock.patch("installer.core.listening_pid", return_value=222)
    @mock.patch("installer.core.is_port_open", return_value=True)
    def test_stop_proxy_refuses_stale_pid(self, port_open, listening, run):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            paths.pid.write_text("111", encoding="ascii")
            self.assertFalse(stop_proxy(paths))
        run.assert_not_called()

    @mock.patch("installer.core.process_executable")
    @mock.patch("installer.core.run_command", return_value=mock.Mock(returncode=0, stdout="SUCCESS"))
    @mock.patch("installer.core.listening_pid", return_value=222)
    @mock.patch("installer.core.is_port_open", return_value=False)
    def test_stop_proxy_kills_recorded_launcher_tree_for_child_listener(self, port_open, listening, run, executable):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            launcher = paths.venv / "Scripts" / "litellm.exe"
            launcher.parent.mkdir(parents=True)
            launcher.touch()
            executable.return_value = launcher
            paths.pid.write_text('{"launcher_pid":111,"listener_pid":222}', encoding="ascii")
            self.assertTrue(stop_proxy(paths))
        self.assertIn("111", run.call_args.args[0])
        self.assertNotIn("222", run.call_args.args[0])

    def test_uninstall_refuses_unmanaged_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(Exception, "卸载目录校验失败"):
                uninstall_all(temp_paths(Path(folder) / "unrelated"))

    @mock.patch("installer.core.python312_bundle_registrations")
    @mock.patch("installer.core.app_owned_python_registration")
    @mock.patch("installer.core.run_command", return_value=mock.Mock(returncode=0, stdout=""))
    def test_owned_python_uses_official_uninstaller(self, run, registration, bundles):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            bundles.side_effect = [[], []]
            registration.side_effect = [
                {"target": str(paths.runtime), "executable": str(paths.runtime / "python.exe")},
                None, None,
            ]
            installer = paths.root / "installers" / "python.exe"
            installer.parent.mkdir(parents=True)
            installer.touch()
            state = {"ownership": {"installed": {"python": {
                "installed_by_app": True, "installer": str(installer),
            }}}}
            core._uninstall_owned_python(paths, state)
        args = run.call_args.args[0]
        self.assertEqual(args[0], str(installer))
        self.assertIn("/uninstall", args)
        self.assertIn("/quiet", args)

    @mock.patch("installer.core.run_command")
    def test_preexisting_components_are_never_uninstalled(self, run):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            state = {"ownership": {"installed": {
                "python": {"installed_by_app": False},
                "claude": {"installed_by_app": False},
                "vscode": {"installed_by_app": False},
                "vscode_extension": {"installed_by_app": False},
            }}}
            core.rollback_owned_components(paths, state)
        run.assert_not_called()

    @mock.patch("installer.core.vscode_extension_is_installed", return_value=False)
    @mock.patch("installer.core.run_command", return_value=mock.Mock(returncode=0, stdout=""))
    def test_owned_vscode_extension_is_removed(self, run, installed):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            code = Path(folder) / "code.cmd"
            code.touch()
            state = {"code": str(code), "ownership": {"installed": {
                "vscode": {"path": str(code)},
                "vscode_extension": {"installed_by_app": True},
            }}}
            core._uninstall_owned_vscode_extension(paths, state)
        self.assertIn("--uninstall-extension", run.call_args.args[0])
        self.assertIn("anthropic.claude-code", run.call_args.args[0])

    @mock.patch("installer.core._known_component_paths")
    @mock.patch("installer.core.run_command", return_value=mock.Mock(returncode=0, stdout=""))
    def test_owned_claude_npm_package_is_removed(self, run, known_paths):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            known_paths.return_value = {
                "claude_native": Path(folder) / "missing-native.exe",
                "claude_share": Path(folder) / "missing-share",
                "claude_data": Path(folder) / "missing-data",
                "claude_npm": Path(folder) / "missing-npm.cmd",
            }
            state = {"ownership": {
                "baseline": {"claude": {
                    "native_existed": True, "share_existed": True, "data_existed": True,
                }},
                "installed": {"claude": {
                    "installed_by_app": True, "method": "npm", "manager": "npm.cmd",
                }},
            }}
            core._uninstall_owned_claude(paths, state)
        self.assertEqual(run.call_args.args[0][:3], ["npm.cmd", "uninstall", "-g"])
        self.assertIn("@anthropic-ai/claude-code", run.call_args.args[0])

    @mock.patch("installer.core.command_path", return_value=None)
    @mock.patch("installer.core._known_component_paths")
    def test_interrupted_managed_claude_install_removes_node_prefix_and_cache(self, known_paths, command):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            managed_prefix = paths.root / "managed-npm"
            managed_node = paths.root / "managed-node"
            npm_cache = paths.root / "cache" / "npm"
            for target in (managed_prefix, managed_node, npm_cache):
                target.mkdir(parents=True)
                (target / "partial.tmp").write_text("partial", encoding="utf-8")
            known_paths.return_value = {
                "claude_native": paths.root / "missing-native.exe",
                "claude_share": paths.root / "missing-share",
                "claude_data": paths.root / "missing-data",
                "claude_npm": paths.root / "missing-npm.cmd",
                "claude_managed": managed_prefix,
                "node_managed": managed_node,
                "npm_cache": npm_cache,
            }
            state = {"ownership": {
                "baseline": {"claude": {
                    "native_existed": False,
                    "share_existed": False,
                    "data_existed": False,
                    "npm_existed": False,
                    "managed_existed": False,
                    "managed_node_existed": False,
                }},
                "installed": {"claude": {
                    "installed_by_app": True,
                    "status": "pending",
                    "method": "pending",
                }},
            }}
            core._uninstall_owned_claude(paths, state)
            self.assertFalse(managed_prefix.exists())
            self.assertFalse(managed_node.exists())
            self.assertFalse(npm_cache.exists())

    @mock.patch("installer.core.prepare_python_installer")
    @mock.patch("installer.core.python312_registrations")
    @mock.patch("installer.core.find_python312")
    @mock.patch("installer.core.run_command", return_value=mock.Mock(returncode=0, stdout=""))
    def test_legacy_owned_python_registration_is_repaired_without_feature_removal(self, run, find, registrations, prepare):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            installer = Path(folder) / "python-installer.exe"
            installer.touch()
            prepare.return_value = installer
            registrations.return_value = [{
                "scope": "current_user", "target": str(paths.runtime),
                "executable": str(paths.runtime / "python.exe"),
            }]
            find.side_effect = [None, str(paths.runtime / "python.exe")]
            result = ensure_python312(paths, lambda *_: None)
        self.assertEqual(result, str(paths.runtime / "python.exe"))
        args = run.call_args.args[0]
        self.assertIn("/repair", args)
        self.assertNotIn("Include_doc=0", args)
        self.assertNotIn("Include_tcltk=0", args)

    @mock.patch("installer.core.python312_bundle_registrations")
    @mock.patch("installer.core.app_owned_python_registration")
    @mock.patch("installer.core.run_command")
    def test_python_uninstall_repairs_and_retries_when_first_attempt_fails(self, run, registration, bundles):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            bundles.return_value = []
            installer = paths.root / "installers" / "python.exe"
            installer.parent.mkdir(parents=True)
            installer.touch()
            registration.side_effect = [
                {"target": str(paths.runtime)}, None,
            ]
            run.side_effect = [
                mock.Mock(returncode=1603, stdout="failed"),
                mock.Mock(returncode=0, stdout="repaired"),
                mock.Mock(returncode=0, stdout="removed"),
            ]
            state = {"ownership": {"installed": {"python": {
                "installed_by_app": True, "installer": str(installer),
            }}}}
            core._uninstall_owned_python(paths, state)
        self.assertEqual(run.call_count, 3)
        self.assertIn("/repair", run.call_args_list[1].args[0])
        self.assertIn("/uninstall", run.call_args_list[2].args[0])

    @mock.patch("installer.core.python312_bundle_registrations")
    @mock.patch("installer.core.app_owned_python_registration", return_value=None)
    @mock.patch("installer.core.run_command", return_value=mock.Mock(returncode=0, stdout=""))
    def test_python_uninstall_removes_bundle_registration_even_when_install_path_key_is_gone(self, run, registration, bundles):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            installer = paths.root / "installers" / "python.exe"
            installer.parent.mkdir(parents=True)
            installer.touch()
            bundles.side_effect = [[{"key": "owned-python"}], [], []]
            state = {"ownership": {
                "baseline": {"python": {"bundle_registrations": []}},
                "installed": {"python": {
                    "installed_by_app": True, "installer": str(installer),
                }},
            }}
            core._uninstall_owned_python(paths, state)
        self.assertIn("/uninstall", run.call_args.args[0])

    @mock.patch("installer.core.subprocess.Popen")
    def test_scheduled_uninstall_cleanup_is_hidden_without_detached_flag(self, popen):
        process = mock.Mock()
        process.poll.return_value = None
        popen.return_value = process
        core._schedule_root_removal(Path(r"C:\Users\test\AppData\Local\ClaudeDeepSeekConfigurator"))
        flags = popen.call_args.kwargs["creationflags"]
        self.assertTrue(flags & subprocess.CREATE_NO_WINDOW)
        self.assertTrue(flags & subprocess.CREATE_NEW_PROCESS_GROUP)
        self.assertFalse(flags & subprocess.DETACHED_PROCESS)

    @mock.patch("installer.core.load_state", return_value={"model": "deepseek-chat"})
    @mock.patch("winreg.QueryValueEx")
    @mock.patch("winreg.CreateKey")
    def test_v27_gateway_values_are_not_restored_after_uninstall(self, create_key, query_value, state):
        legacy = {
            "ANTHROPIC_BASE_URL": "https://user-owned.example",
            "ANTHROPIC_AUTH_TOKEN": "dummy",
            "ANTHROPIC_MODEL": "deepseek-chat",
            "ANTHROPIC_SMALL_FAST_MODEL": "deepseek-chat",
        }
        def read_legacy(_key, name):
            if name not in legacy:
                raise FileNotFoundError(name)
            return legacy[name], "REG_SZ"
        query_value.side_effect = read_legacy
        with tempfile.TemporaryDirectory() as folder:
            backup = capture_user_environment(temp_paths(Path(folder)))
        self.assertEqual(backup["ANTHROPIC_BASE_URL"], "https://user-owned.example")
        self.assertIsNone(backup["ANTHROPIC_AUTH_TOKEN"])
        self.assertIsNone(backup["ANTHROPIC_MODEL"])

    def test_stable_configurator_copy_is_hash_identical(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "downloaded.exe"
            source.write_bytes(b"MZ-test-configurator")
            paths = temp_paths(root / "installed")
            with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", str(source)):
                installed = install_stable_configurator(paths)
            self.assertEqual(installed.read_bytes(), source.read_bytes())


    @mock.patch("installer.core._authenticode_signer", return_value="Acme Corp")
    @mock.patch("installer.core.ctypes.WinDLL")
    def test_signature_rejects_wrong_publisher(self, win_dll, signer):
        win_dll.return_value.WinVerifyTrust.return_value = 0
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "signed.exe"
            target.write_bytes(b"MZ-fake")
            with self.assertRaisesRegex(Exception, "发布者"):
                verify_authenticode(target, "Microsoft Corporation")

    @mock.patch("installer.core._authenticode_signer", return_value="Microsoft Corporation")
    @mock.patch("installer.core.ctypes.WinDLL")
    def test_signature_accepts_matching_publisher(self, win_dll, signer):
        win_dll.return_value.WinVerifyTrust.return_value = 0
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "signed.exe"
            target.write_bytes(b"MZ-fake")
            verify_authenticode(target, "Microsoft Corporation")

    def test_small_fast_model_is_always_v4_flash(self):
        values = gateway_environment("deepseek-v4-pro[1m]", "sk-deepseek-test")
        self.assertEqual(values["ANTHROPIC_MODEL"], "deepseek-v4-pro[1m]")
        self.assertEqual(values["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "deepseek-v4-flash")
        self.assertEqual(values["CLAUDE_CODE_SUBAGENT_MODEL"], "deepseek-v4-flash")

    @mock.patch("installer.core.urllib.request.urlopen")
    def test_download_tolerates_malformed_content_length(self, urlopen):
        class WeirdResponse:
            headers = {"Content-Length": "not-a-number"}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _):
                if not hasattr(self, "done"):
                    self.done = True
                    return b"data"
                return b""

        urlopen.return_value = WeirdResponse()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "setup.exe"
            download("https://example.invalid/setup.exe", target, attempts=1)
            self.assertEqual(target.read_bytes(), b"data")

    def test_streaming_command_reports_lines(self):
        streamed = []
        result = run_command_stream(
            [sys.executable, "-c", "print('Collecting demo'); print('Successfully installed demo')"],
            lambda line, elapsed: streamed.append(line), timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(streamed, ["Collecting demo", "Successfully installed demo"])

    def test_isolated_pip_environment_removes_machine_policy(self):
        with mock.patch.dict(os.environ, {"PIP_REQUIRE_HASHES": "1", "PIP_INDEX_URL": "bad", "HTTPS_PROXY": "proxy"}, clear=False):
            env = isolated_pip_environment()
        self.assertNotIn("PIP_REQUIRE_HASHES", env)
        self.assertNotIn("PIP_INDEX_URL", env)
        self.assertEqual(env["HTTPS_PROXY"], "proxy")
        self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)

    @mock.patch("installer.core.run_command")
    def test_litellm_integrity_requires_imports_and_pip_check(self, run):
        run.side_effect = [
            mock.Mock(returncode=0, stdout="版本=1.80.11\n"),
            mock.Mock(returncode=0, stdout="No broken requirements found.\n"),
        ]
        self.assertTrue(verify_litellm_environment("python.exe")[0])
        self.assertEqual(run.call_count, 2)

    def test_proxy_log_tail_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            paths.log.write_text("Authorization: sk-secret\napi_key=sk-secret\nstartup failed", encoding="utf-8")
            tail = proxy_log_tail(paths, "sk-secret")
            self.assertNotIn("sk-secret", tail)
            self.assertIn("startup failed", tail)

    @mock.patch("installer.core.bundled_wheelhouse", return_value=None)
    @mock.patch("installer.core.run_command")
    @mock.patch("installer.core.run_command_stream")
    @mock.patch("installer.core.verify_litellm_environment")
    def test_hash_failure_retries_without_cache_on_backup_source(self, verify, stream, run, wheelhouse):
        verify.side_effect = [(False, "关键模块无法导入"), (True, "ok"), (True, "ok")]
        stream.side_effect = [
            subprocess.CompletedProcess([], 1, "THESE PACKAGES DO NOT MATCH THE HASHES"),
            subprocess.CompletedProcess([], 0, "Successfully installed"),
        ]
        run.side_effect = [
            mock.Mock(returncode=0, stdout=""),
            mock.Mock(returncode=0, stdout="pip 24.0"),
            mock.Mock(returncode=1, stdout=""),
        ]
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            venv_python = paths.venv / "Scripts/python.exe"
            venv_python.parent.mkdir(parents=True)
            venv_python.touch()
            result = ensure_venv_and_litellm(paths, "python.exe", lambda *_: None)
        self.assertEqual(result, str(venv_python))
        self.assertEqual(stream.call_count, 2)
        for call in stream.call_args_list:
            self.assertIn("--no-cache-dir", call.args[0])
            self.assertNotIn("--resume-retries", call.args[0])
        self.assertIn("pypi.org", " ".join(stream.call_args_list[0].args[0]))
        self.assertIn("tsinghua.edu.cn", " ".join(stream.call_args_list[1].args[0]))

    @mock.patch("installer.core.bundled_lockfile", return_value=Path("offline/requirements.lock"))
    @mock.patch("installer.core.bundled_wheelhouse")
    @mock.patch("installer.core.run_command")
    @mock.patch("installer.core.run_command_stream")
    @mock.patch("installer.core.verify_litellm_environment")
    def test_offline_failure_falls_back_online(self, verify, stream, run, wheelhouse, lockfile):
        wheelhouse.return_value = Path("offline/wheels")
        verify.side_effect = [(False, "not installed"), (True, "ok"), (True, "ok")]
        stream.side_effect = [
            subprocess.CompletedProcess([], 1, "missing offline dependency"),
            subprocess.CompletedProcess([], 0, "Successfully installed"),
        ]
        run.side_effect = [
            mock.Mock(returncode=0, stdout=""),
            mock.Mock(returncode=0, stdout="pip 24.0"),
            mock.Mock(returncode=1, stdout=""),
        ]
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            venv_python = paths.venv / "Scripts/python.exe"
            venv_python.parent.mkdir(parents=True)
            venv_python.touch()
            ensure_venv_and_litellm(paths, "python.exe", lambda *_: None)
        self.assertEqual(stream.call_count, 2)
        offline_args = stream.call_args_list[0].args[0]
        online_args = stream.call_args_list[1].args[0]
        self.assertIn("--no-index", offline_args)
        self.assertIn("--find-links", offline_args)
        self.assertIn("--require-hashes", offline_args)
        self.assertIn("pypi.org", " ".join(online_args))
        self.assertIn("--require-hashes", online_args)
        self.assertIn("--force-reinstall", online_args)


class InstallFlowTests(unittest.TestCase):
    def test_repeat_install_does_not_lose_component_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            baseline = {
                "format": 2,
                "python": {"present": False, "path": "", "registrations": [], "runtime_existed": False},
                "claude": {"present": False, "native_existed": False, "share_existed": False,
                           "data_existed": False, "npm_existed": False},
                "vscode": {"present": False, "program_existed": False, "data_existed": False,
                           "local_data_existed": False, "extensions_dir_existed": False},
                "vscode_extension": {"present": False}, "user_path": [],
                "desktop_shortcut": {"primary_existed": False, "legacy_existed": False},
            }
            claude_calls = 0
            vscode_calls = 0

            def fake_claude(_progress, outcome, **_kwargs):
                nonlocal claude_calls
                claude_calls += 1
                outcome.update({"path": "claude.exe", "method": "native" if claude_calls == 1 else "existing", "manager": ""})
                return "claude.exe"

            def fake_vscode(_progress, _paths, outcome):
                nonlocal vscode_calls
                vscode_calls += 1
                outcome.update({"path": "code.cmd", "method": "official-installer" if vscode_calls == 1 else "existing", "manager": ""})
                return "code.cmd"

            with mock.patch("installer.core.is_supported_windows", return_value=True), \
                    mock.patch("installer.core.is_supported_architecture", return_value=True), \
                    mock.patch("installer.core.capture_install_baseline", return_value=baseline), \
                    mock.patch("installer.core.write_api_key"), \
                    mock.patch("installer.core.ensure_proxy_token", return_value="sk-local-test"), \
                    mock.patch("installer.core.ensure_python312", return_value=str(paths.runtime / "python.exe")), \
                    mock.patch("installer.core.ensure_venv_and_litellm", return_value="venv-python.exe"), \
                    mock.patch("installer.core.probe_install_sources", return_value={}), \
                    mock.patch("installer.core.ensure_claude", side_effect=fake_claude), \
                    mock.patch("installer.core.ensure_vscode", side_effect=fake_vscode), \
                    mock.patch("installer.core.vscode_extension_is_installed", return_value=True), \
                    mock.patch("installer.core.capture_user_environment", return_value={}), \
                    mock.patch("installer.core.set_user_environment"), \
                    mock.patch("installer.core.ensure_git", return_value="git-bash.exe"), \
                    mock.patch("installer.core.remove_user_environment"), \
                    mock.patch("installer.core.start_proxy"), \
                    mock.patch("installer.core.test_connection", return_value=(True, "ok")):
                options = core.InstallOptions(install_vscode=True)
                install_all(paths, "sk-secret", "deepseek-v4-flash", lambda *_: None, options)
                install_all(paths, "sk-secret", "deepseek-v4-flash", lambda *_: None, options)

            state = json.loads(paths.state.read_text(encoding="utf-8"))
            installed = state["ownership"]["installed"]
        self.assertNotIn("python", installed)
        self.assertTrue(installed["claude"]["installed_by_app"])
        self.assertEqual(installed["claude"]["method"], "native")
        self.assertTrue(installed["vscode"]["installed_by_app"])
        self.assertEqual(installed["vscode"]["method"], "official-installer")
        self.assertTrue(installed["vscode_extension"]["installed_by_app"])

    @mock.patch("installer.core.is_supported_windows", return_value=True)
    @mock.patch("installer.core.is_supported_architecture", return_value=True)
    @mock.patch("installer.core.capture_install_baseline", return_value={
        "format": 2,
        "python": {"present": False, "path": "", "registrations": [], "runtime_existed": False},
        "claude": {"present": False}, "vscode": {"present": False},
        "vscode_extension": {"present": False}, "user_path": [],
        "desktop_shortcut": {"primary_existed": False, "legacy_existed": False},
    })
    @mock.patch("installer.core.write_api_key")
    @mock.patch("installer.core.ensure_git", side_effect=core.InstallError("simulated Git failure"))
    def test_git_failure_stops_before_claude_install(self, git, credential, baseline, architecture, supported):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            with self.assertRaisesRegex(Exception, "simulated Git failure"):
                install_all(paths, "sk-secret", "deepseek-v4-flash", lambda *_: None)
            state = json.loads(paths.state.read_text(encoding="utf-8"))
        self.assertEqual(state["ownership"]["installed"], {})
        credential.assert_called_once_with("sk-secret")


class DomesticNetworkStrategyTests(unittest.TestCase):
    @mock.patch("installer.core.command_path", return_value=None)
    @mock.patch("installer.core._probe_url")
    def test_source_probe_runs_independent_sources_and_reports_winget(self, probe, command):
        probe.return_value = {
            "reachable": True, "usable": True, "status": 200,
            "latency_ms": 50, "error": "",
        }
        result = core.probe_install_sources(timeout=0.1)
        self.assertEqual(set(core.NETWORK_TARGETS), set(result) - {"winget"})
        self.assertFalse(result["winget"]["usable"])
        self.assertIn("npm 国内镜像可用", core.install_source_summary(result))

    def test_auto_order_prefers_atomic_installers_before_npm(self):
        probes = {
            "native": {"usable": True, "latency_ms": 500},
            "npm_mirror": {"usable": True, "latency_ms": 40},
            "npm_official": {"usable": False, "latency_ms": 0},
            "winget": {"usable": True, "latency_ms": 0},
        }
        order = core._claude_source_order("auto", probes)
        self.assertEqual(order[0], "winget")
        self.assertLess(order.index("native"), order.index("npm_mirror"))
        self.assertEqual(order[-1], "npm_official")

    @mock.patch("installer.core.resolve_claude_npm_version", return_value="2.1.231")
    @mock.patch("installer.core.managed_claude_path")
    @mock.patch("installer.core.ensure_managed_node", return_value=r"C:\managed\npm.cmd")
    @mock.patch("installer.core.command_path", return_value=None)
    @mock.patch("installer.core.run_command", return_value=mock.Mock(returncode=0, stdout="2.1.239"))
    def test_clean_machine_can_use_managed_node_and_domestic_npm(
        self, run, command, managed_node, managed_claude, resolve_version,
    ):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            managed_claude.side_effect = [None, paths.root / "managed-npm" / "claude.cmd"]
            outcome = {}
            result = ensure_claude(
                lambda *_: None, outcome, paths=paths, strategy="npm_mirror",
            )
        self.assertTrue(result.endswith("claude.cmd"))
        self.assertEqual(outcome["method"], "managed-npm")
        install_args = next(call.args[0] for call in run.call_args_list if "install" in call.args[0])
        self.assertIn("@anthropic-ai/claude-code@2.1.231", install_args)
        self.assertIn("--registry=https://registry.npmmirror.com", install_args)
        self.assertIn("--include=optional", install_args)

    @mock.patch("installer.core.command_path", return_value=None)
    @mock.patch("installer.core.download", side_effect=core.InstallError("WinError 10060"))
    def test_failed_source_raises_actionable_choice_error(self, fetch, command):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(core.InstallSourceError) as captured:
                ensure_claude(
                    lambda *_: None, paths=temp_paths(Path(folder)), strategy="native",
                )
        self.assertIn("WinError 10060", str(captured.exception))
        self.assertIn("npm_mirror", captured.exception.available)

    @mock.patch("installer.core._fetch_json")
    @mock.patch("installer.core._claude_version", return_value="2.1.200")
    def test_update_check_does_not_claim_external_claude_is_managed(self, version, fetch):
        fetch.return_value = {"dist-tags": {"latest": "2.1.239"}}
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            paths.state.write_text(json.dumps({
                "claude": "claude.exe",
                "ownership": {"installed": {"claude": {
                    "installed_by_app": False, "method": "existing", "path": "claude.exe",
                }}},
            }), encoding="utf-8")
            result = core.check_updates(paths)
        self.assertTrue(result["claude"]["available"])
        self.assertFalse(result["claude"]["managed"])
        self.assertIn("不会擅自更新", result["claude"]["message"])

    @mock.patch("installer.core.is_supported_windows", return_value=True)
    @mock.patch("installer.core.write_api_key")
    @mock.patch("installer.core.ensure_python312", return_value="python.exe")
    @mock.patch("installer.core.ensure_venv_and_litellm", return_value="venv-python.exe")
    @mock.patch("installer.core.ensure_claude", return_value="claude.exe")
    @mock.patch("installer.core.ensure_vscode", return_value="code.cmd")
    @mock.patch("installer.core.start_proxy")
    @mock.patch("installer.core.test_connection", return_value=(True, "连接成功，但 API 余额不足"))
    @mock.patch("installer.core.set_user_environment")
    @mock.patch("installer.core.capture_user_environment", return_value={})
    @mock.patch("installer.core.ensure_proxy_token", return_value="sk-local-test")
    def test_full_flow_accepts_balance_error(self, token, capture, set_env, test_conn, start, vscode, claude, venv, python, credential, supported):
        with tempfile.TemporaryDirectory() as folder:
            paths = temp_paths(Path(folder))
            events = []
            with mock.patch("installer.core.ensure_git", return_value="git-bash.exe"), \
                    mock.patch("installer.core.probe_install_sources", return_value={}), \
                    mock.patch("installer.core.remove_user_environment"):
                install_all(paths, "sk-secret", "deepseek-v4-flash", lambda a, b: events.append((a, b)))
            self.assertFalse(paths.config.exists())
            state = json.loads(paths.state.read_text(encoding="utf-8"))
            self.assertEqual(state["model"], "deepseek-v4-flash")
            credential.assert_called_once_with("sk-secret")
            set_env.assert_not_called()
            self.assertEqual(events[0][0], "系统检查")
            self.assertEqual(events[-1], ("完成", "连接成功，但 API 余额不足"))

    @mock.patch("installer.core.is_supported_windows", return_value=False)
    def test_rejects_non_windows(self, supported):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(Exception, "Windows 10/11"):
                install_all(temp_paths(Path(folder)), "sk-secret", "deepseek-v4-flash", lambda *_: None)

    @mock.patch("installer.core.is_supported_windows", return_value=True)
    @mock.patch("installer.core.is_supported_architecture", return_value=False)
    def test_rejects_non_x64(self, architecture, supported):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(Exception, "x64"):
                install_all(temp_paths(Path(folder)), "sk-secret", "deepseek-v4-flash", lambda *_: None)

    @mock.patch("installer.core.is_supported_windows", return_value=True)
    def test_rejects_empty_key(self, supported):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(Exception, "API Key"):
                install_all(temp_paths(Path(folder)), " ", "deepseek-v4-flash", lambda *_: None)

    @mock.patch("installer.core.is_supported_windows", return_value=True)
    def test_rejects_unknown_model(self, supported):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(Exception, "不支持的模型"):
                install_all(temp_paths(Path(folder)), "sk-secret", "unknown-model", lambda *_: None)


if __name__ == "__main__":
    unittest.main()
