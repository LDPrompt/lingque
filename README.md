<h1 align="center">🐦 灵雀 LingQue</h1>

<p align="center">
  <strong>完全自主可控的私人 AI Agent 框架</strong><br/>
  一行命令部署，飞书/钉钉/命令行多通道接入，自我进化，开箱即用
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" alt="Python"></a>
  <a href="https://github.com/LDPrompt/lingque/stargazers"><img src="https://img.shields.io/github/stars/LDPrompt/lingque?style=social" alt="Stars"></a>
  <a href="https://github.com/LDPrompt/lingque/network/members"><img src="https://img.shields.io/github/forks/LDPrompt/lingque?style=social" alt="Forks"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/飞书-WebSocket 长连接-00D09C?logo=bytedance&logoColor=white" alt="Feishu">
  <img src="https://img.shields.io/badge/钉钉-Webhook-0089FF?logo=dingtalk&logoColor=white" alt="DingTalk">
  <img src="https://img.shields.io/badge/CLI-Terminal-4EAA25?logo=gnubash&logoColor=white" alt="CLI">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Playwright-2EAD33?logo=playwright&logoColor=white" alt="Playwright">
  <img src="https://img.shields.io/badge/asyncio-3776AB?logo=python&logoColor=white" alt="asyncio">
  <img src="https://img.shields.io/badge/ChromaDB-FF6F00?logoColor=white" alt="ChromaDB">
  <img src="https://img.shields.io/badge/SQLite_FTS5-003B57?logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/Pydantic-E92063?logo=pydantic&logoColor=white" alt="Pydantic">
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> •
  <a href="#功能特性">功能特性</a> •
  <a href="#架构设计">架构设计</a> •
  <a href="#宝塔面板部署指南">宝塔部署</a> •
  <a href="#配置参考">配置参考</a>
</p>

---

## 灵雀是什么

灵雀（LingQue）是一个**开源的私人 AI Agent 框架**，由灵动 Prompt 团队开发。它不是一个简单的聊天机器人——而是一个能**理解指令、调用工具、自主完成任务、从经验中持续学习**的智能助手。

你可以把它部署到自己的服务器上，通过飞书、钉钉或命令行与它对话，让它帮你：

- 浏览网页、自动填表登录、解决滑块验证码
- 读写文件、执行代码、管理服务器
- 收发邮件、管理日历、定时推送摘要
- 创建飞书云文档、自动分享给团队
- 运行 YAML 声明式工作流、自动化复杂流程
- 记住你的偏好，跨会话保持长期记忆，构建知识图谱
- 从每次任务中**自我学习**，越用越聪明

所有数据存储在你自己的服务器上，**不上传任何第三方平台**。

---

## 功能特性

### 智能体引擎

| 功能 | 说明 |
|------|------|
| ReAct 推理循环 | 思考 → 行动 → 观察，多轮自主决策，最大 30 轮循环 |
| 任务规划 | 复杂任务自动拆解为步骤并展示到飞书卡片 |
| 上下文压缩 | LLM 智能摘要 + 滑动窗口，长对话不丢失关键信息 |
| 多会话隔离 | 基于 `contextvars.ContextVar`，不同群聊/用户的对话互不干扰 |
| 死循环检测 | 自动识别重复行为和卡死循环，智能干预并恢复 |
| Token 优化 | 动态上下文压缩、工具结果截断、精确 token 计数，防止 token 燃烧 |
| 多 Agent 协作 | 子任务自动分发给专业 Agent 并行处理 |
| Ralph Loop | 自主循环引擎，可设定长期目标让灵雀自动推进 |

### 记忆系统

| 功能 | 说明 |
|------|------|
| 短期记忆 | 当前会话上下文，最大 80 条消息 / 64K tokens |
| 长期记忆 | 自动提取重要信息持久化到 MEMORY.md |
| 向量检索 | ChromaDB + sentence-transformers 语义搜索（大服务器） |
| BM25 检索 | 关键词级别的混合检索，补充向量搜索 |
| 知识图谱 | 实体-关系自动抽取，结构化知识存储与查询 |
| 每日日志 | 自动记录每天的工作摘要 |

### 自我进化引擎

灵雀具备**自我学习**能力，越用越聪明：

