#!/usr/bin/env bash
#
# 🐦 灵雀 LingQue 一键安装脚本 (Linux / macOS)
#
# 用法:
#   curl -fsSL https://raw.githubusercontent.com/LDPrompt/lingque/main/scripts/install.sh | bash
#
# 或者先下载再执行:
#   wget -qO install.sh https://raw.githubusercontent.com/LDPrompt/lingque/main/scripts/install.sh
#   bash install.sh
#
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${CYAN}[灵雀]${NC} $1"; }
ok()   { echo -e "${GREEN}[✅]${NC} $1"; }
warn() { echo -e "${YELLOW}[⚠️]${NC} $1"; }
err()  { echo -e "${RED}[❌]${NC} $1"; exit 1; }

REPO_URL="https://github.com/LDPrompt/lingque.git"
INSTALL_DIR="${LINGQUE_INSTALL_DIR:-/opt/lingque}"

# =====================================================================
# .env 交互式配置
# =====================================================================
setup_env() {
    if [[ -f .env ]]; then
        warn ".env 已存在，跳过配置"
        return
    fi

    cp .env.example .env
    echo ""
    echo -e "${BOLD}── 快速配置 ──${NC}"
    echo ""

    echo "选择默认 AI 模型:"
    echo "  1) DeepSeek (推荐，性价比高)"
    echo "  2) 豆包 Doubao (字节跳动)"
    echo "  3) OpenAI (GPT)"
    echo "  4) Anthropic (Claude)"
    echo "  5) 其他 (稍后手动配置)"
    echo ""
    read -rp "请选择 [1-5] (默认 1): " llm_choice
    llm_choice="${llm_choice:-1}"

    case "$llm_choice" in
        1)
            read -rp "请输入 DeepSeek API Key: " api_key
            if [[ -n "$api_key" ]]; then
                sed -i "s|^LLM_PROVIDER=.*|LLM_PROVIDER=deepseek|" .env
                sed -i "s|^#DEEPSEEK_API_KEY=.*|DEEPSEEK_API_KEY=$api_key|" .env
            fi
            ;;
        2)
            read -rp "请输入豆包 API Key: " api_key
            if [[ -n "$api_key" ]]; then
                sed -i "s|^LLM_PROVIDER=.*|LLM_PROVIDER=doubao|" .env
                sed -i "s|^# DOUBAO_API_KEY=.*|DOUBAO_API_KEY=$api_key|" .env
            fi
            ;;
        3)
            read -rp "请输入 OpenAI API Key: " api_key
            if [[ -n "$api_key" ]]; then
                sed -i "s|^LLM_PROVIDER=.*|LLM_PROVIDER=openai|" .env
                sed -i "s|^# OPENAI_API_KEY=.*|OPENAI_API_KEY=$api_key|" .env
            fi
            ;;
        4)
            read -rp "请输入 Anthropic API Key: " api_key
            if [[ -n "$api_key" ]]; then
                sed -i "s|^LLM_PROVIDER=.*|LLM_PROVIDER=anthropic|" .env
                sed -i "s|^# ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$api_key|" .env
            fi
            ;;
        *)
            warn "请稍后编辑 .env 文件配置 LLM"
            ;;
    esac

    echo ""
    echo "选择使用方式:"
    echo "  1) 命令行 (CLI，调试用)"
    echo "  2) 飞书机器人"
    echo "  3) 钉钉机器人"
    echo "  4) 飞书 + 命令行"
    echo ""
    read -rp "请选择 [1-4] (默认 1): " channel_choice
    channel_choice="${channel_choice:-1}"

    case "$channel_choice" in
        2)
            sed -i "s|^CHANNELS=.*|CHANNELS=feishu|" .env
            echo ""
            read -rp "飞书 App ID: " fs_id
            read -rp "飞书 App Secret: " fs_secret
            read -rp "飞书 Verification Token: " fs_token
            read -rp "飞书 Encrypt Key (可选，直接回车跳过): " fs_encrypt
            [[ -n "$fs_id" ]] && sed -i "s|^FEISHU_APP_ID=.*|FEISHU_APP_ID=$fs_id|" .env
            [[ -n "$fs_secret" ]] && sed -i "s|^FEISHU_APP_SECRET=.*|FEISHU_APP_SECRET=$fs_secret|" .env
            [[ -n "$fs_token" ]] && sed -i "s|^FEISHU_VERIFICATION_TOKEN=.*|FEISHU_VERIFICATION_TOKEN=$fs_token|" .env
            [[ -n "$fs_encrypt" ]] && sed -i "s|^FEISHU_ENCRYPT_KEY=.*|FEISHU_ENCRYPT_KEY=$fs_encrypt|" .env
            ;;
        3)
            sed -i "s|^CHANNELS=.*|CHANNELS=dingtalk|" .env
            echo ""
            read -rp "钉钉 App Key: " dt_key
            read -rp "钉钉 App Secret: " dt_secret
            [[ -n "$dt_key" ]] && sed -i "s|^DINGTALK_APP_KEY=.*|DINGTALK_APP_KEY=$dt_key|" .env
            [[ -n "$dt_secret" ]] && sed -i "s|^DINGTALK_APP_SECRET=.*|DINGTALK_APP_SECRET=$dt_secret|" .env
            ;;
        4)
            sed -i "s|^CHANNELS=.*|CHANNELS=cli,feishu|" .env
            echo ""
            read -rp "飞书 App ID: " fs_id
            read -rp "飞书 App Secret: " fs_secret
            read -rp "飞书 Verification Token: " fs_token
            read -rp "飞书 Encrypt Key (可选，直接回车跳过): " fs_encrypt
            [[ -n "$fs_id" ]] && sed -i "s|^FEISHU_APP_ID=.*|FEISHU_APP_ID=$fs_id|" .env
            [[ -n "$fs_secret" ]] && sed -i "s|^FEISHU_APP_SECRET=.*|FEISHU_APP_SECRET=$fs_secret|" .env
            [[ -n "$fs_token" ]] && sed -i "s|^FEISHU_VERIFICATION_TOKEN=.*|FEISHU_VERIFICATION_TOKEN=$fs_token|" .env
            [[ -n "$fs_encrypt" ]] && sed -i "s|^FEISHU_ENCRYPT_KEY=.*|FEISHU_ENCRYPT_KEY=$fs_encrypt|" .env
            ;;
        *)
            sed -i "s|^CHANNELS=.*|CHANNELS=cli|" .env
            ;;
    esac

    ok "配置已保存到 .env"
}


