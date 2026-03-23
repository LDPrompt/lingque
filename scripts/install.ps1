#
# 🐦 灵雀 LingQue 一键安装脚本 (Windows PowerShell)
#
# 用法 (管理员 PowerShell):
#   irm https://raw.githubusercontent.com/LDPrompt/lingque/main/scripts/install.ps1 | iex
#
# 或者先下载再执行:
#   Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LDPrompt/lingque/main/scripts/install.ps1" -OutFile install.ps1
#   .\install.ps1
#

$ErrorActionPreference = "Continue"

function Log($msg)  { Write-Host "[灵雀] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[!!] $msg" -ForegroundColor Yellow }
function Err($msg)  { Write-Host "[ERR] $msg" -ForegroundColor Red; exit 1 }

$RepoUrl = "https://github.com/LDPrompt/lingque.git"
$InstallDir = if ($env:LINGQUE_INSTALL_DIR) { $env:LINGQUE_INSTALL_DIR } else { "$env:USERPROFILE\lingque" }

Write-Host ""
Write-Host "   🐦 灵雀 LingQue 一键安装" -ForegroundColor White
Write-Host "   灵动 Prompt 出品的私人 AI Agent" -ForegroundColor Cyan
Write-Host ""

# =====================================================================
# .env 交互式配置
# =====================================================================
function Setup-Env {
    if (Test-Path ".env") {
        Warn ".env 已存在，跳过配置"
        return
    }

    Copy-Item ".env.example" ".env"
    Write-Host ""
    Write-Host "── 快速配置 ──" -ForegroundColor White
    Write-Host ""

    Write-Host "选择默认 AI 模型:"
    Write-Host "  1) DeepSeek (推荐，性价比高)"
    Write-Host "  2) 豆包 Doubao (字节跳动)"
    Write-Host "  3) OpenAI (GPT)"
    Write-Host "  4) Anthropic (Claude)"
    Write-Host "  5) 其他 (稍后手动配置)"
    Write-Host ""
    $llmChoice = Read-Host "请选择 [1-5] (默认 1)"
    if (-not $llmChoice) { $llmChoice = "1" }

    $envContent = Get-Content ".env" -Raw

    switch ($llmChoice) {
        "1" {
            $apiKey = Read-Host "请输入 DeepSeek API Key"
            if ($apiKey) {
                $envContent = $envContent -replace "(?m)^LLM_PROVIDER=.*", "LLM_PROVIDER=deepseek"
                $envContent = $envContent -replace "(?m)^#DEEPSEEK_API_KEY=.*", "DEEPSEEK_API_KEY=$apiKey"
            }
        }
        "2" {
            $apiKey = Read-Host "请输入豆包 API Key"
            if ($apiKey) {
                $envContent = $envContent -replace "(?m)^LLM_PROVIDER=.*", "LLM_PROVIDER=doubao"
                $envContent = $envContent -replace "(?m)^# DOUBAO_API_KEY=.*", "DOUBAO_API_KEY=$apiKey"
            }
        }
        "3" {
            $apiKey = Read-Host "请输入 OpenAI API Key"
            if ($apiKey) {
                $envContent = $envContent -replace "(?m)^LLM_PROVIDER=.*", "LLM_PROVIDER=openai"
                $envContent = $envContent -replace "(?m)^# OPENAI_API_KEY=.*", "OPENAI_API_KEY=$apiKey"
            }
        }
        "4" {
            $apiKey = Read-Host "请输入 Anthropic API Key"
            if ($apiKey) {
                $envContent = $envContent -replace "(?m)^LLM_PROVIDER=.*", "LLM_PROVIDER=anthropic"
                $envContent = $envContent -replace "(?m)^# ANTHROPIC_API_KEY=.*", "ANTHROPIC_API_KEY=$apiKey"
            }
        }
        default {
            Warn "请稍后编辑 .env 文件配置 LLM"
        }
    }

    Write-Host ""
    Write-Host "选择使用方式:"
    Write-Host "  1) 命令行 (CLI，调试用)"
    Write-Host "  2) 飞书机器人"
    Write-Host "  3) 钉钉机器人"
    Write-Host "  4) 飞书 + 命令行"
    Write-Host ""
    $chChoice = Read-Host "请选择 [1-4] (默认 1)"
    if (-not $chChoice) { $chChoice = "1" }

    switch ($chChoice) {
        "2" {
            $envContent = $envContent -replace "(?m)^CHANNELS=.*", "CHANNELS=feishu"
            Write-Host ""
            $fsId = Read-Host "飞书 App ID"
            $fsSecret = Read-Host "飞书 App Secret"
            $fsToken = Read-Host "飞书 Verification Token"
            $fsEncrypt = Read-Host "飞书 Encrypt Key (可选，直接回车跳过)"
            if ($fsId) { $envContent = $envContent -replace "(?m)^FEISHU_APP_ID=.*", "FEISHU_APP_ID=$fsId" }
            if ($fsSecret) { $envContent = $envContent -replace "(?m)^FEISHU_APP_SECRET=.*", "FEISHU_APP_SECRET=$fsSecret" }
            if ($fsToken) { $envContent = $envContent -replace "(?m)^FEISHU_VERIFICATION_TOKEN=.*", "FEISHU_VERIFICATION_TOKEN=$fsToken" }
            if ($fsEncrypt) { $envContent = $envContent -replace "(?m)^FEISHU_ENCRYPT_KEY=.*", "FEISHU_ENCRYPT_KEY=$fsEncrypt" }
        }
        "3" {
            $envContent = $envContent -replace "(?m)^CHANNELS=.*", "CHANNELS=dingtalk"
            Write-Host ""
            $dtKey = Read-Host "钉钉 App Key"
            $dtSecret = Read-Host "钉钉 App Secret"
            if ($dtKey) { $envContent = $envContent -replace "(?m)^DINGTALK_APP_KEY=.*", "DINGTALK_APP_KEY=$dtKey" }
            if ($dtSecret) { $envContent = $envContent -replace "(?m)^DINGTALK_APP_SECRET=.*", "DINGTALK_APP_SECRET=$dtSecret" }
        }
        "4" {
            $envContent = $envContent -replace "(?m)^CHANNELS=.*", "CHANNELS=cli,feishu"
            Write-Host ""
            $fsId = Read-Host "飞书 App ID"
            $fsSecret = Read-Host "飞书 App Secret"
            $fsToken = Read-Host "飞书 Verification Token"
            $fsEncrypt = Read-Host "飞书 Encrypt Key (可选，直接回车跳过)"
            if ($fsId) { $envContent = $envContent -replace "(?m)^FEISHU_APP_ID=.*", "FEISHU_APP_ID=$fsId" }
            if ($fsSecret) { $envContent = $envContent -replace "(?m)^FEISHU_APP_SECRET=.*", "FEISHU_APP_SECRET=$fsSecret" }
            if ($fsToken) { $envContent = $envContent -replace "(?m)^FEISHU_VERIFICATION_TOKEN=.*", "FEISHU_VERIFICATION_TOKEN=$fsToken" }
            if ($fsEncrypt) { $envContent = $envContent -replace "(?m)^FEISHU_ENCRYPT_KEY=.*", "FEISHU_ENCRYPT_KEY=$fsEncrypt" }
        }
        default {
            $envContent = $envContent -replace "(?m)^CHANNELS=.*", "CHANNELS=cli"
        }
    }

    Set-Content ".env" $envContent -NoNewline
    Ok "配置已保存到 .env"
}