| 模块 | 功能 |
|------|------|
| 任务反思 | 每次任务完成后自动总结经验教训 |
| 错误-修复配对 | 工具失败后记录错误原因和修复方式，下次自动规避 |
| 用户反馈捕获 | 识别"很好"/"不对"等反馈，自动记录上下文 |
| 工具策略统计 | 跟踪每个工具的成功率和耗时，低成功率工具自动提示换策略 |
| 智能经验召回 | 遇到类似任务时自动召回相关经验注入提示词 |

检索后端可切换，适配不同服务器配置：

| 后端 | 内存占用 | 启动时间 | 额外依赖 | 适合场景 |
|------|---------|---------|---------|---------|
| **SQLite FTS5**（默认） | <5MB | <0.1s | 无 | 2C/4G 小服务器 |
| **ChromaDB 向量** | 500MB-1GB | 10-30s | sentence-transformers, chromadb | 8C/16G+ 大服务器 |

通过 `LEARNING_BACKEND=sqlite` 或 `LEARNING_BACKEND=vector` 切换。

### 浏览器自动化

| 功能 | 说明 |
|------|------|
| CDP 真实浏览器 | 自动检测系统 Chrome/Edge，绕过反爬检测 |
| 智能元素定位 | Accessibility Tree + CSS + XPath 多策略 |
| RPA 级丝滑操作 | 贝塞尔曲线鼠标移动、随机打字延迟、平滑滚动 |
| 智能下拉框处理 | 自动识别原生 `<select>` 和自定义下拉框，支持滚动查找 |
| 滑块验证码破解 | 自动识别滑块 CAPTCHA，类人拖拽轨迹 + 随机抖动 |
| iFrame 自动处理 | 自动进入嵌套 iframe 操作元素 |
| 截图与视觉分析 | 截图发送 + LLM 多模态理解页面内容 |
| 数据抓取 | 网络请求监控、结构化数据提取 |
| Cookie 持久化 | 自动保存/加载登录状态 |
| 截图指导式登录 | 截图 → 发飞书 → 用户指导 → 自动操作 |

### 工作流引擎

支持 YAML 声明式工作流，自动化复杂多步骤流程：

```yaml
name: 每日报告
trigger: cron("0 9 * * *")
steps:
  - tool_call: browser_open
    args: { url: "https://dashboard.example.com" }
  - tool_call: browser_screenshot
  - llm: "根据截图生成今日数据报告"
  - notify: feishu
```

内置工作流模板：代码审查、部署检查、批量文件分析、每日报告。

### 通道支持

| 通道 | 功能 |
|------|------|
| 💬 **飞书** | WebSocket 长连接（无需公网 IP）、交互式卡片、任务确认、进度显示、群聊 @、文件上传 |
| 💬 **钉钉** | 基础消息收发 |
| 🖥️ **命令行** | 本地调试，Rich 美化输出 |

### 技能体系

| 类别 | 技能 |
|------|------|
| 📂 **文件操作** | 读写文件、目录管理、文件搜索（安全路径校验） |
| 💻 **代码执行** | Python 执行（安全校验）、系统命令分级管控、Docker 沙箱隔离 |
| 📧 **邮件日历** | 邮件收发（IMAP/SMTP）、飞书日历管理、邮件新消息监控 |
| 📄 **飞书云文档** | 创建文档 → 写入 Markdown → 自动授权 → 发送链接 |
| 👥 **飞书群聊** | @ 群成员、查看群成员列表 |
| 📅 **定时任务** | Cron 表达式调度、磁盘持久化、每日摘要推送 |
| 🔌 **MCP 协议** | 标准 MCP 接入外部工具服务器 |
| 🏪 **技能市场** | 从 GitHub 在线安装社区技能 |
| 🔧 **技能自动生成** | 描述需求，LLM 自动生成技能代码并注册 |
| 🧠 **知识图谱** | 实体/关系查询、LLM 智能抽取 |
| 📈 **自我学习** | 自动记录错误和经验，持续优化 |
| 🔗 **Webhook** | 接收 GitHub/Sentry 等外部事件通知 |
| 🔌 **插件热加载** | 运行时扫描 plugins 目录，自动加载新插件 |

### LLM 支持

| 提供商 | 模型 | 用途 |
|--------|------|------|
| DeepSeek | deepseek-chat / deepseek-reasoner | 主力模型（推荐） |
| 豆包 (Doubao) | doubao-seed-2-0-pro | 备用模型 + 多模态（看图） |
| OpenAI | GPT-4o 等 | 可选 |
| Anthropic | Claude 系列 | 可选 |

支持**自动降级**：主力模型不可用时自动切换到备用模型，含错误重试和消息校验。

