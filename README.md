# ADK to OpenAI API Middleware

将 Google Agent Development Kit (ADK) 的 REST API 转换为 OpenAI 兼容格式的高性能 Python 中间件，支持接入 Dify、ChatBox、LangChain 等 LLM 平台。

## 功能特性

### 核心功能

- **OpenAI API 兼容**: 完全兼容 OpenAI Chat Completions API 规范
- **真正流式响应**: 使用 ADK SSE 端点实现实时流式输出
- **多会话管理**: 支持通过 Header 或请求体区分不同会话
- **请求追踪**: 每个请求分配唯一 ID，便于调试和日志关联
- **可选认证**: Bearer Token API Key 认证

### 多模态支持

| 类型 | 支持格式 | 处理方式 |
|------|----------|----------|
| **图片** | JPEG, PNG, GIF, WebP | 直接传递 |
| **视频** | MP4, MPEG, MOV, AVI, FLV, WebM, 3GP | 直接传递 |
| **音频** | MP3, WAV, FLAC, OGG, AAC, M4A, WebM | 直接传递 |
| **PDF** | application/pdf | 直接传递 |
| **文本** | TXT, HTML, CSS, CSV, XML, RTF, JavaScript, Markdown, JSON | 直接传递 |
| **Office** | DOCX, XLSX, PPTX, DOC, XLS, PPT | **提取文本**后传递 |

### 性能优化

- **连接池复用**: httpx 连接池，减少 TCP 握手开销
- **并发 URL 下载**: 消息中的多个 URL 并发下载
- **HTTP/2 支持**: 支持 HTTP/2 协议
- **请求追踪**: 响应头包含 `X-Request-ID` 和 `X-Process-Time`

### 文本提取

Gemini 原生支持 HTML、CSS、JavaScript、JSON、XML、CSV、RTF、Markdown 等格式，这些会直接传递。

仅对 **Office 文档**自动提取纯文本：

- **DOCX**: 提取段落和表格文本
- **XLSX**: 提取所有工作表内容
- **PPTX**: 提取所有幻灯片文本

### 资源过滤

自动忽略以下资源类型（Gemini 不支持）：
- 字体文件 (WOFF, WOFF2, TTF, OTF)
- 图标文件 (ICO)

## 架构

```
┌─────────────────┐    OpenAI API    ┌─────────────────┐    ADK SSE     ┌─────────────────┐
│                 │     Format       │                 │     Stream     │                 │
│  ChatBox/Dify   │  ─────────────>  │   Middleware    │  ────────────> │   ADK Backend   │
│                 │                  │                 │                │                 │
│                 │  <─────────────  │   - 格式转换     │  <──────────── │   (Gemini)      │
│                 │    JSON/SSE      │   - 消息去重     │    Real SSE    │                 │
└─────────────────┘                  │   - 连接池复用   │                └─────────────────┘
                                     │   - 并发下载     │
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

创建 `.env` 文件（可选）：

```bash
# ADK 后端配置
ADK_HOST=http://localhost:8000
ADK_APP_NAME=agent

# 服务配置
PORT=8080
LOG_LEVEL=INFO

# 文件限制
MAX_FILE_SIZE_MB=20
DOWNLOAD_TIMEOUT=30

# 认证（可选）
REQUIRE_API_KEY=false
API_KEYS=sk-key1,sk-key2
```

### 启动

```bash
# 开发模式
python main.py

# 使用 uvicorn
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

# 生产模式
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 4
```

## Docker 部署

### 快速启动

```bash
# 复制配置文件
cp .env.example .env

# 编辑配置
vim .env  # 修改 ADK_HOST 等配置

# 构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f
```

### 手动构建镜像

```bash
# 构建镜像
docker build -t adk-middleware:latest .

# 运行容器
docker run -d \
  --name adk-middleware \
  -p 8080:8080 \
  -e ADK_HOST=http://your-adk-host:8000 \
  -e ADK_APP_NAME=agent \
  adk-middleware:latest
