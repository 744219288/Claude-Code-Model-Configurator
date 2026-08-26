# Changelog

## V2.9.2 - 2026-08-23

- 新增经 Node.js 官方 SHA-256 和 OpenJS Foundation 数字签名验证的 Node.js 22.23.2 便携运行环境；只解压到配置器目录，不修改系统 PATH。
- Claude Code 安装改为环境自适应多源：npm 国内镜像、npm 官方源、Anthropic 原生安装器和 WinGet；安装前并行探测实际端点并按可用性/延迟选路。
- 全新电脑没有 Node/npm 时自动使用受管理运行环境；系统 Node 低于 22 时也不污染或强制升级用户环境。
- WinGet 固定使用可信 `winget` 源，启用非交互和详细日志，保存返回代码与脱敏错误末尾。
- 普通错误弹窗升级为安装方案选择窗口，可切换国内镜像、官方 npm、官方原生、WinGet、检测到的代理或导出诊断。
- VS Code 与 Anthropic 扩展改为默认不勾选的可选增强，安装失败不再阻断 Claude Code CLI 核心功能。
- 新增“检查更新”入口：外部组件只提示，受管理 Claude Code 可按原来源更新并在失败时尝试恢复原版本；配置器自更新要求 HTTPS、SHA-256 和 Authenticode 发布者三重验证。
- 受管理 Node/npm、npm缓存、Claude安装前 `pending` 记录及更新状态纳入完整卸载和失败回滚。
- 桌面快捷方式改为打开配置器主界面，不再携带 `--launch-claude` 参数直接跳转终端；Claude Code 由用户在界面中主动启动。
- 自动化测试增至 87 项，并完成一次强制模拟“无 Claude、无 Node、无 npm”的隔离目录 npm 国内镜像真实安装与版本验证。

## V2.9.1 - 2026-08-22

- 修复 V2.8.x 删除私有 Python 文件却留下 Python 3.12.10 Windows 产品登记，导致新版进入 `Modify/Updating` 后以 1603 失败的问题。
- 增加 Python InstallPath 与“已安装的应用”双重登记检测；配置器专属旧登记先官方修复，必要时安全撤销后重新安装。
- Python 卸载失败时执行一次官方 `/repair` 后重试 `/uninstall`，最终反查安装目录键和产品登记，残留时拒绝报告成功。
- Python、Claude Code、VS Code、Anthropic 扩展和桌面入口全部在安装动作前写入 `pending` 所有权；安装中断也可回滚。
- 环境变量回滚点改为写入注册表前持久化，连接测试或快捷方式阶段失败也能恢复。
- 重复运行继承首次安装所有权，不会把配置器安装的组件错误改记为“用户原有”。
- 任何可运行的现有 npm/原生 Claude Code 都直接复用，修复 npm 2.1.239 被切换到较旧原生稳定版的问题。
- 诊断包增加 Python 安装、修复、旧登记撤销和卸载日志，仍保持凭据脱敏。
- 旧版缺少所有权日志时列出来源不明的 Claude Code、VS Code 和扩展；只有用户明确确认电脑原先没有这些组件，才纳入旧版彻底清理。
- 自动化测试增至 80 项。

## V2.9.0 - 2026-08-22

- 安装前创建组件基线和原子所有权日志，区分“安装前已有”和“由配置器新增”。
- “卸载”升级为完整事务回滚：由配置器新增的 Python、Claude Code、VS Code 和 Anthropic 扩展全部按原安装方式卸载；安装前已有组件严格保留。
- Python 私有安装关闭文件关联、开始菜单快捷方式、文档、IDLE 和 Tcl/Tk，并在回滚时调用官方安装程序 `/uninstall` 清理 Windows 安装记录。
- 修复桌面快捷方式一直未创建的问题；安装成功后必须生成 `Claude Code + DeepSeek.lnk`，目标为稳定配置器副本并带 `--launch-claude` 参数。
- 用户双击桌面图标后由配置器静默准备 LiteLLM，再直接打开 Claude Code。
- 后台删除程序增加 30 次重试、结果检测和失败提示，不再静默宣称卸载成功。
- 诊断包只输出脱敏所有权摘要，不导出安装前用户 PATH 或完整回滚日志。
- 自动化测试共 73 项，并完成真实 Windows `.lnk` 目标、参数和图标读取验证。