---

## 架构设计

```
┌───────────────────────────────────────────────────────────────────┐
│                     用户 (飞书 / 钉钉 / CLI)                       │
└──────────────────────────────┬────────────────────────────────────┘
                               │
                               ▼
┌───────────────────────────────────────────────────────────────────┐
│                        Gateway 网关层                              │
│                                                                   │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐                    │
│  │ 飞书适配器 │    │ 钉钉适配器 │    │ CLI 适配器│                    │
│  │ WebSocket │    │ Webhook  │    │ Terminal │                    │
│  └─────┬─────┘    └─────┬────┘    └─────┬────┘                    │
│        └────────────────┼───────────────┘                         │
│                         ▼                                         │
│              ┌─────────────────────┐                              │
│              │   消息路由 + 鉴权    │                              │
│              │  (白名单/并发控制)   │                              │
│              └──────────┬──────────┘                              │
└─────────────────────────┼─────────────────────────────────────────┘
                          ▼
┌───────────────────────────────────────────────────────────────────┐
│                        Agent 智能体层                              │
│                                                                   │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────┐       │
│  │  ReAct 循环   │  │  任务规划器    │  │  上下文压缩器     │       │
│  │  (core.py)   │  │  (步骤展示)    │  │  (LLM 摘要)      │       │
│  └──────┬───────┘  └───────────────┘  └──────────────────┘       │
│         │                                                         │
│  ┌──────▼───────┐  ┌───────────────┐  ┌──────────────────┐       │
│  │  记忆系统     │  │  技能注册中心  │  │  自我进化引擎     │       │
│  │ 短期+长期+向量│  │  (可插拔技能)  │  │ (反思/纠错/反馈)  │       │
│  │ +知识图谱     │  │  + 插件热加载  │  │ SQLite/Vector   │       │
│  └──────────────┘  └───────────────┘  └──────────────────┘       │
│                                                                   │
│  ┌──────────────┐  ┌───────────────┐                              │
│  │ Ralph Loop   │  │  工作流引擎    │                              │
│  │ (自主循环)    │  │  (YAML声明式)  │                              │
│  └──────────────┘  └───────────────┘                              │
└──────────────────────────┬────────────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
┌────────────────┐ ┌──────────────┐ ┌──────────────────┐
│  Skills 技能层  │ │ Browser 层   │ │  External 外部层  │
│                │ │              │ │                  │
│ • 文件操作     │ │ • CDP 连接   │ │ • 飞书云文档     │
│ • 代码执行     │ │ • RPA 操作   │ │ • MCP 协议      │
│ • 邮件日历     │ │ • 截图分析   │ │ • 技能市场      │
│ • 定时任务     │ │ • 滑块破解   │ │ • Webhook       │
│ • Docker 沙箱  │ │ • Cookie    │ │ • 邮件监控      │
│ • 知识图谱     │ │ • iFrame    │ │ • 心跳引擎      │
└────────────────┘ └──────────────┘ └──────────────────┘
                           │
                           ▼
┌───────────────────────────────────────────────────────────────────┐
│                     LLM Router 模型路由层                          │
│                                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │ DeepSeek │  │   豆包    │  │  OpenAI  │  │ Claude   │         │
│  │  (主力)   │  │  (备用)   │  │  (可选)   │  │  (可选)   │         │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘         │
│                                                                   │
│              自动降级 / 错误重试 / 消息校验                         │
└───────────────────────────────────────────────────────────────────┘
```

### 核心工作流

```
用户发消息 → 网关接收 → 鉴权校验 → Agent 接管
                                        │
                                 ┌──────▼──────┐
                                 │ 经验召回     │ ← 学习引擎检索相关经验
                                 │ + 任务规划   │ ← 展示步骤到飞书卡片
                                 └──────┬──────┘
                                        ▼
                              ┌──── ReAct 循环 ────┐
                              │                    │
                              │  思考 → 选择工具   │
                              │    ↓              │
                              │  执行工具 (技能)   │ → 记录工具统计
                              │    ↓              │
                              │  观察结果          │ → 错误则记录纠错配对
                              │    ↓              │
                              │  继续/完成?        │──→ 完成 → 任务反思 → 返回结果
                              │    ↓              │
                              │  上下文压缩?       │ ← 动态检测是否需要压缩
                              │    ↓              │
                              │  继续循环          │
                              └───────────────────┘
```

---

## 快速开始

### 环境要求

