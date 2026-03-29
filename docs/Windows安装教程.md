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

## 方式一：源码安装（推荐）

适合绝大多数用户。脚本会 **全自动** 帮你安装 Python、Git，不需要你额外下载任何东西。

> 💡 源码安装非常安全，所有文件只在安装目录内，不会动你电脑上的其他文件。想删掉直接删文件夹就行。

### 第 1 步：打开 PowerShell（管理员）

1. 按键盘上的 **Win 键**（左下角 Windows 图标那个键）
2. 输入 `powershell`
3. 看到「Windows PowerShell」后，**右键** → 选择 **「以管理员身份运行」**
4. 弹出提示框问"是否允许"，点 **是**

> 💡 也可以直接 **右键桌面左下角的开始按钮**（Windows 图标）→ 选「Windows PowerShell (管理员)」

### 第 2 步：（可选）指定安装目录

默认安装到 `C:\Users\你的用户名\lingque`。如果 C 盘空间不足或想装到其他盘，先执行：

```
$env:LINGQUE_INSTALL_DIR = "D:\lingque"
```

把 `D:\lingque` 换成你想装的路径。不需要改就跳过这步。

### 第 3 步：粘贴安装命令

在 PowerShell 窗口里，**复制** 下面这一整行（很长，一定要完整复制），然后在窗口里 **右键粘贴**，按 **回车**：

```
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $f="$env:TEMP\dl.ps1"; $ok=$false; foreach($h in @("cdn.jsdmirror.com","gcore.jsdelivr.net","cdn.jsdelivr.net")){ if(-not $ok){ try{ (New-Object Net.WebClient).DownloadFile("https://$h/gh/LDPrompt/lingque@main/scripts/install-source.ps1",$f); $ok=$true }catch{} } }; if(-not $ok){ Write-Host "[ERR] All CDN failed" -ForegroundColor Red; pause; exit 1 }; [IO.File]::ReadAllText($f,[Text.Encoding]::UTF8)|Set-Content "$env:TEMP\lingque-install.ps1" -Encoding UTF8; powershell -ExecutionPolicy Bypass -File "$env:TEMP\lingque-install.ps1"
```

> 命令会自动尝试 3 个 CDN 节点，哪个能连上用哪个。

#### ⚠️ 如果报错或下载失败怎么办？

**情况 1：显示 "All CDN failed"**

说明你的网络无法访问所有 CDN。请手动下载安装：

1. 用浏览器打开 https://github.com/LDPrompt/lingque
2. 点绿色 **Code** 按钮 → **Download ZIP**
3. 把下载的 ZIP 解压到你想安装的目录（比如 `D:\lingque`）
4. 在该目录下右键空白处 → **在终端中打开** 或 **在此处打开 PowerShell**
5. 运行：

```
powershell -ExecutionPolicy Bypass -File .\scripts\install-source.ps1
```

**情况 2：提示 "禁止运行脚本"**

先执行这一行解锁，再重新粘贴安装命令：

```
Set-ExecutionPolicy Bypass -Scope Process
```

**情况 3：提示 "未找到 Python 3.11+" 但你电脑已有 Python 3.12**

这是因为 `python` 命令指向了旧版本。先停掉脚本（Ctrl+C），然后手动操作：

```powershell
cd D:\lingque
py -3.12 -m venv venv
Set-ExecutionPolicy Bypass -Scope Process
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple/
```

**情况 4：pip 安装报错 "Could not find a version that satisfies the requirement"**

可能是 pip 源太旧，换清华源重试：

```powershell
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple/
```

如果还不行就用官方源：

```powershell
pip install -r requirements.txt -i https://pypi.org/simple/
```

### 第 4 步：跟着提示操作

脚本会自动检测环境、安装依赖。中途会问你几个问题，按下表操作：

| 提示 | 怎么选 |
|------|--------|
| 选择 AI 模型 | 输入 `1` 选 DeepSeek（推荐），按回车 |
| 输入 API Key | 粘贴你准备好的 Key，按回车 |
| 选择使用方式 | 输入 `1` 选命令行（先试用），或 `2` 选飞书机器人，按回车 |
| 如果选了飞书 | 按提示依次填入飞书 App ID、Secret、Token |

