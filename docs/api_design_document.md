# **ADK 接入 Dify 中间件适配器 \- 接口设计文档**

| 文档版本 | 日期 | 状态 | 对应 PRD 版本 | 对应 OpenAPI 版本 |
| :---- | :---- | :---- | :---- | :---- |
| v1.0 | 2025-12-17 | Draft | v1.4 | 1.0.0 |

## **1\. 概述**

本项目旨在开发一个 Python 中间件（Middleware Proxy），用于将 Google Agent Development Kit (ADK) 的自定义 REST/SSE 接口转换为 OpenAI 兼容的 API 格式，以便接入 Dify 平台。

该中间件的核心职责包括：

1. **协议转换**：将 OpenAI 格式的 /v1/chat/completions 请求转换为 ADK 的 /run 或 /run\_sse 请求。  
2. **多模态处理**：自动下载请求中的图片、视频或文件 URL，转换为 Base64 编码并封装为 ADK 的 inlineData。  
3. **会话管理**：基于 Dify 的 user 字段维护 ADK 的 sessionId，实现有状态对话。

## **2\. 服务基础信息**

* **Base URL**: http://localhost:8080/v1 (本地开发默认)  
* **协议**: HTTP/1.1  
* **数据格式**: JSON (application/json), Server-Sent Events (text/event-stream)

## **3\. 核心逻辑映射**

在调用 API 前，需理解以下字段和逻辑的映射关系：

| 逻辑域 | Dify (OpenAI 格式) | 中间件处理逻辑 | ADK (后端格式) |
| :---- | :---- | :---- | :---- |
| **Agent定位** | model (string) | 直接映射或使用默认值 | appName |
| **用户/会话** | user (string) | sessionId \= "session\_" \+ user | userId, sessionId |
| **历史记录** | messages (List) | **丢弃历史**，仅提取 messages\[-1\] (最新一条) | newMessage (ADK 自带记忆) |
| **多模态** | image\_url 或 文本中的 URL | 下载 \-\> 识别 MIME \-\> 转 Base64 | inlineData (mimeType, data) |
| **流式响应** | SSE (delta.content) | 转换 ADK Event \-\> OpenAI Chunk | SSE (content.parts\[\].text) |

## **4\. 接口定义**

### **4.1 对话补全 (Create Chat Completion)**

该接口是核心交互端点，支持文本、图片、文件输入，并支持流式输出。

* **Endpoint**: POST /chat/completions  
* **Summary**: 创建模型响应，处理多模态输入和流式转发。

#### **请求参数 (Request Body)**

application/json

| 参数名 | 类型 | 必填 | 说明 |
| :---- | :---- | :---- | :---- |
| model | string | 是 | 对应 ADK 的 appName，例如 "my\_agent"。 |
| messages | array | 是 | 对话消息列表。中间件仅处理最后一条。 |
| stream | boolean | 否 | 默认为 false。若为 true，使用 SSE 流式返回。 |
| user | string | 否 | **关键字段**。用于生成 ADK sessionId 以保持上下文。 |
| temperature | number | 否 | 采样温度，默认为 1.0。 |

**Message Content 结构说明:**

* **文本**: {"type": "text", "text": "..."}  
* **图片**: {"type": "image\_url", "image\_url": {"url": "..."}} (中间件会自动下载处理)  
* **视频/文件**: Dify 通常将其作为 URL 嵌入文本中，中间件需正则提取并下载。

#### **响应 (Response)**

**场景 A: 非流式响应 (stream=false)**

* **Status**: 200 OK  
* **Content-Type**: application/json

{  
  "id": "chatcmpl-123",  
  "object": "chat.completion",  
  "created": 1677652288,  
  "model": "my\_agent",  
  "choices": \[{  
    "index": 0,  
    "message": {  
      "role": "assistant",  
      "content": "Hello, how can I help you?"  
    },  
    "finish\_reason": "stop"  
  }\]  
}

**场景 B: 流式响应 (stream=true)**

* **Status**: 200 OK  
* **Content-Type**: text/event-stream

数据将以 OpenAI 格式的 Chunk 发送，直到发送 data: \[DONE\] 结束。

data: {"id":"...","choices":\[{"delta":{"content":"Hello"}}\]}

data: {"id":"...","choices":\[{"delta":{"content":" World"}}\]}

data: \[DONE\]

#### **错误处理 (Error Handling)**

| Status | 描述 | 原因示例 |
| :---- | :---- | :---- |
| 400 | Bad Request | 无效输入、图片/文件下载失败、文件超过 20MB |
| 500 | Internal Server Error | ADK 服务不可达、中间件内部错误 |
| 502 | Bad Gateway | ADK 连接失败 |

### **4.2 获取模型列表 (List Models)**

用于 Dify 平台校验模型可用性。

* **Endpoint**: GET /models  
* **Summary**: 列出当前可用的 ADK Agent。

#### **响应 (Response)**

* **Status**: 200 OK  
* **Content-Type**: application/json

{  
  "object": "list",  
  "data": \[  
    {  
      "id": "my\_agent",  
      "object": "model",  
      "created": 1677652288,  
      "owned\_by": "adk"  
    }  
  \]  
}

### **4.3 健康检查 (Health Check)**

用于容器编排或负载均衡器的健康监测。

* **Endpoint**: GET /v1/health
* **Summary**: 检查中间件是否运行正常。

#### **响应 (Response)**

* **Status**: 200 OK  
* **Content-Type**: application/json

{  
  "status": "ok"  
}

## **5\. 环境配置 (Environment Variables)**

为了支持上述接口功能，部署时需配置以下环境变量：

| 变量名 | 必填 | 默认值 | 说明 |
| :---- | :---- | :---- | :---- |
| ADK\_HOST | 是 | http://localhost:8000 | 后端 ADK 服务地址。 |
| ADK\_APP\_NAME | 否 | default\_agent | 当请求中 model 为空时的默认值。 |
| PORT | 否 | 8080 | 中间件监听端口。 |
| MAX\_FILE\_SIZE\_MB | 否 | 20 | 最大文件下载限制 (MB)。 |
| DOWNLOAD\_TIMEOUT | 否 | 30 | 附件下载超时时间 (秒)。 |

## **6\. 开发注意事项**

1. **并发下载**：必须使用异步 (asyncio) 方式处理文件下载，避免阻塞主线程影响高并发响应。  
2. **MIME Type 识别**：对于 Dify 传入的 URL，需通过 HEAD 请求或后缀名准确识别 video/mp4, application/pdf 等类型，以便正确填充 ADK 的 inlineData。  
3. **超时控制**：ADK 推理可能较慢，建议设置 ADK 响应超时时间为 120 秒。