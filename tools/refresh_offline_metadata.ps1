param(
    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
)

$ErrorActionPreference = 'Stop'
$OfflineRoot = Join-Path $ProjectRoot 'offline'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$NodeVersion = '22.23.2'
$GitVersion = '2.55.0.5'

$Files = [ordered]@{}
Get-ChildItem -LiteralPath $OfflineRoot -Recurse -File |
    Where-Object { $_.Name -notin @('manifest.json', 'README.txt') } |
    Sort-Object FullName |
    ForEach-Object {
        $Relative = $_.FullName.Substring($OfflineRoot.Length).TrimStart([char[]]'\/').Replace('\', '/')
        $Files[$Relative] = [ordered]@{
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            size = $_.Length
        }
    }

$Required = @(
    "node/node-v$NodeVersion-win-x64.zip",
    "git/PortableGit-$GitVersion-64-bit.7z.exe"
)
foreach ($Name in $Required) {
    if (-not $Files.Contains($Name)) { throw "离线资源缺少必需项：$Name" }
}

$Manifest = [ordered]@{
    format = 3
    created_at = (Get-Date).ToUniversalTime().ToString('o')
    app_version = '2.9.3'
    connection_mode = 'direct-anthropic'
    node_version = $NodeVersion
    git_version = $GitVersion
    files = $Files
}
[IO.File]::WriteAllText((Join-Path $OfflineRoot 'manifest.json'), ($Manifest | ConvertTo-Json -Depth 6), $Utf8NoBom)

$Components = @(
    [ordered]@{
        type = 'application'; name = 'Node.js managed runtime'; version = $NodeVersion
        hashes = @([ordered]@{ alg = 'SHA-256'; content = $Files["node/node-v$NodeVersion-win-x64.zip"].sha256 })
        properties = @([ordered]@{ name = 'offline-archive'; value = "node-v$NodeVersion-win-x64.zip" })
    },
    [ordered]@{
        type = 'application'; name = 'PortableGit for Windows'; version = $GitVersion
        hashes = @([ordered]@{ alg = 'SHA-256'; content = $Files["git/PortableGit-$GitVersion-64-bit.7z.exe"].sha256 })
        properties = @([ordered]@{ name = 'offline-archive'; value = "PortableGit-$GitVersion-64-bit.7z.exe" })
    }
)
$Sbom = [ordered]@{
    bomFormat = 'CycloneDX'; specVersion = '1.5'; version = 1
    metadata = [ordered]@{ component = [ordered]@{ type = 'application'; name = 'Claude-Code-DeepSeek-Configurator'; version = '2.9.3' } }
    components = $Components
}
[IO.File]::WriteAllText((Join-Path $ProjectRoot 'SBOM.cdx.json'), ($Sbom | ConvertTo-Json -Depth 8), $Utf8NoBom)
Write-Host "已生成 V2.9.3 manifest 与 SBOM，共锁定 $($Files.Count) 个文件。"
