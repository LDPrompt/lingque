# Windows Docker 部署灵雀教程

> 适用系统：Windows 10 / 11（家庭版 / 专业版 / 企业版）
> 预计耗时：30-40 分钟

---

## 一、前置条件

| 项目 | 要求 |
|------|------|
| 系统 | Windows 10 64 位（Build 19041+）或 Windows 11 |
| 内存 | 至少 4GB（推荐 8GB+） |
| 硬盘 | 至少 10GB 可用空间 |
| 网络 | 能访问 Docker Hub 和 GitHub |

---

## 二、安装 Docker Desktop

### 2.1 下载安装

1. 打开 https://www.docker.com/products/docker-desktop/
2. 点击 **Download for Windows** 下载安装包
3. 双击 `Docker Desktop Installer.exe` 安装
4. 安装过程中确保勾选 **Use WSL 2 instead of Hyper-V**（推荐）

### 2.2 启用 WSL 2（如果没有自动启用）

以**管理员身份**打开 PowerShell，执行：

```powershell
# 启用 WSL
wsl --install

# 如果已有 WSL，升级到 WSL 2
wsl --set-default-version 2
```

执行完后**重启电脑**。

### 2.3 验证安装

重启后打开 PowerShell：

```powershell
docker --version
# 预期输出: Docker version 27.x.x, build xxxxx

docker compose version
# 预期输出: Docker Compose version v2.x.x
```

如果都能正常输出，说明安装成功。

> **常见问题**：如果提示 "Docker Desktop requires Windows 10 Pro"，说明你是家庭版，需要先执行上面的 WSL 2 安装步骤。

---

## 三、获取灵雀代码

### 方式 A：Git 克隆（推荐）

```powershell
# 安装 Git（如果没有）
winget install Git.Git

# 克隆项目
cd C:\Users\你的用户名\Desktop
git clone https://github.com/LDPrompt/lingque.git
cd lingque
```

### 方式 B：手动下载

1. 打开 https://github.com/LDPrompt/lingque
2. 点击 **Code** → **Download ZIP**
3. 解压到桌面，重命名文件夹为 `lingque`
4. 在 PowerShell 中进入目录：

```powershell
cd C:\Users\你的用户名\Desktop\lingque
```

---

## 四、配置环境变量

### 4.1 创建 .env 文件

```powershell
copy .env.example .env
```

### 4.2 编辑 .env

用记事本或 VS Code 打开 `.env` 文件：

```powershell
notepad .env
```

#### 最小配置（仅 CLI 模式）

```ini
# ===== LLM 配置 =====
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
DEEPSEEK_API_KEY=你的API密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# ===== 通道 =====
CHANNELS=cli

# ===== 安全 =====
REQUIRE_CONFIRMATION=true
ALLOWED_PATHS=/data/workspaces
```

#### 飞书模式配置

如果要接飞书，额外添加：

```ini
CHANNELS=feishu

FEISHU_APP_ID=你的应用ID
FEISHU_APP_SECRET=你的应用密钥
FEISHU_VERIFICATION_TOKEN=你的验证Token
FEISHU_MODE=websocket
```

#### 使用智谱 GLM

```ini
LLM_PROVIDER=glm
LLM_PROVIDER_GLM=https://open.bigmodel.cn/api/paas/v4|你的API密钥|glm-4-plus
```

保存并关闭文件。

---

## 五、构建并启动

### 5.1 一键启动

在 `lingque` 目录下打开 PowerShell：

```powershell
docker compose up -d
```

首次运行会下载基础镜像并构建，大约需要 **5-10 分钟**（取决于网络速度）。

你会看到类似输出：

```
[+] Building 120.5s (10/10) FINISHED
[+] Running 1/1
 ✔ Container lingque  Started
```

### 5.2 查看运行状态

```powershell
# 查看容器状态
docker compose ps

# 预期输出:
# NAME      IMAGE            STATUS          PORTS
# lingque   lingque-lingque  Up 30 seconds   0.0.0.0:9000->9000/tcp
```

### 5.3 查看日志

```powershell
# 实时查看日志
docker compose logs -f

# 只看最近 50 行
docker compose logs --tail 50
```

看到以下内容说明启动成功：

```
🐦 灵雀 LingQue v0.4.0 - 灵动 Prompt 出品
✅ Agent 初始化完成
📚 学习引擎已就绪
🧠 知识图谱已初始化
👤 用户画像管理器已初始化
启动通道: ['feishu']  (或 ['cli'])
```

---

## 六、使用灵雀

### 6.1 CLI 模式（本地测试）

如果配置了 `CHANNELS=cli`，需要进入容器交互：

```powershell
docker compose exec -it lingque python -m lobster.main --channel cli
```

然后就可以在终端中直接对话了。

### 6.2 飞书模式

配置了飞书后，启动即自动连接飞书。在飞书中 @灵雀 或私聊即可对话。

### 6.3 浏览器自动化（需额外配置）

Docker 容器默认没有图形界面，浏览器自动化需要安装 Chromium：

```powershell
# 进入容器
docker compose exec lingque bash

# 安装 Playwright 浏览器
playwright install chromium
playwright install-deps
```

> 注意：容器内的浏览器只能以无头模式运行（`BROWSER_HEADLESS=true`）。如果需要看到浏览器界面，建议使用本地 Python 部署而非 Docker。

