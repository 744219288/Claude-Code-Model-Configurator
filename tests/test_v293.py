from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import installer.core as core


def paths(root: Path) -> core.Paths:
    return core.Paths(root, root / "python", root / "venv", root / "config", root / "state.json", root / "log", root / "pid")


def make_payload(root: Path) -> None:
    files = {
        f"git/{core.GIT_ARCHIVE}": b"signed-portable-git",
        f"node/{core.NODE_ARCHIVE}": b"official-node-zip",
    }
    manifest_files = {}
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        manifest_files[relative] = {
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    (root / "manifest.json").write_text(
        json.dumps({"format": 3, "files": manifest_files}), encoding="utf-8",
    )


class DirectModePayloadTests(unittest.TestCase):
    def test_complete_payload_requires_git_and_node(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "offline"
            make_payload(root)
            self.assertTrue(core.verify_offline_payload(root)[0])
            (root / "git" / core.GIT_ARCHIVE).unlink()
            ok, detail = core.verify_offline_payload(root)
        self.assertFalse(ok)
        self.assertIn("git/", detail)

    def test_payload_is_copied_and_reverified_in_stable_program_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "portable" / "offline"
            make_payload(source)
            app_paths = paths(root / "Local" / core.APP_NAME)
            with mock.patch("installer.core.application_dir", return_value=source.parent):
                installed = core.install_managed_payload(app_paths)
            self.assertEqual(installed, root / "Local" / "Programs" / core.APP_NAME / "offline")
            self.assertTrue(core.verify_offline_payload(installed)[0])

    def test_missing_payload_reports_real_extraction_instruction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app_paths = paths(root / "Local" / core.APP_NAME)
            with mock.patch("installer.core.application_dir", return_value=root / "wechat-temp"):
                with self.assertRaisesRegex(core.InstallError, "完整解压"):
                    core.install_managed_payload(app_paths)


class StableNpmPairTests(unittest.TestCase):
    def test_stable_main_and_windows_package_must_match(self):
        calls = [
            subprocess.CompletedProcess([], 0, '{"stable":"2.1.231","latest":"2.1.232"}'),
            subprocess.CompletedProcess([], 1, "not found"),
            subprocess.CompletedProcess([], 0, '"2.1.231"'),
        ]
        with mock.patch("installer.core.run_command", side_effect=calls):
            version = core.resolve_claude_npm_version("npm.cmd", "https://registry.example", {})
        self.assertEqual(version, "2.1.231")

    def test_unsynchronised_npm_release_is_rejected(self):
        calls = [
            subprocess.CompletedProcess([], 0, '{"latest":"2.1.232"}'),
            subprocess.CompletedProcess([], 1, "not found"),
        ]
        with mock.patch("installer.core.run_command", side_effect=calls):
            with self.assertRaisesRegex(core.InstallError, "尚未同步"):
                core.resolve_claude_npm_version("npm.cmd", "https://registry.example", {})


class DirectGatewayTests(unittest.TestCase):
    def test_direct_gateway_has_no_local_proxy_or_litellm(self):
        values = core.gateway_environment(core.DEFAULT_MODEL, "sk-test")
        self.assertEqual(values["ANTHROPIC_BASE_URL"], core.DEEPSEEK_ANTHROPIC_URL)
        self.assertNotIn("127.0.0.1", " ".join(values.values()))
        self.assertNotIn("DEEPSEEK_API_KEY", values)

    def test_retired_model_names_are_rejected(self):
        for model in ("deepseek-chat", "deepseek-reasoner"):
            with self.assertRaises(core.InstallError):
                core.gateway_environment(model, "sk-test")


if __name__ == "__main__":
    unittest.main()
