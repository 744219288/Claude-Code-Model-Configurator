此目录是 V2.9.3 在全新 Windows 10/11 x64 电脑上运行所需的本地基础组件：

1. git/PortableGit-2.55.0.5-64-bit.7z.exe
   提供 Claude Code 在 Windows 上必需的 Git Bash；只解压到配置器管理目录。
2. node/node-v22.23.2-win-x64.zip
   提供 npm 安装回退所需的隔离 Node.js 22/npm；不修改系统 Node 或系统 PATH。
3. manifest.json
   锁定每个离线文件的大小和 SHA-256；任何缺失或篡改都会在执行前中止。

V2.9.3 直接使用 https://api.deepseek.com/anthropic，不再安装 Python、LiteLLM，
也不再启动 127.0.0.1:4000 本地代理。Claude Code 本体会从 WinGet、Anthropic
官方安装源、npm 国内镜像或 npm 官方源安装，并在成功后执行版本验证。

重要：发送给新电脑时必须发送整个 ZIP。请先“全部解压”，不要从微信临时目录或
压缩包预览窗口单独双击 EXE。