| 项目 | 最低要求 | 推荐配置 |
|------|---------|---------|
| Python | 3.11+ | 3.12 |
| 内存 | 2GB | 4GB+ |
| 系统 | Linux / macOS / Windows | Ubuntu 20.04+ / CentOS 7+ |

### 1. 克隆项目

```bash
git clone https://github.com/LDPrompt/lingque.git
cd lingque
```

### 2. 安装依赖

```bash
# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 安装浏览器引擎（浏览器自动化必需）
playwright install chromium
```

### 3. 配置

```bash
cp .env.example .env
```

编辑 `.env`，最小配置：

```ini
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
DEEPSEEK_API_KEY=你的API密钥

CHANNELS=cli
REQUIRE_CONFIRMATION=true
ALLOWED_PATHS=./workspaces
MEMORY_DIR=./memory
WORKSPACE_DIR=./workspaces
```

### 4. 启动

```bash
python -m lobster.main
```

看到以下输出说明启动成功：

```
   __    _             ____
  / /   (_)___  ____ _/ __ \__  _____
 / /   / / __ \/ __ `/ / / / / / / _ \
/ /___/ / / / / /_/ / /_/ / /_/ /  __/
\____/_/_/ /_/\__, /\___\_\__,_/\___/
             /____/
🐦 灵雀 LingQue v0.4.0 - 灵动 Prompt 出品

你>
```

### Docker 部署

```bash
# 使用 docker-compose 一键启动
docker-compose up -d

# 查看日志
docker-compose logs -f
```

---

## 宝塔面板部署指南

> 适用于阿里云 / 腾讯云 / 华为云等使用**宝塔面板 (BT Panel)** 管理的 Linux 服务器。

### 1. 服务器要求

| 项目 | 最低配置 | 推荐配置 |
|------|---------|---------|
| CPU | 1 核 | 2 核+ |
| 内存 | 2 GB | 4 GB+ |
| 硬盘 | 20 GB | 40 GB |
| 系统 | CentOS 7+ / Alibaba Cloud Linux 3 / Ubuntu 20.04+ | 同左 |
| 宝塔版本 | 7.x+ | 最新版 |

> **2C/4G 小服务器提示**：设置 `VECTOR_MEMORY_ENABLED=false` 关闭向量库（省 500MB+ 内存），学习引擎默认使用 SQLite FTS5（<5MB 内存），完全不影响功能。

### 2. 安装 Python 环境

在宝塔面板中：

1. 进入 **软件商店** → 搜索 **Python项目管理器** → 安装
2. 在 Python 项目管理器中安装 **Python 3.12**

或者通过 SSH 终端：

```bash
# CentOS / Alibaba Cloud Linux
yum install -y gcc openssl-devel bzip2-devel libffi-devel zlib-devel
wget https://www.python.org/ftp/python/3.12.8/Python-3.12.8.tgz
tar xzf Python-3.12.8.tgz && cd Python-3.12.8
./configure --enable-optimizations --prefix=/usr/local/python312
make -j$(nproc) && make install
ln -s /usr/local/python312/bin/python3.12 /usr/bin/python3.12
```

### 3. 安装 Google Chrome（浏览器自动化必需）

#### CentOS / Alibaba Cloud Linux

```bash
cat > /etc/yum.repos.d/google-chrome.repo << 'EOF'
[google-chrome]
name=Google Chrome
baseurl=https://dl.google.com/linux/chrome/rpm/stable/x86_64
enabled=1
gpgcheck=1
gpgkey=https://dl.google.com/linux/linux_signing_key.pub
EOF

yum install -y google-chrome-stable
google-chrome --version
```

#### Ubuntu / Debian

```bash
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add -
echo "deb [arch=amd64] https://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list
apt update && apt install -y google-chrome-stable
```

### 4. 部署项目

```bash
mkdir -p /www/wwwroot/lingque
cd /www/wwwroot/lingque

# 上传代码（Git 或宝塔面板上传）
git clone https://github.com/LDPrompt/lingque.git .

# 创建虚拟环境
python3.12 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 安装浏览器引擎
playwright install chromium

# 创建工作目录
mkdir -p workspaces memory logs downloads
```

### 5. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，服务器推荐配置：

```ini
# ===== LLM =====
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
DEEPSEEK_API_KEY=你的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# 备用模型（推荐配置，用于多模态和自动降级）
DOUBAO_API_KEY=你的豆包密钥
DOUBAO_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
DOUBAO_MODEL=doubao-seed-2-0-pro-260215

