#
# 🐦 灵雀 LingQue 一键安装 — 源码版 (Windows)
#
# 全自动：缺 Python / Git 会自动安装，用户无需手动下载任何东西
#
# 用法 (管理员 PowerShell):
#   irm https://raw.githubusercontent.com/LDPrompt/lingque/main/scripts/install-source.ps1 | iex
#

try {
    if ((Get-ExecutionPolicy -Scope Process) -eq "Restricted") {
        Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force
    }
} catch {}

$ErrorActionPreference = "Continue"

function Log($msg)  { Write-Host "[灵雀] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[!!] $msg" -ForegroundColor Yellow }
function Err($msg)  { Write-Host "[ERR] $msg" -ForegroundColor Red; Read-Host "按回车退出"; exit 1 }

$RepoUrl = "https://github.com/LDPrompt/lingque.git"
$InstallDir = if ($env:LINGQUE_INSTALL_DIR) { $env:LINGQUE_INSTALL_DIR } else { "$env:USERPROFILE\lingque" }
$TempDir = "$env:TEMP\lingque_setup"

Write-Host ""
Write-Host "   ========================================" -ForegroundColor Cyan
Write-Host "   🐦 灵雀 LingQue 一键安装 (源码版)" -ForegroundColor White
Write-Host "   灵动 Prompt 出品的私人 AI Agent" -ForegroundColor Cyan
Write-Host "   ========================================" -ForegroundColor Cyan
Write-Host ""

# =====================================================================
# 工具函数
# =====================================================================

function Refresh-Path {
    $machinePath = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    $userPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $env:PATH = "$machinePath;$userPath"
}

function Has-Winget {
    try {
        $null = Get-Command "winget" -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Ensure-TempDir {
    if (-not (Test-Path $TempDir)) {
        New-Item -ItemType Directory -Path $TempDir -Force | Out-Null
    }
}

# =====================================================================
# 自动安装 Git
# =====================================================================
function Install-GitAuto {
    Log "正在自动安装 Git..."

    if (Has-Winget) {
        Log "使用 winget 安装 Git..."
        winget install Git.Git --accept-package-agreements --accept-source-agreements --silent
        if ($LASTEXITCODE -eq 0) {
            Refresh-Path
            Ok "Git 安装完成"
            return $true
        }
    }

    Log "正在下载 Git 安装包..."
    Ensure-TempDir
    $gitInstaller = "$TempDir\git-installer.exe"
    try {
        $releaseInfo = Invoke-RestMethod "https://api.github.com/repos/git-for-windows/git/releases/latest" -TimeoutSec 30
        $asset = $releaseInfo.assets | Where-Object { $_.name -match "64-bit\.exe$" -and $_.name -notmatch "portable" } | Select-Object -First 1
        if ($asset) {
            Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $gitInstaller -UseBasicParsing
        } else {
            throw "未找到安装包"
        }
    } catch {
        Invoke-WebRequest -Uri "https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.2/Git-2.47.1.2-64-bit.exe" -OutFile $gitInstaller -UseBasicParsing
    }

    if (Test-Path $gitInstaller) {
        Log "正在安装 Git (静默安装)..."
        Start-Process -FilePath $gitInstaller -ArgumentList "/VERYSILENT", "/NORESTART", "/NOCANCEL", "/SP-", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS" -Wait
        Refresh-Path
        if (Get-Command "git" -ErrorAction SilentlyContinue) {
            Ok "Git 安装完成"
            return $true
        }
        $defaultGitPath = "C:\Program Files\Git\cmd"
        if (Test-Path $defaultGitPath) {
            $env:PATH += ";$defaultGitPath"
            Ok "Git 安装完成"
            return $true
        }
    }

    return $false
}

# =====================================================================
# 自动安装 Python
# =====================================================================
function Install-PythonAuto {
    Log "正在自动安装 Python 3.12..."

    if (Has-Winget) {
        Log "使用 winget 安装 Python..."
        winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent
        if ($LASTEXITCODE -eq 0) {
            Refresh-Path
            Ok "Python 安装完成"
            return $true
        }
    }

    Log "正在下载 Python 安装包..."
    Ensure-TempDir
    $pyInstaller = "$TempDir\python-installer.exe"
    $pyUrl = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
    try {
        Invoke-WebRequest -Uri $pyUrl -OutFile $pyInstaller -UseBasicParsing
    } catch {
        Err "Python 下载失败，请检查网络后重试"
    }

    if (Test-Path $pyInstaller) {
        Log "正在安装 Python (静默安装，约需 1-2 分钟)..."
        Start-Process -FilePath $pyInstaller -ArgumentList "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_test=0", "Include_launcher=1" -Wait
        Refresh-Path

        foreach ($cmd in @("python", "python3", "py")) {
            try {
                $ver = & $cmd --version 2>&1
                if ($ver -match "3\.1[1-9]|3\.[2-9]\d") {
                    Ok "Python 安装完成: $ver"
                    return $true
                }
            } catch {}
        }

        $defaultPyPath = "C:\Program Files\Python312;C:\Program Files\Python312\Scripts"
        $env:PATH += ";$defaultPyPath"
        try {
            $ver = & python --version 2>&1
            Ok "Python 安装完成: $ver"
            return $true
        } catch {}
    }

    return $false
}

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

# ── 检查 / 安装 Git ──
if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {
    Warn "未检测到 Git，即将自动安装..."
    $gitOk = Install-GitAuto
    if (-not $gitOk) {
        Err "Git 自动安装失败，请手动安装: https://git-scm.com/download/win"
    }
} else {
    Ok "Git 已就绪"
}

# ── 检查 / 安装 Python ──
$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "(\d+)\.(\d+)") {
            if ([int]$Matches[1] -ge 3 -and [int]$Matches[2] -ge 11) {
                $pythonCmd = $cmd
                break
            }
        }
    } catch {}
}

