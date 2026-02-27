# ADK to OpenAI API Middleware

将 Google Agent Development Kit (ADK) 的 REST API 转换为 OpenAI 兼容格式的高性能 Python 中间件，支持接入 Dify、ChatBox、LangChain 等 LLM 平台。

## 功能特性

### 核心功能

- **OpenAI API 兼容**: 完全兼容 OpenAI Chat Completions API 规范
- **真正流式响应**: 使用 ADK SSE 端点实现实时流式输出
- **多应用支持**: 支持多个 ADK 应用，格式为 `app_name/agent_name`
- **动态 API Key**: 运行时添加/删除 API Key 无需重启
- **多用户隔离**: 基于 API Key 的用户会话隔离
- **会话记录**: SQLite 存储对话历史，支持查询和导出
- **监控分析**: Prometheus 兼容指标，请求统计和分析
- **请求追踪**: 每个请求分配唯一 ID，便于调试和日志关联

### 多模态支持

| 类型 | 支持格式 | 处理方式 |
|------|----------|----------|
| **图片** | JPEG, PNG, GIF, WebP | 直接传递 |
| **视频** | MP4, MPEG, MOV, AVI, FLV, WebM, 3GP | 直接传递 |
| **音频** | MP3, WAV, FLAC, OGG, AAC, M4A, WebM | 直接传递 |
| **PDF** | application/pdf | 直接传递 |
| **文本** | TXT, HTML, CSS, CSV, XML, RTF, JavaScript, Markdown, JSON | 直接传递 |
| **Office** | DOCX, XLSX, PPTX, DOC, XLS, PPT | **提取文本**后传递 |

## 架构

```
┌─────────────────┐    OpenAI API    ┌─────────────────┐    ADK SSE     ┌─────────────────┐
│                 │     Format       │                 │     Stream     │                 │
│  ChatBox/Dify   │  ─────────────>  │   Middleware    │  ────────────> │   ADK Backend   │
│                 │                  │                 │                │                 │
│                 │  <─────────────  │   - 格式转换     │  <──────────── │   (Gemini)      │
│                 │    JSON/SSE      │   - 多应用路由   │    Real SSE    │                 │
└─────────────────┘                  │   - 动态认证     │                └─────────────────┘
                                     │   - 会话管理     │
                                     └─────────────────┘
```

## 快速开始

### 环境要求

- Python 3.8+
- ADK 后端服务 (运行中)
- 推荐 2GB+ 内存

### 安装

```bash
# 克隆项目
git clone <repository-url>
cd adk_to_openai_api

# 创建虚拟环境
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/Mac
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 配置

创建 `.env` 文件：

```bash
# ADK 后端配置
ADK_HOST=http://localhost:8000
ADK_APP_NAME=agent

# 多应用后端映射（可选）
# 格式：app1:url1,app2:url2 或 JSON 格式
ADK_BACKEND_MAPPING=app1:http://backend1:8000,app2:http://backend2:8000

# 服务配置
PORT=8080
LOG_LEVEL=INFO

# 文件限制
MAX_FILE_SIZE_MB=20
FILE_DOWNLOAD_TIMEOUT=60

# 认证（可选）
ENABLE_API_KEY_AUTH=false
API_KEYS=sk-key1,sk-key2

# 会话记录（可选）
SESSION_HISTORY_ENABLED=true
DATABASE_PATH=data/sessions.db
```

### 启动

```bash
# 开发模式
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

# 生产模式
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 4
```

## 多应用支持

### 配置多应用后端

支持将不同的 ADK 应用路由到不同的后端服务器：

```bash
# 简单格式
ADK_BACKEND_MAPPING=app1:http://backend1:8000,app2:http://backend2:8000

