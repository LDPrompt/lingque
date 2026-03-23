#!/usr/bin/env bash
#
# 灵雀一键升级脚本 (Linux / macOS)
#
# 用法:
#   bash scripts/upgrade.sh          # 交互式
#   bash scripts/upgrade.sh --yes    # 跳过确认
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[灵雀升级]${NC} $1"; }
ok()   { echo -e "${GREEN}[✅]${NC} $1"; }
warn() { echo -e "${YELLOW}[⚠️]${NC} $1"; }
err()  { echo -e "${RED}[❌]${NC} $1"; }

AUTO_YES=false
if [[ "${1:-}" == "--yes" || "${1:-}" == "-y" ]]; then
    AUTO_YES=true
fi

# ── 检测当前版本 ──
CURRENT_VER="unknown"
if [[ -f lobster/__init__.py ]]; then
    CURRENT_VER=$(grep '__version__' lobster/__init__.py | head -1 | sed "s/.*= *['\"]//;s/['\"].*//" )
fi
log "当前版本: v${CURRENT_VER}"

# ── 检测部署方式 ──
DEPLOY_TYPE="source"
if [[ -f /.dockerenv ]] || grep -q docker /proc/1/cgroup 2>/dev/null; then
    DEPLOY_TYPE="docker"
elif systemctl is-active lingque &>/dev/null; then
    DEPLOY_TYPE="systemd"
fi
log "部署方式: ${DEPLOY_TYPE}"

# ── 检查远程更新 ──
log "正在检查更新..."
git fetch origin main --quiet 2>/dev/null || { err "git fetch 失败，请检查网络"; exit 1; }

LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_HEAD=$(git rev-parse origin/main)

if [[ "$LOCAL_HEAD" == "$REMOTE_HEAD" ]]; then
    ok "当前已是最新版本 v${CURRENT_VER}，无需升级"
    exit 0
fi

COMMITS_BEHIND=$(git rev-list HEAD..origin/main --count)
log "发现 ${COMMITS_BEHIND} 个新提交"

if [[ "$AUTO_YES" != "true" ]]; then
    echo ""
    git log HEAD..origin/main --oneline --no-decorate | head -10
    echo ""
    read -rp "是否升级? [Y/n] " confirm
    if [[ "${confirm,,}" == "n" ]]; then
        log "已取消"
        exit 0
    fi
fi

# ── 备份 .env ──
if [[ -f .env ]]; then
    cp .env ".env.backup.$(date +%Y%m%d_%H%M%S)"
    ok "已备份 .env"
fi

# ── 拉取代码 ──
log "正在拉取最新代码..."
git stash --include-untracked -m "upgrade-$(date +%s)" 2>/dev/null && STASHED=true || STASHED=false

if ! git pull --rebase origin main; then
    err "git pull 失败"
    if [[ "$STASHED" == "true" ]]; then
        git stash pop 2>/dev/null || true
    fi
    exit 1
fi
ok "代码已更新"

# ── 安装依赖 (如果变化) ──
if git diff HEAD@{1} --name-only 2>/dev/null | grep -q "requirements.txt"; then
    log "检测到依赖变化，正在安装..."
    pip install -r requirements.txt --quiet 2>/dev/null || pip3 install -r requirements.txt --quiet
    ok "依赖安装完成"
fi

# ── 数据迁移 ──
log "正在检查数据迁移..."
python -m lobster.migrations 2>/dev/null || python3 -m lobster.migrations 2>/dev/null || warn "迁移模块跳过"
ok "数据迁移检查完成"

# ── 恢复本地修改 ──
if [[ "$STASHED" == "true" ]]; then
    git stash pop 2>/dev/null || warn "恢复本地修改失败，可用 git stash list 查看"
fi

# ── 读取新版本 ──
NEW_VER="unknown"
if [[ -f lobster/__init__.py ]]; then
    NEW_VER=$(grep '__version__' lobster/__init__.py | head -1 | sed "s/.*= *['\"]//;s/['\"].*//" )
fi

# ── 重启服务 ──
log "正在重启服务..."
case "$DEPLOY_TYPE" in
    docker)
        docker compose build --quiet && docker compose up -d
        ok "Docker 容器已重启"
        ;;
    systemd)
        sudo systemctl restart lingque
        ok "systemd 服务已重启"
        ;;
    source)
        warn "开发模式，请手动重启服务"
        ;;
esac

echo ""
ok "升级完成! v${CURRENT_VER} → v${NEW_VER}"
echo ""