if (-not $pythonCmd) {
    Warn "未找到 Python 3.11+，即将自动安装..."
    $pyOk = Install-PythonAuto
    if (-not $pyOk) {
        Err "Python 自动安装失败，请手动安装: https://www.python.org/downloads/"
    }
    foreach ($cmd in @("python", "python3", "py")) {
        try {
            $ver = & $cmd --version 2>&1
            if ($ver -match "(\d+)\.(\d+)") {
                if ([int]$Matches[1] -ge 3 -and [int]$Matches[2] -ge 11) {
                    $pythonCmd = $cmd
                    break
                }
            }
        } catch {}
    }
    if (-not $pythonCmd) {
        Err "Python 安装后无法识别，请关闭此窗口重新打开 PowerShell 再运行一次"
    }
} else {
    Ok "Python 已就绪: $(& $pythonCmd --version)"
}

# ── 克隆项目 ──
Write-Host ""
Log "正在下载灵雀..."

if (Test-Path $InstallDir) {
    Warn "目录已存在: $InstallDir"
    $ow = Read-Host "是否覆盖? [y/N]"
    if ($ow -ne "y" -and $ow -ne "Y") {
        Log "安装取消"
        Read-Host "按回车退出"
        exit 0
    }
    Remove-Item $InstallDir -Recurse -Force
}

git clone --depth 1 $RepoUrl $InstallDir
if ($LASTEXITCODE -ne 0) { Err "下载失败，请检查网络连接后重试" }
Ok "下载完成"

Set-Location $InstallDir

# ── 虚拟环境 ──
Write-Host ""
Log "正在创建 Python 虚拟环境..."
& $pythonCmd -m venv venv
if ($LASTEXITCODE -ne 0) { Err "虚拟环境创建失败" }
& ".\venv\Scripts\Activate.ps1"
Ok "虚拟环境已创建"

# ── 安装依赖 ──
Log "正在安装依赖 (首次约需 2-5 分钟，请耐心等待)..."
pip install --upgrade pip -q 2>$null
pip install -r requirements.txt -q
if ($LASTEXITCODE -ne 0) { Err "依赖安装失败，请检查网络" }
Ok "依赖安装完成"

# ── 浏览器引擎 ──
Log "正在安装浏览器引擎..."
try {
    playwright install chromium 2>$null
    Ok "浏览器引擎已安装"
} catch {
    Warn "浏览器引擎安装跳过 (可稍后运行: playwright install chromium)"
}

# ── 配置 .env ──
Setup-Env

# ── 创建启动脚本 ──
$batContent = @"
@echo off
chcp 65001 >nul
title 🐦 灵雀 LingQue AI Agent
cd /d "$InstallDir"
call venv\Scripts\activate.bat
python -m lobster.main
pause
"@
Set-Content "启动灵雀.bat" $batContent

# ── 创建桌面快捷方式 ──
try {
    $desktopPath = [System.Environment]::GetFolderPath("Desktop")
    $shortcutPath = Join-Path $desktopPath "启动灵雀.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = "$InstallDir\启动灵雀.bat"
    $shortcut.WorkingDirectory = $InstallDir
    $shortcut.Description = "灵雀 LingQue AI Agent"
    $shortcut.Save()
    Ok "已创建桌面快捷方式: 启动灵雀"
} catch {
    Ok "已创建 启动灵雀.bat (位于 $InstallDir)"
}

# ── 清理临时文件 ──
if (Test-Path $TempDir) {
    Remove-Item $TempDir -Recurse -Force -ErrorAction SilentlyContinue
}

# ── 完成 ──
Write-Host ""
Write-Host "  ============================================" -ForegroundColor Green
Write-Host "  🎉 灵雀安装完成!" -ForegroundColor Green
Write-Host "  ============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  安装目录: $InstallDir" -ForegroundColor White
Write-Host ""
Write-Host "  启动方式: 双击桌面上的 [启动灵雀] 快捷方式" -ForegroundColor Yellow
Write-Host ""
Write-Host "  其他操作:" -ForegroundColor White
Write-Host "    修改配置: notepad $InstallDir\.env" -ForegroundColor Cyan
Write-Host "    升级版本: 在灵雀目录运行 .\scripts\upgrade.ps1" -ForegroundColor Cyan
Write-Host ""

Read-Host "按回车关闭此窗口"