```

### Docker 配置说明

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `ADK_HOST` | `http://host.docker.internal:8000` | ADK 后端地址 |
| `ADK_APP_NAME` | `agent` | ADK 应用名称 |
| `PORT` | `8080` | 服务端口 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `MAX_FILE_SIZE_MB` | `20` | 最大文件大小 |
| `DOWNLOAD_TIMEOUT` | `30` | URL 下载超时 |
| `REQUIRE_API_KEY` | `false` | 是否启用 API Key |
| `API_KEYS` | (空) | API Key 列表 |

### 连接本地 ADK

如果 ADK 运行在宿主机上，使用 `host.docker.internal`：

```yaml
environment:
  - ADK_HOST=http://host.docker.internal:8000
```

### 健康检查

容器内置健康检查，可通过以下命令查看状态：

```bash
docker ps  # 查看 STATUS 列的 healthy 状态
docker inspect --format='{{.State.Health.Status}}' adk-middleware
```

## API 文档

### OpenAPI/Swagger

访问交互式 API 文档：

- **Swagger UI**: http://localhost:8080/docs
- **ReDoc**: http://localhost:8080/redoc
- **OpenAPI JSON**: http://localhost:8080/openapi.json

### 端点列表

#### 核心端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/chat/completions` | POST | 聊天补全（支持流式/非流式） |
| `/v1/models` | GET | 获取可用模型列表 |
| `/upload` | POST | 文件上传并转换为 Base64 |

#### 健康检查

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/health` | GET | 基础健康检查 |
| `/v1/health/detailed` | GET | 详细健康检查（含 ADK 后端状态） |

#### Session 管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/sessions` | GET | 列出所有缓存的 sessions |
| `/v1/sessions/{session_id}` | DELETE | 删除指定 session |
| `/v1/sessions/{session_id}/reset` | POST | 重置 session（删除并重建） |

#### 监控分析

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/metrics` | GET | Prometheus 兼容的 metrics |
| `/v1/metrics/summary` | GET | JSON 格式的指标摘要 |
| `/v1/metrics/requests` | GET | 最近请求列表 |
| `/v1/metrics/tenant/{id}` | GET | 租户指标 |

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
- `adk_errors_by_type` - 按错误类型统计

### 指标摘要

```bash
curl http://localhost:8080/v1/metrics/summary
```

```json
{
  "total_requests": 100,
  "successful_requests": 95,
  "failed_requests": 5,
  "success_rate_percent": 95.0,
  "total_input_tokens": 5000,
  "total_output_tokens": 10000,
  "total_tokens": 15000,
  "average_latency_ms": 1234.56,
  "by_tenant": {"tenant_1": 50, "tenant_2": 50},
  "by_model": {"agent": 100},
  "errors_by_type": {},
  "content_types": {"text": 80, "image": 20}
}
```

### 最近请求

```bash
# 获取最近 50 条请求
curl "http://localhost:8080/v1/metrics/requests?limit=50"

# 按租户过滤
curl "http://localhost:8080/v1/metrics/requests?tenant_id=tenant_1"
```

### Grafana 集成

在 Grafana 中添加 Prometheus 数据源，指向：
```
http://localhost:8080/v1/metrics
```

#### 会话记录

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/sessions/{session_id}/history` | GET | 获取会话历史记录 |
| `/v1/sessions/{session_id}/export` | GET | 导出会话（支持 json/markdown） |
| `/v1/sessions/{session_id}/history` | DELETE | 删除会话历史 |
| `/v1/history/search?q=xxx` | GET | 搜索消息内容 |
| `/v1/history/stats` | GET | 获取统计信息 |
| `/v1/history/cleanup?days=30` | POST | 清理旧记录 |

## 会话记录

### 功能说明

自动记录所有对话历史到 SQLite 数据库：
- 记录用户消息和助手响应
- 支持流式和非流式请求
- 记录延迟时间和模型信息

### 配置

```bash
# .env
SESSION_HISTORY_ENABLED=true     # 启用会话记录
DATABASE_PATH=data/sessions.db   # 数据库路径
```

### 数据来源说明

`/v1/sessions` 端点的数据来源取决于是否启用会话记录：

