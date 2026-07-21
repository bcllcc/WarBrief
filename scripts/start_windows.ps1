param(
    [int]$Port = 8000,
    [switch]$Demo,
    [switch]$NoBrowser,
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

function Write-Step {
    param([string]$Message)
    Write-Host "[WarBrief] $Message" -ForegroundColor Cyan
}

function Refresh-ProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $wingetLinks = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"
    $parts = @($wingetLinks, $machinePath, $userPath) | Where-Object { $_ }
    $env:Path = ($parts -join ";")
}

function Find-CompatiblePython {
    $candidates = @(
        @{ Command = "py"; Arguments = @("-3.12") },
        @{ Command = "py"; Arguments = @("-3.11") },
        @{ Command = "python"; Arguments = @() }
    )

    foreach ($candidate in $candidates) {
        $command = [string]$candidate.Command
        $arguments = [string[]]$candidate.Arguments
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
            continue
        }
        try {
            $output = @(& $command @arguments -c "import sys; print(sys.executable); print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null)
            if ($LASTEXITCODE -ne 0 -or $output.Count -lt 2) {
                continue
            }
            $version = [Version]$output[1].Trim()
            if ($version -ge [Version]"3.11") {
                return [PSCustomObject]@{
                    Executable = $output[0].Trim()
                    Version = $version
                }
            }
        }
        catch {
            continue
        }
    }
    return $null
}

function Install-WithWinget {
    param(
        [string]$PackageId,
        [string]$DisplayName
    )

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        return $false
    }
    $answer = Read-Host "未检测到 $DisplayName。是否现在使用 winget 安装？[Y/n]"
    if ($answer -match "^[Nn]") {
        return $false
    }
    Write-Step "正在安装 $DisplayName ..."
    & winget install --id $PackageId -e --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "$DisplayName 安装失败，winget 返回代码 $LASTEXITCODE。"
    }
    Refresh-ProcessPath
    return $true
}

function Add-WingetFfmpegToPath {
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        return
    }
    $packagesRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (-not (Test-Path $packagesRoot)) {
        return
    }
    $ffmpeg = Get-ChildItem -Path $packagesRoot -Filter "ffmpeg.exe" -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($ffmpeg) {
        $env:Path = "$($ffmpeg.Directory.FullName);$env:Path"
    }
}

Write-Step "检查 Windows 本机运行环境"
Refresh-ProcessPath

$python = Find-CompatiblePython
if (-not $python) {
    [void](Install-WithWinget -PackageId "Python.Python.3.12" -DisplayName "Python 3.12")
    Refresh-ProcessPath
    $python = Find-CompatiblePython
}
if (-not $python) {
    throw "需要 Python 3.11 或 3.12。安装后重新双击本脚本。"
}
Write-Step "Python $($python.Version) -> $($python.Executable)"

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    [void](Install-WithWinget -PackageId "Gyan.FFmpeg" -DisplayName "FFmpeg / ffprobe")
    Refresh-ProcessPath
    Add-WingetFfmpegToPath
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    throw "未找到 ffmpeg/ffprobe。请完成 FFmpeg 安装、重新打开终端后再次运行。"
}
Write-Step "FFmpeg 环境正常"

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Step "创建项目虚拟环境 .venv"
    & $python.Executable -m venv ".venv"
    if ($LASTEXITCODE -ne 0) {
        throw "创建 Python 虚拟环境失败。"
    }
}

if (-not $SkipDependencyInstall) {
    Write-Step "安装或更新 WarBrief 依赖"
    & $venvPython -m pip install --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "pip 更新失败。"
    }
    & $venvPython -m pip install --disable-pip-version-check -e "."
    if ($LASTEXITCODE -ne 0) {
        throw "WarBrief 依赖安装失败。"
    }
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Step "已生成 .env；默认使用零密钥 Mock 模式"
}

if ($Demo) {
    Write-Step "开始生成离线端到端 Demo"
    & $venvPython -m warbrief demo
    if ($LASTEXITCODE -ne 0) {
        throw "Demo 生成失败。"
    }
    Write-Host ""
    Write-Host "Demo 已完成。输出位于 data\runtime\jobs。" -ForegroundColor Green
    Read-Host "按 Enter 关闭窗口"
    exit 0
}

$url = "http://127.0.0.1:$Port"
Write-Host ""
Write-Host "WarBrief 即将启动：$url" -ForegroundColor Green
Write-Host "停止服务请在本窗口按 Ctrl+C。" -ForegroundColor Yellow
Write-Host "配置真实模型、配音和素材时，编辑项目根目录的 .env。" -ForegroundColor Yellow
Write-Host ""

if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        param($TargetUrl)
        Start-Sleep -Seconds 3
        Start-Process $TargetUrl
    } -ArgumentList $url | Out-Null
}

& $venvPython -m warbrief serve --host "127.0.0.1" --port $Port
