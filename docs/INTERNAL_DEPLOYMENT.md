# ADK Middleware 内网部署指南

## 概述

这是 ADK Middleware 的内网部署版本，专为内网环境优化，包含 Traefik 反向代理和 GitLab CI/CD 集成。

## 快速开始

### 1. 配置环境变量

```bash
cp config/internal.example.env .env
# 编辑 .env 文件，配置内网 ADK 后端地址等
```

### 2. 启动服务

```bash
# 使用 Traefik 启动完整服务
docker-compose -f docker-compose.internal.yml up -d

# 查看服务状态
docker-compose -f docker-compose.internal.yml ps

# 查看日志
docker-compose -f docker-compose.internal.yml logs -f
```

### 3. 访问服务

- API 服务: http://adk-api.internal.local/v1
- Traefik Dashboard: http://localhost:8080/dashboard/
- Prometheus: http://localhost:9090 (需启用 --profile with-monitoring)
- Grafana: http://localhost:3000 (需启用 --profile with-monitoring)

## 架构

```
                    ┌─────────────────┐
                    │   Traefik       │
                    │  (反向代理)      │
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

| 变量名 | 说明 | 示例值 |
|--------|------|--------|
| `ADK_BACKEND_URL` | 内网 ADK 后端地址 | `http://adk-backend.internal.local:8080` |
| `STAGING_ADK_BACKEND_URL` | Staging 环境 ADK 地址 | `http://adk-backend-staging:8080` |
| `PROD_ADK_BACKEND_URL` | 生产环境 ADK 地址 | `http://adk-backend-prod:8080` |
| `API_KEY` | API 认证密钥 | `sk-internal:xxxxx` |
| `CI_REGISTRY_USER` | Registry 用户名 | `gitlab-ci-token` |
| `CI_REGISTRY_PASSWORD` | Registry 密码 | `${CI_JOB_TOKEN}` |

### 部署流程

1. **Lint**: 代码检查 (ruff, mypy, hadolint)
2. **Build**: 构建 Docker 镜像并推送到内网 Registry
3. **Test**: 单元测试和集成测试
4. **Deploy**: 部署到 Staging/Production

## Traefik 配置

### 动态配置

Traefik 动态配置位于 `traefik/dynamic.yml`，包含：

- 路由规则
- 中间件（认证、限流等）
- 负载均衡配置
- 健康检查

### 修改域名

编辑 `.env` 文件中的 `INTERNAL_DOMAIN` 变量：

```bash
INTERNAL_DOMAIN=your-api.internal.local
```

## 监控

### Prometheus Metrics

访问 `/v1/metrics` 获取 Prometheus 格式的指标：

- `http_request_duration_seconds` - 请求耗时
- `http_requests_total` - 请求总数
- `active_requests` - 当前活跃请求数

### Grafana Dashboard

启用监控后，导入 dashboard：

```bash
docker-compose -f docker-compose.internal.yml --profile with-monitoring up -d
```

## 故障排查

### 查看日志

```bash
# 所有服务
docker-compose -f docker-compose.internal.yml logs

# 特定服务
docker-compose -f docker-compose.internal.yml logs adk-middleware
docker-compose -f docker-compose.internal.yml logs traefik
```

### 健康检查

```bash
# API 健康检查
curl http://localhost:8000/v1/health

# Traefik health check
curl http://localhost:8080/ping
```

### 重启服务

```bash
# 重启单个服务
docker-compose -f docker-compose.internal.yml restart adk-middleware

# 重建服务
docker-compose -f docker-compose.internal.yml up -d --build adk-middleware
```

## 与外网版本的区别

| 功能 | 外网版本 | 内网版本 |
|------|----------|----------|
| ADK 后端 | 外网地址 | 内网地址 |
| 反向代理 | 无 | Traefik |
| CI/CD | - | GitLab CI |
| 监控 | 基础 | Prometheus + Grafana |
| 部署方式 | Docker Compose | Docker Compose + Swarm |

## 更新记录

所有提交需包含 JIRA 编号：`##446992`

```bash
git commit -m "feat: 添加内网部署支持 ##446992"
```

## 联系方式

- 作者: fengguohao
- 邮箱: fengguohao@quicktron.com
- GitLab: ssh://git@gitlab.flashhold.com:10022/drl/mcp/adk_to_openai_api.git