# ===== 通道 =====
CHANNELS=feishu

# ===== 飞书 =====
FEISHU_APP_ID=你的应用ID
FEISHU_APP_SECRET=你的应用密钥
FEISHU_VERIFICATION_TOKEN=你的验证Token
FEISHU_MODE=websocket
FEISHU_ALLOWED_USERS=
FEISHU_BOT_OPEN_ID=机器人的open_id

# ===== 安全 =====
REQUIRE_CONFIRMATION=true
MAX_TOOL_LOOPS=30
ALLOWED_PATHS=/www/wwwroot/lingque/workspaces,/tmp

# ===== Agent =====
AGENT_TASK_TIMEOUT=900
AGENT_LLM_TIMEOUT=120
AGENT_TOOL_TIMEOUT=120
AGENT_MAX_CONTEXT_MESSAGES=80
AGENT_MAX_CONTEXT_TOKENS=64000

# ===== 存储 =====
MEMORY_DIR=/www/wwwroot/lingque/memory
WORKSPACE_DIR=/www/wwwroot/lingque/workspaces

# ===== 浏览器 =====
BROWSER_MODE=auto
BROWSER_HEADLESS=true
BROWSER_CDP_PORT=9222

# ===== 性能优化（2C/4G 小服务器推荐） =====
VECTOR_MEMORY_ENABLED=false
LEARNING_BACKEND=sqlite
```

### 6. 配置 Systemd 守护进程

```bash
cat > /etc/systemd/system/lingque.service << 'EOF'
[Unit]
Description=LingQue AI Agent
After=network.target docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/www/wwwroot/lingque
ExecStart=/www/wwwroot/lingque/.venv/bin/python -m lobster.main
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable lingque
systemctl start lingque
```

### 7. 常用运维命令

```bash
# 查看状态
systemctl status lingque

# 查看实时日志
journalctl -u lingque -f

# 重启
systemctl restart lingque

# 停止
systemctl stop lingque

# 更新代码后重启
cd /www/wwwroot/lingque && git pull && systemctl restart lingque
```

### 8. 飞书机器人配置

1. 前往 [飞书开放平台](https://open.feishu.cn/) → 创建企业自建应用
2. 获取 **App ID** 和 **App Secret** 填入 `.env`
3. 在 **事件与回调** 中选择 **使用长连接接收事件**（对应 `FEISHU_MODE=websocket`）
4. 添加以下**事件订阅**：
   - `im.message.receive_v1` — 接收消息
   - `im.message.reaction.created_v1` — 表情回复（可选）
5. 添加以下**机器人能力**：
   - 消息与群组 → 接收消息、发送消息、上传图片/文件
   - 云文档（如需飞书文档功能）→ 创建文档、管理权限
6. **发布应用** 并在飞书中添加机器人到群聊

### 9. 验证部署

```bash
# 1. 检查服务是否正常运行
systemctl status lingque

# 2. 检查日志是否有报错
journalctl -u lingque --no-pager -n 50