# =====================================================================
# 主流程
# =====================================================================
echo ""
echo -e "${BOLD}   🐦 灵雀 LingQue 一键安装${NC}"
echo -e "   ${CYAN}灵动 Prompt 出品的私人 AI Agent${NC}"
echo ""

log "正在检查系统环境..."

echo ""
echo -e "${BOLD}请选择安装方式:${NC}"
echo "  1) Docker 安装 (推荐，隔离干净)"
echo "  2) 源码安装 (直接运行 Python)"
echo ""
read -rp "请输入 [1/2] (默认 1): " install_mode
install_mode="${install_mode:-1}"


# ── Docker 安装 ──────────────────────────────────────────────
if [[ "$install_mode" == "1" ]]; then

    if ! command -v docker &>/dev/null; then
        log "未检测到 Docker，正在安装..."
        if command -v apt-get &>/dev/null; then
            apt-get update -qq
            apt-get install -y -qq docker.io docker-compose-plugin >/dev/null 2>&1 || {
                curl -fsSL https://get.docker.com | sh
            }
        elif command -v yum &>/dev/null; then
            yum install -y docker docker-compose-plugin >/dev/null 2>&1 || {
                curl -fsSL https://get.docker.com | sh
            }
        else
            err "无法自动安装 Docker，请手动安装: https://docs.docker.com/get-docker/"
        fi
        systemctl enable docker 2>/dev/null || true
        systemctl start docker 2>/dev/null || true
        ok "Docker 已安装"
    else
        ok "Docker 已就绪: $(docker --version)"
    fi

    if ! docker compose version &>/dev/null 2>&1; then
        if ! command -v docker-compose &>/dev/null; then
            err "docker compose 不可用，请安装 docker-compose-plugin"
        fi
    fi

    log "正在下载灵雀..."
    if [[ -d "$INSTALL_DIR" ]]; then
        warn "目录已存在: $INSTALL_DIR"
        read -rp "是否覆盖? [y/N] " overwrite
        [[ "${overwrite,,}" != "y" ]] && err "安装取消"
        rm -rf "$INSTALL_DIR"
    fi

    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR" 2>/dev/null || \
        err "下载失败，请检查网络"
    ok "下载完成"

    cd "$INSTALL_DIR"
    setup_env

    log "正在构建并启动..."
    docker compose up -d --build

    echo ""
    ok "🎉 灵雀安装完成!"
    echo ""
    echo -e "  安装目录: ${BOLD}$INSTALL_DIR${NC}"
    echo -e "  查看日志: ${CYAN}cd $INSTALL_DIR && docker compose logs -f${NC}"
    echo -e "  停止服务: ${CYAN}cd $INSTALL_DIR && docker compose down${NC}"
    echo -e "  升级版本: ${CYAN}cd $INSTALL_DIR && bash scripts/upgrade.sh${NC}"
    echo ""
    exit 0
