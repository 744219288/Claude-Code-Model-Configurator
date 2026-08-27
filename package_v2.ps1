$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ReleaseVersion = 'V2.9.6'
$OutputRoot = Join-Path (Split-Path -Parent (Split-Path -Parent $ProjectRoot)) 'outputs'
$Exe = Join-Path $ProjectRoot 'dist-v296\Claude-Code-DeepSeek-一键配置器.exe'
$Staging = Join-Path $ProjectRoot 'release-v2'
$SourceStaging = Join-Path $ProjectRoot 'source-v2'
$ManifestPath = Join-Path $ProjectRoot 'offline\manifest.json'
$IntegrityPath = Join-Path $ProjectRoot 'release-integrity.json'
$SbomPath = Join-Path $ProjectRoot 'SBOM.cdx.json'
$SumsPath = Join-Path $ProjectRoot 'SHA256SUMS'

if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw '缺少离线组件清单。请先运行 build_offline_assets.ps1。'
}
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
$OfflineFiles = @($Manifest.files.PSObject.Properties)
if ($OfflineFiles.Count -eq 0) {
    throw '离线组件为空，已拒绝生成“本地优先”发送包。请先运行 build_offline_assets.ps1。'
}
foreach ($Entry in $OfflineFiles) {
    $Asset = Join-Path (Join-Path $ProjectRoot 'offline') $Entry.Name
    if (-not (Test-Path -LiteralPath $Asset -PathType Leaf)) {
        throw "离线组件清单中的文件不存在：$($Entry.Name)"
    }
}

if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) {
    throw '未找到构建后的 EXE，请先运行 build.ps1。'
}
foreach ($Required in @($IntegrityPath, $SbomPath, $SumsPath)) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
        throw "缺少发布安全产物：$Required。请依次运行 build_offline_assets.ps1、build.ps1、sign_build.ps1。"
    }
}
$Signature = Get-AuthenticodeSignature -LiteralPath $Exe
if ($Signature.Status -ne 'Valid' -or -not $Signature.SignerCertificate) {
    throw 'EXE 尚未通过有效代码签名，已拒绝打包。请先运行 sign_build.ps1。'
}
$Integrity = Get-Content -LiteralPath $IntegrityPath -Raw | ConvertFrom-Json
$ActualExeHash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualExeHash -ne ([string]$Integrity.sha256).ToLowerInvariant()) {
    throw 'EXE 与 release-integrity.json 的 SHA-256 不一致，已拒绝打包。'
}
if (Test-Path -LiteralPath $Staging) {
    $ResolvedStaging = (Resolve-Path -LiteralPath $Staging).Path
    $ResolvedProject = (Resolve-Path -LiteralPath $ProjectRoot).Path
    if (-not $ResolvedStaging.StartsWith($ResolvedProject + [IO.Path]::DirectorySeparatorChar)) {
        throw '临时发布目录不在项目目录中，已停止。'
    }
    Remove-Item -LiteralPath $ResolvedStaging -Recurse -Force
}
New-Item -ItemType Directory -Path $Staging | Out-Null
Copy-Item -LiteralPath $Exe -Destination (Join-Path $Staging 'Claude-Code-DeepSeek-一键配置器.exe')
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'offline') -Destination $Staging -Recurse
foreach ($ReleaseFile in @('微信发送说明.txt', '兼容性说明.txt', '新电脑模拟测试报告.txt', 'V2.9.6全新电脑直连修复报告.md', 'V2.8升级报告.md', 'V2.8.1新电脑运行热修复报告.md', 'V2.8.1干净部署实测结果.json', 'V2.8.2终端体验热修复报告.md', 'V2.9完整回滚与桌面启动升级报告.md', 'V2.9.1完整卸载热修复报告.md', 'V2.9.2国内网络增强升级报告.md', 'SBOM.cdx.json', 'SHA256SUMS', 'release-integrity.json', 'CHANGELOG.md')) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $ReleaseFile) -Destination $Staging
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$Zip = Join-Path $OutputRoot "微信发送包-Claude-Code-DeepSeek-$ReleaseVersion-安全增强版.zip"
if (Test-Path -LiteralPath $Zip) { Remove-Item -LiteralPath $Zip -Force }
Compress-Archive -Path (Join-Path $Staging '*') -DestinationPath $Zip -CompressionLevel Optimal
Copy-Item -LiteralPath $Exe -Destination (Join-Path $OutputRoot 'Claude-Code-DeepSeek-一键配置器.exe') -Force
Copy-Item -LiteralPath $IntegrityPath -Destination $OutputRoot -Force
Copy-Item -LiteralPath $SumsPath -Destination $OutputRoot -Force

if (Test-Path -LiteralPath $SourceStaging) {
    $ResolvedSourceStaging = (Resolve-Path -LiteralPath $SourceStaging).Path
    $ResolvedProject = (Resolve-Path -LiteralPath $ProjectRoot).Path
    if (-not $ResolvedSourceStaging.StartsWith($ResolvedProject + [IO.Path]::DirectorySeparatorChar)) {
        throw '源码临时目录不在项目目录中，已停止。'
    }
    Remove-Item -LiteralPath $ResolvedSourceStaging -Recurse -Force
}
New-Item -ItemType Directory -Path $SourceStaging | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'installer') -Destination $SourceStaging -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'hooks') -Destination $SourceStaging -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'tests') -Destination $SourceStaging -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'offline') -Destination $SourceStaging -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'tools') -Destination $SourceStaging -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'assets') -Destination $SourceStaging -Recurse
foreach ($Name in @('main.py', 'README.md', 'CHANGELOG.md', 'V2.9.6全新电脑直连修复报告.md', 'V2.8升级报告.md', 'V2.8.1新电脑运行热修复报告.md', 'V2.8.1干净部署实测结果.json', 'V2.8.2终端体验热修复报告.md', 'V2.9完整回滚与桌面启动升级报告.md', 'V2.9.1完整卸载热修复报告.md', 'V2.9.2国内网络增强升级报告.md', 'requirements-dev.txt', 'build.ps1', 'build_offline_assets.ps1', 'sign_build.ps1', 'package_v2.ps1', '微信发送说明.txt', '兼容性说明.txt', '新电脑模拟测试报告.txt', 'SBOM.cdx.json')) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $Name) -Destination $SourceStaging
}
$SourceZip = Join-Path $OutputRoot "Claude-Code-DeepSeek-Configurator-$ReleaseVersion-Secure-Source.zip"
if (Test-Path -LiteralPath $SourceZip) { Remove-Item -LiteralPath $SourceZip -Force }
Compress-Archive -Path (Join-Path $SourceStaging '*') -DestinationPath $SourceZip -CompressionLevel Optimal
Write-Host "发送包已生成：$Zip"
