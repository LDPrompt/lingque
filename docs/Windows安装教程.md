# 🐦 灵雀 LingQue — Windows 安装教程

> 本教程适用于 Windows 10 / 11 用户，两种安装方式任选其一。

---

## 准备工作（必读）

安装前你只需要准备 **一样东西**：

> **一个 AI 模型的 API Key**（推荐 DeepSeek，便宜好用）
>
> 获取方式：
> 1. 打开 https://platform.deepseek.com/
> 2. 注册账号 → 充值（几块钱就够用很久）
> 3. 左侧菜单「API Keys」→ 创建一个
> 4. **复制保存好**，安装时要用

---

## 方式一：源码安装（推荐新手）

适合没装过 Docker 的用户。脚本会 **全自动** 帮你安装 Python、Git，不需要你额外下载任何东西。

### 第 1 步：打开 PowerShell（管理员）

1. 按键盘上的 **Win 键**（左下角 Windows 图标那个键）
2. 输入 `powershell`
3. 看到「Windows PowerShell」后，**右键** → 选择 **「以管理员身份运行」**
4. 弹出提示框问"是否允许"，点 **是**

> 💡 也可以直接 **右键桌面左下角的开始按钮**（Windows 图标）→ 选「Windows PowerShell (管理员)」

### 第 2 步：粘贴命令

在打开的蓝色窗口里，**复制** 下面这一整行，然后在窗口里 **右键粘贴**，按 **回车**：

```
irm https://cdn.jsdelivr.net/gh/LDPrompt/lingque@main/scripts/install-source.ps1 | iex
```

#### ⚠️ 如果报错或下载失败怎么办？

**情况 1：命令报错 / 下载失败 / 提示乱码**

jsdelivr CDN 可能有缓存，试试备用地址：

```
irm https://raw.githubusercontent.com/LDPrompt/lingque/main/scripts/install-source.ps1 | iex
```

**情况 2：两个地址都不行 / 提示 "getaddrinfo" 或 "unable to access"**

说明你的网络无法直接访问 GitHub。请按以下步骤手动安装：

1. 用浏览器打开 https://github.com/LDPrompt/lingque
2. 点绿色 **Code** 按钮 → **Download ZIP**
3. 把下载的 ZIP 解压到 `C:\Users\你的用户名\lingque` 目录
4. 在该目录下右键空白处 → **在终端中打开** 或 **在此处打开 PowerShell**
5. 运行：

```
.\scripts\install-source.ps1

```

**情况 3：提示 "无法识别 irm"**

说明 PowerShell 版本太旧，请使用替代命令：

```
powershell -ExecutionPolicy Bypass -Command "& { Invoke-WebRequest -Uri 'https://cdn.jsdelivr.net/gh/LDPrompt/lingque@main/scripts/install-source.ps1' -OutFile '$env:TEMP\install.ps1'; & '$env:TEMP\install.ps1' }"

```

**情况 4：提示 "禁止运行脚本"**

先执行这一行解锁，再重新粘贴安装命令：

```
Set-ExecutionPolicy Bypass -Scope Process

```

### 第 3 步：跟着提示操作

脚本会自动检测环境、安装依赖。中途会问你几个问题，按下表操作：

| 提示 | 怎么选 |
|------|--------|
| 选择 AI 模型 | 输入 `1` 选 DeepSeek（推荐），按回车 |
| 输入 API Key | 粘贴你准备好的 Key，按回车 |
| 选择使用方式 | 输入 `1` 选命令行（先试用），或 `2` 选飞书机器人，按回车 |
| 如果选了飞书 | 按提示依次填入飞书 App ID、Secret、Token |

等脚本跑完，看到 **「🎉 灵雀安装完成!」** 就说明成功了。

### 第 4 步：启动灵雀

安装完成后，桌面上会出现一个 **「启动灵雀」** 的快捷方式，**双击** 就能运行。

看到下面这个画面就说明成功了：

```
   __    _             ____
  / /   (_)___  ____ _/ __ \__  _____
 / /   / / __ \/ __ `/ / / / / / / _ \
/ /___/ / / / / /_/ / /_/ / /_/ /  __/
\____/_/_/ /_/\__, /\___\_\__,_/\___/
             /____/
🐦 灵雀 LingQue v1.3.0 - 灵动 Prompt 出品

你>
```

现在可以直接打字和灵雀对话了！试试输入「你好」。

---

## 方式二：Docker 安装（推荐有一定基础的用户）

Docker 是一种容器技术，好处是环境隔离、干净不污染系统。脚本会自动帮你安装 Docker Desktop 和 Git。

### 第 1 步：打开 PowerShell（管理员）

和上面一样，右键开始菜单 → **Windows PowerShell (管理员)**。

### 第 2 步：粘贴命令

```
irm https://cdn.jsdelivr.net/gh/LDPrompt/lingque@main/scripts/install-docker.ps1 | iex

```

> 如果报错或下载失败，参考上面「方式一」的故障排除方法，把 URL 中的 `install-source.ps1` 换成 `install-docker.ps1` 即可。

### 第 3 步：等待安装

脚本会自动检测并安装 Docker Desktop 和 Git。

**如果是第一次安装 Docker Desktop，需要重启电脑。** 脚本会提示你：

```
Docker Desktop 已安装，需要重启电脑后生效

