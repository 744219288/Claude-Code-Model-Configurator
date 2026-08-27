$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$OutputRoot = Split-Path -Parent $ProjectRoot
$Exe = Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'dist-v296') -Filter '*.exe' | Select-Object -First 1
$ManifestPath = Join-Path $ProjectRoot 'offline\manifest.json'
$SbomPath = Join-Path $ProjectRoot 'SBOM.cdx.json'
$TestStaging = Join-Path $ProjectRoot 'release-v296-unsigned'
$SourceStaging = Join-Path $ProjectRoot 'source-v296'
$TestZip = Join-Path $OutputRoot 'Claude-Code-DeepSeek-V2.9.6-全新电脑直连修复-未签名测试包.zip'
$SourceZip = Join-Path $OutputRoot 'Claude-Code-DeepSeek-Configurator-V2.9.6-Secure-Source.zip'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

if (-not $Exe) { throw '未找到 dist-v296 中的 V2.9.6 EXE。' }
if ((Get-AuthenticodeSignature -LiteralPath $Exe.FullName).Status -eq 'Valid') {
    throw '当前 EXE 已有有效签名，请使用正式签名发布流程，不要生成未签名测试包。'
}
foreach ($Required in @($ManifestPath, $SbomPath, (Join-Path $ProjectRoot 'V2.9.6仅供测试-未签名.txt'))) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) { throw "缺少测试包文件：$Required" }
}
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
$Entries = @($Manifest.files.PSObject.Properties)
if ($Entries.Count -eq 0) { throw 'offline/manifest.json 为空。' }
foreach ($Entry in $Entries) {
    $Asset = Join-Path (Join-Path $ProjectRoot 'offline') $Entry.Name
    if (-not (Test-Path -LiteralPath $Asset -PathType Leaf)) { throw "离线文件不存在：$($Entry.Name)" }
    $Actual = (Get-FileHash -LiteralPath $Asset -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -ne ([string]$Entry.Value.sha256).ToLowerInvariant()) { throw "离线文件哈希不一致：$($Entry.Name)" }
}

function Reset-ProjectStaging([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        $Resolved = (Resolve-Path -LiteralPath $Path).Path
        $ResolvedProject = (Resolve-Path -LiteralPath $ProjectRoot).Path
        if (-not $Resolved.StartsWith($ResolvedProject + [IO.Path]::DirectorySeparatorChar)) {
            throw "临时目录越出项目范围：$Resolved"
        }
        Remove-Item -LiteralPath $Resolved -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Path | Out-Null
}

Reset-ProjectStaging $TestStaging
Copy-Item -LiteralPath $Exe.FullName -Destination (Join-Path $TestStaging 'Claude-Code-DeepSeek-一键配置器.exe')
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'offline') -Destination $TestStaging -Recurse
foreach ($Name in @(
    'V2.9.6仅供测试-未签名.txt', '微信发送说明.txt', '兼容性说明.txt',
    '新电脑模拟测试报告.txt', 'V2.9.6全新电脑直连修复报告.md',
    'V2.9.1完整卸载热修复报告.md', 'CHANGELOG.md', 'SBOM.cdx.json'
)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $Name) -Destination $TestStaging
}
$HashTargets = [ordered]@{
    'Claude-Code-DeepSeek-一键配置器.exe' = Join-Path $TestStaging 'Claude-Code-DeepSeek-一键配置器.exe'
    'offline/manifest.json' = Join-Path $TestStaging 'offline\manifest.json'
    'offline/node/node-v22.23.2-win-x64.zip' = Join-Path $TestStaging 'offline\node\node-v22.23.2-win-x64.zip'
    'offline/git/PortableGit-2.55.0.5-64-bit.7z.exe' = Join-Path $TestStaging 'offline\git\PortableGit-2.55.0.5-64-bit.7z.exe'
    'SBOM.cdx.json' = Join-Path $TestStaging 'SBOM.cdx.json'
}
$HashLines = foreach ($Item in $HashTargets.GetEnumerator()) {
    "$(Get-FileHash -LiteralPath $Item.Value -Algorithm SHA256 | Select-Object -ExpandProperty Hash)  $($Item.Key)"
}
[IO.File]::WriteAllLines((Join-Path $TestStaging 'SHA256SUMS'), $HashLines, $Utf8NoBom)
if (Test-Path -LiteralPath $TestZip) { Remove-Item -LiteralPath $TestZip -Force }
Compress-Archive -Path (Join-Path $TestStaging '*') -DestinationPath $TestZip -CompressionLevel Optimal

Reset-ProjectStaging $SourceStaging
foreach ($Directory in @('installer', 'hooks', 'tests', 'offline', 'tools', 'assets')) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $Directory) -Destination $SourceStaging -Recurse
}
foreach ($Name in @(
    'main.py', 'README.md', 'CHANGELOG.md', 'requirements-dev.txt', 'build.ps1',
    'build_offline_assets.ps1', 'sign_build.ps1', 'package_v2.ps1', 'package_unsigned_test.ps1',
    '微信发送说明.txt', '兼容性说明.txt', '新电脑模拟测试报告.txt',
    'V2.8升级报告.md', 'V2.8.1新电脑运行热修复报告.md', 'V2.8.1干净部署实测结果.json',
    'V2.8.2终端体验热修复报告.md', 'V2.9完整回滚与桌面启动升级报告.md',
    'V2.9.1完整卸载热修复报告.md', 'V2.9.2国内网络增强升级报告.md',
    'V2.9.6全新电脑直连修复报告.md', 'V2.9.6仅供测试-未签名.txt', 'SBOM.cdx.json'
)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $Name) -Destination $SourceStaging
}
if (Test-Path -LiteralPath $SourceZip) { Remove-Item -LiteralPath $SourceZip -Force }
Compress-Archive -Path (Join-Path $SourceStaging '*') -DestinationPath $SourceZip -CompressionLevel Optimal

Write-Host "未签名测试包：$TestZip"
Write-Host "可审计源码包：$SourceZip"
