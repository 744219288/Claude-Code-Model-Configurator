$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$OfflineRoot = Join-Path $ProjectRoot 'offline'
$NodeFolder = Join-Path $OfflineRoot 'node'
$GitFolder = Join-Path $OfflineRoot 'git'
$NodeVersion = '22.23.2'
$GitVersion = '2.55.0.5'
$GitTag = 'v2.55.0.windows.5'
$ExpectedGitHash = '5aa8a20f6e9abb2c755f0e73c91c687701a46b309ad84a0ca6509380fa4ae290'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

New-Item -ItemType Directory -Force -Path $NodeFolder, $GitFolder | Out-Null

function Get-VerifiedDownload([string]$Url, [string]$Destination) {
    if (Test-Path -LiteralPath $Destination -PathType Leaf) { return }
    $Partial = "$Destination.part"
    Remove-Item -LiteralPath $Partial -Force -ErrorAction SilentlyContinue
    for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
        try {
            Write-Host "下载 $([IO.Path]::GetFileName($Destination))（$Attempt/3）..."
            Invoke-WebRequest -Uri $Url -OutFile $Partial -UseBasicParsing -TimeoutSec 600
            if ((Get-Item -LiteralPath $Partial).Length -lt 1MB) { throw '下载文件过小' }
            Move-Item -LiteralPath $Partial -Destination $Destination -Force
            return
        } catch {
            Remove-Item -LiteralPath $Partial -Force -ErrorAction SilentlyContinue
            if ($Attempt -eq 3) { throw }
            Start-Sleep -Seconds (2 * $Attempt)
        }
    }
}

$NodeArchive = Join-Path $NodeFolder "node-v$NodeVersion-win-x64.zip"
$NodeSums = Join-Path $NodeFolder 'SHASUMS256.txt'
$NodeBase = "https://nodejs.org/download/release/v$NodeVersion"
Get-VerifiedDownload "$NodeBase/SHASUMS256.txt" $NodeSums
Get-VerifiedDownload "$NodeBase/node-v$NodeVersion-win-x64.zip" $NodeArchive
$NodeLine = Get-Content -LiteralPath $NodeSums | Where-Object { $_ -match "node-v$([regex]::Escape($NodeVersion))-win-x64\.zip$" } | Select-Object -First 1
if (-not $NodeLine) { throw 'Node.js 官方 SHA-256 清单没有目标 Windows x64 文件。' }
$ExpectedNodeHash = ($NodeLine -split '\s+')[0].ToLowerInvariant()
$ActualNodeHash = (Get-FileHash -LiteralPath $NodeArchive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ExpectedNodeHash -ne $ActualNodeHash) { throw 'Node.js 官方 SHA-256 校验失败。' }

$GitArchive = Join-Path $GitFolder "PortableGit-$GitVersion-64-bit.7z.exe"
$GitUrl = "https://github.com/git-for-windows/git/releases/download/$GitTag/PortableGit-$GitVersion-64-bit.7z.exe"
Get-VerifiedDownload $GitUrl $GitArchive
$ActualGitHash = (Get-FileHash -LiteralPath $GitArchive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ExpectedGitHash -ne $ActualGitHash) { throw 'PortableGit GitHub 官方 SHA-256 校验失败。' }
$GitSignature = Get-AuthenticodeSignature -LiteralPath $GitArchive
if ($GitSignature.Status -ne 'Valid' -or -not $GitSignature.SignerCertificate -or $GitSignature.SignerCertificate.Subject -notmatch 'Johannes Schindelin') {
    throw "PortableGit 数字签名无效或发布者不匹配：$($GitSignature.Status) / $($GitSignature.SignerCertificate.Subject)"
}

# V2.9.3 uses DeepSeek's native Anthropic endpoint. These legacy proxy assets
# are deliberately excluded so a vulnerable/stale LiteLLM stack cannot ship.
foreach ($Legacy in @('python', 'wheels', 'requirements.lock')) {
    $Target = Join-Path $OfflineRoot $Legacy
    if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }
}

& (Join-Path $ProjectRoot 'tools\refresh_offline_metadata.ps1') -ProjectRoot $ProjectRoot
Write-Host "V2.9.3 离线组件完成：Node.js $NodeVersion + PortableGit $GitVersion"