重启后请按以下步骤继续:
1. 启动 Docker Desktop (桌面图标)
2. 等待 Docker 图标变为稳定状态 (约 1 分钟)
3. 重新打开 PowerShell (管理员)，再次运行安装命令
```

按提示操作即可。第二次运行会跳过 Docker 安装，继续后面的步骤。

### 第 4 步：跟着提示操作

和源码版一样，选模型、填 Key、选通道。跑完看到 **「🎉 灵雀安装完成!」** 就 OK 了。

### 第 5 步：查看运行状态

灵雀在 Docker 里后台运行，不需要双击什么。如果想看日志：

```powershell
cd ~\lingque
docker compose logs -f
```

#### ⚠️ Docker 构建报错怎么办？

如果看到类似 `no such host`、`mirrors.ustc.edu.cn`、`connection refused` 的错误，说明 Docker 镜像源有问题。

**修复方法：**

1. 打开 **Docker Desktop** → 左上角齿轮 **Settings** → **Docker Engine**
2. 把配置改成（或者把 `registry-mirrors` 那行清空为 `[]`）：

```json
{
  "registry-mirrors": []
}
```

3. 点 **Apply & Restart**
4. 等 Docker 重启完毕（约 30 秒），重新运行安装命令即可

---

## 安装完成后的常用操作

### 修改配置

配置文件在安装目录的 `.env` 文件里。打开方式：

```powershell
notepad C:\Users\你的用户名\lingque\.env
```

或者用文件管理器找到 `C:\Users\你的用户名\lingque` 文件夹，右键 `.env` → 用记事本打开。

### 升级到最新版

**方法 1：在灵雀对话中发送「检查更新」**（推荐）

灵雀会自动检测并升级，全程自动。

**方法 2：手动执行升级脚本**

```powershell
cd ~\lingque
.\scripts\upgrade.ps1
```

> 升级 **不会** 影响你的记忆、配置、凭证和已保存的数据。

### 停止 / 重启

**源码版：**
- 关闭「启动灵雀」窗口 = 停止
- 双击桌面快捷方式 = 重新启动

**Docker 版：**

```powershell
cd ~\lingque
docker compose down       # 停止
docker compose restart    # 重启
docker compose up -d      # 启动
```

---

## 连接飞书机器人

如果你想通过飞书和灵雀对话（推荐日常使用），需要配置飞书应用：

### 1. 创建飞书应用

1. 打开 [飞书开放平台](https://open.feishu.cn/)，登录
2. 点击 「创建企业自建应用」
3. 填写应用名称（比如"灵雀助手"），创建

### 2. 获取应用凭证

在应用的 「凭证与基础信息」页面，复制：
- **App ID**
- **App Secret**

### 3. 配置事件订阅

1. 进入 「事件与回调」 页面
2. 选择 **「使用长连接接收事件」**（这样不需要公网 IP）
3. 添加事件：`im.message.receive_v1`（接收消息）

### 4. 添加机器人能力

1. 进入 「应用能力」 → 「机器人」 → 开启
2. 进入 「权限管理」，搜索并开通以下权限：
   - `im:message` — 获取与发送消息
   - `im:message:send_as_bot` — 以机器人身份发送消息
   - `im:resource` — 获取消息中的资源文件（图片、文件等）

### 5. 发布应用

点击 「版本管理与发布」 → 「创建版本」 → 提交审核 → 审核通过后上线

### 6. 修改灵雀配置

编辑 `.env` 文件，找到飞书相关配置，填入你复制的信息：

```ini
CHANNELS=feishu
FEISHU_APP_ID=你的AppID
FEISHU_APP_SECRET=你的AppSecret
FEISHU_VERIFICATION_TOKEN=你的Token
FEISHU_MODE=websocket
```

保存后重启灵雀即可。在飞书里给机器人发消息试试！

---

## 常见问题

### Q: GitHub 完全打不开 / 下载不了怎么办？

国内部分网络无法直接访问 GitHub，可以尝试：

1. **使用代理克隆**：打开 PowerShell，执行：

```powershell
git clone --depth 1 https://ghproxy.net/https://github.com/LDPrompt/lingque.git $env:USERPROFILE\lingque
```

如果这条也不行，换一个：

```powershell
git clone --depth 1 https://mirror.ghproxy.com/https://github.com/LDPrompt/lingque.git $env:USERPROFILE\lingque
```

克隆成功后运行：

```powershell
cd $env:USERPROFILE\lingque
.\scripts\install-source.ps1
```

2. **浏览器下载 ZIP**：一般浏览器可以正常打开 GitHub 页面（即使终端不行），下载 ZIP 解压后按「手动安装」步骤操作。

3. **用手机热点**：换个网络环境可能就通了。

### Q: Python 安装后提示"无法识别"

关闭当前 PowerShell 窗口，**重新打开** 一个新的管理员 PowerShell，再运行安装命令即可。（新安装的 Python 需要新窗口才能识别到）

### Q: Docker Desktop 启动后一直显示 "Starting..."

Docker Desktop 首次启动需要初始化，可能要等 2-5 分钟。如果超过 10 分钟还没好，重启电脑试试。

### Q: Docker 构建报错 "no such host" / "mirrors" 相关

Docker 镜像源配置失效了。按上面「Docker 构建报错怎么办」的步骤清除镜像源配置即可。

### Q: 已经有源码了，Docker 构建失败怎么继续？

不需要重新安装，直接在源码目录执行：

```powershell
cd $env:USERPROFILE\lingque
docker compose up -d --build
```

如果还报镜像源错误，先修复 Docker 镜像源（见上文），再执行这条命令。

### Q: 升级后数据会丢失吗？

**不会。** 你的记忆、配置（.env）、凭证、定时任务等所有数据都保存在独立目录中，升级只更新代码，不影响数据。

### Q: 怎么卸载？

1. 停止灵雀（关窗口或 `docker compose down`）
2. 删除安装目录：`C:\Users\你的用户名\lingque`
3. 删除桌面上的「启动灵雀」快捷方式
4. （可选）卸载 Python / Docker Desktop

---

## 还有问题？

加入我们的用户群获取帮助，或在 [GitHub Issues](https://github.com/LDPrompt/lingque/issues) 反馈问题。
