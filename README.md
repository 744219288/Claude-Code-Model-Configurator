# Claude Code + DeepSeek 一键配置器 V2.9.3

面向全新 Windows 10/11 x64 电脑的一键安装器。电脑不需要预装 Python、Node.js、npm、Git、Claude Code 或 VS Code；用户输入 DeepSeek API Key 后，配置器会准备必要组件、安装 Claude Code、验证 API，并创建桌面和终端入口。

## V2.9.3 的关键改变

- 直接连接 DeepSeek 官方 Anthropic 兼容接口 `https://api.deepseek.com/anthropic`。
- 不再安装或运行 Python、LiteLLM、venv 和 `127.0.0.1:4000` 本地代理。
- 模型更新为 `deepseek-v4-flash` 与 `deepseek-v4-pro[1m]`；退休旧的 `deepseek-chat` / `deepseek-reasoner`。
- 内置并校验 PortableGit 2.55.0.5，满足 Claude Code 在原生 Windows 上对 Git Bash 的要求。
- 内置并校验 Node.js 22.23.2，系统没有 npm 时使用隔离的受管理 Node/npm。
- npm 安装不直接使用竞态风险较高的 `@latest`。配置器先确认 Claude Code 主包与 `win32-x64` 平台包存在完全相同版本，再安装该固定版本。
- 首次运行先校验整个 `offline` 清单，再原子复制到 `%LOCALAPPDATA%\Programs\ClaudeDeepSeekConfigurator`。稳定桌面入口不再依赖微信临时目录或原始 ZIP 所在位置。
- Windows 凭据 API 改为 `WinDLL(..., use_last_error=True)`；删除凭据失败不再错误显示“Windows 错误码 0”。
- API Key 只存入当前用户 Windows 凭据管理器，只在启动 Claude Code/VS Code 子进程时注入，不写入状态文件或永久用户环境变量。

## 发送与使用

1. 发送整个 V2.9.3 ZIP，不要只发送 EXE。
2. 在新电脑上把 ZIP 保存到本地，右键“全部解压”。
3. 从解压后的目录运行 `Claude-Code-DeepSeek-一键配置器.exe`，不要在微信预览或压缩包预览中直接运行。
4. 输入 DeepSeek API Key，选择模型，点击“开始安装并配置”。
5. 成功后可点击“启动 Claude Code”，或关闭旧终端、打开新终端后输入 `claude`。

若 ZIP 没有完整解压、`offline` 缺失或文件被修改，程序会在执行任何内置组件前中止，并显示 EXE 目录、预期资源目录和具体校验失败项。

## 安装顺序

1. 检查 Windows 版本、x64 架构、写入权限和磁盘空间。
2. 校验 `offline/manifest.json` 中的全部文件，并安装固定资源副本。
3. 把 API Key 保存到 Windows 凭据管理器。
4. 发现已有 Git Bash；没有时解压内置 PortableGit。
5. 探测 WinGet、Anthropic 官方源、npm 国内镜像与 npm 官方源。
6. 优先使用原子发布的 WinGet/官方安装器；失败时使用隔离 Node/npm 回退。
7. npm 回退先验证主包和 Windows x64 平台包版本同步，再固定版本安装并运行 `claude --version`。
8. 可选安装 VS Code 与 Anthropic 扩展。
9. 清理 V2.7—V2.9.2 遗留的本地代理环境变量，配置子进程级 DeepSeek 直连。
10. 调用 DeepSeek API 做真实连接测试，成功后创建稳定入口。

## 构建与测试

构建机需要 Python 3.12，仅用于生成 EXE；新电脑运行 EXE 不需要 Python。

```powershell
$env:CLAUDE_DEEPSEEK_BUILD_PYTHON312 = 'C:\Path\To\python.exe'
.\build_offline_assets.ps1
.\build.ps1
python -m unittest discover -s tests
.\package_unsigned_test.ps1
```

`build_offline_assets.ps1` 从 Node.js 官方发行页和 Git for Windows 官方 GitHub Release 下载固定组件，校验 Node 官方 SHA-256、GitHub 资产 SHA-256 与 PortableGit Authenticode 发布者，并重新生成 manifest 与 CycloneDX SBOM。

正式发布仍必须使用受信任代码签名证书运行 `sign_build.ps1`；未签名测试包可能触发 SmartScreen，这是测试包属性，不应通过关闭 Windows 安全功能规避。

## 已执行验证

- 98 项自动化测试通过。
- 内置 PortableGit 实际解压，`git --version` 与 `bash.exe` 验证通过。
- 内置 Node.js 实际解压：Node v22.23.2、npm 10.9.8 验证通过。
- 受管理 npm 通过国内镜像查询到主包/Windows 平台包共同版本 2.1.231，实际安装后 `claude --version` 通过。
- 离线 manifest 当前锁定 PortableGit、Node ZIP 和 Node 官方 SHA 清单。

真正宣称“任何正常电脑都绝不出问题”是不负责任的：企业应用控制、杀毒误报、断网、服务端故障或 DeepSeek 账户状态仍可能阻止安装。但 V2.9.3 已消除已知的凭据错误码、临时路径资源丢失、LiteLLM/Python 代理链和 npm 平台包发布竞态，并对每个剩余失败点提供可诊断错误。

本项目是第三方社区工具，不隶属于或代表 Anthropic、DeepSeek、Microsoft、Node.js 或 Git for Windows。
