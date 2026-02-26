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
| `ADK_BACKEND_URL` | 默认 ADK 后端地址 | `http://adk-backend:8080` |
| `ADK_BACKEND_MAPPING` | 多应用映射表 | 见下方说明 |

### 多应用后端配置

当有多个 ADK 应用需要转发时，配置 `ADK_BACKEND_MAPPING` 环境变量：

#### 配置格式

**方式一：简单格式**
```bash
ADK_BACKEND_MAPPING=app1:http://backend1:8080,app2:http://backend2:8080
```

**方式二：JSON 格式**
```bash
ADK_BACKEND_MAPPING='{"app1":"http://backend1:8080","app2":"http://backend2:8080"}'
```

#### Docker Compose 配置示例

```yaml
services:
  adk-middleware:
    environment:
      # 多个后端映射
      - ADK_BACKEND_MAPPING=app1:http://adk-1.internal.local:8080,app2:http://adk-2.internal.local:8080,app3:http://adk-3.internal.local:8080
      # 或者使用 JSON 格式
      - ADK_BACKEND_MAPPING={"app1":"http://adk-1.internal.local:8080","app2":"http://adk-2.internal.local:8080"}
```

#### GitLab CI 配置示例

在 GitLab 项目设置 → CI/CD → Variables 中添加：

| 变量名 | 值 | 类型 |
|--------|---|------|
| `ADK_BACKEND_MAPPING` | `app1:http://adk-1:8080,app2:http://adk-2:8080` | 变量 |

或者在 `.gitlab-ci.yml` 中直接配置：

```yaml
deploy_production:
  variables:
    ADK_BACKEND_MAPPING: "app1:http://adk-prod-1:8080,app2:http://adk-prod-2:8080"
```

#### 配置示例

假设有 3 个 ADK 后端：

| 应用名 | 后端地址 | Agent |
|--------|----------|-------|
| `customer-service` | `http://adk-cs.internal.local:8080` | `chat_agent` |
| `sales-assistant` | `http://adk-sales.internal.local:8080` | `chat_agent` |
| `tech-support` | `http://adk-tech.internal.local:8080` | `faq_agent` |

配置：
```bash
ADK_BACKEND_MAPPING=customer-service:http://adk-cs.internal.local:8080,sales-assistant:http://adk-sales.internal.local:8080,tech-support:http://adk-tech.internal.local:8080
```

#### Model 格式说明

由于不同应用下可能有同名 agent，使用 `app_name/agent_name` 格式来区分：

| 格式 | 说明 | 示例 |
|------|------|------|
| `app_name/agent_name` | 完整格式，推荐使用 | `app1/chat_agent`, `app2/chat_agent` |
| `agent_name` | 仅 agent 名，使用默认应用 | `chat_agent` (使用 ADK_APP_NAME) |

**API 请求示例**：
```bash
# 调用 app1 的 chat_agent
curl -X POST http://your-server:9600/adk/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "app1/chat_agent",
    "messages": [{"role": "user", "content": "你好"}]
  }'

# 调用 app2 的 chat_agent（不同应用下的同名 agent）
curl -X POST http://your-server:9600/adk/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "app2/chat_agent",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

**响应中的 model 字段**：
响应会保持请求中的完整 model 格式：
```json
{
  "id": "chatcmpl-123",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "app1/chat_agent",
  "choices": [...]
}
```
| `ADK_BACKEND_MAPPING` | 多应用映射表 | 见下方说明 |

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