# JSON 格式
ADK_BACKEND_MAPPING='{"app1": "http://backend1:8000", "app2": "http://backend2:8000"}'
```

### 模型格式

调用时使用 `app_name/agent_name` 格式：

```json
{
  "model": "app1/agent_name",
  "messages": [{"role": "user", "content": "你好"}]
}
```

如果只使用默认应用，可以只写 agent 名称：

```json
{
  "model": "agent_name",
  "messages": [{"role": "user", "content": "你好"}]
}
```

### 动态获取应用列表

```bash
curl http://localhost:8080/v1/models
```

返回格式：
```json
{
  "object": "list",
  "data": [
    {"id": "app1/agent1", "object": "model"},
    {"id": "app1/agent2", "object": "model"},
    {"id": "app2/agent1", "object": "model"}
  ]
}
```

## 动态 API Key 管理

### 管理端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/admin/api-keys` | POST | 添加 API Key |
| `/v1/admin/api-keys` | DELETE | 删除 API Key |
| `/v1/admin/api-keys` | GET | 列出所有 API Key |

### 添加 API Key

```bash
curl -X POST "http://localhost:8080/v1/admin/api-keys?api_key=sk-new-key-123"
```

### 删除 API Key

```bash
curl -X DELETE "http://localhost:8080/v1/admin/api-keys?api_key=sk-key-to-delete"
```

### 列出 API Key

```bash
curl http://localhost:8080/v1/admin/api-keys
```

响应：
```json
{
  "count": 2,
  "keys": [
    {"prefix": "sk-key1...", "added_at": "2024-01-20T10:00:00", "source": "env"},
    {"prefix": "sk-key2...", "added_at": "2024-01-20T11:00:00", "source": "dynamic"}
  ]
}
```

### 多用户隔离

每个 API Key 会生成唯一的用户 ID，实现用户会话隔离：

```
API Key: sk-abc123... → User ID: user_abc123def456...
API Key: sk-xyz789... → User ID: user_xyz789abc123...
```

## Docker 部署

### 快速启动

```bash
# 构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f
```

### Docker 配置说明

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `ADK_HOST` | `http://host.docker.internal:8000` | 默认 ADK 后端地址 |
| `ADK_BACKEND_MAPPING` | (空) | 多应用后端映射 |
| `ADK_APP_NAME` | `agent` | 默认应用名称 |
| `PORT` | `8080` | 服务端口 |
| `ENABLE_API_KEY_AUTH` | `false` | 是否启用 API Key 认证 |
| `API_KEYS` | (空) | API Key 列表 |

## API 文档

### OpenAPI/Swagger

访问交互式 API 文档：

- **Swagger UI**: http://localhost:8080/docs
- **ReDoc**: http://localhost:8080/redoc

### 核心端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/chat/completions` | POST | 聊天补全（支持流式/非流式） |
| `/v1/models` | GET | 获取可用模型列表 |
| `/upload` | POST | 文件上传并转换为 Base64 |

### 健康检查

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/health` | GET | 基础健康检查 |
| `/v1/health/detailed` | GET | 详细健康检查（含 ADK 后端状态） |
| `/v1/metrics` | GET | Prometheus 兼容的 metrics |

## 监控分析

### Prometheus Metrics

```bash
curl http://localhost:8080/v1/metrics
```

支持的指标：
- `adk_requests_total` - 总请求数
- `adk_requests_successful` - 成功请求数
- `adk_requests_failed` - 失败请求数
- `adk_tokens_input` - 输入 token 数
- `adk_tokens_output` - 输出 token 数
- `adk_latency_ms_average` - 平均延迟
- `adk_requests_by_model` - 按模型统计
- `adk_requests_by_tenant` - 按租户统计

## 会话记录

### 功能说明

自动记录所有对话历史到 SQLite 数据库，支持：

- 记录用户消息和助手响应
- 支持流式和非流式请求
- 记录延迟时间和模型信息

### 配置

```bash
SESSION_HISTORY_ENABLED=true     # 启用会话记录
DATABASE_PATH=data/sessions.db   # 数据库路径
```

### 会话历史端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/sessions/{session_id}/history` | GET | 获取会话历史记录 |
| `/v1/sessions/{session_id}/export` | GET | 导出会话（支持 json/markdown） |
| `/v1/history/search?q=xxx` | GET | 搜索消息内容 |
| `/v1/history/stats` | GET | 获取统计信息 |

