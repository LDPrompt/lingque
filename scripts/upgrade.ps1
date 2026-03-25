#
# 灵雀一键升级脚本 (Windows PowerShell)
#
# 用法:
#   .\scripts\upgrade.ps1          # 交互式
#   .\scripts\upgrade.ps1 -Yes     # 跳过确认
#

param(
    [switch]$Yes
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectDir

function Log($msg)  { Write-Host "[灵雀升级] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[!!] $msg" -ForegroundColor Yellow }
function Err($msg)  { Write-Host "[ERR] $msg" -ForegroundColor Red }

# ── 检测当前版本 ──
$CurrentVer = "unknown"
if (Test-Path "lobster\__init__.py") {
    $line = Get-Content "lobster\__init__.py" | Select-String '__version__' | Select-Object -First 1
    if ($line) {
        $CurrentVer = ($line -replace '.*=\s*[''"]', '' -replace '[''"].*', '').Trim()
    }
}
Log "当前版本: v$CurrentVer"

# ── 检测部署方式 ──
$DeployType = "source"
$dockerCompose = $null
if (Get-Command "docker" -ErrorAction SilentlyContinue) {
    if (Test-Path "docker-compose.yml") {
        $DeployType = "docker"
        $dockerCompose = "docker-compose.yml"
    }
    elseif (Test-Path "docker-compose.yaml") {
        $DeployType = "docker"
        $dockerCompose = "docker-compose.yaml"
    }
}
Log "部署方式: $DeployType"

# ── 检查远程更新 ──
Log "正在检查更新..."
git fetch origin main --quiet 2>$null
if ($LASTEXITCODE -ne 0) {
    Err "git fetch 失败，请检查网络"
    exit 1
}

$localHead = git rev-parse HEAD
$remoteHead = git rev-parse origin/main

if ($localHead -eq $remoteHead) {
    Ok "当前已是最新版本 v$CurrentVer，无需升级"
    exit 0
}

$commitsBehind = (git rev-list HEAD..origin/main --count).Trim()
Log "发现 $commitsBehind 个新提交"

if (-not $Yes) {
    Write-Host ""
    git log HEAD..origin/main --oneline --no-decorate | Select-Object -First 10
    Write-Host ""
    $confirm = Read-Host "是否升级? [Y/n]"
    if ($confirm -eq "n" -or $confirm -eq "N") {
        Log "已取消"
        exit 0
    }
}

# ── 备份 .env ──
if (Test-Path ".env") {
    $ts = Get-Date -Format "yyyyMMdd_HHmmss"
    Copy-Item ".env" ".env.backup.$ts"
    Ok "已备份 .env"
}

# ── 拉取代码 ──
Log "正在拉取最新代码..."
git stash --include-untracked -m "upgrade-auto" 2>$null
$stashed = ($LASTEXITCODE -eq 0)

git pull --rebase origin main
if ($LASTEXITCODE -ne 0) {
    Err "git pull 失败"
    if ($stashed) { git stash pop 2>$null }
    exit 1
}
Ok "代码已更新"

# ── 安装依赖 ──
$changedFiles = git diff "HEAD@{1}" --name-only 2>$null
if ($changedFiles -match "requirements.txt") {
    Log "检测到依赖变化，正在安装..."
    pip install -r requirements.txt --quiet 2>$null
    Ok "依赖安装完成"
}

# ── 数据迁移 ──
Log "正在检查数据迁移..."
try {
    python -m lobster.migrations 2>$null
    Ok "数据迁移检查完成"
} catch {
    Warn "迁移模块跳过"
}

# ── 恢复本地修改 ──
if ($stashed) {
    git stash pop 2>$null
    if ($LASTEXITCODE -ne 0) { Warn "恢复本地修改失败，可用 git stash list 查看" }
}

# ── 读取新版本 ──
$NewVer = "unknown"
if (Test-Path "lobster\__init__.py") {
    $line = Get-Content "lobster\__init__.py" | Select-String '__version__' | Select-Object -First 1
    if ($line) {
        $NewVer = ($line -replace '.*=\s*[''"]', '' -replace '[''"].*', '').Trim()
    }
}

# ── 重启服务 ──
Log "正在重启服务..."
if ($DeployType -eq "docker") {
    docker compose build --quiet
    docker compose up -d
    Ok "Docker 容器已重启"
} else {
    Warn "开发模式，请手动重启服务"
}

Write-Host ""
Ok "升级完成! v$CurrentVer -> v$NewVer"
Write-Host ""
