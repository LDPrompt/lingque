#
# 🐦 灵雀 LingQue 一键安装 — Docker 版 (Windows)
#
# 全自动：缺 Docker Desktop / Git 会自动安装，用户无需手动下载任何东西
#

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[System.Console]::InputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

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
$RepoMirrors = @(
    "https://ghproxy.net/https://github.com/LDPrompt/lingque.git",
    "https://mirror.ghproxy.com/https://github.com/LDPrompt/lingque.git",
    "https://gh-proxy.com/https://github.com/LDPrompt/lingque.git"
)
$ZipGitHub = "https://github.com/LDPrompt/lingque/archive/refs/heads/main.zip"
$InstallDir = if ($env:LINGQUE_INSTALL_DIR) { $env:LINGQUE_INSTALL_DIR } else { "$env:USERPROFILE\lingque" }
$TempDir = "$env:TEMP\lingque_setup"

Write-Host ""
Write-Host "   ========================================" -ForegroundColor Cyan
Write-Host "   🐦 灵雀 LingQue 一键安装 (Docker 版)" -ForegroundColor White
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

function Find-DockerDesktopExe {
    $candidates = @(
        "C:\Program Files\Docker\Docker\Docker Desktop.exe",
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Docker\Docker Desktop.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    $reg = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop" -ErrorAction SilentlyContinue
    if ($reg -and $reg.InstallLocation) {
        $exe = Join-Path $reg.InstallLocation "Docker Desktop.exe"
        if (Test-Path $exe) { return $exe }
    }
    $reg2 = Get-ItemProperty "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop" -ErrorAction SilentlyContinue
    if ($reg2 -and $reg2.InstallLocation) {
        $exe2 = Join-Path $reg2.InstallLocation "Docker Desktop.exe"
        if (Test-Path $exe2) { return $exe2 }
    }
    return $null
}

function Ensure-DockerInPath {
    $cliPaths = @(
        "C:\Program Files\Docker\Docker\resources\bin",
        "C:\Program Files\Docker\Docker"
    )
    foreach ($p in $cliPaths) {
        if ((Test-Path "$p\docker.exe") -and ($env:PATH -notlike "*$p*")) {
            $env:PATH += ";$p"
        }
    }
}

function Wait-ForDockerReady {
    param([int]$TimeoutSeconds = 120)
    $waited = 0
    while ($waited -lt $TimeoutSeconds) {
        Start-Sleep -Seconds 5
        $waited += 5
        Refresh-Path
        Ensure-DockerInPath
        try {
            docker info *>$null
            if ($LASTEXITCODE -eq 0) {
                return $true
            }
        } catch {}
        Write-Host "  等待 Docker 启动中... ($waited/$TimeoutSeconds 秒)" -ForegroundColor DarkGray
    }
    return $false
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
            throw "no asset"
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
# 自动安装 Docker Desktop
# =====================================================================
function Install-DockerAuto {
    Log "正在自动安装 Docker Desktop..."

    $installed = $false

    if (Has-Winget) {
        Log "使用 winget 安装 Docker Desktop (可能需要几分钟)..."
        $wingetOutput = winget install Docker.DockerDesktop --accept-package-agreements --accept-source-agreements --silent 2>&1
        Write-Host $wingetOutput -ForegroundColor DarkGray
        Refresh-Path
        Ensure-DockerInPath
        if (Find-DockerDesktopExe) {
            $installed = $true
            Ok "Docker Desktop (winget) 安装完成"
        } else {
            Warn "winget 安装后未检测到 Docker Desktop，尝试直接下载..."
        }
    }

    if (-not $installed) {
        Log "正在下载 Docker Desktop 安装包 (约 500MB)..."
        Log "下载过程会显示进度条，请耐心等待..."
        Ensure-TempDir
        $dockerInstaller = "$TempDir\DockerDesktopInstaller.exe"
        $dockerUrl = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"

        $downloadOk = $false
        try {
            Start-BitsTransfer -Source $dockerUrl -Destination $dockerInstaller -Description "Docker Desktop" -ErrorAction Stop
            $downloadOk = $true
        } catch {
            Warn "BITS 下载失败，尝试 WebClient 方式..."
            try {
                $wc = New-Object Net.WebClient
                $wc.DownloadFile($dockerUrl, $dockerInstaller)
                $downloadOk = $true
            } catch {
                Warn "WebClient 下载也失败"
            }
        }

        if ($downloadOk -and (Test-Path $dockerInstaller)) {
            $fileSize = (Get-Item $dockerInstaller).Length / 1MB
            if ($fileSize -lt 100) {
                Warn "下载文件异常 (${fileSize}MB)，可能下载不完整"
            } else {
                Log "正在安装 Docker Desktop (静默安装，约需 2-5 分钟)..."
                Start-Process -FilePath $dockerInstaller -ArgumentList "install", "--quiet", "--accept-license" -Wait
                Refresh-Path
                Ensure-DockerInPath
                if (Find-DockerDesktopExe) {
                    $installed = $true
                    Ok "Docker Desktop 安装完成"
                }
            }
        }
    }

    if (-not $installed) {
        return $false
    }

    $ddExe = Find-DockerDesktopExe
    if ($ddExe) {
        Log "正在启动 Docker Desktop..."
        Start-Process $ddExe
        Log "等待 Docker 引擎就绪 (首次启动可能需要 1-2 分钟)..."
        if (Wait-ForDockerReady -TimeoutSeconds 120) {
            Ok "Docker Desktop 已启动并就绪"
            return "ready"
        } else {
            Warn "Docker Desktop 已安装但引擎尚未就绪"
            Warn "如果是第一次安装，Docker Desktop 可能需要初始化 WSL/Hyper-V"
            return "installed-not-ready"
        }
    }

    return "installed-not-found"
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

# ── 检查 / 安装 Docker Desktop ──
$dockerReady = $false
Refresh-Path
Ensure-DockerInPath

$ddExe = Find-DockerDesktopExe
$dockerCmd = Get-Command "docker" -ErrorAction SilentlyContinue

if ($dockerCmd -or $ddExe) {
    if ($dockerCmd) {
        try {
            docker info *>$null
            if ($LASTEXITCODE -eq 0) {
                Ok "Docker Desktop 已就绪: $(docker --version)"
                $dockerReady = $true
            }
        } catch {}
    }

    if (-not $dockerReady -and $ddExe) {
        Warn "Docker Desktop 已安装但未运行，正在启动..."
        Start-Process $ddExe
        Log "等待 Docker Desktop 就绪 (最多 120 秒)..."
        if (Wait-ForDockerReady -TimeoutSeconds 120) {
            Ok "Docker Desktop 已就绪: $(docker --version)"
            $dockerReady = $true
        } else {
            Write-Host ""
            Write-Host "  Docker Desktop 启动超时，请尝试:" -ForegroundColor Yellow
            Write-Host "  1. 手动双击桌面上的 Docker Desktop 图标" -ForegroundColor Cyan
            Write-Host "  2. 等待任务栏鲸鱼图标稳定 (不再转圈)" -ForegroundColor Cyan
            Write-Host "  3. 重新运行安装命令" -ForegroundColor Cyan
            Write-Host ""
            Read-Host "按回车退出"
            exit 1
        }
    }

    if (-not $dockerReady -and -not $ddExe) {
        Err "检测到 docker 命令但找不到 Docker Desktop，请手动启动 Docker Desktop 后重试"
    }
} else {
    Warn "未检测到 Docker Desktop，即将自动安装..."
    $dockerResult = Install-DockerAuto

    if ($dockerResult -eq $false) {
        Write-Host ""
        Write-Host "  Docker Desktop 自动安装失败，请手动安装:" -ForegroundColor Red
        Write-Host ""
        Write-Host "  1. 浏览器打开: https://www.docker.com/products/docker-desktop/" -ForegroundColor Cyan
        Write-Host "  2. 点击 Download for Windows 下载" -ForegroundColor Cyan
        Write-Host "  3. 双击安装，一路默认下一步" -ForegroundColor Cyan
        Write-Host "  4. 安装完启动 Docker Desktop" -ForegroundColor Cyan
        Write-Host "  5. 等鲸鱼图标稳定后，重新运行安装命令" -ForegroundColor Cyan
        Write-Host ""
        Read-Host "按回车退出"
        exit 1
    }

    if ($dockerResult -eq "ready") {
        $dockerReady = $true
    } elseif ($dockerResult -eq "installed-not-ready") {
        Write-Host ""
        Write-Host "  Docker Desktop 已安装但引擎尚未就绪" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  可能原因: 首次安装需要初始化 WSL2 / Hyper-V" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  请操作:" -ForegroundColor White
        Write-Host "  1. 如果弹出窗口要求启用 WSL2 或 Hyper-V，请同意" -ForegroundColor Cyan
        Write-Host "  2. 如果提示需要重启，请重启电脑" -ForegroundColor Cyan
        Write-Host "  3. 重启后，先手动打开 Docker Desktop" -ForegroundColor Cyan
        Write-Host "  4. 等鲸鱼图标稳定后，重新运行安装命令" -ForegroundColor Cyan
        Write-Host ""
        Read-Host "按回车退出"
        exit 0
    } else {
        Write-Host ""
        Write-Host "  Docker Desktop 安装异常，请手动启动后重试" -ForegroundColor Yellow
        Write-Host ""
        Read-Host "按回车退出"
        exit 1
    }
}

# ── 检查 docker compose ──
try {
    docker compose version *>$null
    if ($LASTEXITCODE -ne 0) { throw "no compose" }
    Ok "docker compose 已就绪"
} catch {
    Err "docker compose 不可用，请确认 Docker Desktop 版本 >= 4.0"
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

$cloneOk = $false

git clone --depth 1 $RepoUrl $InstallDir 2>$null
if ($LASTEXITCODE -eq 0) {
    $cloneOk = $true
} else {
    Warn "GitHub 直连失败，尝试镜像加速..."
    if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue }
    foreach ($mirror in $RepoMirrors) {
        Log "尝试: $mirror"
        git clone --depth 1 $mirror $InstallDir 2>$null
        if ($LASTEXITCODE -eq 0) {
            $cloneOk = $true
            break
        }
        if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

if (-not $cloneOk) {
    Warn "Git 克隆均失败，尝试下载 ZIP 包..."
    if (-not (Test-Path $TempDir)) { New-Item -ItemType Directory -Path $TempDir -Force | Out-Null }
    $zipFile = "$TempDir\lingque.zip"
    try {
        Invoke-WebRequest -Uri $ZipGitHub -OutFile $zipFile -UseBasicParsing -TimeoutSec 60
        if (Test-Path $zipFile) {
            Log "正在解压..."
            Expand-Archive -Path $zipFile -DestinationPath $TempDir -Force
            $extracted = Get-ChildItem "$TempDir\lingque-*" -Directory | Select-Object -First 1
            if ($extracted) {
                if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
                Move-Item $extracted.FullName $InstallDir
                Ok "ZIP 下载解压完成"
                $cloneOk = $true
            }
        }
    } catch {
        Warn "ZIP 下载也失败"
    }
}

if (-not $cloneOk) {
    Write-Host ""
    Write-Host "  所有下载方式均失败，请尝试:" -ForegroundColor Red
    Write-Host "  1. 开启 VPN/代理后重新运行本脚本" -ForegroundColor Cyan
    Write-Host "  2. 手动下载: 浏览器打开 https://github.com/LDPrompt/lingque -> Code -> Download ZIP" -ForegroundColor Cyan
    Write-Host "     解压到 $InstallDir 后重新运行此脚本" -ForegroundColor Cyan
    Write-Host ""
    Read-Host "按回车退出"
    exit 1
}
Ok "下载完成"

Set-Location $InstallDir

# ── 配置 .env ──
Setup-Env

# ── 修复 Docker 镜像源（国内常见问题）──
$mirrorPattern = "mirrors\.ustc\.edu\.cn|mirrors\.aliyun\.com|registry\.docker-cn\.com|mirror\.ccs\.tencentyun\.com|docker\.mirrors\.|mirror\.baidubce\.com|hub-mirror\.c\.163\.com|dockerhub\.azk8s\.cn"
$mirrorFixed = $false

$dockerConfigPath = "$env:USERPROFILE\.docker\daemon.json"
if (Test-Path $dockerConfigPath) {
    try {
        $daemonJson = Get-Content $dockerConfigPath -Raw -ErrorAction SilentlyContinue
        if ($daemonJson -match $mirrorPattern) {
            Warn "检测到失效的 Docker 镜像源 (daemon.json)，正在修复..."
            $daemonObj = $daemonJson | ConvertFrom-Json
            if ($daemonObj."registry-mirrors") {
                $daemonObj."registry-mirrors" = @()
                $daemonObj | ConvertTo-Json -Depth 10 | Set-Content $dockerConfigPath -Encoding UTF8
                $mirrorFixed = $true
                Ok "daemon.json 镜像源已清除"
            }
        }
    } catch {
        Warn "daemon.json 检测跳过: $_"
    }
}

$ddSettingsPath = "$env:APPDATA\Docker\settings.json"
if (Test-Path $ddSettingsPath) {
    try {
        $ddJson = Get-Content $ddSettingsPath -Raw -ErrorAction SilentlyContinue
        if ($ddJson -match $mirrorPattern) {
            Warn "检测到失效的 Docker 镜像源 (Docker Desktop settings)，正在修复..."
            $ddObj = $ddJson | ConvertFrom-Json
            $needSave = $false
            if ($ddObj.PSObject.Properties["overriddenDockerEngineConfig"]) {
                $engineCfg = $ddObj.overriddenDockerEngineConfig
                if ($engineCfg -and $engineCfg.PSObject.Properties["registry-mirrors"]) {
                    $engineCfg."registry-mirrors" = @()
                    $needSave = $true
                }
            }
            if ($ddObj.PSObject.Properties["DockerDesktopDaemonConfig"]) {
                $daemonCfg = $ddObj.DockerDesktopDaemonConfig
                if ($daemonCfg -and "$daemonCfg" -match $mirrorPattern) {
                    $cleanCfg = $daemonCfg -replace '"registry-mirrors"\s*:\s*\[[^\]]*\]', '"registry-mirrors": []'
                    $ddObj.DockerDesktopDaemonConfig = $cleanCfg
                    $needSave = $true
                }
            }
            if ($needSave) {
                $ddObj | ConvertTo-Json -Depth 10 | Set-Content $ddSettingsPath -Encoding UTF8
                $mirrorFixed = $true
                Ok "Docker Desktop settings 镜像源已清除"
            }
        }
    } catch {
        Warn "Docker Desktop settings 检测跳过: $_"
    }
}

if ($mirrorFixed) {
    Log "镜像源已修复，正在重启 Docker Desktop..."
    $ddExeRestart = Find-DockerDesktopExe
    if ($ddExeRestart) {
        Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
        Start-Process $ddExeRestart
        Log "等待 Docker Desktop 重启..."
        if (-not (Wait-ForDockerReady -TimeoutSeconds 60)) {
            Warn "Docker Desktop 重启后未就绪，请手动检查"
        }
    } else {
        Start-Sleep -Seconds 5
    }
    Ok "Docker 镜像源修复完成"
}

# ── 构建并启动 ──
Write-Host ""
Log "正在构建并启动 Docker 容器 (首次约需 3-8 分钟，请耐心等待)..."
docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Warn "Docker 构建失败，正在诊断原因..."
    $pullTest = docker pull hello-world 2>&1
    if ($LASTEXITCODE -ne 0) {
        $pullOutput = "$pullTest"
        if ($pullOutput -match "no such host|timeout|connection refused|TLS handshake") {
            Write-Host ""
            Write-Host "  问题原因: Docker 无法访问镜像仓库 (网络/镜像源问题)" -ForegroundColor Red
            Write-Host ""
            Write-Host "  解决方法:" -ForegroundColor Yellow
            Write-Host "  1. 打开 Docker Desktop -> 设置(Settings) -> Docker Engine" -ForegroundColor Cyan
            Write-Host '  2. 找到 registry-mirrors 那一行，清空为: "registry-mirrors": []' -ForegroundColor Cyan
            Write-Host "  3. 点击 Apply & Restart" -ForegroundColor Cyan
            Write-Host "  4. 等 Docker 重启完毕后，重新运行本安装脚本" -ForegroundColor Cyan
            Write-Host ""
            Read-Host "按回车退出"
            exit 1
        } else {
            Err "Docker 启动失败，请确认 Docker Desktop 正在运行"
        }
    } else {
        Err "Docker 容器构建失败，请查看上方错误信息后重试"
    }
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
Write-Host "  常用命令 (在 $InstallDir 目录下执行):" -ForegroundColor White
Write-Host "    查看日志: docker compose logs -f" -ForegroundColor Cyan
Write-Host "    停止服务: docker compose down" -ForegroundColor Cyan
Write-Host "    重启服务: docker compose restart" -ForegroundColor Cyan
Write-Host "    升级版本: .\scripts\upgrade.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "  修改配置: notepad $InstallDir\.env" -ForegroundColor Cyan
Write-Host ""

Read-Host "按回车关闭此窗口"