# 3. 在飞书中 @ 机器人发送"你好"，应收到回复
```

---

## 项目结构

```
lingque/
├── lobster/                        # 核心代码
│   ├── main.py                     # 启动入口，初始化所有模块
│   ├── config.py                   # Pydantic 配置管理
│   ├── transplanter.py             # 技能市场移植器
│   │
│   ├── agent/                      # 智能体核心
│   │   ├── core.py                 # ReAct 循环、任务规划、死循环检测
│   │   ├── context.py              # 动态系统提示词构建 + 经验注入
│   │   ├── context_engine.py       # 上下文引擎 (RAG/默认双模式)
│   │   ├── memory.py               # 记忆系统（短期/长期/压缩/向量）
│   │   ├── multi_agent.py          # 多 Agent 协作（子任务分发）
│   │   └── ralph_loop.py           # Ralph Loop 自主循环引擎
│   │
│   ├── gateway/                    # 通道网关
│   │   ├── base.py                 # Channel 抽象基类
│   │   ├── feishu.py               # 飞书（WebSocket/卡片/文件上传）
│   │   ├── dingtalk.py             # 钉钉适配器
│   │   └── cli.py                  # 命令行适配器（Rich 美化）
│   │
│   ├── llm/                        # LLM 模型层
│   │   ├── base.py                 # Provider 抽象基类
│   │   ├── router.py               # 模型路由、自动降级、消息校验
│   │   ├── openai_provider.py      # OpenAI / DeepSeek / 豆包
│   │   ├── anthropic_provider.py   # Claude
│   │   └── streaming.py            # 流式响应支持
│   │
│   ├── skills/                     # 技能模块（可插拔）
│   │   ├── registry.py             # 技能注册中心
│   │   ├── file_ops.py             # 文件操作
│   │   ├── web_browse.py           # 网页浏览（httpx 轻量抓取）
│   │   ├── code_runner.py          # 代码执行（安全分级）
│   │   ├── email_calendar.py       # 邮件 + 飞书日历
│   │   ├── browser_login.py        # 截图指导式登录
│   │   ├── feishu_docs.py          # 飞书云文档（创建/写入/分享）
│   │   ├── feishu_group.py         # 飞书群操作（@成员/查群员）
│   │   ├── skill_market.py         # 技能市场浏览与安装
│   │   ├── skill_generator.py      # LLM 自动生成技能
│   │   ├── memory_skills.py        # 记忆管理技能
│   │   ├── knowledge_skills.py     # 知识图谱查询技能
│   │   ├── scheduler_skills.py     # 定时任务技能
│   │   ├── workflow_skills.py      # 工作流引擎技能
│   │   ├── ralph_skills.py         # Ralph Loop 技能
│   │   ├── mcp_skills.py           # MCP 工具注册与调用
│   │   ├── self_improvement.py     # 自我学习与错误记录
│   │   └── plugin_loader.py        # 动态插件加载
│   │
│   ├── browser/                    # 浏览器自动化
│   │   └── playwright_browser.py   # Playwright + CDP + RPA 操作
│   │
│   ├── memory/                     # 记忆增强模块
│   │   ├── vector_store.py         # ChromaDB 向量存储
│   │   ├── search_backend.py       # 检索后端（SQLite FTS5 / Vector）
│   │   ├── learning_engine.py      # 自我进化引擎
│   │   ├── knowledge_graph.py      # 知识图谱（实体-关系）
│   │   ├── bm25.py                 # BM25 关键词检索
│   │   ├── auto_extract.py         # LLM 自动记忆提取
│   │   └── context_compressor.py   # 上下文压缩器
│   │
│   ├── sandbox/                    # 安全沙箱
│   │   └── docker_sandbox.py       # Docker 隔离执行
│   │
│   ├── scheduler/                  # 调度系统
│   │   ├── cron_scheduler.py       # Cron 定时调度
│   │   ├── task_queue.py           # 消息排队处理
│   │   ├── email_monitor.py        # 邮件新消息监控
│   │   ├── heartbeat.py            # 心跳引擎（30 分钟巡检）
│   │   └── webhook_handler.py      # 外部事件接收
│   │
│   ├── workflow/                   # 声明式工作流
│   │   ├── engine.py               # 工作流执行引擎
│   │   ├── models.py               # 数据模型
│   │   ├── loader.py               # YAML 加载器
│   │   ├── steps.py                # 步骤定义
│   │   ├── store.py                # 工作流状态存储
│   │   └── context.py              # 运行时上下文
│   │
│   └── mcp/                        # MCP 协议支持
│       └── client.py               # MCP 客户端管理器
│
├── workspaces/                     # Agent 工作目录
│   ├── plugins/                    # 自定义插件目录
│   └── workflows/                  # 工作流 YAML 文件
│       ├── code_review.yaml
│       ├── deploy_check.yaml
│       ├── batch_file_analyze.yaml
│       └── daily_report.yaml
│
├── deploy/                         # 部署相关
│   ├── install.sh                  # 一键安装脚本
│   └── lingque.service             # Systemd 服务文件
│
├── memory/                         # 长期记忆存储
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 配置参考

所有配置通过 `.env` 文件管理，完整的配置项参见 [`.env.example`](.env.example)。

### 核心配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_PROVIDER` | `deepseek` | LLM 服务商: `deepseek` / `openai` / `anthropic` |
| `LLM_MODEL` | `deepseek-chat` | 模型名称 |
| `CHANNELS` | `cli` | 启用的通道，逗号分隔: `cli,feishu,dingtalk` |

