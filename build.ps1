$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildVenv = Join-Path $ProjectRoot '.build-venv'

$SeedPython = $null
if ($env:CLAUDE_DEEPSEEK_BUILD_PYTHON312 -and (Test-Path -LiteralPath $env:CLAUDE_DEEPSEEK_BUILD_PYTHON312 -PathType Leaf)) {
    $SeedPython = $env:CLAUDE_DEEPSEEK_BUILD_PYTHON312
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $SeedPython = (py -3.12 -c 'import sys; print(sys.executable)') | Select-Object -Last 1
}
if (-not $SeedPython -or -not (Test-Path -LiteralPath $SeedPython -PathType Leaf)) {
    throw '未找到 Python 3.12。可安装 py launcher，或设置 CLAUDE_DEEPSEEK_BUILD_PYTHON312。'
}

if (-not (Test-Path -LiteralPath $BuildVenv)) {
    & $SeedPython -m venv $BuildVenv
}

$BuildPython = Join-Path $BuildVenv 'Scripts\python.exe'
& $BuildPython -m pip install --disable-pip-version-check --upgrade 'pyinstaller==6.16.0'
& $BuildPython -m PyInstaller --noconfirm --clean --onefile --windowed `
    --distpath (Join-Path $ProjectRoot 'dist-v296') `
    --workpath (Join-Path $ProjectRoot 'build-v296') `
    --specpath $ProjectRoot `
    --name 'Claude-Code-DeepSeek-一键配置器' `
    --icon (Join-Path $ProjectRoot 'assets\app_icon.ico') `
    --add-data "$(Join-Path $ProjectRoot 'assets');assets" `
    --additional-hooks-dir (Join-Path $ProjectRoot 'hooks') `
    --runtime-hook (Join-Path $ProjectRoot 'hooks\runtime_tkinter.py') `
    (Join-Path $ProjectRoot 'main.py')

Write-Host "构建完成：$(Join-Path $ProjectRoot 'dist-v296')"