## 使用示例

### 基本对话

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-your-api-key" \
  -d '{
    "model": "agent",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": false
  }'
```

### 多应用调用

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "app1/special_agent",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

### 流式对话

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agent",
    "messages": [{"role": "user", "content": "请详细介绍一下"}],
    "stream": true
  }'
```

### 带图片的消息

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agent",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "描述这张图片"},
        {"type": "image_url", "image_url": {"url": "https://example.com/image.jpg"}}
      ]
    }]
  }'
```

## 平台配置

### ChatBox

1. 设置 > 模型提供方 > 添加 OpenAI 兼容 API
2. 配置：
   - **API 地址**: `http://your-host:8080/v1`
   - **API Key**: 配置的密钥（如启用）
   - **模型**: 使用 `app_name/agent_name` 格式

### Dify

1. 设置 > 模型供应商 > 添加 OpenAI API 兼容供应商
2. 配置：
   - **API Base URL**: `http://your-host:8080/v1`
   - **API Key**: 配置的密钥（如启用）
   - **模型名称**: 使用 `app_name/agent_name` 格式

## 项目结构

```
adk_to_openai_api/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 应用入口
│   ├── core/                # 核心模块
│   │   ├── config.py        # 配置管理
│   │   ├── auth.py          # API Key 认证
│   │   ├── adk_client.py    # ADK 客户端
│   │   ├── api_key_manager.py  # 动态 API Key 管理
│   │   └── metrics.py       # 监控指标收集
│   ├── routers/             # 路由模块
│   │   ├── chat.py          # 聊天相关端点
│   │   └── admin.py         # 管理端点
│   ├── schemas/             # 数据模型
│   │   └── models.py        # Pydantic 模型定义
│   ├── utils/               # 工具模块
│   │   └── multimodal.py    # 多模态处理器
│   └── database/            # 数据库模块
│       └── database.py      # SQLite 会话存储
├── main.py                  # 应用入口
├── requirements.txt         # Python 依赖
├── Dockerfile               # Docker 镜像
├── docker-compose.yml       # Docker Compose
└── README.md
```

## 配置参考

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ADK_HOST` | `http://localhost:8000` | 默认 ADK 后端地址 |
| `ADK_APP_NAME` | `agent` | 默认应用名称 |
| `ADK_BACKEND_MAPPING` | (空) | 多应用后端映射 |
| `PORT` | `8080` | 服务端口 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `ENABLE_API_KEY_AUTH` | `false` | 是否启用 API Key |
| `API_KEYS` | (空) | API Key 列表（逗号分隔） |
| `MAX_FILE_SIZE_MB` | `20` | 最大文件大小 |
| `SESSION_HISTORY_ENABLED` | `true` | 启用会话记录 |
| `DATABASE_PATH` | `data/sessions.db` | 数据库路径 |

## 故障排除

### ADK 连接失败

```
ERROR: ADK HTTP error: 503 - Service Unavailable
```

**解决**:
1. 检查 ADK 后端是否运行
2. 使用 `/v1/health/detailed` 检查连接状态
3. 确认 `ADK_HOST` 和 `ADK_BACKEND_MAPPING` 配置正确

### 认证失败

```
ERROR: Invalid API key provided
```

**解决**:
1. 确认 `Authorization` 头格式正确：`Bearer sk-your-key`
2. 检查 API Key 是否已在管理端点中添加

### 调试模式

```bash
LOG_LEVEL=DEBUG python -m uvicorn app.main:app --reload
```

## 许可证

MIT License - 详见 LICENSE 文件