### Agent 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AGENT_TASK_TIMEOUT` | `600` | 总任务超时（秒） |
| `AGENT_LLM_TIMEOUT` | `120` | LLM 单次响应超时（秒） |
| `AGENT_TOOL_TIMEOUT` | `120` | 工具执行默认超时（秒） |
| `MAX_TOOL_LOOPS` | `30` | ReAct 循环最大轮次 |
| `AGENT_MAX_CONTEXT_MESSAGES` | `80` | 上下文最大消息条数 |
| `AGENT_MAX_CONTEXT_TOKENS` | `64000` | 上下文最大 Token 数 |
| `AGENT_TOOL_TIMEOUT_OVERRIDES` | — | 工具超时覆盖，格式: `tool:seconds,...` |

### 安全配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `REQUIRE_CONFIRMATION` | `true` | 高风险操作是否需要用户确认 |
| `ALLOWED_PATHS` | `./workspaces` | 允许文件操作的目录（逗号分隔） |
| `FEISHU_ALLOWED_USERS` | 空 | 飞书用户白名单（逗号分隔 open_id，空则不限制） |

### 浏览器配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BROWSER_MODE` | `auto` | `auto`=优先真实浏览器, `cdp`=强制 CDP, `builtin`=内置 Chromium |
| `BROWSER_HEADLESS` | `true` | 是否无头模式 |
| `BROWSER_CDP_PORT` | `9222` | CDP 调试端口 |

### 记忆与学习配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VECTOR_MEMORY_ENABLED` | `true` | 是否启用 ChromaDB 向量记忆库（关闭可省 500MB+ 内存） |
| `LEARNING_BACKEND` | `sqlite` | 学习引擎检索后端: `sqlite`（轻量）或 `vector`（语义搜索） |

### 会话配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SESSION_IDLE_TIMEOUT` | `120` | 会话空闲超时（分钟） |
| `SESSION_DAILY_RESET_HOUR` | `4` | 每日重置时刻（24 小时制） |

### 调度配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SCHEDULER_ENABLED` | `true` | 是否启用定时调度器 |
| `DAILY_SUMMARY_CRON` | `0 8 * * *` | 每日摘要推送时间 |
| `DAILY_SUMMARY_ENABLED` | `true` | 是否启用每日摘要 |

---

## 安全设计

1. **数据本地化** — 所有对话记录、记忆、学习数据存储在你的服务器上，不上传任何第三方
2. **操作分级** — 只读操作(low)无需确认，修改操作(medium)静默执行但有审计，危险操作(high)需要用户明确确认
3. **路径隔离** — 文件操作限制在 `ALLOWED_PATHS` 范围内，无法越权访问
4. **命令安全** — 系统命令分为查询(run_query)和执行(run_command)两类，敏感路径/危险命令自动拦截
5. **输出脱敏** — 命令输出中的 API Key、Token 等敏感信息自动替换为 `[REDACTED]`
6. **代码沙箱** — 支持 Docker 隔离执行不可信代码
7. **并发安全** — 基于 `contextvars.ContextVar` 的请求级状态隔离，多群聊并发互不干扰
8. **用户白名单** — 飞书 `FEISHU_ALLOWED_USERS` 限制访问人员
9. **审计日志** — 所有工具调用记录完整日志

---

## 常见问题

<details>
<summary><strong>Q: 启动报错 ModuleNotFoundError</strong></summary>

缺少依赖，确保在虚拟环境中运行 `pip install -r requirements.txt`。

</details>

<details>
<summary><strong>Q: Playwright 报错 Executable doesn't exist</strong></summary>

需要安装浏览器引擎：`playwright install chromium`。服务器环境如果还缺系统依赖：`playwright install-deps`。

</details>

<details>
<summary><strong>Q: 浏览器打开网页被反爬拦截</strong></summary>

确保安装了 Google Chrome 并配置 `BROWSER_MODE=auto`。灵雀会优先使用系统 Chrome 通过 CDP 连接，比内置 Chromium 更不容易被检测。

</details>

<details>
<summary><strong>Q: 飞书消息收不到回复</strong></summary>

1. 确认 `FEISHU_MODE=websocket` 并已在飞书开放平台启用长连接
2. 检查 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 是否正确
3. 查看日志 `journalctl -u lingque -f` 是否有报错
4. 确认应用已发布且机器人已添加到群聊

</details>

<details>
<summary><strong>Q: 多个群同时使用会不会消息串掉</strong></summary>

不会。灵雀使用 `contextvars.ContextVar` 实现请求级状态隔离，每个群聊/用户拥有独立的会话上下文和记忆空间。

</details>

<details>
<summary><strong>Q: DeepSeek API 响应很慢或报错</strong></summary>