# =====================================================================
# 主流程
# =====================================================================

Log "正在检查系统环境..."

Write-Host ""
Write-Host "请选择安装方式:" -ForegroundColor White
Write-Host "  1) Docker 安装 (推荐，隔离干净)"
Write-Host "  2) 源码安装 (直接运行 Python)"
Write-Host ""
$installMode = Read-Host "请输入 [1/2] (默认 2)"
if (-not $installMode) { $installMode = "2" }


# ── Docker 安装 ──
if ($installMode -eq "1") {
    if (-not (Get-Command "docker" -ErrorAction SilentlyContinue)) {
        Err "未检测到 Docker。请先安装 Docker Desktop: https://www.docker.com/products/docker-desktop/"
    }
    Ok "Docker 已就绪"

    if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {
        Err "未检测到 git。请先安装: https://git-scm.com/download/win"
    }

    Log "正在下载灵雀..."
    if (Test-Path $InstallDir) {
        Warn "目录已存在: $InstallDir"
        $ow = Read-Host "是否覆盖? [y/N]"
        if ($ow -ne "y") { Err "安装取消" }
        Remove-Item $InstallDir -Recurse -Force
    }

    git clone --depth 1 $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) { Err "下载失败，请检查网络" }
    Ok "下载完成"

    Set-Location $InstallDir
    Setup-Env

    Log "正在构建并启动..."
    docker compose up -d --build

    Write-Host ""
    Ok "🎉 灵雀安装完成!"
    Write-Host ""
    Write-Host "  安装目录: $InstallDir" -ForegroundColor White
    Write-Host "  查看日志: docker compose logs -f" -ForegroundColor Cyan
    Write-Host "  停止服务: docker compose down" -ForegroundColor Cyan
    Write-Host "  升级版本: .\scripts\upgrade.ps1" -ForegroundColor Cyan
    Write-Host ""
    exit 0
}