| SESSION_HISTORY_ENABLED | 数据来源 | 说明 |
|------------------------|---------|------|
| `true` | 数据库 | 返回有历史记录的 sessions |
| `false` | ADK 缓存 | 返回 ADK 后端缓存的 sessions |

**注意**：启用会话记录后，`/v1/sessions` 返回的 `session_id` 可以直接用于 `/v1/sessions/{session_id}/history` 查询。

### 获取会话历史

```bash
# 获取最近 100 条消息
curl http://localhost:8080/v1/sessions/user_123/history

# 分页获取
curl "http://localhost:8080/v1/sessions/user_123/history?limit=50&offset=0"
```

### 导出会话

```bash
# 导出为 JSON
curl http://localhost:8080/v1/sessions/user_123/export

# 导出为 Markdown
curl "http://localhost:8080/v1/sessions/user_123/export?format=markdown"
```

导出的 Markdown 格式：

```markdown
# Session: user_123

- Created: 2024-01-20 10:30:00
- Messages: 10

## Conversation

**USER:** 你好

**ASSISTANT:** 你好！有什么可以帮助你的？

...
```

### 搜索消息

```bash
# 搜索包含"关键词"的消息
curl "http://localhost:8080/v1/history/search?q=关键词"

# 在特定会话中搜索
curl "http://localhost:8080/v1/history/search?q=关键词&session_id=user_123"
```

### 统计信息

```bash
curl http://localhost:8080/v1/history/stats
```

```json
{
  "enabled": true,
  "total_sessions": 5,
  "total_messages": 42,
  "messages_by_role": {
    "user": 21,
    "assistant": 21
  },
  "recent_messages_24h": 10
}
```

### 清理旧记录

```bash
# 删除 30 天前的会话记录
curl -X POST "http://localhost:8080/v1/history/cleanup?days=30"
```

### 隐私说明

- 数据存储在本地 SQLite 数据库
- 不会上传到任何服务器
- 可随时删除历史记录

## Session 自动管理

### 设计理念

OpenAI API 是无状态的，本中间件通过以下方式实现有状态会话：

| 层级 | 标识 | 说明 |
|------|------|------|
| **租户** | API Key | 区分不同用户/租户 |
| **会话** | 对话历史 Hash | 区分同一租户下的不同会话 |

### Session ID 生成规则

```
Session ID = {租户ID}_{对话历史Hash}
```

**工作原理**：

1. **首次对话**（无历史）→ 生成新 session
2. **继续对话**（有历史）→ 基于历史 hash 复用 session
3. **新话题**（清空历史）→ 生成新 session

### 示例

```bash
# 第一次请求（新对话）
# messages: [{"role": "user", "content": "你好"}]
# → session: session_abc123_new_xxxxx

# 第二次请求（继续对话）
# messages: [历史...] + [{"role": "user", "content": "你是谁"}]
# → session: session_abc123_<hash>
# → 相同历史 = 相同 session = 保持上下文
```

### 手动指定 Session

如果需要手动控制 session，可以通过以下方式（优先级从高到低）：

```bash
# 方式 1: X-Session-ID header
curl -H "X-Session-ID: my_session" ...

# 方式 2: X-User-ID header
curl -H "X-User-ID: my_user" ...

# 方式 3: user 字段
curl -d '{"user": "my_session", "messages": [...]}' ...
```

### ChatBox 等客户端配置

ChatBox 等客户端会自动在请求中包含历史消息，因此：

- **自动保持上下文**：相同历史 → 相同 session
- **无需额外配置**：直接使用即可
- **不同 API Key**：不同租户，session 隔离

## 健康检查

### 基础检查

```bash
curl http://localhost:8080/v1/health
# {"status": "ok"}
```

### 详细检查

```bash
curl http://localhost:8080/v1/health/detailed
```

```json
{
    "middleware": "healthy",
    "adk_backend": "healthy",
    "adk_host": "http://localhost:8000",
    "details": {
        "latency_ms": 54.94,
        "status_code": 200
    },
    "healthy": true
}
```

## Session 管理

### 列出 Sessions

```bash
curl http://localhost:8080/v1/sessions
```

