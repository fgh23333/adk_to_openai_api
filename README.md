# ADK to OpenAI API Middleware

将 Google Agent Development Kit (ADK) 的 REST API 转换为 OpenAI 兼容格式的高性能 Python 中间件，支持接入 Dify、ChatBox、LangChain 等 LLM 平台。

## 功能特性

### 核心功能

- **OpenAI API 兼容**: 完全兼容 OpenAI Chat Completions API 规范
- **流式响应**: 支持 SSE 流式输出，模拟打字机效果
- **多会话管理**: 支持通过 Header 或请求体区分不同会话
- **可选认证**: Bearer Token API Key 认证

### 多模态支持

| 类型 | 支持格式 | 处理方式 |
|------|----------|----------|
| **图片** | JPEG, PNG, GIF, WebP | 直接传递给 Gemini |
| **视频** | MP4, MPEG, MOV, AVI, FLV, WebM, 3GP | 直接传递给 Gemini |
| **音频** | MP3, WAV, FLAC, OGG, AAC, M4A, WebM | 直接传递给 Gemini |
| **PDF** | application/pdf | 直接传递给 Gemini |
| **Office** | DOCX, XLSX, PPTX | **提取文本**后传递 |
| **文本** | TXT, HTML, Markdown, CSV | **提取纯文本**后传递 |

### 文本提取

对于 Office 文档和 HTML/Markdown，中间件会自动提取纯文本内容：

- **HTML**: 移除标签、脚本、样式，提取纯文本
- **Markdown**: 移除标记语法，提取纯文本
- **DOCX**: 提取段落和表格文本
- **XLSX**: 提取所有工作表内容
- **PPTX**: 提取所有幻灯片文本
- **CSV**: 格式化表格输出

### 资源过滤

自动忽略以下资源类型（不传递给 ADK）：
- CSS 样式文件
- JavaScript 文件
- 字体文件 (WOFF, WOFF2, TTF, OTF)
- 图标文件 (ICO)

## 架构

```
┌─────────────────┐    OpenAI API    ┌─────────────────┐    ADK API     ┌─────────────────┐
│                 │     Format       │                 │     Format     │                 │
│  ChatBox/Dify   │  ─────────────>  │   Middleware    │  ────────────> │   ADK Backend   │
│                 │                  │                 │                │                 │
│                 │  <─────────────  │   - 格式转换     │  <────────────  │   (Gemini)      │
│                 │    JSON/SSE      │   - 文本提取     │    JSON/SSE    │                 │
└─────────────────┘                  │   - 会话管理     │                └─────────────────┘
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

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/chat/completions` | POST | 聊天补全（支持流式/非流式） |
| `/v1/models` | GET | 获取可用模型列表 |
| `/v1/health` | GET | 健康检查 |
| `/upload` | POST | 文件上传并转换为 Base64 |

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

### OpenAI 格式音频

```json
{
  "model": "agent",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "input_audio", "input_audio": {"data": "base64...", "format": "mp3"}}
    ]
  }]
}
```

### 带文档的消息（自动提取文本）

```json
{
  "model": "agent",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "总结这个文档"},
      {"type": "file", "file": {"url": "https://example.com/document.docx"}}
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
│   ├── adk_client.py     # ADK 后端客户端
│   ├── multimodal.py     # 多模态内容处理器
│   ├── models.py         # Pydantic 数据模型
│   ├── config.py         # 配置管理
│   └── auth.py           # API Key 认证
├── main.py               # 应用入口
├── requirements.txt      # Python 依赖
└── README.md
```

## 处理流程

```
请求到达
    │
    ▼
┌─────────────────┐
│ 认证验证        │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 会话标识处理    │ ← X-Session-ID / X-User-ID / user 字段
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 多模态处理      │
│  - 图片/视频/音频 → 直接传递
│  - Office/HTML  → 提取文本
│  - CSS/JS/字体  → 自动忽略
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 格式转换        │ ← OpenAI → ADK
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 确保 Session    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 调用 ADK 后端   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 响应转换        │ ← ADK → OpenAI
└────────┬────────┘
         │
         ▼
返回响应（JSON/SSE）
```

## 故障排除

### ADK 连接失败

```
ERROR: ADK HTTP error: 503 - Service Unavailable
```

**解决**: 检查 ADK 后端是否运行，`ADK_HOST` 配置是否正确。

### 文件类型不支持

```
WARNING: 不支持的文件类型: application/xxx
```

**解决**: 该文件类型不在支持列表中，会自动跳过。如需支持，可在 `multimodal.py` 中添加。

### 多模态处理超时

```
WARNING: Failed to process URL: timeout
```

**解决**: 增加 `DOWNLOAD_TIMEOUT` 配置值。

### 调试模式

```bash
LOG_LEVEL=DEBUG python main.py
```

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