# ── 源码安装 ──

# 检查 Python
$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "(\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 11) {
                $pythonCmd = $cmd
                break
            }
        }
    } catch {}
}

if (-not $pythonCmd) {
    Err "未找到 Python 3.11+。请先安装: https://www.python.org/downloads/`n  安装时务必勾选 'Add Python to PATH'"
}
Ok "Python 已就绪: $(& $pythonCmd --version)"

# 检查 git
if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {
    Err "未检测到 git。请先安装: https://git-scm.com/download/win"
}

# 克隆
Log "正在下载灵雀..."
if (Test-Path $InstallDir) {
    Warn "目录已存在: $InstallDir"
    $ow = Read-Host "是否覆盖? [y/N]"
    if ($ow -ne "y") { Err "安装取消" }
    Remove-Item $InstallDir -Recurse -Force
}

git clone --depth 1 $RepoUrl $InstallDir
if ($LASTEXITCODE -ne 0) { Err "下载失败，请检查网络" }
Ok "下载完成"

Set-Location $InstallDir

# 虚拟环境
Log "正在创建 Python 虚拟环境..."
& $pythonCmd -m venv venv
& ".\venv\Scripts\Activate.ps1"
Ok "虚拟环境已创建"

# 安装依赖
Log "正在安装依赖 (首次约需 2-5 分钟)..."
pip install --upgrade pip -q 2>$null
pip install -r requirements.txt -q
Ok "依赖安装完成"

# 浏览器
Log "正在安装浏览器引擎..."
try {
    playwright install chromium 2>$null
    Ok "浏览器引擎已安装"
} catch {
    Warn "浏览器引擎安装跳过 (可稍后运行: playwright install chromium)"
}

# 配置 .env
Setup-Env

# 创建启动快捷脚本
$startScript = @"
@echo off
cd /d "$InstallDir"
call venv\Scripts\activate.bat
python -m lobster.main
pause
"@
Set-Content "启动灵雀.bat" $startScript
Ok "已创建 启动灵雀.bat 快捷方式"

Write-Host ""
Ok "🎉 灵雀安装完成!"
Write-Host ""
Write-Host "  安装目录: $InstallDir" -ForegroundColor White
Write-Host "  启动灵雀: 双击 启动灵雀.bat 或运行:" -ForegroundColor White
Write-Host "    cd $InstallDir" -ForegroundColor Cyan
Write-Host "    .\venv\Scripts\Activate.ps1" -ForegroundColor Cyan
Write-Host "    python -m lobster.main" -ForegroundColor Cyan
Write-Host ""
Write-Host "  修改配置: notepad $InstallDir\.env" -ForegroundColor Cyan
Write-Host "  升级版本: .\scripts\upgrade.ps1" -ForegroundColor Cyan
Write-Host ""
