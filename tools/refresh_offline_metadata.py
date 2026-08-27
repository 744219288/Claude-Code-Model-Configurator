"""Generate the direct-mode offline manifest and CycloneDX SBOM."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


APP_VERSION = "2.9.6"
NODE_VERSION = "22.23.2"
GIT_VERSION = "2.55.0.5"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_files(offline: Path) -> dict[str, dict[str, object]]:
    files: dict[str, dict[str, object]] = {}
    for path in sorted(offline.rglob("*")):
        if not path.is_file() or path.name in {"manifest.json", "README.txt"}:
            continue
        files[path.relative_to(offline).as_posix()] = {
            "sha256": sha256(path),
            "size": path.stat().st_size,
        }
    required = {
        f"node/node-v{NODE_VERSION}-win-x64.zip",
        f"git/PortableGit-{GIT_VERSION}-64-bit.7z.exe",
    }
    missing = sorted(required.difference(files))
    if missing:
        raise ValueError("离线资源缺少必需项：" + "、".join(missing))
    return files


def write_manifest(offline: Path, files: dict[str, dict[str, object]]) -> None:
    manifest = {
        "format": 3,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "app_version": APP_VERSION,
        "connection_mode": "direct-anthropic",
        "node_version": NODE_VERSION,
        "git_version": GIT_VERSION,
        "files": files,
    }
    (offline / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def write_sbom(project: Path, files: dict[str, dict[str, object]]) -> None:
    components = []
    rows = (
        ("Node.js managed runtime", NODE_VERSION, f"node/node-v{NODE_VERSION}-win-x64.zip"),
        ("PortableGit for Windows", GIT_VERSION, f"git/PortableGit-{GIT_VERSION}-64-bit.7z.exe"),
    )
    for name, version, relative in rows:
        components.append({
            "type": "application",
            "bom-ref": f"pkg:generic/{name.lower().replace(' ', '-')}@{version}?arch=x64&os=windows",
            "name": name,
            "version": version,
            "hashes": [{"alg": "SHA-256", "content": files[relative]["sha256"]}],
            "properties": [{"name": "offline-archive", "value": Path(relative).name}],
        })
    fingerprint = "\n".join(row["bom-ref"] + row["hashes"][0]["content"] for row in components)
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'ClaudeDeepSeekConfigurator/' + fingerprint)}",
        "version": 1,
        "metadata": {"component": {
            "type": "application", "name": "Claude-Code-DeepSeek-Configurator", "version": APP_VERSION,
        }},
        "components": components,
    }
    (project / "SBOM.cdx.json").write_text(
        json.dumps(sbom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", nargs="?", type=Path, default=Path(__file__).resolve().parent.parent)
    project = parser.parse_args().project.resolve()
    offline = project / "offline"
    files = build_files(offline)
    write_manifest(offline, files)
    write_sbom(project, files)
    print(f"已生成 V{APP_VERSION} manifest 与 SBOM，共锁定 {len(files)} 个文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
