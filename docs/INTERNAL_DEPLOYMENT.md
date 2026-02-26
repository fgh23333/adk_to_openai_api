# ADK Middleware 内网部署指南

## 概述

这是 ADK Middleware 的内网部署版本，专为内网环境优化，包含 Traefik 反向代理和 GitLab CI/CD 集成。

## 快速开始

### 1. 配置环境变量

创建 `.env` 文件（可选，用于本地测试）：

```bash
cp config/internal.example.env .env
# 编辑 .env 文件，配置内网 ADK 后端地址等
```

### 2. 确保 Traefik 网络存在

```bash
docker network create web_gateway
```

### 3. 本地测试启动

```bash
export CONTAINER_NAME=adk-middleware-test
export HOST_PORT=8000
export HOST_DATA_PATH=./data
export URL_PREFIX=/adk
export PROJECT_NAME=adk-middleware
export ADK_BACKEND_URL=http://your-adk-backend:8080

docker compose -f docker-compose.internal.yml up -d --build
```

### 4. 访问服务

- API 服务: http://your-server:9500/adk/v1/chat/completions
- Swagger 文档: http://your-server:9500/adk/docs
- 健康检查: http://your-server:9500/adk/v1/health

## 架构

```
                    ┌─────────────────┐
                    │   Traefik       │
                    │  (反向代理)      │
                    │   web_gateway   │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  ADK Middleware │
                    │     :8000       │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  ADK Backend    │
                    │  (内网服务)      │
                    └─────────────────┘
```

## GitLab CI/CD

### 配置 GitLab Variables

在 GitLab 项目设置中配置以下变量：

| 变量名 | 说明 | 示例值 | 是否必填 |
|--------|------|--------|----------|
| `SSH_HOST` | 部署目标服务器 IP | `192.168.1.100` | ✅ |
| `SSH_USER` | SSH 登录用户名 | `root` | ✅ |
| `SSH_PASSWORD` | SSH 登录密码 | `your-password` | ✅ |
| `API_KEY` | API 认证密钥 | `sk-internal:xxxxx` | ❌ |
| `ADK_BACKEND_URL` | 内网 ADK 后端地址 | `http://adk-backend:8080` | ❌ |
| `ADK_APP_NAME` | ADK 应用名称 | `default_agent` | ❌ |

### 部署流程

推送代码后，GitLab CI 会自动：

1. **预览环境** (`internal-deploy` 分支): 自动部署到测试环境
2. **生产环境** (`master` 分支): 需要手动触发部署

### 环境配置

| 环境 | 分支 | 端口 | 路由前缀 | 容器名 |
|------|------|------|----------|--------|
| 预览 | `internal-deploy` | 9501 | `/adk-dev` | `adk-middleware-dev` |
| 生产 | `master` | 9500 | `/adk` | `adk-middleware-prod` |

### 修改配置

编辑 `.gitlab-ci.yml` 中的以下变量：

```yaml
deploy_preview:
  variables:
    DEPLOY_PORT: "9501"           # 修改预览环境端口
    URL_PREFIX: "/adk-dev"        # 修改预览环境路由前缀
    ADK_BACKEND_URL: "http://your-adk-dev:8080"  # 修改预览环境 ADK 地址

deploy_production:
  variables:
    DEPLOY_PORT: "9500"           # 修改生产环境端口
    URL_PREFIX: "/adk"            # 修改生产环境路由前缀
    ADK_BACKEND_URL: "http://your-adk-prod:8080"  # 修改生产环境 ADK 地址
```

## Traefik 集成

### 路由配置

服务通过 Docker Compose labels 自动注册到 Traefik：

- 主路由: `PathPrefix($URL_PREFIX)` → 去除前缀 → 转发到容器:8000
- Metrics 路由: `PathPrefix(/v1/metrics)` → 直接转发

### 中间件

- **Strip Prefix**: 去除 URL 前缀
- **CORS**: 跨域支持
- **Compress**: 响应压缩
- **Health Check**: 健康检查 (`/v1/health`)

## 故障排查

### 查看 Docker 日志

```bash
# 查看容器日志
docker logs adk-middleware-prod

# 查看实时日志
docker logs -f adk-middleware-prod
```

### 进入容器调试

```bash
# 进入容器 shell
docker exec -it adk-middleware-prod sh

# 测试健康检查
docker exec adk-middleware-prod curl http://localhost:8000/v1/health

# 查看环境变量
docker exec adk-middleware-prod env | grep ADK
```

### 重新部署

```bash
# 在服务器上手动重新部署
cd /home/projects/adk_middleware_prod
git pull
docker compose -f docker-compose.internal.yml -p adk-middleware-prod up -d --build --force-recreate
```

### 查看 Traefik 状态

```bash
# 查看 Traefik 日志
docker logs traefik

# 查看 Traefik 路由配置
curl http://traefik-server:8080/http/routers
```

## API 使用示例

### 通过 Traefik 代理访问

```bash
# Chat Completion
curl -X POST http://your-server:9500/adk/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-internal:xxxxx" \
  -d '{
    "model": "agent",
    "messages": [{"role": "user", "content": "你好"}]
  }'

# 健康检查
curl http://your-server:9500/adk/v1/health
```

### 直连端口访问（兜底方案）

```bash
# 直接访问容器端口
curl -X POST http://your-server:9500/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "agent", "messages": [{"role": "user", "content": "你好"}]}'
```

## 数据持久化

数据存储在配置的数据目录中：

- **预览环境**: `/home/projects/adk_middleware_data/dev`
- **生产环境**: `/home/projects/adk_middleware_data/prod`

包含内容：
- SQLite 数据库: `sessions.db`
- 其他缓存数据

## 监控

访问 `/v1/metrics` 获取 Prometheus 格式的指标：

- `http_request_duration_seconds` - 请求耗时
- `http_requests_total` - 请求总数
- `active_requests` - 当前活跃请求数

## 与外网版本的区别

| 功能 | 外网版本 | 内网版本 |
|------|----------|----------|
| ADK 后端 | 外网地址 | 内网地址 |
| 反向代理 | 无 | Traefik (web_gateway) |
| CI/CD | - | GitLab CI (SSH 部署) |
| 部署方式 | Docker Compose | Docker Compose + SSH |
| 路由前缀 | 无 | `/adk` 或 `/adk-dev` |

## 更新记录

所有提交需包含 JIRA 编号：`##446992`

```bash
git commit -m "feat: 添加内网部署支持 ##446992"
```

## 联系方式

- 作者: fengguohao
- 邮箱: fengguohao@quicktron.com
- GitLab: ssh://git@gitlab.flashhold.com:10022/drl/mcp/adk_to_openai_api.git