```json
{
    "count": 2,
    "sessions": [
        {"app_name": "agent", "user_id": "user_abc", "session_id": "session_user_abc"},
        {"app_name": "agent", "user_id": "anonymous", "session_id": "session_anonymous"}
    ]
}
```

### 删除 Session

```bash
curl -X DELETE "http://localhost:8080/v1/sessions/session_abc?app_name=agent&user_id=anonymous"
```

```json
{
    "success": true,
    "session_id": "session_abc",
    "app_name": "agent",
    "user_id": "anonymous"
}
```

### 重置 Session（当 session 损坏时）

```bash
curl -X POST "http://localhost:8080/v1/sessions/session_abc/reset"
```

```json
{
    "success": true,
    "session_id": "session_abc",
    "app_name": "agent",
    "user_id": "anonymous",
    "action": "reset"
}
```

## 多会话支持

通过以下方式区分不同会话：

### 方式 1: 请求头 X-Session-ID（推荐）

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "X-Session-ID: conversation_123" \
  -H "Content-Type: application/json" \
  -d '{"model": "agent", "messages": [...]}'
```

### 方式 2: 请求头 X-User-ID

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "X-User-ID: user_abc" \
  ...
```

### 方式 3: 请求体 user 字段

```json
{
  "model": "agent",
  "user": "conversation_456",
  "messages": [...]
}
```

> **注意**: 如果不指定任何会话标识，每次请求都会创建新会话。

## 请求头说明

### 认证头

| Header | 必填 | 说明 | 示例 |
|--------|------|------|------|
| `Authorization` | 可选 | API Key 认证 | `Bearer sk-your-api-key` |

### 会话头

| Header | 必填 | 说明 | 示例 |
|--------|------|------|------|
| `X-Session-ID` | 可选 | 会话标识，用于区分不同对话 | `conversation_123` |
| `X-User-ID` | 可选 | 用户标识，用于区分不同用户 | `user_abc` |

### 追踪头

| Header | 必填 | 说明 | 示例 |
|--------|------|------|------|
| `X-Request-ID` | 可选 | 请求追踪 ID，用于日志关联 | `req_abc123` |

### 完整示例

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-your-api-key" \
  -H "X-Session-ID: my_session_123" \
  -H "X-Request-ID: req_001" \
  -d '{
    "model": "agent",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

### 响应头

每个响应都会返回：

```
X-Request-ID: req_afd77e079d25
X-Process-Time: 1.234s
```

## 流式响应

### 真正的 SSE 流式

使用 ADK `/run_sse` 端点实现真正的流式响应：

```
ADK 生成 → 实时转发 → 用户即时看到
```

**对比**：
- 之前：等待完整响应 → 分块发送（模拟流式）
- 现在：边生成边转发（真正的流式）

### 消息去重

自动处理 ADK SSE 事件中的重复内容：

```
事件1: "Hello"     → 发送 "Hello"
事件2: "Hello Wor" → 发送 " Wor"
事件3: "Hello World" → 发送 "ld"
```

## 请求追踪

每个请求都会分配唯一的 `X-Request-ID`，响应头中包含：

```
X-Request-ID: req_afd77e079d25
X-Process-Time: 1.234s
```

也可以在请求时指定：

```bash
curl -H "X-Request-ID: my-custom-id" http://localhost:8080/v1/chat/completions ...
```

## 性能优化

### 连接池配置

```python
# httpx 连接池
max_connections=100          # 最大连接数
max_keepalive_connections=20 # Keep-alive 连接数
keepalive_expiry=30.0        # Keep-alive 过期时间（秒）
```

### 并发 URL 下载

消息中的多个 URL 会并发下载：

```
之前: URL1 → URL2 → URL3 (串行)
现在: URL1 ┐
       URL2 ├→ 并发
       URL3 ┘
```

## 错误响应

标准化的错误响应格式：

```json
{
  "error": {
    "message": "Unsupported file type 'application/xxx' for 'file.py'",
    "type": "bad_request",
    "code": 400,
    "request_id": "req_a313af662152",
    "details": {...}
  }
}
```

常见错误类型：

