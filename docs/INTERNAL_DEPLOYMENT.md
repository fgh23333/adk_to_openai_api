# ADK Middleware 内网部署指南

## 概述

ADK Middleware 内网部署版本，集成 Traefik 反向代理和 GitLab CI/CD。

## 快速开始

### 1. 确保 Traefik 网络存在

```bash
docker network create web_gateway
```

### 2. 本地测试

```bash
export CONTAINER_NAME=adk-middleware-test
export HOST_PORT=8000
export HOST_DATA_PATH=./data
export URL_PREFIX=/adk
export ADK_BACKEND_URL=http://your-adk-backend:8080

docker compose up -d --build
```

### 3. 访问服务

- API: http://your-server:9500/adk/v1/chat/completions
- Swagger: http://your-server:9500/adk/docs
- 健康检查: http://your-server:9500/adk/v1/health

## GitLab CI/CD

### 配置 GitLab Variables

| 变量名 | 说明 | 示例 |
|--------|------|------|
| `SSH_HOST` | 部署服务器 IP | `192.168.1.100` |
| `SSH_USER` | SSH 用户名 | `root` |
| `SSH_PASSWORD` | SSH 密码 | - |
| `ADK_BACKEND_URL` | ADK 后端地址 | `http://adk-backend:8080` |

### 环境配置

| 环境 | 分支 | 端口 | 路由前缀 |
|------|------|------|----------|
| 预览 | `internal-deploy` | 9601 | `/adk-dev` |
| 生产 | `master` | 9600 | `/adk` |

### 修改配置

编辑 `.gitlab-ci.yml` 中的环境变量。

## 故障排查

```bash
# 查看日志
docker logs adk-middleware-prod

# 健康检查
docker exec adk-middleware-prod curl http://localhost:8000/v1/health

# 手动重新部署
cd /home/projects/adk_middleware_prod
git pull
docker compose -p adk-middleware-prod up -d --build --force-recreate
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CONTAINER_NAME` | adk-middleware | 容器名 |
| `HOST_PORT` | 8000 | 主机端口 |
| `HOST_DATA_PATH` | ./data | 数据目录 |
| `URL_PREFIX` | /adk | 路由前缀 |
| `ADK_BACKEND_URL` | - | ADK 后端地址 |
| `ADK_APP_NAME` | agent | ADK 应用名 |
| `ENABLE_API_KEY_AUTH` | false | 启用 API Key |
| `API_KEY` | - | API 密钥 |
| `LOG_LEVEL` | INFO | 日志级别 |
| `MAX_FILE_SIZE_MB` | 20 | 最大文件大小 |

## 联系方式

- 作者: fengguohao
- 邮箱: fengguohao@quicktron.com