等脚本跑完，看到 **「🎉 灵雀安装完成!」** 就说明成功了。

### 第 5 步：启动灵雀

安装完成后，桌面上会出现一个 **「启动灵雀」** 的快捷方式，**双击** 就能运行。

也可以手动启动：

```powershell
cd D:\lingque  # 换成你的安装目录
Set-ExecutionPolicy Bypass -Scope Process   # 每次新开窗口都要执行这一行
.\venv\Scripts\Activate.ps1
python -m lobster.main
```

> ⚠️ 如果提示 **"禁止运行脚本"**，说明当前 PowerShell 窗口没有执行权限。每次新开 PowerShell 窗口，都要先执行 `Set-ExecutionPolicy Bypass -Scope Process` 再激活虚拟环境。嫌麻烦可以一劳永逸：以管理员身份运行 PowerShell，执行 `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser`，以后就不用每次都打了。

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

### 第 6 步：（可选）启用浏览器控制

灵雀可以控制你本地的 Chrome 浏览器帮你操作网页。安装浏览器驱动：

```powershell
playwright install chromium
```

如果想看到灵雀操作浏览器的过程，编辑 `.env` 文件，加一行：

```
BROWSER_HEADLESS=false
```

重启灵雀后，灵雀操作浏览器时你就能看到 Chrome 窗口在动了。

---

## 方式二：Docker 安装

Docker 是一种容器技术，好处是环境隔离、干净不污染系统。

> ⚠️ **Docker Desktop 要求 Windows 10 22H2 (Build 19045) 或更高版本。** 如果你的 Windows 版本较旧，请使用方式一（源码安装）。
>
> 查看 Windows 版本：按 `Win + R`，输入 `winver`，看到 Build 号。低于 19045 就只能用源码安装。

### 第 1 步：打开 PowerShell（管理员）

和上面一样，右键开始菜单 → **Windows PowerShell (管理员)**。

### 第 2 步：粘贴命令

```
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $f="$env:TEMP\dl.ps1"; $ok=$false; foreach($h in @("cdn.jsdmirror.com","gcore.jsdelivr.net","cdn.jsdelivr.net")){ if(-not $ok){ try{ (New-Object Net.WebClient).DownloadFile("https://$h/gh/LDPrompt/lingque@main/scripts/install-docker.ps1",$f); $ok=$true }catch{} } }; if(-not $ok){ Write-Host "[ERR] All CDN failed" -ForegroundColor Red; pause; exit 1 }; [IO.File]::ReadAllText($f,[Text.Encoding]::UTF8)|Set-Content "$env:TEMP\lingque-install.ps1" -Encoding UTF8; powershell -ExecutionPolicy Bypass -File "$env:TEMP\lingque-install.ps1"
```

> 如果显示 "All CDN failed"，参考上面「方式一」的手动下载方法。

### 第 3 步：等待安装

脚本会自动检测并安装 Docker Desktop 和 Git。安装完成后会**自动启动 Docker Desktop 并等待就绪**，全程无需手动操作。

> 如果脚本提示需要初始化 WSL2 / Hyper-V，按提示同意即可。个别电脑可能需要重启一次，重启后重新运行安装命令就行。

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

配置文件在安装目录的 `.env` 文件里。

```powershell
notepad D:\lingque\.env
```

> ⚠️ 用记事本打开 `.env` 时可能弹出编码提示，点「确定」即可。保存时选择 **文件 → 另存为 → 编码选 UTF-8**，避免编码问题。

### 升级到最新版

**方法 1：在灵雀对话中发送「检查更新」**（推荐）

灵雀会自动检测并升级，全程自动。

**方法 2：手动执行升级脚本**

```powershell
cd D:\lingque
.\scripts\upgrade.ps1
```

> 升级 **不会** 影响你的记忆、配置、凭证和已保存的数据。

### 停止 / 重启

