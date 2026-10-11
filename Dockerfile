# =============================================================
# Emby Manager - Docker 镜像
# 构建: docker build -t emby-manager:latest .
# 运行: 推荐使用 docker compose（见 docker-compose.yml）
# =============================================================
FROM python:3.12-slim

# 运行时环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

# 安装时区数据（依赖均为纯 Python / 有预编译 wheel，无需编译工具链）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先单独安装依赖，充分利用 Docker 层缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝应用代码（.dockerignore 会排除数据库、日志、用户上传等数据）
COPY . .

# 创建运行时与持久化目录，并使用非 root 用户运行
RUN mkdir -p /app/database /app/logs /app/cache /app/sessions \
        /app/static/downloads/clients \
        /app/static/uploads/images \
        /app/static/images/payment_qrcodes/alipay \
        /app/static/images/payment_qrcodes/wechat \
        /app/static/images/refund_qrcodes \
    && useradd -u 1000 -r -m -d /home/app app \
    && chown -R app:app /app

USER app

# 持久化挂载点（compose 中以数据卷挂载，升级镜像不丢数据）
VOLUME ["/app/database", "/app/logs", "/app/cache", "/app/static/downloads/clients", "/app/static/images", "/app/static/uploads"]

EXPOSE 5001

# 健康检查：登录页可访问即视为服务正常（使用镜像自带 python，无需额外安装 curl）
HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5001/login', timeout=8).status == 200 else 1)"

# 容器内使用 gunicorn；数据库初始化与定时任务在 master 进程只执行一次
CMD ["gunicorn", "-c", "docker/gunicorn.conf.py", "app:app"]