## V2.8.2 - 2026-08-22

- 修复从终端启动 Claude Code 时，LiteLLM 后台代理可能闪现空白控制台窗口的问题。
- Windows 后台代理改为 `CREATE_NO_WINDOW + CREATE_NEW_PROCESS_GROUP`，并使用 `SW_HIDE` 作为隐藏窗口兜底，不再组合冲突的 `DETACHED_PROCESS`。
- 已安装 Anthropic VS Code 扩展时直接复用，不再使用 `--force` 强制更新和长时间停留在扩展安装阶段。
- 自动化测试增至 68 项，增加后台无窗口参数、扩展复用和废弃扩展目录识别测试。

## V2.8.1 - 2026-08-22

- 修复部分新电脑从 `%TEMP%` 启动 Python 安装器时出现 `[WinError 5] 拒绝访问`。
- Python 3.12 与 VS Code 安装器改为进入 `%LOCALAPPDATA%\ClaudeDeepSeekConfigurator\installers`，验签后再执行。
- 增加 Python 标准用户目录、Program Files 和注册表发现；保留安装日志并加入诊断包。
- 修复 LiteLLM 控制台启动器 PID 与实际监听子进程 PID 不一致时无法安全停止代理的问题。
- Windows 拒绝执行时给出安全中心、杀毒软件和单位应用控制策略处理提示。
- 界面版本号统一读取核心版本，便于确认测试机是否拿到 V2.8.1。
- 自动化测试增至 66 项，并完成一次 102 wheel 干净目录真实部署、代理鉴权/停止和重复运行实测。

## V2.8 - 2026-08-22

- 为本机 LiteLLM 代理启用随机 `master_key`，移除固定 `dummy` 令牌作为正常鉴权方案。
- DeepSeek API Key 与代理令牌分别存入 Windows Credential Manager；API Key 只注入代理子进程。
- 新增受 PID 与监听端口双重约束的代理停止与 V2.7 旧代理迁移。
- 新增“检查状态”“导出诊断”“完整卸载”操作。
- 卸载恢复安装前环境变量、保留用户安装后的手工修改，并清理配置器 PATH、凭据、代理与私有运行环境。
- 为 102 个离线 wheel 生成 `requirements.lock`，安装时启用 pip `--require-hashes`。
- 新增 CycloneDX 1.5 `SBOM.cdx.json`。
- 新增 `sign_build.ps1`、`release-integrity.json` 和 `SHA256SUMS` 发布流程；未签名或哈希不一致的 EXE 无法正式打包。
- 打包版启动时验证 Authenticode 签名与签名后 SHA-256。
- 删除绕过 SmartScreen 的说明，补充第三方身份、许可风险和模型质量差异说明。
- 安装界面升级为浅色 Fluent 风格双栏布局，增加高 DPI 适配、聚焦式主流程、模型卡片、动态任务卡和紧凑辅助操作区。
- 新增原创应用图标 PNG/ICO，并嵌入窗口、EXE 与安装完成后自动创建的桌面快捷方式。
- 自动化测试从 52 项增至 62 项。

## V2.7

- 优先使用 Anthropic 官方原生 `claude.exe`，增加安全的 `claude.cmd` 入口与 52 项测试矩阵。
# V2.9.3

- 修复 Windows 凭据 API 最后错误码丢失导致的“删除凭据失败，错误码 0”。
- 修复稳定 EXE 未携带 offline 资源、微信/ZIP 临时路径运行后找不到受管理 Node.js。
- 切换到 DeepSeek Anthropic 官方接口直连，移除新安装流程中的 Python/LiteLLM/本地代理。
- 内置 PortableGit 2.55.0.5 和 Node.js 22.23.2，支持无开发环境的新电脑。
- npm 安装增加主包与 Windows x64 平台包同版本发布验证，拒绝不完整的 `@latest`。
- 模型更新为 DeepSeek V4 Flash / V4 Pro 1M；密钥仅注入子进程。
- 新增完整 payload 原子安装与二次校验，自动化测试增至 98 项。