---

## 七、数据持久化

Docker 容器重启不会丢失数据，因为 `docker-compose.yml` 已配置了卷挂载：

| 容器路径 | 宿主机路径 | 说明 |
|----------|-----------|------|
| `/data/memory` | `./memory` | 长期记忆、用户画像、知识图谱 |
| `/data/workspaces` | `./workspaces` | 工作目录、插件、工作流 |

这些目录在你的 `lingque` 文件夹下，即使删除容器数据也不会丢失。

---

## 八、常用运维命令

```powershell
# ───── 启停 ─────
docker compose up -d          # 启动（后台运行）
docker compose down            # 停止并移除容器
docker compose restart         # 重启

# ───── 日志 ─────
docker compose logs -f         # 实时日志
docker compose logs --tail 100 # 最近 100 行

# ───── 更新 ─────
git pull                       # 拉取最新代码
docker compose build           # 重新构建镜像
docker compose up -d           # 启动新版本

# ───── 进入容器 ─────
docker compose exec lingque bash           # 进入容器终端
docker compose exec lingque python --version  # 查看 Python 版本

# ───── 清理 ─────
docker compose down --volumes  # 停止并删除数据卷（谨慎！会丢失记忆数据）
docker system prune -f         # 清理无用镜像和缓存
```

---

## 九、Docker 网络配置（高级）

### 9.1 让容器访问宿主机服务

如果需要让灵雀操作宿主机上的 Chrome 浏览器（CDP 模式）：

1. 宿主机启动 Chrome（开启远程调试）：

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

2. 修改 `.env`：

```ini
BROWSER_MODE=cdp
BROWSER_CDP_URL=ws://host.docker.internal:9222
```

`host.docker.internal` 是 Docker Desktop 提供的特殊域名，指向宿主机。

### 9.2 挂载宿主机目录

如果需要让灵雀读写宿主机上的文件，编辑 `docker-compose.yml`：

```yaml
volumes:
  - ./memory:/data/memory
  - ./workspaces:/data/workspaces
  # 挂载你的文档目录（只读）
  - C:\Users\你的用户名\Documents:/mnt/documents:ro
  # 挂载项目目录（读写）
  - C:\Users\你的用户名\Projects:/mnt/projects
```

同时更新 `.env` 中的 `ALLOWED_PATHS`：

```ini
ALLOWED_PATHS=/data/workspaces,/mnt/documents,/mnt/projects
```

---

## 十、常见问题

### Q: docker compose up 提示网络错误 / 下载超慢

国内网络访问 Docker Hub 可能很慢，可以配置镜像加速器：

1. 打开 Docker Desktop → 设置(Settings) → Docker Engine
2. 在 JSON 配置中添加：

```json
{
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://docker.xuanyuan.me"
  ]
}
```

3. 点击 **Apply & Restart**

### Q: 启动后日志显示 LLM 调用失败

检查 `.env` 中的 API Key 是否正确填写，以及网络是否能访问对应的 API 地址：

```powershell
# 测试 DeepSeek API 连通性
curl https://api.deepseek.com/v1/models -H "Authorization: Bearer 你的密钥"
```

### Q: 容器启动后立刻退出

```powershell
# 查看退出原因
docker compose logs

# 常见原因：
# 1. .env 文件不存在 → 执行 copy .env.example .env
# 2. .env 中缺少必需配置 → 至少配置 LLM_PROVIDER 和 API Key
# 3. Python 依赖安装失败 → 重新构建: docker compose build --no-cache
```

### Q: 如何完全重置

```powershell
# 停止并删除容器
docker compose down

# 删除构建缓存（重新构建）
docker compose build --no-cache

# 重新启动
docker compose up -d
```

如果要清除所有记忆数据（谨慎）：

```powershell
# 删除记忆目录
Remove-Item -Recurse -Force .\memory\*
```

### Q: Windows 防火墙阻止了端口

如果需要从其他设备访问灵雀的 9000 端口：

```powershell
# 以管理员身份运行
netsh advfirewall firewall add rule name="LingQue" dir=in action=allow protocol=TCP localport=9000
```

### Q: 怎么让灵雀开机自启

Docker Desktop 默认开机自启，容器配置了 `restart: unless-stopped`，所以：
- 电脑开机 → Docker Desktop 自动启动 → 灵雀容器自动启动

如果 Docker Desktop 没有设为开机自启：
1. Docker Desktop → 设置 → General → 勾选 **Start Docker Desktop when you sign in**

---

## 附录：不用 Docker 的本地部署（备选）

如果你更喜欢直接在 Windows 上运行（比如需要看到浏览器界面）：

```powershell
# 1. 安装 Python 3.12
winget install Python.Python.3.12

# 2. 克隆项目
git clone https://github.com/LDPrompt/lingque.git
cd lingque

# 3. 创建虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 4. 安装依赖
pip install -r requirements.txt

# 5. 安装浏览器引擎
playwright install chromium

# 6. 配置
copy .env.example .env
notepad .env   # 编辑配置

# 7. 启动
python -m lobster.main
```

本地部署的优势：
- 浏览器可视化操作（`BROWSER_HEADLESS=false`）
- 可以直接操作本地文件
- 调试更方便
