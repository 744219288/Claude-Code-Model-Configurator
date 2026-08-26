param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Fa-f ]+$')]
    [string]$CertificateThumbprint,

    [string]$ExpectedPublisher = '',

    [ValidatePattern('^https://')]
    [string]$TimestampUrl = 'https://timestamp.digicert.com'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Exe = Join-Path $ProjectRoot 'dist-v293\Claude-Code-DeepSeek-一键配置器.exe'
$IntegrityPath = Join-Path $ProjectRoot 'release-integrity.json'
$SumsPath = Join-Path $ProjectRoot 'SHA256SUMS'

if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) {
    throw '未找到构建后的 EXE，请先运行 build.ps1。'
}
$SignTool = Get-Command signtool.exe -ErrorAction SilentlyContinue
if (-not $SignTool) {
    throw '未找到 signtool.exe。请安装 Windows SDK，并在开发者命令提示符中运行。'
}

$Thumbprint = $CertificateThumbprint.Replace(' ', '')
& $SignTool.Source sign /sha1 $Thumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $Exe
if ($LASTEXITCODE -ne 0) {
    throw "signtool 签名失败，退出码：$LASTEXITCODE"
}
& $SignTool.Source verify /pa /all /v $Exe
if ($LASTEXITCODE -ne 0) {
    throw "signtool 验证失败，退出码：$LASTEXITCODE"
}

$Signature = Get-AuthenticodeSignature -LiteralPath $Exe
if ($Signature.Status -ne 'Valid' -or -not $Signature.SignerCertificate) {
    throw "签名后 Windows 验证失败：$($Signature.Status)"
}
$Publisher = $Signature.SignerCertificate.Subject
if ($ExpectedPublisher -and $Publisher.IndexOf($ExpectedPublisher, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
    throw "签名发布者不匹配：$Publisher"
}

$Integrity = [ordered]@{
    format = 1
    version = '2.9.3'
    executable = 'Claude-Code-DeepSeek-一键配置器.exe'
    sha256 = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash.ToLowerInvariant()
    publisher = $Publisher
    signed_at = (Get-Date).ToUniversalTime().ToString('o')
}
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($IntegrityPath, ($Integrity | ConvertTo-Json -Depth 4), $Utf8NoBom)

$HashFiles = @($Exe, $IntegrityPath, (Join-Path $ProjectRoot 'offline\manifest.json'), (Join-Path $ProjectRoot 'SBOM.cdx.json'))
$HashLines = foreach ($File in $HashFiles) {
    if (Test-Path -LiteralPath $File -PathType Leaf) {
        $Hash = (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
        "$Hash  $([IO.Path]::GetFileName($File))"
    }
}
[IO.File]::WriteAllLines($SumsPath, $HashLines, $Utf8NoBom)
Write-Host "签名、启动完整性记录和 SHA256SUMS 已生成。发布者：$Publisher"
