# 🐦 灵雀 LingQue 本地部署教程（Windows + Mac + 飞书对接）

> 本教程适合零基础小白，从零开始在本地电脑部署灵雀 AI Agent，并连接飞书进行对话。
> 全程预计耗时 30-60 分钟。

---

## 📋 目录

- [一、环境准备](#一环境准备)
  - [Windows 环境准备](#windows-环境准备)
  - [Mac 环境准备](#mac-环境准备)
- [二、下载灵雀项目](#二下载灵雀项目)
- [三、安装依赖](#三安装依赖)
- [四、创建飞书机器人应用](#四创建飞书机器人应用)
  - [4.1 创建应用](#41-创建应用)
  - [4.2 获取凭证](#42-获取凭证)
  - [4.3 添加机器人能力](#43-添加机器人能力)
  - [4.4 配置事件订阅](#44-配置事件订阅)
  - [4.5 配置权限](#45-配置权限)
  - [4.6 发布应用](#46-发布应用)
  - [4.7 将机器人添加到群聊](#47-将机器人添加到群聊)
- [五、配置灵雀](#五配置灵雀)
- [六、启动灵雀](#六启动灵雀)
- [七、验证部署](#七验证部署)
- [八、进阶配置](#八进阶配置)
- [九、常见问题排查](#九常见问题排查)
- [附录：完整 .env 配置参考](#附录完整-env-配置参考)

---

## 一、环境准备

### Windows 环境准备

#### 1. 安装 Python 3.12

1. 打开浏览器，访问 **https://www.python.org/downloads/**
2. 点击下载 **Python 3.12.x**（选最新的 3.12 版本）
3. 双击安装包
4. **⚠️ 关键步骤：勾选底部的 `Add python.exe to PATH`**（不勾选后面全部会出错）
5. 点击 `Install Now`，等待安装完成

验证安装：

```powershell
# 按 Win+X，点击"终端"或"PowerShell"，输入：
python --version
# 应显示: Python 3.12.x

pip --version
# 应显示: pip 24.x.x from ...
```

> **如果提示 `python 不是内部命令`**：说明没勾选 PATH，卸载重装并勾选。

#### 2. 安装 Git

1. 访问 **https://git-scm.com/download/win**
2. 下载安装，**一路点 Next 用默认设置**即可
3. 安装完成后重新打开 PowerShell

验证安装：

```powershell
git --version
# 应显示: git version 2.x.x
```

#### 3. 安装 Google Chrome（浏览器自动化用，可选）

如果你希望灵雀能自动操作浏览器（自动填表、截图、爬取网页等），需要安装 Chrome：

- 下载地址：**https://www.google.com/chrome/**
- 已经安装过的可以跳过

---

### Mac 环境准备

#### 1. 安装 Homebrew（Mac 包管理器）

打开**终端 (Terminal)**（在启动台搜索"终端"或按 `Cmd+空格` 搜 `Terminal`）：

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

安装完成后，按照终端提示将 brew 添加到 PATH（M 芯片 Mac 需要执行提示的两行命令）。

#### 2. 安装 Python 3.12

```bash
brew install python@3.12
```

验证：

```bash
python3.12 --version
# 应显示: Python 3.12.x
```

#### 3. 安装 Git

```bash
# Mac 一般自带 git，验证一下：
git --version

# 如果提示安装命令行工具，点"安装"即可
# 或者手动安装：
brew install git
```

#### 4. 安装 Google Chrome（可选）

```bash
brew install --cask google-chrome
```

或者从 **https://www.google.com/chrome/** 下载安装。

---

## 二、下载灵雀项目

### Windows

打开 PowerShell：

```powershell
# 进入你想放项目的目录（比如桌面）
cd ~/Desktop

# 下载项目代码
git clone https://github.com/LDPrompt/lingque.git

# 进入项目目录
cd lingque
```

### Mac

打开终端：

```bash
cd ~/Desktop
git clone https://github.com/LDPrompt/lingque.git
cd lingque
```

> **如果 git clone 很慢**：可以配置 Git 代理，或者直接在 GitHub 页面下载 ZIP 解压。

---

## 三、安装依赖

### Windows

```powershell
# 1. 创建虚拟环境（隔离项目依赖，不影响系统）
python -m venv .venv

# 2. 激活虚拟环境
.venv\Scripts\activate
# 成功后命令行前面会出现 (.venv) 字样

# 3. 升级 pip（避免安装报错）
pip install --upgrade pip

# 4. 安装项目依赖
pip install -r requirements.txt

# 如果下载慢，使用国内镜像源：
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 5. 安装浏览器引擎（浏览器自动化功能需要，可选）
playwright install chromium
```

> **⚠️ 如果 PowerShell 报错 `Execution Policy`**，先执行：
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```
> 然后重新激活虚拟环境。

> **⚠️ 如果 `chromadb` 或 `sentence-transformers` 安装失败**：没关系！这两个是向量记忆（可选功能），后面在配置里关掉就行，完全不影响使用。

### Mac

```bash
# 1. 创建虚拟环境
python3.12 -m venv .venv

# 2. 激活虚拟环境
source .venv/bin/activate

# 3. 升级 pip
pip install --upgrade pip

# 4. 安装项目依赖
pip install -r requirements.txt

# 国内镜像源（如果下载慢）：
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 5. 安装浏览器引擎（可选）
playwright install chromium
```

> **⚠️ Mac 如果报编译错误**，先安装 Xcode 命令行工具：
> ```bash
> xcode-select --install
> ```

### 创建工作目录

Windows：

```powershell
mkdir workspaces, memory, logs, downloads -Force
```

Mac：

```bash
mkdir -p workspaces memory logs downloads
```

---

## 四、创建飞书机器人应用

这是最关键的一步，跟着做就行。

### 4.1 创建应用

1. 打开浏览器，访问 **[飞书开放平台](https://open.feishu.cn/)**
2. 用你的飞书账号登录
3. 点击右上角 **「创建企业自建应用」**
4. 填写应用信息：
   - **应用名称**：`灵雀助手`（或你喜欢的名字）
   - **应用描述**：`私人 AI Agent 助手`
   - **应用图标**：随便选一个
5. 点击 **「创建」**

### 4.2 获取凭证

创建完成后，进入应用管理页面：

1. 在左侧菜单点击 **「凭证与基础信息」**
2. 你会看到两个关键信息，**记下来**：
   - **App ID**：形如 `cli_xxxxxxxxx`
   - **App Secret**：点击显示后复制（形如一串字母数字）
3. 继续往下找到 **Verification Token**：
   - 形如一串字母数字，也**记下来**
4. 如果看到 **Encrypt Key**：也记下来（如果页面上没有，可以留空）

> 📝 **一共需要记录 3-4 个值**：
> - `App ID` → 填到 `.env` 的 `FEISHU_APP_ID`
> - `App Secret` → 填到 `.env` 的 `FEISHU_APP_SECRET`
> - `Verification Token` → 填到 `.env` 的 `FEISHU_VERIFICATION_TOKEN`
> - `Encrypt Key`（可选）→ 填到 `.env` 的 `FEISHU_ENCRYPT_KEY`

### 4.3 添加机器人能力

1. 在左侧菜单点击 **「添加应用能力」**
2. 找到 **「机器人」**，点击 **「添加」**
3. 添加成功后，左侧菜单会出现 **「机器人」** 选项

### 4.4 配置事件订阅（⭐ 最重要）

这一步决定了灵雀能否收到飞书消息。

1. 在左侧菜单点击 **「事件与回调」**
2. **选择接收方式**：
   - 点击 **「使用长连接接收事件」**（WebSocket 模式）
   - ⭐ **强烈推荐长连接模式**，因为本地电脑没有公网 IP，用不了 Webhook 模式
3. 在下方 **「添加事件」**，搜索并添加以下事件：

| 事件名称 | 事件标识 | 用途 |
|----------|---------|------|
| **接收消息** | `im.message.receive_v1` | 接收用户发来的消息（**必须**） |
| **消息表情回复** | `im.message.reaction.created_v1` | 接收表情回复（可选） |
| **卡片回传交互** | `card.action.trigger` | 处理确认按钮点击（**推荐**） |

> ⚠️ `im.message.receive_v1`（接收消息）是**必须添加**的，否则灵雀收不到任何消息。

### 4.5 配置权限

在左侧菜单点击 **「权限管理」**，搜索并开通以下权限：

#### 必须的权限

| 权限名称 | 权限标识 | 用途 |
|----------|---------|------|
| 获取与发送单聊、群组消息 | `im:message` | 收发消息 |
| 获取用户发给机器人的单聊消息 | `im:message.receive_v1` | 接收私聊消息 |
| 以应用身份发送消息 | `im:message:send_as_bot` | 发送消息 |
| 获取群组信息 | `im:chat:readonly` | 获取群聊信息 |
| 获取群成员信息 | `im:chat.member:readonly` | 获取群成员 |

#### 推荐的权限（按需开通）

| 权限名称 | 权限标识 | 用途 |
|----------|---------|------|
| 上传图片 | `im:resource` | 发送截图 |
| 上传文件 | `im:file` | 发送文件 |
| 查看、创建、编辑和管理云文档 | `docs:doc` | 创建飞书文档 |
| 查看、创建、编辑和管理电子表格 | `sheets:spreadsheet` | 操作表格 |
| 管理日历 | `calendar:calendar` | 日历功能 |
| 读取日历忙闲信息 | `calendar:calendar.freebusy:readonly` | 查询忙闲 |

> 💡 **不确定就先只开必须的 5 个**，后面需要什么再来加。

### 4.6 发布应用

1. 在左侧菜单点击 **「版本管理与发布」**
2. 点击 **「创建版本」**
3. 填写版本号（如 `1.0.0`）和更新说明（如"首次发布"）
4. 点击 **「保存」**，然后 **「申请发布」**
5. 如果你是管理员，直接在 **「审核」** 页面通过即可
6. 如果你不是管理员，需要让管理员在飞书管理后台审核通过

> ⚠️ **应用必须发布后才能使用**，未发布的应用无法接收消息。

### 4.7 将机器人添加到群聊

1. 打开飞书客户端
2. 进入你想用的**群聊**（或者新建一个测试群）
3. 点击群聊右上角的 **「...」→ 「设置」→ 「群机器人」**
4. 点击 **「添加机器人」**
5. 搜索你创建的应用名称（如"灵雀助手"）
6. 添加成功后，群里会出现一条提示："xxx 添加了机器人 灵雀助手"

> 💡 你也可以直接私聊机器人（在飞书搜索栏搜"灵雀助手"即可找到）。

### 4.8 获取机器人的 Open ID（推荐）

获取机器人自己的 `open_id` 有助于群聊中正确判断 @ 消息：

1. 发布应用后，在飞书群里 @ 机器人发一条消息
2. 查看灵雀的日志输出，搜索 `open_id`，格式形如 `ou_xxxxxxxxxxxxxxxxxx`
3. 也可以在飞书开放平台 → **「事件与回调」→「调试」** 页面查看事件日志中的发送者信息

---

## 五、配置灵雀

### 1. 创建配置文件

Windows：

```powershell
copy .env.example .env
```

Mac：

```bash
cp .env.example .env
```

### 2. 编辑 .env 文件

用你喜欢的编辑器打开 `.env`：

- **Windows**：用记事本（右键 `.env` → 打开方式 → 记事本）或 VS Code
- **Mac**：`open -e .env` 或 `nano .env` 或 `code .env`

### 3. 填写配置

下面是**本地部署 + 飞书**的推荐配置，把注释后面的值替换成你自己的：

```ini
# ============================================
# 灵雀本地部署 + 飞书对接配置
# ============================================

# ---------- LLM 配置（必填，至少配一个） ----------
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
DEEPSEEK_API_KEY=你的DeepSeek密钥

# DeepSeek API Key 获取：
#   1. 访问 https://platform.deepseek.com/
#   2. 注册/登录 → API Keys → 创建新密钥
#   3. 复制密钥粘贴到上面

# ---------- 通道配置 ----------
# cli = 命令行（本地调试）
# feishu = 飞书机器人
# 两个都要就用逗号隔开
CHANNELS=cli,feishu

# ---------- 飞书配置（必填） ----------
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_VERIFICATION_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_ENCRYPT_KEY=
# 连接模式：本地电脑必须用 websocket（长连接，不需要公网IP）
FEISHU_MODE=websocket
# 安全白名单（限制谁能用，空 = 所有人都能用）
FEISHU_ALLOWED_USERS=
# 机器人自己的 open_id（群聊 @ 判断用，启动后从日志里找）
FEISHU_BOT_OPEN_ID=

# ---------- 安全配置 ----------
REQUIRE_CONFIRMATION=true
ALLOWED_PATHS=./workspaces

# ---------- 存储目录 ----------
MEMORY_DIR=./memory
WORKSPACE_DIR=./workspaces

# ---------- 性能优化（本地电脑推荐） ----------
# 关闭向量记忆库（省约 500MB 内存）
VECTOR_MEMORY_ENABLED=false
# 学习引擎用轻量 SQLite（省内存，效果一样）
LEARNING_BACKEND=sqlite

# ---------- 浏览器自动化（可选） ----------
# auto = 自动检测系统 Chrome，找不到就用内置 Chromium
BROWSER_MODE=auto
# false = 能看到浏览器界面（推荐本地调试）
# true = 无头模式（看不到界面但省资源）
BROWSER_HEADLESS=false

# ---------- Agent 配置 ----------
AGENT_TASK_TIMEOUT=600
AGENT_LLM_TIMEOUT=120
AGENT_MAX_LOOPS=25
LOG_LEVEL=INFO
```

### 4. 配置要点总结

| 配置项 | 你需要填什么 | 从哪里获取 |
|--------|-------------|-----------|
| `DEEPSEEK_API_KEY` | API 密钥 | https://platform.deepseek.com/ |
| `FEISHU_APP_ID` | 应用 ID | 飞书开放平台 → 凭证与基础信息 |
| `FEISHU_APP_SECRET` | 应用密钥 | 飞书开放平台 → 凭证与基础信息 |
| `FEISHU_VERIFICATION_TOKEN` | 验证令牌 | 飞书开放平台 → 事件与回调 |
| `FEISHU_MODE` | 必须填 `websocket` | 本地电脑没有公网 IP，只能用长连接 |

> ⚠️ **`FEISHU_MODE=websocket` 是本地部署的关键**，千万别填成 `webhook`，否则飞书消息发不过来。

---

## 六、启动灵雀

### Windows

```powershell
# 确保虚拟环境已激活（前面有 (.venv)）
.venv\Scripts\activate

# 启动
python -m lobster.main
```

### Mac

```bash
# 确保虚拟环境已激活
source .venv/bin/activate

# 启动
python -m lobster.main
```

### 启动成功的标志

你应该看到类似这样的输出：

```
   __    _             ____
  / /   (_)___  ____ _/ __ \__  _____
 / /   / / __ \/ __ `/ / / / / / / _ \
/ /___/ / / / / /_/ / /_/ / /_/ /  __/
\____/_/_/ /_/\__, /\___\_\__,_/\___/
             /____/
🐦 灵雀 LingQue v0.4.0 - 灵动 Prompt 出品

10:00:00 [lingque] INFO: 🧠 向量记忆库已关闭 (VECTOR_MEMORY_ENABLED=false)
10:00:00 [lingque.learning.backend] INFO: 学习引擎后端: SQLite FTS5
10:00:00 [lingque.learning] INFO: 学习引擎已初始化
10:00:00 [lingque] INFO: 📚 学习引擎已就绪
10:00:01 [lingque.feishu] INFO: 🔌 飞书长连接模式启动中...
10:00:02 [lingque.feishu] INFO: 🔌 飞书 WebSocket 长连接已启动 (自动重连)

你>
```

看到 **「飞书 WebSocket 长连接已启动」** 就说明飞书连接成功了！

> **如果看到报错**，请查看后面的 [常见问题排查](#九常见问题排查) 章节。

---

## 七、验证部署

### 1. 本地命令行测试

在 `你>` 提示符后直接输入：

```
你好，自我介绍一下
```

灵雀应该会回复。这说明 LLM 连接正常。

### 2. 飞书测试

1. 打开飞书
2. **私聊测试**：搜索"灵雀助手"（你的机器人名），直接发消息
3. **群聊测试**：在添加了机器人的群里，@ 机器人发消息，如 `@灵雀助手 你好`
4. 等几秒钟，你应该能收到回复

### 3. 功能测试（可选）

试试这些指令：

```
帮我搜索一下今天的科技新闻
```

```
帮我写一个 Python 脚本，计算 1 到 100 的和
```

```
帮我看看 workspaces 目录下有什么文件
```

```
打开百度首页截个图给我看看
```

---

## 八、进阶配置

### 使用其他大模型

灵雀支持多种大模型，只需修改 `.env`：

#### 通义千问（阿里云）

```ini
LLM_PROVIDER_QWEN=https://dashscope.aliyuncs.com/compatible-mode/v1|sk-你的密钥|qwen-max
LLM_PROVIDER=qwen
```

获取密钥：https://dashscope.console.aliyun.com/

#### Kimi（月之暗面）

```ini
LLM_PROVIDER_KIMI=https://api.moonshot.cn/v1|sk-你的密钥|kimi-k2.5|1
LLM_PROVIDER=kimi
```

获取密钥：https://platform.moonshot.cn/

#### 本地 Ollama（完全离线免费）

```ini
# 先安装 Ollama: https://ollama.ai/
# 然后拉取模型: ollama pull llama3
LLM_PROVIDER_OLLAMA=http://localhost:11434/v1|ollama|llama3
LLM_PROVIDER=ollama
```

#### 配置备用模型（自动降级）

推荐配置一个备用模型，主力模型不可用时自动切换：

```ini
# 主力
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=xxx

# 备用（豆包）
DOUBAO_API_KEY=xxx
DOUBAO_MODEL=doubao-seed-2-0-pro-260215
```

### 让灵雀操作更多本地目录

默认只能操作 `./workspaces` 目录，想让它操作桌面、文档等：

Windows：

```ini
ALLOWED_PATHS=./workspaces,C:\Users\你的用户名\Desktop,C:\Users\你的用户名\Documents
```

Mac：

```ini
ALLOWED_PATHS=./workspaces,/Users/你的用户名/Desktop,/Users/你的用户名/Documents
```

### 限制谁能用

如果你不想所有人都能跟机器人对话：

```ini
# 只允许特定用户（多个用逗号隔开）
FEISHU_ALLOWED_USERS=ou_xxxxxxxxxxxxx,ou_yyyyyyyyyyyyy
```

`open_id` 可以在灵雀的日志中找到——当有人发消息时，日志会打印发送者的 `open_id`。

### 配置邮件收发

```ini
# QQ 邮箱示例
EMAIL_IMAP_HOST=imap.qq.com
EMAIL_IMAP_PORT=993
EMAIL_SMTP_HOST=smtp.qq.com
EMAIL_SMTP_PORT=465
EMAIL_USERNAME=你的QQ邮箱@qq.com
EMAIL_PASSWORD=QQ邮箱授权码
```

> QQ 邮箱授权码获取：QQ 邮箱 → 设置 → 账户 → POP3/SMTP 服务 → 开启 → 生成授权码

### 开机自启（可选）

#### Windows：创建启动脚本

在项目目录创建 `start.bat`：

```bat
@echo off
cd /d "%~dp0"
call .venv\Scripts\activate
python -m lobster.main
pause
```

双击 `start.bat` 即可启动。

如果想开机自启，把 `start.bat` 的快捷方式放到：
`C:\Users\你的用户名\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup`

#### Mac：创建启动脚本

在项目目录创建 `start.sh`：

```bash
#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
python -m lobster.main
```

然后赋予执行权限：

```bash
chmod +x start.sh
```

双击或终端执行 `./start.sh` 即可启动。

---

## 九、常见问题排查

### Q1：PowerShell 报错 `Execution Policy`

```
.venv\Scripts\activate : 无法加载文件...因为在此系统上禁止运行脚本
```

**解决**：

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

然后重新激活虚拟环境。

---

### Q2：`pip install` 报错或下载超时

**解决**：使用国内镜像源：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

或永久配置：

```bash
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

---

### Q3：`chromadb` / `sentence-transformers` 安装失败

**解决**：这两个包是向量记忆功能（可选），安装失败不影响核心功能。在 `.env` 中确保：

```ini
VECTOR_MEMORY_ENABLED=false
LEARNING_BACKEND=sqlite
```

---

### Q4：启动报错 `ModuleNotFoundError: No module named 'xxx'`

**原因**：虚拟环境没激活，或依赖没安装完。

**解决**：

```bash
# Windows
.venv\Scripts\activate
pip install -r requirements.txt

# Mac
source .venv/bin/activate
pip install -r requirements.txt
```

---

### Q5：飞书收不到回复

**排查步骤**：

1. **检查日志**：看终端是否有 `飞书 WebSocket 长连接已启动` 的字样
2. **检查 FEISHU_MODE**：本地必须是 `websocket`，不是 `webhook`
3. **检查应用是否发布**：未发布的应用无法收发消息
4. **检查事件订阅**：确认添加了 `im.message.receive_v1` 事件
5. **检查接收方式**：确认选择了「使用长连接接收事件」
6. **检查权限**：确认开通了 `im:message` 等必要权限
7. **检查 App ID / Secret**：复制时不要多空格或少字符

---

### Q6：飞书群聊 @ 机器人没反应，但私聊有反应

**原因**：群聊需要 @ 机器人才会触发。

**解决**：

1. 在群里发消息时要 `@灵雀助手`（你的机器人名）
2. 在 `.env` 中填写 `FEISHU_BOT_OPEN_ID`，帮助灵雀识别 @ 消息

---

### Q7：浏览器自动化报错 `Executable doesn't exist`

**解决**：

```bash
# 安装浏览器引擎
playwright install chromium

# 如果还报缺少系统依赖（Linux/Mac）：
playwright install-deps
```

---

### Q8：启动后 CPU/内存占用很高

**解决**：在 `.env` 中关闭高内存功能：

```ini
VECTOR_MEMORY_ENABLED=false
LEARNING_BACKEND=sqlite
BROWSER_HEADLESS=true
```

---

### Q9：DeepSeek API 响应很慢

**原因**：DeepSeek 服务器有时高峰期排队。

**解决**：

1. 配置一个备用模型（豆包/千问），灵雀会自动切换
2. 调大超时：`AGENT_LLM_TIMEOUT=180`

---

### Q10：Mac M 芯片安装报错 `error: command 'clang' failed`

**解决**：

```bash
xcode-select --install
```

装完后重新 `pip install -r requirements.txt`。

---

## 附录：完整 .env 配置参考

```ini
# ============================================
# 🐦 灵雀 LingQue 本地部署完整配置
# ============================================

# ==================== LLM ====================
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
DEEPSEEK_API_KEY=                    # ← 必填

# 备用模型（推荐配置）
# DOUBAO_API_KEY=
# DOUBAO_MODEL=doubao-seed-2-0-pro-260215

# 自定义模型示例：
# LLM_PROVIDER_QWEN=https://dashscope.aliyuncs.com/compatible-mode/v1|sk-xxx|qwen-max
# LLM_PROVIDER_KIMI=https://api.moonshot.cn/v1|sk-xxx|kimi-k2.5|1
# LLM_PROVIDER_OLLAMA=http://localhost:11434/v1|ollama|llama3

# ==================== 通道 ====================
CHANNELS=cli,feishu

# ==================== 飞书 ====================
FEISHU_APP_ID=                       # ← 必填
FEISHU_APP_SECRET=                   # ← 必填
FEISHU_VERIFICATION_TOKEN=           # ← 必填
FEISHU_ENCRYPT_KEY=
FEISHU_MODE=websocket                # ← 本地必须 websocket
FEISHU_ALLOWED_USERS=
FEISHU_BOT_OPEN_ID=

# ==================== 安全 ====================
REQUIRE_CONFIRMATION=true
ALLOWED_PATHS=./workspaces
MAX_TOOL_LOOPS=25

# ==================== Agent ====================
AGENT_TASK_TIMEOUT=600
AGENT_LLM_TIMEOUT=120
AGENT_TOOL_TIMEOUT=120
AGENT_MAX_LOOPS=25
AGENT_MAX_CONTEXT_MESSAGES=80
AGENT_MAX_CONTEXT_TOKENS=64000

# ==================== 存储 ====================
MEMORY_DIR=./memory
WORKSPACE_DIR=./workspaces

# ==================== 性能 ====================
VECTOR_MEMORY_ENABLED=false
LEARNING_BACKEND=sqlite

# ==================== 浏览器 ====================
BROWSER_MODE=auto
BROWSER_HEADLESS=false
# BROWSER_CDP_PORT=9222

# ==================== 邮件（可选） ====================
# EMAIL_IMAP_HOST=imap.qq.com
# EMAIL_IMAP_PORT=993
# EMAIL_SMTP_HOST=smtp.qq.com
# EMAIL_SMTP_PORT=465
# EMAIL_USERNAME=
# EMAIL_PASSWORD=

# ==================== 会话 ====================
SESSION_IDLE_TIMEOUT=120
SESSION_DAILY_RESET_HOUR=4

# ==================== 日志 ====================
LOG_LEVEL=INFO
```

---

## 🎉 完成！

恭喜你完成了灵雀的本地部署！现在你可以：

- 在**命令行**直接和灵雀对话
- 在**飞书**私聊或群聊中 @ 灵雀
- 让灵雀帮你**操作浏览器、读写文件、执行代码、收发邮件**
- 设置**定时任务**，让灵雀定期执行工作

灵雀会随着使用**自我学习**，越用越聪明。所有数据都存在你本地电脑上，安全可控。

> 有问题？在飞书群里 @ 机器人说"帮我检查一下系统状态"，灵雀会自我诊断。