| 错误类型 | HTTP 状态码 | 说明 |
|---------|------------|------|
| `bad_request` | 400 | 请求参数错误 |
| `validation_error` | 400 | 数据验证失败 |
| `unauthorized` | 401 | 认证失败 |
| `not_found` | 404 | 资源不存在 |
| `payload_too_large` | 413 | 文件过大 |
| `adk_backend_error` | 502 | ADK 后端错误 |
| `timeout_error` | 504 | 请求超时 |

## 使用示例

### 基本对话

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agent",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": false
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

### 带音频的消息

```json
{
  "model": "agent",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "这段音频说了什么？"},
      {"type": "audio_url", "audio_url": {"url": "https://example.com/audio.mp3"}}
    ]
  }]
}
```

### 带多个 URL 的消息（并发下载）

```json
{
  "model": "agent",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "对比这两张图片"},
      {"type": "image_url", "image_url": {"url": "https://example.com/image1.jpg"}},
      {"type": "image_url", "image_url": {"url": "https://example.com/image2.jpg"}}
    ]
  }]
}
```

### 响应格式

**非流式响应**:
```json
{
  "id": "chatcmpl-1234567890",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "agent",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "你好！有什么可以帮助你的？"},
    "finish_reason": "stop"
  }]
}
```

**流式响应**:
```
data: {"id":"chatcmpl-123","object":"chat.completion.chunk","choices":[{"delta":{"content":"你"}}]}
data: {"id":"chatcmpl-123","object":"chat.completion.chunk","choices":[{"delta":{"content":"好"}}]}
data: [DONE]
```

## 平台配置

### ChatBox

1. 设置 > 模型提供方 > 添加 OpenAI 兼容 API
2. 配置：
   - **API 地址**: `http://your-host:8080/v1`
   - **API Key**: 配置的密钥（如启用）
   - **模型**: 对应 ADK 的 `appName`

### Dify

1. 设置 > 模型供应商 > 添加 OpenAI API 兼容供应商
2. 配置：
   - **API Base URL**: `http://your-host:8080/v1`
   - **API Key**: 配置的密钥（如启用）
   - **模型名称**: 对应 ADK 的 `appName`

## 项目结构

```
adk_to_openai_api/
├── app/
│   ├── __init__.py
│   ├── main.py           # FastAPI 应用和端点
│   ├── adk_client.py     # ADK 客户端（连接池、SSE 流式）
│   ├── multimodal.py     # 多模态处理器（并发下载）
│   ├── models.py         # Pydantic 数据模型
│   ├── config.py         # 配置管理
│   └── auth.py           # API Key 认证
├── main.py               # 应用入口
├── requirements.txt      # Python 依赖
├── Dockerfile            # Docker 镜像
├── docker-compose.yml    # Docker Compose
└── README.md
```

## 故障排除

### ADK 连接失败

```
ERROR: ADK HTTP error: 503 - Service Unavailable
```

**解决**:
1. 检查 ADK 后端是否运行
2. 使用 `/v1/health/detailed` 检查连接状态
3. 确认 `ADK_HOST` 配置正确

### Session 损坏

```
ERROR: Session not found / Invalid session state
```

**解决**: 使用 Session 重置 API
```bash
curl -X POST "http://localhost:8080/v1/sessions/session_xxx/reset"
```

### 文件类型不支持

```
Unsupported file type 'application/xxx' for 'filename'
```

**解决**: 该文件类型不在支持列表中，会自动跳过。如需支持，可在 `multimodal.py` 中添加。

### 调试模式

```bash
LOG_LEVEL=DEBUG python main.py
```

日志会包含 `request_id`，便于追踪问题。

## 配置参考

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ADK_HOST` | `http://localhost:8000` | ADK 后端地址 |
| `ADK_APP_NAME` | `agent` | 默认 ADK agent 名称 |
| `PORT` | `8080` | 服务端口 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `MAX_FILE_SIZE_MB` | `20` | 最大文件大小 |
| `DOWNLOAD_TIMEOUT` | `30` | URL 下载超时（秒） |
| `REQUIRE_API_KEY` | `false` | 是否启用 API Key |
| `API_KEYS` | (空) | 有效的 API Key 列表 |

## 许可证

MIT License - 详见 LICENSE 文件
