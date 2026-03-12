#!/bin/bash
# 🐦 灵雀 LingQue 一键部署脚本
# 适用于 Ubuntu 22.04+ / Debian 12+
set -e

echo "🐦 灵雀 LingQue 部署脚本"
echo "========================="

# 检测部署方式
if command -v docker &> /dev/null; then
    echo ""
    echo "检测到 Docker, 推荐使用 Docker 部署"
    echo "  1) Docker 部署 (推荐)"
    echo "  2) 直接部署 (systemd)"
    read -p "选择 [1/2]: " choice
else
    choice=2
fi

if [ "$choice" = "1" ]; then
    echo ""
    echo "📦 Docker 部署..."

    # 检查 .env
    if [ ! -f .env ]; then
        echo "⚠️  未找到 .env 文件, 从模板创建..."
        cp .env.example .env
        echo "请编辑 .env 填入你的配置, 然后重新运行此脚本"
        exit 0
    fi

    docker compose up -d --build
    echo ""
    echo "✅ 灵雀已启动!"
    echo "   查看日志: docker compose logs -f"
    echo "   停止服务: docker compose down"
    echo "   健康检查: curl http://localhost:9000/health"

else
    echo ""
    echo "🔧 直接部署 (systemd)..."

    # 安装 Python 依赖
    echo "安装 Python 依赖..."
    pip3 install -r requirements.txt --break-system-packages 2>/dev/null || pip3 install -r requirements.txt

    # 检查 .env
    if [ ! -f .env ]; then
        cp .env.example .env
        echo "⚠️  请编辑 .env 填入配置后重新运行"
        exit 0
    fi

    # 创建专用用户 (可选)
    if ! id "lingque" &>/dev/null; then
        echo "创建 lingque 用户..."
        sudo useradd -r -m -s /bin/bash lingque
        sudo cp -r . /home/lingque/lingque
        sudo chown -R lingque:lingque /home/lingque/lingque
    fi

    # 安装 systemd 服务
    echo "安装 systemd 服务..."
    DEPLOY_PATH=$(pwd)
    sudo sed "s|/home/lingque/lingque|${DEPLOY_PATH}|g" deploy/lingque.service | \
        sudo sed "s|User=lingque|User=$(whoami)|g" | \
        sudo sed "s|Group=lingque|Group=$(whoami)|g" | \
        sudo tee /etc/systemd/system/lingque.service > /dev/null

    sudo systemctl daemon-reload
    sudo systemctl enable lingque
    sudo systemctl start lingque

    echo ""
    echo "✅ 灵雀已启动!"
    echo "   查看状态: sudo systemctl status lingque"
    echo "   查看日志: sudo journalctl -u lingque -f"
    echo "   重启服务: sudo systemctl restart lingque"
fi
