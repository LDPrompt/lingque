FROM python:3.12-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git && \
    rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 项目代码
COPY lobster/ lobster/

# 数据目录 (挂载点)
RUN mkdir -p /data/memory /data/workspaces

# 环境变量默认值
ENV MEMORY_DIR=/data/memory
ENV WORKSPACE_DIR=/data/workspaces
ENV CHANNELS=feishu
ENV LOG_LEVEL=INFO

EXPOSE 9000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1

CMD ["python", "-m", "lobster.main"]
