<img width="300" height="100" alt="logo" src="https://github.com/user-attachments/assets/ba43b72a-e152-4e90-a2f7-4981f88008f7" />

# Emby Manager · Emby 用户管理系统

![Python](https://img.shields.io/badge/Python-3.10%20~%203.12-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-2.3%2B-000000?logo=flask)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)
![Database](https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

一个基于 **Flask + SQLite** 的 Emby 用户运营管理系统，粉色少女心风格 UI，开箱即用。
支持激活码注册、套餐续期、Emby 策略自动巡查、观看记录同步、**看剧日历**、金币商城、在线工单、客户端分发等完整运营能力，并提供 **Docker / Docker Compose 一键部署**。

---

## 目录

- [功能特性](#功能特性)
- [在线预览结构](#页面结构)
- [快速开始（Docker 部署）](#快速开始docker-部署)
- [其他 Docker 安装方式](#其他-docker-安装方式)
- [传统方式部署](#传统方式部署)
- [数据持久化与备份](#数据持久化与备份)
- [已有数据迁移到 Docker](#已有数据迁移到-docker)
- [环境变量说明](#环境变量说明)
- [默认账号](#默认账号)
- [目录结构](#目录结构)
- [技术栈](#技术栈)
- [主要接口一览](#主要接口一览)
- [更新日志](#更新日志)
- [安全建议](#安全建议)
- [许可证](#许可证)

---

## 功能特性

### 👤 用户与账号体系
- 管理员 / 普通用户双角色，后台用户列表支持搜索、筛选、批量管理
- **激活码注册**：支持小时卡 / 天卡 / 周卡 / 月卡 / 年卡 / 永久等多种时长
- 用户续期、立即到期、手动禁用/启用、到期自动处理
- 用户详情弹窗展示真实的注册时间、到期时间、最后登录、最近观看记录与观看统计图表
- 邮箱验证码注册、邮箱找回密码
- 登录失败次数限制、修改密码频率与 IP 限制

### 🎬 Emby 集成
- 多 Emby 媒体库绑定（服务器地址 + API 密钥），编辑态一键"测试连接"
- 用户媒体库访问权限管理
- 定时**策略巡查**：到期/禁用用户自动收回 Emby 访问权限，白名单用户豁免
- 每日零点自动同步用户观看记录（与服务器时区保持一致）
- WebDAV 用户自动分配

### 📅 看剧日历（仪表盘）
- 近 12 个月的看剧日程总览，支持箭头与月份选择器切换年月、一键回到今天
- 涂鸦风格标识：**有看剧（跳动爱心+条数徽标）/ 今天（金色星星描边）/ 已过去（淡空心心）/ 未来日期（无标识）**
- 点击日期弹出当天**海报时间线**：影片 Primary 海报封面（非背景图）+ 名称 + 上映年份
- 点击海报查看详情：剧集显示系列简介与当天观看的每一集（集名、观看时间、本集简介）；电影显示影片简介
- **仅激活有效期内用户可查看**：套餐过期后日历卡片自动锁定并引导前往续期，接口同步返回 403

### 💰 运营与支付
- 金币充值、套餐商城、礼品卡
- 支付订单提交与管理员审核、批量处理
- 退款申请与退款收款码管理

### 🎫 工单与文档
- 用户在线工单（账号 / 技术 / 其他分类），支持粘贴与上传截图、消息已读状态
- 管理员工单回复与处理
- 内置客户端使用文档、服务条款、隐私政策页面

### 📦 客户端分发
- 管理与分发 Android / iOS / Windows / macOS / Linux 客户端
- 支持 APK / IPA / EXE / DMG / MSIX / DEB 等格式，下载次数统计
- 客户端下载页同样仅对有效期内用户开放

### 🛡 系统与运维
- Docker / Docker Compose 一键部署，内置健康检查，数据卷持久化
- Gunicorn（Linux 容器）/ Waitress（Windows）多线程生产服务器
- 文件上传魔数 + 扩展名白名单、SQLAlchemy 参数化查询、模板自动转义
- 响应压缩、接口限流、完整操作日志

---

## 页面结构

| 角色 | 主要页面 |
| --- | --- |
| 游客 | 首页、登录、注册（激活码+邮箱验证）、忘记密码、服务条款、隐私政策 |
| 普通用户 | 仪表盘（统计卡片、观看趋势、**看剧日历**、最近观看）、观看历史、媒体库、金币充值、套餐商城、客户端下载、使用文档、我的工单、个人信息 |
| 管理员 | 运营仪表盘、用户管理、激活码管理、绑定媒体库、支付审核、客户端管理、工单管理、系统设置、个人信息 |

<img width="2124" height="1125" alt="image" src="https://github.com/user-attachments/assets/e33fa3ed-cfd6-4b7a-8e60-00e417552d9a" />

<img width="2117" height="1060" alt="image" src="https://github.com/user-attachments/assets/d0b51d4e-a599-4041-b8d2-2cc488d64661" />

<img width="2064" height="1083" alt="image" src="https://github.com/user-attachments/assets/d3be5c75-ab12-48d9-b389-6fcbeccc06d1" />

<img width="2115" height="1152" alt="image" src="https://github.com/user-attachments/assets/7947b297-8398-49ce-8b97-755f18a47083" />



---

## 快速开始（Docker 部署）

适用于任意安装了 **Docker Engine 20.10+ / Docker Desktop（含 Compose v2）** 的 Linux、Windows、macOS。

### 1. 获取项目

```bash
git clone <你的仓库地址> emby-manager
cd emby-manager
```

### 2. 准备环境变量（可选但推荐）

```bash
# Linux / macOS
cp .env.example .env

# Windows PowerShell
copy .env.example .env
```

编辑 `.env`，建议至少设置一个固定的 `EMBY_MANAGER_SECRET_KEY`（容器重建后登录态不会失效）：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 3. 一条命令启动

```bash
docker compose up -d --build
```

首次启动会自动：构建镜像 → 初始化 SQLite 数据库与数据表 → 创建默认管理员 → 启动定时任务。

### 4. 访问

- 首页：`http://<服务器IP>:5001/`
- 登录：`http://<服务器IP>:5001/login`
- 默认管理员：**admin / 123**（首次登录后请立即修改密码）

查看运行状态与日志：

```bash
docker compose ps          # 健康状态（healthy）
docker compose logs -f     # 实时日志
```

---

## 其他 Docker 安装方式

### 方式 A：纯 `docker` 命令（没有 Compose 插件时）

项目内置等价脚本，自动完成构建、建卷、删除旧容器、启动：

```bash
# Linux / macOS
chmod +x docker/run-docker.sh
./docker/run-docker.sh

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File .\docker\run-docker.ps1
```

自定义对外端口：

```bash
APP_PORT=5050 ./docker/run-docker.sh        # Linux/macOS
$env:APP_PORT="5050"; .\docker\run-docker.ps1   # Windows
```

### 方式 B：手动逐条执行 docker 命令

```bash
# 构建镜像
docker build -t emby-manager:latest .

# 创建数据卷
docker volume create emby-database
docker volume create emby-logs
docker volume create emby-cache
docker volume create emby-clients
docker volume create emby-images
docker volume create emby-uploads

# 启动容器
docker run -d \
  --name emby-manager \
  --restart unless-stopped \
  -p 5001:5001 \
  -e TZ=Asia/Shanghai \
  -v emby-database:/app/database \
  -v emby-logs:/app/logs \
  -v emby-cache:/app/cache \
  -v emby-clients:/app/static/downloads/clients \
  -v emby-images:/app/static/images \
  -v emby-uploads:/app/static/uploads \
  emby-manager:latest
```

### 方式 C：docker-compose 旧版（v1，命令带横杠）

```bash
docker-compose up -d --build
```

---

## 传统方式部署

### 环境要求

- Python 3.10 ~ 3.12
- Windows / Linux / macOS 均可

### 启动步骤

```bash
pip install -r requirements.txt

# Windows（Waitress，推荐）
python start_production.py --waitress

# Linux / macOS（Gunicorn，推荐）
python start_production.py --gunicorn

# 开发调试
python app.py
```

> 首次运行会自动创建 `database/emby_manager.db` 与默认管理员。
> 更详细的 Nginx 反向代理、高并发调优说明见 [DEPLOYMENT.md](DEPLOYMENT.md)。

---

## 数据持久化与备份

Docker 方式下，所有业务数据均保存在**数据卷**中，升级/重建镜像不会丢失：

| 数据卷 | 容器内路径 | 内容 |
| --- | --- | --- |
| `emby-database` | `/app/database` | SQLite 数据库（用户、订单、配置、观看记录等全部业务数据） |
| `emby-logs` | `/app/logs` | 运行日志 |
| `emby-cache` | `/app/cache` | 文件缓存 |
| `emby-clients` | `/app/static/downloads/clients` | 管理员上传的客户端安装包 |
| `emby-images` | `/app/static/images` | 收款码、退款码等图片 |
| `emby-uploads` | `/app/static/uploads` | 工单等场景的用户上传图片 |

### 备份数据库

```bash
# 方法一：docker cp（简单直接）
docker cp emby-manager:/app/database/emby_manager.db ./emby_manager.backup.db

# 方法二：通过临时容器打包整个数据卷
docker run --rm -v emby-database:/data -v "$PWD":/backup alpine \
  tar czf /backup/emby-database-backup.tar.gz -C /data .
```

恢复时反向拷回 `/app/database/` 并重启容器即可。建议在业务低峰期操作。

### 升级版本

```bash
git pull
docker compose up -d --build      # 数据卷保留，仅替换程序
```

---

## 已有数据迁移到 Docker

如果此前是传统方式部署（如 Windows 上的 `start_production.py`），希望迁入容器：

1. 先用 compose 启动一次新容器（自动建好数据卷），然后停止：

```bash
docker compose up -d
docker compose stop
```

2. 将旧数据拷入容器对应路径：

```bash
# 数据库（最重要）
docker cp database/emby_manager.db emby-manager:/app/database/

# 可选：客户端安装包、收款码、上传图片
docker cp static/downloads/clients/. emby-manager:/app/static/downloads/clients/
docker cp static/images/. emby-manager:/app/static/images/
docker cp static/uploads/. emby-manager:/app/static/uploads/
```

3. 重新启动：

```bash
docker compose up -d
docker compose logs -f
```

> ⚠️ 镜像本身**不包含**任何业务数据库（`.dockerignore` 已排除 `*.db` 与用户上传内容），请勿将含真实用户信息和 Emby API 密钥的数据库提交到公开 Git 仓库。

---

## 环境变量说明

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_PORT` | `5001` | 仅 compose 使用，宿主机暴露端口 |
| `TZ` | `Asia/Shanghai` | 容器时区；每日零点同步依赖此项，请勿随意修改 |
| `EMBY_MANAGER_SECRET_KEY` | 空（每次重启随机） | Flask 会话密钥，**建议固定设置**，避免容器重建后全体掉线 |
| `GUNICORN_WORKERS` | `2` | Gunicorn worker 进程数 |
| `GUNICORN_THREADS` | `8` | 每 worker 线程数 |
| `GUNICORN_TIMEOUT` | `120` | 请求超时（秒） |
| `GUNICORN_LOG_LEVEL` | `info` | Gunicorn 日志级别 |

> SQLite 单机部署建议保持默认并发（2 × 8）。若上调 worker 数，请确认磁盘 IO 与 CPU 充足。

---

## 默认账号

系统首次初始化时自动创建：

- 用户名：`admin`
- 密码：`123`

**首次登录后请立即在"个人信息"中修改密码。**

---

## 目录结构

```
emby-manager/
├── app.py                     # Flask 主应用（路由、模型、定时任务、Emby API）
├── start_production.py        # 传统部署启动脚本（Waitress/Gunicorn）
├── gunicorn.conf.py           # 传统部署 Gunicorn 配置
├── production_config.py       # 生产环境配置
├── requirements.txt           # Python 依赖
│
├── Dockerfile                 # Docker 镜像构建文件
├── docker-compose.yml         # Docker Compose 编排（推荐）
├── .dockerignore
├── .env.example               # 环境变量模板
├── docker/
│   ├── gunicorn.conf.py       # 容器专用 Gunicorn 配置（env 可调、stdout 日志）
│   ├── run-docker.sh          # 纯 docker 部署脚本（Linux/macOS）
│   └── run-docker.ps1         # 纯 docker 部署脚本（Windows）
│
├── database/                  # SQLite 数据库目录（运行时自动生成）
├── templates/                 # Jinja2 模板（admin / user / 公共页面）
├── static/                    # 样式、脚本、图片、上传与客户端下载目录
├── logs/  cache/  sessions/   # 运行时目录
├── README.md
└── DEPLOYMENT.md              # 传统部署 / Nginx / 高并发详细文档
```

---

## 技术栈

- **后端**：Flask、SQLAlchemy、SQLite、Requests / aiohttp
- **前端**：Jinja2 + 原生 HTML/CSS/JavaScript、Chart.js、Font Awesome
- **生产服务器**：Gunicorn（Linux / Docker）、Waitress（Windows）
- **容器化**：Docker、Docker Compose（python:3.12-slim 基础镜像、非 root 运行、健康检查）
- **反向代理**：Nginx（可选，见 `nginx.conf`）

---

## 主要接口一览

**认证**：`POST /api/login`、`POST /api/logout`、`POST /api/register`、`POST /api/change-password`

**普通用户**：

- `GET /api/user/profile` 个人信息
- `GET /api/user/history` 观看历史
- `GET /api/user/watch-calendar` 看剧日历汇总（**仅有效期内可用**）
- `GET /api/user/watch-calendar/day?date=YYYY-MM-DD` 某日海报时间线与剧集详情（**仅有效期内可用**）
- `POST /api/user/renew` 激活码续期，以及金币充值、商城、工单、客户端等接口

**管理员**：`/api/admin/users`、`/api/admin/codes`、`/api/admin/emby/*`、`/api/admin/payment-orders`、`/api/admin/client-downloads`、`/api/admin/tickets` 等。

---

## 更新日志

### v3.1.0（2026-10）

- 🐳 新增 **Docker / Docker Compose 部署**支持：Dockerfile、compose 编排、环境变量模板、健康检查、非 root 运行、数据卷持久化与一键部署脚本（Linux/macOS/Windows）
- 📅 仪表盘新增**看剧日历**卡片：近 12 个月看剧日程、涂鸦日期标识、当日海报时间线、剧集/电影简介
- 🔒 看剧日历增加**有效期校验**：过期用户日历锁定并引导续期，接口返回 403
- 🔧 修复编辑媒体库时不修改内容直接"测试连接"失败的问题（`***` 掩码密钥自动回退为已存储密钥）
- 📄 服务条款 / 隐私政策页底部新增"返回注册"按钮
- 👤 修复管理员用户详情弹窗信息空白/固定不变、观看统计图表为假数据的问题，改为真实用户数据与真实观看分类统计
- 🔑 会话密钥支持通过环境变量 `EMBY_MANAGER_SECRET_KEY` 固定

### v2.0.0

- 支付审核与退款、金币商城、礼品卡
- 客户端下载管理、工单系统、观看记录每日同步
- 邮箱验证 / 找回密码、Emby 策略巡查与白名单、WebDAV 分配
- 生产环境并发优化

### v1.0.0

- 基础用户管理、激活码体系、Emby 服务器绑定

---

## 安全建议

1. 部署后立即修改默认管理员密码
2. 在 `.env` 中设置高强度的 `EMBY_MANAGER_SECRET_KEY`
3. 不要将 `*.db`、`.env`、日志与上传文件提交到公开仓库（已提供 `.gitignore` / `.dockerignore`）
4. 公网暴露建议前置 Nginx 并启用 HTTPS（参考 `nginx.conf` 与 [DEPLOYMENT.md](DEPLOYMENT.md)）
5. 定期通过数据卷备份数据库

---

## 许可证

本项目采用 MIT 许可证。