**源码版：**
- 在运行窗口按 `Ctrl+C` = 停止
- 双击桌面快捷方式 = 重新启动
- 或手动：

```powershell
cd D:\lingque
Set-ExecutionPolicy Bypass -Scope Process
.\venv\Scripts\Activate.ps1
python -m lobster.main
```

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

### Q: C 盘空间不够怎么办？

灵雀全部装完大约占 2-3GB。可以在安装前指定其他盘：

```powershell
$env:LINGQUE_INSTALL_DIR = "D:\lingque"
```

然后再运行安装命令即可。Python 本身会装在 C 盘（约 200MB），这个无法避免。

### Q: 电脑里有多个 Python 版本，怎么确认用哪个？

打开 PowerShell 执行：

```powershell
py -0
```

会列出所有已安装的 Python 版本。灵雀需要 **3.11 或以上**。如果默认版本不对，手动创建虚拟环境：

```powershell
py -3.12 -m venv venv
```

### Q: Docker Desktop 装不上，提示 "incompatible version of Windows"

你的 Windows 版本低于 22H2 (Build 19045)，不支持最新 Docker Desktop。请用 **方式一源码安装**，效果完全一样。

### Q: GitHub 完全打不开 / 下载不了怎么办？

国内部分网络无法直接访问 GitHub，可以尝试：

1. **使用代理克隆**：

```powershell
git clone --depth 1 https://ghproxy.net/https://github.com/LDPrompt/lingque.git D:\lingque
```

如果这条也不行，换一个：

```powershell
git clone --depth 1 https://mirror.ghproxy.com/https://github.com/LDPrompt/lingque.git D:\lingque
```

2. **浏览器下载 ZIP**：一般浏览器可以正常打开 GitHub 页面（即使终端不行），下载 ZIP 解压后按手动安装步骤操作。

3. **用手机热点**：换个网络环境可能就通了。

### Q: 每次启动都提示 "禁止运行脚本" / "Activate.ps1 无法加载"

Windows 默认禁止运行 PowerShell 脚本。有两种解决办法：

**方法 1（每次）：** 在当前窗口临时解锁
```powershell
Set-ExecutionPolicy Bypass -Scope Process
```

**方法 2（一劳永逸）：** 以管理员身份打开 PowerShell，执行：
```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```
以后所有新窗口都不用再打这条命令了。

### Q: Python 安装后提示"无法识别"

关闭当前 PowerShell 窗口，**重新打开** 一个新的管理员 PowerShell，再运行安装命令即可。（新安装的 Python 需要新窗口才能识别到）

### Q: Docker Desktop 启动后一直显示 "Starting..."

Docker Desktop 首次启动需要初始化，可能要等 2-5 分钟。如果超过 10 分钟还没好，重启电脑试试。

### Q: Docker 构建报错 "no such host" / "mirrors" 相关

Docker 镜像源配置失效了。按上面「Docker 构建报错怎么办」的步骤清除镜像源配置即可。

### Q: 已经有源码了，Docker 构建失败怎么继续？

不需要重新安装，直接在源码目录执行：

```powershell
cd D:\lingque
docker compose up -d --build
```

如果还报镜像源错误，先修复 Docker 镜像源（见上文），再执行这条命令。

### Q: 升级后数据会丢失吗？

**不会。** 你的记忆、配置（.env）、凭证、定时任务等所有数据都保存在独立目录中，升级只更新代码，不影响数据。

### Q: 想看到灵雀操作浏览器的过程

编辑 `.env`，加一行 `BROWSER_HEADLESS=false`，重启灵雀即可看到 Chrome 窗口。

### Q: 怎么卸载？

1. 停止灵雀（Ctrl+C 或 `docker compose down`）
2. 删除安装目录（比如 `D:\lingque`）
3. 删除桌面上的「启动灵雀」快捷方式
4. （可选）卸载 Python / Docker Desktop

---

## 还有问题？

加入我们的用户群获取帮助，或在 [GitHub Issues](https://github.com/LDPrompt/lingque/issues) 反馈问题。