fi


# ── 源码安装 ─────────────────────────────────────────────────
PYTHON_CMD=""
for cmd in python3.12 python3.11 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON_CMD" ]]; then
    log "未找到 Python 3.11+，正在安装..."
    if command -v apt-get &>/dev/null; then
        apt-get update -qq
        apt-get install -y -qq python3 python3-pip python3-venv >/dev/null 2>&1
        PYTHON_CMD="python3"
    elif command -v yum &>/dev/null; then
        yum install -y python3 python3-pip >/dev/null 2>&1
        PYTHON_CMD="python3"
    else
        err "请先安装 Python 3.11+: https://www.python.org/downloads/"
    fi
fi
ok "Python 已就绪: $($PYTHON_CMD --version)"

if ! command -v git &>/dev/null; then
    log "正在安装 git..."
    apt-get install -y -qq git 2>/dev/null || yum install -y git 2>/dev/null || err "请先安装 git"
fi

log "正在下载灵雀..."
if [[ -d "$INSTALL_DIR" ]]; then
    warn "目录已存在: $INSTALL_DIR"
    read -rp "是否覆盖? [y/N] " overwrite
    [[ "${overwrite,,}" != "y" ]] && err "安装取消"
    rm -rf "$INSTALL_DIR"
fi

git clone --depth 1 "$REPO_URL" "$INSTALL_DIR" 2>/dev/null || err "下载失败，请检查网络"
ok "下载完成"

cd "$INSTALL_DIR"

log "正在创建 Python 虚拟环境..."
$PYTHON_CMD -m venv venv
source venv/bin/activate
ok "虚拟环境已创建"

log "正在安装依赖 (首次约需 2-5 分钟)..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
ok "依赖安装完成"

log "正在安装浏览器引擎..."
playwright install chromium 2>/dev/null || warn "浏览器引擎安装跳过 (可稍后运行: playwright install chromium)"

setup_env

echo ""
read -rp "是否创建系统服务 (开机自启)? [Y/n] " setup_service
if [[ "${setup_service,,}" != "n" ]]; then
    cat > /etc/systemd/system/lingque.service << SVCEOF
[Unit]
Description=LingQue AI Agent
After=network.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python -m lobster.main
Restart=always
RestartSec=5
Environment=PATH=$INSTALL_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
SVCEOF
    systemctl daemon-reload
    systemctl enable lingque
    systemctl start lingque
    ok "系统服务已创建并启动"
    echo ""
    echo -e "  查看状态: ${CYAN}systemctl status lingque${NC}"
    echo -e "  查看日志: ${CYAN}journalctl -u lingque -f${NC}"
    echo -e "  重启服务: ${CYAN}systemctl restart lingque${NC}"
else
    echo ""
    echo -e "  手动启动: ${CYAN}cd $INSTALL_DIR && source venv/bin/activate && python -m lobster.main${NC}"
fi

echo ""
ok "🎉 灵雀安装完成!"
echo ""
echo -e "  安装目录: ${BOLD}$INSTALL_DIR${NC}"
echo -e "  修改配置: ${CYAN}nano $INSTALL_DIR/.env${NC}"
echo -e "  升级版本: ${CYAN}bash $INSTALL_DIR/scripts/upgrade.sh${NC}"
echo ""