1. 灵雀已内置自动降级机制，如果配置了豆包 API，DeepSeek 不可用时会自动切换
2. 可以调大 `AGENT_LLM_TIMEOUT`（默认 120 秒）
3. 也可以在 `.env` 中切换为其他模型

</details>

<details>
<summary><strong>Q: 内存占用过高（2C/4G 小服务器）</strong></summary>

在 `.env` 中设置：
```ini
VECTOR_MEMORY_ENABLED=false   # 关闭向量库，省 500MB+ 内存
LEARNING_BACKEND=sqlite       # 学习引擎用 SQLite（默认值，可不写）
```
这两项加起来可以减少约 1GB 内存占用，不影响核心功能。

</details>

<details>
<summary><strong>Q: 想用自己的 OpenAI / Claude 模型</strong></summary>

```ini
# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-xxx

# 或 Claude
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-xxx
```

</details>

<details>
<summary><strong>Q: 如何添加自定义插件</strong></summary>

在 `workspaces/plugins/` 目录下创建 `.py` 文件，参考 `_example_plugin.py`。灵雀会在心跳巡检时自动检测并热加载新插件。

</details>

<details>
<summary><strong>Q: 如何使用工作流</strong></summary>

在 `workspaces/workflows/` 目录下创建 YAML 文件，或对灵雀说"创建一个工作流"让它自动生成。

</details>

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.12 |
| 异步框架 | asyncio + aiohttp |
| LLM 接口 | OpenAI SDK (兼容 DeepSeek / 豆包 / Claude) |
| 浏览器自动化 | Playwright + Chrome DevTools Protocol |
| 向量数据库 | ChromaDB + sentence-transformers（可选） |
| 轻量检索 | SQLite FTS5（内置，零依赖） |
| 知识图谱 | 自研（SQLite 存储 + LLM 抽取） |
| 飞书集成 | lark-oapi (官方 SDK) |
| 配置管理 | Pydantic Settings + python-dotenv |
| 任务调度 | croniter + 自研心跳引擎 |
| 工作流引擎 | 自研（YAML 声明式） |
| 容器沙箱 | Docker SDK |
| 部署 | Docker / Systemd / 宝塔面板 |

---

## 开发路线

- [x] ReAct 智能体引擎
- [x] 飞书/钉钉/CLI 多通道
- [x] 文件操作 + 代码执行
- [x] 浏览器自动化（CDP + Playwright + RPA）
- [x] 滑块验证码自动破解
- [x] 长期记忆 + 向量检索 + BM25
- [x] 知识图谱（SuperMemory）
- [x] 上下文压缩 + 会话管理 + Token 优化
- [x] 安全分级 + 用户鉴权
- [x] 飞书云文档集成
- [x] 技能市场 + 自动生成
- [x] MCP 协议支持
- [x] 定时任务 + 邮件监控 + 心跳引擎
- [x] YAML 声明式工作流
- [x] 自我进化引擎（学习/反思/纠错）
- [x] 轻量化 SQLite FTS5 后端
- [x] 插件热加载
- [x] Ralph Loop 自主循环
- [x] Docker 部署
- [ ] Web 管理面板
- [ ] 多 Agent 协作优化
- [ ] 语音交互
- [ ] 更多 IM 平台适配（微信、Telegram）

---

## 贡献指南

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feature/your-feature`
3. 提交代码：`git commit -m "feat: 你的功能描述"`
4. 推送分支：`git push origin feature/your-feature`
5. 创建 Pull Request

### 技能开发

灵雀采用可插拔的技能架构，添加新技能只需在 `lobster/skills/` 下创建文件并注册：

```python
from .registry import registry

@registry.register(
    name="my_skill",
    description="这个技能的作用",
    parameters={"param1": {"type": "string", "description": "参数说明"}},
    risk_level="low",  # low / medium / high
)
async def my_skill(param1: str) -> str:
    # 你的技能逻辑
    return "执行结果"
```

### 插件开发

在 `workspaces/plugins/` 下创建 `.py` 文件即可，灵雀会自动扫描并加载：

```python
from lobster.skills.registry import registry

@registry.register(
    name="my_plugin",
    description="我的自定义插件",
    parameters={},
    risk_level="low",
)
async def my_plugin() -> str:
    return "插件执行成功"
```

---

## 许可证

[MIT License](LICENSE)

---

<p align="center">
  由 <strong>灵动 Prompt</strong> 团队用 ❤️ 打造<br/>
  <sub>让每个人都有自己的 AI Agent</sub>
</p>
